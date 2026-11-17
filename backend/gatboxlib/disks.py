"""The disks gatbox-disk works with, read from sysfs and the mount table. No device is ever opened here, so
gatbox-web (whose sandbox has no /dev) lists them with this too (docs/plans/2026-11-02-disk-imaging.md §5).

A drive is what it says about itself. Behind the XinTop dock (Task 0, 2026-11-06) the SCSI INQUIRY names the dock, not
the drive, so every drive in it would read "XinTop XT-U33502"; the drive's own ATA model and serial come through the
bridge and udev keeps them (ID_BUS=ata, ID_MODEL_ENC, ID_SERIAL_SHORT), readable by anyone from /run/udev/data. They are
used when they are there and the INQUIRY when they are not (a CF card, an old IDE drive the bridge cannot identify).
A serial is only trusted when it is the drive's own: on a USB bus it is the reader's or the dock's, the same for every
card or drive in it. The by-id name and the USB ID are the dock's or the reader's too (§1, row 1). Only whole disks on
USB are listed: an SD card, an NVMe drive or a SATA disk on PCIe is never offered, and neither is anything that isn't a
whole disk.

Environment (tests): GATBOX_SYSFS, GATBOX_DEV, GATBOX_MOUNTINFO (a fake tree, plain files, a fake mount table),
GATBOX_UDEV (a fake /run/udev/data) and GATBOX_DISKS (the image store, default /srv/gatbox/disks).
"""
import glob
import json
import os
import re
import time

SYSFS = os.environ.get("GATBOX_SYSFS", "/sys")
DEV = os.environ.get("GATBOX_DEV", "/dev")
MOUNTINFO = os.environ.get("GATBOX_MOUNTINFO", "/proc/self/mountinfo")
UDEV = os.environ.get("GATBOX_UDEV", "/run/udev/data")          # udev's database: one b<major>:<minor> file per block device
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


def udev_props(devnum):
    """What udev keeps for a block device: the E: lines of /run/udev/data/b<major>:<minor>. {} when there are none (a
    fake tree, a udev that has not seen the device). Plain file reads: no root, no fork, no device opened."""
    props = {}
    for line in read_file(os.path.join(UDEV, f"b{devnum}")).splitlines():
        if line.startswith("E:") and "=" in line:
            key, _, value = line[2:].partition("=")
            props[key] = value
    return props


def own_identity(props):
    """(model, serial) the drive reports about itself through the bridge, or None. Only for ID_BUS=ata: that is udev's
    ata_id talking to the drive (SAT pass-through). On a USB bus the model and serial are the bridge's own."""
    if props.get("ID_BUS") != "ata":
        return None
    enc = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), props.get("ID_MODEL_ENC", ""))
    model = " ".join(enc.split()) or props.get("ID_MODEL", "").replace("_", " ").strip()
    return (model, props.get("ID_SERIAL_SHORT") or None) if model else None


# What udev's ata_id kept from the drive's own IDENTIFY (it asked once, when the drive appeared), as DRIVE INFO shows it:
# udev key -> label. A key that isn't there is not shown: nothing is guessed, and nothing more is sent to the drive. The
# dock hangs on SMART and native-max commands (the Pi, 2026-11-14), so those are never asked for.
FEATURES = (("ID_ATA_FEATURE_SET_HPA", "Host Protected Area"), ("ID_ATA_FEATURE_SET_SMART", "SMART"),
            ("ID_ATA_FEATURE_SET_PM", "Power management"), ("ID_ATA_FEATURE_SET_APM", "Advanced power management"),
            ("ID_ATA_FEATURE_SET_AAM", "Acoustic management"), ("ID_ATA_FEATURE_SET_SECURITY", "ATA security"))


