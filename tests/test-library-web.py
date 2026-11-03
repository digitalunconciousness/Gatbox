#!/usr/bin/env python3
"""Phase B3: gatbox-web and the logger's limits read through the game library once the deck is seeded.
A real gatbox-web on an invented deck (tests/fakedeck.py): its answers before and after the seed are the same, CONFIRM and
UNCONFIRM go through the library and reach the logger as flat JSON, a change made from outside (the owner's CLI) reaches
the logger within seconds, a damaged export or a vanished library falls back to today's files, two cabinets the seed made one
game share their manuals and ROM list, and the logger's code path never opens the database.
    python3 tests/test-library-web.py
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
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "tests"))
from fakedeck import build_deck, write
from gatboxlib import library as L
from gatboxlib import profiles

TOOL = os.path.join(REPO, "tools/gatbox-library")
T = tempfile.mkdtemp(prefix="gatbox-library-web-")
PORT = 8097
B = f"http://127.0.0.1:{PORT}"
LIB = f"{T}/lib/library.sqlite"
STATE = f"{T}/state"
EXPORT = f"{STATE}/library-specs.json"
passed = failed = 0
web = None


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def env(**extra):
    e = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=STATE, STATE_DIRECTORY=STATE, CACHE_DIRECTORY=f"{T}/cache",
             GATBOX_MANUALS=f"{T}/manuals", GATBOX_ROMS=f"{T}/roms", GATBOX_MAME_ROMS=f"{T}/mame-roms.json",
             GATBOX_LIBRARY=LIB, GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_WEB_PORT=str(PORT),
             GATBOX_SYNC_STATE=f"{T}/sync", MPLCONFIGDIR=f"{T}/cache/mpl")
    e.update(extra)
    return e


def cli(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env(), timeout=120, check=False)
    return r.returncode, r.stdout + r.stderr


def _lan_addrs():
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=False).stdout.split()
    except (FileNotFoundError, OSError):
        pass
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, check=False).stdout
        return [ln.split()[3].split("/")[0] for ln in out.splitlines() if len(ln.split()) > 3]
    except (FileNotFoundError, OSError, IndexError):
        return []


def call(method, path, body=None, base=B):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def get(path):
    return call("GET", path)[1]


def settles(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


def exported():
    try:
        with open(EXPORT, encoding="utf-8") as f:
            return json.load(f)["machines"]
    except (OSError, ValueError, KeyError):
        return None


def windows(slug):
    """What the logger would use for a machine: the rail windows, from the flat files only."""
    return (profiles.machine_specs(f"{T}/data", STATE).get(slug) or {}).get("rails")


def norm(x):
    """An API answer, with lists in a stable order and an empty source the same as none."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items() if not (k == "source" and v in ("", None))}
    if isinstance(x, list):
        items = [norm(i) for i in x]
        return sorted(items, key=lambda i: json.dumps(i, sort_keys=True))
    return x


def snap():
    out = {}
    for slug in ("widget-wars", "gizmo-racer"):
        out[f"manuals {slug}"] = norm(get(f"/api/manuals/{slug}"))
        out[f"specs {slug}"] = norm(get(f"/api/specs/{slug}"))
        out[f"mame {slug}"] = norm(get(f"/api/mame/{slug}"))
    return out


def start():
    global web
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    for d in ("log", "run", "cache", "sync", "state"):
        os.makedirs(f"{T}/{d}", exist_ok=True)
    for f in ("profiles.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    web = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-web")], env=env(),
                           stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            get("/kiosk/state")
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("gatbox-web didn't start")


def drop_library():
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(LIB + suffix):
            os.remove(LIB + suffix)


