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
    check("--search lists every 27C020 variant", code == 0 and {"27C020@DIP32", "TMS27C020@DIP32", "AM27C020@DIP32",
          "TMS27C020@TSOP32", "TMS27C020@PLCC32"} <= set(out.split()))
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
    code, out = dump("-p", "27C020@DIP32", "-l", "NOMAME", "-m", "m0", GATBOX_MAME=f"{T}/no-such-mame")
    side0 = f"{T}/roms/m0/NOMAME_{sha[:8]}.json"
    check("MAME missing: archived anyway, identification null + the error",
          code == 0 and "MAME couldn't run" in out and json.load(open(side0))["romident"]["match"] is None)
    code, out = dump("-p", "27C020@DIP32", "-l", "NOMAME", "-m", "m0", FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic")
    s0 = json.load(open(side0))
    check("the same chip again with MAME: the sidecar gets the MATCH (the .bin untouched)",
          code == 0 and "identified now" in out and s0["romident"]["match"] and "identified" in s0 and len(files("m0")) == 2
          and open(f"{T}/roms/m0/NOMAME_{sha[:8]}.bin", "rb").read() == rom)
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
    code, out = dump("-p", "TMS27C020@TSOP32", "-l", "PKG", "-m", "m2b", FAKE_ID="unknown")
    check("a TSOP pick for a DIP chip (0xFEFF, unknown): the adapter note, and the DIP version to use",
          code == 2 and "got 0xFEFF (unknown)" in out and "needs a socket adapter" in out
          and "-p TMS27C020@DIP32" in out and "--ignore-id" not in out and files("m2b") == [])
    code, out = dump("-p", "OLD2716", "-l", "NOID", "-m", "m2b", FAKE_ID="unknown")
    check("no known ID from a DIP part: check the seating; --ignore-id only for a part with no ID",
          code == 2 and "seated" in out and "--ignore-id" in out and files("m2b") == [])
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

    burn_cli()

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
    burn_jobs(sp)

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


def burn(*args, inp="", **env):
    """gatbox-dump --burn against the fake chip (FAKE_CHIP), the part name typed on stdin."""
    e = dict(os.environ, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"), GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"),
             GATBOX_ROMS=f"{T}/roms", GATBOX_API="http://127.0.0.1:9", FAKE_CHIP=f"{T}/chip.bin", FAKE_COUNTER=f"{T}/count")
    e.update({k: str(v) for k, v in env.items()})
    if os.path.exists(f"{T}/count"):
        os.remove(f"{T}/count")
    r = subprocess.run(["python3", DUMP] + list(args), capture_output=True, text=True, env=e, timeout=60, input=inp)
    return r.returncode, r.stdout + r.stderr


