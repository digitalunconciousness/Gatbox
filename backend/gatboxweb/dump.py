"""The DUMP flow (M7). gatbox-web never touches the T48: its sandbox can't even see the programmer.

    dashboard → POST /api/dump → gatbox-web validates, writes <spool>/request.json
    gatbox-dump.path sees it → gatbox-dump.service (user gatbox-dump: the T48 and the archive, nothing else) runs
        `gatbox-dump --job request.json`: claims it (→ running.json), reads, identifies, archives, writes status.json
    gatbox-web watches status.json → SSE "dump" events → the dashboard's progress and result card

One dump at a time: a request is refused while one is queued or running. The spool (/var/spool/gatbox-dump) is
group gatbox-dump, setgid; gatbox-web joins that group for it. Part names are minipro's own, from the list the
bootstrap generates at install (minipro -q T48 -l); the tiles are families from data/eproms.json.
"""
import glob
import hashlib
import json
import os
import re
import threading
import time
import uuid

from gatboxlib import profiles

from . import config, devices, roster
from .live import LIVE
from .meter import Bad, resolved

LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")      # gatbox-dump's rule: the label becomes a file name
_parts = [None, []]                                            # (mtime, names)
_lock = threading.Lock()


def spool(name):
    return os.path.join(config.DUMP_SPOOL, name)


def all_parts():
    """minipro's T48 part names (the install-time list; '#' lines are its header)."""
    try:
        st = os.stat(config.MINIPRO_PARTS)
    except OSError:
        return []
    if _parts[0] != st.st_mtime:
        with open(config.MINIPRO_PARTS, encoding="utf-8", errors="replace") as f:
            names = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        _parts[:] = [st.st_mtime, names]
    return _parts[1]


def is_dip(name):
    return "@" not in name or "@DIP" in name.upper()


def families():
    d = profiles._load(os.path.join(profiles.data_dir(), "eproms.json")) or {}
    return d.get("families", [])


def search(q=None, family=None, limit=300):
    """GET /api/dump/parts: the families, and the parts matching a family's terms or a free search. DIP first,
    minipro's order within each group, no duplicates (minipro lists some names twice)."""
    fams = families()
    terms = None
    if family:
        f = next((x for x in fams if x["id"] == family), None)
        if not f:
            raise Bad(404, f"no such family: {family!r}")
        terms = [t.lower() for t in f["search"]]
    elif q:
        q = q.strip()
        if len(q) < 2 or len(q) > 40:
            raise Bad(400, "search: 2 to 40 characters")
        terms = [q.lower()]
    out = []
    if terms:
        seen = set()
        for n in all_parts():
            if n not in seen and any(t in n.lower() for t in terms):
                seen.add(n)
                out.append(n)
        out = [n for n in out if is_dip(n)] + [n for n in out if not is_dip(n)]
    return {"families": fams, "parts": out[:limit], "more": max(0, len(out) - limit),
            "installed": bool(all_parts())}


