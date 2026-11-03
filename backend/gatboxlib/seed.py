"""The library's first fill, and imports (design §7, docs/plans/2026-11-03-phase-b-library.md).

Today's knowledge sits in the roster, the manuals list and its sidecars, the specs files, the MAME lists and the dump
archive. `make_plan` reads them (read-only) and proposes a `model_key` for every game; the owner edits the plan; `apply_plan`
writes the records in one transaction and never touches a record that's already there, so running it again changes
nothing. After the seed the library is the source: the git-ignored list files are only read by `import_file`, and every
record an import changes is a local edit. Nothing here reads a PDF's text or sends anything anywhere.

What stays out: `machine-actuals.json` (a value measured on one board) and anything about a cabinet. No record names a
machine, a customer or a site.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import time
import zlib
from decimal import Decimal, InvalidOperation

from . import library as L
from . import profiles

RAIL = re.compile(r"^[+-]\d{1,2}(\.\d)?V$")                  # "+5V", "-5V", "+12V", "+3.3V": the same rule as manuals.py
LISTS = {"manuals": "gatbox-manuals.json", "mame-sets": "gatbox-mame-sets.json", "specs": "gatbox-machine-specs.json"}


def sources():
    """Where today's files are (each overridable, as the services do)."""
    srv = os.environ.get("GATBOX_SRV", "/srv/gatbox")
    return {"data": profiles.data_dir(), "state": profiles.state_dir(),
            "manuals": os.environ.get("GATBOX_MANUALS", os.path.join(srv, "manuals")),
            "roms": os.environ.get("GATBOX_ROMS", os.path.join(srv, "roms")),
            "mame_roms": os.environ.get("GATBOX_MAME_ROMS", "/usr/local/share/gatbox/mame-roms.json"),
            "library": os.environ.get("GATBOX_LIBRARY", os.path.join(srv, "library", "library.sqlite"))}


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pages(path):
    try:
        r = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=30, check=False)
        m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.MULTILINE)
        return int(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _volts(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise L.Reject("volts must be a number")
    try:
        return L.volts(format(Decimal(str(x)), "f"))
    except (InvalidOperation, L.Reject):
        raise L.Reject(f"{x!r} isn't a usable number of volts") from None


def _epoch(text):
    """A date or timestamp as seconds, 0.0 when it isn't one. Never 'now': a record built twice from the same files must
    come out the same, or an import would see a change in every record."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = datetime.datetime.strptime(str(text), fmt)  # noqa: DTZ007
            return (when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)).timestamp()
        except ValueError:
            continue
    return 0.0


def _id(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def fact_group(what):
    w = what.lower()
    if "fuse" in w:
        return "fuse"
    if any(x in w for x in ("monitor", "video", "crt")):
        return "monitor"
    if "psu" in w or "power supply" in w:
        return "psu"
    if any(x in w for x in ("power", "watt", "amp", "voltage", "line")):
        return "power"
    return "other"


# --- the plan -----------------------------------------------------------------------------------------------------------

def make_plan(src):
    """Read-only. {"games": {slug: {key, title, mfr, kind}}, "flags": [...], "counts": {...}}: one entry per roster
    machine, its key proposed (a barcade slug that is a game title simply becomes the key)."""
    roster = profiles.roster(src["data"], src["state"]) or {}
    games, flags, taken = {}, [], set()
    for k in profiles.FLOOR:
        for e in roster.get(k, []):
            slug = e["slug"]
            kind = "pinball" if k == "pinball" else "video"
            title = " ".join(str(e.get("name") or slug).split())[:120]
            mfr = " ".join(str(e.get("mfr") or "").split())[:120]
            key = slug
            try:
                L.key_field(slug)
            except L.Reject:
                try:
                    key = L.model_key(title, kind, mfr, taken=taken)
                    flags.append(f"{slug}: not a usable key as it is, so it gets {key!r}: check it")
                except ValueError as err:
                    flags.append(f"{slug}: no key can be made ({err}): left out until you give it one")
                    key = None
            games[slug] = {"key": key, "title": title, "mfr": mfr, "kind": kind}
            if key:
                taken.add(key)
    slugs = sorted(games)
    for a in slugs:
        for b in slugs:
            if a != b and b.startswith(a + "-"):
                flags.append(f"{a} and {b}: one may be a version or a sequel of the other. Give both the same key to make "
                             f"them one game")
    seen = {}
    for slug, g in sorted(games.items()):
        seen.setdefault(L.slug(g["title"]), []).append(slug)
    for t, ss in seen.items():
        if len(ss) > 1:
            flags.append(f"{', '.join(ss)} share the title {t!r}: if they're one game, give them the same key")
    plan = {"about": "Edit a game's key to rename it, or give two slugs the same key to make them one game; set "
                     "\"skip\": true to leave one out. Keys never change once applied.",
            "made": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "games": games, "flags": flags}
    records, more, _ = build(src, plan)
    plan["flags"] += more
    plan["counts"] = _counts(records)
    return plan


def _counts(records):
    out = {}
    for kind, _, _ in records:
        out[kind] = out.get(kind, 0) + 1
    return out


# --- the records --------------------------------------------------------------------------------------------------------

def build(src, plan, only=None, lists=None):
    """(records, flags, key_of): every library record today's files give, for the games in the plan. `only` limits it to
    some kinds (an import); `lists` replaces a list file's contents by name (the import's FILE)."""
    lists = lists or {}
    games = plan.get("games", {})
    key_of = {slug: g["key"] for slug, g in games.items() if g.get("key") and not g.get("skip")}
    records, flags = [], []

    def want(*kinds):
        return only is None or any(k in only for k in kinds)

    def listed(name):
        d = lists.get(name) if name in lists else _json(os.path.join(src["data"], LISTS[name]))
        m = (d or {}).get("machines")
        return (m if isinstance(m, dict) else {}), ((d or {}).get("meta") or {})

    (manuals, _), (sets, _), (specs, specs_meta) = listed("manuals"), listed("mame-sets"), listed("specs")
    specs_at = _epoch(specs_meta.get("updated"))
    state = profiles.SPECS_CONFIRMED
    confirmed = _json(os.path.join(src["state"], state)) or {}

    if want("game"):
        by_key = {}
        for slug, g in sorted(games.items()):
            if slug in key_of:
                by_key.setdefault(g["key"], []).append((slug, g))
        for key, members in by_key.items():
            slug, g = next((m for m in members if m[0] == key), members[0])
            notes = []
            for s, _ in members:
                e = manuals.get(s) or {}
                if e.get("note"):
                    notes.append(str(e["note"]))
                if e.get("why"):
                    notes.append("Why no manual yet: " + str(e["why"]))
            records.append(("game", key, {"title": g["title"], "mfr": g["mfr"], "kind": g["kind"],
                                          "notes": "\n".join(dict.fromkeys(notes))[:2000]}))
    known = {k for k in key_of.values()}
    for slug in sorted(manuals):
        if slug not in key_of and want("document", "rail_limit", "fact"):
            flags.append(f"manuals list: {slug} isn't a roster machine in the plan: its documents, limits and facts are left out")
    for slug, key in sorted(key_of.items()):
        e = manuals.get(slug) or {}
        if want("document"):
            records += _documents(src, slug, key, e, flags)
        if want("rail_limit"):
            records += _limits(slug, key, e, confirmed.get(slug) or {}, specs.get(slug), specs_at, flags)
        if want("fact"):
            for f in (e.get("specs") or {}).get("sheet", []):
                if isinstance(f, dict) and f.get("what"):
                    what = " ".join(str(f["what"]).split())[:60]
                    records.append(("fact", _id("fact", key, what), {
                        "model_key": key, "group": fact_group(what), "what": what, "value": str(f.get("value", ""))[:120],
                        "doc_id": f.get("doc") if isinstance(f.get("doc"), str) else None,
                        "page": f.get("page") if isinstance(f.get("page"), int) else None,
                        "quote": str(f.get("quote", ""))[:500]}))
        if want("mame"):
            m = sets.get(slug)
            if isinstance(m, dict):
                status = m.get("status") if m.get("status") in ("sure", "check") else None
                records.append(("mame", key, {"model_key": key, "set": m.get("set"), "prefer": m.get("prefer") or m.get("set"),
                                              "status": status, "why": str(m.get("why", ""))[:500]}))
    if want("rom_list"):
        data = _json(src["mame_roms"]) or {}
        for slug, m in sorted((data.get("machines") or {}).items()):
            if slug in key_of and m.get("versions"):
                records.append(("rom_list", key_of[slug], {
                    "model_key": key_of[slug], "mame": (data.get("meta") or {}).get("mame") or "", "set": m.get("set"),
                    "versions": [{"name": v.get("name"), "parent": v.get("parent"), "description": v.get("desc", ""),
                                  "year": v.get("year", ""), "mfr": v.get("mfr", ""), "roms": v.get("roms", []),
                                  "disks": v.get("disks", [])} for v in m["versions"]]}))
    if want("rom_image"):
        records += _images(src, key_of, known, flags)
    valid, dropped = [], []
    for kind, rid, data in records:
        try:
            checked = L.validate(kind, data)
            L.check_id(kind, rid, checked)
            valid.append((kind, rid, checked))
        except L.Reject as err:
            dropped.append(f"left out: {kind} {rid[:40]}: {err}")
    return valid, flags + dropped, key_of


def _documents(src, slug, key, entry, flags):
    folder = os.path.join(src["manuals"], slug)
    listed = {d.get("id"): d for d in entry.get("docs", []) if isinstance(d, dict) and d.get("id")}
    out, done = [], set()
    files = sorted(n for n in (os.listdir(folder) if os.path.isdir(folder) else [])
                   if n.lower().endswith(".pdf") and not n.startswith("."))

    def doc_id(raw, sha):
        if L.DOC_ID.match(raw):
            return raw
        fixed = L.slug(raw)[:80] or "doc-" + sha[:8]
        flags.append(f"{slug}: document {raw!r} gets the id {fixed!r} (its file name has characters an id can't hold)")
        return fixed

    def https(url, did):
        if url and not str(url).startswith("https://"):
            flags.append(f"{slug}: document {did!r} has a source address that isn't https: it's left blank")
            return None
        return url or None

    for name in files:                                  # a PDF in the folder: its sidecar and the list's entry add to it
        pdf = os.path.join(folder, name)
        side = _json(pdf[:-4] + ".json") or {}
        raw = side.get("id") or name[:-4]
        li = listed.get(raw, {})
        sha = side.get("sha256") or _sha256(pdf)
        did = doc_id(raw, sha)
        if did in done:
            continue
        done.add(did)
        kind = li.get("kind") or side.get("kind") or "yours"
        out.append(("document", f"{key}/{did}", {
            "model_key": key, "doc_id": did, "title": (li.get("title") or side.get("title") or raw)[:120],
            "kind": kind if kind in L.DOC_KINDS else "other", "source_url": https(li.get("url") or side.get("url"), did),
            "source": str(li.get("source") or side.get("source") or "")[:200], "sha256": sha,
            "pages": side.get("pages") or _pages(pdf), "bytes": os.path.getsize(pdf)}))
    for raw, li in sorted(listed.items()):             # on the list, not fetched yet: no file, so no hash of its own
        did = doc_id(raw, "0" * 8)
        if did in done:
            continue
        out.append(("document", f"{key}/{did}", {
            "model_key": key, "doc_id": did, "title": (li.get("title") or raw)[:120],
            "kind": li.get("kind") if li.get("kind") in L.DOC_KINDS else "manual", "source_url": https(li.get("url"), did),
            "source": str(li.get("source") or "")[:200], "sha256": li.get("sha256") or None, "pages": None, "bytes": None}))
    return out


def _limits(slug, key, entry, conf, specs_entry, specs_at, flags):
    out = []
    manual_rails = set()
    for r in (entry.get("specs") or {}).get("rails", []):
        if not isinstance(r, dict) or not RAIL.match(str(r.get("rail", ""))):
            continue
        try:
            lo, hi = _volts(r.get("lo")), _volts(r.get("hi"))
        except L.Reject as err:
            flags.append(f"{slug}: rail {r.get('rail')}: {err}")
            continue
        doc = r.get("doc") if isinstance(r.get("doc"), str) else None
        page = r.get("page") if isinstance(r.get("page"), int) else None
        c = conf.get(r["rail"]) or {}
        same = bool(c) and c.get("doc") == doc and c.get("page") == page and [c.get("lo"), c.get("hi")] == [r.get("lo"), r.get("hi")]
        manual_rails.add(r["rail"])
        out.append(("rail_limit", _id("rail_limit", key, r["rail"], doc, page), {
            "model_key": key, "rail": r["rail"], "lo": lo, "hi": hi, "doc_id": doc, "page": page,
            "quote": str(r.get("quote", ""))[:500],
            "confirmed": {"by": "owner", "at": _epoch(c.get("confirmed"))} if same else None}))
    for rail, c in conf.items():
        if rail not in manual_rails:
            flags.append(f"{slug}: a confirmed {rail} limit has no entry on the manuals list any more: it isn't carried over")
    if isinstance(specs_entry, dict):
        for rail, w in (specs_entry.get("rails") or {}).items():
            try:
                lo, hi = _volts(w[0]), _volts(w[1])
            except (L.Reject, TypeError, IndexError) as err:
                flags.append(f"{slug}: specs file rail {rail}: {err}")
                continue
            out.append(("rail_limit", _id("rail_limit", key, rail, None, None), {
                "model_key": key, "rail": str(rail), "lo": lo, "hi": hi, "doc_id": None, "page": None,
                "quote": str(specs_entry.get("source", ""))[:500],
                "confirmed": {"by": "specs file", "at": specs_at}}))
    return out


def _images(src, key_of, known, flags):
    out, seen = [], set()
    pairs = []
    for side in sorted(glob.glob(os.path.join(src["roms"], "*", "*.json"))):
        folder = os.path.basename(os.path.dirname(side))
        if folder != "_images":
            pairs.append((folder, side[:-5] + ".bin", _json(side) or {}))
    for folder, binf, j in pairs:
        if not os.path.isfile(binf):
            flags.append(f"{os.path.basename(binf)}: its sidecar has no .bin beside it: left out")
            continue
        sha = _sha256(binf)
        if sha in seen:
            continue
        seen.add(sha)
        key = key_of.get(folder)
        out.append(("rom_image", sha, {"size": j.get("size") or os.path.getsize(binf), "crc32": j.get("crc32"),
                                       "sha1": j.get("sha1"), "label": str(j.get("label", ""))[:120],
                                       "part": str(j.get("part", ""))[:60], "romident": j.get("romident"),
                                       "model_key": key if key in known else None}))
    for binf in sorted(glob.glob(os.path.join(src["roms"], "_images", "*.bin"))):
        sha = _sha256(binf)
        if sha in seen:
            continue
        seen.add(sha)
        with open(binf, "rb") as f:
            raw = f.read()
        out.append(("rom_image", sha, {"size": len(raw), "crc32": f"{zlib.crc32(raw) & 0xffffffff:08x}",
                                       "sha1": hashlib.sha1(raw).hexdigest(), "label": os.path.basename(binf)[:-4][:120]}))
    return out


# --- apply and import ---------------------------------------------------------------------------------------------------

def write_plan(plan, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def check_plan(plan):
    """Problems in an edited plan, before anything is written."""
    if not isinstance(plan, dict) or not isinstance(plan.get("games"), dict):
        return ["not a plan: it has no games"]
    problems = []
    for slug, g in plan["games"].items():
        if g.get("skip"):
            continue
        try:
            L.key_field(g.get("key"))
        except L.Reject as err:
            problems.append(f"{slug}: key {g.get('key')!r}: {err}")
        if not g.get("title"):
            problems.append(f"{slug}: no title")
        if g.get("kind") not in ("video", "pinball", "other"):
            problems.append(f"{slug}: kind {g.get('kind')!r}")
    return problems


def apply_plan(lib, src, plan):
    """One transaction; a record already there is kept, so a second run changes nothing. The slug -> key map is kept in
    the library for imports. -> {"created", "kept", "rejected", "flags"} (nothing is written if anything is rejected)."""
    problems = check_plan(plan)
    if problems:
        return {"created": 0, "updated": 0, "kept": 0, "rejected": [{"kind": "plan", "id": "", "reason": p} for p in problems],
                "flags": []}
    records, flags, key_of = build(src, plan)
    res = lib.bulk(records, by="seed", create_only=True)
    res["flags"] = flags
    if not res["rejected"]:
        lib.set_meta("slug_keys", json.dumps(key_of, sort_keys=True))
        lib.set_meta("seeded_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return res


def import_file(lib, src, kind, path):
    """Re-read one list file (a manuals list, a MAME sets list or a specs file prepared off the deck) and apply it as
    edits to what's in the library. A machine the seed didn't map is skipped and named."""
    if kind not in LISTS:
        return {"created": 0, "updated": 0, "kept": 0, "rejected": [{"kind": "import", "id": "", "reason": f"kind: one of {', '.join(LISTS)}"}],
                "flags": []}
    data = _json(path)
    if not isinstance(data, dict) or not isinstance(data.get("machines"), dict):
        return {"created": 0, "updated": 0, "kept": 0, "rejected": [{"kind": "import", "id": "", "reason": "not a list file (no machines)"}],
                "flags": []}
    slug_keys = json.loads(lib.meta("slug_keys", "{}"))
    games = {s: {"key": k, "title": k, "mfr": "", "kind": "video"} for s, k in slug_keys.items()}
    kinds = {"manuals": ("document", "rail_limit", "fact"), "mame-sets": ("mame",), "specs": ("rail_limit",)}[kind]
    records, flags, _ = build(src, {"games": games}, only=kinds, lists={kind: data})
    if kind == "specs":
        records = [r for r in records if r[2].get("doc_id") is None and r[2].get("page") is None]
    for slug in sorted(data["machines"]):
        if slug not in slug_keys:
            flags.append(f"{slug}: the seed didn't map it to a game: skipped")
    for kind_, rid, d in records:                      # an import never confirms or unconfirms: the store clears it itself
        if kind_ == "rail_limit":                      # when the numbers or the page change, and a new limit starts unconfirmed
            cur = lib.get("rail_limit", rid)
            d["confirmed"] = cur["data"]["confirmed"] if cur else None
    res = lib.bulk(records, by="import")
    res["flags"] = flags
    return res