def main():
    build_deck(T, stale=False)
    # a PDF in the cocktail cabinet's own folder, for the merge at the end
    pdf = b"%PDF-1.4 cocktail notes"
    write(f"{T}/manuals/widget-wars-cocktail/cocktail-notes.pdf", pdf, True)
    write(f"{T}/manuals/widget-wars-cocktail/cocktail-notes.json", {"id": "cocktail-notes", "title": "Cocktail Notes", "kind": "yours",
                                                                  "pages": 1, "sha256": "ab" * 32})
    start()

    print("an unseeded deck is exactly what it was:")
    before = snap()
    wins0 = {s: windows(s) for s in ("widget-wars", "gizmo-racer")}
    check("no library file: no export either, and the logger's windows are the files' (the specs file and the confirmed state)",
          not os.path.exists(EXPORT) and wins0["widget-wars"] == {"+5V": [4.75, 5.25]} and wins0["gizmo-racer"] == {"+5V": [4.9, 5.1]})
    check("the manuals tab's answer for a machine names its documents and its note", before["manuals widget-wars"]["docs"]
          and before["specs widget-wars"]["rails"])
    check("the games API says the library isn't set up, and refuses to add a game to one",
          get("/api/library") == {"seeded": False}
          and call("POST", "/api/library/games", {"title": "Sprocket Wars", "kind": "video"})[0] == 409)

    print("seeding it (an identity plan) changes no answer:")
    code, _ = cli("seed", "--plan", "--out", f"{T}/plan.json")
    code2, _ = cli("seed", "--apply", f"{T}/plan.json")
    check("seed --plan and --apply on this deck", code == 0 and code2 == 0 and os.path.isfile(LIB))
    check("gatbox-web notices and writes the logger's export within seconds", settles(lambda: exported() is not None, 20))
    after = snap()
    diffs = [k for k in before if before[k] != after[k]]
    for k in diffs:
        print(f"      differs: {k}")
        print("        before:", json.dumps(before[k], sort_keys=True)[:600])
        print("        after: ", json.dumps(after[k], sort_keys=True)[:600])
    check("every API answer is the same as before the seed: manuals, the spec sheet, the MAME chip list, two machines", not diffs)
    check("the logger's windows are the same too, now from the flat export (and name where each came from)",
          {s: windows(s) for s in ("widget-wars", "gizmo-racer")} == wins0
          and exported()["gizmo-racer"]["sources"]["+5V"] == "specs file"
          and exported()["widget-wars"]["sources"]["+5V"].startswith("manual: Widget Wars Operations Manual p.2"))
    check("the export has only limits that are confirmed: the unconfirmed +12V isn't in it", "+12V" not in exported()["widget-wars"]["rails"])

    print("CONFIRM goes through the library and reaches the logger:")
    c, j = call("POST", "/api/specs/widget-wars/confirm", {"rail": "+12V", "doc": "widget-wars-manual", "page": 2})
    row = next((r for r in j["sheet"]["rails"] if r["rail"] == "+12V"), {}) if c == 200 else {}
    check("CONFIRM: 200, the sheet shows it confirmed and the window in use", c == 200 and row["manual"][0]["confirmed"] and row["window"] == [11.4, 12.6])
    check("... the export has it at once, and the logger's window for the machine now has +12V",
          exported()["widget-wars"]["rails"].get("+12V") == [11.4, 12.6] and windows("widget-wars").get("+12V") == [11.4, 12.6])
    check("CONFIRM a limit that isn't on the list: 400, nothing confirmed",
          call("POST", "/api/specs/widget-wars/confirm", {"rail": "+12V", "doc": "widget-wars-manual", "page": 9})[0] == 400)
    c, j = call("DELETE", "/api/specs/widget-wars/confirm", {"rail": "+12V"})
    check("UNCONFIRM: the logger stops using it, at once", c == 200 and "+12V" not in exported()["widget-wars"]["rails"]
          and "+12V" not in (windows("widget-wars") or {}))
    check("UNCONFIRM what isn't confirmed: 404", call("DELETE", "/api/specs/widget-wars/confirm", {"rail": "+12V"})[0] == 404)
    c, j = call("PUT", "/api/actuals/widget-wars", {"rail": "+5V", "value": 5.2, "window": [5.1, 5.3]})
    check("an actual value with its own window still wins over the manual's, as before",
          c == 200 and windows("widget-wars")["+5V"] == [5.1, 5.3] and exported()["widget-wars"]["rails"]["+5V"] == [4.75, 5.25])
    call("DELETE", "/api/actuals/widget-wars", {"rail": "+5V"})

    print("a change made outside gatbox-web (the owner's CLI) reaches the logger:")
    mm = json.load(open(f"{T}/data/gatbox-manuals.json"))
    mm["machines"]["widget-wars"]["specs"]["rails"][0]["hi"] = 5.5
    write(f"{T}/new-manuals.json", mm)
    code, out = cli("import", "--kind", "manuals", f"{T}/new-manuals.json")
    check("import changes +5V's upper limit: the confirmation goes, because the numbers changed", code == 0 and "1 updated" in out)
    check("... and within seconds the logger no longer has the +5V manual limit (nothing confirmed is left for it)",
          settles(lambda: "+5V" not in (exported().get("widget-wars", {}).get("rails") or {}), 20)
          and "+5V" not in (windows("widget-wars") or {}))
    check("... the page shows it unconfirmed", next(r for r in get("/api/specs/widget-wars")["rails"] if r["rail"] == "+5V")["manual"][0]["confirmed"] is False)

    print("falling back to the files:")
    with open(EXPORT, "w") as f:
        f.write("{ this is not json")
    check("a damaged export: the logger reads the files, as on an unseeded deck", windows("widget-wars") == wins0["widget-wars"])
    check("... and gatbox-web writes a good one again", settles(lambda: exported() is not None, 20))
    drop_library()
    check("the library file gone: the export is removed, and the logger is back on the files", settles(lambda: not os.path.exists(EXPORT), 20)
          and windows("widget-wars") == wins0["widget-wars"])
    check("... and the manuals tab answers as it did before any seed", {k: v for k, v in snap().items()} == before)

    print("two cabinets seeded as one game:")
    code, _ = cli("seed", "--plan", "--out", f"{T}/plan2.json")
    plan = json.load(open(f"{T}/plan2.json"))
    plan["games"]["widget-wars-cocktail"]["key"] = "widget-wars"
    write(f"{T}/plan2-merged.json", plan)
    code2, _ = cli("seed", "--apply", f"{T}/plan2-merged.json")
    check("seeded with the cocktail made part of widget-wars", code == 0 and code2 == 0 and settles(lambda: exported() is not None, 20))
    a, b = get("/api/manuals/widget-wars"), get("/api/manuals/widget-wars-cocktail")
    check("both machines list the same documents, the cocktail's own file among them, and both name widget-wars as the game's folder",
          {d["id"] for d in a["docs"]} == {d["id"] for d in b["docs"]} and "cocktail-notes" in {d["id"] for d in a["docs"]}
          and "widget-wars-manual" in {d["id"] for d in b["docs"]} and a["library"] == b["library"] == "widget-wars")
    c1, raw1 = call("GET", "/manual/widget-wars/cocktail-notes.pdf")
    c2, raw2 = call("GET", "/manual/widget-wars-cocktail/widget-wars-manual.pdf")
    check("a file is served from whichever cabinet's folder it's in, by either machine", c1 == 200 and raw1 == pdf and c2 == 200
          and raw2.startswith(b"%PDF"))
    check("the spec sheet, the limits and the ROM list are shared the same way",
          get("/api/specs/widget-wars-cocktail")["rails"] == get("/api/specs/widget-wars")["rails"]
          and get("/api/mame/widget-wars-cocktail")["versions"] == get("/api/mame/widget-wars")["versions"]
          and windows("widget-wars-cocktail") == windows("widget-wars"))

    print("once seeded, the library is the source (an edit there shows in the answers):")
    lib = L.Library(LIB, create=False)
    d = lib.get("document", "widget-wars/widget-wars-schematics")
    lib.put("document", d["id"], dict(d["data"], title="Schematics (edited in the library)"), d["rev"])
    f = next(x for x in lib.all("fact", "widget-wars") if x["data"]["what"] == "Fuse F1")
    lib.put("fact", f["id"], dict(f["data"], value="8 A slow-blow"), f["rev"])
    rl = lib.get("rom_list", "widget-wars")
    rom = json.loads(json.dumps(rl["data"]))
    rom["versions"][0]["description"] = "Widget Wars (edited in the library)"
    lib.put("rom_list", "widget-wars", rom, rl["rev"])
    g = lib.get("game", "widget-wars")
    lib.put("game", "widget-wars", dict(g["data"], notes="A note from the library"), g["rev"])
    docs = get("/api/manuals/widget-wars")
    check("a document's title, the game's note, a fact's value and a ROM version's description all show the edit",
          any(x["title"] == "Schematics (edited in the library)" for x in docs["not_fetched"]) and docs["note"] == "A note from the library"
          and next(x for x in get("/api/specs/widget-wars")["facts"] if x["what"] == "Fuse F1")["value"] == "8 A slow-blow"
          and get("/api/mame/widget-wars")["versions"][0]["desc"] == "Widget Wars (edited in the library)")
    check("... and the other cabinet that became the same game shows it too",
          get("/api/manuals/widget-wars-cocktail")["note"] == "A note from the library")

    print("the games API (the LIBRARY view's):")
    lst = get("/api/library")
    byk = {g["key"]: g for g in lst["games"]}
    check("the list: every live game by title, with what the library holds for it and which machines are it",
          [g["title"] for g in lst["games"]] == ["Gizmo Racer", "Sprocket & Cog", "Widget Pinball", "Widget Wars"]
          and lst["count"] == 4 and byk["widget-wars"]["limits"] == 2 and byk["widget-wars"]["facts"] == 2
          and [m["slug"] for m in byk["widget-wars"]["machines"]] == ["widget-wars", "widget-wars-cocktail"]
          and byk["widget-wars"]["mame"] == "widgetw")
    check("a game with no manual fetched is MISSING; one with a manual isn't; the notes come with each game",
          byk["gizmo-racer"]["missing"] and not byk["widget-wars"]["missing"] and lst["missing"] == 3
          and byk["widget-wars"]["notes"] == "A note from the library" and byk["widget-wars"]["fetched"] == 3
          and byk["widget-wars"]["docs"] == 4)
    check("a proposed key: the title as a slug, a pinball's with pin-, a taken one gets the maker",
          get("/api/library/key?title=Sprocket+Wars&kind=video")["key"] == "sprocket-wars"
          and get("/api/library/key?title=Widget+Wars&kind=pinball")["key"] == "pin-widget-wars"
          and get("/api/library/key?title=Widget+Wars&kind=video&mfr=Other+Co")["key"] == "widget-wars-other-co"
          and call("GET", "/api/library/key?title=%E3%82%B2%E3%83%BC%E3%83%A0")[0] == 400)
    c, g = call("POST", "/api/library/games", {"title": "Sprocket Wars", "mfr": "Cogco", "year": 1984, "kind": "video",
                                               "notes": "Upright only"})
    check("ADD GAME: 201, the proposed key, its fields kept, no machines yet", c == 201 and g["key"] == "sprocket-wars"
          and g["title"] == "Sprocket Wars" and g["year"] == 1984 and g["machines"] == [] and g["missing"])
    check("... it's in the list, and a second one with that key is refused",
          any(x["key"] == "sprocket-wars" for x in get("/api/library")["games"])
          and call("POST", "/api/library/games", {"title": "Sprocket Wars", "kind": "video", "key": "sprocket-wars"})[0] == 409)
    check("ADD GAME with a missing title, a bad kind or a title over 120 characters: 400 each",
          [call("POST", "/api/library/games", b)[0] for b in ({"kind": "video"}, {"title": "X", "kind": "arcade"},
                                                                {"title": "x" * 121, "kind": "video"})] == [400, 400, 400])
    check("a game no roster machine is still has manuals and a spec sheet, and no actual values (those are per machine)",
          get("/api/manuals/sprocket-wars")["docs"] == [] and get("/api/specs/sprocket-wars")["machine"] is False
          and call("PUT", "/api/actuals/sprocket-wars", {"rail": "+5V", "value": 5.0})[0] == 404
          and get("/api/specs/widget-wars")["machine"] is True)
    c, e = call("PUT", "/api/library/games/sprocket-wars", {"rev": g["rev"], "title": "Sprocket Wars II", "notes": "Upright and cocktail"})
    check("EDIT: the fields change and the key never does", c == 200 and e["key"] == "sprocket-wars" and e["title"] == "Sprocket Wars II"
          and e["notes"] == "Upright and cocktail" and e["rev"] == g["rev"] + 1)
    check("EDIT from a stale revision: 409, nothing changed; an unknown game: 404; a bad field: 400",
          call("PUT", "/api/library/games/sprocket-wars", {"rev": g["rev"], "title": "Stale"})[0] == 409
          and get("/api/library/games/sprocket-wars")["title"] == "Sprocket Wars II"
          and call("PUT", "/api/library/games/nope", {"rev": 1, "title": "x"})[0] == 404
          and call("PUT", "/api/library/games/sprocket-wars", {"rev": e["rev"], "title": ""})[0] == 400)
    lib = L.Library(LIB, create=False)
    lib.put("game", "listed-only", {"title": "Listed Only", "kind": "video"}, 0)
    lib.put("document", "listed-only/manual", {"model_key": "listed-only", "doc_id": "manual", "title": "Manual", "kind": "manual",
                                               "source_url": "https://archive.example/m.pdf"}, 0)
    lo = next(g for g in get("/api/library")["games"] if g["key"] == "listed-only")
    check("a game whose documents are listed but not fetched yet is still MISSING a manual",
          lo["docs"] == 1 and lo["fetched"] == 0 and lo["missing"])
    lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
    if lan:
        base = B.replace("127.0.0.1", lan)
        check("from a phone on the network the library is read-only: add and edit are 403, the list still reads",
              call("POST", "/api/library/games", {"title": "From A Phone", "kind": "video"}, base=base)[0] == 403
              and call("PUT", "/api/library/games/sprocket-wars", {"rev": e["rev"], "title": "x"}, base=base)[0] == 403
              and call("GET", "/api/library", base=base)[0] == 200)
    else:
        print("  skip  the phone's read-only check (no LAN address here)")

    print("limits and retirement in the library reach the logger:")
    lib = L.Library(LIB, create=False)
    g5 = next(r for r in lib.all("rail_limit", "gizmo-racer") if r["data"]["rail"] == "+5V")
    lib.put("rail_limit", g5["id"], dict(g5["data"], hi="5.2"), g5["rev"])
    check("a specs-file limit whose numbers are edited loses its confirmation, so the logger stops using it",
          settles(lambda: "+5V" not in (exported().get("gizmo-racer", {}).get("rails") or {}), 20)
          and "+5V" not in (windows("gizmo-racer") or {}))
    lib.put("rail_limit", "ff" * 16, {"model_key": "widget-wars", "rail": "+5V", "lo": "4.8", "hi": "5.2", "doc_id": "widget-wars-manual",
                                     "page": 3, "quote": "+5 VDC 4.8 to 5.2 V, newer page"}, 0)
    c, _ = call("POST", "/api/specs/widget-wars/confirm", {"rail": "+5V", "doc": "widget-wars-manual", "page": 3})
    # the first +5V manual limit was confirmed when the deck was seeded, the second just now: the later confirmation is in force
    check("two confirmed manual limits for one rail: the most recently confirmed is the one in force",
          c == 200 and exported()["widget-wars"]["rails"]["+5V"] == [4.8, 5.2] and windows("widget-wars")["+5V"] == [4.8, 5.2])
    gz = lib.get("game", "gizmo-racer")
    lib.put("game", "gizmo-racer", dict(gz["data"], replaced_by="widget-wars"), gz["rev"])
    ov = get("/api/manuals")["machines"]
    check("... and the retired game leaves the games list, while asking for it by its old key gives the survivor",
          "gizmo-racer" not in {g["key"] for g in get("/api/library")["games"]}
          and get("/api/library/games/gizmo-racer")["key"] == "widget-wars")
    check("a game retired into another: its machine reads the survivor's documents and the logger gets the survivor's limits",
          get("/api/manuals/gizmo-racer")["library"] == "widget-wars" and ov["gizmo-racer"] == ov["widget-wars"]
          and settles(lambda: windows("gizmo-racer") == windows("widget-wars"), 20))

    print("the logger never opens the database:")
    src = [os.path.join(REPO, "backend", "gatboxlib", "profiles.py"), os.path.join(REPO, "backend", "gatboxlib", "modes.py"),
           os.path.join(REPO, "backend", "gatboxlib", "csvlog.py"), os.path.join(REPO, "backend", "gatbox-raillog")]
    text = "".join(open(p).read() for p in src if os.path.isfile(p))
    check("profiles, modes, csvlog and gatbox-raillog import neither sqlite3 nor the library", "sqlite3" not in text
          and "import library" not in text and "libview" not in text and "libsource" not in text)

    log = open(f"{T}/web.log").read()
    check("server log: no errors", "Traceback" not in log)
    print(f"\nlibrary web: {passed} passed, {failed} failed")


try:
    main()
finally:
    if web:
        web.terminate()
        try:
            web.wait(10)
        except subprocess.TimeoutExpired:
            web.kill()
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
