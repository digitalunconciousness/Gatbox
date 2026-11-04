"""Conflicts between this deck's library and the archive's, and the deck's side of the sync, for the 7"
(docs/plans/2026-11-04-phase-d-archive.md, slice D4).

gatbox-sync (its own user) judges every record and, when the deck and the archive both changed one differently, keeps it in
`<sync state>/library/conflicts.json` with both versions and what they started from; it writes nothing on either side. This
module only reads that file (the folder is world-readable) and shows each conflict as BEFORE / THIS DECK / THE ARCHIVE with
the differing fields marked. The deck side shown is the library's record *now*, so a game she just EDITed shows as edited.

Her choice is a local write marked as such: `library-resolutions.json` in gatbox-web's own state folder, {key, keep, deck,
archive}: the two states she was shown. The sync reads it back over loopback on its next run and applies it only if both sides
still hold exactly those (otherwise she sees the conflict again, as it is by then). Nothing here writes the library, and the
choice is the Pi's own screen only, like every other edit.

Also the sync's own numbers for SYSTEM's Library tile: when it last ran and how it went, records changed on the deck that the
archive hasn't got yet, manuals and ROM images waiting to be sent, conflicts, and whether the two libraries are identical.
"""
import json
import os
import threading
import time

from . import config, hubs, libsource
from .meter import Bad

RES_FILE = "library-resolutions.json"
KEEP = ("deck", "archive")
SHOW_MAX = 300
_cache = {}
_lock = threading.Lock()


