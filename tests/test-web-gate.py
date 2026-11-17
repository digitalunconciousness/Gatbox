#!/usr/bin/env python3
"""gatbox-web's write gate. A client at 127.0.0.2 stands in for a phone on the network: it is not the Pi's own screen
(is_local() is 127.0.0.1 and ::1 only). Temp dirs and a spare port; the real logger, state and roster are never touched."""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8104
T = tempfile.mkdtemp(prefix="gatbox-gate-")
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def env():
    return dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=f"{T}/state", STATE_DIRECTORY=f"{T}/state",
                CACHE_DIRECTORY=f"{T}/cache", GATBOX_MANUALS=f"{T}/manuals", GATBOX_ROMS=f"{T}/roms", GATBOX_LOGDIR=f"{T}/log",
                GATBOX_RUNDIR=f"{T}/run", GATBOX_WEB_PORT=str(PORT), GATBOX_SYNC_STATE=f"{T}/sync", MPLCONFIGDIR=f"{T}/cache/mpl",
                GATBOX_LIBRARY=f"{T}/state/none.sqlite")


def call(method, path, body=None, src="127.0.0.1", headers=None, raw=None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20, source_address=(src, 0))
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    h = {"Content-Type": ctype, "Content-Length": str(len(data)), "Host": f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    hdrs = dict(r.getheaders())
    c.close()
    return r.status, out, hdrs


def phone(method, path, body=None, **kw):
    return call(method, path, body, src="127.0.0.2", **kw)[0]


def main():
    for d in ("data", "state", "cache", "log", "run", "manuals", "roms", "sync"):
        os.makedirs(f"{T}/{d}", exist_ok=True)
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    json.dump({"meta": {}, "video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x", "risk": [],
                                            "faults": [], "parts": [], "notes": ""}]},
              open(f"{T}/data/gatbox-barcade-roster.json", "w"))
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    web = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-web")], env=env(),
                           stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                call("GET", "/kiosk/state")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("gatbox-web didn't start")

        print("from a phone on the network: the Pi's own screen's changes are refused")
        refused = [("POST", "/api/roster", {"slug": "x", "name": "X"}), ("PUT", "/api/roster/widget-wars", {"name": "Y"}),
                   ("DELETE", "/api/machine", None), ("PUT", "/api/phase", {"phase": "x"}), ("POST", "/api/touchtest", {}),
                   ("POST", "/api/dump", {"op": "read"}), ("POST", "/api/disk/read", {}), ("POST", "/api/disk/check", {}),
                   ("POST", "/api/disk/import", {}), ("POST", "/api/disk/file", {}), ("POST", "/api/disk/mame", {}),
                   ("POST", "/api/disk/checklist/new", {}), ("POST", "/api/orders", {}), ("DELETE", "/api/orders/x", None),
                   ("POST", "/api/wifi", {}), ("DELETE", "/api/wifi/x", None), ("POST", "/api/specs/widget-wars/confirm", {}),
                   ("PUT", "/api/actuals/widget-wars", {}), ("DELETE", "/api/actuals/widget-wars", None),
                   ("POST", "/api/library/games", {}), ("POST", "/api/scan", {"code": "widget-wars"}),
                   ("POST", "/kiosk/shutdown", None), ("POST", "/api/browse/index", {})]
        got = {(m, p): phone(m, p, b) for m, p, b in refused}
        check("every one of them: 403", all(c == 403 for c in got.values()))
        for k, c in got.items():
            if c != 403:
                print(f"        {k[0]} {k[1]} -> {c}")
        st, out, _ = call("DELETE", "/api/machine", src="127.0.0.2")
        check("... saying where changes are made", st == 403 and b"the Pi's own screen" in out)

        print("from a phone: the cabinet's own changes still work")
        check("PUT /api/machine (which machine)", phone("PUT", "/api/machine", {"slug": "widget-wars"}) == 200)
        check("POST /api/mark", phone("POST", "/api/mark", {"source": "phone"}) in (200, 201, 202))
        check("POST /api/disk/checklist (a tick on the swap checklist) is not refused by the gate",
              phone("POST", "/api/disk/checklist", {"item": "five_volts", "done": True}) != 403)
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20, source_address=("127.0.0.2", 0))
        c.request("POST", "/control", body=b"action=stop", headers={"Content-Type": "application/x-www-form-urlencoded",
                                                                      "Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}"})
        r = c.getresponse()
        r.read()
        c.close()
        check("POST /control from the phone's own page: Start/Stop still works (303)", r.status == 303)

        print("a change sent by another site's page (CSRF) is refused, even the phone's own")
        evil = {"Origin": "http://evil.example"}
        check("POST /control with another site's Origin: 403", call("POST", "/control", raw=b"action=stop",
                                                                     ctype="application/x-www-form-urlencoded", src="127.0.0.2",
                                                                     headers=evil)[0] == 403)
        check("POST /api/mark with Sec-Fetch-Site: cross-site: 403",
              call("POST", "/api/mark", {"source": "phone"}, src="127.0.0.2", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403)
        check("PUT /api/machine with Origin: null (a sandboxed page): 403",
              call("PUT", "/api/machine", {"slug": "widget-wars"}, src="127.0.0.2", headers={"Origin": "null"})[0] == 403)
        check("the same request from the phone's own page (same Origin): taken",
              call("PUT", "/api/machine", {"slug": "widget-wars"}, src="127.0.0.2",
                   headers={"Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin"})[0] == 200)

        print("the Pi's own screen: unchanged")
        check("DELETE /api/machine from 127.0.0.1: allowed", call("DELETE", "/api/machine")[0] in (200, 204))
        check("reads from a phone: still allowed", call("GET", "/api/roster", src="127.0.0.2")[0] == 200
              and call("GET", "/", src="127.0.0.2")[0] == 200)

        print("headers on every response")
        _, _, h = call("GET", "/api/system")
        _, _, hp = call("GET", "/")
        check("nosniff, no framing, same-origin referrer: on the API and the phone page",
              all(x.get("X-Content-Type-Options") == "nosniff" and x.get("X-Frame-Options") == "DENY"
                  and x.get("Referrer-Policy") == "same-origin" for x in (h, hp)))
    finally:
        web.terminate()
        web.wait(timeout=10)
        if os.environ.get("TEST_KEEP"):
            print("kept", T)
        else:
            shutil.rmtree(T, ignore_errors=True)
    print(f"web gate: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
