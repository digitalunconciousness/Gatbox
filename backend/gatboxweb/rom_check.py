"""CHECK: one dump, four comparisons, and the owner's known-good / known-bad marks
(docs/plans/2026-11-03-phase-b-library.md, slice B6; design: integration plan §4.4, "hashes on the spot").

    against                         source                                  answers
    MAME                            the dump's own `mame -romident` result  which set and ROM this is, or NO MATCH
    the game's expected chips       the library's rom_list for its game     right chip / another revision / not this game
    her images                      the library's rom_image marks           known good / known bad / seen before / never seen
    this machine's earlier dumps    the dump archive's sidecars             same as last time / changed

A comparison reads the archive and the library and sends nothing anywhere. Each check carries a `level` (ok, warn, bad or
mut) and words: a state is never only a colour. A mark is a write to the library's rom_image record for those bytes (made
from the dump when it's marked, or earlier by `libsource.adopt_dumps`), the Pi's own screen only.
"""
import glob
import hashlib
import json
import os
import re

from . import config, libsource, meter
from .live import LIVE

IMAGE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)/([A-Za-z0-9][A-Za-z0-9._-]{0,150})\.bin$")
VERDICTS = ("good", "bad", "unknown")


def _sidecar(path):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else None
    except (OSError, ValueError):
        return None


def _locate(image):
    """(machine folder, file stem, its sidecar, the .bin's path) for an archive-relative name, or a 400 / 404."""
    m = IMAGE.match(image) if isinstance(image, str) else None
    if not m:
        raise meter.Bad(400, "image: <machine>/<label>_<sha1:8>.bin, as the dump archive names it")
    folder, stem = m.group(1), m.group(2)
    binf = os.path.join(config.ROMS, folder, stem + ".bin")
    side = _sidecar(binf[:-4] + ".json")
    if side is None or not os.path.isfile(binf):
        raise meter.Bad(404, f"no such dump: {image!r}")
    return folder, stem, side, binf


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _archive():
    """Every archived dump: [{machine, file, label, sha1, epoch, date, side}], from the sidecars."""
    out = []
    for side in glob.glob(os.path.join(config.ROMS, "*", "*.json")):
        folder = os.path.basename(os.path.dirname(side))
        j = _sidecar(side)
        if folder == "_images" or not j or not j.get("sha1"):
            continue
        out.append({"machine": folder, "file": os.path.basename(side)[:-5] + ".bin", "label": j.get("label") or "",
                    "sha1": j["sha1"], "epoch": j.get("epoch") or 0, "date": (j.get("date") or "")[:16].replace("T", " ")})
    return out


WORDS = {"match": "MATCH", "no_match": "NO MATCH", "unknown": "NOT IDENTIFIED", "right_chip": "RIGHT CHIP",
         "other_revision": "ANOTHER REVISION", "not_this_game": "NOT THIS GAME", "unlisted": "NOT LISTED", "no_list": "NO CHIP LIST",
         "no_game": "NO GAME", "no_library": "NO LIBRARY", "known_good": "KNOWN GOOD", "known_bad": "KNOWN BAD",
         "seen_before": "SEEN BEFORE", "never_seen": "NEVER SEEN", "same": "SAME AS LAST TIME", "changed": "CHANGED", "first": "FIRST DUMP"}


def _chk(against, verdict, level, detail, **extra):
    """One comparison's result: the verdict, its word, how it should be drawn, and the explanation. `text` is the word and
    the explanation in one line, for anything that shows it without a badge."""
    return dict({"against": against, "verdict": verdict, "word": WORDS[verdict], "level": level, "detail": detail,
                 "text": f"{WORDS[verdict]}: {detail}"}, **extra)