def root():
    return os.path.join(hubs._root(), "library")


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _cached(path, make, default):
    """The file's parsed contents, remembered until it changes (state.json holds the base of every record: about a megabyte)."""
    try:
        st = os.stat(path)
    except OSError:
        return default
    sig = (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == sig:
            return hit[1]
    raw = _read(path, None)
    value = make(raw) if isinstance(raw, dict) else default
    with _lock:
        _cache[path] = (sig, value)
    return value


def state_of(rec):
    return None if rec is None else ("deleted" if rec["deleted"] else rec["hash"])


# --- reading a conflict ---------------------------------------------------------------------------------------------------

LABEL = {"mfr": "MAKER", "kind": "KIND", "doc_id": "DOCUMENT", "source_url": "SOURCE ADDRESS", "model_key": "GAME",
         "lo": "LOW (V)", "hi": "HIGH (V)", "what": "ABOUT", "set": "MAME SET", "prefer": "PREFERRED VERSION",
         "why": "WHY", "sha256": "FILE (SHA-256)", "crc32": "CRC-32", "sha1": "SHA-1"}


def _show(name, v):
    """One field as text: a value, or a short summary of one too big to compare by eye."""
    if v is None:
        return "—"
    if name == "versions" and isinstance(v, list):
        return f"{len(v)} version{'s' if len(v) != 1 else ''}, {sum(len(x.get('roms', [])) for x in v)} ROMs"
    if name == "romident" and isinstance(v, dict):
        return "MAME's answer: " + ("match" if v.get("match") else "no match")
    if name == "confirmed":
        return f"confirmed by {v['by']}" if isinstance(v, dict) else "not confirmed"
    if name in ("sha256", "sha1") and isinstance(v, str):
        return v[:16] + "…"
    if isinstance(v, (dict, list)):
        v = json.dumps(v, sort_keys=True)
    text = str(v)
    return text if len(text) <= SHOW_MAX else text[:SHOW_MAX] + "…"


def _name(kind, rid, rec_data, titles):
    """What the 7" calls the entry: the game's title first, then what it is about."""
    d = rec_data or {}
    game = rid if kind == "game" else d.get("model_key")
    title = titles.get(game) or game or ""
    what = {"game": "", "document": d.get("title") or d.get("doc_id") or "document", "fact": d.get("what") or "fact",
            "rail_limit": f"{d.get('rail') or '?'} limit", "mame": "MAME set", "rom_list": "ROM list",
            "rom_image": d.get("label") or d.get("chip") or "ROM image"}.get(kind, kind)
    return game, (f"{title}: {what}" if what else title)


def _side(rec):
    if rec is None:
        return None
    return {"deleted": bool(rec["deleted"]), "by": rec.get("by"), "at": rec.get("at"), "rev": rec.get("rev")}


def _fields(base, deck, archive):
    """The fields of a record, each with its three values and which side changed it. A removed side has no fields; against a
    removal only what the live side changed since the base is listed (the rest is the same record, restated)."""
    names = []
    for src in (deck, archive, base):
        for k in (src or {}):
            if k not in names:
                names.append(k)
    out = []
    for k in names:
        b, d, a = (base or {}).get(k), (deck or {}).get(k), (archive or {}).get(k)
        if deck is not None and archive is not None and d == a and (base is None or b == d):
            continue
        if (deck is None or archive is None) and base is not None and (d if archive is None else a) == b:
            continue
        out.append({"name": k, "label": LABEL.get(k, k.upper().replace("_", " ")), "base": _show(k, b) if base is not None else None,
                    "deck": _show(k, d) if deck is not None else None, "archive": _show(k, a) if archive is not None else None,
                    "differs": deck is not None and archive is not None and d != a,
                    "deck_changed": base is not None and deck is not None and d != b,
                    "archive_changed": base is not None and archive is not None and a != b})
    return out


def _titles(v):
    return {k: g["data"]["title"] for k, g in v.games.items()}


def _pending():
    return {r["key"]: r for r in (_read(os.path.join(config.CTRL, RES_FILE), {}).get("resolutions") or []) if isinstance(r, dict) and "key" in r}


def listing():
    """GET /api/library/conflicts: {seeded, count, conflicts: [...], sync: {...}}."""
    v = libsource.current()
    sync = summary(v)
    if v is None:
        return {"seeded": False, "count": 0, "conflicts": [], "sync": sync}
    titles = _titles(v)
    pend = _pending()
    out = []
    for c in _cached(os.path.join(root(), "conflicts.json"), lambda j: j.get("conflicts") or [], []):
        kind, _, rid = c["key"].partition("|")
        deck_rec = v.lib.get(kind, rid, deleted=True)                      # the deck as it is now, not as the sync saw it
        arch = c.get("archive")
        ds, as_ = state_of(deck_rec), state_of(arch)
        if ds == as_:
            continue                                                       # they agree since the sync looked: the next run clears it
        base = c.get("base")
        bdata = (base or {}).get("data")
        data = {"deck": None if deck_rec is None or deck_rec["deleted"] else deck_rec["data"],
                "archive": None if arch is None or arch["deleted"] else arch["data"]}
        game, name = _name(kind, rid, data["deck"] or data["archive"] or bdata, titles)
        item = {"key": c["key"], "kind": kind, "id": rid, "game": game, "name": name, "since": c.get("since"),
                "deck_state": ds, "archive_state": as_, "deck": _side(deck_rec), "archive": _side(arch),
                "base": None if base is None else {"state": base.get("s"), "known": bdata is not None or kind == "rom_list"},
                "fields": _fields(bdata, data["deck"], data["archive"]),
                "editable": kind == "game" and data["deck"] is not None, "deck_data": data["deck"] if kind == "game" else None,
                "chosen": (pend.get(c["key"]) or {}).get("keep")}
        if item["chosen"] and ((pend[c["key"]].get("deck"), pend[c["key"]].get("archive")) != (ds, as_)):
            item["chosen"] = None                                          # she chose about other versions than these
        out.append(item)
    out.sort(key=lambda i: (i["name"].lower(), i["key"]))
    return {"seeded": True, "count": len(out), "conflicts": out, "sync": sync}


def by_game():
    """{game key: how many conflicts} for the badge wherever a game shows. Cheap: no record is compared here."""
    out = {}
    for c in _cached(os.path.join(root(), "conflicts.json"), lambda j: j.get("conflicts") or [], []):
        kind, _, rid = c["key"].partition("|")
        side = c.get("deck") or c.get("archive") or {}
        game = rid if kind == "game" else (side.get("data") or {}).get("model_key")
        if game:
            out[game] = out.get(game, 0) + 1
    return out


# --- her choice -----------------------------------------------------------------------------------------------------------

def choose(body, local):
    """POST /api/library/conflicts/resolve {key, keep: "deck"|"archive", deck, archive}: the Pi's own screen only. `deck` and
    `archive` are the two states the conflict showed (deck_state, archive_state): a choice about versions that have changed since
    is refused, so she never decides about something she didn't see."""
    if not local:
        raise Bad(403, "a conflict is settled on the Pi's own screen")
    v = libsource.current()
    if v is None:
        raise Bad(409, "the game library isn't set up on this Pi yet")
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    key, keep = body.get("key"), body.get("keep")
    if keep not in KEEP:
        raise Bad(400, "keep: \"deck\" or \"archive\"")
    now = next((c for c in listing()["conflicts"] if c["key"] == key), None)
    if now is None:
        raise Bad(404, "that isn't a conflict any more (the next sync run clears it)")
    if now["deck_state"] != body.get("deck") or now["archive_state"] != body.get("archive"):
        raise Bad(409, "one side changed since you looked: open the conflict again")
    with _lock:
        path = os.path.join(config.CTRL, RES_FILE)
        rows = [r for r in (_read(path, {}).get("resolutions") or []) if isinstance(r, dict) and r.get("key") != key]
        rows.append({"key": key, "keep": keep, "deck": now["deck_state"], "archive": now["archive_state"], "at": int(time.time())})
        os.makedirs(config.CTRL, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"resolutions": rows}, f)
        os.replace(path + ".tmp", path)
    return {"key": key, "keep": keep, "queued": True,
            "note": "The next sync run applies it" + ("." if summary(v)["at"] else " (the sync hasn't run yet).")}


