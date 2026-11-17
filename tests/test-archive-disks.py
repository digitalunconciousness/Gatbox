#!/usr/bin/env python3
"""The deck's disk images on the archive (docs/plans/2026-11-17-disk-image-drain.md; archive/gatboxarchive/diskstore.py): the
real server on spare ports over a temp folder, called over HTTP. Only the private listener (the home network) and only the deck's
device token may send them; the tunnel answers 404. Every byte is invented.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))
sys.path.insert(0, os.path.join(REPO, "tests"))

from gatboxarchive import diskstore as DS  # noqa: E402
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ta", os.path.join(REPO, "tests", "test-archive.py"))
_ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ta)
Client = _ta.Client

passed = failed = 0
MIB = 1024 * 1024
PART = DS.PART_SIZE


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def serve(cfg):
    arc = Archive(Config(cfg))
    servers = make_servers(arc)
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return arc, servers


def settle(P, sha, tok, want=("stored", "failed"), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        c, b, _ = P.call("GET", f"/api/v1/disks/{sha}", token=tok)
        if c == 200 and b["state"] in want:
            return b
        time.sleep(0.05)
    return P.call("GET", f"/api/v1/disks/{sha}", token=tok)[1]


def send(P, tok, data, side, parts=None):
    sha = hashlib.sha256(data).hexdigest()
    c, b, _ = P.call("POST", "/api/v1/disks", {"sha256": sha, "size": len(data), "side": side}, token=tok)
    if c != 200:
        return sha, c, b
    for n in (parts if parts is not None else b.get("missing", [])):
        P.call("PUT", f"/api/v1/disks/{sha}/parts/{n}", raw=data[n * PART:(n + 1) * PART], token=tok, ctype="application/octet-stream")
    return sha, c, b


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-archive-disks-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"archive disks: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    base = {"ARCHIVE_DATA": f"{tmp}/data", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0"}

    print("off until ARCHIVE_DISKS is set:")
    arc, (tun, prv) = serve(base)
    dev, rd = arc.tokens.add("deck", "device"), arc.tokens.add("cabcrawl", "reader")
    T, P = Client(tun.server_address[1]), Client(prv.server_address[1])
    c, b, _ = P.call("GET", "/api/v1/disks", token=dev)
    check("no ARCHIVE_DISKS: the private listener says they are off (503) and how to turn them on", c == 503 and "ARCHIVE_DISKS" in b["error"])
    for s in (tun, prv):
        s.shutdown()

    print("a disks folder it can't use (a fresh mount point is root's; the owner's container, 2026-11-17):")
    open(f"{tmp}/a-file", "w").close()
    arc, (tun, prv) = serve(dict(base, ARCHIVE_DISKS=f"{tmp}/a-file/disks"))
    dev = arc.tokens.add("deck-x", "device")
    T, P = Client(tun.server_address[1]), Client(prv.server_address[1])
    check("the archive still starts and the library still answers (the disk images are an extra)",
          T.call("GET", "/api/v1/library/digest", token=dev)[0] == 200 and T.call("GET", "/api/v1/health")[0] == 200)
    c, b, _ = P.call("GET", "/api/v1/disks", token=dev)
    check("... and the disk calls say they are off and why, naming the folder and install.sh",
          c == 503 and f"{tmp}/a-file/disks" in b["error"] and "install.sh" in b["error"])
    for s in (tun, prv):
        s.shutdown()

    disks = f"{tmp}/disks"
    arc, (tun, prv) = serve(dict(base, ARCHIVE_DISKS=disks))
    dev, rd = arc.tokens.add("deck-b", "device"), arc.tokens.add("cabcrawl-b", "reader")
    T, P = Client(tun.server_address[1]), Client(prv.server_address[1])
    img = os.urandom(2 * PART + 12345)                       # three parts, the last one short
    sha = hashlib.sha256(img).hexdigest()
    side = {"machine": "widget-wars", "label": "WW-HDD", "sha1": hashlib.sha1(img).hexdigest(), "bytes": len(img)}

    print("who may send them (the owner, 2026-11-17: over the home network only, never the tunnel):")
    check("the tunnel answers every disk call 404, even for the deck's token",
          [T.call("GET", "/api/v1/disks", token=dev)[0], T.call("POST", "/api/v1/disks", {"sha256": sha, "size": 1, "side": {}}, token=dev)[0],
           T.call("GET", f"/api/v1/disks/{sha}", token=dev)[0]] == [404, 404, 404])
    check("the private listener: no token 401, a reader 403", P.call("GET", "/api/v1/disks")[0] == 401 and P.call("GET", "/api/v1/disks", token=rd)[0] == 403)
    check("... and the deck's token is still refused for the library there (only the disk calls take it)",
          P.call("GET", "/api/v1/library/digest", token=dev)[0] == 401)
    check("the deck's token on the private listener: an empty list", P.call("GET", "/api/v1/disks", token=dev)[:2] == (200, {"images": []}))

    print("an image, part by part:")
    c, b, _ = P.call("POST", "/api/v1/disks", {"sha256": sha, "size": len(img), "side": side}, token=dev)
    check("begin: open, 3 parts of 8 MiB, every one missing", c == 200 and b["state"] == "open" and b["parts"] == 3 and b["missing"] == [0, 1, 2]
          and b["part_size"] == PART)
    check("the file it fills is the image's size from the start (written in place, not joined)",
          os.path.getsize(f"{disks}/incoming/{sha}/data") == len(img))
    c, b, _ = P.call("PUT", f"/api/v1/disks/{sha}/parts/0", raw=img[:PART], token=dev, ctype="application/octet-stream")
    check("part 0", c == 200 and b["part"] == 0)
    check("a part of the wrong length: 400", P.call("PUT", f"/api/v1/disks/{sha}/parts/1", raw=img[PART:PART + 10], token=dev,
                                                   ctype="application/octet-stream")[0] == 400)
    check("a part past the end: 400", P.call("PUT", f"/api/v1/disks/{sha}/parts/3", raw=b"x", token=dev, ctype="application/octet-stream")[0] == 400)
    c, b, _ = P.call("POST", f"/api/v1/disks/{sha}/finish", token=dev)
    check("finish with parts missing: 409, saying how many", c == 409 and "2 part(s)" in b["error"])
    c, b, _ = P.call("POST", "/api/v1/disks", {"sha256": sha, "size": len(img), "side": side}, token=dev)
    check("begin again (the deck came back later): it resumes, wanting only 1 and 2", c == 200 and b["missing"] == [1, 2] and b["have"] == 1)
    for n in (1, 2):
        P.call("PUT", f"/api/v1/disks/{sha}/parts/{n}", raw=img[n * PART:(n + 1) * PART], token=dev, ctype="application/octet-stream")
    c, b, _ = P.call("POST", f"/api/v1/disks/{sha}/finish", token=dev)
    check("finish: it checks in the background", c == 200 and b["state"] in ("checking", "stored"))
    b = settle(P, sha, dev)
    check("... and it is stored: the bytes hash to the SHA-256 announced", b["state"] == "stored" and b["size"] == len(img))
    stored = f"{disks}/stored/{sha[:2]}/{sha}"
    check("... the image and its sidecar are kept; nothing is left arriving", open(stored + ".img", "rb").read() == img
          and json.load(open(stored + ".json"))["label"] == "WW-HDD" and os.listdir(f"{disks}/incoming") == [])
    c, b, _ = P.call("GET", "/api/v1/disks", token=dev)
    check("the list names it: machine, label, SHA-1", [(i["sha256"], i["machine"], i["label"], i["sha1"]) for i in b["images"]]
          == [(sha, "widget-wars", "WW-HDD", side["sha1"])])
    c, b, _ = P.call("POST", "/api/v1/disks", {"sha256": sha, "size": len(img), "side": side}, token=dev)
    check("sent again: already stored, nothing to send", c == 200 and b["state"] == "stored")
    check("nothing about it is on the tunnel", T.call("GET", f"/api/v1/disks/{sha}", token=dev)[0] == 404)

    print("bytes that don't match:")
    bad = os.urandom(PART + 7)
    bsha = hashlib.sha256(bad).hexdigest()
    P.call("POST", "/api/v1/disks", {"sha256": bsha, "size": len(bad), "side": {}}, token=dev)
    P.call("PUT", f"/api/v1/disks/{bsha}/parts/0", raw=bad[:PART], token=dev, ctype="application/octet-stream")
    P.call("PUT", f"/api/v1/disks/{bsha}/parts/1", raw=b"\0" * 7, token=dev, ctype="application/octet-stream")   # the wrong last bytes
    P.call("POST", f"/api/v1/disks/{bsha}/finish", token=dev)
    b = settle(P, bsha, dev)
    check("a wrong part: FAILED, every part wanted again, nothing stored", b["state"] == "failed" and b["missing"] == [0, 1]
          and "SHA-256" in b["why"] and not os.path.exists(f"{disks}/stored/{bsha[:2]}/{bsha}.img"))
    for n in (0, 1):
        P.call("PUT", f"/api/v1/disks/{bsha}/parts/{n}", raw=bad[n * PART:(n + 1) * PART], token=dev, ctype="application/octet-stream")
    P.call("POST", f"/api/v1/disks/{bsha}/finish", token=dev)
    check("sent again, right this time: stored", settle(P, bsha, dev)["state"] == "stored")

    print("refused, with nothing written:")
    check("a sha256 that isn't one: 400; a size of 0: 400; a sidecar that isn't an object: 400",
          [P.call("POST", "/api/v1/disks", {"sha256": "x" * 64, "size": 5, "side": {}}, token=dev)[0],
           P.call("POST", "/api/v1/disks", {"sha256": "a" * 64, "size": 0, "side": {}}, token=dev)[0],
           P.call("POST", "/api/v1/disks", {"sha256": "a" * 64, "size": 5, "side": "no"}, token=dev)[0]] == [400, 400, 400])
    check("a sidecar over 64 KiB: 400", P.call("POST", "/api/v1/disks", {"sha256": "a" * 64, "size": 5, "side": {"x": "y" * 70000}},
                                               token=dev)[0] == 400)
    check("a part before begin: 404", P.call("PUT", f"/api/v1/disks/{'b' * 64}/parts/0", raw=b"x", token=dev, ctype="application/octet-stream")[0] == 404)
    check("an image it never heard of: 404", P.call("GET", f"/api/v1/disks/{'c' * 64}", token=dev)[0] == 404)
    check("nothing was left arriving by the refusals", os.listdir(f"{disks}/incoming") == [])
    for s in (tun, prv):
        s.shutdown()

    print("room (it keeps ARCHIVE_DISK_RESERVE free, counting what is still arriving):")
    free = shutil.disk_usage(disks).free
    arc, (tun, prv) = serve(dict(base, ARCHIVE_DISKS=disks, ARCHIVE_DISK_RESERVE=str(free - 30 * MIB)))
    dev = arc.tokens.add("deck2", "device")
    P = Client(prv.server_address[1])
    big = 40 * MIB
    c, b, _ = P.call("POST", "/api/v1/disks", {"sha256": "d" * 64, "size": big, "side": {}}, token=dev)
    check("an image bigger than the room left: 507, saying how much it can take, nothing begun",
          c == 507 and "no room" in b["error"] and not os.path.exists(f"{disks}/incoming/{'d' * 64}"))
    c1, _, _ = P.call("POST", "/api/v1/disks", {"sha256": "e" * 64, "size": 20 * MIB, "side": {}}, token=dev)
    c2, b2, _ = P.call("POST", "/api/v1/disks", {"sha256": "f" * 64, "size": 20 * MIB, "side": {}}, token=dev)
    check("two that each fit, but not together while the first is still arriving: the first opens, the second is 507",
          c1 == 200 and c2 == 507)
    for s in (tun, prv):
        s.shutdown()
    shutil.rmtree(f"{disks}/incoming/{'e' * 64}")

    print("zeros take no room (the owner, 2026-11-17: an image that is mostly an empty drive):")
    zs = DS.DiskStore(f"{tmp}/zeros")
    zimg = os.urandom(1000) + bytes(PART - 1000) + bytes(PART) + bytes(PART // 2) + os.urandom(4096)
    zsha = hashlib.sha256(zimg).hexdigest()
    zs.begin(zsha, len(zimg), {"label": "EMPTYISH"})
    wrong = os.urandom(PART)
    zs.put_part(zsha, 0, zimg[:PART])
    zs.put_part(zsha, 1, wrong)                                # a part that arrived wrong the first time
    zs.put_part(zsha, 2, zimg[2 * PART:])
    zs.finish(zsha)
    for _ in range(200):
        if zs.state(zsha)["state"] != "checking":
            break
        time.sleep(0.05)
    check("a wrong part: the check fails, as before", zs.state(zsha)["state"] == "failed")
    for n in range(3):
        zs.put_part(zsha, n, zimg[n * PART:(n + 1) * PART])    # sent again: part 1 is all zeros this time
    zs.finish(zsha)
    for _ in range(200):
        if zs.state(zsha)["state"] != "checking":
            break
        time.sleep(0.05)
    zp = f"{tmp}/zeros/stored/{zsha[:2]}/{zsha}.img"
    check("the zero part sent again replaces the wrong bytes under it (a hole, not a skipped write): stored, and the bytes match",
          zs.state(zsha)["state"] == "stored" and open(zp, "rb").read() == zimg)
    check("... and the image takes about its data, not its size", os.stat(zp).st_blocks * 512 < 2 * MIB and os.path.getsize(zp) == len(zimg))

    print("the archive restarts in the middle of a check:")
    again = os.urandom(PART // 2)
    asha = hashlib.sha256(again).hexdigest()
    st = DS.DiskStore(disks)
    st.begin(asha, len(again), {"label": "AGAIN"})
    st.put_part(asha, 0, again)
    meta = json.load(open(f"{disks}/incoming/{asha}/meta.json"))
    meta["state"] = "checking"                                 # as if the process stopped while it hashed
    json.dump(meta, open(f"{disks}/incoming/{asha}/meta.json", "w"))
    arc, (tun, prv) = serve(dict(base, ARCHIVE_DISKS=disks))
    dev = arc.tokens.add("deck3", "device")
    P = Client(prv.server_address[1])
    check("asking for its state starts the check again, and it ends stored", settle(P, asha, dev)["state"] == "stored")
    c, b, _ = P.call("PUT", f"/api/v1/disks/{asha}/parts/0", raw=again, token=dev, ctype="application/octet-stream")
    check("a part for a stored image changes nothing", c == 200 and b["state"] == "stored" and open(f"{disks}/stored/{asha[:2]}/{asha}.img", "rb").read() == again)
    for s in (tun, prv):
        s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
