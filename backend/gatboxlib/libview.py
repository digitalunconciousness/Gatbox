"""Today's data shapes, built from the library (docs/plans/2026-11-03-phase-b-library.md, slice B3).

Once the deck is seeded the library is the source, but the readers (manuals, the spec sheet, the MAME chip list, the
logger's windows) were written against the manuals list, the specs file, the CONFIRMed state and mame-roms.json. Each
function here returns one of those shapes from the library's records, so the readers change their data source and
nothing else. Every result is keyed by the game's `model_key` and by each roster slug the seed mapped to it, so a lookup
by either finds it.

No database is opened here: callers pass a `Library`. The logger never calls this. It reads the flat export that
`export_specs` makes, which gatbox-web writes.
"""
import json
import time

from . import library as L

WHY = "Why no manual yet: "
SPECS_FILE_LABEL = "specs file"


def slug_keys(lib):
    """{roster slug: model_key}, as the seed stored it."""
    try:
        m = json.loads(lib.meta("slug_keys", "{}"))
    except ValueError:
        return {}
    return m if isinstance(m, dict) else {}


def doc_id_for(raw):
    """The id the seed gave a document from its file's id or stem (a stem with characters an id can't hold is slugged)."""
    if isinstance(raw, str) and L.DOC_ID.match(raw):
        return raw
    return (L.slug(raw)[:80] or None) if isinstance(raw, str) else None


