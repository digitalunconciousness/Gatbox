#!/usr/bin/env python3
"""Disk imaging (docs/plans/2026-11-02-disk-imaging.md): gatbox-disk against a fake sysfs tree, plain files standing in
for devices, a fake mount table and tests/fake-ddrescue. No test opens a real block device. Each check is one of the
note's §1 lessons or a §5 guard: identity by the drive (not the dock's shared by-id name), empty slots, the image
store and mmcblk/nvme never offered, the store's marker, one read for an HDD and two for flash, bad areas and RETRY,
dated names and no duplicates, the archive queue, the TARGET CHECK's refusals and contents, and a WRITE that's
re-identified, held, verified and logged. A few run in-process (the drive swapped between check and write, a busy
O_EXCL claim, a verify mismatch, an expired check), the rest through the CLI.
    python3 tests/test-disk.py
"""
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools/gatbox-disk")
T = tempfile.mkdtemp(prefix="gatbox-disk-test-")
SYS, DEV, STORE, MOUNTINFO = f"{T}/sys", f"{T}/dev", f"{T}/store", f"{T}/mountinfo"
LOG = f"{T}/ddrescue.log"
MiB = 1 << 20
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


# --- the fake machine: sysfs, devices, mounts -------------------------------------------------------------------------

DISKS = {}          # name -> its sysfs block directory


def usb_disk(name, nbytes, vendor, model, port, vid="1234", pid="5678", logical=512, devnum="8:0", parts=(),
             data=None, driver="uas"):
    """A whole disk behind a USB bridge, as the kernel lays it out, and a plain file of NBYTES standing in for it."""
    usb = f"{SYS}/devices/platform/axi/1000120000.pcie/xhci-hcd.0/usb2/2-{port}"
    os.makedirs(f"{usb}/2-{port}:1.0", exist_ok=True)
    for f, v in (("idVendor", vid), ("idProduct", pid), ("serial", "WIDGETDOCK0001")):
        open(f"{usb}/{f}", "w").write(v + "\n")
    drv = f"{SYS}/bus/usb/drivers/{driver}"
    os.makedirs(drv, exist_ok=True)
    link = f"{usb}/2-{port}:1.0/driver"
    if not os.path.lexists(link):
        os.symlink(drv, link)
    scsi = f"{usb}/2-{port}:1.0/host{port}/target{port}:0:0/{port}:0:0:0"
    blk = f"{scsi}/block/{name}"
    shutil.rmtree(blk, ignore_errors=True)
    os.makedirs(f"{blk}/queue")
    for f, v in (("vendor", vendor), ("model", model), ("rev", "1.0")):
        open(f"{scsi}/{f}", "w").write(v + "\n")
    os.symlink(scsi, f"{blk}/device")
    open(f"{blk}/size", "w").write(f"{nbytes // 512}\n")
    open(f"{blk}/dev", "w").write(devnum + "\n")
    open(f"{blk}/removable", "w").write("0\n")
    open(f"{blk}/queue/logical_block_size", "w").write(f"{logical}\n")
    open(f"{blk}/queue/rotational", "w").write("1\n")
    major, minor = devnum.split(":")
    for i, _ in enumerate(parts, 1):
        os.makedirs(f"{blk}/{name}{i}")
        open(f"{blk}/{name}{i}/partition", "w").write(f"{i}\n")
        open(f"{blk}/{name}{i}/dev", "w").write(f"{major}:{int(minor) + i}\n")
    os.makedirs(f"{SYS}/block", exist_ok=True)
    if os.path.lexists(f"{SYS}/block/{name}"):
        os.remove(f"{SYS}/block/{name}")
    os.symlink(blk, f"{SYS}/block/{name}")
    with open(f"{DEV}/{name}", "wb") as f:
        if data is not None:
            f.write(data)
        f.truncate(nbytes)
    DISKS[name] = blk


