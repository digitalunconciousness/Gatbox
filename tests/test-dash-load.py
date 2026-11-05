#!/usr/bin/env python3
"""The dashboard after a load that went wrong. 2026-11-05: an install wrote the dashboard's files, the open 7" page saw
the new version and reloaded at once, and its stylesheet and script requests met gatbox-web restarting a second or two
later. The page came up with neither: no stylesheet, so the SHUT DOWN overlay (hidden only by .hide) covered the screen
saying "Shutting down…"; no script, so nothing could reload it. It stayed that way for two hours.

gatbox-web from the checkout serves a temp copy of web/dash (so the test can change the dashboard's version), and
headless Chromium refuses chosen requests (Network.setBlockedURLs) the way a restarting server would:
    no stylesheet: the overlay stays hidden, and the page reloads itself once the stylesheet can be had;
    no script: the page reloads itself once the script can be had;
    an install: the page doesn't reload into the restart that follows the new files;
    SHUT DOWN still raises the overlay.
Temp dirs and a spare port only.
    python3 tests/test-dash-load.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from cdp import Chrome  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-dash-load-")
PORT = 8094
B = f"http://127.0.0.1:{PORT}"
URL = B + "/dash/"
PAGE_FILES = ["*/dash/*.css", "*/dash/*.js"]     # what the 2026-11-05 reload didn't get (index.html it did)
passed = failed = 0

STYLED = "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() === '#150a28'"
RUNNING = "document.getElementById('h-clock').textContent !== '--:--:--'"     # dash.js's tick() sets the clock
OVERLAY_SHOWN = "getComputedStyle(document.getElementById('kx-off')).display !== 'none'"


def check(name, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def settles(c, js, timeout):
    """True once *js* is, or False after *timeout*. A failed load leaves Chromium's error page, where *js* throws."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if c.eval(f"!!({js})"):
                return True
        except RuntimeError:
            pass
        time.sleep(0.25)
    return False


def block(c, *patterns):
    c.send("Network.setBlockedURLs", {"urls": list(patterns)})


def load(c, *patterns):
    """A fresh tab on the dashboard, with *patterns* refused from its first request on."""
    c.open("about:blank")
    c.send("Network.enable")
    block(c, *patterns)
    c.events.clear()
    c.send("Page.navigate", {"url": URL})
    c.event("Page.loadEventFired")
    c.eval("window.firstLoad = true")          # gone once the page reloads


def came_back(c, timeout=20):
    return settles(c, f"!window.firstLoad && {STYLED} && {RUNNING}", timeout)


def next_poll(c, timeout=15):
    """Wait for this tab's next /api/system answer (it polls every 10 s; the earlier tabs are still open and polling)."""
    c.events.clear()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            e = c.event("Network.responseReceived", timeout=max(0.1, end - time.monotonic()))
        except TimeoutError:
            return False
        if e.get("sessionId") == c.sid and "/api/system" in e["params"]["response"]["url"]:
            return True
    return False


def start_web():
    shutil.copytree(os.path.join(REPO, "web/dash"), f"{T}/dash")
    for d in ("ctrl", "cache", "wifi", "sync", "log", "run", "data", "spool", "roms", "manuals", "lib", "mspool"):
        os.makedirs(f"{T}/{d}", exist_ok=True)
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), GATBOX_WEB_DASH=f"{T}/dash", STATE_DIRECTORY=f"{T}/ctrl",
               CACHE_DIRECTORY=f"{T}/cache", GATBOX_WIFI_SPOOL=f"{T}/wifi", GATBOX_SYNC_STATE=f"{T}/sync",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data", MPLCONFIGDIR=f"{T}/cache/mpl",
               GATBOX_DUMP_SPOOL=f"{T}/spool", GATBOX_ROMS=f"{T}/roms", GATBOX_MINIPRO_PARTS=f"{T}/parts.txt",
               GATBOX_MAME_ROMS=f"{T}/mame-roms.json", GATBOX_MANUALS=f"{T}/manuals",
               GATBOX_LIBRARY=f"{T}/lib/library.sqlite", GATBOX_MAME_TITLES=f"{T}/mame-titles.json",
               GATBOX_MANUALS_SPOOL=f"{T}/mspool")
    p = subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                         stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(50):
        try:
            urllib.request.urlopen(B + "/kiosk/state", timeout=2).read()
            return p
        except OSError:
            time.sleep(0.1)
    raise SystemExit("gatbox-web didn't start; see " + f"{T}/web.log")


def main():
    web = start_web()
    try:
        with Chrome(f"{T}/browser") as c:
            print("no stylesheet")
            load(c, "*/dash/dash.css")
            check("the page has no stylesheet (the test's premise)", not c.eval(STYLED))
            check("the SHUT DOWN overlay stays hidden without it", not c.eval(OVERLAY_SHOWN))
            block(c)
            check("the page reloads itself, styled and running, once the stylesheet can be had", came_back(c))

            print("no script")
            load(c, "*/dash/dash.js")
            check("the page has no script (the test's premise)", not c.eval(RUNNING))
            block(c)
            check("the page reloads itself, styled and running, once the script can be had", came_back(c))

            print("an install: new files, then gatbox-web restarts")
            load(c)
            check("the page is up", settles(c, f"{STYLED} && {RUNNING}", 10))
            check("the page polls (and knows the installed version)", next_poll(c))
            block(c, *PAGE_FILES)                 # gatbox-web restarting: the page's files can't be had...
            st = os.stat(f"{T}/dash/dash.css")    # ...and the install has just written them (a new version)
            os.utime(f"{T}/dash/dash.css", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
            check("the page polls and sees the new version", next_poll(c))
            time.sleep(1)                         # an install restarts gatbox-web within a second or two
            check("it doesn't reload into the restart", c.eval("!!window.firstLoad"))
            block(c)
            check("it reloads after the restart, styled and running", came_back(c))

            print("SHUT DOWN")
            load(c)
            c.eval("document.querySelector('#nav [data-view=system]').click()")
            check("the 7\"'s own buttons are there (the test is at 127.0.0.1)",
                  settles(c, "!document.getElementById('sy-kiosk').classList.contains('hide')", 10))
            c.eval("document.querySelector('#sy-kiosk [data-act=shutdown]')"
                   ".dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            check("a 3 s hold raises the overlay over the whole screen",
                  settles(c, f"{OVERLAY_SHOWN} && document.getElementById('kx-off').getBoundingClientRect().height"
                             " === innerHeight", 6))
            state = json.loads(urllib.request.urlopen(B + "/kiosk/state", timeout=5).read())
            check("and asked gatbox-web to shut down", bool(state.get("shutdown_at")))
    finally:
        os.killpg(web.pid, 15)
        web.wait(5)
        shutil.rmtree(T, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
