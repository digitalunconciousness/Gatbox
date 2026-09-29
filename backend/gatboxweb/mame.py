"""Each machine's ROM chips, from MAME (GET /api/mame/<slug>): every chip of the machine's MAME set, in every
version MAME knows (the parent and its clones: revisions, regions), each ticked when a dump in the archive has its
SHA-1, wherever it was archived. The checklist itself is built at install by gatbox-mame-roms from the Pi's own
`mame -listxml` and the machine -> set list (gatbox-mame-sets.json): hashes only, no ROM data.

The version shown first: the one the archive's dumps match best, else the list's "prefer", else the parent.
"""
import glob
import json
import os

from gatboxlib import profiles

from . import config, roster
from .meter import Bad


def _archive():
    """{sha1: [{machine, label, file, date}]}: the dump archive's sidecars (a few dozen files: read per request)."""
    idx = {}
    for side in glob.glob(os.path.join(config.ROMS, "*", "*.json")):
        try:
            with open(side, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(j, dict) and j.get("sha1"):
            idx.setdefault(j["sha1"], []).append({"machine": os.path.basename(os.path.dirname(side)), "label": j.get("label"),
                                                  "file": os.path.basename(side)[:-5] + ".bin", "date": j.get("date")})
    return idx


def checklist(slug):
    if slug not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {slug!r}")
    try:
        data = profiles._load(config.MAME_ROMS)
    except (OSError, ValueError):
        data = None
    if not data:
        raise Bad(503, "no MAME checklist on this Pi yet (the bootstrap builds it from gatbox-mame-sets.json)")
    meta = data.get("meta", {})
    m = data.get("machines", {}).get(slug)
    if m is None:
        return {"slug": slug, "set": None, "status": "unmapped", "why": "not on the MAME list yet (gatbox-mame-sets.json)",
                "mame": meta.get("mame"), "versions": [], "default": None}
    idx = _archive()
    versions = []
    for v in m.get("versions", []):
        roms = [dict(r, dumped=idx.get(r.get("sha1"), [])) for r in v.get("roms", [])]
        versions.append(dict(v, roms=roms, dumped=sum(1 for r in roms if r["dumped"])))
    prefer = m.get("prefer") or m.get("set")
    best = max(versions, key=lambda v: (v["dumped"], v["name"] == prefer, v["parent"]), default=None)
    return {"slug": slug, "set": m.get("set"), "status": m.get("status"), "why": m.get("why", ""),
            "mame": meta.get("mame"), "versions": versions, "default": best["name"] if best else None}