def _num(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def drive_info(props, real, ident):
    """DRIVE INFO for one disk: what its IDENTIFY said through udev (only when ident['by'] is 'ata') and what the kernel
    knows (sectors, physical sector size, whether it spins). Plain reads of /run/udev/data and sysfs: no device is opened
    and no command is sent to the drive."""
    own = ident.get("by") == "ata"
    info = {"identify": own, "sectors": ident["bytes"] // 512 if ident["bytes"] else 0, "logical": ident["logical"],
            "physical": _num(read_file(os.path.join(real, "queue", "physical_block_size"))),
            "rotational": read_file(os.path.join(real, "queue", "rotational")) == "1",
            "firmware": None, "rpm": None, "sata_gen": None, "write_cache": None, "features": []}
    if own:
        info["firmware"] = props.get("ID_REVISION") or None
        info["rpm"] = _num(props.get("ID_ATA_ROTATION_RATE_RPM"))          # 0 = not a spinning disk (a CF card, an SSD)
        gens = [g for g in (3, 2, 1) if props.get(f"ID_ATA_SATA_SIGNAL_RATE_GEN{g}") == "1"]
        info["sata_gen"] = gens[0] if gens else None
        if "ID_ATA_WRITE_CACHE" in props:
            info["write_cache"] = props.get("ID_ATA_WRITE_CACHE_ENABLED") == "1"
        for key, label in FEATURES:
            if props.get(key) == "1":
                info["features"].append({"name": label, "enabled": props.get(key + "_ENABLED") == "1"
                                         if key + "_ENABLED" in props else None})
    return info


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
                 "serial": None, "chs": None,          # CHS is in IDENTIFY but udev does not keep it; hdparm -I would show it
                 "bytes": int(read_file(os.path.join(real, "size"), "0") or 0) * 512,
                 "logical": int(read_file(os.path.join(real, "queue", "logical_block_size"), "512") or 512),
                 "usb": parent[0], "driver": parent[1], "by_id": sorted(by_id.get(name, [])), "by": "inquiry"}
        props = udev_props(read_file(os.path.join(real, "dev")))
        own = own_identity(props)
        if own:
            # The INQUIRY is the dock's. Keep its name as the bridge, and name the drive by what it says itself (its model
            # carries the make, so there is no separate vendor).
            ident.update(bridge=f"{ident['vendor']} {ident['model']}".strip(), vendor="", model=own[0], serial=own[1], by="ata")
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
                    "identity": ident, "info": drive_info(props, real, ident)})
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


def sidecar(rel):
    """One image's whole sidecar, by the store-relative name images() lists, or None. A name that is not one of those (a
    path out of the store, an image with no sidecar) is never opened."""
    store = os.path.realpath(DISKS)
    if not isinstance(rel, str) or not rel.endswith(".img") or os.path.isabs(rel):
        return None
    side = os.path.realpath(os.path.join(store, rel[:-4] + ".json"))
    if not side.startswith(store + os.sep) or not os.path.isfile(os.path.realpath(os.path.join(store, rel))):
        return None
    try:
        with open(side) as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    return j if isinstance(j, dict) else None


def writes():
    """Every line of the store's writes.jsonl, oldest first (a line that is not JSON is skipped). One line per write the
    job finished or stopped: gatbox-disk is the only thing that writes it."""
    out = []
    for line in read_file(os.path.join(os.path.realpath(DISKS), "writes.jsonl")).splitlines():
        try:
            w = json.loads(line)
        except ValueError:
            continue
        if isinstance(w, dict):
            out.append(w)
    return out


def images():
    """The store's images, from their sidecars (never their bytes): the newest first. Only a .img with a sidecar,
    inside the store, counts; a read still in progress (<label>.read.json) doesn't."""
    store = os.path.realpath(DISKS)
    out = []
    for side in glob.glob(os.path.join(store, "*", "*.json")):
        img = side[:-5] + ".img"
        if side.endswith(".read.json") or not os.path.isfile(img) or not os.path.realpath(img).startswith(store + os.sep):
            continue
        try:
            with open(side) as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        src = j.get("source") or {}
        out.append({"image": os.path.relpath(img, store), "machine": j.get("machine"), "label": j.get("label"),
                    "bytes": j.get("bytes"), "sha1": j.get("sha1"), "sha256": j.get("sha256"), "media": j.get("media"), "realm": j.get("realm"),
                    "started": j.get("started"), "source": f"{src.get('vendor', '')} {src.get('model', '')}".strip(),
                    "reads": j.get("reads"), "also_read": len(j.get("also_read") or []),
                    "fingerprint": (j.get("fingerprint") or {}).get("nonzero"),
                    "mame": (j.get("mame") or {}).get("state"), "archive": (j.get("archive") or {}).get("state"),
                    "imported": src.get("imported")})
    return sorted(out, key=lambda i: i.get("started") or 0, reverse=True)


# --- IMPORT's _incoming folder (tools/gatbox-disk import_file) -------------------------------------------------------------
INCOMING = "_incoming"
SETTLE = float(os.environ.get("GATBOX_IMPORT_SETTLE", "60"))   # a file still growing (a copy under way) is not imported


def incoming_dir():
    return os.path.join(os.path.realpath(DISKS), INCOMING)


def incoming():
    """The files waiting in _incoming: [{name, bytes, age, settled}]. Hidden files and .part files are copies under way."""
    d, out, now = incoming_dir(), [], time.time()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for n in names:
        p = os.path.join(d, n)
        if n.startswith(".") or n.endswith(".part") or not os.path.isfile(p) or os.path.islink(p):
            continue
        st = os.stat(p)
        out.append({"name": n, "bytes": st.st_size, "age": round(now - st.st_mtime, 1), "settled": now - st.st_mtime >= SETTLE})
    return out
