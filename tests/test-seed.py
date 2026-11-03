#!/usr/bin/env python3
"""The library's seed (backend/gatboxlib/seed.py, tools/gatbox-library) against a fake deck: a roster, a manuals list with
sidecars and a hand-dropped PDF, the specs file, a confirmed limit, MAME lists, and a dump archive, all invented
(Widget Wars). The plan is read-only and flags what looks off; an edited plan is what's applied, in one transaction;
running it again changes nothing; an import is an edit that never confirms or unconfirms a limit.
    python3 tests/test-seed.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
from gatboxlib import library as L
from gatboxlib import seed

TOOL = os.path.join(REPO, "tools/gatbox-library")
T = tempfile.mkdtemp(prefix="gatbox-seed-test-")
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def load(path):
    with open(path) as f:
        return json.load(f)


def write(path, data, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w") as f:
        f.write(data if binary else (data if isinstance(data, str) else json.dumps(data)))


def env(**extra):
    e = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=f"{T}/state", GATBOX_MANUALS=f"{T}/manuals",
             GATBOX_ROMS=f"{T}/roms", GATBOX_MAME_ROMS=f"{T}/mame-roms.json", GATBOX_LIBRARY=f"{T}/lib/library.sqlite")
    e.update(extra)
    return e


def run(*args, **extra):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env(**extra), timeout=120, check=False)
    return r.returncode, r.stdout + r.stderr


def build_deck():
    """The files the seed reads, as they'd be on the Pi."""
    write(f"{T}/data/gatbox-barcade-roster.json", {
        "meta": {}, "video_games": [
            {"slug": "widget-wars", "name": "Widget Wars", "mfr": "Widgetco", "platform": "x"},
            {"slug": "widget-wars-cocktail", "name": "Widget Wars Cocktail", "mfr": "Widgetco", "platform": "x"},
            {"slug": "gizmo-racer", "name": "Gizmo Racer", "mfr": "Gizmo Inc", "platform": "x"},
            {"slug": "ws-9", "name": "Sprocket & Cog", "mfr": "", "platform": "x"}],
        "pinball": [{"slug": "pin-widget-pinball", "name": "Widget Pinball", "mfr": "Widgetco", "platform": "y"}], "retired": []})
    pdf = b"%PDF-1.4 widget wars manual bytes"
    write(f"{T}/manuals/widget-wars/widget-wars-manual.pdf", pdf, True)
    write(f"{T}/manuals/widget-wars/widget-wars-manual.json", {
        "id": "widget-wars-manual", "title": "Widget Wars Operations Manual", "kind": "manual", "url": "https://archive.example/ww.pdf",
        "source": "an archive", "sha256": hashlib.sha256(pdf).hexdigest(), "pages": 3, "added": "fetch"})
    write(f"{T}/manuals/widget-wars/My Notes (1).pdf", b"%PDF-1.4 hand dropped", True)         # no sidecar, a name an id can't have
    write(f"{T}/data/gatbox-manuals.json", {"machines": {
        "widget-wars": {"docs": [{"id": "widget-wars-manual", "title": "Widget Wars Operations Manual", "kind": "manual",
                                  "url": "https://archive.example/ww.pdf", "source": "an archive"},
                                 {"id": "widget-wars-schematics", "title": "Schematics", "kind": "schematics",
                                  "url": "http://insecure.example/s.pdf"}],
                        "why": "the schematics are only on a site that doesn't serve plain PDFs",
                        "specs": {"rails": [{"rail": "+5V", "lo": 4.75, "hi": 5.25, "doc": "widget-wars-manual", "page": 2,
                                             "quote": "+5 VDC 4.75 to 5.25 V"},
                                            {"rail": "+12V", "lo": 11.4, "hi": 12.6, "doc": "widget-wars-manual", "page": 2,
                                             "quote": "+12 VDC"},
                                            {"rail": "nonsense", "lo": 1, "hi": 2, "doc": "x", "page": 1, "quote": ""}],
                                  "sheet": [{"what": "Fuse F1", "value": "5 A slow-blow", "doc": "widget-wars-manual", "page": 2,
                                             "quote": "FUSE F1 5A SLO-BLO"},
                                            {"what": "Monitor", "value": "19 inch raster", "doc": None, "page": None, "quote": ""}]}},
        "widget-wars-cocktail": {"docs": [], "note": "Cocktail table too"},
        "ghost-machine": {"docs": [], "why": "not on the roster"}}})
    write(f"{T}/data/gatbox-machine-specs.json", {"meta": {"updated": "2026-09-26"}, "machines": {
        "gizmo-racer": {"rails": {"+5V": [4.9, 5.1]}, "source": "Gizmo Racer manual, DC limits (per the owner)"}}})
    write(f"{T}/state/machine-specs-confirmed.json", {"widget-wars": {
        "+5V": {"lo": 4.75, "hi": 5.25, "doc": "widget-wars-manual", "page": 2, "source": "Widget Wars Operations Manual p.2",
                "confirmed": "2026-10-01T12:00:00+0000"},
        "+12V": {"lo": 11.4, "hi": 12.5, "doc": "widget-wars-manual", "page": 2, "confirmed": "2026-10-01T12:00:00+0000"},
        "+3.3V": {"lo": 3.2, "hi": 3.4, "doc": "gone", "page": 1, "confirmed": "2026-10-01T12:00:00+0000"}}})
    write(f"{T}/data/gatbox-mame-sets.json", {"machines": {
        "widget-wars": {"set": "widgetw", "status": "sure"}, "gizmo-racer": {"set": None, "status": "none", "why": "PC-based"}}})
    rom = {"name": "ww1.u10", "size": 524288, "crc": "deadbeef", "sha1": "d" * 40, "region": "user1"}
    write(f"{T}/mame-roms.json", {"meta": {"mame": "0.276 (mame0276)"}, "machines": {
        "widget-wars": {"set": "widgetw", "status": "sure", "why": "", "prefer": "widgetw", "versions": [
            {"name": "widgetw", "desc": "Widget Wars (version 1.2)", "year": "1983", "mfr": "Widgetco", "parent": True,
             "roms": [rom], "disks": [{"name": "widgetw", "sha1": "e" * 40, "region": "ide:0:hdd"}]}]},
        "gizmo-racer": {"set": None, "status": "none", "why": "PC-based"}}})
    img = os.urandom(2048)
    sha1, crc = hashlib.sha1(img).hexdigest(), f"{zlib.crc32(img) & 0xffffffff:08x}"
    write(f"{T}/roms/widget-wars/WW-U10_{sha1[:8]}.bin", img, True)
    write(f"{T}/roms/widget-wars/WW-U10_{sha1[:8]}.json", {
        "label": "WW-U10", "part": "27C040@DIP32", "machine": "widget-wars", "size": 2048, "sha1": sha1, "crc32": crc,
        "romident": {"match": True, "matches": [{"set": "widgetw", "rom": "ww1.u10", "description": "x"}]}})
    write(f"{T}/roms/unknown-board/NOBIN_aaaaaaaa.json", {"label": "NOBIN", "sha1": "a" * 40, "crc32": "0" * 8, "size": 1})
    write(f"{T}/roms/_images/loose-image.bin", b"loose image bytes", True)
    return img