def burns():
    p = f"{T}/roms/burns.jsonl"
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def burn_cli():
    """Burning a chip from the terminal: every stop leaves the chip as it was; the part name is typed to confirm."""
    print("burn (CLI):")
    img = os.urandom(262144)
    open(f"{T}/img.bin", "wb").write(img)
    open(f"{T}/small.bin", "wb").write(os.urandom(131072))
    blank = b"\xff" * 262144

    def chip(data=blank):
        open(f"{T}/chip.bin", "wb").write(data)

    def chip_now():
        return open(f"{T}/chip.bin", "rb").read()

    chip()
    code, out = burn("--burn", f"{T}/small.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n")
    check("an image the wrong size for the part: stopped before the chip is touched",
          code == 2 and "131,072 bytes" in out and "262,144" in out and chip_now() == blank)
    chip(img)
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n")
    check("a chip that isn't blank: stopped (UV-erase it first), chip unchanged",
          code == 2 and "not blank" in out and "UV" in out and chip_now() == img)
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C010@DIP32\n")
    check("the part name mistyped at the prompt: nothing written",
          code == 2 and "not burned" in out and chip_now() == blank)
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", "-m", "sonic-test", inp="27C020@DIP32\n")
    check("a good burn: blank check, write, minipro's verify, 2 read-backs = the image: VERIFIED",
          code == 0 and "VERIFIED" in out and chip_now() == img)
    last = burns()[-1]
    check("... logged in burns.jsonl: image, its SHA-1, part, VPP, verified",
          last["state"] == "verified" and last["sha1"] == hashlib.sha1(img).hexdigest() and last["part"] == "27C020@DIP32"
          and last["image"] == f"{T}/img.bin" and last["vpp"] == "12.5V" and last["machine"] == "sonic-test")
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n", FAKE_WRITE_FAIL=1)
    check("minipro's verify fails after the write: FAILED, with its address, logged",
          code == 2 and "Verification failed" in out and burns()[-1]["state"] == "stopped")
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n", FAKE_FLAKY=1)
    check("the read-backs disagree: FAILED (reseat and read it again)", code == 2 and "read-back" in out and "VERIFIED" not in out)
    chip()
    open(f"{T}/img1m.bin", "wb").write(os.urandom(131072))
    code, out = burn("--burn", f"{T}/img1m.bin", "-p", "27C1000@DIP32", inp="27C1000@DIP32\n")
    check("a non-JEDEC part needs --yes: stopped, nothing written", code == 2 and "non-JEDEC" in out and chip_now() == blank)
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n", FAKE_ID="mismatch")
    check("a chip-ID complaint: stopped with minipro's suggestion, nothing written",
          code == 2 and "TMS27C020@DIP32" in out and chip_now() == blank)
    code, out = burn("--burn", f"{T}/nope.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n")
    check("an image that isn't there: refused", code == 2 and "nope.bin" in out)
    # a 16-bit part: minipro 0.7.4 gives its size in words ("Memory: 262144 Words" = 524,288 bytes, read off this Pi)
    wide = dict(FAKE_PARTS="27C020@DIP32,AM27C4096@DIP40,ATF16V8B", FAKE_ORG="AM27C4096@DIP40=Words,ATF16V8B=Bits",
                FAKE_SIZES="27C020@DIP32=262144,AM27C4096@DIP40=524288,ATF16V8B=2194")
    img4 = os.urandom(524288)
    open(f"{T}/img4m.bin", "wb").write(img4)
    chip(b"\xff" * 524288)
    code, out = burn("--burn", f"{T}/img4m.bin", "-p", "AM27C4096@DIP40", inp="AM27C4096@DIP40\n", **wide)
    check("a 16-bit part (minipro: 'Memory: 262144 Words'): words x 2 = the image's 524,288 bytes, VERIFIED",
          code == 0 and "VERIFIED" in out and chip_now() == img4)
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "ATF16V8B", inp="ATF16V8B\n", **wide)
    check("a part minipro sizes in bits (a PLD, not an EPROM): refused, nothing written",
          code == 2 and "bits" in out.lower() and "EPROM" in out and chip_now() == blank)
    # anything unexpected once the write has started still ends as a logged stop that says the chip was written
    chip()
    n0 = len(burns())
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n", FAKE_WRITE_JUNK=1)
    check("an unexpected error after the write starts: STOPPED (no traceback), the chip was written, logged",
          code == 2 and "STOPPED" in out and "Traceback" not in out and "written" in out
          and len(burns()) == n0 + 1 and burns()[-1]["state"] == "stopped")
    # the burn log can't be written: the result still stands
    log = f"{T}/roms/burns.jsonl"
    os.rename(log, log + ".keep")
    os.makedirs(log)
    chip()
    code, out = burn("--burn", f"{T}/img.bin", "-p", "27C020@DIP32", inp="27C020@DIP32\n")
    check("the burn log can't be written: still VERIFIED, and it says the log wasn't written",
          code == 0 and "VERIFIED" in out and "Traceback" not in out and "log" in out.lower() and chip_now() == img)
    os.rmdir(log)
    os.rename(log + ".keep", log)


