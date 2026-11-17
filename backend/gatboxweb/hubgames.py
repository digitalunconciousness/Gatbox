"""Games for the hubs' machines (docs/plans/2026-11-17-hub-games.md; the owner, 2026-11-17: new cabcrawl and tracker games should
reach the archive without her going to it).

Each hub's machine list (cached by gatbox-sync) names a machine's game by its `model_key`. When a key names no game in the deck's
library, the deck makes the game itself, titled from the machine's name, and the library sync takes it to the archive; the apps'
ten-minute read then finds it. Nobody visits the archive.

Unless it might be a game the library already has under another key (a title that reads the same once "II" is "2", "&" is "and"
and "the" is dropped, or a key that does): then nothing is made, and it waits on the 7" under LIBRARY -> FROM THE HUBS:
  SAME AS <game>   the hub's key is kept as a retired key pointing at that game (replaced_by), which the apps already follow
  ADD AS NEW       the game is made after all
A machine whose hub names no key at all is left alone: its key is the app's to set (both apps now offer one from the title).
"""
import json
import os
import re
import threading
import time

from gatboxlib import library as L

from . import config, hubs, libsource, meter
from .live import LIVE

STATE = "hub-games.json"                 # in gatbox-web's state: what the owner decided, so a decision isn't asked again
EVERY = 60.0
_lock = threading.Lock()
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _norm(text):
    """A title or key as words that compare: lower case, "&" as "and", Roman numerals II-X as digits, no "the", no punctuation."""
    words = re.findall(r"[a-z0-9]+", str(text or "").lower().replace("&", " and ").replace("-", " "))
    return " ".join(ROMAN.get(w, w) for w in words if w != "the")


def _title(name):
    """A machine's name as a game title: a cabinet number on the end (" #2") is the machine's, not the game's."""
    return re.sub(r"\s*#\s*\d+\s*$", "", str(name or "")).strip()[:120] or None


def _state():
    try:
        with open(os.path.join(config.CTRL, STATE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    p = os.path.join(config.CTRL, STATE)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(p + ".tmp", p)


def _valid_key(k):
    try:
        return L.key_field(k) == k
    except L.Reject:
        return False


def unknown(v):
    """{key: {key, title, machines: [{slug, name, hub}], similar: [{key, title}]}}: hub keys naming no game in the library."""
    live = {k: v.games[k]["data"]["title"] for k in v.live()}
    out = {}
    for h in hubs.hubs():
        for m in h["machines"]:
            k = m.get("model_key")
            if not isinstance(k, str) or not _valid_key(k) or k in v.games:
                continue
            e = out.setdefault(k, {"key": k, "title": _title(m.get("name")) or k.replace("-", " ").title(), "machines": [],
                                   "similar": []})
            e["machines"].append({"slug": m["slug"], "name": m.get("name") or m["slug"], "hub": h["label"]})
    for k, e in out.items():
        n, nk = _norm(e["title"]), _norm(k)
        e["similar"] = [{"key": g, "title": t} for g, t in sorted(live.items(), key=lambda x: x[1].lower())
                        if (n and _norm(t) == n) or _norm(g) == nk]
    return out


def _make(v, e, survivor=None):
    hubs_ = sorted({m["hub"] for m in e["machines"]})
    data = {"title": e["title"], "kind": "video",
            "notes": f"added from {', '.join(hubs_)}'s machine list ({', '.join(m['slug'] for m in e['machines'])})"}
    if survivor:
        data["replaced_by"] = survivor
    res = v.lib.put("game", e["key"], data, 0, by="gatbox")
    if res["result"] == "rejected":
        raise meter.Bad(400, res.get("reason") or "the library won't take that")
    return res["result"] == "created"


def scan():
    """Make the games that are plainly new; -> the ones waiting for the owner (those that might be a game the library has)."""
    v = libsource.current()
    if v is None:
        return []
    decided = _state().get("decided") or {}
    waiting, made = [], []
    with _lock:
        for k, e in unknown(v).items():
            if e["similar"] and k not in decided:
                waiting.append(e)
                continue
            if _make(v, e, (decided.get(k) or {}).get("as") if (decided.get(k) or {}).get("as") != "new" else None):
                made.append(k)
    for k in made:
        LIVE.emit("library", {"key": k, "action": "added", "from": "hubs"})
    if made:
        libsource.refresh_export()
    return waiting


def waiting():
    """GET /api/library/hubgames: the hub keys waiting for the owner's SAME AS / ADD AS NEW."""
    return {"waiting": scan()}


def decide(key, body, local):
    """POST /api/library/hubgames/<key> {as: "new" | <a game's key>}: the owner's answer for one waiting key."""
    if not local:
        raise meter.Bad(403, "the library is edited on the Pi's own screen")
    v = libsource.current()
    if v is None:
        raise meter.Bad(409, "the game library isn't set up on this Pi yet")
    as_ = body.get("as") if isinstance(body, dict) else None
    e = unknown(v).get(key)
    if e is None:
        raise meter.Bad(404, f"no hub machine is waiting with the key {key!r}")
    if as_ != "new":
        if not isinstance(as_, str) or as_ not in v.live():
            raise meter.Bad(400, "as: \"new\", or the key of a game in the library")
    with _lock:
        d = _state()
        d.setdefault("decided", {})[key] = {"as": as_, "at": round(time.time(), 3)}
        _save(d)
        _make(v, e, None if as_ == "new" else as_)
    libsource.refresh_export()
    LIVE.emit("library", {"key": key, "action": "added" if as_ == "new" else "retired", "from": "hubs"})
    return {"key": key, "as": as_}


def watch(every=EVERY):
    """Look again whenever it might matter: every minute (gatbox-sync refreshes the hubs' lists every two)."""
    def loop():
        while True:
            try:
                scan()
            except (OSError, ValueError, meter.Bad):
                pass
            time.sleep(every)
    threading.Thread(target=loop, name="hub-games", daemon=True).start()