def other_disk(name, kind):
    """An SD card, NVMe drive or SATA disk on the Pi itself (not USB): sysfs has it, and gatbox-disk must never list
    or open it. A SATA disk on a PCIe HAT is still called sdX: only its place in sysfs says it isn't on USB."""
    where = {"mmc": "mmc_host/mmc0/mmc0:0001", "nvme": "1000110000.pcie/nvme/nvme0",
             "sata": "1000110000.pcie/0000:01:00.0/ata1/host0/target0:0:0/0:0:0:0"}[kind]
    blk = f"{SYS}/devices/platform/axi/{where}/block/{name}"
    os.makedirs(f"{blk}/queue", exist_ok=True)
    open(f"{blk}/size", "w").write("1000\n")
    open(f"{blk}/dev", "w").write("179:0\n")
    os.symlink(blk, f"{SYS}/block/{name}")


def by_id(name, link):
    os.makedirs(f"{DEV}/disk/by-id", exist_ok=True)
    p = f"{DEV}/disk/by-id/{link}"
    if os.path.lexists(p):
        os.remove(p)
    os.symlink(f"../../{name}", p)


def mount_table(store=True, extra=()):
    lines = ["22 1 179:2 / / rw,noatime shared:1 - ext4 /dev/mmcblk0p2 rw",
             "23 22 179:1 / /boot/firmware rw shared:2 - vfat /dev/mmcblk0p1 rw"]
    if store:
        lines.append(f"40 22 8:33 / {STORE} rw,noatime shared:9 - ext4 /dev/sdc1 rw")
    lines += list(extra)
    open(MOUNTINFO, "w").write("\n".join(lines) + "\n")


def env(**extra):
    e = dict(os.environ, GATBOX_SYSFS=SYS, GATBOX_DEV=DEV, GATBOX_MOUNTINFO=MOUNTINFO, GATBOX_DISKS=STORE,
             GATBOX_DDRESCUE=os.path.join(REPO, "tests/fake-ddrescue"), GATBOX_API="http://127.0.0.1:9",
             FAKE_DDRESCUE_LOG=LOG)
    e.update({k: str(v) for k, v in extra.items()})
    return e


def disk(*args, stdin="", **extra):
    if os.path.exists(LOG):
        os.remove(LOG)
    r = subprocess.run(["python3", TOOL, *args], capture_output=True, text=True, env=env(**extra), input=stdin,
                       timeout=120)
    return r.returncode, r.stdout + r.stderr


def calls():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


