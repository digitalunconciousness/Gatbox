#!/usr/bin/env python3
"""gatbox-disk-send (docs/plans/2026-11-17-disk-image-drain.md): the store's images to the archive, over the home network only.
The real tool against the real archive server (archive/gatboxarchive) on a spare port, and a fake image store (tests/fakedisk.py).
Every byte and name is invented.
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
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))
sys.path.insert(0, os.path.join(REPO, "tests"))

from fakedisk import FakeDisks  # noqa: E402
from gatboxarchive import diskstore as DS  # noqa: E402
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402

TOOL = os.path.join(REPO, "tools", "gatbox-disk-send")
PART = DS.PART_SIZE
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-disk-send-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"disk send: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    F = FakeDisks(f"{tmp}/fake")
    F.mount_table()
    store = F.store
    spool, state, conf = f"{tmp}/spool", f"{tmp}/state", f"{tmp}/archive.conf"
    os.makedirs(spool)

    def image(machine, label, data, started):
        os.makedirs(f"{store}/{machine}", exist_ok=True)
        base = f"{store}/{machine}/{label}"
        open(base + ".img", "wb").write(data)
        side = {"label": label, "machine": machine, "bytes": len(data), "sha1": hashlib.sha1(data).hexdigest(),
                "sha256": hashlib.sha256(data).hexdigest(), "started": started, "archive": {"state": "pending"},
                "also_read": [{"label": "x"}] * 3, "ddrescue": {"big": "not sent"}}
        json.dump(side, open(base + ".json", "w"))
        return side["sha256"], data

    def tool(*args, **env):
        e = F.env(GATBOX_LIB=os.path.join(REPO, "backend"), GATBOX_DISK_SEND_CONF=conf, GATBOX_DISK_SEND_STATE=state,
                  GATBOX_DISK_SPOOL=spool, GATBOX_DISK_SEND_POLL="0.05", GATBOX_DISK_SEND_REACH="2", **env)
        e.pop("STATE_DIRECTORY", None)
        e.pop("CREDENTIALS_DIRECTORY", None)
        p = subprocess.run([sys.executable, TOOL, *args], env=e, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def st():
        try:
            return json.load(open(f"{state}/state.json"))
        except (OSError, ValueError):
            return {}

    a_sha, a = image("widget-wars", "WW-HDD", os.urandom(2 * PART + 999), 100.0)
    b_sha, b = image("unassigned", "CAMERA-CARD", os.urandom(PART // 3), 200.0)

    arc = Archive(Config({"ARCHIVE_DATA": f"{tmp}/adata", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0",
                          "ARCHIVE_DISKS": f"{tmp}/adisks"}))
    tun, prv = make_servers(arc)
    for s in (tun, prv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    dev = arc.tokens.add("deck", "device")
    lan = f"http://127.0.0.1:{prv.server_address[1]}"

    print("nothing configured, or configured wrong: it says so and sends nothing")
    code, out = tool()
    check("no archive.conf: off, and why", code == 0 and st().get("result") == "off" and "archive.conf" in st()["why"])
    open(conf, "w").write(f"ARCHIVE_URL=https://archive.example.test\nARCHIVE_TOKEN={dev}\n")
    tool()
    check("an archive.conf with only the tunnel's address: off, naming ARCHIVE_LAN_URL (the tunnel is never used for images)",
          st().get("result") == "off" and "ARCHIVE_LAN_URL" in st()["why"])
    for bad in ("https://archive.example.test", "http://archive.example.test:8081", "http://8.8.8.8:8081", f"{lan}/api"):
        open(conf, "w").write(f"ARCHIVE_LAN_URL={bad}\nARCHIVE_TOKEN={dev}\n")
        tool()
        check(f"ARCHIVE_LAN_URL={bad}: refused, not a private http address", st().get("result") == "off" and "private" in st()["why"])
    open(conf, "w").write(f"ARCHIVE_LAN_URL={lan}\n")
    tool()
    check("no device token: off, and why", st().get("result") == "off" and "ARCHIVE_TOKEN" in st()["why"])

    print("away from home, or the store or a disk job in the way:")
    open(conf, "w").write(f"ARCHIVE_LAN_URL=http://127.0.0.1:1\nARCHIVE_TOKEN={dev}\n")
    code, out = tool()
    check("no answer on the home address: AWAY, exit 0 (the timer must not fail every 15 minutes away from home)",
          code == 0 and st().get("result") == "away" and "home network" in st()["why"])
    open(conf, "w").write(f"ARCHIVE_URL=https://archive.example.test\nARCHIVE_LAN_URL={lan}\nARCHIVE_TOKEN={dev}\n")
    open(f"{spool}/running.json", "w").write("{}")
    tool()
    check("a disk job running: it waits, and nothing reaches the archive", st().get("result") == "waiting" and arc.disks.list() == []
          and os.listdir(f"{tmp}/adisks/incoming") == [])
    os.remove(f"{spool}/running.json")
    F.mount_table(store=False)
    tool()
    check("the store unplugged: it waits, saying so", st().get("result") == "waiting" and "store" in st()["why"])
    F.mount_table()

    print("at home, with the archive's disks on:")
    code, out = tool()
    stored = {i["sha256"]: i for i in arc.disks.list()}
    check("both images sent and checked by the archive; DONE", code == 0 and st().get("result") == "done" and set(stored) == {a_sha, b_sha})
    check("... the bytes on the archive are the store's", open(f"{tmp}/adisks/stored/{a_sha[:2]}/{a_sha}.img", "rb").read() == a
          and open(f"{tmp}/adisks/stored/{b_sha[:2]}/{b_sha}.img", "rb").read() == b)
    check("... oldest first", out.index("WW-HDD") < out.index("CAMERA-CARD"))
    check("... the journal shows it moving: a start line, progress in 5 % steps, and the archive's check",
          "sending widget-wars/WW-HDD.img (" in out and "widget-wars/WW-HDD.img: 100 % (3 of 3 parts)" in out
          and "1 part)" in out
          and "the archive is reading it back" in out)
    check("... NOT ARCADE ones too (the owner: everything)", stored[b_sha]["machine"] == "unassigned")
    side = json.load(open(f"{tmp}/adisks/stored/{a_sha[:2]}/{a_sha}.json"))
    check("... the archive keeps the sidecar's facts, not its bulky logs", side["label"] == "WW-HDD" and side["sha1"] == hashlib.sha1(a).hexdigest()
          and "ddrescue" not in side and "also_read" not in side)
    check("... its state lists both as sent, and nothing in progress", set(st()["sent"]) == {a_sha, b_sha} and "current" not in st())
    check("... the store is untouched (it reads only)", json.load(open(f"{store}/widget-wars/WW-HDD.json"))["archive"] == {"state": "pending"})
    code, out = tool()
    check("a second run: DONE, nothing sent", st().get("result") == "done" and "sent " not in out)

    print("gatbox-web shows it (GET /api/disk/images):")
    os.environ.update(F.env(GATBOX_DISK_SEND_STATE=state))
    from gatboxweb import disk as WD  # noqa: E402
    ims = {i["label"]: i for i in WD.images()}
    check("both images ARCHIVED (uploaded), from the sender's state", ims["WW-HDD"]["archive"] == "uploaded" and ims["CAMERA-CARD"]["archive"] == "uploaded")
    sm = WD.send_summary()
    check("... and the drain's line: DONE, 2 there", sm["result"] == "done" and sm["sent"] == 2 and sm["current"] is None)

    print("resuming, and stopping for a disk job:")
    c_sha, c = image("widget-wars", "WW-LATER", os.urandom(3 * PART + 5), 300.0)
    arc.disks.begin(c_sha, len(c), {"label": "WW-LATER"})
    arc.disks.put_part(c_sha, 0, c[:PART])                    # an earlier run got this far
    spec = importlib.util.spec_from_loader("gds", importlib.machinery.SourceFileLoader("gds", TOOL))
    gds = importlib.util.module_from_spec(spec)
    os.environ.update(GATBOX_DISK_SEND_CONF=conf, GATBOX_DISK_SPOOL=spool, GATBOX_DISK_SEND_POLL="0.05")
    spec.loader.exec_module(gds)
    calls, armed = [], [True]
    real_put = gds.Archive.call

    def counting(self, method, path, body=None, raw=None, timeout=60):
        if method == "PUT":
            calls.append(path)
            if armed[0]:
                armed[0] = False
                open(f"{spool}/request.json", "w").write("{}")      # a READ is queued while this part goes up
        return real_put(self, method, path, body, raw, timeout)
    gds.Archive.call = counting
    gds.STATE_DIR = state
    gds.run()
    check("a disk job queued mid-image: it stops after the part in flight, WAITING, and the image shows SENDING with what it has",
          st().get("result") == "waiting" and calls == [f"/api/v1/disks/{c_sha}/parts/1"]
          and st()["current"]["sha256"] == c_sha and st()["current"]["have"] == 2)
    ims = {i["label"]: i for i in WD.images()}
    check("... gatbox-web: SENDING, 2 of 4 parts", ims["WW-LATER"]["archive"] == "sending" and ims["WW-LATER"]["sending"]["have"] == 2
          and ims["WW-LATER"]["sending"]["parts"] == 4)
    os.remove(f"{spool}/request.json")
    calls.clear()
    gds.run()
    check("the next run resumes: only parts 2 and 3 go up, and it is stored", calls == [f"/api/v1/disks/{c_sha}/parts/{n}" for n in (2, 3)]
          and st().get("result") == "done" and c_sha in st()["sent"]
          and open(f"{tmp}/adisks/stored/{c_sha[:2]}/{c_sha}.img", "rb").read() == c)
    gds.Archive.call = real_put

    print("the archive full, or already holding an image:")
    for s in (tun, prv):
        s.shutdown()
    free = shutil.disk_usage(f"{tmp}/adisks").free
    arc2 = Archive(Config({"ARCHIVE_DATA": f"{tmp}/adata", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0",
                           "ARCHIVE_DISKS": f"{tmp}/adisks", "ARCHIVE_DISK_RESERVE": str(free)}))
    tun, prv = make_servers(arc2)
    for s in (tun, prv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    open(conf, "w").write(f"ARCHIVE_LAN_URL=http://127.0.0.1:{prv.server_address[1]}\nARCHIVE_TOKEN={dev}\n")
    d_sha, d = image("widget-wars", "WW-NEW", os.urandom(PART), 400.0)
    code, out = tool()
    check("no room on the archive: NO ROOM, saying so, exit 0; the image stays pending", code == 0 and st().get("result") == "no_room"
          and "no room" in st()["why"] and d_sha not in st()["sent"])
    s_ = st()
    s_["sent"].pop(a_sha)                                      # as if this deck's state were lost
    json.dump(s_, open(f"{state}/state.json", "w"))
    os.remove(f"{store}/widget-wars/WW-NEW.img")
    code, out = tool()
    check("an image the archive already holds (its state lost): taken as sent, nothing uploaded",
          st().get("result") == "done" and a_sha in st()["sent"] and "sent " not in out)
    for s in (tun, prv):
        s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
