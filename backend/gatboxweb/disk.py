"""The DISK flow (docs/plans/2026-11-02-disk-imaging.md). gatbox-web never opens a block device and never serves image
bytes: it lists disks from sysfs (gatboxlib.disks), reads the store's sidecars, and queues jobs.

    the 7" → POST /api/disk/… → gatbox-web validates → <spool>/request.json
    gatbox-disk.path sees it → gatbox-disk.service (user gatbox-disk: whole USB disks and the store, nothing else) runs
        `gatbox-disk --job request.json`: claims it (→ running.json), reads, checks or writes, writes status.json
    gatbox-web watches status.json → SSE "disk" events → the DISK tab's progress and result cards

One disk job at a time; a T48 job doesn't block it. A WRITE is accepted from the Pi's own screen only (the hold there
is the arm, as for a burn), after a passing TARGET CHECK of the same image and drive in the last 5 minutes, and is
armed for 30 seconds. The job checks all of that again before it writes. The spool is group gatbox-disk-queue, which
gatbox-web joins; the devices' group is the job's alone (§9.3). The swap checklist's ticks are kept here, in
gatbox-web's state, keyed by the write's job id: the store is the job's to write.
"""
import json
import os
import re
import shutil
import threading
import time
import uuid

from gatboxlib import disks as dl

from . import config, hubs, roster
from .live import LIVE
from .meter import Bad, resolved

ARM_S, CHECK_FRESH_S = 30, 300
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")      # gatbox-disk's rule: the label becomes a file name
MEDIA = ("hdd", "cf", "sd", "ssd")
# The swap checklist (note §4.4). It blocks nothing; each tick keeps its time.
CHECKLIST = [("before", "version", "power-up screen: version noted (phone photo)"),
             ("before", "settings", "settings and audits photographed"),
             ("before", "five_volts", "5 V at the board under load, 5.0–5.2 V (the last reading here, or SAVE READING)"),
             ("before", "jumper", "jumper, pin-1 stripe and power plug photographed"),
             ("after", "version_match", "version matches"),
             ("after", "settings_match", "settings and audits match"),
             ("after", "games", "3+ full games played (guns if fitted)"),
             ("after", "cold_boots", "5 cold boots, about 30 s apart"),
             ("after", "attract", "30–60 min of attract mode"),
             ("after", "original", "the original labelled and left with the machine's owner")]
_lock = threading.Lock()


def spool(name):
    return os.path.join(config.DISK_SPOOL, name)


