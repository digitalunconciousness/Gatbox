#!/usr/bin/env python3
"""Each machine's ROM chips from MAME (2026-09-29): gatbox-mame-roms builds the checklist from `mame -listxml` and the
machine -> set list (a fake MAME with a cut-down driver list: parent + clones, a device, a BIOS, a hard-disk game, a
chip MAME has no dump of), knows when it's stale (MAME or the list changed), prints a machine's checklist ticked from
the dump archive; GET /api/mame/<slug> ticks chips wherever they were archived and picks the version to show first.
With the real MAME on this Pi, the SegaSonic set is built for real and its epr-15781c.ic18 carries the SHA-1 of the
owner's dump. Temp dirs and a spare port only.
    python3 tests/test-mame.py        (GATBOX_TEST_REAL_MAME=0 skips the real-MAME build, ~1-2 min)
"""
import glob
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
TOOL = os.path.join(REPO, "tools/gatbox-mame-roms")
T = tempfile.mkdtemp(prefix="gatbox-mame-")
PORT = 8091
B = f"http://127.0.0.1:{PORT}"
SONIC_18 = "9f524012a7adbc71737f90fc556f0ce9adc2bcf8"      # epr-15781c.ic18 (MAME's hash = the owner's dump)
SONIC_17 = "8f173cd5c7c817dcccdcad9be5781cfaa081d73e"      # epr-15787c.ic17
passed = failed = 0
procs = []

MAPPING = {"meta": {"about": "test"}, "machines": {
    "segasonic-the-hedgehog": {"set": "sonic", "status": "sure"},
    "area-51": {"set": "area51", "prefer": "area51t", "status": "check", "why": "which revision?"},
    "the-swarm": {"set": None, "why": "PC-based (hard drive): no ROM chips in MAME"},
    "gone-set": {"set": "nosuchset", "status": "sure"}}}
ROSTER = {"meta": {"platforms": {}}, "pinball": [], "retired": [],
          "video_games": [{"slug": s, "name": n, "platform": "x", "mfr": "x", "risk": [], "faults": [], "parts": [], "notes": ""}
                          for s, n in (("segasonic-the-hedgehog", "SegaSonic The Hedgehog"), ("area-51", "Area 51"),
                                       ("the-swarm", "The Swarm"), ("gone-set", "Gone Set"), ("gauntlet", "Gauntlet"))]}


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def api(path):
    try:
        with urllib.request.urlopen(B + path, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def tool(*args, **env):
    e = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"),
             GATBOX_ROMS=f"{T}/roms")
    e.update(env)
    return subprocess.run(["python3", TOOL, *args], capture_output=True, text=True, env=e, timeout=600)


def sidecar(machine, label, sha1):
    d = f"{T}/roms/{machine}"
    os.makedirs(d, exist_ok=True)
    with open(f"{d}/{label}_{sha1[:8]}.json", "w") as f:
        json.dump({"label": label, "sha1": sha1, "date": "2026-09-29T01:38:07-05:00"}, f)