def _mame(side):
    ri = side.get("romident") or {}
    if ri.get("match") is True:
        ms = ri.get("matches") or []
        first = "; ".join(f"{m.get('set')} / {m.get('rom')}" + (f" ({m['description']})" if m.get("description") else "") for m in ms[:3])
        return _chk("mame", "match", "ok", first or "known to MAME", matches=ms)
    if ri.get("match") is False:
        return _chk("mame", "no_match", "warn", "MAME doesn't know these bytes: a revision it lacks, a changed chip or a bad read.")
    return _chk("mame", "unknown", "mut", "MAME couldn't run when this was dumped.")


def _game(v, machine, sha1, mame_check):
    key = v.key_for(machine) if v else None
    if v is None:
        return _chk("game", "no_library", "mut", "the game library isn't set up, so there's no list of this game's chips to compare with.")
    if not key:
        return _chk("game", "no_game", "warn", f"{machine} has no game in the library: LIBRARY, ALL GAMES or + ADD GAME links one.")
    title = v.games[key]["data"]["title"]
    rl = (v.recs("rom_list", key) or [None])[0]
    if rl is None or not rl["data"]["versions"]:
        return _chk("game", "no_list", "warn", f"the library has no chip list for {title} (MAME doesn't have it, or the list isn't built).", key=key)
    mame = (v.recs("mame", key) or [None])[0]
    versions = rl["data"]["versions"]
    prefer = (mame["data"]["prefer"] or mame["data"]["set"]) if mame else None
    first = next((x for x in versions if x["name"] == prefer), next((x for x in versions if x["parent"]), versions[0]))
    hits = [(x, r) for x in versions for r in x["roms"] if (r.get("sha1") or "").lower() == sha1.lower()]
    if hits:
        here = next(((x, r) for x, r in hits if x["name"] == first["name"]), None)
        if here:
            return _chk("game", "right_chip", "ok", f"{here[1]['name']} of {here[0]['description'] or here[0]['name']}", key=key)
        x, r = hits[0]
        return _chk("game", "other_revision", "warn", f"{r['name']} of {x['description'] or x['name']}, not "
                    f"{first['description'] or first['name']} (the version MAME lists first for {title}).", key=key)
    sets = {m.get("set") for m in (mame_check.get("matches") or [])}
    if sets and not sets & {x["name"] for x in versions}:
        return _chk("game", "not_this_game", "bad", f"MAME knows these bytes as {', '.join(sorted(s for s in sets if s))}, "
                    f"which isn't a version of {title}.", key=key)
    n = sum(len(x["roms"]) for x in versions)
    return _chk("game", "unlisted", "warn", f"not one of the {n} chips MAME lists for {title}'s versions: a revision it lacks, "
                f"a changed chip or a bad read.", key=key)


def _images(v, sha256, sha1, machine, file):
    rec = v.lib.get("rom_image", sha256) if v else None
    d = rec["data"] if rec else {}
    if d.get("verdict") == "good":
        return _chk("images", "known_good", "ok", d["note"] if d.get("note") else "marked by you")
    if d.get("verdict") == "bad":
        return _chk("images", "known_bad", "bad", d["note"] if d.get("note") else "marked by you")
    others = [a for a in _archive() if a["sha1"] == sha1 and (a["machine"], a["file"]) != (machine, file)]
    if others:
        o = min(others, key=lambda a: a["epoch"])
        return _chk("images", "seen_before", "warn", f"the same bytes were dumped as {o['label']} from {o['machine']} on {o['date']}. "
                    "Not marked good or bad yet.")
    return _chk("images", "never_seen", "mut", "the first time these bytes have been read, and not marked good or bad yet.")


def _history(machine, file, label, sha1, epoch):
    earlier = sorted((a for a in _archive() if a["machine"] == machine and a["label"].lower() == label.lower()
                      and a["file"] != file and a["epoch"] < epoch), key=lambda a: a["epoch"])
    if not earlier:
        return _chk("machine", "first", "mut", f"the first time {label} has been dumped from this machine.")
    last = earlier[-1]
    if last["sha1"] == sha1:
        return _chk("machine", "same", "ok", f"the same bytes as the dump of {last['date']}: the chip hasn't changed.")
    return _chk("machine", "changed", "warn", f"since {last['date']} it was {last['sha1'][:8]}, and it's {sha1[:8]} now. "
                "Expected after a burn; otherwise a failing chip or a bad read.")


