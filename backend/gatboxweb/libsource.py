"""The deck's game library as gatbox-web reads and edits it (docs/plans/2026-11-03-phase-b-library.md, slice B3).

`current()` is the library's view once the deck is seeded, else None, and None means every reader does exactly what it did
before the library existed (hard rule 14). The readers (manuals, the spec sheet, the MAME chip list, `roster.library`) ask
it for today's data shapes (gatboxlib.libview) and keep their own logic.

CONFIRM and UNCONFIRM are writes to the library's rail limits. After any change gatbox-web rewrites `library-specs.json`
in its state directory: the confirmed limits as flat JSON. That file is all the logger ever reads
(gatboxlib.profiles.machine_specs), so gatbox-raillog, which runs as root, never opens SQLite and keeps working whatever
state the library is in (hard rule 3). A watcher notices changes made from outside (the owner's `gatbox-library seed` or
`import`) and refreshes the export within a few seconds.
"""
import json
import os
import sqlite3
import threading
import time

from gatboxlib import library as L
from gatboxlib import libview, profiles

from . import (  # meter.Bad at call time: meter imports roster, which imports this
    config,
    meter,
)

PATH = config.LIBRARY
_lock = threading.RLock()
_cache = {"sig": None, "view": None, "memo": {}}


def _sig():
    out = []
    for p in (PATH, PATH + "-wal"):
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out) if out[0] is not None else None


def current():
    """The library's view, rebuilt when its file changes, when the deck is seeded; else None."""
    sig = _sig()
    if sig is None:
        _cache.update(sig=None, view=None, memo={})
        return None
    with _lock:
        if _cache["sig"] == sig:
            return _cache["view"]
        try:
            lib = L.Library(PATH, create=False)
            view = libview.View(lib) if lib.meta("seeded_at") else None
        except (sqlite3.Error, OSError, ValueError):
            view = None
        _cache.update(sig=sig, view=view, memo={})
        return view


def _memo(name, make):
    v = current()
    if v is None:
        return None
    with _lock:
        if name not in _cache["memo"]:
            _cache["memo"][name] = make(v)
        return _cache["memo"][name]


def key_for(name):
    v = current()
    return v.key_for(name) if v else None


def library_slug(name):
    """The slug whose folder and list entry stand for this machine's game, or None when the library doesn't know it
    (then the caller applies today's rules)."""
    v = current()
    key = v.key_for(name) if v else None
    return v.primary(key) if key else None


def folders(slug):
    """Every folder under the manuals root that holds this game's files, the primary first: [slug] with no library."""
    v = current()
    key = v.key_for(slug) if v else None
    return v.folders(key) if key else [slug]


def manuals_list():
    return _memo("manuals", lambda v: v.manuals_list())


def confirmed_state():
    return _memo("confirmed", lambda v: v.confirmed_state())


def file_spec():
    return _memo("file_spec", lambda v: v.file_spec())


def mame_roms(file_data=None):
    """mame-roms.json's shape: the library's machines over the installed file's (a machine the library has nothing for
    keeps the file's entry), the file's MAME version when it has one."""
    lib = _memo("mame", lambda v: v.mame_roms())
    if lib is None:
        return file_data
    if not file_data:
        return lib
    return {"meta": file_data.get("meta") or lib["meta"],
            "machines": {**(file_data.get("machines") or {}), **lib["machines"]}}


# --- CONFIRM, through the library -------------------------------------------------------------------------------------

def confirm(name, rail, doc, page):
    v = current()
    key = v.key_for(name)
    rec = next((r for r in v.recs("rail_limit", key)
                if r["data"]["rail"] == rail and r["data"]["doc_id"] == doc and r["data"]["page"] == page), None)
    if rec is None:
        raise meter.Bad(400, "no such limit on this machine's spec sheet (rail, doc and page must match one)")
    d = rec["data"]
    if not float(d["lo"]) < float(d["hi"]):
        raise meter.Bad(400, "that entry has no usable limits")
    res = v.lib.put("rail_limit", rec["id"], dict(d, confirmed={"by": "owner", "at": round(time.time(), 3)}), rec["rev"],
                    by="gatbox")
    if res["result"] not in ("updated", "duplicate"):
        raise meter.Bad(409, "that limit changed while you looked at it: open the page again")
    refresh_export()
    return current().confirmed_state()[key][rail]


def unconfirm(name, rail):
    v = current()
    key = v.key_for(name)
    recs = [r for r in v.recs("rail_limit", key)
            if r["data"]["rail"] == rail and r["data"]["doc_id"] is not None and r["data"]["confirmed"]]
    if not recs:
        raise meter.Bad(404, f"{rail!r} isn't confirmed on {name}")
    for r in recs:
        res = v.lib.put("rail_limit", r["id"], dict(r["data"], confirmed=None), r["rev"], by="gatbox")
        if res["result"] not in ("updated", "duplicate"):
            raise meter.Bad(409, "that limit changed while you looked at it: open the page again")
    refresh_export()


# --- the logger's flat export -------------------------------------------------------------------------------------------

def refresh_export():
    """Write library-specs.json when the confirmed limits differ from what's there; remove it when the deck isn't seeded
    (the logger then reads the files, as before). True when it wrote or removed something."""
    path = os.path.join(config.CTRL, profiles.LIBRARY_SPECS)
    v = current()
    if v is None:
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
    machines = v.export_specs()
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("machines") == machines:
                return False
    except (OSError, ValueError, AttributeError):
        pass
    doc = {"made": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machines": machines,
           "about": "The game library's confirmed rail limits, as the logger reads them. Written by gatbox-web; don't edit."}
    os.makedirs(config.CTRL, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.chmod(path + ".tmp", 0o644)
    os.replace(path + ".tmp", path)
    return True


def watch(every=3.0):
    """Keep the export in step with the library, whoever changed it."""
    def loop():
        while True:
            try:
                refresh_export()
            except (OSError, sqlite3.Error, ValueError):
                pass                                     # the logger keeps whatever export it has, or the files
            time.sleep(every)
    threading.Thread(target=loop, name="library-export", daemon=True).start()
