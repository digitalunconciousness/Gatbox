"""Roster entries for the dashboard's MACHINE panel. The roster is data of record shared with the owner's
maintenance app: read only, never reshaped on disk. Entries are merged here, in the response only, with:
  * their platform (meta.platforms[entry.platform]): desc, faults, parts, pm
  * the critical actions that name them. Those lists name games by display name ("Addams Family (WPC)",
    "Gauntlet"), not by slug, so a string names an entry when the entry's name appears in it as whole words; when
    several names fit, only the longest counts ("Gauntlet Legends" names Gauntlet Legends, not Gauntlet). When no
    name fits, a string of two words or more (minus any "(…)" note) that appears in a name names that entry.
  * their machine spec (gatbox-machine-specs.json), and their risk flags spelled out (meta.risk_legend)

Machines added on the dashboard (add / edit below) live in the state dir's roster-added.json, in the roster's own
entry format, and gatboxlib.profiles.roster() merges them in, so everything that takes a roster slug takes them too.
The installed roster file stays as installed; GET /roster.json hands back the file plus the additions for the
maintenance app to import. The file wins a slug both have, so once the app's roster carries a machine, it's the file's.
"""
import json
import os
import re
import threading
import unicodedata

from gatboxlib import profiles

from . import config, meter      # meter.Bad at call time: meter imports this module too

KINDS = profiles.FLOOR                                 # ("video_games", "pinball")
FIELDS = ("slug", "name", "platform", "mfr", "risk", "faults", "parts", "notes")   # a roster entry, in the file's order
NOT_SURE = "confirm"      # platform + risk flag for "not sure yet" (how the roster already lists an unconfirmed board)
RESERVED = {"unassigned"}                              # the captures / dumps name for "no machine"
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_lock = threading.Lock()


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


def added_slugs():
    """The machines on the floor because they were added here (the roster file doesn't have them)."""
    f = profiles.roster_file() or {}
    in_file = {e.get("slug") for k in KINDS + ("retired",) for e in f.get(k, []) if isinstance(e, dict)}
    return {e["slug"] for es in profiles.roster_added().values() for e in es} - in_file


def platforms():
    """[{key, desc}] from meta.platforms, by description (what the ADD MACHINE picker lists)."""
    p = ((profiles.roster_file() or {}).get("meta") or {}).get("platforms") or {}
    return sorted(({"key": k, "desc": (v or {}).get("desc") or k} for k, v in p.items()), key=lambda x: x["desc"].lower())


def listing():
    """GET /api/roster."""
    es = entries()
    if es is None:
        return None
    specs, mine = profiles.machine_specs(), added_slugs()
    return [{"slug": e["slug"], "name": e.get("name"), "kind": k, "platform": e.get("platform"), "mfr": e.get("mfr"),
             "risk": e.get("risk", []), "spec": e["slug"] in specs, "added": e["slug"] in mine} for k, e in es]


