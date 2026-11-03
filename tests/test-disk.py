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
sys.path.insert(0, os.path.join(REPO, "tests"))
from fakedisk import MiB, FakeDisks, fat_card  # noqa: E402

TOOL = os.path.join(REPO, "tools/gatbox-disk")
T = tempfile.mkdtemp(prefix="gatbox-disk-test-")
SYS, DEV, STORE, MOUNTINFO = f"{T}/sys", f"{T}/dev", f"{T}/store", f"{T}/mountinfo"
LOG = f"{T}/ddrescue.log"
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


# --- the fake machine: sysfs, devices, mounts (tests/fakedisk.py) -----------------------------------------------------

F = FakeDisks(T)
DISKS = F.blocks
usb_disk, other_disk, by_id, mount_table, env = F.usb_disk, F.other_disk, F.by_id, F.mount_table, F.env


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
    check("the O_EXCL claim refused (EBUSY: mounted or open elsewhere): stopped, nothing written, and the log says so",
          held and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src))
          and json.loads(open(f"{STORE}/writes.jsonl").readlines()[-1])["written"] is False)
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
    check("... and that the drive was written (so it's partly written now)", last["written"] is True)
    r = g.Read("sdb", "widget-wars", "WW-REALM", "ssd", realm="widget-hub", say=quiet)
    usb_disk("sdb", MiB, "Generic", "Card Reader SD", port=4, devnum="8:16", data=os.urandom(MiB))
    info = r.go()
    check("a read's realm is kept in its sidecar",
          json.load(open(info["stored"]["path"][:-4] + ".json"))["realm"] == "widget-hub")

    print("the dashboard's job (gatbox-disk --job):")
    spool = f"{T}/spool"
    os.makedirs(spool)

    def job(req, **extra):
        if os.path.exists(LOG):
            os.remove(LOG)
        with open(f"{spool}/request.json", "w") as f:
            json.dump(dict(req, id="job1", at=time.time()), f)
        r = subprocess.run(["python3", TOOL, "--job", f"{spool}/request.json", "--status", f"{spool}/status.json"],
                           capture_output=True, text=True, env=env(**extra), timeout=120)
        gone = not os.path.exists(f"{spool}/request.json") and not os.path.exists(f"{spool}/running.json")
        return r.returncode, json.load(open(f"{spool}/status.json")), gone

    card2 = os.urandom(MiB)
    usb_disk("sdb", len(card2), "Generic", "Card Reader SD", port=4, devnum="8:16", data=card2)
    ident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sdb")["identity"]
    code, st, gone = job({"op": "read", "device": "sdb", "identity": ident, "machine": "widget-wars", "label": "WW-JOB",
                          "media": "sd", "realm": "widget-hub", "note": "from the 7\""})
    side = json.load(open(st["stored"]["path"][:-4] + ".json")) if st.get("stored") else {}
    check("a READ job: claimed, done, stored, status written, the spool emptied", code == 0 and gone
          and st["state"] == "done" and st["op"] == "read" and st["job"] == "job1" and st["stored"]["new"])
    check("... the realm and note gatbox-web sent are in the sidecar", side.get("realm") == "widget-hub"
          and side.get("note") == "from the 7\"")
    other = dict(ident, model="SOMETHING ELSE")
    code, st, gone = job({"op": "read", "device": "sdb", "identity": other, "machine": "widget-wars", "label": "WW-JOB2",
                          "media": "sd"})
    check("a READ job for a drive swapped since it was picked: stopped, nothing read", st["state"] == "stopped"
          and "different drive from the one picked" in st["error"] and calls() == [] and gone)
    usb_disk("sdh", 8 * MiB, "WIDGET", "WIDGET SSD 64G C", port=8, devnum="8:112")
    tident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sdh")["identity"]
    code, st, gone = job({"op": "check", "image": image, "device": "sdh", "identity": tident})
    check("a CHECK job: target_ok, blank, the time it passed", st["state"] == "done" and st["op"] == "check"
          and st["target_ok"] and st["contents"]["blank"] and not st["needs_ack"] and st["at"] > 0)
    checked = st
    now = time.time()
    code, st, gone = job({"op": "write", "check": checked, "ack": False, "machine": "widget-wars", "realm": "widget-hub",
                          "armed": {"by": "screen", "at": now, "until": now - 1}})
    check("a WRITE job whose 30-second arm ran out: stopped, nothing written", st["state"] == "stopped"
          and "arm ran out" in st["error"] and open(f"{DEV}/sdh", "rb").read(len(src)) == bytes(len(src)))
    stale = dict(checked, at=checked["at"] - 301)
    code, st, gone = job({"op": "write", "check": stale, "ack": False,
                          "armed": {"by": "screen", "at": now, "until": time.time() + 30}})
    check("a WRITE job with a check more than 5 minutes old: stopped", st["state"] == "stopped"
          and "more than 5 minutes old" in st["error"])
    code, st, gone = job({"op": "write", "check": checked, "ack": False, "machine": "widget-wars", "realm": "widget-hub",
                          "armed": {"by": "screen", "at": time.time(), "until": time.time() + 30}})
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("an armed WRITE job: written, verified twice, logged with the arm and the realm", st["state"] == "done"
          and st["verified"] and open(f"{DEV}/sdh", "rb").read(len(src)) == src and last["state"] == "verified"
          and last["armed"]["by"] == "screen" and last["realm"] == "widget-hub")
    old2 = os.urandom(MiB)
    usb_disk("sde", len(old2), "WIDGET", "OLDSPINNER 40", port=5, devnum="8:64", data=old2)
    ident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sde")["identity"]
    code, st, gone = job({"op": "read", "device": "sde", "identity": ident, "machine": "widget-wars", "label": "WW-OLD2",
                          "media": "hdd"}, FAKE_DDRESCUE_BAD="0:512")
    check("a READ job on bad areas: stopped with needs_retry, so the 7\" offers RETRY", st["state"] == "stopped"
          and st.get("needs_retry") is True and st["ddrescue"]["bad_areas"] == 1)
    code, st, gone = job({"op": "retry", "machine": "widget-wars", "label": "WW-OLD2"},
                         FAKE_DDRESCUE_BAD="0:512", FAKE_DDRESCUE_HEAL=1)
    check("a RETRY job: -r3 on the same map, then stored", st["state"] == "done" and calls()[0].startswith("-r3 ")
          and st["stored"]["new"])
    code, st, gone = job({"op": "format"})
    check("an unknown job: stopped, saying so", st["state"] == "stopped" and "unknown job" in st["error"] and gone)

    web_side(image)

    print(f"\ndisk: {passed} passed, {failed} failed")
    shutil.rmtree(T, ignore_errors=True)
    return 0 if failed == 0 else 1


