#!/usr/bin/env python3
"""The game library's store (backend/gatboxlib/library.py), docs/contract/library-v1: the format's vectors, keys that
agree with the roster's slugs, compare-and-set writes (create, update, a resent uid, a stale revision, a delete),
what the store refuses, the confirmed rule, the changes feed and the digest, and two writers on one file (threads and
a second process). Invented data throughout (Widget Wars).
    python3 tests/test-library.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
from gatboxlib import library as L

T = tempfile.mkdtemp(prefix="gatbox-library-test-")
passed = failed = 0
n = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def fresh():
    global n
    n += 1
    return L.Library(f"{T}/lib{n}/library.sqlite")


GAME = {"title": "Widget Wars", "mfr": "Widgetco", "year": 1983, "kind": "video"}
LIMIT = {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25", "doc_id": "widget-wars-manual", "page": 2,
         "quote": "+5 VDC 4.75 to 5.25 V"}
LID = "3f2a" * 8
CONF = {"by": "gatbox", "at": 1790000000.0}


def seeded():
    lib = fresh()
    assert lib.put("game", "widget-wars", GAME, 0)["result"] == "created"
    return lib


def main():
    with open(f"{REPO}/docs/contract/library-v1/library-vectors.json") as f:
        vec = json.load(f)

    print("the format's vectors:")
    check("canonical JSON and its SHA-256: every vector",
          all(L.canonical(v["data"]) == v["json"] and L.hash_of(v["data"]) == v["sha256"] for v in vec["canonical"]))
    got = []
    for v in vec["model_key"]:
        try:
            got.append(L.model_key(v["title"], v.get("kind", "video"), v.get("mfr"), v.get("year"), v.get("taken", ())) == v["key"])
        except ValueError as e:
            got.append(v["key"] is None and v["error"] in str(e))
    check("model_key: every vector (a taken title takes the maker, then the year; 60 characters; nothing from a title with no letters)",
          all(got))
    ok = True
    for before, after in vec["volts"]:
        try:
            ok = ok and L.volts(before) == after
        except L.Reject:
            ok = ok and after is None
    check("volts: decimal strings with no trailing zeros; floats, signs and commas refused", ok)
    from gatboxweb import roster
    titles = [v for v in vec["model_key"] if v["key"] and not v.get("taken") and not v.get("mfr")]
    check("keys agree with the roster's own slug rule (gatboxweb.roster.slugify) on every vector",
          all(L.model_key(v["title"], v.get("kind", "video")) == roster.slugify(v["title"], v.get("kind", "video")) for v in titles))

    print("compare-and-set writes:")
    lib = fresh()
    r = lib.put("game", "widget-wars", GAME, 0, uid="u1")
    check("create: created, revision 1, the hash of its data, every field stored",
          r["result"] == "created" and r["record"]["rev"] == 1 and r["record"]["hash"] == L.hash_of(r["record"]["data"])
          and r["record"]["data"]["notes"] == "" and r["record"]["data"]["replaced_by"] is None)
    check("create over an existing record: conflict, nothing written", lib.put("game", "widget-wars", GAME, 0, uid="u2")["result"] == "conflict"
          and lib.get("game", "widget-wars")["rev"] == 1)
    r = lib.put("game", "widget-wars", dict(GAME, notes="Cocktail cab too"), 1, uid="u3")
    check("update with the right revision: updated, revision 2", r["result"] == "updated" and r["record"]["rev"] == 2)
    stale = lib.put("game", "widget-wars", dict(GAME, notes="x"), 1, uid="u4")
    check("a stale revision: conflict, and it returns what's there now", stale["result"] == "conflict" and stale["record"]["rev"] == 2)
    again = lib.put("game", "widget-wars", dict(GAME, notes="Cocktail cab too"), 1, uid="u3")
    check("the same uid sent again (a lost reply): duplicate, not a false conflict", again["result"] == "duplicate"
          and lib.get("game", "widget-wars")["rev"] == 2)
    check("the same data with a new uid: duplicate, no new revision",
          lib.put("game", "widget-wars", dict(GAME, notes="Cocktail cab too"), 2, uid="u5")["result"] == "duplicate"
          and lib.get("game", "widget-wars")["rev"] == 2)
    check("a uid already used for another record: rejected",
          lib.put("game", "other", dict(GAME, title="Other"), 0, uid="u3")["result"] == "rejected")
    lib.put("document", "widget-wars/manual", {"model_key": "widget-wars", "doc_id": "manual", "title": "Manual", "kind": "manual"}, 0)
    r = lib.put("document", "widget-wars/manual", None, 1, deleted=True)
    check("a child deleted: a tombstone with the next revision, still in the library's records",
          r["result"] == "updated" and r["record"]["deleted"] and r["record"]["rev"] == 2
          and lib.get("document", "widget-wars/manual") is None and lib.get("document", "widget-wars/manual", deleted=True))
    check("deleting it again, or a record that isn't there: conflict",
          lib.put("document", "widget-wars/manual", None, 2, deleted=True)["result"] == "conflict"
          and lib.put("document", "widget-wars/nope", None, 0, deleted=True)["result"] == "conflict")
    r = lib.put("document", "widget-wars/manual", {"model_key": "widget-wars", "doc_id": "manual", "title": "Manual 2", "kind": "manual"}, 2)
    check("a deleted record written again: it comes back, revision 3", r["result"] == "updated" and r["record"]["rev"] == 3
          and not r["record"]["deleted"])

    print("what the store refuses (nothing written):")
    lib = seeded()
    base = {"model_key": "widget-wars", "doc_id": "manual", "title": "Manual", "kind": "manual"}
    cases = {
        "an unknown field": ("document", "widget-wars/manual", dict(base, colour="red")),
        "a title over 120 characters": ("game", "g", dict(GAME, title="x" * 121)),
        "a kind that isn't one": ("game", "g", dict(GAME, kind="arcade")),
        "a game with no title": ("game", "g", {"kind": "video"}),
        "a key with capitals": ("game", "Widget-Wars", GAME),
        "the reserved key unassigned": ("game", "unassigned", GAME),
        "a document id that isn't <model_key>/<doc_id>": ("document", "widget-wars/other", base),
        "an http source address": ("document", "widget-wars/manual", dict(base, source_url="http://example.com/m.pdf")),
        "a document of a game that isn't there": ("document", "nope/manual", dict(base, model_key="nope")),
        "a limit with volts as a float": ("rail_limit", LID, dict(LIMIT, lo=4.75)),
        "a limit whose lo is above its hi": ("rail_limit", LID, dict(LIMIT, lo="6", hi="5")),
        "a limit of a game that isn't there": ("rail_limit", LID, dict(LIMIT, model_key="nope")),
        "a limit id that isn't 32 hex digits": ("rail_limit", "abc", LIMIT),
        "a ROM list over 2 MB": ("rom_list", "widget-wars", {"model_key": "widget-wars", "versions": [
            {"name": "v", "roms": [{"name": "r" * 100, "size": 1, "crc": "0", "sha1": "0"}] * 20000}]}),
        "a ROM image with a short SHA-1": ("rom_image", "a" * 64, {"size": 1, "crc32": "0" * 8, "sha1": "abc"}),
        "an unknown kind": ("platform", "x", {}),
    }
    for what, (kind, rid, data) in cases.items():
        r = lib.put(kind, rid, data, 0)
        check(f"{what}: rejected, with its reason", r["result"] == "rejected" and r.get("reason"))
    check("nothing from those was written", lib.digest()["count"] == 1)
    check("a game deleted: rejected (games retire with replaced_by)",
          lib.put("game", "widget-wars", None, 1, deleted=True)["result"] == "rejected")
    check("a game replaced by itself or by a game that isn't there: rejected",
          lib.put("game", "widget-wars", dict(GAME, replaced_by="widget-wars"), 1)["result"] == "rejected"
          and lib.put("game", "widget-wars", dict(GAME, replaced_by="nope"), 1)["result"] == "rejected")
    lib.put("game", "widget-wars-dupe", dict(GAME, title="Widget Wars (dupe)"), 0)
    check("a mistaken duplicate retired with replaced_by: resolve() follows it",
          lib.put("game", "widget-wars-dupe", dict(GAME, title="Widget Wars (dupe)", replaced_by="widget-wars"), 1)["result"] == "updated"
          and lib.resolve("widget-wars-dupe")["id"] == "widget-wars" and lib.resolve("nope") is None)
    check("... and the survivor can't be pointed back at the retired one (a loop)",
          lib.put("game", "widget-wars", dict(GAME, replaced_by="widget-wars-dupe"), 1)["result"] == "rejected")
    r = lib.put("rom_image", "b" * 64, {"size": 4, "crc32": "0" * 8, "sha1": "c" * 40, "model_key": "widget-wars"}, 0)
    check("a ROM image of a known game is stored by its hash; a missing optional field takes its default",
          r["result"] == "created" and r["record"]["data"]["verdict"] == "unknown" and r["record"]["data"]["chip"] == "")

    print("the confirmed rule (hard rule 12):")
    lib = seeded()
    r = lib.put("rail_limit", LID, dict(LIMIT, confirmed=CONF), 0)
    check("a limit created confirmed by a trusted writer (the seed) keeps it", r["record"]["data"]["confirmed"] == CONF)
    rev = lambda: lib.get("rail_limit", LID)["rev"]                                                 
    conf = lambda: lib.get("rail_limit", LID)["data"]["confirmed"]                                 
    lib.put("rail_limit", LID, dict(LIMIT, confirmed=CONF, quote="a different quote"), rev())
    check("a change to the quote keeps it confirmed", conf() == CONF)
    lib.put("rail_limit", LID, dict(LIMIT, confirmed=CONF, hi="5.5"), rev())
    check("a change to hi clears it, whatever the writer sent", conf() is None)
    lib.put("rail_limit", LID, dict(LIMIT, hi="5.5", confirmed=CONF), rev())
    check("confirming (a write that changes none of the numbers) sets it", conf() == CONF)
    for field, value in (("lo", "4.7"), ("doc_id", "other-manual"), ("page", 3)):
        lib.put("rail_limit", LID, dict(LIMIT, hi="5.5", confirmed=CONF, **{field: value}), rev())
        check(f"a change to {field} clears it", conf() is None)
        lib.put("rail_limit", LID, dict(lib.get("rail_limit", LID)["data"], confirmed=CONF), rev())

    print("the feed and the digest:")
    lib = fresh()
    for i in range(5):
        lib.put("game", f"game-{i}", dict(GAME, title=f"Game {i}"), 0, uid=f"g{i}")
    lib.put("game", "game-1", dict(GAME, title="Game 1 again"), 1, uid="g1b")
    f = lib.changes(0, limit=3)
    check("the feed: oldest first, a page at a time, the cursor and more",
          [r["id"] for r in f["records"]] == ["game-0", "game-2", "game-3"] and f["more"] and f["cursor"] > 0)
    f2 = lib.changes(f["cursor"], limit=3)
    check("... the next page: the rest, and a record changed twice only once, at its latest revision",
          [r["id"] for r in f2["records"]] == ["game-4", "game-1"] and not f2["more"]
          and f2["records"][1]["rev"] == 2 and f2["records"][1]["data"]["title"] == "Game 1 again")
    check("... after the last cursor: nothing, and the cursor stays", lib.changes(f2["cursor"])["records"] == []
          and lib.changes(f2["cursor"])["cursor"] == f2["cursor"])
    a, b = fresh(), fresh()
    for lib_, by, at in ((a, "gatbox", 1.0), (b, "someone", 99.0)):
        lib_.put("game", "widget-wars", GAME, 0, uid=f"{by}-1", by=by, at=at)
        lib_.put("game", "widget-wars", dict(GAME, notes="n"), 1, uid=f"{by}-2", by=by, at=at)
    check("two libraries with the same history agree on the digest, whoever wrote it and whenever", a.digest() == b.digest()
          and a.digest()["count"] == 1)
    b.put("game", "widget-wars", dict(GAME, notes="n2"), 2)
    check("... and stop agreeing when a record differs", a.digest() != b.digest())
    check("counts by kind, and the file's size", a.counts() == {"game": 1} and a.size() > 0)

    print("two writers on one file:")
    lib = seeded()
    results = []
    threads = [threading.Thread(target=lambda: results.append(lib.put("document", "widget-wars/race", {
        "model_key": "widget-wars", "doc_id": "race", "title": "Race", "kind": "manual"}, 0))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("eight threads create one record at once: exactly one created, the rest conflict",
          sorted(r["result"] for r in results) == ["conflict"] * 7 + ["created"])
    script = ("import sys; sys.path.insert(0, sys.argv[1]); from gatboxlib import library as L\n"
              "lib = L.Library(sys.argv[2])\n"
              "for i in range(60):\n"
              "    r = lib.put('fact', '%032x' % (1000 + i), {'model_key': 'widget-wars', 'group': 'fuse', 'what': 'F%d' % i, 'value': '5 A'}, 0)\n"
              "    assert r['result'] == 'created', r\n")
    other = subprocess.Popen([sys.executable, "-c", script, os.path.join(REPO, "backend"), lib.path])
    for i in range(60):
        r = lib.put("fact", "%032x" % (2000 + i), {"model_key": "widget-wars", "group": "psu", "what": f"P{i}", "value": "12 V"}, 0)
        assert r["result"] == "created", r
    check("a second process writing while this one does: both finish, all 120 facts there", other.wait(60) == 0
          and len(lib.all("fact")) == 120)

    print(f"\nlibrary: {passed} passed, {failed} failed")


try:
    main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
