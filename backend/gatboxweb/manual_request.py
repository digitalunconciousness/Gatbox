"""FIND A MANUAL: a manual on request (docs/plans/2026-11-03-phase-b-library.md, slice B8).

    the 7" → POST /api/library/games/<key>/manual/{search,files,fetch} → gatbox-web validates → <spool>/request.json
    gatbox-manuals.path sees it → gatbox-manuals.service runs `gatbox-manuals --job request.json`: it claims it (running.json),
        searches archive.org, lists one item's PDFs or downloads one, and writes status.json
    gatbox-web watches status.json → SSE "manual" → the 7" shows the candidates, then the files, then the fetched document;
        a finished fetch becomes the game's `document` in the library (sha256, pages, bytes) and the game stops being MISSING

gatbox-web never contacts the internet itself: the job does, as its own user, with the same host rules as `gatbox-manuals fetch`
(https from the manual archives only, a real PDF only). One job at a time. Everything here is the Pi's own screen only.
Without the job installed there's no spool, and every request answers 503 saying so.
"""
import json
import os
import re
import threading
import time
import uuid

from . import config, libsource, meter
from .live import LIVE

SPOOL = os.environ.get("GATBOX_MANUALS_SPOOL", "/var/spool/gatbox-manuals")
IA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
KINDS = ("manual", "schematics", "parts", "kit", "bulletin", "other")
STALE = 30 * 60                 # the job's TimeoutStartSec is 20 minutes: a request or claim older than this was left by a job that died
_lock = threading.Lock()
_adopted = set()


def spool(name):
    return os.path.join(SPOOL, name)


