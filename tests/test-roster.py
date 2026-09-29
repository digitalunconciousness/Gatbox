#!/usr/bin/env python3
"""Adding a machine on the Pi (2026-09-29): POST /api/roster (the ID it gets, the checks), PUT /api/roster/<slug>
(machines added here only), the merge (the roster file wins a slug both have; retired slugs stay taken), the added
machines everywhere a roster slug is taken (machine, scan, captures, dumps, labels), and GET /roster.json (the file
with the additions, in its own format). The installed roster file is never written. Temp dirs and a spare port only.
    python3 tests/test-roster.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="gatbox-roster-")
PORT = 8092
B = f"http://127.0.0.1:{PORT}"
FIELDS = ["slug", "name", "platform", "mfr", "risk", "faults", "parts", "notes"]
passed = failed = 0
procs = []

ROSTER = {"meta": {"title": "test roster — synthetic", "generated": "2026-07-03",
                   "platforms": {"test_hdd": {"desc": "Test platform with a hard drive", "faults": ["drive failure"],
                                              "parts": ["CF adapter"], "pm": "image the drive"},
                                 "test_pin": {"desc": "Test pinball system", "faults": [], "parts": []}},
                   "risk_legend": {"hdd": "aging drive", "confirm": "platform not confirmed"}, "critical_actions": {}},
          "video_games": [{"slug": "gauntlet-legends", "name": "Gauntlet Legends", "platform": "test_hdd", "mfr": "Atari/Midway",
                           "risk": ["hdd"], "faults": [], "parts": [], "notes": ""},
                          {"slug": "gauntlet", "name": "Gauntlet", "platform": "test_hdd", "mfr": "Atari", "risk": [],
                           "faults": [], "parts": [], "notes": ""}],
          "pinball": [{"slug": "pin-x-men", "name": "X-Men", "platform": "test_pin", "mfr": "Stern", "risk": [],
                       "faults": [], "parts": [], "notes": ""}],
          "retired": [{"slug": "pin-gone", "name": "Gone", "platform": "test_pin", "mfr": "Stern", "notes": "removed"}]}


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def api(method, path, body=None, ctype="application/json"):
    req = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": ctype} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def raw(path):
    with urllib.request.urlopen(B + path, timeout=120) as r:
        return r.status, dict(r.headers), r.read()


def machine(**kw):
    return dict({"name": "Street Fighter Alpha 2", "mfr": "Capcom", "kind": "video_games", "platform": "test_hdd",
                 "notes": ""}, **kw)


def main():
    for d in ("log", "run", "ctrl", "cache", "data"):
        os.makedirs(os.path.join(T, d))
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    rp, added = f"{T}/data/gatbox-barcade-roster.json", f"{T}/ctrl/roster-added.json"
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data", GATBOX_ROMS=f"{T}/roms",
               GATBOX_LABELS=os.path.join(REPO, "tools/gatbox-labels"), MPLCONFIGDIR=f"{T}/cache/mpl")
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    procs.append(subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                  stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT))
    for _ in range(50):
        try:
            raw("/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)

    print("no roster installed:")
    check("GET /api/roster -> 503", api("GET", "/api/roster")[0] == 503)
    c, d = api("POST", "/api/roster", machine(platform="confirm", dry_run=True))
    check("a dry run still works (NOT SURE platform): the ID it would get", c == 200 and d["entry"]["slug"] == "street-fighter-alpha-2")
    check("... and writes nothing", not os.path.exists(added))

    with open(rp, "w", encoding="utf-8") as f:
        json.dump(ROSTER, f, indent=2)
    original = open(rp, "rb").read()

    print("the roster file + nothing added:")
    c, d = api("GET", "/api/roster")
    check("listing: 3 machines, none added, the platforms by name",
          c == 200 and len(d["machines"]) == 3 and d["added"] == 0 and not any(m["added"] for m in d["machines"])
          and [p["key"] for p in d["platforms"]] == ["test_pin", "test_hdd"])

    print("the ID a new machine gets (dry runs):")
    ids = {}
    for name, kind in (("Street Fighter Alpha 2", "video_games"), ("Satan's Hollow 2", "video_games"),
                       ("Tron: Legacy", "video_games"), ("Rock & Roll", "video_games"), ("Q*bert's Qubes", "video_games"),
                       ("Medieval Madness", "pinball"), ("Pin-Bot", "pinball"), ("Pokémon", "video_games")):
        c, d = api("POST", "/api/roster", machine(name=name, kind=kind, platform="confirm", dry_run=True))
        ids[name] = d["entry"]["slug"] if c == 200 else f"HTTP {c}"
    check("lower-case, hyphens, apostrophes dropped, & -> and, pin- for pinball, accents folded",
          ids == {"Street Fighter Alpha 2": "street-fighter-alpha-2", "Satan's Hollow 2": "satans-hollow-2",
                  "Tron: Legacy": "tron-legacy", "Rock & Roll": "rock-and-roll", "Q*bert's Qubes": "q-berts-qubes",
                  "Medieval Madness": "pin-medieval-madness", "Pin-Bot": "pin-pin-bot", "Pokémon": "pokemon"})
    check("dry runs write nothing", not os.path.exists(added))

    print("the checks:")
    bad = {"no name": machine(name=""), "name all symbols": machine(name="!!!"), "name with a tab": machine(name="a\tb"),
           "name over 60": machine(name="x" * 61), "no maker": machine(mfr=" "), "maker over 40": machine(mfr="x" * 41),
           "bad kind": machine(kind="retired"), "unknown platform": machine(platform="nope"),
           "notes over 200": machine(notes="x" * 201), "name not a string": machine(name=5), "not an object": [1]}
    got = {k: api("POST", "/api/roster", b)[0] for k, b in bad.items()}
    check("bad input -> 400 each", set(got.values()) == {400})
    check("plain form post -> 415", api("POST", "/api/roster", machine(), ctype="text/plain")[0] == 415)
    c, d = api("POST", "/api/roster", machine(name="gauntlet"))
    check("a name already on the roster (any case) -> 409", c == 409 and "gauntlet" in d["error"])
    c, d = api("POST", "/api/roster", machine(name="Gauntlet!"))
    check("a different name, same ID -> 409", c == 409)
    c, d = api("POST", "/api/roster", machine(name="Gone", kind="pinball"))
    check("a retired machine's ID -> 409, says retired", c == 409 and "retired" in d["error"])
    c, d = api("POST", "/api/roster", machine(name="Unassigned"))
    check("'unassigned' is reserved -> 409", c == 409)
    check("nothing written by any of them", not os.path.exists(added))

    print("add:")
    c, d = api("POST", "/api/roster", machine(notes="  CPS2  "))
    e = d.get("entry", {})
    check("201, the roster's own fields in its order", c == 201 and list(e) == FIELDS
          and e == {"slug": "street-fighter-alpha-2", "name": "Street Fighter Alpha 2", "platform": "test_hdd", "mfr": "Capcom",
                    "risk": [], "faults": [], "parts": [], "notes": "CPS2"})
    c, d = api("POST", "/api/roster", machine(name="Medieval  Madness", mfr="Williams", kind="pinball", platform="confirm"))
    check("pinball, NOT SURE: pin- ID, platform + risk 'confirm', spaces squeezed",
          c == 201 and d["entry"]["slug"] == "pin-medieval-madness" and d["entry"]["name"] == "Medieval Madness"
          and d["entry"]["platform"] == "confirm" and d["entry"]["risk"] == ["confirm"])
    check("the same one again -> 409", api("POST", "/api/roster", machine())[0] == 409)
    check("the roster file is untouched", open(rp, "rb").read() == original)
    a = json.load(open(added))
    check("saved to the state dir", [x["slug"] for x in a["video_games"]] == ["street-fighter-alpha-2"]
          and [x["slug"] for x in a["pinball"]] == ["pin-medieval-madness"])

    print("the added machines, everywhere:")
    c, d = api("GET", "/api/roster")
    flags = {m["slug"]: m["added"] for m in d["machines"]}
    check("listing: 5 machines, 2 added", len(d["machines"]) == 5 and d["added"] == 2 and flags["street-fighter-alpha-2"]
          and flags["pin-medieval-madness"] and not flags["gauntlet"])
    c, d = api("GET", "/api/roster/street-fighter-alpha-2")
    check("entry: merged with its platform, marked added", c == 200 and d["added"] is True and d["kind"] == "video_games"
          and d["platform_info"]["desc"] == "Test platform with a hard drive" and d["faults_all"] == ["drive failure"])
    c, d = api("GET", "/api/roster/pin-medieval-madness")
    check("NOT SURE entry: the confirm risk spelled out", c == 200 and d["risk_info"] == [{"flag": "confirm", "meaning": "platform not confirmed"}])
    c, d = api("PUT", "/api/machine", {"slug": "street-fighter-alpha-2"})
    check("PUT /api/machine takes it", c == 200 and d["entry"]["name"] == "Street Fighter Alpha 2")
    check("GET /api/machine shows it", api("GET", "/api/machine")[1]["entry"]["name"] == "Street Fighter Alpha 2")
    c, d = api("POST", "/api/scan", {"code": "PIN-MEDIEVAL-MADNESS"})
    check("a QR scan of its ID sets it", c == 200 and d["action"] == "machine")
    check("captures + dumps accept it", api("GET", "/api/captures?machine=pin-medieval-madness")[0] == 200
          and api("GET", "/api/dumps?machine=pin-medieval-madness")[0] == 200)
    api("DELETE", "/api/machine")

    print("edit (added machines only):")
    c, d = api("PUT", "/api/roster/street-fighter-alpha-2", {"name": "Street Fighter Alpha 2 (US)", "notes": "CPS2 B-board"})
    check("name + notes changed, ID kept", c == 200 and d["entry"]["slug"] == "street-fighter-alpha-2"
          and d["entry"]["name"] == "Street Fighter Alpha 2 (US)" and d["entry"]["notes"] == "CPS2 B-board")
    c, d = api("PUT", "/api/roster/street-fighter-alpha-2", {"platform": "confirm"})
    check("platform -> NOT SURE adds the confirm risk", c == 200 and d["entry"]["risk"] == ["confirm"])
    c, d = api("PUT", "/api/roster/street-fighter-alpha-2", {"platform": "test_hdd"})
    check("... and a real platform takes it away", c == 200 and d["entry"]["risk"] == [] and d["entry"]["platform"] == "test_hdd")
    check("a name another machine has -> 409", api("PUT", "/api/roster/street-fighter-alpha-2", {"name": "GAUNTLET"})[0] == 409)
    check("its own name again is fine", api("PUT", "/api/roster/street-fighter-alpha-2", {"name": "Street Fighter Alpha 2 (US)"})[0] == 200)
    check("slug or kind can't change -> 400", [api("PUT", "/api/roster/street-fighter-alpha-2", b)[0]
                                                for b in ({"slug": "x"}, {"kind": "pinball"})] == [400, 400])
    c, d = api("PUT", "/api/roster/gauntlet", {"name": "Gauntlet 2"})
    check("a machine from the roster file -> 409", c == 409 and "roster file" in d["error"])
    check("unknown -> 404", api("PUT", "/api/roster/nope", {"name": "x"})[0] == 404)
    check("the roster file is still untouched", open(rp, "rb").read() == original)

    print("export (GET /roster.json):")
    c, h, body = raw("/roster.json")
    ex = json.loads(body)
    check("a download named like the roster file", c == 200 and 'filename="gatbox-barcade-roster.json"' in h.get("Content-Disposition", ""))
    check("the file's meta and retired as they were", ex["meta"] == ROSTER["meta"] and ex["retired"] == ROSTER["retired"])
    check("the file's machines first, then the added ones",
          [m["slug"] for m in ex["video_games"]] == ["gauntlet-legends", "gauntlet", "street-fighter-alpha-2"]
          and [m["slug"] for m in ex["pinball"]] == ["pin-x-men", "pin-medieval-madness"])
    check("entries in the roster's own shape (no 'added' or 'kind')", all(list(m) == FIELDS for m in ex["video_games"] + ex["pinball"]))
    check("same layout as the file (2-space indent, ASCII escapes)", body.startswith(b'{\n  "meta": {') and b"\\u2014" in body)

    print("labels:")
    try:
        import qrcode  # noqa: F401
        have_qr = True
    except ImportError:
        have_qr = False
        print("  skip  (python3-qrcode not installed)")
    if have_qr:
        r = subprocess.run([os.path.join(REPO, "tools/gatbox-labels"), "-o", f"{T}/labels.pdf"], capture_output=True, text=True,
                           env=dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=f"{T}/ctrl", MPLCONFIGDIR=f"{T}/cache/mpl"),
                           timeout=120)
        check("gatbox-labels (as gatbox-web runs it): 5 machines", "5 machines" in r.stdout)
        c, _, pdf = raw("/labels.pdf")
        first = sorted(f for f in os.listdir(f"{T}/cache") if f.startswith("labels_"))
        api("POST", "/api/roster", machine(name="Joust 2", mfr="Williams"))
        c2, _, pdf2 = raw("/labels.pdf")
        second = sorted(f for f in os.listdir(f"{T}/cache") if f.startswith("labels_"))
        check("/labels.pdf rebuilt after an add", c == c2 == 200 and pdf2[:5] == b"%PDF-" and first != second)

    print("the roster file wins:")
    newer = json.loads(json.dumps(ROSTER))
    newer["video_games"].append({"slug": "street-fighter-alpha-2", "name": "Street Fighter Alpha 2 (from the app)",
                                 "platform": "test_hdd", "mfr": "Capcom", "risk": [], "faults": [], "parts": [], "notes": ""})
    newer["retired"].append({"slug": "pin-medieval-madness", "name": "Medieval Madness", "platform": "test_pin", "mfr": "Williams"})
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(newer, f, indent=2)
    c, d = api("GET", "/api/roster")
    by = {m["slug"]: m for m in d["machines"]}
    check("a slug in both: the file's entry, listed once, not 'added'",
          [m["slug"] for m in d["machines"]].count("street-fighter-alpha-2") == 1
          and by["street-fighter-alpha-2"]["name"] == "Street Fighter Alpha 2 (from the app)" and not by["street-fighter-alpha-2"]["added"])
    check("retired in the file: off the floor", "pin-medieval-madness" not in by)
    check("added count: only what the file lacks (joust-2)", d["added"] == (1 if have_qr else 0))
    check("editing it now -> 409 (the file owns it)", api("PUT", "/api/roster/street-fighter-alpha-2", {"notes": "x"})[0] == 409)
    ex = json.loads(raw("/roster.json")[2])
    check("export: no duplicate", [m["slug"] for m in ex["video_games"]].count("street-fighter-alpha-2") == 1)

    print("a damaged additions file:")
    with open(added, "w") as f:
        f.write("{not json")
    c, d = api("GET", "/api/roster")
    check("the roster still loads (the file's machines)", c == 200 and len(d["machines"]) == 4)
    c, d = api("POST", "/api/roster", machine(name="Defender II"))
    check("adding refuses to overwrite it -> 503", c == 503 and open(added).read() == "{not json")

    web = open(f"{T}/web.log").read()
    check("server log: no errors", "Traceback" not in web and "error on" not in web)
    print(f"roster: {passed} passed, {failed} failed")


try:
    main()
finally:
    for p in procs:
        p.terminate()
        p.wait(10)
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
