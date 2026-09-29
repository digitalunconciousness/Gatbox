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


try:
    main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