def _read(name):
    try:
        with open(spool(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def store():
    """The image store: mounted with its marker, and its space (statvfs only, nothing read)."""
    s = {"path": dl.DISKS, "mounted": dl.store_mounted(), "free": None, "total": None}
    if s["mounted"]:
        try:
            u = shutil.disk_usage(dl.DISKS)
            s.update(free=u.free, total=u.total)
        except OSError:
            pass
    return s


def status():
    """GET /api/disk: the disks, the store, whether a job is queued or running, and the latest progress or result."""
    queued, running = os.path.exists(spool("request.json")), os.path.exists(spool("running.json"))
    req = (_read("request.json") if queued else _read("running.json") if running else None) or {}
    request = {k: req.get(k) for k in ("op", "device", "label", "image", "machine") if k in req} or None
    return {"disks": dl.disks(), "store": store(), "busy": queued or running, "queued": queued, "running": running,
            "status": _read("status.json"), "request": request, "spool": os.path.isdir(config.DISK_SPOOL)}


def images():
    """GET /api/disk/images: the store's images from their sidecars, newest first. Never their bytes."""
    return dl.images() if dl.store_mounted() else []


def _machine(body):
    machine = body.get("machine") or resolved()["machine"] or "unassigned"
    if machine != "unassigned" and machine not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {machine!r}")
    return machine


def _realm(machine):
    """The machine's world: the hub whose roster has it (Phase 8). None for a machine no hub has, or two do."""
    hub, _ = hubs.owner(machine)
    return hub.get("name") if hub else None


def _disk(name, identity):
    """The disk the 7" picked, if it may be a source or target and is still the drive it showed."""
    d = next((x for x in dl.disks() if x["name"] == name), None) if isinstance(name, str) else None
    if d is None:
        raise Bad(404, f"no USB disk called {name!r}")
    if d["role"] != "ok":
        what, _ = dl.REFUSED[d["role"]]
        raise Bad(409, what.format(n=name, m=", ".join(d["mounted"])))
    if not isinstance(identity, dict) or not dl.same_drive(d["identity"], identity):
        raise Bad(409, f"{name} isn't the drive that was picked: pick it again")
    return d


def _store():
    if not dl.store_mounted():
        raise Bad(409, "IMAGE STORE NOT CONNECTED")


def _image(rel):
    """An image from the store's list (by its store-relative name), as the absolute path the job gets."""
    found = next((i for i in images() if i["image"] == rel), None) if isinstance(rel, str) else None
    if found is None:
        raise Bad(400, f"not an image in the store: {rel!r}")
    return os.path.join(os.path.realpath(dl.DISKS), rel), found


def _queue(fields, check=None):
    """request.json for gatbox-disk.service (the path unit fires on that name only): one disk job at a time.
    check(): a last look at the state, under the lock, just before the request is written. What it returns goes into
    the request, so the job never sees a half-written one."""
    if not os.path.isdir(config.DISK_SPOOL):
        raise Bad(503, "the disk service isn't installed (no spool)")
    with _lock:
        if os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")):
            raise Bad(409, "a disk job is already queued or running")
        if check:
            fields = dict(fields, **(check() or {}))
        req = dict(fields, id=uuid.uuid4().hex[:12], at=round(time.time(), 3))
        tmp = spool(f".request-{req['id']}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.replace(tmp, spool("request.json"))
    return req


def _obj(body):
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    return body


def read_request(body):
    """POST /api/disk/read {device, identity, machine?, label, media, note?}."""
    body = _obj(body)
    label, media = body.get("label"), body.get("media")
    if not isinstance(label, str) or not LABEL.match(label.strip()):
        raise Bad(400, "label: 1-40 of letters, digits, '.', '_', '-' (it becomes the file name)")
    if media not in MEDIA:
        raise Bad(400, f"media: one of {', '.join(MEDIA)} (a USB bridge can't tell a disk from flash)")
    machine = _machine(body)
    _store()
    d = _disk(body.get("device"), body.get("identity"))
    note = body.get("note") if isinstance(body.get("note"), str) else ""
    return _queue({"op": "read", "device": d["name"], "identity": d["identity"], "machine": machine,
                   "label": label.strip(), "media": media, "realm": _realm(machine), "note": note[:500]})


def retry_request(body):
    """POST /api/disk/retry {machine, label}: ddrescue -r3 on that read's map (the read stopped on bad areas)."""
    body = _obj(body)
    label = body.get("label")
    if not isinstance(label, str) or not LABEL.match(label):
        raise Bad(400, "label: the label of the read that stopped")
    machine = _machine(body)
    _store()
    if not os.path.isfile(os.path.join(os.path.realpath(dl.DISKS), machine, label + ".read.json")):
        raise Bad(404, f"no read of {label} in progress for {machine}")
    return _queue({"op": "retry", "machine": machine, "label": label})


def check_request(body):
    """POST /api/disk/check {image, device, identity}: the TARGET CHECK."""
    body = _obj(body)
    _store()
    path, img = _image(body.get("image"))
    d = _disk(body.get("device"), body.get("identity"))
    return _queue({"op": "check", "image": path, "device": d["name"], "identity": d["identity"],
                   "machine": img.get("machine"), "realm": img.get("realm")})


def write_request(body, local):
    """POST /api/disk/write {image, device, ack?, verify_only?}: armed by a hold on the Pi's own screen."""
    if not local:
        raise Bad(403, "a disk write is armed at the Pi: hold WRITE on its own screen")
    body = _obj(body)
    _store()
    path, img = _image(body.get("image"))
    ack, verify_only = body.get("ack") is True, body.get("verify_only") is True
    device = body.get("device")

    def check_first():
        last = _read("status.json") or {}
        if not (last.get("op") == "check" and last.get("state") == "done" and last.get("target_ok") is True
                and last.get("image") == path and last.get("device") == device
                and (last.get("at") or 0) >= time.time() - CHECK_FRESH_S):
            raise Bad(409, "CHECK this image and drive first (a passing check in the last 5 minutes)")
        if last.get("needs_ack") and not ack and not verify_only:
            raise Bad(409, "the target isn't blank: acknowledge that it gets overwritten")
        if verify_only and not last.get("already_written"):
            raise Bad(409, "the target doesn't hold this image: write it instead")
        d = next((x for x in dl.disks() if x["name"] == device), None)
        if d is None or d["role"] != "ok" or not dl.same_drive(d["identity"], last.get("identity") or {}):
            raise Bad(409, f"{device} isn't the drive that was checked: check it again")
        return {"check": last}                      # the job re-validates it before it writes
    now = round(time.time(), 3)
    return _queue({"op": "write", "image": path, "device": device, "ack": ack, "verify_only": verify_only,
                   "machine": img.get("machine"), "realm": img.get("realm"),
                   "armed": {"by": "screen", "at": now, "until": now + ARM_S}}, check=check_first)


# --- the swap checklist ------------------------------------------------------------------------------------------------

def _ticks():
    try:
        with open(os.path.join(config.CTRL, "disk-checklists.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def checklist(write_id):
    """GET /api/disk/checklist?write=ID: the items, and the ticks for that write."""
    t = _ticks().get(write_id or "", {})
    return {"write": write_id, "items": [{"when": w, "item": k, "text": text, "done": bool((t.get(k) or {}).get("done")),
                                          "at": (t.get(k) or {}).get("at")} for w, k, text in CHECKLIST]}


def tick(body):
    """POST /api/disk/checklist {write, item, done}: tick or untick one item. It blocks nothing."""
    body = _obj(body)
    write_id, item, done = body.get("write"), body.get("item"), body.get("done")
    if not isinstance(write_id, str) or not re.match(r"^[0-9a-f]{12}$", write_id):
        raise Bad(400, "write: the write's job id")
    if item not in {k for _, k, _ in CHECKLIST} or not isinstance(done, bool):
        raise Bad(400, "item: one of the checklist's, done: true or false")
    with _lock:
        t = _ticks()
        t.setdefault(write_id, {})[item] = {"done": done, "at": round(time.time(), 3)}
        path = os.path.join(config.CTRL, "disk-checklists.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(t, f)
        os.replace(path + ".tmp", path)
    return checklist(write_id)


def watch():
    """Relay the job's progress: an SSE 'disk' event whenever the spool changes."""
    def loop():
        last = None
        while True:
            try:
                sig = tuple(os.stat(spool(n)).st_mtime_ns if os.path.exists(spool(n)) else 0
                            for n in ("request.json", "running.json", "status.json"))
                if sig != last:
                    if last is not None:
                        LIVE.emit("disk", status())
                    last = sig
            except OSError:
                pass
            time.sleep(0.5)
    threading.Thread(target=loop, name="disk-watch", daemon=True).start()