def files(machine):
    d = f"{STORE}/{machine}"
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def load_tool():
    """gatbox-disk as a module, for the in-process checks (environment set first: it's read at import)."""
    os.environ.update(env())
    loader = importlib.machinery.SourceFileLoader("gatbox_disk", TOOL)
    spec = importlib.util.spec_from_loader("gatbox_disk", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def fat_card(nbytes, label=b"WIDGETCARD "):
    """A FAT32 boot sector at the start of a whole card (no partition table), as a camera formats one."""
    b = bytearray(nbytes)
    b[3:11] = b"MSDOS5.0"
    b[71:82] = label
    b[82:90] = b"FAT32   "
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def iso_card(nbytes):
    """An ISO 9660 image on a card, like an installer written to it (the 11-02 near miss)."""
    b = bytearray(nbytes)
    b[32769:32774] = b"CD001"
    b[32808:32808 + 15] = b"WIDGET_INSTALL "
    return bytes(b)


def dos_table(nbytes):
    """A dos partition table with one ext4 partition at 1 MiB."""
    b = bytearray(nbytes)
    b[446 + 4] = 0x83
    b[446 + 8:446 + 12] = (2048).to_bytes(4, "little")
    b[446 + 12:446 + 16] = ((nbytes - MiB) // 512).to_bytes(4, "little")
    b[510:512] = b"\x55\xaa"
    sb = MiB + 1024
    b[sb + 56:sb + 58] = b"\x53\xef"
    b[sb + 120:sb + 120 + 7] = b"WIDGETS"
    return bytes(b)


def main():
    for d in (SYS, DEV, STORE):
        os.makedirs(d)
    open(f"{STORE}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mount_table()
    src = os.urandom(3 * MiB)
    sha = hashlib.sha1(src).hexdigest()
    # the dock's bridge gives every drive the same by-id name (the 11-02 lesson): sda is the old drive on it
    usb_disk("sda", len(src), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=src)
    by_id("sda", "usb-Widget_Dock_0001-0:0")
    usb_disk("sdc", 64 * MiB, "WD", "Widget Store 4T", port=2, devnum="8:32", parts=["sdc1"])
    usb_disk("sdd", 0, "Generic", "Card Reader CF", port=3, devnum="8:48")          # an empty slot
    other_disk("mmcblk0", "mmc")
    other_disk("nvme0n1", "nvme")
    other_disk("sdz", "sata")
    day = time.strftime("%Y%m%d")

    print("what's listed:")
    code, out = disk("list", "--json")
    j = json.loads(out)
    names = {d["name"]: d for d in j["disks"]}
    check("USB disks are listed; the SD card, the NVMe drive and a SATA disk on PCIe are not",
          set(names) == {"sda", "sdc", "sdd"})
    a = names["sda"]["identity"]
    check("identity: the drive's own model, size and sector size, the bridge's USB ID, the by-id name",
          a["model"] == "WIDGETDRIVE 1000" and a["vendor"] == "WIDGET" and a["bytes"] == len(src) and a["logical"] == 512
          and a["usb"] == "1234:5678" and a["by_id"] == ["usb-Widget_Dock_0001-0:0"] and a["driver"] == "uas")
    check("the image store is marked as the store", names["sdc"]["role"] == "store" and j["store"]["mounted"])
    check("a 0-byte device is an empty slot", names["sdd"]["role"] == "empty")
    code, out = disk("list")
    check("the terminal's list says IMAGE STORE and empty slot", "IMAGE STORE" in out and "(empty slot)" in out)

    print("the image store's guard:")
    mount_table(store=False)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-HDD", "--media", "hdd")
    check("not mounted: READ refused, IMAGE STORE NOT CONNECTED, ddrescue never ran, nothing under the mountpoint",
          code == 2 and "IMAGE STORE NOT CONNECTED" in out and calls() == [] and files("widget-wars") == [])
    mount_table()
    os.rename(f"{STORE}/.gatbox-image-store", f"{T}/marker")
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-HDD", "--media", "hdd")
    check("mounted but no marker: refused the same way", code == 2 and "IMAGE STORE NOT CONNECTED" in out and calls() == [])
    os.rename(f"{T}/marker", f"{STORE}/.gatbox-image-store")

    print("refusals:")
    for dev, words in (("sdc", "the image store"), ("sdd", "empty slot"), ("mmcblk0", "no USB disk"),
                       ("nvme0n1", "no USB disk"), ("sdz", "no USB disk")):
        code, out = disk("read", "-d", dev, "-m", "widget-wars", "-l", "X", "--media", "cf")
        check(f"{dev} is never a source ({words})", code == 2 and words in out and calls() == [])
    mount_table(extra=["50 22 8:1 / /media/user/WIDGET rw - vfat /dev/sda1 rw"])
    usb_disk("sda", len(src), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", parts=["sda1"], data=src)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "X", "--media", "hdd")
    check("a mounted partition: refused, eject it first", code == 2 and "mounted" in out and calls() == [])
    mount_table()
    usb_disk("sda", len(src), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=src)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "bad label", "--media", "hdd")
    check("a label with a space is refused", code == 2 and "label:" in out)
    code, out = disk("read", "-d", "sda", "-m", "Widget Wars", "-l", "X", "--media", "hdd")
    check("a machine that isn't a slug is refused", code == 2 and "machine:" in out)

    print("READ, an HDD:")
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-HDD", "--media", "hdd", FAKE_DDRESCUE_MUTATE=1)
    img = f"WW-HDD_{day}_{sha[:8]}"
    check("stored as <label>_<YYYYmmdd>_<sha1:8>.img, with its map and sidecar",
          code == 0 and files("widget-wars") == [img + ".img", img + ".json", img + ".map"])
    check("ddrescue ran once, no scraping, sparse output", calls() == [f"-n --sparse {DEV}/sda {STORE}/widget-wars/WW-HDD.img.part "
                                                                         f"{STORE}/widget-wars/WW-HDD.map"])
    check("an HDD is read once: the drive changing afterwards isn't noticed (no second read to spin it)", "STORED" in out)
    check("the stored bytes are the drive's", open(f"{STORE}/widget-wars/{img}.img", "rb").read() == src)
    side = json.load(open(f"{STORE}/widget-wars/{img}.json"))
    check("sidecar: label, machine, media, the source's identity, hashes, ddrescue summary, one read",
          side["label"] == "WW-HDD" and side["machine"] == "widget-wars" and side["media"] == "hdd"
          and side["source"]["model"] == "WIDGETDRIVE 1000" and side["bytes"] == len(src) and side["sha1"] == sha
          and side["sha256"] == hashlib.sha256(src).hexdigest() and side["ddrescue"]["complete"]
          and side["reads"] == 1 and side["clock"] == "unknown" and side["tool"] == "gatbox-disk")
    check("queued for the archive: archive pending, one queue line keyed by SHA-256",
          side["archive"] == {"state": "pending"}
          and [json.loads(l)["sha256"] for l in open(f"{STORE}/archive-queue.jsonl")] == [side["sha256"]])
    check("the MAME check isn't claimed (it's Task 3)", side["mame"]["state"] == "not run")

    print("the same bytes again:")
    usb_disk("sda", len(src), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=src)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-AGAIN", "--media", "hdd")
    side = json.load(open(f"{STORE}/widget-wars/{img}.json"))
    check("not stored twice: already in the store, no new files", code == 0 and "ALREADY IN THE STORE" in out
          and files("widget-wars") == [img + ".img", img + ".json", img + ".map"])
    check("... the existing sidecar gains this read's date", [r["label"] for r in side["also_read"]] == ["WW-AGAIN"]
          and side["also_read"][0]["date"] == day)
    check("... and no second queue line", len(open(f"{STORE}/archive-queue.jsonl").read().splitlines()) == 1)

    print("READ, flash:")
    card = os.urandom(2 * MiB)
    usb_disk("sdb", len(card), "Generic", "Card Reader SD", port=4, devnum="8:16", data=card)
    by_id("sdb", "usb-Widget_Dock_0001-0:0")        # the same by-id name as sda's: the dock's, not the card's
    code, out = disk("read", "-d", "sdb", "-m", "widget-wars", "-l", "WW-SD", "--media", "sd", FAKE_DDRESCUE_MUTATE=1)
    check("a card that reads differently twice: stopped, flaky, nothing stored",
          code == 2 and "reads differently twice: flaky" in out
          and not any(f.startswith("WW-SD_") for f in files("widget-wars")))
    usb_disk("sdb", len(card), "Generic", "Card Reader SD", port=4, devnum="8:16", data=card)
    for f in files("widget-wars"):
        if f.startswith("WW-SD"):
            os.remove(f"{STORE}/widget-wars/{f}")
    code, out = disk("read", "-d", "sdb", "-m", "widget-wars", "-l", "WW-SD", "--media", "sd")
    side = json.load(open(f"{STORE}/widget-wars/WW-SD_{day}_{hashlib.sha1(card).hexdigest()[:8]}.json"))
    check("a good card: two reads, identical, stored", code == 0 and side["reads"] == 2 and side["identical"])

    print("bad areas and RETRY:")
    old = os.urandom(MiB)
    usb_disk("sde", len(old), "WIDGET", "OLDSPINNER 40", port=5, devnum="8:64", data=old)
    code, out = disk("read", "-d", "sde", "-m", "widget-wars", "-l", "WW-OLD", "--media", "hdd",
                     FAKE_DDRESCUE_BAD="4096:1024")
    check("ddrescue found bad areas: stopped with the map's summary, no automatic retry",
          code == 2 and "bad areas" in out and "1 bad area(s), 1,024 bytes not read" in out and len(calls()) == 1)
    check("... the partial image and the map are kept, nothing named yet",
          {"WW-OLD.img.part", "WW-OLD.map", "WW-OLD.read.json"} <= set(files("widget-wars"))
          and not any(f.startswith("WW-OLD_") for f in files("widget-wars")))
    code, out = disk("retry", "-m", "widget-wars", "-l", "WW-OLD", FAKE_DDRESCUE_BAD="4096:1024")
    check("RETRY runs ddrescue -r3 on the same map; still bad: stopped again", code == 2 and calls()[0].startswith("-r3 ")
          and calls()[0].endswith(f"{STORE}/widget-wars/WW-OLD.map") and "bad areas" in out)
    code, out = disk("retry", "-m", "widget-wars", "-l", "WW-OLD", FAKE_DDRESCUE_BAD="4096:1024", FAKE_DDRESCUE_HEAL=1)
    oldsha = hashlib.sha1(old).hexdigest()
    check("RETRY reads them after all: stored, with both passes in the sidecar",
          code == 0 and f"WW-OLD_{day}_{oldsha[:8]}.img" in files("widget-wars")
          and [p["opts"] for p in json.load(open(f"{STORE}/widget-wars/WW-OLD_{day}_{oldsha[:8]}.json"))["passes"]] == [["-r3"]]
          and open(f"{STORE}/widget-wars/WW-OLD_{day}_{oldsha[:8]}.img", "rb").read() == old)
    code, out = disk("retry", "-m", "widget-wars", "-l", "NOPE")
    check("RETRY with no read in progress: says so", code == 2 and "no read of NOPE in progress" in out)
    code, out = disk("read", "-d", "sde", "-m", "widget-wars", "-l", "WW-FAIL", "--media", "hdd", FAKE_DDRESCUE_EXIT=1)
    check("ddrescue failing: stopped with its words, the map kept for RETRY", code == 2 and "ddrescue exited 1" in out
          and "No such device" in out)

    print("TARGET CHECK:")
    image = f"widget-wars/{img}.img"
    usb_disk("sdf", len(src) - 512, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80")
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("a target 512 bytes too small: refused, both sizes shown", code == 2 and "too small" in out
          and f"{len(src) - 512:,}" in out and f"{len(src):,}" in out)
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", logical=4096)
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("4096-byte sectors: refused", code == 2 and "4096-byte sectors" in out)
    usb_disk("sdf", len(src), "WIDGET", "WIDGETDRIVE 1000", port=6, devnum="8:80")
    by_id("sdf", "usb-Widget_Dock_0001-0:0")
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("the target is the source (same by-id name, model and size): refused", code == 2
          and "this is the drive the image came from" in out)
    code, out = disk("check", "-i", "../etc/passwd", "-d", "sdf")
    check("an image outside the store: refused", code == 2 and "isn't an image in the store" in out)
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80")
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    c = json.loads(out.splitlines()[-1])
    check("a blank target: target_ok, blank, no acknowledgement needed", code == 0 and c["target_ok"]
          and c["contents"]["blank"] and not c["needs_ack"] and not c["already_written"])
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=iso_card(8 * MiB))
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("an installer card on it (ISO 9660): shown, needs an acknowledgement", code == 0 and "iso9660" in out
          and "WIDGET_INSTALL" in out)
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=fat_card(8 * MiB))
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    c = json.loads(out.splitlines()[-1])
    check("a FAT32 card: the filesystem and its label", c["contents"]["fs"] == "vfat"
          and c["contents"]["label"] == "WIDGETCARD" and c["needs_ack"])
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=dos_table(8 * MiB))
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    c = json.loads(out.splitlines()[-1])
    p = c["contents"]["partitions"]
    check("a dos table: each partition's filesystem and label", c["contents"]["table"] == "dos" and len(p) == 1
          and p[0]["fs"] == "ext4" and p[0]["label"] == "WIDGETS" and p[0]["start"] == MiB)

    print("WRITE:")
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=dos_table(8 * MiB))
    code, out = disk("write", "-i", image, "-d", "sdf", stdin="WIDGET SSD 64G\n")
    check("a non-blank target without --overwrite: refused, nothing written", code == 2 and "isn't blank" in out
          and open(f"{DEV}/sdf", "rb").read() == dos_table(8 * MiB))
    tail = os.urandom(8 * MiB - len(src))
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=bytes(len(src)) + tail)
    code, out = disk("write", "-i", image, "-d", "sdf", stdin="WIDGET SSD\n")
    check("the model typed wrong: nothing written", code == 2 and "model wasn't typed" in out
          and open(f"{DEV}/sdf", "rb").read(len(src)) == bytes(len(src)))
    code, out = disk("write", "-i", image, "-d", "sdf", "--overwrite", stdin="WIDGET SSD 64G\n")
    data = open(f"{DEV}/sdf", "rb").read()
    check("written and verified twice; the rest of the larger drive untouched", code == 0 and "VERIFIED" in out
          and data[:len(src)] == src and data[len(src):] == tail and f"{len(tail):,} bytes beyond them untouched" in out)
    lines = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")]
    check("writes.jsonl: one line per write, the refused ones too (not blank, the model mistyped), then verified",
          [l["state"] for l in lines] == ["stopped", "stopped", "verified"] and "isn't blank" in lines[0]["error"]
          and "model wasn't typed" in lines[1]["error"] and lines[-1]["target"]["model"] == "WIDGET SSD 64G"
          and lines[-1]["sha1"] == sha and lines[-1]["armed"] == {"by": "terminal", "typed": True})
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    check("checked again: already written, VERIFY ONLY offered", code == 0 and json.loads(out.splitlines()[-1])["already_written"])
    code, out = disk("write", "-i", image, "-d", "sdf", "--verify-only", stdin="WIDGET SSD 64G\n")
    check("VERIFY ONLY: read back twice, nothing written", code == 0 and "VERIFIED" in out)

    print("WRITE, the guards (in-process):")
    g = load_tool()
    quiet = (lambda *a: None)
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96")
    c = g.check(image, "sdg", say=quiet)
    usb_disk("sdg", 16 * MiB, "WIDGET", "OTHER DRIVE", port=7, devnum="8:96")   # swapped on the dock after the check
    try:
        g.Write(c, armed={"by": "test"}, say=quiet).go()
        swapped = False
    except g.Stop as s:
        swapped = "different drive" in s.what
    check("the drive swapped between CHECK and WRITE: stopped, nothing written",
          swapped and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src)))
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96")
    c = g.check(image, "sdg", say=quiet)
    c["at"] -= 301
    try:
        g.Write(c, armed={"by": "test"}, say=quiet).go()
        expired = False
    except g.Stop as s:
        expired = "more than 5 minutes old" in s.what
    check("a check more than 5 minutes old: refused", expired)
    c = g.check(image, "sdg", say=quiet)
    real_open = os.open

    def busy(path, flags, *a):
        if flags & os.O_EXCL and path.endswith("/sdg"):
            raise OSError(errno.EBUSY, "Device or resource busy")
        return real_open(path, flags, *a)
    g.os.open = busy
    try:
        g.Write(c, armed={"by": "test"}, say=quiet).go()
        held = False
    except g.Stop as s:
        held = "busy" in s.what
    finally:
        g.os.open = real_open
    check("the O_EXCL claim refused (EBUSY: mounted or open elsewhere): stopped, nothing written",
          held and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src)))
    c = g.check(image, "sdg", say=quiet)
    real_read = g.hashed_read
    g.hashed_read = lambda path, length, sha256=False: "0" * 40 if path.endswith("/sdg") else real_read(path, length, sha256)
    try:
        g.Write(c, armed={"by": "test"}, realm="widget-hub", say=quiet).go()
        mismatch = False
    except g.Stop as s:
        mismatch = "verify 1 doesn't match" in s.what
    finally:
        g.hashed_read = real_read
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("a verify mismatch: stopped, with a stopped line in writes.jsonl", mismatch and last["state"] == "stopped"
          and "verify 1" in last["error"])
    check("... the realm it was given is in that line", last["realm"] == "widget-hub")
    r = g.Read("sdb", "widget-wars", "WW-REALM", "ssd", realm="widget-hub", say=quiet)
    usb_disk("sdb", MiB, "Generic", "Card Reader SD", port=4, devnum="8:16", data=os.urandom(MiB))
    info = r.go()
    check("a read's realm is kept in its sidecar",
          json.load(open(info["stored"]["path"][:-4] + ".json"))["realm"] == "widget-hub")

    print(f"\ndisk: {passed} passed, {failed} failed")
    shutil.rmtree(T, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