def main():
    os.makedirs(f"{T}/data")
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    json.dump(ROSTER, open(f"{T}/data/gatbox-barcade-roster.json", "w"))
    json.dump(MAPPING, open(f"{T}/data/gatbox-mame-sets.json", "w"), indent=2)
    out = f"{T}/mame-roms.json"

    print("build (fake MAME):")
    check("--check before a build: stale", tool("--check", out).returncode == 1)
    r = tool("--build", out)
    check("builds", r.returncode == 0 and "4 machines, 2 with a MAME set (4 versions)" in r.stdout)
    d = json.load(open(out))
    M = d["machines"]
    sonic = M["segasonic-the-hedgehog"]
    check("sonic: the parent first, then its clone", [v["name"] for v in sonic["versions"]] == ["sonic", "sonicp"]
          and sonic["versions"][0]["parent"] and not sonic["versions"][1]["parent"])
    roms = {r["name"]: r for r in sonic["versions"][0]["roms"]}
    check("each chip: name, size in bytes, CRC, SHA-1, region",
          roms["epr-15781c.ic18"] == {"name": "epr-15781c.ic18", "size": 262144, "crc": "65b06c25", "sha1": SONIC_18,
                                      "region": "mainpcb:maincpu"})
    check("a chip MAME has no dump of: no hash, status nodump", "sha1" not in roms["mpr-15790.ic36"]
          and roms["mpr-15790.ic36"]["status"] == "nodump")
    check("the clone keeps the chip it shares with the parent", SONIC_18 in [r["sha1"] for r in sonic["versions"][1]["roms"]])
    check("description, maker, year", sonic["versions"][0]["desc"] == "SegaSonic The Hedgehog (Japan, rev. C)"
          and sonic["versions"][0]["mfr"] == "Sega" and sonic["versions"][0]["year"] == "1992")
    a51 = M["area-51"]
    check("a hard-disk game: its disk image listed, status/why/prefer kept",
          a51["versions"][0]["disks"] == [{"name": "area51", "sha1": "2" * 40, "region": "ide:0:hdd"}]
          and (a51["status"], a51["why"], a51["prefer"]) == ("check", "which revision?", "area51t"))
    check("no MAME set: none + why", M["the-swarm"]["set"] is None and M["the-swarm"]["status"] == "none"
          and "PC-based" in M["the-swarm"]["why"] and "versions" not in M["the-swarm"])
    check("a set this MAME doesn't have: missing", M["gone-set"]["status"] == "missing" and M["gone-set"]["versions"] == [])
    check("devices, BIOSes and unmapped sets left out", not any(v["name"] in ("z80", "neogeo", "pacman")
                                                                 for m in M.values() for v in m.get("versions", [])))
    check("meta: the MAME version, no ROM data", d["meta"]["mame"] == "0.276 (mame0276)" and "Hashes only" in d["meta"]["about"])

    print("stale or not:")
    check("--check right after the build: up to date", tool("--check", out).returncode == 0)
    check("another MAME version: stale", tool("--check", out, FAKE_MAME_VERSION="0.277 (mame0277)").returncode == 1)
    MAPPING["machines"]["gauntlet"] = {"set": None, "why": "test"}
    json.dump(MAPPING, open(f"{T}/data/gatbox-mame-sets.json", "w"), indent=2)
    check("the list changed: stale", tool("--check", out).returncode == 1)
    del MAPPING["machines"]["gauntlet"]
    json.dump(MAPPING, open(f"{T}/data/gatbox-mame-sets.json", "w"), indent=2)
    check("back as it was: up to date again", tool("--check", out).returncode == 0)
    check("no MAME at all: stale, not a crash", tool("--check", out, GATBOX_MAME="/nonexistent/mame").returncode == 1)

    print("the archive ticks (terminal):")
    sidecar("segasonic-the-hedgehog", "EPR-15781C", SONIC_18)
    sidecar("unassigned", "EPR-15787C", SONIC_17)
    r = tool("segasonic-the-hedgehog", "--from", out)
    lines = r.stdout.splitlines()
    check("✓ on both dumped chips, with the label and where it's archived",
          any(l.startswith("  ✓ epr-15781c.ic18") and "EPR-15781C (segasonic-the-hedgehog)" in l for l in lines)
          and any(l.startswith("  ✓ epr-15787c.ic17") and "(unassigned)" in l for l in lines))
    check("... 2/4 dumped on the parent, · on the rest", "2/4 dumped" in r.stdout
          and any(l.startswith("  · epr-15786c.ic8") for l in lines))
    check("a disk game says it's not a T48 job", "not a T48 job" in tool("area-51", "--from", out).stdout)

    print("GET /api/mame/<slug>:")
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data", GATBOX_ROMS=f"{T}/roms",
               GATBOX_MAME_ROMS=out, MPLCONFIGDIR=f"{T}/cache/mpl")
    for x in ("ctrl", "cache", "log", "run"):
        os.makedirs(f"{T}/{x}", exist_ok=True)
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    procs.append(subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                  stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT))
    for _ in range(50):
        try:
            api("/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)
    c, j = api("/api/mame/segasonic-the-hedgehog")
    v0 = j["versions"][0]
    r18 = next(r for r in v0["roms"] if r["name"] == "epr-15781c.ic18")
    r17 = next(r for r in v0["roms"] if r["name"] == "epr-15787c.ic17")
    check("the set, its status, MAME's version", c == 200 and (j["set"], j["status"], j["mame"]) == ("sonic", "sure", "0.276 (mame0276)"))
    check("a chip dumped for this machine: ticked with its label", [x["label"] for x in r18["dumped"]] == ["EPR-15781C"]
          and r18["dumped"][0]["machine"] == "segasonic-the-hedgehog" and r18["dumped"][0]["file"] == "EPR-15781C_9f524012.bin")
    check("a chip archived as unassigned counts too, and says where", r17["dumped"][0]["machine"] == "unassigned")
    check("counts per version; first shown: the best-matched (the parent: 2 vs 1)",
          (v0["dumped"], j["versions"][1]["dumped"], j["default"]) == (2, 1, "sonic"))
    c, j = api("/api/mame/area-51")
    check("no dumps: the list's 'prefer' version first", c == 200 and j["default"] == "area51t" and j["why"] == "which revision?")
    sidecar("area-51", "A51-U11", "1" * 40)
    check("... until a dump matches another version", api("/api/mame/area-51")[1]["default"] == "area51")
    c, j = api("/api/mame/the-swarm")
    check("no MAME set: says why, no versions", c == 200 and j["set"] is None and j["status"] == "none" and j["versions"] == []
          and j["default"] is None)
    c, j = api("/api/mame/gauntlet")
    check("on the roster, not on the MAME list: unmapped", c == 200 and j["status"] == "unmapped")
    check("not on the roster -> 404", api("/api/mame/nope")[0] == 404 and api("/api/mame/..%2F..%2Fetc")[0] == 404)
    os.rename(out, out + ".away")
    check("no checklist built -> 503", api("/api/mame/segasonic-the-hedgehog")[0] == 503)
    os.rename(out + ".away", out)
    web = open(f"{T}/web.log").read()
    check("server log: no errors", "Traceback" not in web and "error on" not in web)

    print("the real MAME on this Pi:")
    if os.environ.get("GATBOX_TEST_REAL_MAME") == "0" or not os.access("/usr/games/mame", os.X_OK):
        print("  skip  (no /usr/games/mame, or GATBOX_TEST_REAL_MAME=0)")
    else:
        json.dump({"machines": {"segasonic-the-hedgehog": {"set": "sonic", "status": "sure"}}},
                  open(f"{T}/data/gatbox-mame-sets.json", "w"))
        real = f"{T}/real.json"
        r = tool("--build", real, GATBOX_MAME="/usr/games/mame", HOME=f"{T}/home")
        ok = r.returncode == 0 and os.path.exists(real)
        vs = json.load(open(real))["machines"]["segasonic-the-hedgehog"]["versions"] if ok else []
        par = next((v for v in vs if v["parent"]), {"roms": []})
        r18 = next((x for x in par["roms"] if x["name"] == "epr-15781c.ic18"), {})
        check(f"built from the real driver list: sonic + {max(0, len(vs) - 1)} other version(s)", ok and par.get("name") == "sonic")
        check("epr-15781c.ic18: 256 KB, the SHA-1 of the owner's dump", r18.get("size") == 262144 and r18.get("sha1") == SONIC_18)
        mine = [json.load(open(p)).get("sha1") for p in glob.glob("/srv/gatbox/roms/segasonic-the-hedgehog/*.json")]
        if mine:
            check("... and the archive on this Pi has that dump (it will tick)", SONIC_18 in mine)
    print(f"mame: {passed} passed, {failed} failed")


try:
    main()
finally:
    for p in procs:
        p.terminate()
        p.wait(10)
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
