#!/usr/bin/env python3
"""Phase B3: gatbox-web and the logger's limits read through the game library once the deck is seeded.
A real gatbox-web on an invented deck (tests/fakedeck.py): its answers before and after the seed are the same, CONFIRM and
UNCONFIRM go through the library and reach the logger as flat JSON, a change made from outside (the owner's CLI) reaches
the logger within seconds, a damaged export or a vanished library falls back to today's files, two cabinets the seed made one
game share their manuals and ROM list, and the logger's code path never opens the database.
    python3 tests/test-library-web.py
"""
import hashlib
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
import zlib

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
             GATBOX_SYNC_STATE=f"{T}/sync", MPLCONFIGDIR=f"{T}/cache/mpl", GATBOX_MAME_TITLES=f"{T}/mame-titles.json")
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


def add_dump(machine, label, data, epoch, match=None, matches=(), when=None):
    """An archived dump, as gatbox-dump leaves one: <machine>/<label>_<sha1:8>.bin and its sidecar."""
    sha1 = hashlib.sha1(data).hexdigest()
    base = f"{T}/roms/{machine}/{label}_{sha1[:8]}"
    write(base + ".bin", data, True)
    write(base + ".json", {"label": label, "part": "27C040@DIP32", "machine": machine, "size": len(data), "sha1": sha1,
                           "crc32": f"{zlib.crc32(data) & 0xffffffff:08x}", "epoch": epoch,
                           "date": when or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(1790000000 + epoch * 86400)),
                           "romident": {"match": match, "matches": list(matches)} if match is not None else {}})
    return f"{machine}/{label}_{sha1[:8]}.bin", sha1, hashlib.sha256(data).hexdigest()


def chk(image):
    c, j = call("GET", f"/api/library/check?image={image}")
    return j if c == 200 else {"error": j, "code": c}


def verdicts(j):
    return {c["against"]: c["verdict"] for c in j["checks"]}


def drop_library():
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(LIB + suffix):
            os.remove(LIB + suffix)