def _lan_addrs():
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=False).stdout.split()
    except (FileNotFoundError, OSError):
        pass
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                             check=False).stdout
        return [ln.split()[3].split("/")[0] for ln in out.splitlines() if len(ln.split()) > 3]
    except (FileNotFoundError, OSError, IndexError):
        return []


def web_side(image):
    """gatbox-web's DISK API, with a runner thread standing in for gatbox-disk.path + .service."""
    import socket
    import threading
    import urllib.error
    import urllib.request
    print("gatbox-web's DISK API:")
    port, B = 8098, "http://127.0.0.1:8098"
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is in use")
    w = f"{T}/web"
    for d in ("log", "run", "ctrl", "cache", "data", "spool", "sync"):
        os.makedirs(f"{w}/{d}")
    for f in ("profiles.json", "gatbox-machine-specs.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{w}/data/")
    json.dump({"meta": {}, "video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x"}],
               "pinball": []}, open(f"{w}/data/gatbox-barcade-roster.json", "w"))
    # one hub, whose roster has the machine: its realm is that hub's name
    json.dump({"hubs": [{"name": "default", "label": "WIDGET HUB"}]}, open(f"{w}/sync/status.json", "w"))
    json.dump({"machines": [{"slug": "widget-wars", "name": "Widget Wars"}]}, open(f"{w}/sync/roster.json", "w"))
    wenv = dict(env(), GATBOX_WEB_PORT=str(port), STATE_DIRECTORY=f"{w}/ctrl", CACHE_DIRECTORY=f"{w}/cache",
                GATBOX_LOGDIR=f"{w}/log", GATBOX_RUNDIR=f"{w}/run", GATBOX_DATA=f"{w}/data",
                GATBOX_SYNC_STATE=f"{w}/sync", GATBOX_DISK_SPOOL=f"{w}/spool", GATBOX_DUMP_SPOOL=f"{w}/nodump",
                MPLCONFIGDIR=f"{w}/cache/mpl")
    web = subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=wenv,
                           stdout=open(f"{w}/web.log", "w"), stderr=subprocess.STDOUT)
    stop = threading.Event()

    def runner():                                   # gatbox-disk.path: a request appears -> gatbox-disk --job
        while not stop.is_set():
            if os.path.exists(f"{w}/spool/request.json"):
                bad = {"FAKE_DDRESCUE_BAD": "0:512"} if os.path.exists(f"{w}/bad") else {}
                heal = {"FAKE_DDRESCUE_HEAL": "1"} if os.path.exists(f"{w}/heal") else {}
                subprocess.run(["python3", TOOL, "--job", f"{w}/spool/request.json", "--status", f"{w}/spool/status.json"],
                               env=env(GATBOX_API=B, **bad, **heal), capture_output=True, timeout=120, check=False)
            time.sleep(0.2)
    threading.Thread(target=runner, daemon=True).start()

    def call(method, path, body=None, base=B):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except ValueError:
                return e.code, None

    def done():
        for _ in range(300):
            _, st = call("GET", "/api/disk")
            if not st["busy"] and st["status"] and st["status"].get("state") in ("done", "stopped"):
                return st["status"]
            time.sleep(0.2)
        return {}

    def disk_of(name):
        return next(d for d in call("GET", "/api/disk")[1]["disks"] if d["name"] == name)

    try:
        for _ in range(50):
            try:
                call("GET", "/kiosk/state")
                break
            except OSError:
                time.sleep(0.1)
        code, st = call("GET", "/api/disk")
        names = {d["name"]: d["role"] for d in st["disks"]}
        check("GET /api/disk: the USB disks with their roles, the store mounted with its space, the spool there",
              code == 200 and names.get("sdc") == "store" and names.get("sdd") == "empty" and "mmcblk0" not in names
              and st["store"]["mounted"] and st["store"]["total"] > 0 and st["spool"] and not st["busy"])
        code, r = call("GET", "/api/disk/images")
        check("GET /api/disk/images: the store's images from their sidecars, with label and source drive",
              code == 200 and any(i["image"] == image and i["label"] == "WW-HDD" and i["source"] == "WIDGET WIDGETDRIVE 1000"
                                  for i in r["images"]))
        card = os.urandom(MiB)
        usb_disk("sdb", len(card), "Generic", "Card Reader SD", port=4, devnum="8:16", data=card)
        sdb = disk_of("sdb")
        good = {"device": "sdb", "identity": sdb["identity"], "machine": "widget-wars", "label": "WEB-SD", "media": "sd"}
        bad = [call("POST", "/api/disk/read", dict(good, **b))[0] for b in (
            {"label": "../x"}, {"media": "floppy"}, {"machine": "nope"}, {"device": "sdq"}, {"device": "sdd"},
            {"device": "sdc"}, {"identity": dict(sdb["identity"], model="OTHER")})]
        check("READ: bad label / media / machine / no such disk / empty slot / the store / not the picked drive "
              "-> 400 / 400 / 404 / 404 / 409 / 409 / 409", bad == [400, 400, 404, 404, 409, 409, 409])
        mount_table(store=False)
        code, r = call("POST", "/api/disk/read", good)
        check("READ with the store unplugged -> 409, IMAGE STORE NOT CONNECTED", code == 409
              and "IMAGE STORE NOT CONNECTED" in r["error"])
        mount_table()
        code, r = call("POST", "/api/disk/read", good)
        st = done()
        side = json.load(open(st["stored"]["path"][:-4] + ".json")) if st.get("stored") else {}
        check("READ -> 202, queued with the machine's realm; the job reads and stores it", code == 202
              and r["realm"] == "default" and st.get("state") == "done" and side.get("realm") == "default")
        open(f"{w}/spool/running.json", "w").write("{}")
        code, r = call("POST", "/api/disk/read", dict(good, label="WEB-SD2"))
        os.remove(f"{w}/spool/running.json")
        check("one disk job at a time: 409 while one runs", code == 409)
        usb_disk("sdh", 8 * MiB, "WIDGET", "WIDGET SSD 64G C", port=8, devnum="8:112", data=fat_card(8 * MiB))
        sdh = disk_of("sdh")
        target = {"image": image, "device": "sdh"}
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            code, r = call("POST", "/api/disk/write", target, base=B.replace("127.0.0.1", lan))
            check("WRITE from the network (a phone) -> 403: only the Pi's own screen arms", code == 403)
        code, r = call("POST", "/api/disk/write", target)
        check("WRITE with no CHECK first -> 409", code == 409 and "CHECK this image and drive first" in r["error"])
        code, r = call("POST", "/api/disk/check", dict(target, image="../../etc/passwd", identity=sdh["identity"]))
        check("CHECK an image that isn't in the store -> 400", code == 400)
        code, r = call("POST", "/api/disk/check", dict(target, identity=sdh["identity"]))
        st = done()
        check("CHECK -> 202; the job finds a FAT32 card: target_ok, needs an acknowledgement", code == 202
              and st.get("op") == "check" and st.get("target_ok") and st.get("needs_ack"))
        code, r = call("POST", "/api/disk/write", target)
        check("WRITE a non-blank target without ack -> 409", code == 409 and "isn't blank" in r["error"])
        code, r = call("POST", "/api/disk/write", dict(target, device="sdb", ack=True))
        check("WRITE another drive than the checked one -> 409", code == 409)
        t0 = time.time()
        code, r = call("POST", "/api/disk/write", dict(target, ack=True))
        check("WRITE after the CHECK, on the Pi -> 202, armed by the screen for 30 s, the check inside the request",
              code == 202 and r["armed"]["by"] == "screen" and abs(r["armed"]["until"] - (t0 + 30)) < 3
              and r["check"]["target_ok"] and r["check"]["device"] == "sdh")
        st = done()
        src = open(f"{STORE}/{image}", "rb").read()
        check("... the job writes and verifies: the drive holds the image", st.get("op") == "write"
              and st.get("state") == "done" and st.get("verified") and open(f"{DEV}/sdh", "rb").read(len(src)) == src)
        write_id = st.get("job")
        code, r = call("POST", "/api/disk/write", dict(target, ack=True))
        check("WRITE again without a new CHECK -> 409 (the last job was the write)", code == 409)
        code, r = call("POST", "/api/disk/check", dict(target, identity=sdh["identity"]))
        st = done()
        sp = f"{w}/spool/status.json"
        j = json.load(open(sp))
        j["at"] -= 301
        json.dump(j, open(sp, "w"))
        code, r = call("POST", "/api/disk/write", dict(target, verify_only=True))
        check("a CHECK more than 5 minutes old -> 409", code == 409)
        code, r = call("GET", f"/api/disk/checklist?write={write_id}")
        check("the swap checklist: one per write, made when it's queued: its ten items, none ticked, the image and "
              "the target named", code == 200 and len(r["items"]) == 10 and not any(i["done"] for i in r["items"])
              and r["image"] == image and r["device"] == "sdh" and r["target"] == "WIDGET WIDGET SSD 64G C")
        code, r = call("GET", "/api/disk/checklist")
        check("... without a write id: the newest one", code == 200 and r["write"] == write_id)
        code, r = call("POST", "/api/disk/checklist", {"write": write_id, "item": "five_volts", "done": True})
        ticked = [i for i in r["items"] if i["done"]] if code == 200 else []
        check("... a tick is kept, with its time", code == 200 and [i["item"] for i in ticked] == ["five_volts"]
              and ticked[0]["at"] > 0)
        bad = [call("POST", "/api/disk/checklist", b)[0] for b in (
            {"write": write_id, "item": "nope", "done": True}, {"write": "../x", "item": "games", "done": True},
            {"write": write_id, "item": "games", "done": "yes"})]
        check("... a bad item, write id or done -> 400 each", bad == [400, 400, 400])
        code, r = call("POST", "/api/disk/checklist", {"write": "0123456789ab", "item": "games", "done": True})
        code2, _ = call("GET", "/api/disk/checklist?write=0123456789ab")
        check("... a write with no checklist -> 404, for a tick and a read", code == 404 and code2 == 404)
        code, r = call("POST", "/api/disk/checklist/new", {})
        own = r.get("write") if code == 200 else None
        code2, _ = call("POST", "/api/disk/checklist", {"write": own, "item": "attract", "done": True})
        code3, r3 = call("GET", "/api/disk/checklist")
        check("... a checklist on its own: no image, ticks kept; it's now the newest", code == 200 and r["image"] is None
              and code2 == 200 and code3 == 200 and r3["write"] == own and r3["write"] != write_id
              and [i["item"] for i in r3["items"] if i["done"]] == ["attract"])
        code, r = call("GET", f"/api/disk/checklist?write={write_id}")
        check("... the write's own ticks are still its own", [i["item"] for i in r["items"] if i["done"]] == ["five_volts"])
        code, r = call("GET", "/api/disk/checklists")
        check("... both kept, newest first, with their ticks counted", code == 200
              and [(x["write"], x["done"], x["of"]) for x in r["checklists"]] == [(own, 1, 10), (write_id, 1, 10)])
        code, r = call("POST", "/api/disk/retry", {"machine": "widget-wars", "label": "NOPE"})
        check("RETRY with no read in progress -> 404", code == 404)
        usb_disk("sde", MiB, "WIDGET", "OLDSPINNER 40", port=5, devnum="8:64", data=os.urandom(MiB))
        open(f"{w}/bad", "w").close()
        code, r = call("POST", "/api/disk/read", {"device": "sde", "identity": disk_of("sde")["identity"],
                                                  "machine": "widget-wars", "label": "WEB-OLD", "media": "hdd"})
        st = done()
        check("a READ on bad areas: stopped with needs_retry", st.get("state") == "stopped" and st.get("needs_retry"))
        open(f"{w}/heal", "w").close()
        code, r = call("POST", "/api/disk/retry", {"machine": "widget-wars", "label": "WEB-OLD"})
        st = done()
        check("RETRY -> 202; the job reads the bad areas after all and stores it", code == 202 and st.get("state") == "done")
        os.remove(f"{w}/bad")
        os.remove(f"{w}/heal")
        codes = [call("GET", p)[0] for p in (f"/api/disk/images/{image}", f"/disks/{image}", f"/srv/gatbox/disks/{image}",
                                             f"/dash/../../{image}")]
        check("no route serves the store's bytes: 404 everywhere", all(c == 404 for c in codes))
        check("server log: no errors", "Traceback" not in open(f"{w}/web.log").read())
    finally:
        stop.set()
        web.terminate()
        web.wait(10)


if __name__ == "__main__":
    sys.exit(main())
