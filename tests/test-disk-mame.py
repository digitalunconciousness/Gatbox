#!/usr/bin/env python3
"""The MAME CHD check (docs/plans/2026-11-02-disk-imaging.md §7, Task 3): gatbox-disk `mame` and `chd`, gatboxlib/chd.py, against
the fake machine of tests/fakedisk.py and tests/fake-chdman (no real drive, no real CHD).

What is checked: `chdman info` as chdman 0.289 prints it is read (SHA1, Data SHA1, logical size with its commas, the GDDD geometry);
a reference CHD in _chd/ is named by the MAME disk whose combined SHA1 it has, one that is no MAME disk says so and one chdman can't read
says why; an image that is MAME's disk is a MATCH (also with bytes past the disk); one the game wrote to in a few places says that, with
how many bytes and places; one that is another version says that; a shorter one says it can't be that disk; every version's CHD is tried
and the matching clone is found; no CHD for the machine's disk lists the CHDs wanted; a machine MAME has no disk for, or no machine, says
so; the result goes into the image's sidecar and nowhere else; the scratch file is gone and the image unchanged; no room, no chdman, a
chdman that fails, and a disk job running each stop with a sentence; the job mode; and the terminal.
    python3 tests/test-disk-mame.py
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
from fakedisk import FakeDisks, MiB  # noqa: E402

TOOL = os.path.join(REPO, "tools", "gatbox-disk")
T = tempfile.mkdtemp(prefix="gatbox-mame-test-")
F = FakeDisks(T)
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def sha(b):
    return hashlib.sha1(b).hexdigest()


def main():
    F.mount_table()
    store = F.store
    raws = f"{T}/raws"
    os.makedirs(raws)
    os.makedirs(f"{store}/_chd")

    def make_chd(name, data, cyls=407, heads=4, secs=26):
        raw = f"{raws}/{name}.raw"
        open(raw, "wb").write(data)
        json.dump({"raw": raw, "cyls": cyls, "heads": heads, "secs": secs}, open(f"{store}/_chd/{name}.chd", "w"))
        meta = f"CYLS:{cyls},HEADS:{heads},SECS:{secs},BPS:512"
        return sha(data + meta.encode())

    disk_v1 = os.urandom(6 * MiB)
    disk_v2 = bytearray(disk_v1)
    for k in range(0, len(disk_v2), 4096):
        disk_v2[k] ^= 0xFF                                      # another version: different everywhere
    disk_v2 = bytes(disk_v2)
    c_v1 = make_chd("widgetw", disk_v1)
    c_v2 = make_chd("widgetwa", disk_v2)
    make_chd("stranger", os.urandom(1 * MiB))
    open(f"{store}/_chd/broken.chd", "w").write("not a chd")
    roms = {"meta": {}, "machines": {
        "widget-wars": {"set": "widgetw", "versions": [
            {"name": "widgetw", "desc": "Widget Wars (v1.1)", "parent": True, "roms": [], "disks": [{"name": "widgetw", "sha1": c_v1}]},
            {"name": "widgetwa", "desc": "Widget Wars (v1.0)", "parent": False, "roms": [], "disks": [{"name": "widgetwa", "sha1": c_v2}]}]},
        "sprocket": {"set": "sprocket", "versions": [{"name": "sprocket", "desc": "Sprocket", "parent": True, "roms": [], "disks": []}]}}}
    rp = f"{T}/mame-roms.json"
    json.dump(roms, open(rp, "w"))
    os.environ.update(F.env(GATBOX_CHDMAN=os.path.join(REPO, "tests", "fake-chdman"), GATBOX_MAME_ROMS=rp))
    sys.path.insert(0, os.path.join(REPO, "backend"))
    from gatboxlib import chd as C
    loader = importlib.machinery.SourceFileLoader("gatbox_disk", TOOL)
    spec = importlib.util.spec_from_loader("gatbox_disk", loader)
    G = importlib.util.module_from_spec(spec)
    loader.exec_module(G)

    def put_image(machine, label, data):
        os.makedirs(f"{store}/{machine}", exist_ok=True)
        base = f"{store}/{machine}/{label}_20261116_{sha(data)[:8]}"
        open(base + ".img", "wb").write(data)
        json.dump({"label": label, "machine": machine, "media": "hdd", "bytes": len(data), "sha1": sha(data),
                   "mame": {"state": "not run"}, "tool": "gatbox-disk"}, open(base + ".json", "w"))
        return os.path.relpath(base + ".img", store)

    def run(image, machine=None):
        w = G.Work(None, lambda *a: None)
        try:
            return G.mame_check(image, C.mame_disks(roms, machine or json.load(open(f"{store}/{image[:-4]}.json"))["machine"]), w), w
        except (G.Stop, C.ChdError) as e:
            return {"error": getattr(e, "what", str(e))}, w

    print("chdman info, as chdman 0.289 prints it (the owner's laptop, 2026-11-16):")
    real = """chdman - MAME Compressed Hunks of Data (CHD) manager 0.289 (mame0289-dirty)
