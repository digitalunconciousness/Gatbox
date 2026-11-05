#!/usr/bin/env python3
"""Phase C: what gatbox-web hands gatbox-sync of the dump archive. A real gatbox-web on a spare port over an invented
archive (a dump, a dump with no machine, one whose file no longer matches its sidecar, one over 32 MiB, burns with
and without their image, a link out of the archive): the entries and their holds, the part boundaries of an image,
the names that are refused, and that nobody but 127.0.0.1 gets any of it (a request from 127.0.0.2 is not local).
    python3 tests/test-romsync.py
"""
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="gatbox-romsync-")
PORT = 8097
MIB = 1024 * 1024
passed = failed = 0
procs = []


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def get(path, source=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20, source_address=(source, 0) if source else None)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def jget(path, source=None):
    code, body = get(path, source)
    try:
        return code, json.loads(body)
    except ValueError:
        return code, None


def post(path, body):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    out = r.read()
    conn.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, None


def sidecar(folder, label, data, **kw):
    sha1 = hashlib.sha1(data).hexdigest()
    name = f"{label}_{sha1[:8]}"
    os.makedirs(f"{T}/roms/{folder}", exist_ok=True)
    with open(f"{T}/roms/{folder}/{name}.bin", "wb") as f:
        f.write(data)
    side = {"part": "27C020@DIP32", "label": label, "machine": folder, "size": len(data), "sha1": sha1,
            "crc32": format(zlib.crc32(data) & 0xFFFFFFFF, "08x"),
            "romident": {"match": True, "matches": [{"set": "widgetw", "rom": "ww-u10.bin"}]}, "reads_compared": 2,
            "identical": True, "blank": False, "date": "2026-11-01T10:00:00+00:00", "epoch": 1790000300.0, "clock": "ntp",
            "programmer": {"minipro": "0.7.4"}, "tool": "gatbox-dump"}
    side.update(kw)
    with open(f"{T}/roms/{folder}/{name}.json", "w") as f:
        json.dump(side, f)
    return name, sha1


