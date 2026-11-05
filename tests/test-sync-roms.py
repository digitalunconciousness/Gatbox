#!/usr/bin/env python3
"""Phase C: gatbox-sync sends dumps and burns to a hub. A stub gatbox-web serves the loopback reads
(/api/sync/dumps, /api/sync/burns, /api/sync/image) and stub hubs implement the contract's files calls and the
ingest of rom_dump and burn; gatbox-sync runs as a subprocess against them.

What's checked: the files go first, in parts, and resume from the parts a hub holds; the items are exactly the
contract's example for the example's machine; a hub that doesn't list `roms` is sent nothing; a dump with no
machine, an image over 32 MiB and a file that no longer matches its record are held with the reason; two hubs
never see each other's machines, nor their images; a refusal counts and parks like a session's; and an older
gatbox-web without the reads costs nothing. Every machine and byte is invented.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = os.path.join(REPO, "backend", "gatbox-sync")
EXAMPLES = os.path.join(REPO, "docs", "contract", "v1", "examples")
MIB = 1024 * 1024
DEV = "a1b2c3d4e5f6"
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


# --- the stubs --------------------------------------------------------------------------------------------------

class Web:
    """gatbox-web's loopback reads. `dumps` and `burns` are what it lists, `images` the bytes by archive name."""

    def __init__(self):
        self.dumps, self.burns, self.images, self.hits, self.has_sync = [], [], {}, [], True
        web = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj, raw=None):
                body = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlsplit(self.path)
                web.hits.append(self.path)
                if u.path == "/api/rail/sessions":
                    return self.reply(200, {"sessions": []})
                if u.path == "/api/orders":
                    return self.reply(200, {"orders": []})
                if not u.path.startswith("/api/sync/") or not web.has_sync:
                    return self.reply(404, {"error": "not found"})
                if u.path == "/api/sync/dumps":
                    return self.reply(200, {"dumps": web.dumps})
                if u.path == "/api/sync/burns":
                    return self.reply(200, {"burns": web.burns})
                q = parse_qs(u.query)
                data = web.images.get(q["name"][0])
                n = int(q["part"][0])
                if data is None or n * 8 * MIB >= max(len(data), 1):
                    return self.reply(404, {"error": "no such part"})
                return self.reply(200, None, raw=data[n * 8 * MIB:(n + 1) * 8 * MIB])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class Hub:
    """A hub: roster, capabilities, the files calls and the ingest of the two ROM items. `log` is every call."""

    def __init__(self, slugs, caps=("roms",)):
        self.slugs, self.caps, self.log = list(slugs), list(caps), []
        self.files, self.items, self.reject, self.fail_part, self.up = {}, {}, None, None, True
        hub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length", 0)))

            def do_GET(self):
                if not hub.up:
                    return self.reply(503, {"error": "down"})
                path = urlsplit(self.path).path
                if path == "/api/v1/health":
                    return self.reply(200, {"ok": True, "contract": "v1", "time": 0, "app": "stub",
                                            "capabilities": hub.caps})
                if path == "/api/v1/roster":
                    return self.reply(200, {"contract": "v1", "machines": [
                        {"slug": s, "name": s, "status": "Working", "location": "", "model_key": None}
                        for s in hub.slugs]})
                if path.startswith("/api/v1/machines/"):
                    return self.reply(200, {"contract": "v1", "machine": "x", "status": "open", "orders": []})
                return self.reply(404, {"error": "not found"})

            def do_PUT(self):
                parts = urlsplit(self.path).path.split("/")          # '', api, v1, files, <sha>, parts, <n>
                data = self.body()
                hub.log.append(("PUT", parts[4], int(parts[6]), len(data)))
                if hub.fail_part is not None and int(parts[6]) == hub.fail_part:
                    hub.fail_part = None
                    return self.reply(500, {"error": "the hub fell over"})
                hub.files[parts[4]]["parts"][int(parts[6])] = data
                return self.reply(200, {"state": "open", "sha256": parts[4], "part": int(parts[6]),
                                        "have": sorted(hub.files[parts[4]]["parts"])})

            def do_POST(self):
                path = urlsplit(self.path).path
                raw = self.body()
                if path == "/api/v1/files":
                    b = json.loads(raw)
                    hub.log.append(("BEGIN", b["sha256"], b["size"]))
                    if hub.reject == "413":
                        return self.reply(413, {"error": "a file is at most 33554432 bytes"})
                    f = hub.files.setdefault(b["sha256"], {"size": b["size"], "parts": {}, "stored": False})
                    if f["stored"]:
                        return self.reply(200, {"state": "stored", "sha256": b["sha256"], "size": b["size"]})
                    return self.reply(200, {"state": "open", "sha256": b["sha256"], "size": b["size"],
                                            "part_size": 8 * MIB, "parts": -(-b["size"] // (8 * MIB)),
                                            "have": sorted(f["parts"])})
                if path.startswith("/api/v1/files/") and path.endswith("/finish"):
                    sha = path.split("/")[4]
                    hub.log.append(("FINISH", sha))
                    f = hub.files[sha]
                    joined = b"".join(f["parts"][n] for n in sorted(f["parts"]))
                    if hashlib.sha256(joined).hexdigest() != sha:
                        return self.reply(422, {"error": "the parts don't join"})
                    f["stored"] = True
                    return self.reply(200, {"state": "stored", "sha256": sha, "size": len(joined)})
                if path == "/api/v1/ingest":
                    body = json.loads(raw)
                    hub.log.append(("INGEST", [i["kind"] for i in body["items"]], [i["machine"] for i in body["items"]]))
                    out = []
                    for it in body["items"]:
                        if it["machine"] not in hub.slugs:
                            out.append({"uid": it["uid"], "kind": it["kind"], "status": "rejected",
                                        "reason": f"no machine with slug {it['machine']!r}"})
                        elif hub.reject == "items":
                            out.append({"uid": it["uid"], "kind": it["kind"], "status": "rejected",
                                        "reason": "sha1 isn't the stored file's"})
                        elif it["uid"] in hub.items:
                            out.append({"uid": it["uid"], "kind": it["kind"], "status": "duplicate"})
                        else:
                            hub.items[it["uid"]] = it
                            out.append({"uid": it["uid"], "kind": it["kind"], "status": "created"})
                    return self.reply(200, {"contract": "v1", "received": len(out), "results": out})
                return self.reply(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, kind):
        return [c for c in self.log if c[0] == kind]


# --- entries as gatbox-web lists them ---------------------------------------------------------------------------

def dump_entry(machine, label, data, epoch, **kw):
    sha1 = hashlib.sha1(data).hexdigest()
    e = {"key": f"dump:{machine}/{label}_{sha1[:8]}.bin", "machine": machine, "file": f"{machine}/{label}_{sha1[:8]}.bin",
         "label": label, "part": "27C020@DIP32", "size": len(data), "sha1": sha1,
         "crc32": format(zlib.crc32(data) & 0xFFFFFFFF, "08x"), "sha256": hashlib.sha256(data).hexdigest(),
         "romident": {"match": True, "matches": [{"set": "widgetw", "rom": "ww-u10.bin", "description": "Widget Wars"}]},
         "reads": 2, "identical": True, "blank": False, "epoch": epoch, "clock": "ntp", "order": None, "hold": None}
    e.update(kw)
    return e


def burn_entry(machine, data, epoch, name, **kw):
    sha1 = hashlib.sha1(data).hexdigest()
    key = f"burn:{epoch:.3f}:{sha1}"
    e = {"key": key, "machine": machine, "image": name, "size": len(data), "sha1": sha1,
         "sha256": hashlib.sha256(data).hexdigest(), "part": "27C020@DIP32", "vpp": "13V", "blank_ok": True,
         "state": "verified", "error": None, "epoch": epoch, "order": None, "hold": None}
    e.update(kw)
    return e


def uid(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# --- running the sync -------------------------------------------------------------------------------------------

class Rig:
    def __init__(self, hubs):
        """hubs: [(name, Hub)]; the first is the default hub (hub.conf), the rest go in hubs.d."""
        self.tmp = tempfile.mkdtemp(prefix="sync-roms-")
        self.state = os.path.join(self.tmp, "state")
        self.confd = os.path.join(self.tmp, "hubs.d")
        os.makedirs(self.confd)
        self.web = Web()
        self.hubs = hubs
        for i, (name, hub) in enumerate(hubs):
            path = os.path.join(self.tmp, "hub.conf") if i == 0 else os.path.join(self.confd, f"{name}.conf")
            with open(path, "w") as f:
                # each hub issues its own token (and so its own device id): the sync refuses a shared one
                dev = DEV if i == 0 else f"{'bc'[i - 1] * 6 if i < 3 else 'd' * 6}{i}{i}{i}{i}{i}{i}"[:12]
                f.write(f"HUB_URLS={hub.url}\nHUB_TOKEN=gbx_{dev}.not-a-real-value\nHUB_LABEL={name.upper()}\n")

    def run(self, **env):
        e = dict(os.environ, GATBOX_SYNC_CONF=os.path.join(self.tmp, "hub.conf"), GATBOX_SYNC_CONF_DIR=self.confd,
                 GATBOX_SYNC_WEB=self.web.url, STATE_DIRECTORY=self.state, **env)
        r = subprocess.run([sys.executable, SYNC], env=e, capture_output=True, text=True, timeout=120)
        self.out, self.rc = r.stdout + r.stderr, r.returncode
        return self

    def status(self):
        with open(os.path.join(self.state, "roms-status.json")) as f:
            return json.load(f)["items"]

    def book(self, name="default"):
        path = os.path.join(self.state, "roms.json") if name == "default" else os.path.join(self.state, "hubs", name, "roms.json")
        with open(path) as f:
            return json.load(f)


IMG = bytes(range(32))
BIG = os.urandom(9 * MIB)                                 # 8 MiB + 1 MiB: two parts


def main():
    # ---- one hub: the example, as the contract has it -----------------------------------------------------------
    print("one hub: files first, then the example's two items")
    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    img = example("rom-image.json")
    data = bytes.fromhex(img["hex"])
    ex = {i["kind"]: i for i in example("rom-ingest-request.json")["items"]}
    rig.web.dumps = [dump_entry("widget-wars", "U10", data, ex["rom_dump"]["created"])]
    rig.web.burns = [burn_entry("widget-wars", data, ex["burn"]["created"], "widget-wars/U10_" + img["sha1"][:8] + ".bin")]
    rig.web.dumps[0]["romident"] = {"match": True, "matches": [{"set": "widgetw", "rom": "ww-u10.bin"}]}
    rig.web.images = {rig.web.dumps[0]["file"]: data, rig.web.burns[0]["image"]: data}
    rig.run()
    check("exits 0", rig.rc == 0)
    posted = hub.calls("INGEST")
    check("one ingest with the dump and the burn", posted == [("INGEST", ["rom_dump", "burn"], ["widget-wars", "widget-wars"])])
    check("the image was begun, sent in one part and finished before the items",
          [c[0] for c in hub.log if c[0] in ("BEGIN", "PUT", "FINISH", "INGEST")][:4] == ["BEGIN", "PUT", "FINISH", "BEGIN"]
          and hub.log[-1][0] == "INGEST")
    check("the hub holds the image", hub.files[img["sha256"]]["stored"] is True)
    got = {i["kind"]: i for i in hub.items.values()}
    check("the dump is the contract's example, uid included", got["rom_dump"] == ex["rom_dump"])
    check("the burn is the contract's example, uid included", got["burn"] == ex["burn"])
    check("a second image begin finds it stored and sends no part again",
          len(hub.calls("PUT")) == 1 and len(hub.calls("BEGIN")) == 2)
    st = rig.status()
    check("the 7\" is told both are SENT, by archive name",
          all(v == {"state": "sent", "hub": "BARCADE"} for v in st.values()) and len(st) == 2)
    check("the ledger remembers the two uids", len(rig.book()["sent"]) == 2)
    before = len(hub.log)
    rig.run()
    check("a second run sends nothing", len(hub.log) == before and rig.rc == 0)
    check("and says sent still", all(v["state"] == "sent" for v in rig.status().values()))

    # ---- resuming ----------------------------------------------------------------------------------------------
    print("a cut-off upload resumes from the parts the hub has:")
    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("widget-wars", "BIG", BIG, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: BIG}
    hub.fail_part = 1                                      # the hub falls over on the second part, once
    rig.run()
    check("the first run stops on the failed part, exits 0, sends no item",
          rig.rc == 0 and not hub.items and hub.calls("PUT") == [("PUT", hashlib.sha256(BIG).hexdigest(), 0, 8 * MIB),
                                                                  ("PUT", hashlib.sha256(BIG).hexdigest(), 1, MIB)])
    key = rig.web.dumps[0]["key"]
    check("it waits, with the reason, and is not counted as a refusal",
          rig.status()[key]["state"] == "waiting" and "HTTP 500" in rig.status()[key]["reason"] and not rig.book()["tries"])
    hub.log.clear()
    rig.run()
    check("the second run sends only the missing part", [c[2] for c in hub.calls("PUT")] == [1])
    check("and then the item", len(hub.items) == 1 and rig.status()[key]["state"] == "sent")

    # ---- capability, no hub, holds -----------------------------------------------------------------------------
    print("what is held, and why:")
    hub = Hub(["widget-wars"], caps=())
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("widget-wars", "U10", IMG, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: IMG}
    rig.run()
    check("a hub that doesn't list roms is sent nothing", not hub.calls("BEGIN") and not hub.items and rig.rc == 0)
    s = rig.status()[rig.web.dumps[0]["key"]]
    check("held, and it says why", s["state"] == "held" and "roms" in s["reason"])
    hub.caps = ["roms"]
    rig.run()
    check("the day it lists roms, it is sent", len(hub.items) == 1)

    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("widget-wars", "U10", IMG, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: IMG}
    hub.up = False
    rig.run()
    check("no hub reachable: waiting, exit 0, nothing sent",
          rig.rc == 0 and rig.status()[rig.web.dumps[0]["key"]] == {"state": "waiting", "hub": "BARCADE", "reason": "no hub reachable"})

    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("unassigned", "U1", IMG, 1790000100.0, hold="no machine: it goes to the archive only"),
                     dump_entry("widget-wars", "BIGONE", IMG, 1790000200.0, hold="over the hubs' 32 MiB: it stays on the deck"),
                     dump_entry("widget-wars", "U10", IMG, 1790000300.0)]
    rig.web.burns = [burn_entry("widget-wars", IMG, 1790000400.0, "_images/gone.bin",
                                hold="the image is gone from the archive")]
    rig.web.images = {rig.web.dumps[2]["file"]: IMG}
    rig.run()
    st = rig.status()
    check("a dump with no machine, one over 32 MiB and a burn whose image is gone are never uploaded",
          len(hub.calls("BEGIN")) == 1 and len(hub.items) == 1)
    check("each is held with its reason",
          st[rig.web.dumps[0]["key"]] == {"state": "held", "reason": "no machine: it goes to the archive only"}
          and "32 MiB" in st[rig.web.dumps[1]["key"]]["reason"] and "gone" in st[rig.web.burns[0]["key"]]["reason"])

    # ---- refusals ----------------------------------------------------------------------------------------------
    print("a refusal counts, and parks:")
    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("widget-wars", "U10", IMG, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: IMG}
    key = rig.web.dumps[0]["key"]
    hub.reject = "items"
    for n in (1, 2):
        rig.run(GATBOX_SYNC_MAX_TRIES="3")
    check("refused twice: still tried, the reason is shown as waiting",
          rig.status()[key]["state"] == "waiting" and "sha1" in rig.status()[key]["reason"] and not rig.book()["parked"])
    rig.run(GATBOX_SYNC_MAX_TRIES="3")
    check("the third refusal parks it, with the hub's reason",
          rig.status()[key]["state"] == "refused" and "sha1" in rig.status()[key]["reason"] and len(rig.book()["parked"]) == 1)
    before = len(hub.log)
    rig.run(GATBOX_SYNC_MAX_TRIES="3")
    check("a parked item is not offered again", len(hub.log) == before and rig.rc == 0)
    hub.reject = None
    rig.run(GATBOX_SYNC_MAX_TRIES="3", GATBOX_SYNC_UNPARK="1")
    check("GATBOX_SYNC_UNPARK offers it again, and now it goes", len(hub.items) == 1 and rig.status()[key]["state"] == "sent")

    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.dumps = [dump_entry("widget-wars", "U10", IMG, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: IMG}
    hub.reject = "413"
    for _ in range(2):
        rig.run(GATBOX_SYNC_MAX_TRIES="2")
    key = rig.web.dumps[0]["key"]
    check("an image the hub refuses outright (413) parks like any refusal, after the tries",
          rig.status()[key]["state"] == "refused" and "413" in rig.status()[key]["reason"] and not hub.items)

    # ---- two hubs ----------------------------------------------------------------------------------------------
    print("two hubs: nothing crosses")
    barcade, shop = Hub(["widget-wars"]), Hub(["sprocket"])
    rig = Rig([("barcade", barcade), ("shop", shop)])
    a, b, c = bytes([1] * 40), bytes([2] * 40), bytes([3] * 40)
    rig.web.dumps = [dump_entry("widget-wars", "U10", a, 1790000300.0), dump_entry("sprocket", "U5", b, 1790000310.0),
                     dump_entry("mystery-box", "U1", c, 1790000320.0)]
    rig.web.burns = [burn_entry("sprocket", b, 1790000500.0, "sprocket/U5_" + hashlib.sha1(b).hexdigest()[:8] + ".bin")]
    rig.web.images = {rig.web.dumps[0]["file"]: a, rig.web.dumps[1]["file"]: b, rig.web.dumps[2]["file"]: c}
    rig.run()
    check("each hub got its own machine's items only",
          [i["machine"] for i in barcade.items.values()] == ["widget-wars"]
          and sorted(i["machine"] for i in shop.items.values()) == ["sprocket", "sprocket"])
    check("and its own machine's images only",
          set(barcade.files) == {hashlib.sha256(a).hexdigest()} and set(shop.files) == {hashlib.sha256(b).hexdigest()})
    check("a machine on neither roster is held, uploaded nowhere",
          rig.status()[rig.web.dumps[2]["key"]] == {"state": "held", "reason": "no hub's roster has this machine"}
          and hashlib.sha256(c).hexdigest() not in barcade.files and hashlib.sha256(c).hexdigest() not in shop.files)
    check("the statuses name the hub that has each",
          rig.status()[rig.web.dumps[0]["key"]]["hub"] == "BARCADE" and rig.status()[rig.web.dumps[1]["key"]]["hub"] == "SHOP")
    both = Hub(["widget-wars", "sprocket"])
    barcade2 = Hub(["widget-wars"])
    rig = Rig([("barcade", barcade2), ("shop", both)])
    rig.web.dumps = [dump_entry("widget-wars", "U10", a, 1790000300.0)]
    rig.web.images = {rig.web.dumps[0]["file"]: a}
    rig.run()
    check("a machine two rosters claim goes to neither, and says so",
          not barcade2.files and not both.files and "rosters" in rig.status()[rig.web.dumps[0]["key"]]["reason"])

    # ---- an older gatbox-web -----------------------------------------------------------------------------------
    print("an older gatbox-web:")
    hub = Hub(["widget-wars"])
    rig = Rig([("barcade", hub)])
    rig.web.has_sync = False
    rig.run()
    check("no /api/sync reads: nothing to send, exit 0, no files call", rig.rc == 0 and not hub.calls("BEGIN"))

    print(f"sync roms: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
