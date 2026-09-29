"""Roster entries for the dashboard's MACHINE panel. The roster is data of record shared with the owner's
maintenance app: read only, never reshaped on disk. Entries are merged here, in the response only, with:
  * their platform (meta.platforms[entry.platform]): desc, faults, parts, pm
  * the critical actions that name them. Those lists name games by display name ("Addams Family (WPC)",
    "Gauntlet"), not by slug, so a string names an entry when the entry's name appears in it as whole words; when
    several names fit, only the longest counts ("Gauntlet Legends" names Gauntlet Legends, not Gauntlet). When no
    name fits, a string of two words or more (minus any "(…)" note) that appears in a name names that entry.
  * their machine spec (gatbox-machine-specs.json), and their risk flags spelled out (meta.risk_legend)
"""
import re

from gatboxlib import profiles

KINDS = ("video_games", "pinball")


def _norm(s):
    return " " + " ".join(re.sub(r"[^0-9a-z]+", " ", s.lower()).split()) + " "


def entries():
    """[(kind, entry)] for the floor (not 'retired'), or None when no roster is installed."""
    r = profiles.roster()
    if r is None:
        return None
    return [(k, e) for k in KINDS for e in r.get(k, []) if isinstance(e, dict) and e.get("slug")]


def slugs():
    es = entries()
    return None if es is None else {e["slug"] for _, e in es}


def name(slug):
    for _, e in entries() or []:
        if e["slug"] == slug:
            return e.get("name")
    return None


def listing():
    """GET /api/roster."""
    es = entries()
    if es is None:
        return None
    specs = profiles.machine_specs()
    return [{"slug": e["slug"], "name": e.get("name"), "kind": k, "platform": e.get("platform"), "mfr": e.get("mfr"),
             "risk": e.get("risk", []), "spec": e["slug"] in specs} for k, e in es]


def _strings(v, path):
    """(path, text, the nearest enclosing dict) for each string in a list, anywhere under v (not 'sources')."""
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "sources":
                continue
            if isinstance(x, list) and any(isinstance(i, str) for i in x):
                for i in x:
                    if isinstance(i, str):
                        yield path + [k], i, v
            else:
                yield from _strings(x, path + [k])
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x, path)


def _names_in(text, names):
    """The roster names a critical-action string names (see the module docstring)."""
    t = _norm(text)
    hits = {n for n in names if len(n) > 3 and n in t}
    if hits:                                   # names inside the string: the longest ones
        return {n for n in hits if not any(n != o and n in o for o in hits)}
    bare = _norm(re.sub(r"\([^)]*\)", " ", text))
    if len(bare.split()) < 2:                  # "TMNT" alone isn't enough to pick "TMNT Turtles in Time"
        return set()
    return {n for n in names if bare in n}     # "Addams Family (WPC)" -> "The Addams Family"


def critical_actions(entry, meta, all_names):
    """The critical-action strings that name this entry, each with its action's note and the details beside it
    (the scalar fields and sources of the group the string sits in)."""
    me = _norm(entry.get("name", ""))
    out = []
    for key, action in (meta.get("critical_actions") or {}).items():
        items = ([([key], s, None) for s in action if isinstance(s, str)] if isinstance(action, list)
                 else list(_strings(action, [key])))
        for path, text, holder in items:
            if me not in _names_in(text, all_names):
                continue
            detail = {k: v for k, v in (holder or {}).items()
                      if (isinstance(v, (str, int, float)) or k == "sources") and not (holder is action and k == "note")}
            out.append({"action": key, "path": "/".join(path), "text": text,
                        "note": action.get("note") if isinstance(action, dict) else None, "detail": detail or None})
    return out


def entry(slug):
    """GET /api/roster/{slug}: None if unknown (or no roster)."""
    r = profiles.roster()
    if r is None:
        return None
    meta = r.get("meta", {})
    all_names = {_norm(e.get("name", "")) for k in KINDS + ("retired",) for e in r.get(k, []) if isinstance(e, dict)}
    for k, e in entries():
        if e["slug"] != slug:
            continue
        plat = (meta.get("platforms") or {}).get(e.get("platform")) or {}
        legend = meta.get("risk_legend") or {}
        spec = profiles.machine_specs().get(slug)
        return {**e, "kind": k,
                "platform_info": {"key": e.get("platform"), **plat} if plat else None,
                "faults_all": list(e.get("faults", [])) + [f for f in plat.get("faults", []) if f not in e.get("faults", [])],
                "parts_all": list(e.get("parts", [])) + [p for p in plat.get("parts", []) if p not in e.get("parts", [])],
                "risk_info": [{"flag": f, "meaning": legend.get(f)} for f in e.get("risk", [])],
                "critical_actions": critical_actions(e, meta, all_names),
                "spec": spec}
    return None
