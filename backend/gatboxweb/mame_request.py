"""Chip lists for games added after the seed (docs/plans/2026-11-04-phase-d-archive.md, slice D4).

The seed built every game's ROM list from MAME. A game added later (FIND GAME on the 7", or the archive) has its MAME set noted
but no list, so CHECK says "NO CHIP LIST". This closes that gap the way FIND A MANUAL does, with a spool and a job:

    gatbox-web notices a game with a MAME set and no ROM list → <spool>/request.json {id, sets}
    gatbox-mame-roms.path sees it → gatbox-mame-roms.service runs `gatbox-mame-roms --job`: it reads `mame -listxml` once for all
        the sets asked for (each with its clones) and writes status.json. No network, no library, no ROM files: hashes only
    gatbox-web reads status.json and makes each game's `rom_list` record (create-only: a list that exists is never touched)

Nothing here needs the job to exist: without the spool (units not installed) a game just stays without a list, as before, and
SYSTEM says how many are waiting. A set MAME has no entry for is remembered and not asked for again for a day. Everything is
the Pi's own doing; no screen action is needed.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

from gatboxlib import library as L

from . import config, libsource

SPOOL = os.environ.get("GATBOX_MAME_SPOOL", "/var/spool/gatbox-mame")
STALE = 30 * 60                       # the job's timeout is 20 minutes: older than this, a request or claim belongs to a job that died
TRY_AGAIN = 24 * 3600
MAX_SETS = 200
_lock = threading.Lock()


def spool(name):
    return os.path.join(SPOOL, name)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _state_path():
    return os.path.join(config.CTRL, "mame-requests.json")


def _state():
    s = _read(_state_path())
    return s if isinstance(s, dict) else {}


def _save(state):
    os.makedirs(config.CTRL, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _state_path())


def wanted():
    """[(game key, MAME set)] for every live game that notes a set and has no ROM list, by key."""
    v = libsource.current()
    if v is None:
        return []
    out = []
    for key in sorted(v.live()):
        m = (v.recs("mame", key) or [None])[0]
        if m and m["data"]["set"] and not v.recs("rom_list", key):
            out.append((key, m["data"]["set"]))
    return out


def status():
    """GET /api/library/mame: whether the job is installed and busy, which games are waiting, and the last answer."""
    w = wanted()
    answer = _read(spool("status.json"))
    return {"spool": os.path.isdir(SPOOL), "busy": os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")),
            "waiting": [{"key": k, "set": s} for k, s in w],
            "last": None if not isinstance(answer, dict) else {k: answer.get(k) for k in ("state", "at", "mame", "error", "missing")}}


def _clear_stale():
    for name in ("request.json", "running.json"):
        try:
            if time.time() - os.stat(spool(name)).st_mtime > STALE:
                os.remove(spool(name))
        except OSError:
            pass


def ingest():
    """Make the ROM lists the job's answer carries for games still waiting. -> how many were made."""
    state = _state()
    pend, answer = state.get("pending"), _read(spool("status.json"))
    if not pend or not isinstance(answer, dict) or answer.get("job") != pend.get("id"):
        return 0
    made = 0
    v = libsource.current()
    if answer.get("state") == "done" and v is not None:
        lib = v.lib
        sets = answer.get("sets") or {}
        for key, name in wanted():
            ver = (sets.get(name) or {}).get("versions")
            if not ver:
                continue
            data = {"model_key": key, "mame": answer.get("mame") or "", "set": name,
                    "versions": [{"name": x.get("name"), "parent": x.get("parent"), "description": x.get("desc", ""), "year": x.get("year", ""),
                                  "mfr": x.get("mfr", ""), "roms": x.get("roms", []), "disks": x.get("disks", [])} for x in ver]}
            r = lib.put("rom_list", key, data, 0, uid=f"mame-{pend['id']}-{key}"[:64], by="mame")
            if r["result"] in ("created", "duplicate"):
                made += r["result"] == "created"
            elif r["result"] != "conflict":
                state.setdefault("refused", {})[key] = str(r.get("reason"))[:200]
    state.pop("pending", None)
    _save(state)
    return made


def tick():
    """One look: take an answer if there is one, then ask for the sets still without a list (one request at a time)."""
    with _lock:
        if not os.path.isdir(SPOOL):
            return
        _clear_stale()
        ingest()
        if os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")):
            return
        state = _state()
        if state.get("pending"):
            if time.time() - state["pending"].get("at", 0) > STALE:
                state.pop("pending")                         # asked, and nothing came back: the tried marks keep it from hammering
                _save(state)
            return
        tried = state.setdefault("tried", {})
        todo = sorted({s for _, s in wanted() if time.time() - tried.get(s, 0) > TRY_AGAIN})[:MAX_SETS]
        if not todo:
            return
        rid = uuid.uuid4().hex
        tmp = spool(f".request-{rid[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "sets": todo, "at": time.time()}, f)
        os.chmod(tmp, 0o664)
        os.replace(tmp, spool("request.json"))
        for s in todo:
            tried[s] = time.time()
        state["pending"] = {"id": rid, "at": time.time(), "sets": todo}
        _save(state)


def watch(every=5.0):
    def loop():
        while True:
            try:
                tick()
            except (OSError, ValueError, KeyError, sqlite3.Error, L.Reject):
                pass                                          # the next look tries again
            time.sleep(every)
    threading.Thread(target=loop, name="mame-request", daemon=True).start()