# --- machines added on the Pi ---------------------------------------------------------------------
def slugify(nm, kind):
    """The ID a new machine gets: lower-case words joined by hyphens, apostrophes dropped, "&" read as "and", accents
    folded to ASCII; pinball IDs start with "pin-" (the roster's convention). "Satan's Hollow" -> satans-hollow."""
    s = unicodedata.normalize("NFKD", nm).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`]", "", s.replace("&", " and "))
    s = "-".join(re.findall(r"[a-z0-9]+", s))
    s = (("pin-" + s) if kind == "pinball" and s else s)[:60].rstrip("-")
    return s


def _text(body, key, limit, required):
    v = body.get(key, "")
    if not isinstance(v, str):
        raise meter.Bad(400, f"{key}: text")
    v = " ".join(v.split())                               # squeeze runs of spaces
    if any(unicodedata.category(c) == "Cc" for c in v) or "\t" in body.get(key, ""):
        raise meter.Bad(400, f"{key}: no tabs or control characters")
    if required and not v:
        raise meter.Bad(400, f"{key}: required")
    if len(v) > limit:
        raise meter.Bad(400, f"{key}: at most {limit} characters")
    return v


def _platform(body, default=None):
    p = body.get("platform", default)
    if p != NOT_SURE and p not in {x["key"] for x in platforms()}:
        raise meter.Bad(400, f"platform: one of the roster's platforms, or {NOT_SURE!r} (not sure yet)")
    return p


def _clash(nm, slug, skip=None):
    """Why a name / ID can't be used (it's on the roster already, retired included), or None."""
    r = profiles.roster() or {}
    for k in KINDS + ("retired",):
        for e in r.get(k, []):
            if not isinstance(e, dict) or e.get("slug") == skip:
                continue
            if e.get("slug") == slug or (nm and _norm(e.get("name", "")) == _norm(nm)):
                where = " (retired in the roster file: bring it back there)" if k == "retired" else ""
                return f"already on the roster as {e.get('name')} ({e.get('slug')}){where}"
    return None


def _read_added():
    """roster-added.json for a change: {} when there's none yet; a damaged file stops the change (never overwritten)."""
    p = os.path.join(config.CTRL, profiles.ADDED_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return p, {}
    except (OSError, ValueError) as e:
        raise meter.Bad(503, f"{profiles.ADDED_FILE} can't be read ({e}): fix or remove it on the Pi first")
    if not isinstance(d, dict):
        raise meter.Bad(503, f"{profiles.ADDED_FILE} isn't a JSON object: fix or remove it on the Pi first")
    return p, d


def _write_added(p, d):
    d = {"about": "Machines added on the GATBOX dashboard, in the roster's entry format. Merged into the roster by "
                  "gatbox-web; GET /roster.json exports the roster with them.", **{k: d.get(k, []) for k in KINDS}}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def add(body):
    """POST /api/roster {name, mfr, kind, platform, notes, dry_run}: a new machine (201), or with dry_run the entry it
    would be (200, nothing saved). Returns (entry, kind, saved)."""
    if not isinstance(body, dict):
        raise meter.Bad(400, "expected a JSON object")
    nm = _text(body, "name", 60, True)
    mfr = _text(body, "mfr", 40, True)
    notes = _text(body, "notes", 200, False)
    kind = body.get("kind")
    if kind not in KINDS:
        raise meter.Bad(400, f"kind: one of {', '.join(KINDS)}")
    plat = _platform(body)
    slug = slugify(nm, kind)
    if not SLUG.match(slug):
        raise meter.Bad(400, "name: needs letters or digits (the machine's ID is made from it)")
    if slug in RESERVED:
        raise meter.Bad(409, f"{slug!r} is reserved: pick another name")
    entry = dict(zip(FIELDS, (slug, nm, plat, mfr, [NOT_SURE] if plat == NOT_SURE else [], [], [], notes)))
    with _lock:
        why = _clash(nm, slug)
        if why:
            raise meter.Bad(409, why)
        if body.get("dry_run"):
            return entry, kind, False
        p, d = _read_added()
        d.setdefault(kind, []).append(entry)
        _write_added(p, d)
    return entry, kind, True


def edit(slug, body):
    """PUT /api/roster/<slug> {name?, mfr?, platform?, notes?}: a machine added here (the ID and kind never change;
    the roster file's machines are changed in the file)."""
    if not isinstance(body, dict) or not body:
        raise meter.Bad(400, "expected a JSON object with name, mfr, platform or notes")
    extra = set(body) - {"name", "mfr", "platform", "notes"}
    if extra:
        raise meter.Bad(400, f"can't change {', '.join(sorted(extra))} (the ID and the kind are permanent)")
    with _lock:
        if slug not in added_slugs():
            if slug in (slugs() or set()):
                raise meter.Bad(409, f"{slug} comes from the roster file: change it there (the maintenance app)")
            raise meter.Bad(404, f"not in the roster: {slug!r}")
        p, d = _read_added()
        kind, e = next((k, e) for k in KINDS for e in d.get(k, []) if isinstance(e, dict) and e.get("slug") == slug)
        new = dict(e)
        if "name" in body:
            new["name"] = _text(body, "name", 60, True)
            why = _clash(new["name"], None, skip=slug)
            if why:
                raise meter.Bad(409, why)
        if "mfr" in body:
            new["mfr"] = _text(body, "mfr", 40, True)
        if "notes" in body:
            new["notes"] = _text(body, "notes", 200, False)
        if "platform" in body:
            new["platform"] = _platform(body)
            risk = [f for f in new.get("risk", []) if f != NOT_SURE]
            new["risk"] = risk + [NOT_SURE] if new["platform"] == NOT_SURE else risk
        d[kind] = [new if x is e else x for x in d[kind]]
        _write_added(p, d)
    return new, kind


def export():
    """GET /roster.json: the roster as the maintenance app imports it (the file, plus what was added here), in the
    file's own layout (2-space indent, ASCII escapes), or None when there's no roster at all."""
    r = profiles.roster()
    return None if r is None else json.dumps(r, indent=2) + "\n"


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
        return {**e, "kind": k, "added": slug in added_slugs(),
                "platform_info": {"key": e.get("platform"), **plat} if plat else None,
                "faults_all": list(e.get("faults", [])) + [f for f in plat.get("faults", []) if f not in e.get("faults", [])],
                "parts_all": list(e.get("parts", [])) + [p for p in plat.get("parts", []) if p not in e.get("parts", [])],
                "risk_info": [{"flag": f, "meaning": legend.get(f)} for f in e.get("risk", [])],
                "critical_actions": critical_actions(e, meta, all_names),
                "spec": spec}
    return None
