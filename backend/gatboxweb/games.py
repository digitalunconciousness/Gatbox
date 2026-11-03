"""The game library's games, for the LIBRARY view (docs/plans/2026-11-03-phase-b-library.md, slice B4).

GET /api/library lists every game with what the library holds for it (documents fetched or not, limits and how many are
confirmed, facts, MAME set, the roster machines that are this game) and whether it's missing a manual, with the reason the
seed kept. A game's documents, spec sheet and ROM chips are read through the same endpoints as ever (/api/manuals,
/api/specs, /api/mame), which accept a game's key as well as a machine's slug. Editing is here: ADD GAME and EDIT.

A game's key is its title as a slug and is permanent, so the view shows the proposed key before a game is saved. Edits are
the Pi's own screen only (decision L5: no library edits from a phone on the deck's Wi-Fi). Every write is compare-and-set:
an edit names the revision it started from, and a game changed meanwhile is a 409, never a silent overwrite.
"""
from gatboxlib import library as L
from gatboxlib import libview

from . import libsource, meter, roster
from .live import LIVE


def _view():
    v = libsource.current()
    if v is None:
        raise meter.Bad(409, "the game library isn't set up on this Pi yet (gatbox-library seed --plan)")
    return v


def _machine_names():
    return {e["slug"]: e.get("name") or e["slug"] for _, e in (roster.entries() or [])}


def _summary(v, key, names):
    g = v.games[key]
    d = g["data"]
    docs, limits = v.recs("document", key), v.recs("rail_limit", key)
    fetched = [x for x in docs if x["data"]["sha256"]]
    lines = [x for x in d["notes"].split("\n") if x]
    mame = (v.recs("mame", key) or [None])[0]
    return {"key": key, "rev": g["rev"], "title": d["title"], "mfr": d["mfr"], "year": d["year"], "kind": d["kind"],
            "machines": [{"slug": s, "name": names.get(s, s)} for s in v.slugs.get(key, [])], "primary": v.primary(key),
            "docs": len(docs), "fetched": len(fetched), "limits": len(limits),
            "confirmed": sum(1 for x in limits if x["data"]["confirmed"]), "facts": len(v.recs("fact", key)),
            "mame": mame["data"]["set"] if mame else None, "missing": not fetched,
            "why": " ".join(x[len(libview.WHY):] for x in lines if x.startswith(libview.WHY)),
            "notes": d["notes"]}


def status():
    """GET /api/library: {seeded: false}, or the games (live ones, by title) with a count of those missing a manual."""
    v = libsource.current()
    if v is None:
        return {"seeded": False}
    names = _machine_names()
    games = sorted((_summary(v, k, names) for k in v.live()), key=lambda g: g["title"].lower())
    return {"seeded": True, "games": games, "count": len(games), "missing": sum(1 for g in games if g["missing"]),
            "bytes": v.lib.size()}


def detail(key):
    v = _view()
    if key not in v.games:
        raise meter.Bad(404, f"no game {key!r} in the library")
    return _summary(v, v.lib.resolve(key)["id"], _machine_names())


def propose_key(title, kind, mfr, year):
    """GET /api/library/key: the key a new game would get. It's permanent, so the view shows it before saving."""
    v = _view()
    try:
        return {"key": L.model_key(title, kind if kind in ("video", "pinball", "other") else "video", mfr or None,
                                   int(year) if str(year or "").isdigit() else None, taken=set(v.games))}
    except ValueError as e:
        raise meter.Bad(400, str(e)) from None


def _fields(body):
    if not isinstance(body, dict):
        raise meter.Bad(400, "expected a JSON object")
    return body


def _refused(res):
    if res["result"] == "rejected":
        raise meter.Bad(400, res.get("reason") or "the library won't take that")
    if res["result"] == "conflict":
        raise meter.Bad(409, "that game changed while you were editing it: open it again")


def add(body, local):
    """POST /api/library/games {title, mfr?, year?, kind, notes?, key?}: a new game."""
    if not local:
        raise meter.Bad(403, "the library is edited on the Pi's own screen")
    body = _fields(body)
    v = _view()
    key = body.get("key") or propose_key(body.get("title") or "", body.get("kind") or "video", body.get("mfr"),
                                         body.get("year"))["key"]
    if key in v.games:
        raise meter.Bad(409, f"there is already a game with the key {key!r}")
    data = {k: body[k] for k in ("title", "mfr", "year", "kind", "notes") if k in body}
    res = v.lib.put("game", key, data, 0, by="gatbox")
    _refused(res)
    LIVE.emit("library", {"key": key, "action": "added"})
    return detail(key)


def edit(key, body, local):
    """PUT /api/library/games/<key> {rev, title?, mfr?, year?, notes?}: change a game's fields. The key never changes."""
    if not local:
        raise meter.Bad(403, "the library is edited on the Pi's own screen")
    body = _fields(body)
    v = _view()
    cur = v.lib.get("game", key)
    if cur is None:
        raise meter.Bad(404, f"no game {key!r} in the library")
    if body.get("rev") != cur["rev"]:
        raise meter.Bad(409, "that game changed while you were editing it: open it again")
    data = dict(cur["data"], **{k: body[k] for k in ("title", "mfr", "year", "notes") if k in body})
    _refused(v.lib.put("game", key, data, cur["rev"], by="gatbox"))
    LIVE.emit("library", {"key": key, "action": "edited"})
    return detail(key)