def _read(name):
    try:
        with open(spool(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status():
    """GET /api/dump: the T48, whether a dump is queued/running, and the latest progress or result."""
    queued, running = os.path.exists(spool("request.json")), os.path.exists(spool("running.json"))
    st = _read("status.json")
    t48 = devices.snapshot()["t48"]
    req = (_read("request.json") if queued else _read("running.json") if running else None) or {}
    # what's queued or running (a dump, a blank check, a burn), so the progress card is titled by it at once
    request = {k: req.get(k) for k in ("op", "part", "label", "image") if k in req} or None
    if request is not None:
        request.setdefault("op", "dump")
    return {"t48": t48, "busy": queued or running, "queued": queued, "running": running, "status": st,
            "request": request, "spool": os.path.isdir(config.DUMP_SPOOL)}


def request(body):
    """POST /api/dump {part, label, machine?, ignore_id?, keep_blank?, yes?}."""
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    part, label = body.get("part"), body.get("label")
    if not isinstance(part, str) or part not in all_parts():
        raise Bad(400, f"not a T48 part name in minipro: {part!r}")
    if not isinstance(label, str) or not LABEL.match(label.strip()):
        raise Bad(400, "label: 1-40 of letters, digits, '.', '_', '-' (it becomes the file name)")
    machine = body.get("machine") or resolved()["machine"] or "unassigned"
    if machine != "unassigned" and machine not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {machine!r}")
    return _queue({"part": part, "label": label.strip(), "machine": machine,
                   "ignore_id": body.get("ignore_id") is True, "keep_blank": body.get("keep_blank") is True,
                   "yes": body.get("yes") is True})


def _queue(fields, check=None):
    """request.json for gatbox-dump.service (the path unit fires on that name only): one job at a time, the T48 there.
    check(): a last look at the state, under the lock, just before the request is written."""
    if not os.path.isdir(config.DUMP_SPOOL):
        raise Bad(503, "the dump service isn't installed (no spool)")
    with _lock:
        if os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")):
            raise Bad(409, "a dump is already queued or running")
        if not devices.snapshot()["t48"]["present"]:
            raise Bad(409, "the T48 isn't plugged in")
        if check:
            check()
        req = dict(fields, id=uuid.uuid4().hex[:12], at=round(time.time(), 3))
        tmp = spool(f".request-{req['id']}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.replace(tmp, spool("request.json"))
    return req


# --- BURN (2026-09-29, the owner's decision): an image from the archive into a blank chip. gatbox-web only queues the
# BLANK CHECK and the armed burn; gatbox-dump.service does both (see tools/gatbox-dump, class Burn). The burn is
# accepted from the Pi's own screen only (the hold stands in for the physical ARM button to come), and only after a
# blank check of the same image and part that passed in the last 5 minutes.
ARM_S, BLANK_FRESH_S = 30, 300
_sha = {}                                                        # (path, size, mtime) -> sha1, for images with no sidecar


def images():
    """GET /api/burn/images: every .bin in the archive's machine folders and in _images/, by real path inside it."""
    root = os.path.realpath(config.ROMS)
    out = []
    for p in sorted(glob.glob(os.path.join(config.ROMS, "*", "*.bin"))):
        real = os.path.realpath(p)
        if not real.startswith(root + os.sep) or not os.path.isfile(real):
            continue                                             # a link out of the archive is never offered
        folder, name = os.path.basename(os.path.dirname(p)), os.path.basename(p)
        side = {}
        try:
            with open(p[:-4] + ".json", encoding="utf-8") as f:
                side = json.load(f)
        except (OSError, ValueError):
            pass
        st = os.stat(real)
        sha1 = side.get("sha1")
        if not sha1:
            k = (real, st.st_size, st.st_mtime_ns)
            if k not in _sha:
                h = hashlib.sha1()
                with open(real, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                _sha[k] = h.hexdigest()
            sha1 = _sha[k]
        ri = side.get("romident") or {}
        out.append({"image": f"{folder}/{name}", "folder": folder, "name": name, "label": side.get("label"),
                    "size": st.st_size, "sha1": sha1, "match": ri.get("match"),
                    "matches": [f"{m.get('set')}/{m.get('rom')}" for m in ri.get("matches", [])]})
    return out


def _image(rel):
    """An image from the list above (by its archive-relative name), as the absolute path the job gets."""
    if not isinstance(rel, str) or rel not in {x["image"] for x in images()}:
        raise Bad(400, f"not an image in the archive: {rel!r}")
    return os.path.join(config.ROMS, rel)


def _burn_fields(body):
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    image, part = _image(body.get("image")), body.get("part")
    if not isinstance(part, str) or part not in all_parts():
        raise Bad(400, f"not a T48 part name in minipro: {part!r}")
    machine = body.get("machine") or resolved()["machine"] or "unassigned"
    if machine != "unassigned" and machine not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {machine!r}")
    return {"image": image, "part": part, "machine": machine, "yes": body.get("yes") is True}


def blank_request(body):
    """POST /api/burn/blank {image, part, machine?, yes?}: the BLANK CHECK (size, pin check, minipro -b)."""
    return _queue(dict(_burn_fields(body), op="blank"))


def burn_request(body, local):
    """POST /api/burn {image, part, machine?, yes?}: the burn, armed by a hold on the Pi's own screen."""
    if not local:
        raise Bad(403, "a burn is armed at the Pi: hold BURN on its own screen")
    f = _burn_fields(body)

    def blank_first():
        last = _read("status.json") or {}
        if not (last.get("op") == "blank" and last.get("state") == "done" and last.get("blank_ok") is True
                and last.get("image") == f["image"] and last.get("part") == f["part"]
                and (last.get("finished_at") or 0) >= time.time() - BLANK_FRESH_S):
            raise Bad(409, "BLANK CHECK this image and part first (a passing one in the last 5 minutes)")
    now = round(time.time(), 3)
    return _queue(dict(f, op="burn", armed={"by": "screen", "at": now, "until": now + ARM_S}), check=blank_first)


def dumps(slug):
    """GET /api/dumps?machine=: the archive's sidecars for a machine, newest first."""
    if slug != "unassigned" and slug not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {slug!r}")
    out = []
    for side in glob.glob(os.path.join(config.ROMS, slug, "*.json")):
        try:
            with open(side, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        ri = j.get("romident") or {}
        out.append({"file": os.path.basename(side)[:-5] + ".bin", "label": j.get("label"), "part": j.get("part"),
                    "size": j.get("size"), "sha1": j.get("sha1"), "crc32": j.get("crc32"), "date": j.get("date"),
                    "epoch": j.get("epoch"), "match": ri.get("match"), "matches": ri.get("matches", []),
                    "blank": j.get("blank"), "id_check": j.get("id_check")})
    return sorted(out, key=lambda x: x.get("epoch") or 0, reverse=True)


def watch():
    """Relay the job's progress: an SSE 'dump' event whenever the spool changes."""
    def loop():
        last = None
        while True:
            try:
                sig = tuple(os.stat(spool(n)).st_mtime_ns if os.path.exists(spool(n)) else 0
                            for n in ("request.json", "running.json", "status.json"))
                if sig != last:
                    if last is not None:
                        LIVE.emit("dump", status())
                    last = sig
            except OSError:
                pass
            time.sleep(0.5)
    threading.Thread(target=loop, name="dump-watch", daemon=True).start()