Input file:   out.chd
File Version: 5
Logical size: 21,671,936 bytes
Hunk Size:    4,096 bytes
Total Hunks:  5,291
Unit Size:    512 bytes
Total Units:  42,328
Compression:  lzma (LZMA), zlib (Deflate), huff (Huffman), flac (FLAC)
CHD size:     226 bytes
Ratio:        0.0%
SHA1:         2b32c6a4b40d69f1edd9b681b135f5ba0e9b9503
Data SHA1:    6ec58adcd0b463f71b7dec52d2ada0751b261c1c
Metadata:     Tag='GDDD'  Index=0  Length=33 bytes
              CYLS:407,HEADS:4,SECS:26,BPS:512."""
    i = C.parse_info(real)
    check("the real output: SHA1, Data SHA1, the logical size without its commas, the hunk, the version and the geometry",
          i["sha1"] == "2b32c6a4b40d69f1edd9b681b135f5ba0e9b9503" and i["data_sha1"] == "6ec58adcd0b463f71b7dec52d2ada0751b261c1c"
          and i["logical"] == 21671936 and i["hunk"] == 4096 and i["version"] == "5" and i["chs"] == {"cyls": 407, "heads": 4, "secs": 26, "bps": 512})
    try:
        C.parse_info("Logical size: 12 bytes\nSHA1: nothing")
        bad = False
    except C.ChdError:
        bad = True
    check("output without the two hashes is refused, not guessed at", bad)

    print("the reference CHDs in _chd/:")
    rows = {r["file"]: r for r in C.list_chds(store, roms)}
    check("each one is listed: the two MAME disks named (set, disk, machine), a stranger as no MAME disk, a broken one with why",
          rows["widgetw.chd"]["mame"]["set"] == "widgetw" and rows["widgetw.chd"]["mame"]["machine"] == "widget-wars"
          and rows["widgetwa.chd"]["mame"]["set"] == "widgetwa" and rows["stranger.chd"]["mame"] is None
          and "chdman info failed" in rows["broken.chd"]["error"] and rows["widgetw.chd"]["logical"] == 6 * MiB)
    rc = subprocess.run(["python3", TOOL, "chd"], capture_output=True, text=True, env=dict(os.environ))
    check("gatbox-disk chd says the same, in words", rc.returncode == 0 and "widgetw.chd: widgetw (widgetw), for widget-wars" in rc.stdout
          and "stranger.chd: not a MAME disk this Pi knows" in rc.stdout)

    print("images against them:")
    snap = {f: os.path.getsize(f"{store}/_chd/{f}") for f in os.listdir(f"{store}/_chd")}
    im = put_image("widget-wars", "WW-HDD", disk_v1 + os.urandom(2 * MiB))
    before = sha(open(f"{store}/{im}", "rb").read())
    r, _ = run(im)
    side = json.load(open(f"{store}/{im[:-4]}.json"))
    check("MAME's disk exactly, on a bigger drive: MATCH, the set and version named, and the bytes past it said",
          r["state"] == "match" and r["set"] == "widgetw" and r["desc"] == "Widget Wars (v1.1)" and "2,097,152 bytes past it" in r["why"])
    check("... the result is in the image's sidecar, the image is unchanged, and no scratch file is left",
          side["mame"]["state"] == "match" and side["mame"]["chd"] == "widgetw.chd" and sha(open(f"{store}/{im}", "rb").read()) == before
          and (not os.path.isdir(f"{store}/_chd_scratch") or os.listdir(f"{store}/_chd_scratch") == []))
    written = bytearray(disk_v1)
    for off in (100_000, 2_000_000, 5_000_000):
        written[off:off + 300] = os.urandom(300)
    im2 = put_image("widget-wars", "WW-AUDITS", bytes(written))
    r, _ = run(im2)
    check("MAME's disk with the game's own writes in three places: it says so, with the bytes and the places, and that it is most likely this version",
          r["state"] == "differs" and r["verdict"] == "written" and r["places"] == 3 and 0 < r["bytes"] <= 900 and r["exact"]
          and "written to these areas" in r["why"] and r["set"] == "widgetw")
    check("... the scratch file the CHD was unpacked into is gone", not os.path.isdir(f"{store}/_chd_scratch") or os.listdir(f"{store}/_chd_scratch") == [])
    im3 = put_image("widget-wars", "WW-OLD", disk_v2)
    r, _ = run(im3)
    check("every version's CHD is tried: the image of v1.0 is a MATCH with the clone's CHD, after the parent's was tried",
          r["state"] == "match" and r["set"] == "widgetwa" and [t["set"] for t in r["tried"]] == ["widgetw", "widgetwa"])
    other = os.urandom(6 * MiB)
    im4 = put_image("widget-wars", "WW-OTHER", other)
    r, _ = run(im4)
    check("another drive altogether: 'another version of the game, or damage', never 'bad', and it stops counting byte by byte",
          r["state"] == "differs" and r["verdict"] == "other" and "another version of the game, or damage" in r["why"] and "bad" not in r["why"])
    im5 = put_image("widget-wars", "WW-SHORT", disk_v1[:3 * MiB])
    r, _ = run(im5)
    check("an image shorter than MAME's disk: it can't be that disk, and it says why", r["state"] == "differs" and r["verdict"] == "shorter"
          and "can't be that disk" in r["why"])
    im6 = put_image("sprocket", "SP-HDD", os.urandom(1 * MiB))
    r, _ = run(im6)
    check("a machine MAME lists no hard disk for: says so", r["state"] == "no_disk" and "no hard disk for sprocket" in r["why"])
    im7 = put_image("unassigned", "LOOSE", os.urandom(1 * MiB))
    r, _ = run(im7)
    check("an image read for no machine: says there is no MAME disk to compare it with", r["state"] == "no_disk" and "no machine" in r["why"])
    for f in ("widgetw.chd", "widgetwa.chd"):
        os.rename(f"{store}/_chd/{f}", f"{T}/{f}")
    r, _ = run(im)
    check("no CHD in _chd/ for the machine's disks: it says which CHDs to copy there", r["state"] == "no_reference"
          and r["wanted"] == ["widgetw (widgetw)", "widgetwa (widgetwa)"] and "Copy MAME's CHD" in r["why"])
    for f in ("widgetw.chd", "widgetwa.chd"):
        os.rename(f"{T}/{f}", f"{store}/_chd/{f}")
    check("the CHDs themselves are never changed", {f: os.path.getsize(f"{store}/_chd/{f}") for f in os.listdir(f"{store}/_chd")} == snap)

    print("what stops it:")
    real_usage = G.shutil.disk_usage
    G.shutil.disk_usage = lambda p: type("U", (), {"free": 100})()
    r, _ = run(im2)
    G.shutil.disk_usage = real_usage
    check("no room in the store to unpack the CHD (only needed when they differ): it says how much it needs", "needs" in r.get("error", "") and "GB free" in r["error"])
    os.environ["FAKE_CHDMAN_FAIL"] = "extracthd"
    r, _ = run(im2)
    os.environ.pop("FAKE_CHDMAN_FAIL")
    check("chdman failing to unpack: it says so, and leaves no scratch file", "extracthd failed" in r.get("error", "")
          and (not os.path.isdir(f"{store}/_chd_scratch") or os.listdir(f"{store}/_chd_scratch") == []))
    keep = C.CHDMAN
    C.CHDMAN = f"{T}/no-such-chdman"
    r = C.list_chds(store, roms)
    C.CHDMAN = keep
    check("chdman not installed: every CHD says so, and that the bootstrap installs it (mame-tools)", all("mame-tools" in x.get("error", "") for x in r))
    lock = G.take_lock()
    r, _ = run(im)
    os.close(lock)
    check("a disk read or write running (the store's lock held): it waits for it, said in words", "another disk read or write is running" in r.get("error", ""))
    F.mount_table(store=False)
    r, _ = run(im)
    F.mount_table()
    check("the store unplugged: IMAGE STORE NOT CONNECTED", r.get("error") == "IMAGE STORE NOT CONNECTED")

    print("the job mode, and the terminal:")
    spool = f"{T}/spool"
    os.makedirs(spool)
    json.dump({"op": "mame", "id": "m1", "at": 0, "image": f"{store}/{im3}", "disks": C.mame_disks(roms, "widget-wars")}, open(f"{spool}/request.json", "w"))
    subprocess.run(["python3", TOOL, "--job", f"{spool}/request.json", "--status", f"{spool}/status.json"], env=dict(os.environ), timeout=120)
    st = json.load(open(f"{spool}/status.json"))
    check("the job: done, with the result in the status file, and the claim removed", st["state"] == "done" and st["op"] == "mame"
          and st["mame"]["state"] == "match" and st["mame"]["set"] == "widgetwa" and not os.path.exists(f"{spool}/running.json"))
    json.dump({"op": "mame", "id": "m2", "at": 0, "image": f"{store}/{im}", "disks": "not a dict"}, open(f"{spool}/request.json", "w"))
    subprocess.run(["python3", TOOL, "--job", f"{spool}/request.json", "--status", f"{spool}/status.json"], env=dict(os.environ), timeout=120)
    st = json.load(open(f"{spool}/status.json"))
    check("a request whose disks aren't what gatbox-web sends: no disks, so 'no hard disk', not a crash", st["state"] == "done" and st["mame"]["state"] == "no_disk")
    os.environ["FAKE_CHDMAN_FAIL"] = "extracthd"
    json.dump({"op": "mame", "id": "m3", "at": 0, "image": f"{store}/{im2}", "disks": C.mame_disks(roms, "widget-wars")}, open(f"{spool}/request.json", "w"))
    subprocess.run(["python3", TOOL, "--job", f"{spool}/request.json", "--status", f"{spool}/status.json"], env=dict(os.environ), timeout=120)
    os.environ.pop("FAKE_CHDMAN_FAIL")
    st = json.load(open(f"{spool}/status.json"))
    check("the job, when chdman fails: stopped, the reason in words, and that nothing was changed", st["state"] == "stopped" and "extracthd failed" in st["error"]
          and "nothing was changed" in st["hint"])
    rc = subprocess.run(["python3", TOOL, "mame", "-i", im], capture_output=True, text=True, env=dict(os.environ))
    check("gatbox-disk mame from the terminal (its MAME disks from the installed checklist): MATCH", rc.returncode == 0 and "MAME: MATCH" in rc.stdout)

    print(f"\nmame: {passed} passed, {failed} failed")
    shutil.rmtree(T, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