def _read(name):
    try:
        with open(spool(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _clear_stale():
    """Remove a request or a claim a dead job left behind, or the 7" would say "already running" for ever. Called with _lock held
    or from status(), where a race with a live job loses nothing: a job that old has been stopped by systemd."""
    for name in ("request.json", "running.json"):
        try:
            if time.time() - os.stat(spool(name)).st_mtime > STALE:
                os.remove(spool(name))
        except OSError:
            pass


def status():
    """GET /api/library/manual: whether the job is installed and busy, and the last result."""
    _clear_stale()
    queued, running = os.path.exists(spool("request.json")), os.path.exists(spool("running.json"))
    req = (_read("request.json") if queued else _read("running.json") if running else None) or {}
    return {"spool": os.path.isdir(SPOOL), "busy": queued or running, "queued": queued, "running": running,
            "status": _read("status.json"), "request": {k: req.get(k) for k in ("op", "key", "id") if k in req} or None}


def _queue(fields):
    if not os.path.isdir(SPOOL):
        raise meter.Bad(503, "the manuals service isn't installed on this Pi yet (no spool)")
    with _lock:
        _clear_stale()
        if os.path.exists(spool("request.json")) or os.path.exists(spool("running.json")):
            raise meter.Bad(409, "a manual search or download is already running")
        req = dict(fields, id=uuid.uuid4().hex[:12], at=round(time.time(), 3))
        tmp = spool(f".request-{req['id']}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.replace(tmp, spool("request.json"))
    return req


def _gate(local, key, body):
    if not local:
        raise meter.Bad(403, "a manual is looked for on the Pi's own screen")
    if not isinstance(body, dict):
        raise meter.Bad(400, "expected a JSON object")
    v = libsource.current()
    if v is None:
        raise meter.Bad(409, "the game library isn't set up on this Pi yet (gatbox-library seed --plan)")
    game = v.lib.resolve(key)
    if game is None:
        raise meter.Bad(404, f"no game {key!r} in the library")
    return v, game["id"]


def search(key, body, local):
    """POST /api/library/games/<key>/manual/search {query}: archive.org's texts with every word in their title."""
    _, key = _gate(local, key, body)
    q = " ".join(str(body.get("query") or "").split())
    if not 2 <= len(q) <= 80:
        raise meter.Bad(400, "query: 2 to 80 characters")
    return _queue({"op": "search", "key": key, "query": q})


def files(key, body, local):
    """POST /api/library/games/<key>/manual/files {identifier}: the PDFs in one archive.org item."""
    _, key = _gate(local, key, body)
    ident = body.get("identifier")
    if not isinstance(ident, str) or not IA_ID.match(ident):
        raise meter.Bad(400, "identifier: an archive.org item's identifier")
    return _queue({"op": "files", "key": key, "identifier": ident})


def _doc_id(v, key, folder_files, title):
    """A document id for the new file: the title as a slug, made unique among the game's documents and its folder's files."""
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70] or "manual"
    taken = {d["data"]["doc_id"] for d in v.recs("document", key)} | {f[:-4] for f in folder_files}
    cand, i = base, 2
    while cand in taken:
        cand, i = f"{base}-{i}", i + 1
    return cand


def fetch(key, body, local):
    """POST /api/library/games/<key>/manual/fetch {identifier, file, title?, kind?}: download one PDF into the game's folder."""
    v, key = _gate(local, key, body)
    ident, name = body.get("identifier"), body.get("file")
    if not isinstance(ident, str) or not IA_ID.match(ident):
        raise meter.Bad(400, "identifier: an archive.org item's identifier")
    if (not isinstance(name, str) or not name.lower().endswith(".pdf") or ".." in name or name.startswith("/")
            or not 0 < len(name) <= 200):
        raise meter.Bad(400, "file: the name of a PDF in that item")
    kind = body.get("kind") if body.get("kind") in KINDS else "manual"
    title = " ".join(str(body.get("title") or os.path.splitext(os.path.basename(name))[0]).split())[:120]
    folder = v.primary(key)
    try:
        have = os.listdir(os.path.join(config.MANUALS, folder))
    except OSError:
        have = []
    return _queue({"op": "fetch", "key": key, "folder": folder, "doc_id": _doc_id(v, key, have, title), "identifier": ident,
                   "file": name, "title": title, "kind": kind})


def adopt(st):
    """A finished fetch becomes the game's document in the library: its hash, pages and size. Safe to repeat: a document that's
    there with its hash is left alone, so a restart between the download and this changes nothing."""
    if not st or st.get("op") != "fetch" or st.get("state") != "done" or not st.get("sha256"):
        return False
    v = libsource.current()
    key, did = st.get("key"), st.get("doc_id")
    if v is None or not key or not did or key not in v.games:
        return False
    rid = f"{key}/{did}"
    cur = v.lib.get("document", rid)
    if cur and cur["data"]["sha256"] == st["sha256"]:
        return False
    data = {"model_key": key, "doc_id": did, "title": st.get("title") or did, "kind": st.get("kind") or "manual",
            "source_url": st.get("source_url") if str(st.get("source_url", "")).startswith("https://") else None,
            "source": st.get("source") or "", "sha256": st["sha256"], "pages": st.get("pages"), "bytes": st.get("bytes")}
    res = v.lib.put("document", rid, data, cur["rev"] if cur else 0, by="manuals")
    return res["result"] in ("created", "updated")


def watch():
    """Relay the job's progress (SSE "manual") and adopt a finished fetch into the library."""
    def loop():
        last = None
        while True:
            try:
                sig = tuple(os.stat(spool(n)).st_mtime_ns if os.path.exists(spool(n)) else 0
                            for n in ("request.json", "running.json", "status.json"))
                if sig != last:
                    st = _read("status.json")
                    if st and st.get("op") == "fetch" and st.get("job") not in _adopted and adopt(st):
                        _adopted.add(st.get("job"))
                        LIVE.emit("library", {"key": st.get("key"), "action": "manual"})
                    if last is not None:
                        LIVE.emit("manual", status())
                    last = sig
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
    threading.Thread(target=loop, name="manual-watch", daemon=True).start()