def main():
    for d in ("log", "run", "ctrl", "cache", "data", "roms"):
        os.makedirs(os.path.join(T, d), exist_ok=True)
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    img = bytes(range(32))
    good, good_sha1 = sidecar("widget-wars", "U10", img)
    sidecar("unassigned", "U1", b"\x01" * 16, epoch=1790000100.0)
    bad, _ = sidecar("widget-wars", "BAD", b"\x02" * 16, epoch=1790000200.0)
    with open(f"{T}/roms/widget-wars/{bad}.bin", "wb") as f:                      # changed on disk since it was dumped
        f.write(b"\x03" * 16)
    sidecar("widget-wars", "ONCE", b"\x04" * 16, epoch=1790000250.0, identical=False)
    sidecar("widget-wars", "TWICE", b"\x05" * 16, epoch=1790000260.0, reads_compared=1)
    big, _ = sidecar("widget-wars", "HUGE", b"\x06" * 8, epoch=1790000270.0, size=33 * MIB)
    with open(f"{T}/roms/widget-wars/{big}.bin", "wb") as f:
        f.truncate(33 * MIB)
    multi = os.urandom(9 * MIB)
    nine, _ = sidecar("widget-wars", "NINE", multi, epoch=1790000280.0)
    os.makedirs(f"{T}/roms/_images", exist_ok=True)
    with open(f"{T}/roms/_images/known-good.bin", "wb") as f:
        f.write(img)
    open(f"{T}/outside.bin", "wb").write(b"outside the archive")
    os.symlink(f"{T}/outside.bin", f"{T}/roms/widget-wars/escape.bin")
    lines = [
        {"epoch": 1790000400.0, "state": "verified", "image": f"{T}/roms/widget-wars/{good}.bin", "sha1": good_sha1, "size": 32,
         "part": "27C020@DIP32", "vpp": "13V", "machine": "widget-wars", "blank_ok": True, "error": None},
        {"epoch": 1790000500.5, "state": "stopped", "image": f"{T}/roms/_images/known-good.bin", "sha1": good_sha1, "size": 32,
         "part": "27C020@DIP32", "vpp": None, "machine": "widget-wars", "blank_ok": False,
         "error": "blank check failed: the chip isn't blank"},
        {"epoch": 1790000600.0, "state": "stopped", "image": f"{T}/roms/widget-wars/{good}.bin", "sha1": None, "size": 32,
         "part": "27C020@DIP32", "machine": "widget-wars", "error": "the T48 wasn't found"},
        {"epoch": 1790000700.0, "state": "verified", "image": f"{T}/roms/widget-wars/deleted.bin", "sha1": "0" * 40, "size": 4,
         "part": "27C020@DIP32", "machine": "widget-wars"},
        {"epoch": 1790000800.0, "state": "verified", "image": f"{T}/outside.bin", "sha1": "1" * 40, "size": 19,
         "part": "27C020@DIP32", "machine": "widget-wars"},
        {"epoch": 1790000900.0, "state": "verified", "image": f"{T}/roms/widget-wars/{good}.bin", "sha1": good_sha1, "size": 32,
         "part": "27C020@DIP32", "machine": "unassigned"},
    ]
    with open(f"{T}/roms/burns.jsonl", "w") as f:
        f.writelines(json.dumps(ln) + "\n" for ln in lines)
        f.write("this line is not json\n")

    # a roster with the one machine, and what gatbox-sync last cached: its open order, and where each dump and burn stands
    with open(f"{T}/data/gatbox-barcade-roster.json", "w") as f:
        json.dump({"meta": {}, "video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x"}], "pinball": []}, f)
    os.makedirs(f"{T}/sync")
    with open(f"{T}/sync/orders.json", "w") as f:
        json.dump({"at": 1790000000.0, "open": {"widget-wars": [{"id": 412, "issue": "Rail sags under load", "priority": "High",
                                                                    "status": "Open", "source": "web", "reported": 1790000000.0}]}}, f)
    with open(f"{T}/sync/roms-status.json", "w") as f:
        json.dump({"at": 1790000500.0, "items": {
            f"dump:widget-wars/{good}.bin": {"state": "sent", "hub": "BARCADE"},
            f"dump:widget-wars/{bad}.bin": {"state": "held", "reason": "the file's SHA-1 isn't the one recorded when it was dumped"},
            f"burn:1790000500.500:{good_sha1}": {"state": "refused", "hub": "BARCADE", "reason": "no machine with slug 'widget-wars'"}}}, f)
    with open(f"{T}/parts.txt", "w") as f:                 # minipro's part list, as the bootstrap writes it
        f.write("# minipro 0.7.4 T48\n27C020@DIP32\n")
    env = dict(os.environ, GATBOX_MINIPRO_PARTS=f"{T}/parts.txt", GATBOX_WEB_PORT=str(PORT), GATBOX_SYNC_STATE=f"{T}/sync",
               STATE_DIRECTORY=f"{T}/ctrl",
               CACHE_DIRECTORY=f"{T}/cache", GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data",
               GATBOX_ROMS=f"{T}/roms", MPLCONFIGDIR=f"{T}/cache/mpl")
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    procs.append(subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                  stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT))
    for _ in range(60):
        try:
            get("/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)

    print("the dumps:")
    code, body = jget("/api/sync/dumps")
    by = {d["file"].split("/")[-1].split("_")[0]: d for d in body["dumps"]} if code == 200 else {}
    check("200, every archived dump, none from _images", code == 200 and len(body["dumps"]) == 7 and "known-good.bin" not in str(body))
    d = by.get("U10", {})
    check("a good dump carries what the contract's rom_dump needs",
          d.get("machine") == "widget-wars" and d.get("label") == "U10" and d.get("part") == "27C020@DIP32" and d.get("size") == 32
          and d.get("sha1") == good_sha1 and d.get("crc32") == format(zlib.crc32(img) & 0xFFFFFFFF, "08x")
          and d.get("sha256") == hashlib.sha256(img).hexdigest() and d.get("reads") == 2 and d.get("identical") is True
          and d.get("blank") is False and d.get("epoch") == 1790000300.0 and d.get("clock") == "ntp" and d.get("hold") is None
          and d.get("key") == f"dump:widget-wars/{good}.bin" and d.get("file") == f"widget-wars/{good}.bin")
    check("it has no hold and a MAME verdict", d.get("romident", {}).get("match") is True)
    check("a dump with no machine is held", "no machine" in by["U1"]["hold"])
    check("a file that changed since it was dumped is held, not sent", "SHA-1" in by["BAD"]["hold"])
    check("a dump not read identically is held", "twice" in by["ONCE"]["hold"])
    check("a dump read once is held", "twice" in by["TWICE"]["hold"])
    check("an image over 32 MiB is held on the deck", "32 MiB" in by["HUGE"]["hold"])
    check("oldest first", [x["epoch"] for x in body["dumps"]] == sorted(x["epoch"] for x in body["dumps"]))

    print("the burns:")
    code, body = jget("/api/sync/burns")
    bs = body["burns"] if code == 200 else []
    check("200, the six lines that are JSON (the seventh is skipped)", code == 200 and len(bs) == 6)
    v = bs[0] if bs else {}
    rel = f"widget-wars/{good}.bin"
    check("a verified burn: its image is an archive-relative name, hashed",
          v.get("image") == rel and v.get("sha256") == hashlib.sha256(img).hexdigest() and v.get("sha1") == good_sha1
          and v.get("state") == "verified" and v.get("vpp") == "13V" and v.get("blank_ok") is True and v.get("hold") is None
          and v.get("key") == f"burn:1790000400.000:{good_sha1}" and v.get("epoch") == 1790000400.0)
    check("a stopped burn keeps its reason, and an image from _images/ is resolved too",
          bs[1]["state"] == "stopped" and "isn't blank" in bs[1]["error"] and bs[1]["image"] == "_images/known-good.bin"
          and bs[1]["hold"] is None and bs[1]["key"] == f"burn:1790000500.500:{good_sha1}")
    check("a burn that stopped before its image was read has no hash to send: held", "no hash" in bs[2]["hold"])
    check("a burn whose image is gone is held", "gone" in bs[3]["hold"])
    check("a path outside the archive is never read: held as gone", bs[4]["image"] is None and "gone" in bs[4]["hold"]
          and bs[4]["sha256"] is None)
    check("a burn with no machine is held", "no machine" in bs[5]["hold"])

    print("an image, in parts:")
    c, data = get(f"/api/sync/image?name=widget-wars/{good}.bin&part=0")
    check("the whole of a small image is part 0", c == 200 and data == img)
    c, p0 = get(f"/api/sync/image?name=widget-wars/{nine}.bin&part=0")
    c1, p1 = get(f"/api/sync/image?name=widget-wars/{nine}.bin&part=1")
    check("a 9 MiB image is 8 MiB and 1 MiB", c == c1 == 200 and len(p0) == 8 * MIB and len(p1) == MIB and p0 + p1 == multi)
    check("a part past the end is 404", get(f"/api/sync/image?name=widget-wars/{nine}.bin&part=2")[0] == 404)
    check("a part that isn't a number is 400", get(f"/api/sync/image?name=widget-wars/{nine}.bin&part=x")[0] == 400)
    check("an image over 32 MiB is 413", get(f"/api/sync/image?name=widget-wars/{big}.bin&part=0")[0] == 413)
    check("an image from _images/ can be read (a burn's)", get("/api/sync/image?name=_images/known-good.bin&part=0")[1] == img)
    for bad_name in ("../outside.bin", "/etc/passwd", "widget-wars/../../outside.bin", "widget-wars/escape.bin",
                     "widget-wars/nothing.bin", "widget-wars/%2e%2e/outside.bin", "burns.jsonl", "widget-wars/U10.json", ""):
        c, _ = get("/api/sync/image?name=" + bad_name + "&part=0")
        check(f"image name {bad_name!r} is refused", c in (400, 404))
    check("no name at all is 400", get("/api/sync/image?part=0")[0] == 400)

    print("only the Pi itself:")
    for path in ("/api/sync/dumps", "/api/sync/burns", f"/api/sync/image?name=widget-wars/{good}.bin&part=0"):
        c, b = get(path, source="127.0.0.2")
        check(f"{path.split('?')[0]} from another address is 403 and carries no data",
              c == 403 and img not in b and good_sha1.encode() not in b)
    check("and nothing was served to it", get("/api/sync/dumps")[0] == 200)

    print("where each one stands (the 7\"'s words):")
    code, body = jget("/api/dumps?machine=widget-wars")
    by = {d["file"]: d for d in body["dumps"]} if code == 200 else {}
    check("a dump gatbox-sync sent says SENT to its hub", by.get(f"{good}.bin", {}).get("sent") == {"state": "sent", "hub": "BARCADE"})
    check("a held one carries the reason", "SHA-1" in (by.get(f"{bad}.bin", {}).get("sent") or {}).get("reason", ""))
    check("one the sync hasn't mentioned has no state", by.get(f"{nine}.bin", {}).get("sent") is None)
    burns_ = body.get("burns") or []
    check("the machine's burns, newest first, each with its state (the unassigned one is another machine's)",
          [b["epoch"] for b in burns_] == sorted((b["epoch"] for b in burns_), reverse=True) and len(burns_) == 5
          and all(b["part"] == "27C020@DIP32" for b in burns_))
    check("a refused burn says REFUSED with the hub's reason",
          next(b for b in burns_ if b["epoch"] == 1790000500.5)["sent"]["state"] == "refused"
          and "no machine" in next(b for b in burns_ if b["epoch"] == 1790000500.5)["sent"]["reason"])
    check("a burn's image is shown by its file name only", next(b for b in burns_ if b["epoch"] == 1790000400.0)["image"] == f"{good}.bin")

    print("FOR ORDER on a dump or a burn:")
    c, r = jget("/api/orders/open?machine=widget-wars")
    check("the open orders of a machine, from the sync's cache", c == 200 and [o["id"] for o in r["open"]] == [412])
    check("a machine with none has none", jget("/api/orders/open?machine=nothing-here")[1] == {"machine": "nothing-here", "open": []})
    check("a machine that isn't a slug is 400", jget("/api/orders/open?machine=../x")[0] == 400 and jget("/api/orders/open")[0] == 400)
    dump_body = {"part": "27C020@DIP32", "label": "U10", "machine": "widget-wars"}
    # Validation comes before the queue: a good order reaches "the dump service isn't installed" (503, no spool here),
    # a bad one is stopped at 400 first.
    check("an order that is open for the machine passes (then 503: no dump service in this test)",
          post("/api/dump", dict(dump_body, order=412))[0] in (503, 409))
    for what, order in (("one that isn't open", 999), ("a string", "412"), ("true", True), ("a float", 4.5)):
        check(f"{what} is 400", post("/api/dump", dict(dump_body, order=order))[0] == 400)
    check("an order for the machine 'unassigned' is 400", post("/api/dump", {"part": "27C020@DIP32", "label": "U10", "order": 412,
                                                                          "machine": "unassigned"})[0] == 400)
    check("no order is fine as ever", post("/api/dump", dump_body)[0] in (503, 409))

    print("what was already there:")
    check("the image bytes have no other URL: /api/dumps carries none", img not in get("/api/dumps?machine=unassigned")[1])
    log = open(f"{T}/web.log").read()
    check("server log: no tracebacks", "Traceback" not in log)

    print(f"romsync: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for p in procs:
            p.terminate()
        shutil.rmtree(T, ignore_errors=True)
    sys.exit(rc)
