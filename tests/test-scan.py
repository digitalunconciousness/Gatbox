#!/usr/bin/env python3
"""M6: the barcode scanner. gatbox-scand's key decoder (US layout, shift, caps, keypad, Enter/Tab/LF suffixes, no
suffix), the daemon end to end (key events from a file stand in for the grabbed EY-H2; codes posted to a test
gatbox-web), POST /api/scan's rules (slug → machine, GATBOX:MARK, GATBOX:NEW, unknown, loopback only), the scanner
on /api/devices, and gatbox-labels (when python3-qrcode is installed). Temp dirs and a spare port only.
    python3 tests/test-scan.py
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="gatbox-scan-")
PORT = 8098
B = f"http://127.0.0.1:{PORT}"
passed = failed = 0
procs = []


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def load_scand():
    loader = importlib.machinery.SourceFileLoader("scand", os.path.join(REPO, "backend/gatbox-scand"))
    spec = importlib.util.spec_from_loader("scand", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# keys for text, the way a US keyboard wedge types it (shift around capitals and symbols)
PLAIN = {v[0]: k for k, v in load_scand().KEYMAP.items() if k < 60}
SHIFTED = {v[1]: k for k, v in load_scand().KEYMAP.items() if k < 60 and v[1] != v[0]}


def keys(text, end=28):
    ev = []
    for ch in text:
        if ch in PLAIN:
            ev += [(PLAIN[ch], 1), (PLAIN[ch], 0)]
        else:
            ev += [(42, 1), (SHIFTED[ch], 1), (SHIFTED[ch], 0), (42, 0)]
    if end:
        ev += [(end, 1), (end, 0)]
    return ev


def decode(events, **kw):
    s = load_scand()
    d, out = s.Decoder(), []
    for code, val in events:
        r = d.feed(code, val, now=0.0)
        if r:
            out.append(r)
    return out, d


def api(method, path, body=None, host="127.0.0.1"):
    req = urllib.request.Request(f"http://{host}:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


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


def main():
    print("decoder:")
    check("GATBOX:MARK (shift for capitals and the colon)", decode(keys("GATBOX:MARK"))[0] == ["GATBOX:MARK"])
    check("digits + Enter (a product barcode)", decode(keys("012345678905"))[0] == ["012345678905"])
    check("a slug with hyphens", decode(keys("gauntlet-legends"))[0] == ["gauntlet-legends"])
    check("Tab, keypad Enter and LF end a code too",
          [decode(keys("abc", end=e))[0] for e in (15, 96, 101)] == [["abc"]] * 3)
    kp = [(79, 1), (79, 0), (80, 1), (80, 0), (82, 1), (82, 0), (96, 1), (96, 0)]
    check("keypad digits", decode(kp)[0] == ["120"])
    caps = [(58, 1), (58, 0)] + keys("abc", end=0) + [(58, 1), (58, 0)] + keys("d")
    check("caps lock flips letters", decode(caps)[0] == ["ABCd"])
    check("key-up and autorepeat ignored", decode([(30, 1), (30, 2), (30, 0), (28, 1)])[0] == ["a"])
    check("a bare Enter is no code", decode([(28, 1), (28, 0)])[0] == [])
    check("keys with no text ignored (F1, arrows)", decode([(59, 1), (103, 1)] + keys("x"))[0] == ["x"])
    s = load_scand()
    d = s.Decoder()
    for c, v in keys("NOSUFFIX", end=0):
        d.feed(c, v, now=10.0)
    check("no suffix: finished after IDLE_S without a key", d.idle(now=10.2) is None and d.idle(now=10.6) == "NOSUFFIX")

    print("find() on this Pi:")
    os.environ["GATBOX_SCAN_IDS"] = "af99:8002"
    s = load_scand()
    here = s.find()
    if os.path.exists("/sys/bus/usb/devices") and any(open(p).read().strip() == "af99" for p in
                                                      __import__("glob").glob("/sys/bus/usb/devices/*/idVendor")):
        check("the EY-H2 found by USB ID, as an event node", len(here) == 1 and here[0].startswith("/dev/input/event"))
    else:
        print("  skip  (the EY-H2 isn't plugged in)")
    os.environ["GATBOX_SCAN_IDS"] = "ffff:fffe"          # (0000:0000 is real: the power button, HDMI CEC)
    check("nothing else matches", load_scand().find() == [])

    print("gatbox-web + gatbox-scand end to end:")
    for d_ in ("log", "run", "ctrl", "cache", "data", "scand"):
        os.makedirs(os.path.join(T, d_))
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    with open(f"{T}/data/gatbox-barcade-roster.json", "w") as f:
        json.dump({"meta": {"platforms": {}, "critical_actions": {}},
                   "video_games": [{"slug": "gauntlet-legends", "name": "Gauntlet Legends", "platform": "x"},
                                   {"slug": "gauntlet", "name": "Gauntlet", "platform": "x"}],
                   "pinball": [{"slug": "pin-x-men", "name": "X-Men", "platform": "y"}],
                   "retired": [{"slug": "pin-gone", "name": "Gone", "platform": "y"}]}, f)
    os.makedirs(f"{T}/wifi", exist_ok=True)
    os.environ["GATBOX_WIFI_SPOOL"] = f"{T}/wifi"
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_WIFI_SPOOL=f"{T}/wifi",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data",
               GATBOX_SCAND_STATE=f"{T}/scand/state.json", GATBOX_LABELS=os.path.join(REPO, "tools/gatbox-labels"),
               MPLCONFIGDIR=f"{T}/cache/mpl")
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    procs.append(subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                  stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT))
    for _ in range(50):
        try:
            api("GET", "/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)
    ev = []
    for code in ("gauntlet-legends", "GATBOX:MARK", "GATBOX:NEW", "012345678905", "gauntlet-legends"):
        ev += keys(code)
    with open(f"{T}/events", "wb") as f:
        for c, v in ev:
            f.write(struct.pack("llHHi", 0, 0, 1, c, v))
            f.write(struct.pack("llHHi", 0, 0, 0, 0, 0))          # EV_SYN between keys, as the kernel sends
    r = subprocess.run(["python3", os.path.join(REPO, "backend/gatbox-scand")], timeout=60, capture_output=True, text=True,
                       env=dict(os.environ, GATBOX_SCAN_FAKE=f"{T}/events", GATBOX_SCAN_API=f"{B}/api/scan",
                                RUNTIME_DIRECTORY=f"{T}/scand"))
    out = r.stdout
    check("five codes read and posted", out.count("gatbox-scand: scan ") == 5)
    check("slug -> machine, then the same slug -> no change",
          "'gauntlet-legends': machine" in out and out.rstrip().splitlines()[-1].endswith("'gauntlet-legends': same-machine"))
    check("GATBOX:MARK -> a scan mark in the spool",
          "\tscan\t" in open(f"{T}/ctrl/marks.spool").read())
    check("GATBOX:NEW -> start-request", os.path.exists(f"{T}/ctrl/start-request"))
    check("a product barcode -> unknown", "'012345678905': unknown" in out)
    check("the machine is set", open(f"{T}/ctrl/machine").read().strip() == "gauntlet-legends")
    code, d = api("GET", "/api/devices")
    rec = [x["action"] for x in d["scanner"]["recent"]]
    check("/api/devices: recent scans, newest first", rec == ["same-machine", "unknown", "new", "mark", "machine"])
    check("state.json written back to not attached at exit", json.load(open(f"{T}/scand/state.json"))["attached"] is False)

    print("POST /api/scan rules:")
    check("lower-case command works", api("POST", "/api/scan", {"code": "gatbox:mark"})[1]["action"] == "mark")
    check("upper-case slug works", api("POST", "/api/scan", {"code": "PIN-X-MEN"})[1]["action"] == "machine")
    check("a retired machine is unknown", api("POST", "/api/scan", {"code": "pin-gone"})[1]["action"] == "unknown")
    check("no code / tab / too long -> 400", [api("POST", "/api/scan", b)[0] for b in
          ({}, {"code": "a\tb"}, {"code": "x" * 201})] == [400, 400, 400])

    # A Wi-Fi QR. Android and iOS both produce this format from "share this network", and the
    # scanner is already loopback-only, so standing at the box is the credential.
    print("a scanned Wi-Fi QR:")
    KEY = "hunter2-not-a-real-key"
    spool = os.environ["GATBOX_WIFI_SPOOL"]

    def request():
        p = os.path.join(spool, "request.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        os.unlink(p)
        return d

    request()
    c, r = api("POST", "/api/scan", {"code": f"WIFI:T:WPA;S:BenchNet;P:{KEY};;"})
    check("a WIFI: code is a wifi action", c == 200 and r["action"] == "wifi")
    check("it names the ssid", r.get("ssid") == "BenchNet")
    # The response reaches the DEVICES panel's recent-scans list, and every other branch of
    # scan() echoes the raw code -- which here *is* the key.
    check("the key is not in the response", KEY not in json.dumps(r))
    check("the raw code is not echoed back", KEY not in str(r.get("code", "")))
    req = request()
    check("a join request is written", req is not None and req.get("action") == "join"
          and req.get("ssid") == "BenchNet" and req.get("psk") == KEY)

    c, r = api("POST", "/api/scan", {"code": "WIFI:T:nopass;S:CafeOpen;;"})
    check("an open network has an empty key", c == 200 and r["action"] == "wifi"
          and (request() or {}).get("psk") == "")
    c, r = api("POST", "/api/scan", {"code": "WIFI:S:NoTypeField;P:abc;;"})
    check("T: is optional", c == 200 and r["action"] == "wifi" and r.get("ssid") == "NoTypeField")
    request()

    # The format escapes a literal ; : or backslash inside a field, because an SSID may
    # contain any of them. Built here rather than written inline: the escaping is the point,
    # and a source literal would be escaping the escaping.
    bs = chr(92)
    code = "WIFI:T:WPA;S:The" + bs + ";Cafe" + bs + ":Wi-Fi;P:a" + bs + bs + "b;;"
    c, r = api("POST", "/api/scan", {"code": code})
    check("escaped ; : and backslash are unescaped", c == 200
          and r.get("ssid") == "The;Cafe:Wi-Fi" and (request() or {}).get("psk") == "a" + bs + "b")


    check("an ssid over 32 bytes is refused",
          api("POST", "/api/scan", {"code": "WIFI:T:WPA;S:" + "x" * 33 + ";P:k;;"})[1]["action"] == "unknown")
    for bad in ("WIFI:", "WIFI:T:WPA;;", "WIFI:S:;P:k;;", "WIFI:nonsense"):
        check(f"malformed is unknown, not a crash: {bad!r}",
              api("POST", "/api/scan", {"code": bad})[1]["action"] == "unknown")
    check("nothing was written for any of those", request() is None)

    # A cabinet's printed label is a hub URL, not a bare slug: the host varies by site and is never checked.
    for code in ("http://hub.example.test/g/pin-x-men", "https://hub.example.test:5000/g/PIN-X-MEN/",
                 "/g/pin-x-men", "http://hub.example.test/g/pin-x-men?src=label"):
        c, r = api("POST", "/api/scan", {"code": code})
        check(f"label URL resolves to the machine: {code}",
              c == 200 and r["action"] in ("machine", "same-machine")
              and r["slug"] == "pin-x-men" and r["via_label"] is True)
    check("a bare slug is not reported as a label",
          api("POST", "/api/scan", {"code": "pin-x-men"})[1]["via_label"] is False)
    c, r = api("POST", "/api/scan", {"code": "http://hub.example.test/g/not-on-this-floor"})
    check("a label for a machine this roster lacks says so",
          c == 200 and r["action"] == "unknown" and "not in this roster" in r["detail"])
    for code in ("http://hub.example.test/g/", "http://hub.example.test/g/-bad",
                 "http://hub.example.test/g/a/b", "http://hub.example.test/games/3"):
        check(f"not a label, so not treated as one: {code}",
              api("POST", "/api/scan", {"code": code})[1]["action"] == "unknown")
    lan = next((a for a in _lan_addrs()
                if "." in a), None)
    if lan:
        check("from the network -> 403", api("POST", "/api/scan", {"code": "GATBOX:NEW"}, host=lan)[0] == 403)
        # The Wi-Fi endpoints follow the same rule, and for a second reason: a join asked
        # for over the hotspot would cut the connection making the request.
        check("wifi GET from the network -> 403", api("GET", "/api/wifi", host=lan)[0] == 403)
        check("wifi POST from the network -> 403",
              api("POST", "/api/wifi", {"ssid": "x"}, host=lan)[0] == 403)
        check("wifi DELETE from the network -> 403", api("DELETE", "/api/wifi/x", host=lan)[0] == 403)

    print("labels:")
    try:
        import qrcode  # noqa: F401
        have_qr = True
    except ImportError:
        have_qr = False
        print("  skip  (python3-qrcode not installed yet: the bootstrap installs it)")
    if have_qr:
        out = f"{T}/labels.pdf"
        r = subprocess.run([os.path.join(REPO, "tools/gatbox-labels"), "-o", out], capture_output=True, text=True,
                           env=dict(os.environ, GATBOX_DATA=f"{T}/data", MPLCONFIGDIR=f"{T}/cache/mpl"), timeout=120)
        pdf = open(out, "rb").read() if os.path.exists(out) else b""
        check("gatbox-labels: 3 machines (not retired), command card + 1 page",
              "3 machines, 2 pages" in r.stdout and pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages") == 2)
        req = urllib.request.urlopen(f"{B}/labels.pdf", timeout=120)
        check("/labels.pdf served", req.status == 200 and req.read(5) == b"%PDF-")
    web = open(f"{T}/web.log").read()
    check("server log: no errors", "Traceback" not in web and "error on" not in web)
    print(f"scan: {passed} passed, {failed} failed")


try:
    main()
finally:
    for p in procs:
        p.terminate()
        p.wait(10)
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
