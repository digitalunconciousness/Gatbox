"""The LIBRARY's title search (docs/plans/2026-11-03-phase-b-library.md, slice B5): MAME's own game list, offline.

`gatbox-mame-roms --titles` writes it at install, once per MAME version: [set, description, year, maker, parent] for every game
MAME knows. A search suggests a game, its key and its MAME set for a machine the library has no game for; pinball and games
MAME doesn't have are typed instead (ADD GAME). Nothing here leaves the Pi.
"""
import json
import os
import re
import threading

from gatboxlib import library as L

from . import config, libsource, meter

LIMIT = 30
_cache = {"sig": None, "titles": None, "meta": None}
_lock = threading.Lock()


def _load():
    try:
        st = os.stat(config.MAME_TITLES)
    except OSError:
        return None, None
    sig = (st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["sig"] != sig:
            try:
                with open(config.MAME_TITLES, encoding="utf-8") as f:
                    d = json.load(f)
                _cache.update(sig=sig, titles=[(r[0], r[1], r[2], r[3], r[4], _plain(r[1])) for r in d["titles"]], meta=d.get("meta") or {})
            except (OSError, ValueError, KeyError, IndexError):
                _cache.update(sig=sig, titles=None, meta=None)
        return _cache["titles"], _cache["meta"]


def game_title(desc):
    """A game's title from MAME's description: the trailing "(version 1.6)", "(Japan, rev. C)" parts are versions, not the title."""
    t = re.sub(r"\s*\([^()]*\)\s*$", "", desc).strip()
    return t or desc


def _plain(text):
    return " ".join(text.lower().replace("-", " ").replace(".", " ").split())


def _rank(row, tokens, q):
    title = _plain(game_title(row[1]))
    if title == q:
        return 0
    if title.startswith(q):
        return 1
    if all(re.search(r"\b" + re.escape(t), title) for t in tokens):
        return 2
    return 3


def search(q):
    """GET /api/library/titles?q=: up to 30 games whose description holds every word typed, best first (the exact title, then a
    title that starts with it, then words at the start of words), parents before their clones. Each carries the key a new game
    would get, and the game in the library that already is it (by MAME set or by key), if any."""
    titles, meta = _load()
    if titles is None:
        return {"built": False, "mame": None, "results": [],
                "why": "MAME's title list isn't built on this Pi yet: the install builds it from MAME (gatbox-mame-roms --titles)."}
    q = _plain(str(q or ""))
    if len(q) < 2:
        raise meter.Bad(400, "q: at least 2 characters")
    tokens = q.split()
    hits = [r for r in titles if all(t in r[5] for t in tokens)]
    hits.sort(key=lambda r: (_rank(r, tokens, q), bool(r[4]), len(r[1]), r[0]))
    v = libsource.current()
    by_set = {}
    if v:
        for k in v.live():
            for m in v.recs("mame", k):
                if m["data"]["set"]:
                    by_set[m["data"]["set"]] = k
    taken = set(v.games) if v else set()
    out = []
    for r in hits[:LIMIT]:
        title, year = game_title(r[1]), int(r[2]) if r[2].isdigit() else None
        try:
            key = L.model_key(title, "video", r[3] or None, year, taken=taken)
        except ValueError:
            key = None
        known = by_set.get(r[0]) or by_set.get(r[4]) or (v.key_for(key) if v and key else None)
        out.append({"set": r[0], "desc": r[1], "title": title, "year": year, "mfr": r[3], "clone_of": r[4] or None,
                    "key": key, "in_library": known})
    return {"built": True, "mame": meta.get("mame"), "count": len(hits), "results": out}