def main():
    build_deck(T, stale=False)
    subprocess.run([sys.executable, os.path.join(REPO, "tools/gatbox-mame-roms"), "--titles", f"{T}/mame-titles.json"], check=True,
                   capture_output=True, env=dict(os.environ, GATBOX_MAME=os.path.join(REPO, "tests/fake-mame")))
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
    sysl = get("/api/system")["library"]
    check("SYSTEM's library numbers before the seed: not set up, but the manuals' size and the room on /srv are there",
          sysl["seeded"] is False and sysl["manuals_bytes"] > 0 and sysl["roms_bytes"] > 0 and sysl["db_bytes"] == 0 and "games" not in sysl
          and "low" in sysl)
    first_img = next(f for f in sorted(os.listdir(f"{T}/roms/widget-wars")) if f.startswith("WW-U10_") and f.endswith(".bin"))
    j = chk(f"widget-wars/{first_img}")
    check("CHECK before the library is set up: MAME's answer, the machine's history and what was seen before all work; the "
          "game's chips say there's no library; a mark is refused",
          j["library"] is False and verdicts(j) == {"mame": "match", "game": "no_library", "images": "never_seen", "machine": "first"}
          and call("POST", f"/api/library/images/{j['sha256']}/mark", {"verdict": "good"})[0] == 409)
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

    print("SYSTEM's library numbers:")
    sysl = get("/api/system")["library"]
    check("seeded: games, documents on the Pi of those listed, limits confirmed of all, ROM images, the database's size",
          sysl["seeded"] and sysl["games"] == 4 and sysl["documents"] == 4 and sysl["fetched"] == 3 and sysl["limits"] == 3
          and sysl["confirmed"] == 2 and sysl["images"] == 3 and sysl["db_bytes"] > 0 and sysl["missing"] == 3)
    from gatboxweb import games as gw_games
    GB = 1000 ** 3
    check("running short: under 2 GB free, or 90% used, warns; room to spare doesn't",
          gw_games.is_low(1 * GB, 60 * GB)              # 1 GB free of 60
          and gw_games.is_low(5 * GB, 52 * GB)          # 5 GB free of 52: 90.4% used
          and gw_games.is_low(1.9 * 1024 ** 3, 500 * GB)
          and not gw_games.is_low(8 * GB, 64 * GB)      # 8 GB free of 64: 87.5% used
          and not gw_games.is_low(50 * GB, 100 * GB))

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

    print("CHECK: four comparisons, and the marks:")
    jw = chk(f"widget-wars/{first_img}")
    check("a dump that is the game's listed chip: MAME match, RIGHT CHIP, never seen before, the first dump of that chip",
          verdicts(jw) == {"mame": "match", "game": "right_chip", "images": "never_seen", "machine": "first"}
          and "RIGHT CHIP: ww1.u10" in jw["checks"][1]["text"] and jw["checks"][1]["word"] == "RIGHT CHIP" and [c["level"] for c in jw["checks"]] == ["ok", "ok", "mut", "mut"]
          and jw["library"] and jw["size"] == 2048 and len(jw["sha256"]) == 64)
    img_a, sha1_a, sha256_a = add_dump("widget-wars", "WW-U10", os.urandom(4096), 20)
    check("the dump archive's new dump becomes a library image on its own, within seconds (no mark yet)",
          settles(lambda: any(i["sha256"] == sha256_a for i in get("/api/library/games/widget-wars/images")["images"]), 20)
          and next(i for i in get("/api/library/games/widget-wars/images")["images"] if i["sha256"] == sha256_a)["verdict"] == "unknown")
    ja = chk(img_a)
    check("a later dump of the same chip with different bytes: CHANGED since the earlier one, and MAME no match / not in the list",
          verdicts(ja)["machine"] == "changed" and "CHANGED: since" in ja["checks"][3]["text"]
          and verdicts(ja)["mame"] == "unknown" and verdicts(ja)["game"] == "unlisted")
    img_b, _, sha256_b = add_dump("widget-wars", "ww-u10", os.urandom(4096), 30, match=False)
    check("NO MATCH in MAME is a warning, with its words", verdicts(chk(img_b))["mame"] == "no_match"
          and chk(img_b)["checks"][0]["level"] == "warn" and "NO MATCH" in chk(img_b)["checks"][0]["text"])
    same_bytes = os.urandom(512)
    img_aa, _, _ = add_dump("widget-wars", "AA-U1", same_bytes, 50)
    img_h, _, _ = add_dump("widget-wars", "aa-u1", same_bytes, 51)
    check("the same bytes under the same label (any case) as an earlier dump: SAME AS LAST TIME; the earlier one is a FIRST DUMP",
          verdicts(chk(img_h))["machine"] == "same" and verdicts(chk(img_aa))["machine"] == "first")
    lib = L.Library(LIB, create=False)
    rl = lib.get("rom_list", "widget-wars")
    data = json.loads(json.dumps(rl["data"]))
    other = hashlib.sha1(b"another revision's chip").hexdigest()
    data["versions"].append({"name": "widgetw12", "parent": False, "description": "Widget Wars (version 1.2)", "year": "1983",
                             "mfr": "Widgetco", "roms": [{"name": "ww1.u10", "size": 23, "crc": "00000000", "sha1": other}], "disks": []})
    lib.put("rom_list", "widget-wars", data, rl["rev"])
    img_d, _, _ = add_dump("widget-wars", "WW-REV", b"another revision's chip", 31, match=True,
                           matches=[{"set": "widgetw12", "rom": "ww1.u10", "description": "x"}])
    jd = chk(img_d)
    check("a chip of another revision of the same game: ANOTHER REVISION, naming it and the version MAME lists first",
          verdicts(jd)["game"] == "other_revision" and "ANOTHER REVISION: ww1.u10 of Widget Wars (version 1.2)" in jd["checks"][1]["text"])
    img_e, _, _ = add_dump("widget-wars", "ELSEWHERE", b"a chip of some other game", 32, match=True,
                           matches=[{"set": "othergame", "rom": "x.bin", "description": "Other Game"}])
    je = chk(img_e)
    check("a chip MAME knows as another game's: NOT THIS GAME, in red, saying which",
          verdicts(je)["game"] == "not_this_game" and je["checks"][1]["level"] == "bad" and "othergame" in je["checks"][1]["text"])
    img_f, _, _ = add_dump("unassigned", "LOOSE", b"loose chip bytes", 33)
    check("a dump from no machine: the machine has no game, and it says how to link one", verdicts(chk(img_f))["game"] == "no_game")
    img_g, _, _ = add_dump("ws-9", "SHARED", b"the same chip on two machines", 34)
    add_dump("widget-wars", "SHARED", b"the same chip on two machines", 35)
    jg = chk(img_g)
    check("the same bytes dumped on another machine too: SEEN BEFORE, naming where and when",
          verdicts(jg)["images"] == "seen_before" and "widget-wars" in jg["checks"][2]["text"] and "2026-" in jg["checks"][2]["text"])
    sha_a = jw["sha256"]
    c, _ = call("POST", f"/api/library/images/{sha_a}/mark", {"verdict": "good", "note": "read twice, matches the ROM set", "revision": "1.2"})
    jw2 = chk(f"widget-wars/{first_img}")
    check("MARK KNOWN GOOD: kept with its note and revision, and CHECK says so, with the words",
          c == 200 and jw2["mark"]["verdict"] == "good" and verdicts(jw2)["images"] == "known_good"
          and "read twice" in jw2["checks"][2]["text"] and jw2["checks"][2]["level"] == "ok" and jw2["mark"]["revision"] == "1.2")
    call("POST", f"/api/library/images/{sha256_b}/mark", {"verdict": "bad", "note": "reads differently each time"})
    check("MARK KNOWN BAD: in red", verdicts(chk(img_b))["images"] == "known_bad" and chk(img_b)["checks"][2]["level"] == "bad")
    check("the mark survives new dumps arriving (the watcher never changes a record that's there)",
          (add_dump("widget-wars", "LATER", b"a dump after the marks", 36) and settles(
              lambda: any(i["label"] == "LATER" for i in get("/api/library/games/widget-wars/images")["images"]), 20))
          and chk(f"widget-wars/{first_img}")["mark"]["verdict"] == "good")
    check("UNMARK: back to never seen", call("POST", f"/api/library/images/{sha_a}/mark", {"verdict": "unknown"})[0] == 200
          and verdicts(chk(f"widget-wars/{first_img}"))["images"] in ("never_seen", "seen_before"))
    check("bad marks and bad images: a verdict that isn't one, a hash that isn't one, a hash not in the library: 400 / 400 / 404",
          [call("POST", f"/api/library/images/{sha_a}/mark", {"verdict": "great"})[0],
           call("POST", "/api/library/images/xyz/mark", {"verdict": "good"})[0],
           call("POST", f"/api/library/images/{'0' * 64}/mark", {"verdict": "good"})[0]] == [400, 400, 404])
    check("CHECK with a path that isn't an archived dump: 400 for a traversal or a wrong name, 404 for a dump that isn't there",
          [call("GET", "/api/library/check?image=../etc/passwd")[0], call("GET", "/api/library/check?image=widget-wars/x.txt")[0],
           call("GET", "/api/library/check?image=widget-wars/NOPE_00000000.bin")[0], call("GET", "/api/library/check")[0],
           call("GET", "/api/library/check?image=../widget-wars/x.bin")[0], call("GET", "/api/library/check?image=widget-wars/../x.bin")[0]]
          == [400, 400, 404, 400, 400, 400])
    lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
    if lan:
        check("a mark from a phone on the network: 403; the check itself reads",
              call("POST", f"/api/library/images/{sha_a}/mark", {"verdict": "good"}, base=B.replace("127.0.0.1", lan))[0] == 403
              and call("GET", f"/api/library/check?image=widget-wars/{first_img}", base=B.replace("127.0.0.1", lan))[0] == 200)

    print("the title search (MAME's own list, offline):")
    mame_env = dict(os.environ, GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"))
    tool = [sys.executable, os.path.join(REPO, "tools/gatbox-mame-roms")]
    built = json.load(open(f"{T}/mame-titles.json"))
    check("gatbox-mame-roms --titles: every game MAME lists, and no device or BIOS set",
          [r[0] for r in built["titles"]] == ["sonic", "sonicp", "area51", "area51t", "pacman"] and built["meta"]["count"] == 5
          and built["meta"]["mame"] == "0.276 (mame0276)")
    check("--titles-check: up to date for this MAME, out of date for a newer one",
          subprocess.run(tool + ["--titles-check", f"{T}/mame-titles.json"], env=mame_env, check=False).returncode == 0
          and subprocess.run(tool + ["--titles-check", f"{T}/mame-titles.json"], env=dict(mame_env, FAKE_MAME_VERSION="0.277"),
                             check=False).returncode == 1)
    t = get("/api/library/titles?q=pac+man")
    check("search 'pac man': the game, its title without the maker's parenthesis, the key it would get, and its MAME set",
          t["built"] and t["mame"] == "0.276 (mame0276)" and t["results"][0]["set"] == "pacman" and t["results"][0]["title"] == "Pac-Man"
          and t["results"][0]["key"] == "pac-man" and t["results"][0]["year"] == 1980 and t["results"][0]["in_library"] is None)
    t = get("/api/library/titles?q=sonic")
    check("search 'sonic': the parent before its clone; the version in the description isn't part of the title",
          [r["set"] for r in t["results"]] == ["sonic", "sonicp"] and t["results"][0]["title"] == "SegaSonic The Hedgehog"
          and t["results"][1]["clone_of"] == "sonic")
    check("search with one letter: 400; with nothing in MAME's list, or only some of the words: no results; capitals and hyphens don't matter",
          call("GET", "/api/library/titles?q=a")[0] == 400 and get("/api/library/titles?q=zzzzzz")["results"] == []
          and get("/api/library/titles?q=sonic+zzzz")["results"] == []
          and get("/api/library/titles?q=AREA-51")["results"][0]["set"] == "area51")
    from gatboxweb import config as gw_config
    from gatboxweb import titles as gw_titles
    keep = gw_config.MAME_TITLES
    gw_config.MAME_TITLES = f"{T}/no-such-titles.json"
    unbuilt = gw_titles.search("pac")
    gw_config.MAME_TITLES = keep
    check("before the list is built: it says so and why, and finds nothing", unbuilt["built"] is False and unbuilt["results"] == []
          and "install builds it" in unbuilt["why"])
    c, g = call("POST", "/api/library/games", {"title": "Pac-Man", "mfr": "Namco (Midway license)", "year": 1980, "kind": "video",
                                               "key": "pac-man", "mame_set": "pacman"})
    mm = L.Library(LIB, create=False).get("mame", "pac-man")
    check("ADD GAME from a MAME hit: the game, and its MAME set noted for a dump to settle",
          c == 201 and g["key"] == "pac-man" and g["mame"] == "pacman" and mm["data"]["set"] == "pacman" and mm["data"]["status"] == "check")
    again = get("/api/library/titles?q=pac+man")["results"][0]
    check("search again: the game is already in the library, found by its MAME set; its natural key is taken, so a new one would get the maker",
          again["in_library"] == "pac-man" and again["key"] == "pac-man-namco-midway-license")

    print("linking a machine to a game:")
    c, r = call("POST", "/api/library/games/widget-wars/machines", {"slug": "ws-9"})
    check("a machine already linked to another game: 409, naming it; nothing changed", c == 409 and "ws-9" in r["error"]
          and [m["slug"] for m in get("/api/library/games/ws-9")["machines"]] == ["ws-9"])
    c, g = call("POST", "/api/library/games/widget-wars/machines", {"slug": "ws-9", "replace": True})
    check("replacing it on purpose: the machine is now that game's, and reads its manuals", c == 200
          and "ws-9" in [m["slug"] for m in g["machines"]] and get("/api/manuals/ws-9")["docs"] == get("/api/manuals/widget-wars")["docs"])
    check("... and its limits reach the logger: the same windows as the game's machine, at once",
          windows("ws-9") == windows("widget-wars") and windows("ws-9"))
    c, _ = call("DELETE", "/api/library/games/widget-wars/machines/ws-9")
    check("UNLINK: the machine is its own game again and the logger drops the limits it borrowed",
          c == 200 and get("/api/manuals/ws-9")["docs"] == [] and not windows("ws-9"))
    check("unlinking what isn't linked: 404; a machine not on the roster: 404; a game that isn't there: 404",
          [call("DELETE", "/api/library/games/widget-wars/machines/ws-9")[0],
           call("POST", "/api/library/games/widget-wars/machines", {"slug": "nope"})[0],
           call("POST", "/api/library/games/nope/machines", {"slug": "ws-9"})[0]] == [404, 404, 404])
    lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
    if lan:
        check("from a phone on the network: link and unlink are 403",
              call("POST", "/api/library/games/widget-wars/machines", {"slug": "ws-9"}, base=B.replace("127.0.0.1", lan))[0] == 403
              and call("DELETE", "/api/library/games/widget-wars/machines/ws-9", base=B.replace("127.0.0.1", lan))[0] == 403)

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