def main():
    img = build_deck()

    print("the plan (read-only):")
    plan_path = f"{T}/plan.json"
    code, out = run("seed", "--plan", "--out", plan_path)
    plan = load(plan_path) if code == 0 else {}
    check("seed --plan writes a plan to the path it's given, readable only by its owner",
          code == 0 and os.path.isfile(plan_path) and (os.stat(plan_path).st_mode & 0o077) == 0)
    check("every roster machine has a proposed key: a slug that is a title becomes the key as it is",
          {s: g["key"] for s, g in plan["games"].items()} == {
              "widget-wars": "widget-wars", "widget-wars-cocktail": "widget-wars-cocktail", "gizmo-racer": "gizmo-racer",
              "ws-9": "ws-9", "pin-widget-pinball": "pin-widget-pinball"})
    check("a pinball is a pinball, a title with an ampersand is kept whole",
          plan["games"]["pin-widget-pinball"]["kind"] == "pinball" and plan["games"]["ws-9"]["title"] == "Sprocket & Cog")
    flags = "\n".join(plan["flags"])
    check("it flags two slugs that look like one game (a cocktail and an upright)",
          "widget-wars and widget-wars-cocktail" in flags)
    check("it flags the manuals list's machine that isn't on the roster, and a document id it had to change",
          "ghost-machine" in flags and "My Notes (1)" in flags and "'my-notes-1'" in flags)
    check("... a source address that isn't https, a confirmed limit nothing on the list matches, a rail it can't use",
          "isn't https" in flags and "+3.3V" in flags)
    check("... a dump sidecar with no .bin", "NOBIN_aaaaaaaa.bin" in flags)
    check("the counts say what would be written",
          plan["counts"] == {"game": 5, "document": 3, "rail_limit": 3, "fact": 2, "mame": 2, "rom_list": 1, "rom_image": 2})
    check("the plan wrote nothing: no library exists yet", not os.path.exists(f"{T}/lib"))

    print("applying it:")
    edited = json.loads(json.dumps(plan))
    edited["games"]["widget-wars-cocktail"]["key"] = "widget-wars"          # the owner: one game, two cabinets
    edited["games"]["ws-9"]["skip"] = True
    write(f"{T}/plan-edited.json", edited)
    code, out = run("seed", "--apply", f"{T}/plan-edited.json")
    lib = L.Library(f"{T}/lib/library.sqlite", create=False)
    check("seed --apply: created everything in one go, and says what it noted", code == 0 and "created" in out and "note:" in out)
    check("two slugs given one key are one game; a skipped one is left out",
          lib.get("game", "widget-wars") and not lib.get("game", "widget-wars-cocktail") and not lib.get("game", "ws-9")
          and len(lib.all("game")) == 3)
    g = lib.get("game", "widget-wars")["data"]
    check("a game's notes carry the manuals list's note and why, from every slug that became it",
          "Cocktail table too" in g["notes"] and "Why no manual yet: the schematics" in g["notes"]
          and g["mfr"] == "Widgetco" and g["kind"] == "video")
    docs = {d["data"]["doc_id"]: d["data"] for d in lib.all("document", "widget-wars")}
    check("documents: the fetched one with its hash and pages, the hand-dropped one as `yours` with a clean id, "
          "the unfetched one listed with no hash and an http address dropped",
          set(docs) == {"widget-wars-manual", "my-notes-1", "widget-wars-schematics"}
          and docs["widget-wars-manual"]["sha256"] == hashlib.sha256(b"%PDF-1.4 widget wars manual bytes").hexdigest()
          and docs["widget-wars-manual"]["pages"] == 3 and docs["widget-wars-manual"]["bytes"] > 0
          and docs["my-notes-1"]["kind"] == "yours" and docs["my-notes-1"]["sha256"]
          and docs["widget-wars-schematics"]["sha256"] is None and docs["widget-wars-schematics"]["source_url"] is None)
    limits = {(r["data"]["model_key"], r["data"]["rail"]): r["data"] for r in lib.all("rail_limit")}
    ww5, ww12, gz5 = limits[("widget-wars", "+5V")], limits[("widget-wars", "+12V")], limits[("gizmo-racer", "+5V")]
    check("rail limits: volts as normalized strings with the manual's page and words", ww5["lo"] == "4.75" and ww5["hi"] == "5.25"
          and ww5["doc_id"] == "widget-wars-manual" and ww5["page"] == 2 and ww5["quote"] == "+5 VDC 4.75 to 5.25 V")
    check("... confirmed where the owner confirmed that page against those numbers, and only there (a +12V that was "
          "confirmed at another number isn't)",
          ww5["confirmed"] and ww5["confirmed"]["by"] == "owner" and ww12["confirmed"] is None)
    check("... the specs file's limit is confirmed (cited per the owner), with its source line as the quote",
          gz5["confirmed"]["by"] == "specs file" and gz5["doc_id"] is None and "per the owner" in gz5["quote"])
    facts = {f["data"]["what"]: f["data"] for f in lib.all("fact")}
    check("facts from the spec page, grouped", facts["Fuse F1"]["group"] == "fuse" and facts["Monitor"]["group"] == "monitor"
          and facts["Fuse F1"]["page"] == 2)
    mame = lib.get("mame", "widget-wars")["data"]
    check("MAME: the set and its status; a game MAME doesn't have has no status",
          mame["set"] == "widgetw" and mame["status"] == "sure" and lib.get("mame", "gizmo-racer")["data"]["status"] is None)
    rl = lib.get("rom_list", "widget-wars")["data"]
    check("the ROM list: MAME's version, each version's year, maker, ROMs and disks",
          rl["mame"] == "0.276 (mame0276)" and rl["versions"][0]["year"] == "1983" and rl["versions"][0]["roms"][0]["name"] == "ww1.u10"
          and rl["versions"][0]["disks"][0]["sha1"] == "e" * 40 and not lib.get("rom_list", "gizmo-racer"))
    images = {i["data"]["label"]: i for i in lib.all("rom_image")}
    check("dumps: each image by its SHA-256, with MAME's answer and its game; a loose _images/ file too",
          images["WW-U10"]["id"] == hashlib.sha256(img).hexdigest() and images["WW-U10"]["data"]["model_key"] == "widget-wars"
          and images["WW-U10"]["data"]["romident"]["match"] and images["loose-image"]["data"]["model_key"] is None
          and images["loose-image"]["data"]["size"] == len(b"loose image bytes"))
    check("the slug -> key map is kept for imports, with the seed's time",
          json.loads(lib.meta("slug_keys"))["widget-wars-cocktail"] == "widget-wars" and lib.meta("seeded_at"))
    before = lib.digest()
    code, out = run("seed", "--apply", f"{T}/plan-edited.json")
    check("applying it again changes nothing", code == 0 and "0 created" in out and lib.digest() == before)

    print("an edit after the seed survives a re-seed:")
    r = lib.get("game", "widget-wars")
    lib.put("game", "widget-wars", dict(r["data"], notes="Her own note"), r["rev"])
    run("seed", "--apply", f"{T}/plan-edited.json")
    check("the library is the source: a re-seed never overwrites an edit", lib.get("game", "widget-wars")["data"]["notes"] == "Her own note")
    lib.put("document", "widget-wars/my-notes-1", None, lib.get("document", "widget-wars/my-notes-1")["rev"], deleted=True)
    run("seed", "--apply", f"{T}/plan-edited.json")
    check("... and a document she deleted stays deleted", lib.get("document", "widget-wars/my-notes-1") is None)

    print("a plan that can't be applied writes nothing:")
    bad = json.loads(json.dumps(edited))
    bad["games"]["gizmo-racer"]["key"] = "Gizmo Racer!"
    write(f"{T}/plan-bad.json", bad)
    fresh = f"{T}/lib2/library.sqlite"
    code, out = run("seed", "--apply", f"{T}/plan-bad.json", GATBOX_LIBRARY=fresh)
    check("a key that isn't one: refused with its slug, and no library file is written",
          code == 2 and "gizmo-racer" in out and "nothing was written" in out and not os.path.exists(fresh))
    direct = L.Library(f"{T}/lib4/library.sqlite")
    res = seed.apply_plan(direct, {"data": f"{T}/data", "state": f"{T}/state", "manuals": f"{T}/manuals", "roms": f"{T}/roms",
                                   "mame_roms": f"{T}/mame-roms.json", "library": ""}, bad)
    check("... and seed.apply_plan refuses it too when called directly, not only the command",
          res["rejected"] and res["created"] == 0 and direct.digest()["count"] == 0)
    broken = f"{T}/lib3/library.sqlite"          # the plan step never creates one
    write(f"{T}/data2/gatbox-barcade-roster.json", {"video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x"}]})
    write(f"{T}/data2/gatbox-manuals.json", {"machines": {"widget-wars": {"docs": [], "specs": {"rails": [
        {"rail": "+5V", "lo": 6, "hi": 5, "doc": "d", "page": 1, "quote": "x" * 600}]}}}})
    code, out = run("seed", "--plan", "--out", f"{T}/plan-b.json", GATBOX_DATA=f"{T}/data2", GATBOX_LIBRARY=broken)
    check("a record the store would refuse (a limit whose lo is above its hi) is dropped at plan time and named, "
          "not found at apply time", code == 0 and "left out: rail_limit" in out and "lo is above hi" in out)

    print("an import is an edit:")
    mm = load(f"{T}/data/gatbox-manuals.json")
    m = mm["machines"]["widget-wars"]["specs"]["rails"]
    m[0]["hi"] = 5.5                                                                      # the +5V upper limit changes
    m.append({"rail": "-5V", "lo": -5.25, "hi": -4.75, "doc": "widget-wars-manual", "page": 3, "quote": "-5 VDC"})
    write(f"{T}/new-manuals.json", mm)
    def lim(rail):
        return next(r for r in lib.all("rail_limit") if r["data"]["model_key"] == "widget-wars" and r["data"]["rail"] == rail)
    before_conf = lim("+5V")["data"]["confirmed"]
    code, out = run("import", "--kind", "manuals", f"{T}/new-manuals.json")
    check("import --kind manuals: a changed number is an update; it clears the confirmation, because the numbers changed",
          code == 0 and lim("+5V")["data"]["hi"] == "5.5" and before_conf and lim("+5V")["data"]["confirmed"] is None)
    check("... a new limit arrives unconfirmed; the others are as they were",
          lim("-5V")["data"]["confirmed"] is None and lim("-5V")["data"]["lo"] == "-5.25" and lim("+12V")["data"]["hi"] == "12.6")
    check("... a limit confirmed from the specs file stays confirmed: an import never unconfirms one it didn't change",
          next(r for r in lib.all("rail_limit") if r["data"]["model_key"] == "gizmo-racer")["data"]["confirmed"] is not None)
    check("... the game's note she edited isn't touched by a manuals import", lib.get("game", "widget-wars")["data"]["notes"] == "Her own note")
    d1 = lib.digest()
    code, out = run("import", "--kind", "manuals", f"{T}/new-manuals.json")
    check("the same import again: nothing changes", code == 0 and "0 created, 0 updated" in out and lib.digest() == d1)
    code, out = run("import", "--kind", "mame-sets", f"{T}/data/gatbox-mame-sets.json")
    check("import --kind mame-sets: the same list again changes nothing", code == 0 and "0 updated" in out and lib.digest() == d1)
    write(f"{T}/ghost.json", {"machines": {"never-seeded": {"set": "x", "status": "sure"}}})
    code, out = run("import", "--kind", "mame-sets", f"{T}/ghost.json")
    check("a machine the seed never mapped is skipped and named", code == 0 and "never-seeded" in out)
    code, out = run("import", "--kind", "nope", f"{T}/ghost.json")
    check("an unknown kind or a file that isn't a list: refused", code != 0)
    write(f"{T}/notalist.json", [1, 2])
    code, out = run("import", "--kind", "manuals", f"{T}/notalist.json")
    check("a file that isn't a list file: refused, nothing written", code == 2 and "not a list file" in out and lib.digest() == d1)

    print("status:")
    code, out = run("status")
    check("status: counts by kind, the size and the seed's time", code == 0 and "game" in out and "seeded 20" in out)
    code, out = run("status", GATBOX_LIBRARY=f"{T}/nowhere/library.sqlite")
    check("no library yet: it says how to make one", code != 0 and "seed --plan" in out)

    print(f"\nseed: {passed} passed, {failed} failed")


try:
    main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
