#!/usr/bin/env python3
"""Headless Chromium for the dashboard tests, driven over the DevTools protocol on a pipe (stdlib only).

Talks CDP over --remote-debugging-pipe (JSON messages ending in NUL, written to the browser's fd 3 and read from
its fd 4), since there's no websocket module on the Pi. --password-store=basic is essential: without it every http
page hangs before the request is sent (2026-09-28: the net log stops right after the privacy-mode step, where
cookies are needed and Chromium waits on the desktop keyring for their key; data: URLs, which need no cookies,
worked). The kiosk launcher passes the same flag for the same reason.

    with Chrome(profile_dir) as c:
        c.open("http://127.0.0.1:8096/dash/", 1024, 600)
        c.wait("document.querySelector('#hero') !== null")
        c.eval("…")
        c.shot("meter.png")

Also a CLI: cdp.py URL OUT.png [WIDTH HEIGHT] [WAIT-JS]
"""
import base64
import json
import os
import select
import subprocess
import sys
import time


class Chrome:
    def __init__(self, profile, browser="chromium"):
        self.profile, self.browser = profile, browser
        self.n, self.buf, self.events, self.sid = 0, b"", [], None

    def __enter__(self):
        to_r, self.to_w = os.pipe()          # we write, the browser reads on its fd 3
        self.from_r, from_w = os.pipe()      # the browser writes on its fd 4, we read

        def fds():                           # in the child: the pipe ends become fd 3 and 4, kept open across exec
            a, b = os.dup(to_r), os.dup(from_w)   # via fresh fds: dup2(3, 3) would keep os.pipe()'s close-on-exec
            os.dup2(a, 3)
            os.dup2(b, 4)
        self.p = subprocess.Popen(
            [self.browser, "--headless", "--disable-gpu", "--remote-debugging-pipe", f"--user-data-dir={self.profile}",
             "--no-first-run", "--disable-background-networking", "--disable-component-update", "--no-proxy-server", "--password-store=basic",
             "--hide-scrollbars", "--force-color-profile=srgb", "about:blank"],
            preexec_fn=fds, close_fds=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_r)
        os.close(from_w)
        return self

    def __exit__(self, *a):
        try:
            self.send("Browser.close", timeout=5)
        except Exception:
            pass
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()
        os.close(self.to_w)
        os.close(self.from_r)

    def _read(self, timeout):
        end = time.monotonic() + timeout
        while b"\0" not in self.buf:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.from_r], [], [], left)[0]:
                raise TimeoutError("no answer from the browser")
            chunk = os.read(self.from_r, 1 << 20)
            if not chunk:
                raise EOFError("the browser went away")
            self.buf += chunk
        msg, self.buf = self.buf.split(b"\0", 1)
        return json.loads(msg)

    def send(self, method, params=None, timeout=30, session=True):
        self.n += 1
        m = {"id": self.n, "method": method, "params": params or {}}
        if session and self.sid:
            m["sessionId"] = self.sid
        os.write(self.to_w, json.dumps(m).encode() + b"\0")
        while True:
            r = self._read(timeout)
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result", {})
            self.events.append(r)

    def event(self, name, timeout=30):
        end = time.monotonic() + timeout
        while True:
            for i, e in enumerate(self.events):
                if e.get("method") == name:
                    return self.events.pop(i)
            self.events.append(self._read(max(0.01, end - time.monotonic())))

    def open(self, url, width=1024, height=600):
        tid = self.send("Target.createTarget", {"url": "about:blank"}, session=False)["targetId"]
        self.sid = self.send("Target.attachToTarget", {"targetId": tid, "flatten": True}, session=False)["sessionId"]
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600})
        self.send("Page.enable")
        self.events.clear()
        self.send("Page.navigate", {"url": url})
        self.event("Page.loadEventFired")

    def eval(self, js, timeout=30):
        r = self.send("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"js: {r['exceptionDetails'].get('exception', {}).get('description', r)}")
        return r.get("result", {}).get("value")

    def wait(self, js, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.eval(f"!!({js})"):
                return True
            time.sleep(0.1)
        raise TimeoutError(f"never true: {js}")

    def shot(self, path):
        data = self.send("Page.captureScreenshot", {"format": "png"})["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))


if __name__ == "__main__":
    url, out = sys.argv[1], sys.argv[2]
    w, h = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1024, 600)
    with Chrome(os.path.join(os.path.dirname(os.path.abspath(out)), ".cdp-profile")) as c:
        c.open(url, w, h)
        if len(sys.argv) > 5:
            c.wait(sys.argv[5])
        c.shot(out)
    print(out)
