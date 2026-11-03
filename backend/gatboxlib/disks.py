"""The disks gatbox-disk works with, read from sysfs and the mount table. No device is ever opened here, so
gatbox-web (whose sandbox has no /dev) lists them with this too (docs/plans/2026-11-02-disk-imaging.md §5).

A drive is what it says about itself: its INQUIRY vendor and model, its size and sector size (and its ATA serial,
once Task 0 shows what the dock passes through). The by-id name and the USB ID are the dock's or the reader's, the
same for every drive in it (§1, row 1). Only whole disks on USB are listed: an SD card, an NVMe drive or a SATA disk
on PCIe is never offered, and neither is anything that isn't a whole disk.

Environment (tests): GATBOX_SYSFS, GATBOX_DEV, GATBOX_MOUNTINFO (a fake tree, plain files, a fake mount table) and
GATBOX_DISKS (the image store, default /srv/gatbox/disks).
"""
import glob
import json
import os
import re

SYSFS = os.environ.get("GATBOX_SYSFS", "/sys")
DEV = os.environ.get("GATBOX_DEV", "/dev")
MOUNTINFO = os.environ.get("GATBOX_MOUNTINFO", "/proc/self/mountinfo")
DISKS = os.environ.get("GATBOX_DISKS", "/srv/gatbox/disks")
MARKER = ".gatbox-image-store"                                  # on the image-store drive itself (§5)
WHOLE_DISK = re.compile(r"^sd[a-z]+$")                          # only SCSI-disk names: never mmcblk*, nvme*, loop*
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware")


def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def mounts():
    """[(major:minor, mount point)] from the mount table. Matching by device number, not by a /dev name, so a
    by-uuid or by-label mount is caught the same way."""
    out = []
    for line in read_file(MOUNTINFO).splitlines():
        f = line.split()
        if len(f) > 4:
            out.append((f[2], f[4].replace("\\040", " ")))
    return out


def store_mounted():
    """The image store is a mount point, and the drive mounted there carries the marker. Both, or nothing is written
    under it: an unplugged drive must never fall through to the SD card (note §5)."""
    real = os.path.realpath(DISKS)
    return any(os.path.realpath(mp) == real for _, mp in mounts()) and os.path.isfile(os.path.join(real, MARKER))


def _usb_parent(real):
    """The USB device (the dock's or reader's bridge) above a disk in sysfs: (vid:pid, its driver), or None."""
    d = real
    while d and d != "/" and len(d) > len(SYSFS):
        if os.path.isfile(os.path.join(d, "idVendor")) and os.path.isfile(os.path.join(d, "idProduct")):
            usb = f"{read_file(os.path.join(d, 'idVendor'))}:{read_file(os.path.join(d, 'idProduct'))}"
            drivers = {os.path.basename(os.path.realpath(p)) for p in glob.glob(os.path.join(d, "*:*", "driver"))}
            return usb, ",".join(sorted(drivers)) or None
        d = os.path.dirname(d)
    return None


def disks():
    """Every whole USB disk, with its identity and its role: ok (a source or target), empty (a slot with nothing in
    it), store (the image store), system (the Pi's own), mounted. Anything that isn't a whole disk on USB isn't here."""
    by_id = {}
    for link in glob.glob(os.path.join(DEV, "disk", "by-id", "*")):
        by_id.setdefault(os.path.basename(os.path.realpath(link)), []).append(os.path.basename(link))
    mtab = mounts()
    store = os.path.realpath(DISKS)
    out = []
    for path in sorted(glob.glob(os.path.join(SYSFS, "block", "*"))):
        name = os.path.basename(path)
        if not WHOLE_DISK.match(name):
            continue
        real = os.path.realpath(path)
        parent = _usb_parent(real)
        if not re.search(r"/usb\d+/", real) or not parent:
            continue
        parts = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(real, name + "*", "partition")))
        devnums = {read_file(os.path.join(real, "dev"))} | {read_file(os.path.join(real, p, "dev")) for p in parts}
        where = [mp for num, mp in mtab if num in devnums]
        ident = {"vendor": read_file(os.path.join(real, "device", "vendor")),
                 "model": read_file(os.path.join(real, "device", "model")),
                 "serial": None, "chs": None,          # the drive's own, once Task 0 shows what the dock passes through
                 "bytes": int(read_file(os.path.join(real, "size"), "0") or 0) * 512,
                 "logical": int(read_file(os.path.join(real, "queue", "logical_block_size"), "512") or 512),
                 "usb": parent[0], "driver": parent[1], "by_id": sorted(by_id.get(name, []))}
        if ident["bytes"] == 0:
            role = "empty"
        elif any(os.path.realpath(mp) == store for mp in where):
            role = "store"
        elif any(mp in SYSTEM_MOUNTS for mp in where):
            role = "system"
        elif where:
            role = "mounted"
        else:
            role = "ok"
        out.append({"name": name, "node": os.path.join(DEV, name), "role": role, "mounted": where,
                    "partitions": parts, "rotational": read_file(os.path.join(real, "queue", "rotational")) == "1",
                    "identity": ident})
    return out


REFUSED = {"empty": ("{n} is an empty slot (0 bytes)", "put the card or drive in, wait a few seconds, and list again"),
           "store": ("{n} is the image store", "the store is never a source or a target"),
           "system": ("{n} holds the Pi's own system", "it's never a source or a target"),
           "mounted": ("{n} has something mounted ({m})", "the desktop may have mounted it: eject it there first")}


def same_drive(a, b):
    """The same physical drive, by what the drive says about itself. The by-id name and the USB ID are the dock's."""
    keys = ("vendor", "model", "bytes", "logical", "serial")
    return all(a.get(k) == b.get(k) for k in keys)


def describe(ident):
    serial = f" · serial {ident['serial']}" if ident.get("serial") else ""
    return (f"{ident.get('vendor', '')} {ident.get('model', '')}".strip()
            + f" · {ident.get('bytes', 0):,} bytes · {ident.get('logical')}-byte sectors{serial}")


def images():
    store = os.path.realpath(DISKS)
    out = []
    for side in sorted(glob.glob(os.path.join(store, "*", "*.json"))):
        if side.endswith(".read.json") or not os.path.isfile(side[:-5] + ".img"):
            continue
        try:
            with open(side) as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"image": os.path.relpath(side[:-5] + ".img", store), "machine": j.get("machine"),
                    "bytes": j.get("bytes"), "sha1": j.get("sha1"), "media": j.get("media"),
                    "archive": (j.get("archive") or {}).get("state")})
    return out