def check(image):
    """GET /api/library/check?image=<machine>/<file>.bin: the four comparisons for one archived dump, and its mark."""
    folder, stem, side, binf = _locate(image)
    sha1, label = side.get("sha1") or "", side.get("label") or stem
    sha256 = _sha256(binf)
    v = libsource.current()
    file = stem + ".bin"
    mame = _mame(side)
    rec = v.lib.get("rom_image", sha256) if v else None
    d = rec["data"] if rec else {}
    return {"image": image, "machine": folder, "label": label, "part": side.get("part"), "size": side.get("size") or os.path.getsize(binf),
            "sha1": sha1, "sha256": sha256, "date": (side.get("date") or "")[:16].replace("T", " "),
            "checks": [mame, _game(v, folder, sha1, mame), _images(v, sha256, sha1, folder, file),
                       _history(folder, file, label, sha1, side.get("epoch") or 0)],
            "mark": {"verdict": d.get("verdict", "unknown"), "note": d.get("note", ""), "chip": d.get("chip", ""),
                     "revision": d.get("revision", ""), "recorded": rec is not None},
            "library": v is not None}


def mark(sha256, body, local):
    """POST /api/library/images/<sha256>/mark {verdict, note?, chip?, revision?}: known good, known bad or unmarked."""
    if not local:
        raise meter.Bad(403, "the library is edited on the Pi's own screen")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise meter.Bad(400, "sha256: 64 lower-case hex digits")
    if not isinstance(body, dict) or body.get("verdict") not in VERDICTS:
        raise meter.Bad(400, "verdict: good, bad or unknown")
    v = libsource.current()
    if v is None:
        raise meter.Bad(409, "the game library isn't set up on this Pi yet (gatbox-library seed --plan)")
    rec = v.lib.get("rom_image", sha256)
    if rec is None:
        libsource.adopt_dumps(force=True)                   # a dump the watcher hasn't reached yet
        v = libsource.current()
        rec = v.lib.get("rom_image", sha256)
        if rec is None:
            raise meter.Bad(404, "that image isn't in the library: it comes from a dump in the archive")
    data = dict(rec["data"], verdict=body["verdict"])
    for k in ("note", "chip", "revision"):
        if k in body:
            data[k] = body[k]
    res = v.lib.put("rom_image", sha256, data, rec["rev"], by="gatbox")
    if res["result"] == "rejected":
        raise meter.Bad(400, res.get("reason") or "the library won't take that")
    if res["result"] == "conflict":
        raise meter.Bad(409, "that image changed while you looked at it: open it again")
    LIVE.emit("library", {"image": sha256, "action": "marked"})
    return {"sha256": sha256, "verdict": data["verdict"], "note": data["note"], "chip": data["chip"], "revision": data["revision"]}


def images(key):
    """GET /api/library/games/<key>/images: the game's ROM images, with their marks."""
    v = libsource.current()
    if v is None:
        raise meter.Bad(409, "the game library isn't set up on this Pi yet (gatbox-library seed --plan)")
    if key not in v.games:
        raise meter.Bad(404, f"no game {key!r} in the library")
    files = {}
    for a in _archive():
        files.setdefault(a["sha1"], f"{a['machine']}/{a['file']}")
    out = [{"sha256": r["id"], "label": r["data"]["label"], "part": r["data"]["part"], "size": r["data"]["size"],
            "sha1": r["data"]["sha1"], "verdict": r["data"]["verdict"], "note": r["data"]["note"], "chip": r["data"]["chip"],
            "revision": r["data"]["revision"], "match": (r["data"]["romident"] or {}).get("match"),
            "image": files.get(r["data"]["sha1"])}
           for r in v.recs("rom_image", key)]
    return {"key": key, "images": sorted(out, key=lambda i: (i["label"] or "").lower())}
