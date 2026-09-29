"""GET /api/devices: the USB devices GATBOX works with, from sysfs (no device is opened; the logger owns the port).

USB IDs read off this Pi with lsusb (2026-09-28), not guessed:
    DMM adapter   067b:xxxx  any Prolific (VID 067b), the same match as udev's /dev/gatbox-dmm rule
                             (the CableCreation PL-2303 reads 067b:23a3)
    T48           a466:0a53  XGecu T48 (lsusb calls it TL866II Plus; minipro -L sees a T48)
    touch         0eef:0005  Waveshare 7" (C) capacitive panel, D-WAV Scientific
    scanner       not read yet: the Eyoyo EY-H2 gets its ID when it's first plugged in (M6)
"""
import glob
import os

from .live import LIVE

USB = "/sys/bus/usb/devices"
DMM_VID = "067b"
T48 = ("a466", "0a53")
TOUCH = ("0eef", "0005")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def usb():
    """[{id, vid, pid, product, manufacturer, path}] for every USB device (not the root hubs' interfaces)."""
    out = []
    for d in sorted(glob.glob(os.path.join(USB, "*"))):
        vid, pid = _read(os.path.join(d, "idVendor")), _read(os.path.join(d, "idProduct"))
        if vid and pid:
            out.append({"id": f"{vid}:{pid}", "vid": vid, "pid": pid, "product": _read(os.path.join(d, "product")),
                        "manufacturer": _read(os.path.join(d, "manufacturer")), "path": os.path.basename(d)})
    return out


def _tty(dev):
    """The ttyUSB* a USB serial adapter was given (from its interface directories)."""
    for t in glob.glob(os.path.join(USB, dev["path"] + ":*", "ttyUSB*")):
        return os.path.basename(t)
    return None


def snapshot():
    devs = usb()
    adapter = next((d for d in devs if d["vid"] == DMM_VID), None)
    t48 = next((d for d in devs if (d["vid"], d["pid"]) == T48), None)
    touch = next((d for d in devs if (d["vid"], d["pid"]) == TOUCH), None)
    last, age = LIVE.last, LIVE.age()
    flowing = LIVE.logging()
    return {
        "dmm": {"adapter": {"present": bool(adapter), "usb_id": adapter and adapter["id"],
                            "product": adapter and adapter["product"], "tty": adapter and _tty(adapter)},
                "readings": {"flowing": flowing, "age_s": age, "mode": last["mode"] if (last and flowing) else None,
                             "file": LIVE.name if flowing else None},
                "chain": "UT61E -> UT-D02 -> PL-2303 -> /dev/gatbox-dmm (19200 7O1, sigrok uni-t-ut61e-ser)"},
        "t48": {"present": bool(t48), "usb_id": t48 and t48["id"], "product": t48 and t48["product"]},
        "scanner": {"present": None, "grabbed": None, "note": "Eyoyo EY-H2: USB ID not read yet (M6)"},
        "touch": {"present": bool(touch), "usb_id": touch and touch["id"], "product": touch and touch["product"]},
        "usb": devs,
    }