def burn_jobs(sp):
    """The dashboard's BLANK CHECK and BURN, as gatbox-dump.service runs them: op blank / op burn requests."""
    import time
    print("burn jobs (gatbox-dump.service):")
    os.makedirs(f"{T}/roms/_images", exist_ok=True)
    img = os.urandom(262144)
    open(f"{T}/roms/_images/diag.bin", "wb").write(img)
    image, blank = f"{T}/roms/_images/diag.bin", b"\xff" * 262144

    def job(req, **env):
        json.dump(req, open(f"{sp}/request.json", "w"))
        code, _ = burn("--job", f"{sp}/request.json", "--status", f"{sp}/status.json", **env)
        return code, json.load(open(f"{sp}/status.json"))

    def chip(data=blank):
        open(f"{T}/chip.bin", "wb").write(data)

    chip()
    code, st = job({"id": "b1", "op": "blank", "image": image, "part": "27C020@DIP32", "machine": "m10"})
    check("op blank on a blank chip: done, blank_ok, with the image's SHA-1 and the part",
          code == 0 and st["op"] == "blank" and st["state"] == "done" and st["blank_ok"] is True
          and st["sha1"] == hashlib.sha1(img).hexdigest() and st["part"] == "27C020@DIP32" and st["image"] == image
          and not os.path.exists(f"{sp}/request.json"))
    chip(img)
    code, st = job({"id": "b2", "op": "blank", "image": image, "part": "27C020@DIP32"})
    check("op blank on a programmed chip: stopped, blank_ok false", st["state"] == "stopped" and st["blank_ok"] is False)
    chip()
    code, st = job({"id": "b3", "op": "burn", "image": image, "part": "27C020@DIP32"})
    check("op burn with no arm: stopped, nothing written", st["state"] == "stopped" and "arm" in st["error"]
          and open(f"{T}/chip.bin", "rb").read() == blank)
    now = time.time()
    code, st = job({"id": "b4", "op": "burn", "image": image, "part": "27C020@DIP32",
                    "armed": {"by": "screen", "at": now - 60, "until": now - 30}})
    check("op burn with an expired arm: stopped, nothing written", st["state"] == "stopped" and "expired" in st["error"]
          and open(f"{T}/chip.bin", "rb").read() == blank)
    code, st = job({"id": "b5", "op": "burn", "image": f"{T}/img.bin", "part": "27C020@DIP32",
                    "armed": {"by": "screen", "at": now, "until": now + 30}})
    check("op burn with an image outside the archive: stopped, nothing written", st["state"] == "stopped"
          and "archive" in st["error"] and open(f"{T}/chip.bin", "rb").read() == blank)
    code, st = job({"id": "b6", "op": "burn", "image": image, "part": "27C020@DIP32", "machine": "m10",
                    "armed": {"by": "screen", "at": now, "until": now + 30}})
    check("op burn, armed: done, verified; the chip holds the image", code == 0 and st["op"] == "burn" and st["state"] == "done"
          and st["verified"] is True and open(f"{T}/chip.bin", "rb").read() == img)
    check("... logged with who armed it", burns()[-1]["state"] == "verified" and burns()[-1]["armed"]["by"] == "screen"
          and burns()[-1]["machine"] == "m10")
    check("... and the request is used up (claimed once)", not os.path.exists(f"{sp}/request.json")
          and not os.path.exists(f"{sp}/running.json"))
    chip()
    now = time.time()
    code, st = job({"id": "b7", "op": "burn", "image": image, "part": "27C020@DIP32",
                    "armed": {"by": "screen", "at": now, "until": now + 30}}, FAKE_FLAKY=1)
    check("a stop after the write (the read-backs disagree): the status says the chip was written",
          st["state"] == "stopped" and st.get("written") is True)
    log = f"{T}/roms/burns.jsonl"
    os.rename(log, log + ".keep")
    os.makedirs(log)
    code, st = job({"id": "b8", "op": "burn", "image": image, "part": "27C020@DIP32",
                    "armed": {"by": "screen", "at": now - 60, "until": now - 30}})
    check("the burn log can't be written: the job still ends with its result, the log's error beside it; not written",
          code == 0 and st["state"] == "stopped" and "expired" in st["error"] and st.get("log_error")
          and not st.get("written"))
    os.rmdir(log)
    os.rename(log + ".keep", log)