class View:
    """The library as of one moment: its records grouped by game, and the slug <-> key maps."""

    def __init__(self, lib):
        self.lib = lib
        self.sk = slug_keys(lib)
        self.slugs = {}
        for s, k in sorted(self.sk.items()):
            self.slugs.setdefault(k, []).append(s)
        self.games = {g["id"]: g for g in lib.all("game")}
        by = {}
        for kind in ("document", "rail_limit", "fact", "mame", "rom_list", "rom_image"):
            for r in lib.all(kind):
                by.setdefault(kind, {}).setdefault(r["data"]["model_key"], []).append(r)
        self.by = by

    def key_for(self, name):
        """The game a slug, a hub's model_key or a key itself stands for (retired keys followed), or None."""
        if not isinstance(name, str):
            return None
        key = self.sk.get(name) or (name if name in self.games else None)
        if key is None:
            return None
        g = self.lib.resolve(key)
        return g["id"] if g else None

    def primary(self, key):
        """The slug whose folder holds the game's files: the slug equal to the key, else the first one, else the key."""
        slugs = self.slugs.get(key, [])
        return key if key in slugs else (slugs[0] if slugs else key)

    def names(self, key):
        return [key] + [s for s in self.slugs.get(key, []) if s != key]

    def folders(self, key):
        """Every folder under the manuals root that holds this game's files, the primary one first."""
        first = self.primary(key)
        return [first] + [n for n in self.names(key) if n != first]

    def recs(self, kind, key):
        return self.by.get(kind, {}).get(key, [])

    def _expand(self, per_key):
        """{name: entry}: each live game's entry under its key and its slugs, and a retired game's slugs pointing at its
        survivor's entry (a mistaken duplicate was merged into it: its machines read the survivor)."""
        out = {}
        for key, entry in per_key.items():
            for n in self.names(key):
                out[n] = entry
        for key, g in self.games.items():
            if g["data"]["replaced_by"]:
                survivor = self.lib.resolve(key)
                if survivor and survivor["id"] in per_key:
                    for n in self.names(key):
                        out[n] = per_key[survivor["id"]]
        return out

    def _doc_title(self, key, doc_id):
        d = self.lib.get("document", f"{key}/{doc_id}") if doc_id else None
        return (d["data"]["title"] if d else None) or doc_id or ""

    # --- the manuals list's shape: {name: {docs, specs: {rails, sheet}, why, note}} ------------------------------------
    def manuals_list(self):
        per_key = {}
        for key, g in self.games.items():
            if g["data"]["replaced_by"]:
                continue
            notes = [x for x in g["data"]["notes"].split("\n") if x]
            why = " ".join(x[len(WHY):] for x in notes if x.startswith(WHY))
            note = "\n".join(x for x in notes if not x.startswith(WHY))
            docs = [{"id": d["data"]["doc_id"], "title": d["data"]["title"], "kind": d["data"]["kind"],
                     "url": d["data"]["source_url"], "source": d["data"]["source"], "sha256": d["data"]["sha256"]}
                    for d in sorted(self.recs("document", key), key=lambda r: r["id"])]
            rails = [{"rail": r["data"]["rail"], "lo": float(r["data"]["lo"]), "hi": float(r["data"]["hi"]),
                      "doc": r["data"]["doc_id"], "page": r["data"]["page"], "quote": r["data"]["quote"]}
                     for r in sorted(self.recs("rail_limit", key), key=lambda r: (r["data"]["rail"], r["data"]["page"] or 0))
                     if r["data"]["doc_id"] is not None]
            sheet = [{"what": f["data"]["what"], "value": f["data"]["value"], "doc": f["data"]["doc_id"],
                      "page": f["data"]["page"], "quote": f["data"]["quote"]}
                     for f in sorted(self.recs("fact", key), key=lambda r: r["data"]["what"])]
            per_key[key] = {"docs": docs, "specs": {"rails": rails, "sheet": sheet}, "why": why, "note": note}
        return self._expand(per_key)

    # --- machine-specs-confirmed.json's shape: {name: {rail: {lo, hi, doc, page, source, confirmed}}} -------------------
    def _confirmed(self, key):
        best = {}
        for r in self.recs("rail_limit", key):
            d = r["data"]
            if d["doc_id"] is None or not d["confirmed"]:
                continue
            if d["rail"] not in best or d["confirmed"]["at"] >= best[d["rail"]]["confirmed"]["at"]:
                best[d["rail"]] = d
        return {rail: {"lo": float(d["lo"]), "hi": float(d["hi"]), "doc": d["doc_id"], "page": d["page"],
                       "source": f"{self._doc_title(key, d['doc_id'])} p.{d['page']}" if d["page"] else self._doc_title(key, d["doc_id"]),
                       "confirmed": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(d["confirmed"]["at"]))}
                for rail, d in best.items()}

    def confirmed_state(self):
        per_key = {k: c for k in self.live() if (c := self._confirmed(k))}
        return self._expand(per_key)

    def live(self):
        return [k for k, g in self.games.items() if not g["data"]["replaced_by"]]

    # --- gatbox-machine-specs.json's shape: {name: {rails: {rail: [lo, hi]}, source}} ----------------------------------
    def _file_limits(self, key):
        rails, source = {}, ""
        for r in sorted(self.recs("rail_limit", key), key=lambda r: r["data"]["rail"]):
            d = r["data"]
            if d["doc_id"] is None and d["confirmed"]:
                rails[d["rail"]] = [float(d["lo"]), float(d["hi"])]
                source = source or d["quote"]
        return rails, source

    def file_spec(self):
        per_key = {}
        for key in self.live():
            rails, source = self._file_limits(key)
            if rails:
                per_key[key] = {"rails": rails, "source": source}
        return self._expand(per_key)

    # --- the logger's export (library-specs.json): machine_specs()'s own shape --------------------------------------------
    def export_specs(self):
        """Per name, the rail windows in force from the library: the specs-file limits, overridden by the latest
        CONFIRMed manual limit of each rail. Only confirmed limits are ever here (hard rule 12)."""
        per_key = {}
        for key in self.live():
            rails, source = self._file_limits(key)
            sources = {r: SPECS_FILE_LABEL for r in rails}
            for rail, c in self._confirmed(key).items():
                rails[rail] = [c["lo"], c["hi"]]
                sources[rail] = f"manual: {c['source']}".strip()
            if rails:
                entry = {"rails": rails, "sources": sources}
                if source:
                    entry["source"] = source
                per_key[key] = entry
        return self._expand(per_key)

    # --- mame-roms.json's shape: {meta, machines: {name: {set, status, why, prefer, versions}}} ----------------------------
    def mame_roms(self):
        per_key, mame_version = {}, None
        for key in self.live():
            m = (self.recs("mame", key) or [None])[0]
            rl = (self.recs("rom_list", key) or [None])[0]
            if m is None and rl is None:
                continue
            md = m["data"] if m else {"set": rl["data"]["set"], "prefer": None, "status": "sure", "why": ""}
            entry = {"set": md["set"], "status": md["status"] or "none", "why": md["why"], "prefer": md["prefer"] or md["set"]}
            if rl is not None and rl["data"]["versions"]:
                mame_version = mame_version or rl["data"]["mame"]
                entry["versions"] = [{"name": v["name"], "desc": v["description"], "year": v["year"], "mfr": v["mfr"],
                                      "parent": v["parent"],
                                      "roms": [{k: x for k, x in r.items() if x not in (None, "")} for r in v["roms"]],
                                      "disks": [{k: x for k, x in d.items() if x not in (None, "")} for d in v["disks"]]}
                                     for v in rl["data"]["versions"]]
            elif md["set"]:
                entry["versions"] = []
                entry["status"], entry["why"] = "missing", f"MAME {mame_version or ''} has no set {md['set']!r}".replace("  ", " ")
            per_key[key] = entry
        return {"meta": {"mame": mame_version, "source": "the game library"}, "machines": self._expand(per_key)}