# --- the sync's numbers, for SYSTEM -------------------------------------------------------------------------------------------

def _waiting(v):
    """Records changed on the deck since the sync last looked that the archive doesn't hold yet (those pulled from the archive
    are not counted: their hash is the base's)."""
    st = _cached(os.path.join(root(), "state.json"), lambda j: {"cursor": int(j.get("deck_cursor", 0)), "base": {k: b.get("s") for k, b in (j.get("base") or {}).items()}}, None)
    if st is None:
        return None
    n, cursor = 0, st["cursor"]
    for _ in range(2):
        page = v.lib.changes(cursor, 500)
        for r in page["records"]:
            if st["base"].get(f"{r['kind']}|{r['id']}") != state_of(r):
                n += 1
        cursor = page["cursor"]
        if not page["more"]:
            return n
    return n


def _files_waiting():
    """(manuals and ROM images the deck holds that were never sent, documents listed with no file here), cached half a minute."""
    with _lock:
        hit = _cache.get("files")
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
    try:
        from . import libsync_api
        listed = libsync_api.files()
        sent = _read(os.path.join(root(), "files.json"), {}).get("up", {})
        out = (len([s for s in listed["have"] if s not in sent]), len(listed["want"]))
    except (Bad, OSError, ValueError):
        out = None
    with _lock:
        _cache["files"] = (time.monotonic(), out)
    return out


def summary(v=None):
    """What SYSTEM shows of the sync: {configured, at, ok, error, conflicts, identical, waiting, files_to_send, files_to_fetch}."""
    st = (_read(os.path.join(hubs._root(), "status.json"), {}) or {}).get("library")
    out = {"configured": isinstance(st, dict), "at": None, "ok": None, "error": None, "conflicts": 0, "pending": 0, "identical": None,
           "waiting": None, "files_to_send": None, "files_to_fetch": None}
    if isinstance(st, dict):
        out.update(at=st.get("at"), ok=st.get("ok"), error=st.get("error"), pending=st.get("pending") or 0, identical=st.get("identical"),
                   refused=bool(st.get("refused_by_archive")), dryrun=bool(st.get("dryrun")))
    out["conflicts"] = len(_cached(os.path.join(root(), "conflicts.json"), lambda j: j.get("conflicts") or [], []))
    if v is not None and out["configured"]:
        out["waiting"] = _waiting(v)
        fw = _files_waiting()
        if fw:
            out["files_to_send"], out["files_to_fetch"] = fw
    return out