def burn_web(w, call, B):
    """gatbox-web's BURN API: the images it offers, BLANK CHECK, and the burn itself (the Pi's own screen only, after a
    recent blank check of the same image and part). The runner stands in for gatbox-dump.path."""
    import time
    import urllib.error
    import urllib.request
    print("gatbox-web's BURN API:")
    os.makedirs(f"{w}/roms/segasonic-the-hedgehog", exist_ok=True)
    os.makedirs(f"{w}/roms/_images", exist_ok=True)
    img = os.urandom(262144)
    sha = hashlib.sha1(img).hexdigest()
    open(f"{w}/roms/segasonic-the-hedgehog/EPR-15781C_{sha[:8]}.bin", "wb").write(img)
    json.dump({"label": "EPR-15781C", "sha1": sha, "size": 262144,
               "romident": {"match": True, "matches": [{"set": "sonic", "rom": "epr-15781c.ic18", "description": "x"}]}},
              open(f"{w}/roms/segasonic-the-hedgehog/EPR-15781C_{sha[:8]}.json", "w"))
    diag = os.urandom(131072)
    open(f"{w}/roms/_images/diag-rom.bin", "wb").write(diag)
    os.symlink("/etc/hostname", f"{w}/roms/_images/escape.bin")
    open(f"{w}/chip.bin", "wb").write(b"\xff" * 262144)
    code, r = call("GET", "/api/burn/images")
    got = {x["image"]: x for x in r.get("images", [])} if code == 200 else {}
    rel = f"segasonic-the-hedgehog/EPR-15781C_{sha[:8]}.bin"
    check("images: the archive's (with SHA-1 + MAME match) and _images/ (SHA-1 computed); nothing outside the archive",
          code == 200 and {rel, "_images/diag-rom.bin"} <= set(got) and "_images/escape.bin" not in got
          and all(k.split("/")[0] in ("segasonic-the-hedgehog", "_images") for k in got) and got[rel]["sha1"] == sha
          and got[rel]["matches"] == ["sonic/epr-15781c.ic18"] and got["_images/diag-rom.bin"]["size"] == 131072
          and got["_images/diag-rom.bin"]["sha1"] == hashlib.sha1(diag).hexdigest())
    bad = [call("POST", "/api/burn/blank", b)[0] for b in ({"image": "../../etc/passwd", "part": "27C020@DIP32"},
                                                             {"image": "_images/escape.bin", "part": "27C020@DIP32"},
                                                             {"image": rel, "part": "27C999@DIP32"})]
    check("blank check: an image not in the list or an unknown part -> 400", bad == [400, 400, 400])
    code, r = call("POST", "/api/burn", {"image": rel, "part": "27C020@DIP32"})
    # _queue checks the T48 before it runs the blank-check guard, so off the Pi the
    # refusal is the right code for the other reason. Assert whichever applies.
    want = "blank" if t48_present() else "t48"
    check(f"burn with no blank check first -> 409 ({want})",
          code == 409 and want in r["error"].lower())
    lan = next((a for a in _lan_addrs() if "." in a), None)
    if lan:
        req = urllib.request.Request(B.replace("127.0.0.1", lan) + "/api/burn", method="POST",
                                     data=json.dumps({"image": rel, "part": "27C020@DIP32"}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("burn from the network (a phone) -> 403: only the Pi's own screen arms", code == 403)
    if not t48_present():
        print("  skip  (the T48 isn't plugged in: blank checks and burns are refused without it)")
        return

    def wait():
        for _ in range(150):
            c, st = call("GET", "/api/dump")
            if not st["busy"] and st["status"] and st["status"].get("state") in ("done", "stopped"):
                return st["status"]
            time.sleep(0.2)
    code, r = call("POST", "/api/burn/blank", {"image": rel, "part": "27C020@DIP32", "machine": "segasonic-the-hedgehog"})
    st = wait()
    check("BLANK CHECK -> 202, the job says blank", code == 202 and st["op"] == "blank" and st["blank_ok"] is True)
    code, r = call("POST", "/api/burn", {"image": rel, "part": "TMS27C020@DIP32"})
    code2, r = call("POST", "/api/burn", {"image": "_images/diag-rom.bin", "part": "27C020@DIP32"})
    check("burn another part, or another image, than the blank check's -> 409 each", code == 409 and code2 == 409)
    t0 = time.time()
    code, r = call("POST", "/api/burn", {"image": rel, "part": "27C020@DIP32", "machine": "segasonic-the-hedgehog"})
    check("burn after the blank check, on the Pi -> 202, armed by the screen for 30 s",
          code == 202 and r["op"] == "burn" and r["armed"]["by"] == "screen" and abs(r["armed"]["until"] - (t0 + 30)) < 3)
    st = wait()
    check("... the job burns and verifies: the chip holds the image", st["op"] == "burn" and st["state"] == "done"
          and st["verified"] is True and open(f"{w}/chip.bin", "rb").read() == img)
    code, r = call("POST", "/api/burn", {"image": rel, "part": "27C020@DIP32"})
    check("burn again without a new blank check -> 409 (the last job was a burn)", code == 409)


def _lan_addrs():
    """This host's addresses. `hostname -I` is not present everywhere (Arch ships it
    in inetutils), so fall back to the kernel's own view before giving up."""
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    except (FileNotFoundError, OSError):
        pass
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True).stdout
        return [ln.split()[3].split("/")[0] for ln in out.splitlines() if len(ln.split()) > 3]
    except (FileNotFoundError, OSError, IndexError):
        return []


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
                try:                                 # blank checks and burns get the fake chip, dumps the fake ROM
                    op = json.load(open(f"{w}/spool/request.json")).get("op")
                except (OSError, ValueError):
                    op = None
                chip = {"FAKE_CHIP": f"{w}/chip.bin"} if op in ("blank", "burn") else {}
                subprocess.run(["python3", DUMP, "--job", f"{w}/spool/request.json", "--status", f"{w}/spool/status.json"],
                               env=dict(fake, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"),
                                        GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"), GATBOX_ROMS=f"{w}/roms",
                                        GATBOX_API=B, FAKE_ROM=f"{T}/rom.bin", FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic",
                                        **chip),
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
        burn_web(w, call, B)
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
