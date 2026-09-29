#!/usr/bin/env python3
"""M7: gatbox-dump against stand-ins for minipro and MAME (tests/fake-minipro, tests/fake-mame): no programmer, no
chip, temp dirs only. Every rule from the spec: exact part names (search, refusal), label rules, pin check (ok /
not supported / bad), two reads compared (flaky = stop), chip-ID mismatch (stop with minipro's suggestion; only
--ignore-id passes -y), blank all-FF / all-00, the non-JEDEC 27C1000 confirmation, MATCH / NO MATCH, the archive
(sidecar, never overwrite, same SHA-1 = already archived), --json. Plus one real `mame -romident` run on random
bytes (NO MATCH), when MAME is installed.
    python3 tests/test-dump.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(REPO, "tools/gatbox-dump")
T = tempfile.mkdtemp(prefix="gatbox-dump-test-")
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def dump(*args, **env):
    e = dict(os.environ, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"), GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"),
             GATBOX_ROMS=f"{T}/roms", GATBOX_API="http://127.0.0.1:9", FAKE_ROM=f"{T}/rom.bin", FAKE_COUNTER=f"{T}/count")
    e.update({k: str(v) for k, v in env.items()})
    if os.path.exists(f"{T}/count"):
        os.remove(f"{T}/count")
    r = subprocess.run(["python3", DUMP] + list(args), capture_output=True, text=True, env=e, timeout=60)
    return r.returncode, r.stdout + r.stderr


def files(machine):
    d = f"{T}/roms/{machine}"
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def main():
    rom = os.urandom(262144)
    open(f"{T}/rom.bin", "wb").write(rom)
    sha = hashlib.sha1(rom).hexdigest()
    open(f"{T}/ff.bin", "wb").write(b"\xff" * 65536)
    open(f"{T}/00.bin", "wb").write(b"\x00" * 65536)

    print("part names:")
    code, out = dump("--search", "27C020")
    check("--search lists every 27C020 variant", code == 0 and out.split()[-3:] == ["27C020@DIP32", "TMS27C020@DIP32", "AM27C020@DIP32"])
    code, out = dump("-p", "27C999@DIP32", "-l", "X")
    check("an unknown part is refused", code == 2 and "isn't a T48 part name" in out)
    code, out = dump("-p", "27C02", "-l", "X")
    check("... with the near names listed", code == 2 and "TMS27C020@DIP32" in out)
    code, out = dump("-p", "27C020@DIP32", "-l", "bad label")
    check("a label with a space is refused", code == 2 and "label:" in out)

    print("a good read:")
    code, out = dump("-p", "27C020@DIP32", "-l", "EPR-15781C", "-m", "sonic-test", FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic The Hedgehog (Japan, rev. C)")
    check("MATCH printed with set/rom", code == 0 and "MATCH: sonic/epr-15781c.ic18 (SegaSonic The Hedgehog (Japan, rev. C))" in out)
    check("archived as <label>_<sha1:8>.bin + .json", files("sonic-test") == [f"EPR-15781C_{sha[:8]}.bin", f"EPR-15781C_{sha[:8]}.json"])
    side = json.load(open(f"{T}/roms/sonic-test/EPR-15781C_{sha[:8]}.json"))
    check("the archived bytes are the chip's", open(f"{T}/roms/sonic-test/EPR-15781C_{sha[:8]}.bin", "rb").read() == rom)
    check("sidecar: part, size, sha1, crc32, reads, pin/id checks, programmer",
          side["part"] == "27C020@DIP32" and side["size"] == 262144 and side["sha1"] == sha and len(side["crc32"]) == 8
          and side["reads_compared"] == 2 and side["identical"] and side["pin_check"] == "ok" and side["id_check"] == "ok"
          and side["programmer"]["minipro"] == "0.7.4" and side["romident"]["matches"][0]["set"] == "sonic")
    check("no gatbox-web: machine from -m, clock 'unknown'", side["machine"] == "sonic-test" and side["clock"] == "unknown")
    code, out = dump("-p", "27C020@DIP32", "-l", "AGAIN", "-m", "sonic-test")
    check("the same chip again: already archived, nothing new", code == 0 and "already archived" in out and len(files("sonic-test")) == 2)
    code, out = dump("-p", "27C020@DIP32", "-l", "X1", FAKE_MAME_MATCH="")
    check("no -m and no gatbox-web: 'unassigned', NO MATCH", code == 0 and "NO MATCH" in out and len(files("unassigned")) == 2)

    print("what stops a dump:")
    code, out = dump("-p", "27C020@DIP32", "-l", "FLAKY", "-m", "m1", FAKE_FLAKY=1)
    check("the two reads differ: STOPPED, reseat, nothing archived",
          code == 2 and "the two reads differ (1 bytes)" in out and "reseat" in out and files("m1") == [])
    code, out = dump("-p", "27C020@DIP32", "-l", "ID", "-m", "m2", FAKE_ID="mismatch")
    check("chip ID mismatch: STOPPED with minipro's message and its suggestion",
          code == 2 and "Invalid Chip ID: expected 0x8934, got 0x9732" in out and "-p TMS27C020@DIP32" in out and files("m2") == [])
    code, out = dump("-p", "27C020@DIP32", "-l", "ID", "-m", "m2", "--ignore-id", FAKE_ID="mismatch")
    side = json.load(open(f"{T}/roms/m2/" + [f for f in files("m2") if f.endswith(".json")][0]))
    check("--ignore-id reads (-y) and the sidecar says so", code == 0 and side["id_check"].startswith("ignored (--ignore-id)"))
    code, out = dump("-p", "27C020@DIP32", "-l", "PIN", "-m", "m3", FAKE_PIN="bad")
    check("bad pin contact: STOPPED before reading", code == 2 and "pin check" in out and "reseat" in out and files("m3") == [])
    code, out = dump("-p", "27C020@DIP32", "-l", "PIN", "-m", "m3", FAKE_PIN="unsupported")
    side = json.load(open(f"{T}/roms/m3/" + [f for f in files("m3") if f.endswith(".json")][0]))
    check("pin test not supported for the part: carries on, noted", code == 0 and side["pin_check"] == "not supported for this part")
    code, out = dump("-p", "27C020@DIP32", "-l", "BLANK", "-m", "m4", FAKE_ROM=f"{T}/ff.bin")
    check("all 0xFF: STOPPED (blank, or no contact), not archived", code == 2 and "all 0xFF" in out and files("m4") == [])
    code, out = dump("-p", "27C020@DIP32", "-l", "ZERO", "-m", "m4", FAKE_ROM=f"{T}/00.bin")
    check("all 0x00: STOPPED", code == 2 and "all 0x00" in out and files("m4") == [])
    code, out = dump("-p", "27C020@DIP32", "-l", "BLANK", "-m", "m4", "--keep-blank", FAKE_ROM=f"{T}/ff.bin")
    check("--keep-blank archives it, marked blank", code == 0 and json.load(open(f"{T}/roms/m4/" + [f for f in files("m4") if f.endswith(".json")][0]))["blank"].startswith("all 0xFF"))
    code, out = dump("-p", "27C1000@DIP32", "-l", "NJ", "-m", "m5")
    check("27C1000: non-JEDEC warning, needs --yes", code == 2 and "non-JEDEC" in out and "--yes" in out and files("m5") == [])
    code, out = dump("-p", "27C1000@DIP32", "-l", "NJ", "-m", "m5", "--yes")
    check("... and reads with --yes", code == 0 and len(files("m5")) == 2)
    code, out = dump("-p", "27C020@DIP32", "-l", "NOCHIP", "-m", "m6", FAKE_ROM=f"{T}/missing.bin")
    check("a failed read: STOPPED, minipro's words shown", code == 2 and "read 1 failed" in out and "IO error" in out)
    os.makedirs(f"{T}/roms/m7")
    open(f"{T}/roms/m7/TAKEN_{sha[:8]}.bin", "wb").write(b"something else")
    code, out = dump("-p", "27C020@DIP32", "-l", "TAKEN", "-m", "m7")
    check("never overwrites: a different file with that name stops it",
          code == 2 and "already exists" in out and open(f"{T}/roms/m7/TAKEN_{sha[:8]}.bin", "rb").read() == b"something else")

    print("--json:")
    code, out = dump("-p", "27C020@DIP32", "-l", "J", "-m", "m8", "--json")
    j = json.loads(out.strip().splitlines()[-1])
    check("one JSON object: state done, steps, archived path", code == 0 and j["state"] == "done" and j["archived"]["new"]
          and [s["step"] for s in j["steps"]][:3] == ["pin check", "read 1 of 2", "read 2 of 2"])
    code, out = dump("-p", "27C020@DIP32", "-l", "J", "-m", "m9", "--json", FAKE_FLAKY=1)
    j = json.loads(out.strip().splitlines()[-1])
    check("... and for a stop: state stopped, error, hint", code == 2 and j["state"] == "stopped" and "differ" in j["error"] and j["hint"])

    print("job mode (gatbox-dump.service):")
    sp = f"{T}/spool"
    os.makedirs(sp)
    json.dump({"id": "j1", "part": "TMS27C020@DIP32", "label": "JOB-1", "machine": "m10"}, open(f"{sp}/request.json", "w"))
    code, out = dump("--job", f"{sp}/request.json", "--status", f"{sp}/status.json", FAKE_MAME_MATCH="epr-x.ic1 sonic SegaSonic")
    st = json.load(open(f"{sp}/status.json"))
    check("the request is claimed and gone; status done, MATCH, archived",
          code == 0 and not os.path.exists(f"{sp}/request.json") and not os.path.exists(f"{sp}/running.json")
          and st["state"] == "done" and st["romident"]["match"] and st["job"] == "j1" and len(files("m10")) == 2)
    json.dump({"id": "j2", "part": "27C020@DIP32", "label": "JOB-2", "machine": "m10"}, open(f"{sp}/request.json", "w"))
    code, out = dump("--job", f"{sp}/request.json", "--status", f"{sp}/status.json", FAKE_ID="mismatch")
    st = json.load(open(f"{sp}/status.json"))
    check("a stop is a result (exit 0): state stopped, error, the suggested part",
          code == 0 and st["state"] == "stopped" and "Invalid Chip ID" in st["error"] and "TMS27C020@DIP32" in st["hint"])
    json.dump({"id": "j3", "part": "27C020@DIP32", "label": "../x", "machine": "m10"}, open(f"{sp}/request.json", "w"))
    code, out = dump("--job", f"{sp}/request.json", "--status", f"{sp}/status.json")
    check("a bad request (label) never reaches the programmer", code == 0 and json.load(open(f"{sp}/status.json"))["state"] == "stopped")
    code, out = dump("--job", f"{sp}/request.json")
    check("no request: nothing to do", code == 0)

    web_side()

    print("data/eproms.json vs the real minipro:")
    if shutil.which("minipro"):
        real = subprocess.run(["minipro", "-q", "T48", "-l"], capture_output=True, text=True, timeout=60).stdout.split()
        fams = json.load(open(os.path.join(REPO, "data/eproms.json")))["families"]
        empty = [t for f in fams for t in f["search"]
                 if not any(t.lower() in n.lower() and ("@" not in n or "@DIP" in n.upper()) for n in real)]
        check(f"every family's search terms match DIP parts ({sum(len(f['search']) for f in fams)} terms)", empty == [])
    else:
        print("  skip  (minipro not installed)")

    print("real MAME:")
    if shutil.which("mame"):
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("gdump", DUMP)
        spec = importlib.util.spec_from_loader("gdump", loader)
        mod = importlib.util.module_from_spec(spec)
        os.environ["GATBOX_MAME"] = "mame"
        loader.exec_module(mod)
        r = mod.romident(os.urandom(4096))
        check("mame -romident on random bytes: NO MATCH, parsed", r == {"match": False, "matches": []})
    else:
        print("  skip  (mame not installed)")
    print(f"dump: {passed} passed, {failed} failed")


def t48_present():
    import glob as g
    return any(open(p).read().strip() == "a466" for p in g.glob("/sys/bus/usb/devices/*/idVendor"))


def web_side():
    """gatbox-web's DUMP API with a runner thread standing in for gatbox-dump.path + .service."""
    import socket
    import threading
    import time
    import urllib.error
    import urllib.request
    print("gatbox-web's DUMP API:")
    port, B = 8099, "http://127.0.0.1:8099"
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is in use")
    w = f"{T}/web"
    for d in ("log", "run", "ctrl", "cache", "data", "spool", "roms"):
        os.makedirs(f"{w}/{d}")
    for f in ("profiles.json", "gatbox-machine-specs.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{w}/data/")
    json.dump({"meta": {}, "video_games": [{"slug": "segasonic-the-hedgehog", "name": "SegaSonic The Hedgehog", "platform": "x"}],
               "pinball": []}, open(f"{w}/data/gatbox-barcade-roster.json", "w"))
    fake = dict(os.environ, FAKE_PARTS="27C020@DIP32,TMS27C020@DIP32,AM27C020@DIP32,M27C801,M27C801@PLCC32")
    names = subprocess.run([os.path.join(REPO, "tests/fake-minipro"), "-q", "T48", "-l"], capture_output=True, text=True, env=fake).stdout
    open(f"{w}/parts.txt", "w").write("# minipro 0.7.4 T48\n" + names)
    env = dict(os.environ, GATBOX_WEB_PORT=str(port), STATE_DIRECTORY=f"{w}/ctrl", CACHE_DIRECTORY=f"{w}/cache",
               GATBOX_LOGDIR=f"{w}/log", GATBOX_RUNDIR=f"{w}/run", GATBOX_DATA=f"{w}/data", GATBOX_DUMP_SPOOL=f"{w}/spool",
               GATBOX_ROMS=f"{w}/roms", GATBOX_MINIPRO_PARTS=f"{w}/parts.txt", MPLCONFIGDIR=f"{w}/cache/mpl")
    web = subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env, stdout=open(f"{w}/web.log", "w"),
                           stderr=subprocess.STDOUT)
    stop = threading.Event()

    def runner():                                   # gatbox-dump.path: a request appears → gatbox-dump --job
        while not stop.is_set():
            if os.path.exists(f"{w}/spool/request.json"):
                subprocess.run(["python3", DUMP, "--job", f"{w}/spool/request.json", "--status", f"{w}/spool/status.json"],
                               env=dict(fake, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"),
                                        GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"), GATBOX_ROMS=f"{w}/roms",
                                        GATBOX_API=B, FAKE_ROM=f"{T}/rom.bin", FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic"),
                               capture_output=True, timeout=60)
            time.sleep(0.2)
    threading.Thread(target=runner, daemon=True).start()

    def call(method, path, body=None):
        req = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")
    try:
        for _ in range(50):
            try:
                call("GET", "/kiosk/state")
                break
            except OSError:
                time.sleep(0.1)
        code, r = call("GET", "/api/dump/parts")
        check("families from data/eproms.json (14)", code == 200 and len(r["families"]) == 14 and r["installed"])
        code, r = call("GET", "/api/dump/parts?family=27C080")
        check("a family lists minipro's names, DIP first (M27C801 has no @)", r["parts"] == ["M27C801", "M27C801@PLCC32"])
        code, r = call("GET", "/api/dump/parts?q=tms27")
        check("free search", r["parts"] == ["TMS27C020@DIP32"])
        bad = [call("POST", "/api/dump", b)[0] for b in (
            {"part": "27C999@DIP32", "label": "X"}, {"part": "TMS27C020@DIP32", "label": "../x"},
            {"part": "TMS27C020@DIP32", "label": "X", "machine": "nope"})]
        check("bad part / label / machine -> 400 / 400 / 404", bad == [400, 400, 404])
        if t48_present():
            code, r = call("POST", "/api/dump", {"part": "TMS27C020@DIP32", "label": "EPR-15781C", "machine": "segasonic-the-hedgehog"})
            check("a good request -> 202, queued", code == 202 and r["part"] == "TMS27C020@DIP32")
            for _ in range(100):
                code, st = call("GET", "/api/dump")
                if not st["busy"] and st["status"] and st["status"].get("state") in ("done", "stopped"):
                    break
                time.sleep(0.2)
            check("the job ran: GET /api/dump says done, MATCH", st["status"]["state"] == "done" and st["status"]["romident"]["match"])
            code, r = call("GET", "/api/dumps?machine=segasonic-the-hedgehog")
            check("GET /api/dumps lists it", code == 200 and len(r["dumps"]) == 1 and r["dumps"][0]["match"]
                  and r["dumps"][0]["label"] == "EPR-15781C")
            open(f"{w}/spool/running.json", "w").write("{}")
            code, r = call("POST", "/api/dump", {"part": "TMS27C020@DIP32", "label": "X2"})
            check("one at a time: 409 while a dump runs", code == 409)
            os.remove(f"{w}/spool/running.json")
        else:
            print("  skip  (the T48 isn't plugged in: requests are refused without it)")
        check("server log: no errors", "Traceback" not in open(f"{w}/web.log").read())
    finally:
        stop.set()
        web.terminate()
        web.wait(10)


try:
    main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
