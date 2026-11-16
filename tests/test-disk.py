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
import glob
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


def arm():
    """The arm gatbox-web gives a write: 30 seconds from now."""
    return {"by": "test", "at": time.time(), "until": time.time() + 30}


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
    check("sidecar: the image's own fingerprint, the bench number (non-zero bytes in the first 64 MiB) and a SHA-1",
          side.get("fingerprint") == {"bytes": len(src), "nonzero": len(src) - src.count(0), "sha1": sha})
    code, out = disk("list", "--json")
    check("the image list carries it for the 7\"", [i.get("fingerprint") for i in json.loads(out)["images"]] == [len(src) - src.count(0)])

    print("the same bytes again:")
    usb_disk("sda", len(src), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=src)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-AGAIN", "--media", "hdd")
    side = json.load(open(f"{STORE}/widget-wars/{img}.json"))
    check("not stored twice: already in the store, no new files", code == 0 and "ALREADY IN THE STORE" in out
          and files("widget-wars") == [img + ".img", img + ".json", img + ".map"])
    check("... the existing sidecar gains this read's date", [r["label"] for r in side["also_read"]] == ["WW-AGAIN"]
          and side["also_read"][0]["date"] == day)
    check("... and no second queue line", len(open(f"{STORE}/archive-queue.jsonl").read().splitlines()) == 1)

    print("a drive that is not a machine's (the drive browser's note, 2026-11-15: a personal drive's image goes nowhere without a yes):")
    fam = os.urandom(2 * MiB)
    usb_disk("sdq", len(fam), "WIDGET", "FAMILYDRIVE 500", port=17, devnum="8:256", data=fam)
    code, out = disk("read", "-d", "sdq", "-m", "unassigned", "-l", "FAMILY-DISK", "--media", "hdd")
    side_u = json.load(open(f"{STORE}/unassigned/FAMILY-DISK_{day}_{hashlib.sha1(fam).hexdigest()[:8]}.json")) if code == 0 else {}
    check("a READ for no machine: stored, its sidecar says archive 'none', not for a machine, and nothing is added to the queue",
          code == 0 and side_u.get("archive") == {"state": "none", "why": "not for a machine"}
          and len(open(f"{STORE}/archive-queue.jsonl").read().splitlines()) == 1)
    F.unplug("sdq")

    print("the same bytes under another machine, or none (2026-11-15, from the Pi: the check looked only in the machine's own folder):")
    fam_name = f"FAMILY-DISK_{day}_{hashlib.sha1(fam).hexdigest()[:8]}"
    ww_before = files("widget-wars")
    usb_disk("sdq", len(fam), "WIDGET", "FAMILYDRIVE 500", port=17, devnum="8:256", data=fam)
    code, out = disk("read", "-d", "sdq", "-m", "widget-wars", "-l", "FAMILY-AGAIN", "--media", "hdd")
    side_u = json.load(open(f"{STORE}/unassigned/{fam_name}.json"))
    check("the same drive read again for a machine: it is already in the store, under 'unassigned', and says so; no second copy is made",
          code == 0 and "ALREADY IN THE STORE" in out and f"unassigned/{fam_name}.img" in out and files("widget-wars") == ww_before
          and files("unassigned").count(fam_name + ".img") == 1)
    check("... the existing sidecar notes this read, with the machine it was for, and is still 'not for a machine' (nothing moved, nothing queued)",
          [(r["label"], r.get("machine")) for r in side_u["also_read"]] == [("FAMILY-AGAIN", "widget-wars")]
          and side_u["archive"] == {"state": "none", "why": "not for a machine"} and len(open(f"{STORE}/archive-queue.jsonl").read().splitlines()) == 1)
    F.unplug("sdq")
    usb_disk("sdq", len(src), "WIDGET", "WIDGETDRIVE 1000", port=17, devnum="8:256", data=src)
    code, out = disk("read", "-d", "sdq", "-m", "unassigned", "-l", "WW-LATER", "--media", "hdd")
    side_w = json.load(open(f"{STORE}/widget-wars/{img}.json"))
    check("and the other way round: a machine's image read again for no machine is found in that machine's folder, and 'unassigned' gets nothing new",
          code == 0 and "ALREADY IN THE STORE" in out and f"widget-wars/{img}.img" in out
          and [r["label"] for r in side_w["also_read"]][-1] == "WW-LATER" and side_w["also_read"][-1]["machine"] == "unassigned"
          and files("unassigned") == [fam_name + ".img", fam_name + ".json", fam_name + ".map"])
    F.unplug("sdq")
    # an image kept twice already (an older store: the check only looked in the machine's own folder): a read notes itself on the copy
    # in its own folder, and a sidecar whose image is gone, or that is not JSON, is not an image in the store and never matches (they are named
    # to sort before the real copy, so the search has to get past them)
    for ext_ in (".img", ".json"):
        os.link(f"{STORE}/unassigned/{fam_name}{ext_}", f"{STORE}/widget-wars/{fam_name}{ext_}")
    open(f"{STORE}/widget-wars/0-orphan.json", "w").write(json.dumps({"sha1": hashlib.sha1(fam).hexdigest()}))
    open(f"{STORE}/widget-wars/0-broken.json", "w").write("{not json")
    open(f"{STORE}/widget-wars/0-broken.img", "w").close()                  # with an image, so the search does try to read it
    usb_disk("sdq", len(fam), "WIDGET", "FAMILYDRIVE 500", port=17, devnum="8:256", data=fam)
    code, out = disk("read", "-d", "sdq", "-m", "widget-wars", "-l", "FAMILY-THIRD", "--media", "hdd")
    both = [os.path.basename(os.path.dirname(p)) for p in sorted(glob.glob(f"{STORE}/*/{fam_name}.json")) if "FAMILY-THIRD" in open(p).read()]
    check("kept twice: the read is noted on the copy in the machine's own folder, an orphan sidecar and a broken one are ignored, nothing crashes",
          code == 0 and "ALREADY IN THE STORE" in out and f"widget-wars/{fam_name}.img" in out and "0-orphan" not in out and both == ["widget-wars"])
    for n_ in ("0-orphan.json", "0-broken.json", "0-broken.img", fam_name + ".img", fam_name + ".json"):
        os.remove(f"{STORE}/widget-wars/{n_}")
    F.unplug("sdq")

    print("the drive's own identity (Task 0, 2026-11-06: the dock's INQUIRY names the dock):")
    usb_disk("sdk", 3 * MiB, "XinTop", "XT-U33502", port=11, devnum="8:160", data=os.urandom(3 * MiB),
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"))
    usb_disk("sdl", 0, "Generic-", "Multi-Card", port=12, devnum="8:176",
             udev={"ID_BUS": "usb", "ID_MODEL": "Multi-Card", "ID_SERIAL_SHORT": "20120501030900000"})
    code, out = disk("list", "--json")
    names = {d["name"]: d["identity"] for d in json.loads(out)["disks"]}
    k, r = names["sdk"], names["sdl"]
    check("a drive behind the dock is named by what it says itself: its own model (padded, \\x20-escaped by udev) and serial",
          k["model"] == "WDC WD800JD-75MSA1" and k["vendor"] == "" and k["serial"] == "WD-WIDGET0001" and k.get("by") == "ata")
    check("... and the dock's INQUIRY is kept as the bridge", k.get("bridge") == "XinTop XT-U33502")
    check("a serial on a USB bus is the reader's, not the card's: not trusted, the INQUIRY stands",
          r["serial"] is None and r.get("by") == "inquiry" and r["vendor"] == "Generic-" and r["model"] == "Multi-Card")
    check("a drive with no udev data (nothing passed through) falls back to the INQUIRY",
          names["sda"].get("by") == "inquiry" and names["sda"]["model"] == "WIDGETDRIVE 1000" and names["sda"]["serial"] is None)
    code, out = disk("list")
    check("the terminal's list shows the drive, not the dock", "WDC WD800JD-75MSA1" in out and "serial WD-WIDGET0001" in out)

    print("DRIVE INFO (what udev kept from the drive's own IDENTIFY, and sysfs; nothing is sent to the drive):")
    usb_disk("sdk", 3 * MiB, "XinTop", "XT-U33502", port=11, devnum="8:160", data=os.urandom(3 * MiB), physical=4096,
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"),
             udev={"ID_REVISION": "10.0", "ID_ATA_ROTATION_RATE_RPM": "7200", "ID_ATA_SATA": "1",
                   "ID_ATA_SATA_SIGNAL_RATE_GEN1": "1", "ID_ATA_SATA_SIGNAL_RATE_GEN2": "1",
                   "ID_ATA_WRITE_CACHE": "1", "ID_ATA_WRITE_CACHE_ENABLED": "1",
                   "ID_ATA_FEATURE_SET_HPA": "1", "ID_ATA_FEATURE_SET_HPA_ENABLED": "1",
                   "ID_ATA_FEATURE_SET_SMART": "1", "ID_ATA_FEATURE_SET_SMART_ENABLED": "0"})
    usb_disk("sdm", 3 * MiB, "XinTop", "XT-U33502", port=13, devnum="8:192", data=os.urandom(3 * MiB),
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0009"))                # IDENTIFY gave a model and serial and nothing else
    code, out = disk("list", "--json")
    info = {d["name"]: d["info"] for d in json.loads(out)["disks"]}
    ik, il, im = info["sdk"], info["sdl"], info["sdm"]
    check("an ATA drive: its firmware, spin speed, SATA speed, write cache and feature sets, as udev kept them",
          ik["identify"] and ik["firmware"] == "10.0" and ik["rpm"] == 7200 and ik["sata_gen"] == 2 and ik["write_cache"] is True
          and ik["features"] == [{"name": "Host Protected Area", "enabled": True}, {"name": "SMART", "enabled": False}])
    check("... and what the kernel knows: the sectors (512-byte units), the logical and physical sector size",
          ik["sectors"] == 3 * MiB // 512 and ik["logical"] == 512 and ik["physical"] == 4096)
    check("a key udev did not keep is not shown, not guessed: an ATA drive with only a model and serial has no firmware, "
          "spin speed, SATA speed, cache or features", im["identify"] and im["firmware"] is None and im["rpm"] is None
          and im["sata_gen"] is None and im["write_cache"] is None and im["features"] == [])
    check("a card reader's disk has no IDENTIFY to show: only the kernel's facts", not il["identify"] and il["firmware"] is None
          and il["features"] == [] and il["logical"] == 512)
    F.unplug("sdm")

    print("the source, by the drive's own serial:")
    srcA = os.urandom(3 * MiB)
    usb_disk("sdk", len(srcA), "XinTop", "XT-U33502", port=11, devnum="8:160", data=srcA,
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"))
    code, out = disk("read", "-d", "sdk", "-m", "widget-wars", "-l", "ATA-A", "--media", "hdd")
    imgA = f"widget-wars/ATA-A_{day}_{hashlib.sha1(srcA).hexdigest()[:8]}.img"
    sideA = json.load(open(f"{STORE}/{imgA[:-4]}.json"))
    check("READ records the drive's own model and serial as the source", code == 0
          and sideA["source"]["model"] == "WDC WD800JD-75MSA1" and sideA["source"]["serial"] == "WD-WIDGET0001")
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=bytes(len(srcA)),
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0002"))
    code, out = disk("check", "-i", imgA, "-d", "sdl", "--json")
    check("another drive of the same model and size, another serial: not the source", code == 0
          and json.loads(out.splitlines()[-1])["target_ok"])
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=srcA,
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0002"))
    code, out = disk("check", "-i", imgA, "-d", "sdl", "--json")
    check("... even holding the image's bytes (a copy already written): not the source, VERIFY ONLY offered",
          code == 0 and json.loads(out.splitlines()[-1])["already_written"])
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=bytes(len(srcA)),
             ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"))
    code, out = disk("check", "-i", imgA, "-d", "sdl")
    check("the same serial is the source, whatever is on it now", code == 2 and "this is the drive the image came from" in out
          and "same model and serial" in out)
    usb_disk("sdk", 3 * MiB, "XinTop", "XT-U33502", port=11, devnum="8:160", data=os.urandom(3 * MiB))
    usb_disk("sdl", 0, "Generic-", "Multi-Card", port=12, devnum="8:176")
    F.unplug("sdk")
    F.unplug("sdl")

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
    usb_disk("sdf", len(src), "WIDGET", "WIDGETDRIVE 1000", port=6, devnum="8:80", data=src)
    by_id("sdf", "usb-Widget_Dock_0001-0:0")
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("the target is the source (same make and size, and it starts with the image's bytes): refused, and the hint names RESTORE",
          code == 2 and "this is the drive the image came from" in out and "RESTORE" in out)
    # The dock names itself and a card has no serial, so make and size are the same for a new card of the same size. What
    # is on it is not: a blank or different card is a different card, and the check goes on.
    usb_disk("sdf", len(src), "WIDGET", "WIDGETDRIVE 1000", port=6, devnum="8:80")
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    check("a new drive of the same make and size, blank: not the source, the check goes on",
          code == 0 and json.loads(out.splitlines()[-1])["target_ok"])
    usb_disk("sdf", len(src), "WIDGET", "WIDGETDRIVE 1000", port=6, devnum="8:80", data=fat_card(len(src)))
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    check("... or holding something else: still not the source, and it needs the acknowledgement",
          code == 0 and json.loads(out.splitlines()[-1])["needs_ack"])
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
    # An arcade card is raw: no partition table, no filesystem, and sector 0 (even the first MiB) can be empty while the
    # game starts further in (the Gauntlet Legends CF was like that). "Blank" must not mean "the first MiB is empty".
    raw = bytes(4 * MiB) + os.urandom(4 * MiB)
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=raw)
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    c = json.loads(out.splitlines()[-1])
    check("a raw card, first 4 MiB empty and data after: not blank, needs the acknowledgement",
          code == 0 and not c["contents"]["blank"] and c["needs_ack"])
    check("its fingerprint is the bench number (non-zero bytes in the first 64 MiB)",
          c["contents"]["fingerprint"]["nonzero"] == len(raw) - raw.count(0) and c["contents"]["fingerprint"]["bytes"] == len(raw))
    code, out = disk("check", "-i", image, "-d", "sdf")
    check("the check says so in words: raw data, with the count", "first 64 MiB aren't empty" in out and "non-zero bytes" in out)
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=b"\xff" * (8 * MiB))
    code, out = disk("check", "-i", image, "-d", "sdf", "--json")
    check("an erased flash card (all 0xFF) is still blank", json.loads(out.splitlines()[-1])["contents"]["blank"])

    print("WRITE:")
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=dos_table(8 * MiB))
    code, out = disk("write", "-i", image, "-d", "sdf", stdin="WIDGET SSD 64G\n")
    check("a non-blank target without --overwrite: refused, nothing written", code == 2 and "isn't blank" in out
          and open(f"{DEV}/sdf", "rb").read() == dos_table(8 * MiB))
    # Zeros for the first 3 MiB and data after them: not blank (a raw card can look like this), so it needs --overwrite.
    tail = os.urandom(8 * MiB - len(src))
    usb_disk("sdf", 8 * MiB, "WIDGET", "WIDGET SSD 64G", port=6, devnum="8:80", data=bytes(len(src)) + tail)
    code, out = disk("write", "-i", image, "-d", "sdf", "--overwrite", stdin="WIDGET SSD\n")
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
        g.Write(c, armed=arm(), say=quiet).go()
        swapped = False
    except g.Stop as s:
        swapped = "different drive" in s.what
    check("the drive swapped between CHECK and WRITE: stopped, nothing written",
          swapped and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src)))
    # Two drives of the same model and size behind the dock, blank both: nothing on them tells them apart, their serials do.
    ata = lambda n: ("WDC WD800JD-75MSA1", f"WD-WIDGET000{n}")        # noqa: E731
    usb_disk("sdg", 8 * MiB, "XinTop", "XT-U33502", port=7, devnum="8:96", ata=ata(2))
    c = g.check(image, "sdg", say=quiet)
    usb_disk("sdg", 8 * MiB, "XinTop", "XT-U33502", port=7, devnum="8:96", ata=ata(3))      # another drive, the same make and size
    try:
        g.Write(c, ack=True, armed=arm(), say=quiet).go()
        other_drive = False
    except g.Stop as s:
        other_drive = "different drive" in s.what
    check("another drive of the same model and size swapped in, both blank: stopped by its serial, nothing written",
          other_drive and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src)))
    # The same make and size on the dock, a different card in it: the identity cannot tell, what is on it can.
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96")
    c = g.check(image, "sdg", say=quiet)
    other = os.urandom(8 * MiB)
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96", data=other)   # another card, same identity
    try:
        g.Write(c, ack=True, armed=arm(), say=quiet).go()        # --overwrite does not excuse it
        changed = False
    except g.Stop as s:
        changed = "changed since the check" in s.what
    check("a different card of the same make and size swapped in after the check: stopped even with --overwrite, nothing written",
          changed and open(f"{DEV}/sdg", "rb").read() == other)
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96")
    usb_disk("sdg", 8 * MiB, "WIDGET", "WIDGET SSD 64G B", port=7, devnum="8:96")
    c = g.check(image, "sdg", say=quiet)
    c["at"] -= 301
    try:
        g.Write(c, armed=arm(), say=quiet).go()
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
        g.Write(c, armed=arm(), say=quiet).go()
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
    g.hashed_read = lambda path, length, sha256=False, on_progress=None: "0" * 40 if path.endswith("/sdg") else real_read(path, length, sha256, on_progress)
    try:
        g.Write(c, armed=arm(), realm="widget-hub", say=quiet).go()
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

    print("progress while a long step runs (a stand-in ddrescue that takes its time and is noisy):")
    import threading
    blob = os.urandom(3 * MiB)
    usb_disk("sda", len(blob), "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=blob)
    ticks, real_tick = [], g.Work.tick
    g.Work.tick = lambda self, detail, progress: (ticks.append((self.current, detail, progress)), real_tick(self, detail, progress))[1]
    g.POLL_S, g.TICK_S = 0.2, 0                     # the tool's own 2 s and 5 s, so that a 2 s stand-in shows several ticks
    os.environ.update(FAKE_DDRESCUE_SLOW="2", FAKE_DDRESCUE_NOISE="1")
    status, seen, ran = f"{T}/prog-status.json", [], {}

    def long_read():
        try:
            ran["info"] = g.Read("sda", "widget-wars", "WW-PROG", "hdd", status=status, say=quiet).go()
        except Exception as e:                      # reported by the check below
            ran["error"] = e
    th = threading.Thread(target=long_read, daemon=True)
    th.start()
    deadline = time.time() + 60                     # a read that never ends fails the check below instead of hanging the run
    while th.is_alive() and time.time() < deadline:
        try:
            s = json.load(open(status))
            seen.append((s["step"], s["progress"], s.get("detail", "")))
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    th.join(1)
    for k in ("FAKE_DDRESCUE_SLOW", "FAKE_DDRESCUE_NOISE"):
        del os.environ[k]
    reads = [t for t in ticks if t[0] == "read"]
    check("a ddrescue that prints more than a pipe holds still finishes: its output goes to files, not to a pipe nobody reads",
          not th.is_alive() and "info" in ran and "error" not in ran)
    check("while ddrescue runs the bar moves: how much it has read, between the 5% it starts at and the 80% hashing starts at",
          len(reads) >= 2 and all(0.05 < p <= 0.78 + 1e-9 for _, _, p in reads) and any("of 3 MB read from the drive" in d for _, d, _ in reads))
    check("... and the status file the dashboard reads says so while it runs, not only at the end",
          any(st == "read" and 0.05 < p < 0.78 and "read from the drive" in d for st, p, d in seen))
    hashes = [t for t in ticks if t[0] == "hash"]
    check("hashing ticks too, ending at 97% of the bar on a hard disk, and the bar never goes backwards",
          hashes and hashes[-1][2] == 0.97 and hashes[-1][1] == "SHA-1 and SHA-256: 3 MB of 3 MB"
          and [t[2] for t in ticks] == sorted(t[2] for t in ticks))
    check("a tick is not a step: the job's list of steps stays short",
          len(ran["info"]["steps"]) <= 6 and [s["step"] for s in ran["info"]["steps"]][:3] == ["identified", "read", "hash"])
    usb_disk("sdb", 9 * MiB, "Generic", "Card Reader SD", port=4, devnum="8:16", data=os.urandom(9 * MiB))
    del ticks[:]
    g.Read("sdb", "widget-wars", "WW-FLASH", "sd", say=quiet).go()
    check("a card is read twice, so its first hash ends at 85% and the second read goes on to 97%, the bar never going back",
          [t for t in ticks if t[0] == "hash"][-1][2] == 0.85 and [t for t in ticks if t[0] == "second read"][-1][2] == 0.97
          and len([t for t in ticks if t[0] == "second read"]) == 3 and [t[2] for t in ticks] == sorted(t[2] for t in ticks))
    g.Work.tick = real_tick
    g.POLL_S, g.TICK_S = 2.0, 5.0
    w = g.Work(status=f"{T}/tick-status.json", say=quiet)
    w.step("read", "x", progress=0.05)
    w.tick("early", 0.3)
    early = json.load(open(f"{T}/tick-status.json"))["progress"]
    w._ticked -= 6
    w.tick("later", 0.4)
    later = json.load(open(f"{T}/tick-status.json"))
    check("the status file is on the SD card: a tick right after another writes nothing, and one 5 s later does",
          early == 0.05 and later["progress"] == 0.4 and later["detail"] == "later" and len(w.info["steps"]) == 1)
    code, out = disk("read", "-d", "sda", "-m", "widget-wars", "-l", "WW-NOPE", "--media", "hdd", FAKE_DDRESCUE_EXIT="1")
    check("a ddrescue that fails still reports why, from its own stderr", code == 2 and "ddrescue exited 1" in out
          and "Can't open input file" in out)

    print("WRITE, what the audit of this path found (in-process):")
    import fcntl

    def blank_target(model="WIDGET SSD 64G B", data=None):
        usb_disk("sdg", 8 * MiB, "WIDGET", model, port=7, devnum="8:96", data=data)

    def untouched():
        return open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(len(src))

    def store_copy(tag):
        """A second image in the store, its own name and the same sidecar, to damage without touching the first."""
        base = f"{STORE}/{image[:-4]}"
        new = f"{STORE}/widget-wars/{tag}"
        shutil.copy(base + ".img", new + ".img")
        shutil.copy(base + ".json", new + ".json")
        return f"widget-wars/{tag}.img", new + ".img"

    def refused(c, what, **kw):
        try:
            g.Write(c, armed=arm(), say=quiet, **kw).go()
        except g.Stop as s:
            return what.lower() in s.what.lower()
        return False

    # the bar through a WRITE (2026-11-15, from the Pi's READ: it sat at 5% through the whole step, and the WRITE path had the same
    # gap: 5% for the write, then the first read-back at 85% and the second at 100% while it was still reading)
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    pub, real_pub = [], g.Work._publish
    g.Work._publish = lambda self, name, detail, progress: (pub.append((name, progress, detail)), real_pub(self, name, detail, progress))[1]
    real_chunk, real_tickS, g.CHUNK, g.TICK_S = g.CHUNK, g.TICK_S, 512 * 1024, 0
    try:
        g.Write(c, armed=arm(), say=quiet).go()
        wpub = list(pub)
        del pub[:]
        c2 = g.check(image, "sdg", say=quiet)
        g.Write(c2, armed=arm(), say=quiet).go(verify_only=True)
        vpub = list(pub)
    finally:
        g.Work._publish, g.CHUNK, g.TICK_S = real_pub, real_chunk, real_tickS

    def within(pubs, step, lo, hi, text=""):
        got = [p for st, p, d in pubs if st == step and text in d and p is not None]
        return len(got) >= 2 and all(lo <= p <= hi for p in got)
    n = len(src)
    check("WRITE: while the image's own checksum is checked the bar moves (1% to 5%), and says what it is doing",
          within(wpub, "image", 0.01, 0.05, "against its checksum") and any("of" in d and "against its checksum" in d for st, p, d in wpub))
    check("... while it is written the bar moves from 5% to 60%, with how much has gone to the drive",
          within(wpub, "write", 0.05, 0.60, "written to sdg") and max(p for st, p, d in wpub if st == "write") > 0.5)
    check("... the two read-backs share 60% to 97% equally (60 to 78.5, then 78.5 to 97), each saying which it is",
          within(wpub, "verify", 0.60, 0.785, "read-back 1 of 2") and within(wpub, "verify", 0.785, 0.97, "read-back 2 of 2")
          and max(p for st, p, d in wpub if "read-back 1 of 2" in d) <= 0.785 < max(p for st, p, d in wpub if "read-back 2 of 2" in d))
    check("... the bar never goes backwards, and is not at 100% until it is verified (it was at 100% during the second read-back)",
          [p for st, p, d in wpub] == sorted(p for st, p, d in wpub) and max(p for st, p, d in wpub if st != "verified") < 1.0
          and wpub[-1][0] == "verified" and wpub[-1][1] == 1.0)
    check("a VERIFY ONLY has no write, so its two read-backs share 5% to 97% (5 to 51, then 51 to 97)",
          not any(st == "write" for st, p, d in vpub) and within(vpub, "verify", 0.05, 0.51, "read-back 1 of 2") and within(vpub, "verify", 0.51, 0.97, "read-back 2 of 2")
          and [p for st, p, d in vpub] == sorted(p for st, p, d in vpub) and max(p for st, p, d in vpub if st != "verified") < 1.0)
    blank_target()

    # a damaged image: the bytes in the store no longer hash to the sidecar's SHA-1, and nothing said so until after
    blank_target()
    rel, path = store_copy("DAMAGED_20261105_00000000")
    c = g.check(rel, "sdg", say=quiet)
    with open(path, "r+b") as f:
        f.seek(len(src) - 1)
        last_byte = f.read(1)
        f.seek(len(src) - 1)
        f.write(bytes([last_byte[0] ^ 0xFF]))
    check("an image damaged in the store (same size, one byte wrong): refused before the first byte is written",
          refused(c, "checksum") and untouched())
    # an image whose size changed after the check
    blank_target()
    rel, path = store_copy("SHORT_20261105_00000001")
    c = g.check(rel, "sdg", say=quiet)
    os.truncate(path, len(src) - MiB)
    check("an image truncated after the check: refused before the first byte is written",
          refused(c, "size") and untouched())

    # needs_ack was read at check time: a target that was blank then and is not now
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    blank_target(data=iso_card(8 * MiB))                          # the same drive, written to since the check
    stale = c["needs_ack"] is False and refused(c, "changed since the check")
    check("a target that was blank at the check and holds an installer now: refused (it changed), nothing written",
          stale and open(f"{DEV}/sdg", "rb").read(32774)[32769:32774] == b"CD001")
    # a raw card: no signature anywhere, the first 4 MiB empty and the game after them. Not blank, so --overwrite is needed.
    blank_target(data=bytes(4 * MiB) + os.urandom(4 * MiB))
    c = g.check(image, "sdg", say=quiet)
    check("a raw card with data after an empty first MiB: the write is refused without --overwrite, nothing written",
          c["needs_ack"] and refused(c, "isn't blank") and open(f"{DEV}/sdg", "rb").read(4 * MiB) == bytes(4 * MiB))

    # the arm: whatever the request carries, only a real one counts
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    bad_arms = {"False": False, "empty": {}, "no expiry": {"by": "screen"}, "expiry that isn't a time": {"until": "soon"},
                "a bool for an expiry": {"until": True}, "already expired": {"by": "screen", "until": time.time() - 5}}
    results = {}
    for name, a in bad_arms.items():
        try:
            g.Write(c, armed=a, say=quiet).go()
            results[name] = "WROTE"
        except g.Stop:
            results[name] = "stopped"
    check("an arm that is False, empty, without an expiry, with a nonsense one or already expired: stopped, nothing written",
          set(results.values()) == {"stopped"} and untouched())

    # RESTORE (the owner, 2026-11-16): the image back on the drive it came from, to undo what was done to it since. It lifts the one
    # refusal and nothing else: another drive is still refused, the image has to be a complete read, and the overwrite is always
    # acknowledged, even when the drive looks blank.
    print("RESTORE (in-process):")
    ata_a = "WDC WD800JD-75MSA1"
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=bytes(len(srcA)), ata=(ata_a, "WD-WIDGET0001"))
    r = g.check(imgA, "sdl", say=quiet, restore=True)
    check("RESTORE onto the drive with the image's serial: the check passes, says restore, matched by the serial, and asks for the "
          "acknowledgement although the drive is blank",
          r["target_ok"] and r["restore"] is True and r["restore_proof"] == "serial" and r["needs_ack"] is True and r["contents"]["blank"])
    try:
        g.check(imgA, "sdl", say=quiet)
        plain = ""
    except g.Stop as s:
        plain = f"{s.what} {s.hint}"
    check("... without RESTORE the same drive is still refused, and the hint says RESTORE is the way", "the drive the image came from" in plain
          and "use RESTORE" in plain)

    def restore_refused(img_rel, dev, what):
        try:
            g.check(img_rel, dev, say=quiet, restore=True)
        except g.Stop as s:
            return what in s.what
        return False
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=bytes(len(srcA)), ata=(ata_a, "WD-WIDGET0002"))
    other_serial = restore_refused(imgA, "sdl", "serial is different")
    usb_disk("sdl", len(srcA), "XinTop", "XT-U33502", port=12, devnum="8:176", data=bytes(len(srcA)), ata=("WDC WD800JD-OTHER", "WD-WIDGET0001"))
    other_model = restore_refused(imgA, "sdl", "another make or model")
    blank_target(model="WIDGETDRIVE 1000")                        # the source's make and model, but 8 MiB, not 3
    other_size = restore_refused(image, "sdg", "another size")
    check("RESTORE onto another serial, another model or another size: refused, each saying so (another drive is WRITE's job)",
          other_serial and other_model and other_size)
    F.unplug("sdl")

    def original(data=None):
        """The drive the WW-HDD image came from (no serial: the make, model and size are all that say so)."""
        usb_disk("sdg", len(src), "WIDGET", "WIDGETDRIVE 1000", port=7, devnum="8:96", data=src if data is None else data)
    original()
    rel, path = store_copy("INCOMPLETE_20261116_00000000")
    sp = path[:-4] + ".json"
    sc = json.load(open(sp))
    sc["ddrescue"]["complete"] = False
    json.dump(sc, open(sp, "w"))
    incomplete = restore_refused(rel, "sdg", "isn't a complete read")
    rel, path = store_copy("ONCE_20261116_00000001")
    sp = path[:-4] + ".json"
    sc = json.load(open(sp))
    sc["media"], sc["reads"] = "sd", 1
    json.dump(sc, open(sp, "w"))
    once = restore_refused(rel, "sdg", "read once, not twice")
    check("RESTORE of an image with unread areas, or of a card's image that was read only once: refused, nothing changed",
          incomplete and once and open(f"{DEV}/sdg", "rb").read(len(src)) == src)
    changed = bytearray(src)
    changed[:4096] = os.urandom(4096)                            # a game wrote to it since the read
    original(bytes(changed))
    c = g.check(image, "sdg", say=quiet, restore=True)
    check("RESTORE onto the original, its first bytes changed since the read and no serial: matched by make, model and size, "
          "the acknowledgement asked for, and it does not claim to hold the image",
          c["restore"] is True and "no serial" in c["restore_proof"] and c["needs_ack"] and not c["already_written"])
    check("RESTORE written without the acknowledgement: refused, and the drive is as it was",
          refused(c, "restore overwrites") and open(f"{DEV}/sdg", "rb").read(len(src)) == bytes(changed))
    g.Write(c, ack=True, armed=arm(), say=quiet).go()
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("RESTORE acknowledged and armed: the drive holds the image again, read back twice, and the log line says restore and why it "
          "was matched", open(f"{DEV}/sdg", "rb").read(len(src)) == src and last["state"] == "verified" and last["restore"] is True
          and "no serial" in last["restore_proof"])
    original()
    c = g.check(image, "sdg", say=quiet, restore=True)
    g.Write(c, armed=arm(), say=quiet).go(verify_only=True)
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("RESTORE onto a drive that already holds the image: already_written, VERIFY ONLY needs no acknowledgement, nothing is written",
          c["already_written"] and c["restore"] and last["state"] == "verified" and last["verify_only"] and not last["written"])
    blank_target()

    # a short pwrite must not lose bytes: the loop finishes the piece
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    real_pwrite = os.pwrite
    g.os.pwrite = lambda fd, data, off: real_pwrite(fd, bytes(data)[:max(1, len(data) // 3)], off)
    try:
        info = g.Write(c, armed=arm(), say=quiet).go()
        short_ok = info["verified"] is True
    except g.Stop:
        short_ok = False
    finally:
        g.os.pwrite = real_pwrite
    check("a drive that takes a third of each piece per call: the rest is written, and it verifies",
          short_ok and open(f"{DEV}/sdg", "rb").read(len(src)) == src)

    # the drive swapped in the instant between the identity check and the open
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    real_open = os.open

    def swap_then_open(path, flags, *a, **kw):
        if flags & os.O_EXCL and path.endswith("/sdg"):
            g.os.open = real_open                                  # the fake drive's own set-up opens files too
            blank_target("SWAPPED IN AT THE LAST MOMENT")
            g.os.open = swap_then_open
        return real_open(path, flags, *a, **kw)
    g.os.open = swap_then_open
    try:
        g.Write(c, armed=arm(), say=quiet).go()
        late_swap = False
    except g.Stop as s:
        late_swap = "different drive" in s.what
    finally:
        g.os.open = real_open
    check("the drive swapped between the identity check and the open: stopped after the claim, nothing written",
          late_swap and untouched())

    # a write-protected target: a Stop that says so, not a traceback
    blank_target()
    c = g.check(image, "sdg", say=quiet)

    def protected(path, flags, *a, **kw):
        if flags & os.O_EXCL and path.endswith("/sdg"):
            raise OSError(errno.EROFS, "Read-only file system")
        return real_open(path, flags, *a, **kw)
    g.os.open = protected
    try:
        g.Write(c, armed=arm(), say=quiet).go()
        locked = False
    except g.Stop as s:
        locked = "write-protected" in s.what
    except OSError:
        locked = False
    finally:
        g.os.open = real_open
    check("a write-protected target (EROFS on open): stopped, in words, nothing written", locked and untouched())

    # VERIFY ONLY needs to read, not to write: a locked card can still be checked
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    g.Write(c, armed=arm(), say=quiet).go()
    c = g.check(image, "sdg", say=quiet)
    seen = []

    def watch_flags(path, flags, *a, **kw):
        if path.endswith("/sdg"):
            seen.append(flags)
        return real_open(path, flags, *a, **kw)
    g.os.open = watch_flags
    try:
        g.Write(c, armed=arm(), say=quiet).go(verify_only=True)
    finally:
        g.os.open = real_open
    claims = [f for f in seen if f & os.O_EXCL]
    check("VERIFY ONLY claims the drive read-only", claims and all(f & os.O_ACCMODE == os.O_RDONLY for f in claims))

    # one read or write at a time on the store, whoever started it: a held lock stops the second
    blank_target()
    c = g.check(image, "sdg", say=quiet)
    lock = os.open(f"{STORE}/.gatbox-disk.lock", os.O_CREAT | os.O_RDWR, 0o664)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        g.Write(c, armed=arm(), say=quiet).go()
        busy_store = False
    except g.Stop as s:
        busy_store = "another disk" in s.what
    try:
        g.Read("sdb", "widget-wars", "WW-LOCKED", "ssd", say=quiet).go()
        busy_read = False
    except g.Stop as s:
        busy_read = "another disk" in s.what
    finally:
        os.close(lock)
    check("a read or write while another holds the store's lock: stopped, nothing written",
          busy_store and busy_read and untouched())
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("... and the refused write is in writes.jsonl like any other", last["state"] == "stopped"
          and "another disk" in last["error"])

    # a drive whose bridge reports no model: nothing typed must not count as the model typed
    usb_disk("sdi", 8 * MiB, "WIDGET", "", port=9, devnum="8:128")
    code, out = disk("write", "-i", image, "-d", "sdi", stdin="")
    check("a drive with an empty model and nothing typed (Enter, or EOF): refused, nothing written",
          code == 2 and "model wasn't typed" in out and open(f"{DEV}/sdi", "rb").read(len(src)) == bytes(len(src)))
    code, out = disk("write", "-i", image, "-d", "sdi", stdin="sdi\n")
    check("... and typing the device name instead writes and verifies it", code == 0 and "VERIFIED" in out
          and open(f"{DEV}/sdi", "rb").read(len(src)) == src)

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
    # the same family drive again, through the dashboard's job: the status says which folder already holds the bytes (the screen's wording)
    usb_disk("sdq", len(fam), "WIDGET", "FAMILYDRIVE 500", port=17, devnum="8:256", data=fam)
    fident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sdq")["identity"]
    code, st, gone = job({"op": "read", "device": "sdq", "identity": fident, "machine": "widget-wars", "label": "WW-FAMJOB", "media": "hdd"})
    check("a READ job whose bytes are already stored under another machine: done, not new, and the status names the folder that holds them",
          code == 0 and gone and st["state"] == "done" and st["stored"]["new"] is False and st["stored"]["machine"] == "unassigned"
          and st["stored"]["path"].endswith(f"/unassigned/{fam_name}.img"))
    F.unplug("sdq")
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
    for what, bad in (("false", False), ("empty", {}), ("with no expiry", {"by": "screen"})):
        code, st, gone = job({"op": "write", "check": checked, "ack": False, "machine": "widget-wars", "armed": bad})
        check(f"a WRITE job whose arm is {what}: stopped and not treated as armed, nothing written",
              st["state"] == "stopped" and "arm is malformed" in st["error"] and gone
              and open(f"{DEV}/sdh", "rb").read(len(src)) == bytes(len(src)))
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
    # RESTORE through the dashboard's jobs: the 7"'s RESTORE CHECK, then the armed write that has to carry the acknowledgement
    usb_disk("sdh", len(src), "WIDGET", "WIDGETDRIVE 1000", port=8, devnum="8:112", data=src)
    rident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sdh")["identity"]
    code, st, gone = job({"op": "check", "image": image, "device": "sdh", "identity": rident})
    check("a CHECK job on the drive the image came from: stopped, and the hint says RESTORE", st["state"] == "stopped"
          and "the drive the image came from" in st["error"] and "RESTORE" in st["hint"])
    usb_disk("sdh", len(src), "WIDGET", "WIDGETDRIVE 1000", port=8, devnum="8:112", data=bytes(len(src)))
    code, st, gone = job({"op": "check", "image": image, "device": "sdh", "identity": rident, "restore": True})
    check("a RESTORE CHECK job: done, restore, needs the acknowledgement although the drive is blank", st["state"] == "done"
          and st["restore"] is True and st["needs_ack"] is True and st["contents"]["blank"])
    rchecked = st
    code, st, gone = job({"op": "write", "check": rchecked, "ack": False, "machine": "widget-wars",
                          "armed": {"by": "screen", "at": time.time(), "until": time.time() + 30}})
    check("a RESTORE write job without the acknowledgement: stopped, nothing written", st["state"] == "stopped"
          and "restore overwrites" in st["error"].lower() and open(f"{DEV}/sdh", "rb").read(len(src)) == bytes(len(src)))
    code, st, gone = job({"op": "write", "check": rchecked, "ack": True, "machine": "widget-wars",
                          "armed": {"by": "screen", "at": time.time(), "until": time.time() + 30}})
    last = [json.loads(l) for l in open(f"{STORE}/writes.jsonl")][-1]
    check("... acknowledged and armed: the image is back on the drive, verified, logged as a restore with the arm",
          st["state"] == "done" and st["verified"] and st["restore"] is True and open(f"{DEV}/sdh", "rb").read(len(src)) == src
          and last["restore"] is True and last["armed"]["by"] == "screen" and last["ack"] is True)
    F.unplug("sdh")
    # SIGTERM is how systemd stops the job (its time limit, `systemctl stop`). The job used to die on the spot: no status, no log line, and
    # a running.json that the DISK tab, a new request and every thumbnail waited on for good (the Pi, 2026-11-16: a 2.5 hour write against a
    # 3 hour limit). It now says so, exits normally and removes its claim.
    import signal as _sig
    term_ident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sdb")["identity"]
    with open(f"{spool}/request.json", "w") as f:
        json.dump({"op": "read", "device": "sdb", "identity": term_ident, "machine": "widget-wars", "label": "WW-TERM", "media": "sd",
                   "id": "jobterm", "at": time.time()}, f)
    pr = subprocess.Popen(["python3", TOOL, "--job", f"{spool}/request.json", "--status", f"{spool}/status.json"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env(FAKE_DDRESCUE_SLOW="20"))
    t_end = time.time() + 30
    while time.time() < t_end and not os.path.exists(f"{spool}/running.json"):
        time.sleep(0.05)
    time.sleep(1.5)                                                    # into the read
    claimed = os.path.exists(f"{spool}/running.json")
    pr.send_signal(_sig.SIGTERM)
    try:
        pr.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        pr.kill()
        pr.communicate()
    tst = json.load(open(f"{spool}/status.json"))
    check("SIGTERM in the middle of a job: it exits normally, says it was stopped from outside, and removes its claim (no running.json left)",
          claimed and pr.returncode == 0 and not os.path.exists(f"{spool}/running.json") and tst["state"] == "stopped"
          and "stopped from outside" in tst["error"] and tst["job"] == "jobterm" and "partly written" in tst["hint"]
          and not any(f.startswith("WW-TERM_") for f in files("widget-wars")))
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
    code, st, gone = job({"op": "shred"})
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
        # A status that says "running" with no claim behind it is a job that died without finishing (a power cut, a reboot): shown as running
        # it would be a progress card for ever. It reads as stopped and stale, with the warning about a partly written drive.
        sp0 = f"{w}/spool/status.json"
        before = open(sp0).read() if os.path.exists(sp0) else None
        with open(sp0, "w") as f:
            json.dump({"op": "write", "job": "dead1", "state": "running", "step": "verify", "progress": 0.62}, f)
        code, r = call("GET", "/api/disk")
        check("a status that says running with no claim behind it (a power cut, a reboot): GET /api/disk shows it stopped and stale, with the "
              "partly-written warning, and not busy", code == 200 and not r["busy"] and r["status"]["state"] == "stopped" and r["status"]["stale"] is True
              and "restarted" in r["status"]["error"] and "partly written" in r["status"]["hint"] and r["status"]["progress"] == 0.62)
        with open(f"{w}/spool/running.json", "w") as f:
            json.dump({"op": "write", "id": "dead1"}, f)                  # with its claim there, the same status is a job that is running
        code, r = call("GET", "/api/disk")
        check("... and with the claim there it is a running job: busy, still running, not stale", code == 200 and r["busy"]
              and r["status"]["state"] == "running" and not r["status"].get("stale"))
        os.remove(f"{w}/spool/running.json")
        if before is None:
            os.remove(sp0)
        else:
            with open(sp0, "w") as f:
                f.write(before)
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
        code, r = call("POST", "/api/disk/check", dict(target, identity=sdh["identity"], restore=True))
        st = done()
        sent = json.load(open(f"{w}/spool/running.json")) if os.path.exists(f"{w}/spool/running.json") else {}
        check("CHECK with restore -> 202; the job gets the flag and refuses a card that is not the image's own drive, saying so",
              code == 202 and st.get("op") == "check" and st.get("state") == "stopped" and "another make or model" in st.get("error", "")
              and not sent)
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
              and r["image"] == image and r["device"] == "sdh" and r["target"] == "WIDGET WIDGET SSD 64G C"
              and r["machine"] == "widget-wars")
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
        print("the job record (the bench procedure's Appendix B):")
        code, rec = call("GET", f"/api/disk/record?image={image}")
        sec = {s["title"]: {r["label"]: r for r in s["rows"]} for s in rec["sections"]} if code == 200 else {}
        side = json.load(open(f"{STORE}/{image[:-4]}.json"))
        rd = sec.get("THE READ", {})
        check("GET /api/disk/record: the read, from the sidecar: machine, source drive, fingerprint, image bytes and hashes",
              code == 200 and rec["image"] == image and rd["Date and machine"]["value"] == "widget-wars"
              and rd["Date and machine"]["at"] == side["started"] and "WIDGETDRIVE 1000" in rd["Source media"]["value"]
              and rd["Image bytes"]["value"] == f"{side['bytes']:,}" and rd["Image SHA-1"]["value"] == side["sha1"]
              and rd["Image SHA-256"]["value"] == side["sha256"] and rd["Image file"]["value"] == image
              and rd["Fingerprint, first 64 MiB (non-zero bytes)"]["value"] == f"{side['fingerprint']['nonzero']:,}")
        check("... the ddrescue result and how many times it was read", "100.0% rescued" in rd["ddrescue"]["value"]
              and "0 bad areas" in rd["ddrescue"]["value"] and rd["ddrescue"]["ok"] is True and "read once" in rd["Passes"]["value"])
        check("... a line the deck cannot know stays blank, to write in: Notes", rd["Notes"]["value"] is None)
        wl = next(s["rows"] for s in rec["sections"] if s["title"] == "WRITTEN TO A DRIVE")
        wr = {r["label"]: r for r in wl[:4]}                     # the newest write first: this test's verified one
        check("... the writes, from writes.jsonl, newest first: verified, the target drive, what was on it, the bytes beyond "
              "the image", wr["Write"]["value"].startswith("verified") and wr["Write"]["ok"] is True
              and "WIDGET SSD 64G C" in wr["Target media"]["value"] and "overwrite acknowledged" in wr["On the target before"]["value"]
              and "bytes" in wr["Beyond the image, left as it was"]["value"])
        stopped = [r for r in wl if r["label"] == "Write" and r["ok"] is False]
        check("... the earlier writes of the same bytes are listed too, and a stopped one says why and that nothing was written",
              rec["writes"] >= 2 and stopped and "stopped:" in stopped[0]["value"] and "nothing was written" in stopped[0]["value"])
        cab = sec.get("IN THE CAB", {})
        ticked = [k for k, r in cab.items() if r["value"] == "done"]
        check("... the cab lines, from this image's checklist: the five-volt tick is there with its time, the rest are blank",
              len(cab) == 10 and len(ticked) == 1 and "5 V at the board" in ticked[0] and cab[ticked[0]]["at"] > 0
              and rec["checklist"] == write_id)
        codes = [call("GET", f"/api/disk/record?image={p}")[0] for p in ("../../etc/passwd", "/etc/passwd", "nope.img",
                                                                          "widget-wars/nope.img", "", "widget-wars/x.json")]
        check("an image that isn't in the store, a path out of it, or no name -> 404", all(c == 404 for c in codes))
        # a sidecar and an image just outside the store, and a write line for other bytes: neither may show up
        open(f"{T}/outside.img", "wb").write(b"x")
        json.dump({"label": "OUTSIDE", "sha1": "f" * 40}, open(f"{T}/outside.json", "w"))
        code_out = call("GET", "/api/disk/record?image=../outside.img")[0]
        with open(f"{STORE}/writes.jsonl", "a") as f:
            f.write(json.dumps({"state": "stopped", "sha1": "0" * 40, "epoch": time.time() + 60, "target": None,
                                "error": "OTHER-BYTES-MARKER"}) + "\n")
            f.write("this line is not JSON\n")
        code_w, rec2 = call("GET", f"/api/disk/record?image={image}")
        check("a file outside the store is never read (404), a write of other bytes isn't on this image's record, and a bad "
              "line in writes.jsonl is skipped", code_out == 404 and code_w == 200 and "OTHER-BYTES-MARKER" not in json.dumps(rec2))
        # FORMAT through gatbox-web (docs/plans/2026-11-16-restore-and-format.md): the same shape as a write. The tool's own tests are
        # tests/test-disk-format.py; these are the routes and their gates. Skipped, saying so, where mkfs.fat is not installed.
        import shutil as _shutil
        if not _shutil.which("mkfs.fat", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin"):
            print("  skip  FORMAT through gatbox-web: mkfs.fat is not installed here (apt: dosfstools)")
        else:
            usb_disk("sdf", 20 * MiB, "WIDGET", "WIDGET CF 20", port=21, devnum="8:80", data=bytes(range(1, 256)) * 4096)
            fd_ = disk_of("sdf")
            target_f = {"device": "sdf", "identity": fd_["identity"]}
            check("POST /api/disk/format-check: a filesystem that isn't one of the three, or a label with a space -> 400; a drive that isn't the one picked -> 409",
                  call("POST", "/api/disk/format-check", dict(target_f, fs="ntfs"))[0] == 400
                  and call("POST", "/api/disk/format-check", dict(target_f, fs="fat32", label="two words"))[0] == 400
                  and call("POST", "/api/disk/format-check", {"device": "sdf", "identity": dict(fd_["identity"], model="OTHER"), "fs": "fat32"})[0] == 409)
            check("POST /api/disk/format with no FORMAT CHECK behind it -> 409", call("POST", "/api/disk/format", {"device": "sdf", "ack": True})[0] == 409)
            code, r = call("POST", "/api/disk/format-check", dict(target_f, fs="fat32", label="widget"))
            fc = done()
            check("POST /api/disk/format-check -> 202, and the job's answer: it passes, the label in capitals, FAT16 for a card this small, the "
                  "acknowledgement wanted and the no-image warning", code == 202 and r["op"] == "format_check" and r["label"] == "WIDGET"
                  and fc.get("op") == "format_check" and fc.get("target_ok") and fc["layout"]["made"] == "FAT16" and fc["needs_ack"] and fc["warn_no_image"])
            check("POST /api/disk/format without the acknowledgement on a drive that isn't blank -> 409; for another drive than the one checked -> 409",
                  call("POST", "/api/disk/format", {"device": "sdf"})[0] == 409 and call("POST", "/api/disk/format", {"device": "sda", "ack": True})[0] == 409)
            lan_f = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
            if lan_f:
                check("POST /api/disk/format from a phone -> 403: a format is armed at the Pi", call("POST", "/api/disk/format", {"device": "sdf", "ack": True}, base=B.replace("127.0.0.1", lan_f))[0] == 403)
            code, r = call("POST", "/api/disk/format", {"device": "sdf", "ack": True, "fs": "ext4", "label": "EVIL"})
            ff = done()
            check("POST /api/disk/format from the Pi's own screen -> 202, armed for 30 s; the job formats what the CHECK said (FAT16, WIDGET), "
                  "whatever the request says, and ends verified", code == 202 and r["armed"]["by"] == "screen" and 29 <= r["armed"]["until"] - r["armed"]["at"] <= 31
                  and ff.get("op") == "format" and ff.get("state") == "done" and ff.get("verified") and ff.get("made") == "FAT16" and ff.get("label") == "WIDGET")
            code, r = call("GET", "/api/disk/formats")
            check("GET /api/disk/formats: that format, newest first, with the drive, the filesystem, the label and verified",
                  code == 200 and r["formats"][0]["state"] == "verified" and r["formats"][0]["fs"] == "FAT16" and r["formats"][0]["label"] == "WIDGET"
                  and r["formats"][0]["device"] == "sdf" and "WIDGET CF 20" in r["formats"][0]["target"])
            check("a second POST /api/disk/format on the strength of the same check -> 409 (the last result is the format, not a check)",
                  call("POST", "/api/disk/format", {"device": "sdf", "ack": True})[0] == 409)
            code_r, rec_f = call("GET", f"/api/disk/record?image={image}")
            check("a format's line in writes.jsonl is not on any image's record", code_r == 200 and "FAT16" not in json.dumps(rec_f))
            F.unplug("sdf")
        mount_table(store=False)
        code, _ = call("GET", f"/api/disk/record?image={image}")
        mount_table()
        check("with the store unplugged -> 409, IMAGE STORE NOT CONNECTED", code == 409)
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
