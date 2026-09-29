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
    return {"t48": t48, "busy": queued or running, "queued": queued, "running": running, "status": st,
            "spool": os.path.isdir(config.DUMP_SPOOL)}


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
    if not os.path.isdir(config.DUMP_SPOOL):
        raise Bad(503, "the dump service isn't installed (no spool)")
    with _lock:
        if os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")):
            raise Bad(409, "a dump is already queued or running")
        if not devices.snapshot()["t48"]["present"]:
            raise Bad(409, "the T48 isn't plugged in")
        req = {"id": uuid.uuid4().hex[:12], "part": part, "label": label.strip(), "machine": machine,
               "ignore_id": body.get("ignore_id") is True, "keep_blank": body.get("keep_blank") is True,
               "yes": body.get("yes") is True, "at": round(time.time(), 3)}
        tmp = spool(f".request-{req['id']}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.replace(tmp, spool("request.json"))            # the path unit fires on this name only
    return req


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
