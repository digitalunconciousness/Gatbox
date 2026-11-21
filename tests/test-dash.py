#!/usr/bin/env python3
"""The M5 dashboard, end to end: gatbox-raillog over tools/gatbox-replay, gatbox-web from the checkout, and the page
in headless Chromium (tests/cdp.py) at the 7" panel's 1024x600. Each state the spec lists is driven for real (the
logger reads a replayed CSV, the page is tapped like a finger would), checked in the DOM, and screenshotted:

    idle (no simulated data), normal rail, marks, power off and on, the over-voltage alarm (2+ readings) + ACK,
    a lone spike, HOLD, a dial change, OL, the 09-25 fixture's 20 V burst, a profile change (keypad), setting and
    clearing the machine, sessions (the fixture's report + chart), system, devices, dump, and a phone-size view.

Temp dirs and spare ports only; a synthetic two-entry roster stands in for the real one.
    python3 tests/test-dash.py [SCREENSHOT_DIR]      (no dir: the screenshots are taken, then thrown away)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from cdp import Chrome  # noqa: E402
from fakedisk import MiB, FakeDisks, fat_card  # noqa: E402
import fakeimage  # noqa: E402
from fakearchive import start_archive

KEEP = len(sys.argv) > 1
OUT = sys.argv[1] if KEEP else tempfile.mkdtemp(prefix="gatbox-dash-shots-")
os.makedirs(OUT, exist_ok=True)
T = tempfile.mkdtemp(prefix="gatbox-dash-")
PORT = 8096
B = f"http://127.0.0.1:{PORT}"
OHM = "Ω"                                     # sigrok's OHM SIGN
FX = "/var/log/gatbox/rail_20260925_021402.csv"
passed = failed = 0
procs = {}
FD = None                                     # the fake drives (tests/fakedisk.py): the DISK tab's


def settles(c, js, timeout=20):
    """True once *js* is, or False after *timeout*. Chrome.wait raises; a check wants a
    boolean -- and these assertions wait on the SYSTEM view's own repaint rather than on a
    sleep that has to guess its period."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if c.eval(f"!!({js})"):
            return True
        time.sleep(0.25)
    return False


# The keypad at whatever size the viewport is now: how many keys are cut off (by the key grid's
# own scrolling or by the bottom of the screen), whether SAVE is on screen, the shortest key.
KP_FIT = """(() => {
  const k = document.querySelector('#pane .keys'); if (!k) return null;
  const kb = k.getBoundingClientRect(), bs = [...k.querySelectorAll('button')];
  const cut = bs.filter(b => { const r = b.getBoundingClientRect();
                               return r.top < kb.top - 1 || r.bottom > Math.min(kb.bottom, innerHeight) + 1; }).length;
  const s = [...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAVE').getBoundingClientRect();
  return {cut, keys: bs.length, save: s.top >= 0 && s.bottom <= innerHeight && s.height > 0,
          shortest: Math.round(Math.min(...bs.map(b => b.getBoundingClientRect().height)))};
})()"""


def swap_file(path, text):
    """Replace a file whole (gatbox-web may read it at any moment: never let it see half of one)."""
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


# A picker's choices: how many, and the shortest (a finger needs >= 64 px on the 7").
PICK_ROWS = """(() => { const rs = [...document.querySelectorAll('#pane .picklist .row')];
  return {n: rs.length, min: rs.length ? Math.round(Math.min(...rs.map(r => r.getBoundingClientRect().height))) : 0}; })()"""
# The 7" MACHINE page: the work orders / job card above the first folded section.
MC_ORDERS_FIRST = """(() => {
  const o = [...document.querySelectorAll('#mc-body .card')].find(c => {
    const h = ((c.querySelector('h3') || {}).textContent || ''); return h === 'Work orders' || h.startsWith('Job'); });
  const f = document.querySelector('#mc-body button.fold');
  return !!(o && f) && o.getBoundingClientRect().top < f.getBoundingClientRect().top; })()"""
KB_LETTERS = {}              # the number pad's top on the 7", for the letters keyboard to match
# The 7" box: the frame (header, nav, main) inside the bezel margin, and the smallest visible text.
BOX_FRAME = """(() => { const bad = [];
  for (const s of ['header', 'nav', 'main']) { const r = document.querySelector(s).getBoundingClientRect();
    if (r.left < 19.5 || r.top < 15.5 || r.right > innerWidth - 19.5 || r.bottom > innerHeight - 15.5)
      bad.push(s + ' ' + [r.left, r.top, r.right, r.bottom].map(Math.round).join(',')); }
  return bad; })()"""
BOX_SMALL = """(() => { const out = new Set();
  for (const root of [document.querySelector('header'), document.querySelector('nav'), document.querySelector('.view.on')]) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode()); ) {
      if (!n.textContent.trim()) continue;
      const e = n.parentElement, r = e.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(e).visibility === 'hidden') continue;
      const f = parseFloat(getComputedStyle(e).fontSize);
      if (f < 14) out.add(e.tagName.toLowerCase() + '.' + e.className + ' ' + f + 'px ' + n.textContent.trim().slice(0, 24));
    } }
  return [...out].slice(0, 6); })()"""


# The 7" keyboard: one block, one size, wherever it opens. Its top, height, the row heights, keys cut off, and where
# CANCEL / SAVE end.
KB_BOX = """(() => {
  const k = document.querySelector('#pane .keys'), a = document.querySelector('#pane .kp-actions');
  if (!k || !a) return null;
  const kr = k.getBoundingClientRect(), ar = a.getBoundingClientRect(), bs = [...k.querySelectorAll('button')];
  const cut = bs.filter(b => { const r = b.getBoundingClientRect();
                               return r.top < kr.top - 1 || r.bottom > Math.min(kr.bottom, innerHeight) + 1; }).length;
  return {top: Math.round(kr.top), h: Math.round(kr.height), cut, act_bottom: Math.round(ar.bottom),
          act_left: Math.round(ar.left), act_right: Math.round(ar.right),
          rows: [...new Set(bs.filter(b => !b.classList.contains('x4')).map(b => Math.round(b.getBoundingClientRect().height)))]};
})()"""
# Type n characters with a real keyboard's keydown (the keypad takes those too), then: is the cursor inside the field?
TYPE_LONG = """((n) => { for (let i = 0; i < n; i++) document.dispatchEvent(new KeyboardEvent('keydown', {key: 'W'}));
  const f = document.querySelector('#pane .kp-field'), cur = f && f.querySelector('.cur');
  if (!cur) return false;
  const fr = f.getBoundingClientRect(), cr = cur.getBoundingClientRect();
  return cr.right <= fr.right + 1 && cr.left >= fr.left - 1 && cr.bottom <= fr.bottom + 1 && cr.top >= fr.top - 1; })"""
UNTYPE = """((n) => { for (let i = 0; i < n; i++) document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Backspace'})); return true; })"""


def kp_fits(c, sizes):
    """The open keypad at each (width, height), as a phone sees it (box mode off): every key and SAVE on screen, keys at
    least 26 px. Back to the 7" (box mode on) after."""
    c.eval("document.documentElement.classList.remove('box')")
    bad = {}
    for w, h in sizes:
        c.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.3)
        m = c.eval(KP_FIT)
        if not m or m["cut"] or not m["save"] or m["shortest"] < 26:
            bad[f"{w}x{h}"] = m
    c.send("Emulation.setDeviceMetricsOverride", {"width": 1024, "height": 600, "deviceScaleFactor": 1, "mobile": False})
    c.eval("document.documentElement.classList.add('box')")
    time.sleep(0.3)
    if bad:
        print("    ", bad)
    return not bad


def check(name, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def api(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, data=json.dumps(body or {}).encode() if method != "GET" else None,
                                 headers={"Content-Type": "application/json"} if method != "GET" else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def mkcsv(name, rows):
    p = os.path.join(T, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("iso_time,epoch,value,unit,flags,uptime_s\n# clock=ntp\n")
        for i, r in enumerate(rows):
            f.write(f"2026-09-21T00:00:00,{1790000000 + i}.5,{r},{1000 + i}.00\n")
    return p


def stop(name):
    p = procs.pop(name, None)
    if p:
        try:
            os.killpg(p.pid, 15)
        except ProcessLookupError:
            pass
        p.wait(10)


def logger(csv, rate=10):
    """The real logger over a replay (the previous one stopped first: a new session each time)."""
    stop("logger")
    env = dict(os.environ, GATBOX_SIGROK=os.path.join(REPO, "tools/gatbox-replay"), GATBOX_REPLAY_FILE=csv,
               GATBOX_REPLAY_RATE=str(rate), GATBOX_REPLAY_HOLD="1", GATBOX_DMM_PORT=f"{T}/port",
               GATBOX_LOGDIR=f"{T}/log", RUNTIME_DIRECTORY=f"{T}/run", GATBOX_CTRL=f"{T}/ctrl", GATBOX_LED=f"{T}/led",
               GATBOX_CLOCK_WAIT="0", GATBOX_NTP_SYNCED="yes", GATBOX_META=os.path.join(REPO, "backend/gatbox-meta"))
    procs["logger"] = subprocess.Popen(["bash", os.path.join(REPO, "backend/gatbox-raillog")], env=env,
                                       stdout=open(f"{T}/journal", "a"), stderr=subprocess.STDOUT, start_new_session=True)


def rep(n, row):
    return [row] * n


def setup():
    try:                                              # a server already on the port would answer for ours
        api("GET", "/kiosk/state")
        raise SystemExit(f"port {PORT} is already in use: stop whatever runs there first")
    except OSError:
        pass
    global FD
    for d in ("log", "run", "ctrl", "cache", "data", "led", "spool", "dspool", "mspool"):
        os.makedirs(os.path.join(T, d))
    # the DISK tab's drives: one on the dock, the image store, an empty card slot (invented names, plain files)
    FD = FakeDisks(f"{T}/disk")
    FD.usb_disk("sda", 3 * MiB, "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=os.urandom(3 * MiB))
    FD.usb_disk("sdc", 64 * MiB, "WD", "Widget Store 4T", port=2, devnum="8:32", parts=["sdc1"])
    FD.usb_disk("sdd", 0, "Generic", "Card Reader CF", port=3, devnum="8:48")
    FD.mount_table()
    with open(f"{T}/led/trigger", "w") as f:
        f.write("[mmc0] none\n")
    open(f"{T}/led/brightness", "w").close()
    open(f"{T}/port", "w").close()
    for f in ("profiles.json", "gatbox-machine-specs.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    names = subprocess.run([os.path.join(REPO, "tests/fake-minipro"), "-q", "T48", "-l"], capture_output=True, text=True).stdout
    open(f"{T}/parts.txt", "w").write("# minipro 0.7.4 T48\n" + names)
    open(f"{T}/rom.bin", "wb").write(os.urandom(262144))
    with open(f"{T}/data/gatbox-barcade-roster.json", "w") as f:
        json.dump({"meta": {"platforms": {"test_hdd": {"desc": "Test platform with a hard drive", "faults": ["drive failure"],
                                                       "parts": ["CF adapter"], "pm": "image the drive"}},
                            "risk_legend": {"hdd": "aging drive"},
                            "critical_actions": {"image_drive_now": {"note": "Image the drive.", "ide_to_cf_ssd": ["Gauntlet Legends"]}}},
                   "video_games": [{"slug": "gauntlet-legends", "name": "Gauntlet Legends", "platform": "test_hdd",
                                    "mfr": "Atari/Midway", "risk": ["hdd"], "faults": [], "parts": [], "notes": "test entry"},
                                   {"slug": "gauntlet", "name": "Gauntlet", "platform": "test_hdd", "mfr": "Atari",
                                    "risk": [], "faults": [], "parts": [], "notes": ""}],
                   "pinball": [], "retired": []}, f)
    if os.path.exists(FX):
        shutil.copy(FX, f"{T}/log/")
    # the MAME chip list (built at install on the Pi; here by hand) + one archived dump that ticks a chip
    rom = lambda n, h: {"name": n, "size": 524288, "crc": h * 8, "sha1": h * 40, "region": "user1"}   # noqa: E731
    with open(f"{T}/mame-roms.json", "w") as f:
        json.dump({"meta": {"mame": "0.276 (mame0276)", "key": "test"}, "machines": {
            "gauntlet-legends": {"set": "gauntleg", "status": "sure", "why": "", "prefer": "gauntleg", "versions": [
                {"name": "gauntleg", "desc": "Gauntlet Legends (version 1.6)", "year": "1998", "mfr": "Atari Games",
                 "parent": True, "roms": [rom("legend15.u10", "a"), rom("legend15.u11", "b")],
                 "disks": [{"name": "gauntleg", "sha1": "c" * 40, "region": "ide:0:hdd"}]},
                {"name": "gauntleg12", "desc": "Gauntlet Legends (version 1.2)", "year": "1998", "mfr": "Atari Games",
                 "parent": False, "roms": [rom("legend12.u10", "d")], "disks": []}]},
            "gauntlet": {"set": None, "status": "none", "why": "test: no set", "prefer": None}}}, f)
    os.makedirs(f"{T}/roms/gauntlet-legends")
    with open(f"{T}/roms/gauntlet-legends/LEGEND15-U10_aaaaaaaa.bin", "wb") as f:      # the sidecar's chip, as far as CHECK reads it
        f.write(b"fixture bytes of a chip")
    with open(f"{T}/roms/gauntlet-legends/LEGEND15-U10_aaaaaaaa.json", "w") as f:
        json.dump({"label": "LEGEND15-U10", "part": "27C040@DIP32", "sha1": "a" * 40, "date": "2026-09-29T10:00:00-05:00",
                   "romident": {"match": True, "matches": [{"set": "gauntleg", "rom": "legend15.u10", "description": "x"}]}}, f)
    # a burn for the same machine, and where gatbox-sync says the dump and the burn stand (roms-status.json): the dump is waiting
    # for a hub, the burn went to WORKSHOP
    with open(f"{T}/roms/burns.jsonl", "w") as f:
        f.write(json.dumps({"epoch": 1790000400.0, "state": "verified", "image": f"{T}/roms/gauntlet-legends/LEGEND15-U10_aaaaaaaa.bin",
                            "sha1": "a" * 40, "size": 524288, "part": "27C040@DIP32", "vpp": "13V", "machine": "gauntlet-legends",
                            "blank_ok": True, "error": None}) + "\n")
    os.makedirs(f"{T}/sync", exist_ok=True)
    with open(f"{T}/sync/roms-status.json", "w") as f:
        json.dump({"at": 1790000500.0, "items": {
            "dump:gauntlet-legends/LEGEND15-U10_aaaaaaaa.bin": {"state": "waiting", "hub": "WORKSHOP", "reason": "no hub reachable"},
            f"burn:1790000400.000:{'a' * 40}": {"state": "sent", "hub": "WORKSHOP"}}}, f)
    # a made-up 3-page manual for Gauntlet Legends (the fetch tool's output: PDF + sidecar + text) and its spec sheet
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    md = f"{T}/manuals/gauntlet-legends"
    os.makedirs(md)
    with PdfPages(f"{md}/gl-manual.pdf") as pp:
        for text in ("GAUNTLET LEGENDS OPERATIONS MANUAL", "PRODUCT SPECIFICATIONS\n+5 VDC 4.75 to 5.25 V\nFUSE F1 5A SLO-BLO",
                     "SELF-TEST MENU"):
            fig = plt.figure(figsize=(8.5, 11))
            fig.text(0.1, 0.8, text, fontsize=16)
            pp.savefig(fig)
            plt.close(fig)
    subprocess.run(["pdftotext", "-layout", f"{md}/gl-manual.pdf", f"{md}/gl-manual.txt"], check=True)
    json.dump({"id": "gl-manual", "title": "Gauntlet Legends Operations Manual", "kind": "manual", "pages": 3,
               "added": "fetch"}, open(f"{md}/gl-manual.json", "w"))
    json.dump({"machines": {"gauntlet-legends": {
        "docs": [{"id": "gl-manual", "title": "Gauntlet Legends Operations Manual", "kind": "manual", "url": "https://archive.org/x"}],
        "specs": {"rails": [{"rail": "+5V", "lo": 4.75, "hi": 5.25, "doc": "gl-manual", "page": 2, "quote": "+5 VDC 4.75 to 5.25 V"}],
                  "sheet": [{"what": "Fuse F1", "value": "5 A slow-blow", "doc": "gl-manual", "page": 2, "quote": "FUSE F1 5A SLO-BLO"}]}}}},
              open(f"{T}/data/gatbox-manuals.json", "w"))
    # A spool with something already in it, so the card has saved networks to show. The key
    # is here deliberately: the test asserts it reaches no part of the page.
    os.makedirs(f"{T}/wifi", exist_ok=True)
    # gatbox-sync's cache: one order already open on the hub for this machine, so the card
    # has something to offer attaching a trace to.
    os.makedirs(f"{T}/sync", exist_ok=True)
    json.dump({"at": 1790000000.0, "open": {"gauntlet-legends": [
        {"id": 412, "external_id": None, "issue": "Rail sags under load",
         "priority": "High", "status": "Open", "source": "web",
         "reported": 1790000000.0}]}}, open(f"{T}/sync/orders.json", "w"))
    # What the bootstrap recorded: a release, and the checkout had a local change.
    json.dump({"describe": "v2026.11.21-dirty", "commit": "0" * 40, "branch": "main", "local_changes": True},
              open(f"{T}/installed.json", "w"))
    json.dump({"at": 1790000000.0, "ssids": ["HomeNetwork", "OldArcade"]},
              open(f"{T}/wifi/saved.json", "w"))
    json.dump({"at": 1790000000.0, "ssids": ["HomeNetwork", "CafeOpen"]},
              open(f"{T}/wifi/scan.json", "w"))
    json.dump({"at": 1790000000.0, "action": "join", "ssid": "HomeNetwork", "ok": True,
               "state": "joined", "detail": "hunter2-must-not-appear"},
              open(f"{T}/wifi/status.json", "w"))
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_WIFI_SPOOL=f"{T}/wifi", GATBOX_SYNC_STATE=f"{T}/sync", GATBOX_INSTALLED=f"{T}/installed.json",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_REPORT=os.path.join(REPO, "tools/gatbox-rail-report"),
               GATBOX_DATA=f"{T}/data", MPLCONFIGDIR=f"{T}/cache/mpl", GATBOX_DUMP_SPOOL=f"{T}/spool",
               GATBOX_ROMS=f"{T}/roms", GATBOX_MINIPRO_PARTS=f"{T}/parts.txt", GATBOX_MAME_ROMS=f"{T}/mame-roms.json",
               GATBOX_MANUALS=f"{T}/manuals", GATBOX_LIBRARY=f"{T}/lib/library.sqlite", GATBOX_MAME_TITLES=f"{T}/mame-titles.json",
               GATBOX_MANUALS_SPOOL=f"{T}/mspool", GATBOX_SYSFS=FD.sys, GATBOX_DEV=FD.dev, GATBOX_MOUNTINFO=FD.mountinfo, GATBOX_UDEV=FD.udev,
               GATBOX_DISKS=FD.store, GATBOX_DISK_SPOOL=f"{T}/dspool", GATBOX_BROWSE_SOCK=f"{T}/browse.sock",
               GATBOX_DISK_SEND_STATE=f"{T}/dsend", GATBOX_IMPORT_SETTLE="0")
    # A stand-in fls in front of the real one: it adds one line a drive cannot be made to give here, an entry whose inode another file
    # now holds ("deleted-realloc"), to a folder listing while the file `ghost` is beside it. The browser scrubs its environment, so
    # the stand-in is steered by that file and not by a variable.
    fstub = f"{T}/flsbin"
    os.makedirs(fstub)
    open(f"{fstub}/fls", "w").write(
        "#!/bin/sh\n"
        "here=$(dirname \"$0\")\n"
        f"{shutil.which('fls')} \"$@\"\n"
        "rc=$?\n"
        "case \" $* \" in *\" -r \"*) ;; *) [ -e \"$here/ghost\" ] && printf '%s\\n' "
        "'0|/ghost.png (deleted-realloc)|99999|r/rrw-r--r--|0|0|2048|1794786586|1794786586|1794786586|1794786586';; esac\n"
        "exit $rc\n")
    os.chmod(f"{fstub}/fls", 0o755)
    # the drive browser's service (FILES), over the same fake store, on its own socket (systemd would start it on demand)
    procs["browse"] = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-browse"), "--socket", f"{T}/browse.sock", "--idle", "3000"],
                                       env=dict(os.environ, PATH=f"{fstub}:" + os.environ["PATH"], GATBOX_LIB=os.path.join(REPO, "backend"), GATBOX_DISKS=FD.store,
                                                GATBOX_MOUNTINFO=FD.mountinfo, GATBOX_BROWSE_DIR=f"{FD.store}/_browse",
                                                GATBOX_DISK_SPOOL=f"{T}/dspool"), stderr=subprocess.DEVNULL, start_new_session=True)
    subprocess.run([sys.executable, os.path.join(REPO, "tools/gatbox-mame-roms"), "--titles", f"{T}/mame-titles.json"], check=True,
                   capture_output=True, env=dict(os.environ, GATBOX_MAME=os.path.join(REPO, "tests/fake-mame")))
    os.environ["GATBOX_DATA"] = f"{T}/data"               # the logger's gatbox-meta reads the same data
    procs["web"] = subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                    stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(50):
        try:
            api("GET", "/kiosk/state")
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("gatbox-web didn't start")


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
    setup()
    with Chrome(f"{T}/browser") as c:
        q = lambda js: c.eval(js)                                  # noqa: E731
        text = lambda sel: q(f"(document.querySelector({json.dumps(sel)}) || {{}}).textContent || ''")   # noqa: E731
        banners = lambda: q("[...document.querySelectorAll('#banners .banner')].map(b => b.textContent).join(' | ')")  # noqa: E731
        click = lambda sel: q(f"document.querySelector({json.dumps(sel)}).click()")  # noqa: E731
        shot = lambda n: c.shot(os.path.join(OUT, n + ".png"))     # noqa: E731

        def open_fold(word):                       # a closed section on the 7" machine page: tap its SHOW
            q(f"[...document.querySelectorAll('#mc-body button.fold')].find(b => b.textContent.includes({json.dumps(word)})).click()")
            c.wait(f"[...document.querySelectorAll('#mc-body button.fold.open')].some(b => b.textContent.includes({json.dumps(word)}))", 10)

        def tap_keys(s):
            for ch in s:
                q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(ch)}).click()")
            q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAVE').click()")

        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.includes('rail')")
        print("the script itself:")
        # A second declaration of a function's name in the same scope silently replaces the first. Two branches each add
        # their own `imagesCard` and git merges them without a conflict; one tab then calls the other's.
        decls = re.findall(r"^\s*(?:async )?function ([A-Za-z_$][\w$]*)", open(os.path.join(REPO, "web/dash/dash.js"), encoding="utf-8").read(), re.M)
        check("dash.js declares every function once (a repeat replaces the first without a word)",
              sorted({n for n in decls if decls.count(n) > 1}) == [])
        print("idle:")
        check("no reading, no simulated value", text("#m-value") == "no reading" and "NO READINGS" in banners())
        shot("00-idle")

        print("normal rail:")
        logger(mkcsv("rail.csv", rep(400, "5.02,V,DC AUTO")), rate=10)
        c.wait("document.querySelector('#m-value').textContent === '5.02'", 20)
        time.sleep(3)
        check("5.02 V, IN WINDOW, logging", text("#m-state") == "IN WINDOW" and text("#m-unit") == "V" and "LOGGING" in text("#m-session"))
        check("no banners", banners() == "")
        check("header: LOGGING + clock source", text("#h-logtxt") == "LOGGING" and text("#h-src") in ("NTP", "RTC", "UNVERIFIED"))
        shot("01-normal-rail")

        print("stop logging from METER (the owner: no hunting through SYSTEM for it):")
        stop_btn = "[...document.querySelectorAll('#v-meter button')].find(b => b.textContent === 'STOP LOGGING')"
        check("meter: STOP LOGGING by the logging line while it logs, a full-size button",
              q(f"(() => {{ const b = {stop_btn}; return !!b && b.getClientRects().length > 0 && b.getBoundingClientRect().height >= 56; }})()"))
        q(f"{stop_btn}.click()")
        c.wait("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'STOP')", 5)
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'STOP').click()")
        check("meter: STOP asks first, then the logger stops (LOGGER STOPPED), and the stop button goes",
              settles(c, "document.querySelector('#banners').textContent.includes('LOGGER STOPPED')")
              and api("GET", "/api/meter")["stopped"]           # /api/meter is live; /api/system is cached 2 s
              and not q(f"(() => {{ const b = {stop_btn}; return !!b && b.getClientRects().length > 0; }})()"))
        click("#b-new")                                    # it reads START LOGGING now
        check("meter: START LOGGING (the NEW FILE spot) starts it again",
              settles(c, "!document.querySelector('#banners').textContent.includes('LOGGER STOPPED')")
              and not api("GET", "/api/meter")["stopped"])
        c.wait("document.querySelector('#m-session').textContent.startsWith('LOGGING')", 20)

        print("marks:")
        click("#b-mark")
        c.wait("document.querySelector('#toast').textContent.startsWith('MARK')")
        time.sleep(1)
        f = sorted(os.listdir(f"{T}/log"))[-1]
        check("MARK -> # mark= in the live file", "# mark=" in open(f"{T}/log/{f}").read())
        shot("02-mark")

        print("power off and on:")
        logger(mkcsv("power.csv", rep(30, "5.02,V,DC AUTO") + rep(30, "3.1,mV,DC AUTO") + rep(300, "5.02,V,DC AUTO")), rate=10)
        c.wait("document.querySelector('#m-state').textContent === 'OFF'", 20)
        check("board off: OFF badge", text("#m-state") == "OFF")
        c.wait("document.querySelector('#m-state').textContent === 'IN WINDOW'", 20)
        time.sleep(1)
        check("back on: IN WINDOW", text("#m-state") == "IN WINDOW")
        shot("03-power-off-on")

        print("over-voltage alarm (2+ readings) and a lone spike:")
        logger(mkcsv("ov.csv", rep(20, "5.02,V,DC AUTO") + ["6.40,V,DC AUTO"] + rep(20, "5.02,V,DC AUTO")
                     + ["6.10,V,DC AUTO", "6.25,V,DC AUTO", "6.15,V,DC AUTO"] + rep(300, "5.02,V,DC AUTO")), rate=10)
        c.wait("document.querySelector('#toast').textContent.startsWith('SPIKE')", 20)
        check("lone reading: amber SPIKE toast, no takeover",
              q("document.querySelector('#alarm').classList.contains('hide')"))
        shot("04-spike")
        c.wait("!document.querySelector('#alarm').classList.contains('hide')", 20)
        time.sleep(0.8)
        check("2+ readings: full-screen OVER-VOLTAGE with the peak", "6.250 V" in text("#al-v") and "limit" in text("#al-sub"))
        shot("05-alarm")
        click("#al-ack")
        time.sleep(0.5)
        check("ACK clears it", q("document.querySelector('#alarm').classList.contains('hide')"))
        time.sleep(2)
        shot("06-after-ack")

        print("ALARM OFF:")
        click("#b-alarm")
        c.wait("document.querySelector('#b-alarm').textContent.startsWith('ALARM OFF')")
        logger(mkcsv("ov2.csv", rep(20, "5.02,V,DC AUTO") + ["6.10,V,DC AUTO", "6.25,V,DC AUTO"] + rep(200, "5.02,V,DC AUTO")), rate=10)
        time.sleep(5)
        check("switched off: no takeover", q("document.querySelector('#alarm').classList.contains('hide')"))
        click("#b-alarm")
        c.wait("document.querySelector('#b-alarm').textContent.startsWith('ALARM ON')")

        print("HOLD:")
        logger(mkcsv("hold.csv", rep(10, "5.02,V,DC AUTO") + rep(300, "5.02,V,DC AUTO HOLD")), rate=10)
        c.wait("document.querySelector('#banners').textContent.includes('HOLD IS ON')", 20)
        check("HOLD IS ON banner", "HOLD IS ON" in banners())
        shot("07-hold")

        print("dial change (+5V profile, dial turned to V~):")
        logger(mkcsv("dial.csv", rep(20, "5.02,V,DC AUTO") + rep(300, "0.051,V,AC AUTO")), rate=10)
        c.wait("document.querySelector('#banners').textContent.includes('SET DIAL TO')", 20)
        time.sleep(1)
        check("SET DIAL TO V⎓ banner, AC voltage chip", "SET DIAL TO" in banners() and text("#m-mode") == "AC voltage")
        check("no window badge off the rail mode", q("document.querySelector('#m-state').classList.contains('hide')"))
        shot("08-dial-change")

        print("OL (resistance profile):")
        api("PUT", "/api/meter/profile", {"id": "resistance"})
        logger(mkcsv("ol.csv", rep(10, f"1.20,k{OHM},AUTO") + rep(300, f"inf,T{OHM},AUTO")), rate=10)
        c.wait("document.querySelector('#m-value').textContent === 'OL'", 20)
        time.sleep(1)
        check("OL shown as OL, resistance chip, no dial banner", text("#m-value") == "OL" and text("#m-mode") == "Resistance"
              and "SET DIAL" not in banners())
        check("SAVE READING live on a bench profile, says where it goes",
              not q("document.querySelector('#b-capture').disabled") and text("#b-capture") == "SAVE READINGto unassigned (no machine)")
        shot("09-ol")

        print("save reading (keypad):")
        click("#b-capture")
        c.wait("!document.querySelector('#sheet').classList.contains('hide')")
        shot("10-keypad")
        tap_keys("R12")
        c.wait("document.querySelector('#toast').textContent.startsWith('SAVED')", 10)
        check("reading saved (unassigned)", "R12" in text("#toast") and "unassigned" in text("#toast"))
        c.wait("document.querySelector('#m-saved').textContent.includes('R12')", 10)
        check("... and listed under the reading", text("#m-saved") == "SAVED: R12 OL")
        shot("10b-saved")

        print("the 09-25 fixture's 20 V burst (03:22:40-03:23:25):")
        api("PUT", "/api/meter/profile", {"id": "rail-5v"})
        if os.path.exists(FX):
            burst = os.path.join(T, "burst.csv")
            with open(FX) as fi, open(burst, "w") as fo:
                for line in fi:
                    if line.startswith(("iso", "#")) or "2026-09-25T03:22:40" <= line[:19] <= "2026-09-25T03:23:25":
                        fo.write(line)
            logger(burst, rate=6)
            c.wait("document.querySelector('#toast').textContent.startsWith('SPIKE')", 30)
            time.sleep(14)
            check("glitches arrive as SPIKEs, never a takeover (single readings)",
                  q("document.querySelector('#alarm').classList.contains('hide')"))
            shot("11-fixture-burst")
        else:
            print("  skip  (no 09-25 fixture on this machine)")

        print("profile change (picker + keypad):")
        logger(mkcsv("rail2.csv", rep(600, "5.02,V,DC AUTO")), rate=10)
        c.wait("document.querySelector('#m-value').textContent === '5.02'", 20)
        click("#m-profile")
        c.wait("document.querySelectorAll('#pane .tile').length > 5")
        shot("12-profile-picker")
        q("[...document.querySelectorAll('#pane .tile')].find(t => t.textContent.startsWith('Ripple')).click()")
        c.wait("document.querySelector('#pane .kp-field')")
        nb = q(KB_BOX)
        KB_LETTERS["num_top"] = nb and nb["top"]
        check("number pad on the 7\": the same 274 px block, every key on screen, CANCEL / SAVE inside the margin",
              nb and nb["h"] == 274 and nb["cut"] == 0 and nb["act_bottom"] <= 584)
        check("number pad on a phone held sideways (844x320): every key and SAVE on screen", kp_fits(c, [(844, 320)]))
        tap_keys("150")
        c.wait("document.querySelector('#m-profile').textContent.startsWith('Ripple')", 10)
        c.wait("document.querySelector('#banners').textContent.includes('SET DIAL TO')", 20)
        check("ripple, 0–150 mV (your value), SET DIAL TO V~", "0–150 mV" in text("#m-window") and "your value" in text("#m-window")
              and "SET DIAL TO" in banners())
        shot("13-profile-ripple")
        api("PUT", "/api/meter/profile", {"id": "rail-5v"})

        print("machine: set + clear:")
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-pick').disabled")
        # A real roster is ~95 machines; this one is two, and a picker of eight or fewer is just buttons. For the long
        # list, ten more cabinets for the length of this check (gatbox-web re-reads the roster when it changes).
        roster_f = f"{T}/data/gatbox-barcade-roster.json"
        roster_orig = open(roster_f).read()
        big = json.loads(roster_orig)
        big["video_games"] += [{"slug": f"test-cabinet-{i:02d}", "name": f"Test Cabinet {i:02d}", "platform": "test_hdd",
                                "mfr": "Test", "risk": [], "faults": [], "parts": [], "notes": ""} for i in range(1, 11)]
        swap_file(roster_f, json.dumps(big))
        click("#mc-pick")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 12")
        # On the 7" a picker is buttons first: the machines, big, and no keyboard until SEARCH.
        check("pick machine: big buttons, no keyboard until SEARCH",
              not q("!!document.querySelector('#pane .keys')") and q(PICK_ROWS)["min"] >= 64
              and q("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'SEARCH')"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SEARCH').click()")
        c.wait("document.querySelector('#pane .keys')")
        sk = q(KB_BOX)
        check("pick machine: SEARCH brings the keyboard, the same 274 px block in the same place, every key on screen",
              sk and sk["h"] == 274 and sk["cut"] == 0 and sk["top"] == KB_LETTERS.get("num_top") and sk["act_bottom"] <= 584)
        for ch in "LEG":
            q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(ch)}).click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 1")
        check("pick machine: the matches show above the keyboard", q("(() => { const r = document.querySelector('#pane .picklist .row')"
              ".getBoundingClientRect(), k = document.querySelector('#pane .keys').getBoundingClientRect();"
              " return r.height >= 64 && r.bottom <= k.top; })()"))
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#mc-body').textContent.includes('Gauntlet Legends')", 10)
        swap_file(roster_f, roster_orig)                          # back to the two-machine roster
        time.sleep(0.5)
        # On the 7" the page leads with what gets acted on (the work orders card) and folds the reference away.
        check("machine (7\"): the work orders card comes before the reference", settles(c, MC_ORDERS_FIRST))
        folds = q("[...document.querySelectorAll('#mc-body button.fold')]"
                  ".map(b => [b.querySelector('.fold-t').textContent, b.querySelector('.fold-act').textContent])") or []
        check("machine (7\"): DETAILS, SAVED READINGS, ROM CHIPS, DUMPS start closed, each behind SHOW",
              [f[0] for f in folds] == ["DETAILS", "SAVED READINGS", "ROM CHIPS (MAME)", "DUMPS"]
              and all(f[1] == "SHOW" for f in folds) and "aging drive" not in text("#mc-body"))
        shot("14-machine")
        open_fold("DETAILS")
        check("machine card: name, risk, critical action, rail spec", all(s in text("#mc-body") for s in
              ("Gauntlet Legends", "aging drive", "image drive now", "+5V  4.9–5.1 V")))
        open_fold("ROM")
        c.wait("document.querySelector('#mc-body').textContent.includes('OF 2 DUMPED')", 10)
        check("ROM chips (MAME): 1 of 2 dumped, ✓ with the dump's label, the disk not a T48 job",
              "ROM CHIPS (MAME)" in text("#mc-body").upper()       # the 7" fold's title is in capitals
              and all(s_ in text("#mc-body") for s_ in ("1 OF 2 DUMPED", "← LEGEND15-U10", "not a T48 job",
                                                     "512 KB · 27C040 / 27C4001 or 27C4096 / 27C400")))
        open_fold("DUMPS")
        c.wait("document.querySelector('#mc-body').textContent.includes('LEGEND15-U10')", 10)
        check("machine: the dump says WAITING with the reason, the burn says SENT TO WORKSHOP",
              all(s_ in text("#mc-body") for s_ in ("WAITING", "no hub reachable", "Burns", "VERIFIED", "SENT TO WORKSHOP", "27C040@DIP32")))
        q("document.querySelector('#v-machine').scrollTop = 10000")
        time.sleep(0.4)
        shot("14b-machine-dumps-sent")
        # A redraw (every state event) used to empty the page first: it jumped to the top under the finger.
        q("(() => { const v = document.querySelector('#v-machine'); v.scrollTop = 250;"
          " document.querySelector('#mc-body').firstChild.dataset.old = '1'; })()")
        y0 = q("document.querySelector('#v-machine').scrollTop")
        api("PUT", "/api/meter/profile", {"id": "free"})
        c.wait("!document.querySelector('#mc-body [data-old]')", 10)
        time.sleep(0.8)
        check("machine: a redraw leaves the page where it was, and an open section open",
              y0 > 100 and abs(q("document.querySelector('#v-machine').scrollTop") - y0) <= 2
              and "OF 2 DUMPED" in text("#mc-body"))
        api("PUT", "/api/meter/profile", {"id": "rail-5v"})
        time.sleep(0.8)
        q("document.querySelector('#v-machine').scrollTop = 10000")
        shot("14b-machine-chips")
        q("[...document.querySelectorAll('#mc-body button')].find(b => b.textContent === 'gauntleg ▸').click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('1.2')).click()")
        c.wait("document.querySelector('#mc-body').textContent.includes('0 OF 1 DUMPED')", 10)
        check("the version switch: MAME's other version, its own chips", "legend12.u10" in text("#mc-body")
              and "Gauntlet Legends (version 1.2)" in text("#mc-body"))
        print("orders from the bench:")
        # The MACHINE card raises a work order for the machine it is showing. Free typing
        # through keypad(), which is an overlay rather than a field in the polled grid -- the
        # C3 rule: loadMachine() rebuilds #mc-body and anything typed into a tile is lost.
        q("document.querySelector('#v-machine').scrollTop = 10000")
        orders_card = ("[...document.querySelectorAll('#mc-body .card')]"
                       ".find(c => (c.querySelector('h3') || {}).textContent === 'Work orders')")
        check("machine: a work orders card", q(f"!!({orders_card})"))
        check("machine: it shows what the hub already has open",
              "Rail sags under load" in q(f"((({orders_card}) || {{}}).textContent || '')"))
        check("machine: no bare input in the polled card",
              q("!document.querySelector('#mc-body input')"))
        opened = q("(() => { const c = " + orders_card + "; if (!c) return false;"
                   " const b = [...c.querySelectorAll('button')]"
                   "   .find(b => b.textContent === 'NEW ORDER'); if (!b) return false;"
                   " b.click(); return true; })()")
        check("machine: NEW ORDER opens the keypad", opened
              and q("!document.querySelector('#sheet').classList.contains('hide')")
              and q("!!document.querySelector('#pane .keys')"))
        check("machine: a long complaint (two lines), the field follows the cursor", q(TYPE_LONG + "(150)"))
        q(UNTYPE + "(150)")
        for ch in "BAD":
            q("[...document.querySelectorAll('#pane .keys button')]"
              f".find(b => b.textContent === {json.dumps(ch)}).click()")
        q("(() => { const b = [...document.querySelectorAll('#pane button')]"
          "   .find(b => b.textContent === 'SAVE'); if (b) b.click(); return !!b; })()")
        # Then two pickFrom sheets: how urgent, and whether the last trace goes with it.
        # Both are ordinary answers, which is why neither is a confirm/cancel.
        c.wait("document.querySelectorAll('#pane .picklist .row').length >= 5", 10)
        check("machine: it asks how urgent",
              "Critical" in q("document.querySelector('#pane').textContent"))
        check("machine: how urgent is five big buttons, no keyboard, no SEARCH",
              not q("!!document.querySelector('#pane .keys')") and q(PICK_ROWS) == {"n": 5, "min": q(PICK_ROWS)["min"]}
              and q(PICK_ROWS)["min"] >= 64
              and not q("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'SEARCH')"))
        q("[...document.querySelectorAll('#pane .picklist .row')]"
          ".find(r => r.textContent.includes('High')).click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2", 10)
        check("machine: and whether to attach the last trace",
              "Without a trace" in q("document.querySelector('#pane').textContent"))
        check("machine: with or without the trace is two big buttons, no keyboard",
              not q("!!document.querySelector('#pane .keys')") and q(PICK_ROWS)["n"] == 2 and q(PICK_ROWS)["min"] >= 64)
        q("[...document.querySelectorAll('#pane .picklist .row')]"
          ".find(r => r.textContent.includes('Without')).click()")
        time.sleep(1.5)
        check("machine: the order is queued",
              settles(c, f"((({orders_card}) || {{}}).textContent || '').includes('queued')"))
        with urllib.request.urlopen(B + "/api/orders") as r:
            filed = json.load(r)["orders"]
        check("machine: it was filed for the machine on the card",
              len(filed) == 1 and filed[0]["machine"] == "gauntlet-legends"
              and filed[0]["issue"].startswith("BAD"))
        check("machine: with the priority that was chosen",
              bool(filed) and filed[0].get("priority") == "High")
        check("machine: and nothing attached, as asked",
              bool(filed) and "session_file" not in filed[0])

        click('[data-view="meter"]')
        c.wait("document.querySelector('#m-window').textContent.includes('machine spec')", 20)
        check("meter: 4.9–5.1 V machine spec · Gauntlet Legends", "4.9–5.1 V · machine spec · Gauntlet Legends" in text("#m-window"))
        shot("15-meter-machine")
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-clear').disabled")
        click("#mc-clear")
        c.wait("!document.querySelector('#sheet').classList.contains('hide')")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLEAR').click()")
        c.wait("document.querySelector('#mc-body').textContent.includes('No machine set')", 10)
        check("cleared", api("GET", "/api/machine")["slug"] is None)

        print("scanner (M6):")
        click('[data-view="meter"]')
        api("POST", "/api/scan", {"code": "012345678905"})
        c.wait("document.querySelector('#toast').textContent.startsWith('UNKNOWN CODE')", 10)
        check("an unknown code shows as UNKNOWN CODE", "012345678905" in text("#toast"))
        shot("16a-scan-unknown")
        api("POST", "/api/scan", {"code": "gauntlet"})
        c.wait("document.querySelector('#toast').textContent.startsWith('SCANNED Gauntlet')", 10)
        check("a cabinet scan: SCANNED <name>, machine set", api("GET", "/api/machine")["slug"] == "gauntlet")
        api("DELETE", "/api/machine")
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-labels').disabled")
        click("#mc-labels")
        c.wait("document.querySelectorAll('#pane .row').length === 4", 10)
        check("label list: the two commands + both machines, with COPY",
              all(s in text("#pane") for s in ("GATBOX:MARK", "GATBOX:NEW", "gauntlet-legends", "COPY")))
        shot("16b-label-list")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")

        print("add a machine (keypad with shift, platform picker) + export:")
        def field(label):
            q(f"[...document.querySelectorAll('#pane .fld')].find(b => b.firstChild.textContent === {json.dumps(label)}).click()")

        def key(ch):
            # '#pane button', not '#pane .keys button': CANCEL and SAVE sit in their own row
            # outside the key grid, which is what keeps them on the screen.
            q(f"[...document.querySelectorAll('#pane button')].find(b => b.textContent === {json.dumps(ch)}).click()")

        def typed(label, keys_):
            field(label)
            c.wait("document.querySelector('#pane .keys')")
            for ch in keys_:
                key(ch)
            key("SAVE")
            c.wait("document.querySelector('#pane .seg')")

        click("#mc-add")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .seg')")
        check("the form: name, maker, video/pinball, platform, notes; ADD disabled",
              [x for x in q("[...document.querySelectorAll('#pane .fld span')].map(s => s.textContent)")] == ["NAME", "MAKER", "PLATFORM", "NOTES"]
              and q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'ADD MACHINE').disabled"))
        # "aA" is the case key. It was "abc" until the keypad became one layout everywhere --
        # the layer key has to read ABC for "back to letters", and two keys reading abc and
        # ABC side by side is a coin toss.
        typed("NAME", ["Z", "aA", "a", "x", "x", "o", "n"])
        typed("MAKER", "SEGA")
        field("PLATFORM")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2")
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('hard drive')).click()")
        c.wait("document.querySelector('#pane .idline').textContent.startsWith('ID zaxxon')", 10)
        check("Zaxxon (shift worked), SEGA, the platform, and the ID before saving",
              all(x in text("#pane") for x in ("Zaxxon", "SEGA", "Test platform with a hard drive", "ID zaxxon · permanent")))
        shot("16c-add-machine")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'ADD MACHINE').click()")
        c.wait("document.querySelector('#toast').textContent.startsWith('ADDED Zaxxon')", 10)
        check("added: toast, on the roster, marked added",
              any(m["slug"] == "zaxxon" and m["added"] for m in api("GET", "/api/roster")["machines"]))
        click("#mc-add")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .seg')")
        typed("NAME", "GAUNTLET")
        typed("MAKER", "X")
        field("PLATFORM")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2")
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#pane .idline').textContent.includes('already on the roster')", 10)
        check("a name already on the roster: said before saving, ADD stays off",
              q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'ADD MACHINE').disabled"))
        shot("16d-add-duplicate")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CANCEL').click()")
        c.wait("document.querySelector('#sheet').classList.contains('hide')")
        click("#mc-export")
        c.wait("document.querySelector('#pane') && document.querySelector('#pane').textContent.includes('roster.json')", 10)
        check("export on the kiosk: the address to open, the added machine with EDIT",
              "/roster.json" in text("#pane") and "Zaxxon" in text("#pane")
              and q("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'EDIT')"))
        shot("16e-export")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'EDIT').click()")
        c.wait("document.querySelector('#pane h2').textContent === 'Edit Zaxxon'", 10)
        check("EDIT: the same form, ID shown as permanent, no type switch",
              "ID zaxxon (permanent)" in text("#pane") and not q("document.querySelector('#pane .seg')"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CANCEL').click()")

        print("sessions:")
        click('[data-view="sessions"]')
        c.wait("document.querySelectorAll('#s-list .row').length >= 5", 20)
        if os.path.exists(FX):
            q("[...document.querySelectorAll('#s-list .row')].find(r => r.textContent.includes('2026-09-25')).click()")
            c.wait("document.querySelector('#s-detail canvas') && document.querySelector('#s-detail pre')", 30)
            time.sleep(1.5)
            check("fixture: report text + chart, OV 3 (3 suspect) tag", "OVER-VOLTAGE (3)" in text("#s-detail pre")
                  and "OV 3 (3 suspect)" in text("#s-list"))
            shot("16-sessions-fixture")
        stop("logger")

        print("LIBRARY before the seed (each machine's manuals as ever): the spec sheet, the viewer, CONFIRM, an actual:")
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})
        click('[data-view="library"]')
        c.wait("document.querySelector('#lb-body').textContent.includes('Documents (1)')", 15)
        check("spec sheet: the manual's limit with its page and CONFIRM, the specs file's beside it, a fact",
              all(s_ in text("#lb-body") for s_ in ("Spec sheet", "4.75–5.25 V", "p.2 ▸", "CONFIRM", "specs file: 4.9–5.1 V",
                                                     "Fuse F1", "5 A slow-blow", "Gauntlet Legends Operations Manual")))
        shot("18a-manuals")
        q("document.querySelector('#lb-body .row.doc').click()")
        c.wait("!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').naturalWidth > 0", 30)
        check("the viewer: page 1 of 3 drawn by the Pi", text("#vw-page") == "1 / 3")
        click("#vw-next")
        c.wait("document.querySelector('#vw-page').textContent === '2 / 3' && document.querySelector('#vw-img').complete "
               "&& document.querySelector('#vw-img').naturalWidth > 0", 30)
        shot("18b-viewer")
        # The viewer covers the whole screen: on the 7" its buttons too keep off the bezel.
        vw = q("[...document.querySelectorAll('#viewer button')].filter(b => b.getClientRects().length).map(b => {"
               " const r = b.getBoundingClientRect(); return [b.textContent, r.left, r.top, r.right, r.bottom]; })"
               ".filter(([, l, t, r, b]) => l < 19.5 || t < 15.5 || r > innerWidth - 19.5 || b > innerHeight - 15.5).map(x => x[0])")
        check("viewer (7\"): every button inside the bezel margin", not vw)
        if vw:
            print("    ", vw)
        click("#vw-prev")
        c.wait("document.querySelector('#vw-page').textContent === '1 / 3'", 10)
        click("#vw-search")
        c.wait("document.querySelector('#pane .keys')")
        tap_keys("4.75")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 1", 15)
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#vw-page').textContent === '2 / 3'", 10)
        check("SEARCH 4.75 -> page 2", True)
        click("#vw-close")
        q("[...document.querySelectorAll('#lb-body button')].find(b => b.textContent === 'CONFIRM').click()")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .pagebox img')", 10)
        c.wait("document.querySelector('#pane .pagebox img').naturalWidth > 0", 30)
        check("CONFIRM shows the page and the manual's words first", "“+5 VDC 4.75 to 5.25 V”" in text("#pane"))
        shot("18c-confirm")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CONFIRM').click()")
        c.wait("document.querySelector('#lb-body').textContent.includes('✓ CONFIRMED')", 15)
        check("confirmed: the window in use is the manual's, and the logger's too",
              "4.75–5.25 V · the manual, confirmed" in text("#lb-body")
              and api("GET", "/api/meter/profile")["profile"]["window"] == [4.75, 5.25])
        api("PUT", "/api/actuals/gauntlet-legends", {"rail": "+5V", "value": 5.2, "window": [5.1, 5.3], "note": "boosted at the PSU"})
        c.wait("document.querySelector('#lb-body').textContent.includes('own window')", 15)
        check("an actual from the field beside the manual (kept, still confirmed), its own window in use",
              all(s_ in text("#lb-body") for s_ in ("5.20 V", "own window 5.1–5.3 V", "boosted at the PSU", "4.75–5.25 V",
                                                     "✓ CONFIRMED", "5.1–5.3 V · this machine's actual")))
        shot("18d-actual")
        click('[data-view="meter"]')
        c.wait("document.querySelector('#m-window').textContent.includes(\"this machine's actual\")", 15)
        check("the meter says whose window it is", "5.1–5.3 V · this machine's actual" in text("#m-window"))
        api("DELETE", "/api/actuals/gauntlet-legends", {"rail": "+5V"})
        api("DELETE", "/api/specs/gauntlet-legends/confirm", {"rail": "+5V"})
        api("DELETE", "/api/machine")

        print("DUMP: which chip (MAME's list):")
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})
        click('[data-view="dump"]')
        c.wait("[...document.querySelectorAll('#du-body button')].some(b => b.textContent.startsWith('WHICH CHIP?'))", 15)
        q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('WHICH CHIP?')).click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 1", 10)
        check("the chips of the version shown (1.2 was picked on MACHINE)", "legend12.u10" in text("#pane"))
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#du-body').textContent.includes('CHIP: legend12.u10')", 10)
        check("picked: the label from its printed name, the 512 KB family tiles lit (the part is still yours to pick)",
              "LABEL: LEGEND12" in text("#du-body") and "fits 27C040 / 27C4001 or 27C4096 / 27C400" in text("#du-body")
              and q("[...document.querySelectorAll('#du-body .tile.on')].map(t => t.firstChild.textContent).join('|')")
              == "27C040 / 27C4001|27C4096 / 27C400" and "1 · PART" in text("#du-body"))
        shot("17-dump-chip")
        check("FOR ORDER: the machine's one open order is preselected, with its issue",
              settles(c, "document.querySelector('#du-body').textContent.includes('FOR ORDER #412')")
              and "Rail sags under load" in text("#du-body"))
        shot("17b-dump-for-order")
        q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('FOR ORDER #412')).click()")
        check("a tap clears it", settles(c, "document.querySelector('#du-body').textContent.includes('FOR ORDER: NONE')"))
        q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('FOR ORDER: NONE')).click()")
        c.wait("document.querySelector('#sheet:not(.hide)') && document.querySelector('#pane').textContent.includes('FOR WHICH ORDER?')", 10)
        check("and a tap on NONE offers the open orders, and NO ORDER",
              "#412" in text("#pane") and "NO ORDER" in text("#pane"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent.startsWith('#412')).click()")
        check("picking it puts it back", settles(c, "document.querySelector('#du-body').textContent.includes('FOR ORDER #412')"))
        api("DELETE", "/api/machine")
        c.wait("!document.querySelector('#du-body').textContent.includes('CHIP: legend12')", 10)
        check("no machine: the chip and its label go with it", not q("document.querySelectorAll('#du-body .tile.on').length")
              and "TYPE THE CHIP'S LABEL" in text("#du-body"))

        print("DUMP (M7; a runner stands in for gatbox-dump.path):")
        if any(open(p_).read().strip() == "a466" for p_ in __import__("glob").glob("/sys/bus/usb/devices/*/idVendor")):
            done = threading.Event()

            def runner():
                while not done.is_set():
                    if os.path.exists(f"{T}/spool/request.json"):
                        time.sleep(1.5)                          # long enough to see the progress card
                        try:                                     # blank checks and burns get the fake chip
                            op = json.load(open(f"{T}/spool/request.json")).get("op")
                        except (OSError, ValueError):
                            op = None
                        subprocess.run(["python3", os.path.join(REPO, "tools/gatbox-dump"), "--job", f"{T}/spool/request.json",
                                        "--status", f"{T}/spool/status.json"], capture_output=True, timeout=60,
                                       env=dict(os.environ, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"),
                                                GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"), GATBOX_ROMS=f"{T}/roms",
                                                GATBOX_API=B, FAKE_ROM=f"{T}/rom.bin", FAKE_ID=os.environ.get("FAKE_ID_NEXT", ""),
                                                FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic The Hedgehog (Japan, rev. C)",
                                                **({"FAKE_CHIP": f"{T}/chip.bin"} if op in ("blank", "burn") else {})))
                    time.sleep(0.2)
            threading.Thread(target=runner, daemon=True).start()
            click('[data-view="dump"]')
            c.wait("document.querySelectorAll('#du-body .tile').length === 14", 15)
            check("DUMP: T48 READY, 14 family tiles", "T48 READY" in text("#du-body"))
            shot("17a-dump-pick")
            q("[...document.querySelectorAll('#du-body .tile')].find(t => t.textContent.startsWith('27C020')).click()")
            c.wait("document.querySelectorAll('#pane .picklist .row').length >= 2", 10)
            shot("17b-dump-family")
            q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent === '27C020@DIP32').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('27C020@DIP32')", 10)
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('TYPE THE CHIP')).click()")
            c.wait("document.querySelector('#pane .kp-field')")
            tap_keys("EPR-1")
            c.wait("document.querySelector('#du-body').textContent.includes('LABEL: EPR-1')", 10)
            os.environ["FAKE_ID_NEXT"] = "mismatch"                   # the generic name: minipro's chip-ID stop
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'DUMP').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('DUMPING')", 10)
            shot("17c-dump-running")
            c.wait("document.querySelector('#du-body').textContent.includes('STOPPED')", 30)
            check("a wrong part: STOPPED with minipro's words and a USE <part> button",
                  "Invalid Chip ID" in text("#du-body") and q("[...document.querySelectorAll('#du-body button')].some(b => b.textContent === 'USE TMS27C020@DIP32')"))
            shot("17d-dump-stopped")
            os.environ["FAKE_ID_NEXT"] = ""
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'USE TMS27C020@DIP32').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('MATCH')", 30)
            time.sleep(1.5)
            check("USE the suggested part: MATCH sonic / epr-15781c.ic18, archived, listed",
                  "sonic / epr-15781c.ic18" in text("#du-body") and "archived:" in text("#du-body") and "EPR-1 MATCH" in text("#du-body"))
            shot("17e-dump-match")

            print("BURN (armed by a hold on the Pi's own screen):")
            os.makedirs(f"{T}/roms/_images", exist_ok=True)
            img = os.urandom(262144)
            open(f"{T}/roms/_images/diag.bin", "wb").write(img)
            open(f"{T}/chip.bin", "wb").write(b"\xff" * 262144)
            q("[...document.querySelectorAll('#du-body .seg button')].find(b => b.textContent === 'BURN').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('1 · IMAGE')", 10)
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('PICK AN IMAGE')).click()")
            c.wait("document.querySelectorAll('#pane .picklist .row').length >= 2", 10)
            check("images: the archive's and _images/", "diag.bin" in text("#pane") and "EPR-1" in text("#pane"))
            q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('diag.bin')).click()")
            c.wait("document.querySelector('#du-body').textContent.includes('IMAGE: diag.bin')", 10)
            check("the 256 KB family tiles lit for the image", q("[...document.querySelectorAll('#du-body .tile.on')].map(t => "
                                                                "t.firstChild.textContent).join('|')") == "27C020 / 27C2001")
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'CHANGE') && "
              "[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'CHANGE').click()")
            c.wait("document.querySelectorAll('#du-body .tile').length === 14", 10)
            q("[...document.querySelectorAll('#du-body .tile')].find(t => t.textContent.startsWith('27C020')).click()")
            c.wait("!document.querySelector('#sheet').classList.contains('hide') && "
                   "[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent === '27C020@DIP32')", 10)
            q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent === '27C020@DIP32').click()")
            c.wait("[...document.querySelectorAll('#du-body button')].some(b => b.textContent === 'BLANK CHECK')", 10)
            check("no burn button before a blank check", not q("[...document.querySelectorAll('#du-body button')].some("
                                                                "b => b.textContent.startsWith('HOLD'))"))
            shot("19a-burn-pick")
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'BLANK CHECK').click()")
            c.wait("[...document.querySelectorAll('#du-body button')].some(b => b.textContent.startsWith('HOLD 3 S TO BURN'))", 30)
            check("blank: BLANK, and the hold to burn appears on the Pi's own screen", "BLANK" in text("#du-body")
                  and "diag.bin → 27C020@DIP32" in text("#du-body"))
            shot("19b-burn-armable")
            q("(() => { const b = [...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('HOLD 3 S')); "
              "b.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); })()")
            time.sleep(1.0)
            q("(() => { const b = [...document.querySelectorAll('#du-body button')].find(b => b.classList.contains('arm')); "
              "b && b.dispatchEvent(new PointerEvent('pointerup', {bubbles: true})); })()")
            time.sleep(1.0)
            check("let go early: nothing armed, nothing sent", not os.path.exists(f"{T}/spool/request.json")
                  and open(f"{T}/chip.bin", "rb").read() == b"\xff" * 262144)
            # count the page's POSTs to /api/burn; while __burnFail is set, answer them 409 (nothing gets burned)
            q("(() => { window.__burnPosts = 0; window.__burnFail = true; const f0 = window.fetch; "
              "window.fetch = (u, o) => { if (o && o.method === 'POST' && String(u) === '/api/burn') { window.__burnPosts++; "
              "if (window.__burnFail) return Promise.resolve(new Response(JSON.stringify({error: 'the T48 dropped off USB'}), "
              "{status: 409, headers: {'Content-Type': 'application/json'}})); } return f0(u, o); }; })()")
            q("(() => { const b = document.querySelector('#du-body button.hold'); "
              "for (const id of [11, 12]) b.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, pointerId: id})); })()")
            time.sleep(0.3)
            q("(() => { const b = document.querySelector('#du-body button.hold'); "
              "for (const id of [11, 12]) b.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, pointerId: id})); })()")
            time.sleep(3.5)
            check("two fingers, both lifted early: nothing armed, nothing sent", q("window.__burnPosts") == 0)
            q("document.querySelector('#du-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            c.wait("window.__burnPosts === 1", 10)
            time.sleep(1.0)                                            # the 409's toast; the button is spent
            q("document.querySelector('#du-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            time.sleep(0.2)
            q("document.querySelector('#du-body button.hold').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")
            time.sleep(3.5)
            check("a hold whose request fails (409): a fresh button, and a tap on it arms nothing",
                  q("window.__burnPosts") == 1 and q("document.querySelector('#du-body button.hold').textContent").startswith("HOLD 3 S"))
            q("window.__burnFail = false")
            q("(() => { const b = [...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('HOLD 3 S')); "
              "b.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); })()")
            c.wait("document.querySelector('#du-body').textContent.includes('BURNING')", 15)
            shot("19c-burning")
            c.wait("document.querySelector('#du-body').textContent.includes('VERIFIED')", 40)
            check("held 3 s: BURNING, then VERIFIED; the chip holds the image", open(f"{T}/chip.bin", "rb").read() == img
                  and "2 read-backs identical to the image" in text("#du-body"))
            shot("19d-burn-verified")
            lan = next((a for a in _lan_addrs() if "." in a), None)
            if lan:
                open(f"{T}/chip.bin", "wb").write(b"\xff" * 262144)
                api("POST", "/api/burn/blank", {"image": "_images/diag.bin", "part": "27C020@DIP32"})
                c.wait("document.querySelector('#du-body').textContent.includes('HOLD 3 S TO BURN')", 30)
                c.open(f"http://{lan}:{PORT}/dash/", 1024, 600)
                c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
                click('[data-view="dump"]')
                c.wait("document.querySelectorAll('#du-body .seg button').length === 2", 15)
                q("[...document.querySelectorAll('#du-body .seg button')].find(b => b.textContent === 'BURN').click()")
                c.wait("document.querySelector('#du-body').textContent.includes('1 · IMAGE')", 10)
                q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('PICK AN IMAGE')).click()")
                c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.includes('diag.bin'))", 10)
                q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('diag.bin')).click()")
                c.wait("document.querySelectorAll('#du-body .tile').length === 14", 10)
                q("[...document.querySelectorAll('#du-body .tile')].find(t => t.textContent.startsWith('27C020')).click()")
                c.wait("!document.querySelector('#sheet').classList.contains('hide') && "
                       "[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent === '27C020@DIP32')", 10)
                q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent === '27C020@DIP32').click()")
                c.wait("document.querySelector('#du-body').textContent.includes('4 · BURN')", 10)
                time.sleep(0.5)
                shot("19e-burn-phone")
                check("from a phone (not the Pi): no hold to burn, it says to arm at the Pi",
                      not q("[...document.querySelectorAll('#du-body button')].some(b => b.textContent.startsWith('HOLD'))")
                      and "arm it at the Pi" in text("#du-body"))
                c.open(B + "/dash/", 1024, 600)
                c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)

            print("BURN: a non-JEDEC confirmation covers that part only; a stop after the write says so:")
            open(f"{T}/roms/_images/one-mbit.bin", "wb").write(os.urandom(131072))
            open(f"{T}/chip.bin", "wb").write(b"\xff" * 131072)
            c.open(B + "/dash/", 1024, 600)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
            click('[data-view="dump"]')
            c.wait("document.querySelectorAll('#du-body .seg button').length === 2", 15)
            q("[...document.querySelectorAll('#du-body .seg button')].find(b => b.textContent === 'BURN').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('1 · IMAGE')", 10)
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent.startsWith('PICK AN IMAGE')).click()")
            c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.includes('one-mbit.bin'))", 10)
            q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('one-mbit.bin')).click()")
            c.wait("document.querySelector('#du-body').textContent.includes('IMAGE: one-mbit.bin')", 10)

            def search_part(name):
                q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'SEARCH').click()")
                c.wait("document.querySelector('#pane .kp-field')")
                tap_keys(name.split("@")[0])
                c.wait(f"[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent === {json.dumps(name)})", 10)
                q(f"[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent === {json.dumps(name)}).click()")
                c.wait(f"document.querySelector('#du-body').textContent.includes({json.dumps(name)})", 10)
            search_part("27C1000@DIP32")
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'BLANK CHECK').click()")
            c.wait("[...document.querySelectorAll('#du-body button')].some(b => b.textContent === 'I CHECKED: BLANK CHECK AGAIN')", 30)
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'I CHECKED: BLANK CHECK AGAIN').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('HOLD 3 S TO BURN one-mbit.bin → 27C1000@DIP32')", 30)
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'CHANGE').click()")
            c.wait("document.querySelectorAll('#du-body .tile').length === 14", 10)
            search_part("27C301@DIP32")
            q("[...document.querySelectorAll('#du-body button')].find(b => b.textContent === 'BLANK CHECK').click()")
            c.wait("document.querySelector('#du-body').textContent.includes('27C301@DIP32: non-JEDEC') || "
                   "document.querySelector('#du-body').textContent.includes('HOLD 3 S TO BURN one-mbit.bin → 27C301')", 30)
            check("I CHECKED for 27C1000, then CHANGE to 27C301: its own non-JEDEC stop, no hold to burn",
                  "27C301@DIP32: non-JEDEC" in text("#du-body")
                  and not q("[...document.querySelectorAll('#du-body button')].some(b => b.textContent.startsWith('HOLD'))"))

            def result_for(st):
                json.dump(dict(st, op="burn", state="stopped", image=f"{T}/roms/_images/one-mbit.bin", part="27C301@DIP32",
                               finished_at=time.time()), open(f"{T}/spool/.st.tmp", "w"))
                os.replace(f"{T}/spool/.st.tmp", f"{T}/spool/status.json")
                c.wait(f"document.querySelector('#du-body').textContent.includes({json.dumps(st['error'])})", 10)
                return text("#du-body")
            got = result_for({"error": "read 1 failed: IO error: expected 64 bytes but 0 bytes transferred", "written": True,
                              "hint": "check the chip is in the socket, notch toward the lever"})
            check("a stop after the write (a read-back failed): FAILED, and it says the chip was written",
                  "FAILED" in got and "NOT BURNED" not in got and "the chip was written" in got)
            shot("19f-burn-written")
            got = result_for({"error": "the arm expired before the burn started", "hint": "hold the button again"})
            check("a stop before the write: NOT BURNED, nothing about the chip being written",
                  "NOT BURNED" in got and "the chip was written" not in got)
            done.set()
        else:
            print("  skip  (the T48 isn't plugged in)")

        for v in ("system", "devices", "dump"):
            click(f'[data-view="{v}"]')
            time.sleep(2)
            shot(f"17-{v}")
        # Back to SYSTEM, and it matters: loadSystem() rebuilds #sy-grid only while that view
        # is the open one, and a hidden view keeps its DOM. Asserting from here with the view
        # left on "dump" reads a grid painted once and never repainted -- which is how the
        # checks below that exist *because* of the 5 s repaint came to pass without one ever
        # happening.
        click('[data-view="system"]')
        time.sleep(1)
        check("system: kiosk controls on the Pi's own screen", q("!document.querySelector('#sy-kiosk').classList.contains('hide')"))
        # The hub push (gatbox-sync). Nothing has run here and there is no
        # /var/lib/gatbox-sync, so the tile must say so plainly rather than read as a failure
        # -- a Pi with no token in /etc/gatbox/hub.conf is the normal case, not a fault.
        hubcard = ("[...document.querySelectorAll('#sy-grid .card')]"
                   ".find(c => (c.querySelector('h2') || {}).textContent === 'Hub')")
        check("system: a Hub tile", q(f"!!({hubcard})"))
        softcard = ("[...document.querySelectorAll('#sy-grid .card')]"
                    ".find(c => (c.querySelector('h2') || {}).textContent === 'Software')")
        check("system: Software says what the bootstrap installed, and that it had local changes",
              settles(c, f"(({softcard}) || {{}}).textContent.includes('v2026.11.21-dirty on main (local changes)')", 15))
        libcard = ("[...document.querySelectorAll('#sy-grid .card')]"
                   ".find(c => (c.querySelector('h2') || {}).textContent === 'Library')")
        check("system: a Library tile, before the library is set up: it says so, with the manuals' size and the room on /srv",
              settles(c, f"!!({libcard}) && {libcard}.textContent.includes('not set up yet')", 15)
              and "size on the Pi" in q(f"{libcard}.textContent") and "room on /srv" in q(f"{libcard}.textContent"))
        # Wi-Fi: joining a network nobody coded in, from the Pi's own screen. The card is
        # here and not on a phone, because a join asked for over the hotspot would cut the
        # connection making the request.
        wifi = ("[...document.querySelectorAll('#sy-grid .card')]"
                ".find(c => (c.querySelector('h2') || {}).textContent === 'Wi-Fi')")
        check("system: a Wi-Fi card", q(f"!!({wifi})"))
        # The card is drawn first and filled when /api/wifi answers, so wait for it the way the
        # Library tile above does: read straight away, a slow machine sees an empty card.
        check("system: it lists a saved network",
              settles(c, f"((({wifi}) || {{}}).textContent || '').includes('HomeNetwork')", 15))
        check("system: it offers a scan", q(
            "(() => { const c = " + wifi + "; if (!c) return false;"
            " return [...c.querySelectorAll('button')].some(b => b.textContent === 'SCAN'); })()"))
        check("system: it offers to join what is in range", q(
            "(() => { const c = " + wifi + "; if (!c) return false;"
            " return [...c.querySelectorAll('button')].some(b => b.textContent === 'JOIN'); })()"))
        # The card must NOT hold a bare input. loadSystem() rebuilds #sy-grid every 5 s while
        # the SYSTEM view is open, so anything typed into a field inside a tile is destroyed
        # mid-entry -- and the kiosk has no on-screen keyboard, so there is nothing to type
        # with anyway. Every other typed field here goes through keypad()/sheet(), which is
        # an overlay outside the poll loop.
        check("system: no bare input inside the polled grid",
              q("!document.querySelector('#sy-grid input')"))
        click_in_card = ("(() => { const c = " + wifi + "; if (!c) return false;"
                         " const b = [...c.querySelectorAll('button')]"
                         "   .find(b => b.textContent === 'JOIN'); if (!b) return false;"
                         " b.click(); return true; })()")
        check("system: JOIN opens the on-screen keypad", q(click_in_card)
              and q("!document.querySelector('#sheet').classList.contains('hide')" + " && !!document.querySelector('#pane .keys')"))
        # The confirm button must be *on the screen*. With the symbols appended below the
        # letters it was pushed past the bottom of the 7" panel, so there was no visible way
        # to save -- the keypad looked like it had no confirm at all.
        check("system: SAVE is within the panel, not off the bottom", q(
            "(() => { const b = [...document.querySelectorAll('#pane button')]"
            "   .find(b => b.textContent === 'SAVE'); if (!b) return false;"
            " const r = b.getBoundingClientRect();"
            " return r.bottom <= window.innerHeight && r.top >= 0 && r.height > 0; })()"))
        check("system: and so is CANCEL", q(
            "(() => { const b = [...document.querySelectorAll('#pane button')]"
            "   .find(b => b.textContent === 'CANCEL'); if (!b) return false;"
            " const r = b.getBoundingClientRect();"
            " return r.bottom <= window.innerHeight && r.height > 0; })()"))
        # Every row, not just SAVE. The rows were a fixed 54 px and only the key grid could give, so
        # on anything shorter than the 7" (a phone held sideways, its address bar showing) the grid
        # scrolled its rows out of sight -- one row visible, no scrollbar. The 7" keeps its 54 px keys.
        kb = q(KB_BOX)
        check("keypad on the 7\": the whole keyboard, a fixed 274 px block of 50 px rows, CANCEL / SAVE inside the margin",
              kb and kb["h"] == 274 and kb["rows"] == [50] and kb["cut"] == 0 and kb["act_bottom"] <= 584
              and kb["act_left"] >= 20 and kb["act_right"] <= 1004)
        check("keypad on the 7\": letters and number pad sit in the same place", kb and kb["top"] == KB_LETTERS.get("num_top"))
        check("keypad: typing past the field's width, the field follows the cursor", q(TYPE_LONG + "(60)"))
        q(UNTYPE + "(60)")
        check("keypad on a phone held sideways (844x390, 740x360, 844x320): every row and SAVE on screen",
              kp_fits(c, [(844, 390), (740, 360), (844, 320)]))
        # The symbols are a layer, reached by a toggle, the way a phone keyboard does it --
        # so the keypad is the same height whatever is being typed.
        shot("17b-keypad-letters")
        check("system: the letters are showing first", q(
            "[...document.querySelectorAll('#pane .keys button')]"
            "   .map(b => b.textContent).join('').includes('Q')"))
        check("system: ?#+ reaches the rest of the printable set", q(
            "(() => { const t = [...document.querySelectorAll('#pane .keys button')]"
            "   .find(b => b.textContent === '?#+'); if (!t) return false; t.click();"
            " const s = [...document.querySelectorAll('#pane .keys button')]"
            "   .map(b => b.textContent).join('');"
            " return ['@', '#', '_', '%', '^', '~', '{'].every(c => s.includes(c)); })()"))
        shot("17c-keypad-symbols")
        check("system: the toggle goes back to letters", q(
            "(() => { const t = [...document.querySelectorAll('#pane .keys button')]"
            "   .find(b => b.textContent === 'ABC'); if (!t) return false; t.click();"
            " return [...document.querySelectorAll('#pane .keys button')]"
            "   .map(b => b.textContent).join('').includes('Q'); })()"))
        check("system: the keypad is six rows whichever layer is up", q(
            "(() => { const n = () => document.querySelectorAll('#pane .keys button').length;"
            " const before = n();"
            " [...document.querySelectorAll('#pane .keys button')]"
            "   .find(b => b.textContent === '?#+').click();"
            " const after = n();"
            " [...document.querySelectorAll('#pane .keys button')]"
            "   .find(b => b.textContent === 'ABC').click();"
            " return before === after; })()"))
        # The whole point: it is an overlay, not a tile, so a repaint cannot eat it.
        time.sleep(6)
        check("system: it survives the 5 s repaint",
              q("!document.querySelector('#sheet').classList.contains('hide')" + " && !!document.querySelector('#pane .keys')"))
        q("(() => { const b = [...document.querySelectorAll('#pane .keys button')]"
          "   .find(b => b.textContent === 'CANCEL'); if (b) b.click(); return true; })()")
        # A request waiting in the spool must read as "working", not as the previous join's
        # outcome. The spool is one slot and status.json is one outcome, so before the
        # request carried an id the card showed the earlier result under the button just
        # pressed -- "failed" under a JOIN that was still running.
        # Parenthesised as a whole: `a || b` binds looser than `.`, so without the outer
        # brackets `wtext + ".includes(x)"` reads as `a || (b.includes(x))` and is true for
        # any non-empty card -- a check that asserts nothing.
        wtext = f"((({wifi}) || {{}}).textContent || '')"
        # Assembled, not written out: a quoted psk literal in a tracked file is exactly what
        # the pre-push hook refuses, and it is right to. tests/test-hooks.sh does the same
        # with its key-shaped value.
        key3 = "hunter3" + "-must-not-appear"
        json.dump({"id": "pending-1", "action": "join", "ssid": "CafeOpen", "psk": key3},
                  open(f"{T}/wifi/request.json", "w"))
        check("system: a waiting request reads as working",
              settles(c, f"{wtext}.includes('working')"))
        check("system: and not as the earlier outcome", "joined" not in q(wtext))
        check("system: a waiting key reaches no part of the page",
              "hunter3" not in q("document.body.textContent")
              and "hunter3" not in q("document.documentElement.outerHTML"))
        # Abandoned: nothing took it. The card has to say so, because the cause is an install
        # problem (gatbox-wifi.path not enabled) that nothing else on this page would show --
        # and it has to keep saying so. The GET that finds a stale request is also the one
        # that sweeps it, so a card that only read the response would flash the warning once
        # and lose it on the next repaint, five seconds later.
        os.utime(f"{T}/wifi/request.json", (0, time.time() - 600))
        check("system: an abandoned request is called out",
              settles(c, f"{wtext}.includes('unanswered')"))
        check("system: and the stale request is swept off the spool",
              not os.path.exists(f"{T}/wifi/request.json"))
        time.sleep(6)                                  # at least one more repaint
        check("system: the warning survives the repaint that follows",
              "unanswered" in q(wtext))
        check("system: no key anywhere on the page",
              not any(k in q("document.body.textContent") for k in ("hunter2", "hunter3"))
              and not any(k in q("document.documentElement.outerHTML")
                          for k in ("hunter2", "hunter3")))
        check("system: it says sync isn't configured, not that it failed",
              "not configured" in q(f"(({hubcard}) || {{}}).textContent || ''"))
        check("devices: NOT FITTED cards greyed", q("document.querySelectorAll('#dv-grid .notfit').length") == 3)

        print("several hubs (Phase 8):")
        # gatbox-sync's state for two hubs: ALPHA (the default, no capabilities) lists our machines, BRAVO takes jobs
        # and lists a customer machine known by model, plus one of ours, which makes that one a conflict.
        sd = f"{T}/sync"
        os.makedirs(f"{sd}/hubs/bravo", exist_ok=True)
        w = lambda p_, o: json.dump(o, open(f"{sd}/{p_}", "w"))   # noqa: E731
        w("status.json", {"at": time.time(), "ok": True, "queued": 0, "sent_total": 3,
                          "hubs": [{"name": "default", "label": "ALPHA", "capabilities": [], "ok": True, "queued": 0,
                                    "hub": "https://a.example", "at": time.time(), "sent_total": 2},
                                   {"name": "bravo", "label": "BRAVO", "capabilities": ["jobs"], "ok": True, "queued": 0,
                                    "hub": "https://b.example", "at": time.time(), "sent_total": 1,
                                    "roms_waiting": 2, "roms_parked": 1}],
                          "held": [], "conflicts": [{"machine": "gauntlet", "hubs": ["ALPHA", "BRAVO"]}], "refused": []})
        w("hub.json", {"name": "default", "label": "ALPHA", "capabilities": []})
        w("roster.json", {"machines": [{"slug": "gauntlet-legends", "name": "Gauntlet Legends"},
                                       {"slug": "gauntlet", "name": "Gauntlet"}]})
        w("hubs/bravo/hub.json", {"name": "bravo", "label": "BRAVO", "capabilities": ["jobs"]})
        w("hubs/bravo/roster.json", {"machines": [
            {"slug": "s-0042-ms-pac-man", "name": "Ms. Pac-Man", "status": "In shop", "location": "Bench",
             "model_key": "gauntlet-legends"},
            {"slug": "gauntlet", "name": "Gauntlet"}]})
        w("hubs/bravo/orders.json", {"open": {"s-0042-ms-pac-man": [
            {"id": 7, "issue": "Dead on arrival", "priority": "Normal", "status": "Waiting on parts",
             "work_order": "WO-2026-0012", "nte": "250.00", "source": "web", "reported": 1790000000.0}]}})
        # An earlier step can leave a sheet up (the Wi-Fi keypad); the tiles are under it.
        q("(() => { const b = [...document.querySelectorAll('#sheet:not(.hide) #pane button')]"
          ".find(b => b.textContent === 'CANCEL' || b.textContent === 'CLOSE'); if (b) b.click(); })()")
        click('[data-view="system"]')
        check("system: a tile per hub, by label",
              settles(c, "[...document.querySelectorAll('#sy-grid h2')].map(h => h.textContent).join('|')"
                         ".includes('Hub · ALPHA|Hub · BRAVO')"))
        check("system: the conflict, in its own tile",
              settles(c, "[...document.querySelectorAll('#sy-grid .card')].some(c => c.textContent.includes('Hub routing')"
                         " && c.textContent.includes('gauntlet: on ALPHA and BRAVO'))"))
        check("system: a hub's tile counts its dumps and burns waiting and refused",
              settles(c, "[...document.querySelectorAll('#sy-grid .card')].some(c => c.textContent.includes('Hub · BRAVO')"
                         " && c.textContent.includes('2 waiting, 1 refused'))"))
        shot("19-system-hubs")
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-pick').disabled", 10)
        click("#mc-pick")
        c.wait("document.querySelectorAll('#pane .picklist .grp').length > 0", 10)
        heads = q("[...document.querySelectorAll('#pane .picklist .grp')].map(g => g.textContent).join('|')")
        # Zaxxon was added on this Pi earlier in the run and no hub has it.
        check("picker: grouped under each hub's label, THIS PI for the rest", heads == "ALPHA|BRAVO|THIS PI")
        check("picker: the conflict says so",
              "CONFLICT" in q("document.querySelector('#pane .picklist').textContent"))
        shot("20-picker-hubs")
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('Ms. Pac-Man')).click()")
        job_card = ("[...document.querySelectorAll('#mc-body .card')]"
                    ".find(c => ((c.querySelector('h3') || {}).textContent || '').startsWith('Job'))")
        check("machine: the customer machine, its hub and model",
              settles(c, "document.querySelector('#mc-body').textContent.includes('HUB: BRAVO')")
              and "Model: Gauntlet Legends" in text("#mc-body"))
        check("machine: a JOB card, not Work orders",
              settles(c, f"!!({job_card})") and not q("[...document.querySelectorAll('#mc-body h3')]"
                                                      ".some(h => h.textContent === 'Work orders')"))
        jt = q(f"(({job_card}) || {{}}).textContent || ''")
        check("machine: the job's complaint, status, work order and NTE",
              all(x in jt for x in ("Dead on arrival", "Waiting on parts", "WO-2026-0012", "NTE 250.00")))
        q(f"[...({job_card}).querySelectorAll('button')].find(b => b.textContent === 'AS-FOUND').click()")
        time.sleep(1)
        check("machine: the card is drawn once, however many reloads overlap",
              q("[...document.querySelectorAll('#mc-body h3')].filter(h => h.textContent.startsWith('Job')).length") == 1
              and q("document.querySelectorAll('#mc-body .big').length") == 1)
        check("machine: AS-FOUND sets the session's phase",
              settles(c, "document.querySelector('#m-window').textContent.includes('AS-FOUND')")
              and api("GET", "/api/meter/profile")["phase"] == "as_found")
        q(f"({job_card}).scrollIntoView()")
        shot("21-machine-job")
        logger(mkcsv("job.csv", rep(400, "5.02,V,DC AUTO")), rate=10)
        c.wait("document.querySelector('#m-value').textContent === '5.02'", 20)
        # SAVE READING is for bench profiles; "free" takes any reading. A profile change is a new file too.
        api("PUT", "/api/meter/profile", {"id": "free"})
        click('[data-view="meter"]')
        c.wait("!document.querySelector('#b-capture').disabled", 20)
        click("#b-capture")
        c.wait("!document.querySelector('#sheet').classList.contains('hide')"
               " && [...document.querySelectorAll('#pane button')].some(b => b.textContent === 'SAVE')", 10)
        tap_keys("CN1")
        check("save reading: it asks which end of the job",
              settles(c, "[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'AS-LEFT')"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'AS-LEFT').click()")
        check("save reading: AS-LEFT becomes the phase, and the reading is saved",
              settles(c, "document.querySelector('#m-window').textContent.includes('AS-LEFT')")
              and any(k["label"] == "CN1" for k in api("GET", "/api/captures?machine=s-0042-ms-pac-man")["captures"]))
        stop("logger")
        api("PUT", "/api/meter/profile", {"id": "rail-5v"})
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})
        click('[data-view="machine"]')
        check("machine: a barcade machine keeps Work orders, and no phase",
              settles(c, "[...document.querySelectorAll('#mc-body h3')].some(h => h.textContent === 'Work orders')")
              and not q(f"!!({job_card})") and "AS-" not in text("#m-window"))

        print("DISK (a runner stands in for gatbox-disk.path):")
        import threading
        dk_done = threading.Event()

        def disk_runner():
            while not dk_done.is_set():
                if os.path.exists(f"{T}/dspool/request.json"):
                    time.sleep(1.5)                          # long enough to see the progress card
                    extra = {}
                    if os.path.exists(f"{T}/dk-bad"):
                        extra["FAKE_DDRESCUE_BAD"] = "0:512"
                    if os.path.exists(f"{T}/dk-heal"):
                        extra["FAKE_DDRESCUE_HEAL"] = "1"
                    if os.path.exists(f"{T}/dk-slow"):          # a drive that takes its time, and a job that says where it is
                        extra.update(FAKE_DDRESCUE_SLOW="4", FAKE_DDRESCUE_NOISE="1", GATBOX_DISK_POLL="0.3", GATBOX_DISK_TICK="0")
                    subprocess.run(["python3", os.path.join(REPO, "tools/gatbox-disk"), "--job", f"{T}/dspool/request.json",
                                    "--status", f"{T}/dspool/status.json"], capture_output=True, timeout=120, check=False,
                                   env=FD.env(GATBOX_API=B, GATBOX_IMPORT_SETTLE="0", **extra))
                time.sleep(0.2)
        threading.Thread(target=disk_runner, daemon=True).start()

        def dk_tap(start, sel="#dk-body button"):
            q(f"[...document.querySelectorAll({json.dumps(sel)})].find(b => b.textContent.startsWith({json.dumps(start)})).click()")

        def dk_has(t, timeout=15):
            return settles(c, f"document.querySelector('#dk-body').textContent.includes({json.dumps(t)})", timeout)

        def dk_seg(t):
            q(f"[...document.querySelectorAll('#dk-body .seg button')].find(b => b.textContent === {json.dumps(t)}).click()")

        def dk_tile(name):
            q(f"[...document.querySelectorAll('#dk-body .dk-drives button')].find(b => b.textContent.startsWith({json.dumps(name + ' ·')})).click()")
            c.wait(f"[...document.querySelectorAll('#dk-body .dk-drives .tile.on')].some(b => b.textContent.startsWith({json.dumps(name + ' ·')}))", 10)

        # a reading saved earlier on this machine (SAVE READING needs a live meter, which isn't running here)
        os.makedirs(f"{T}/ctrl/captures", exist_ok=True)
        with open(f"{T}/ctrl/captures/gauntlet-legends.csv", "w") as f:
            f.write("iso,epoch,value,unit,mode,profile,label,flags\n2026-11-03T07:00:00,1793700000.000,5.02,V,VDC,rail-5v,CN1,\n")
        click('[data-view="disk"]')
        c.wait("document.querySelectorAll('#dk-body .dk-drives .tile').length === 3", 15)
        tiles = q("[...document.querySelectorAll('#dk-body .dk-drives .tile')].map(t => t.tagName + ' ' + t.textContent)")
        check("DISK: the store CONNECTED; sda a button, the IMAGE STORE and an EMPTY SLOT plain tiles that say so",
              "CONNECTED" in text("#dk-body .dk-store") and tiles[0].startswith("BUTTON sda · WIDGET WIDGETDRIVE 1000")
              and tiles[1].startswith("DIV sdc") and "IMAGE STORE" in tiles[1] and tiles[2].startswith("DIV sdd")
              and "EMPTY SLOT" in tiles[2] and text("#n-disk-s") == "1 drive")
        check("READ says to run the Pi from its wall supply, not the cabinet's 12 V buck (note §1, row 10)",
              "wall supply" in text("#dk-body") and "12 V buck" in text("#dk-body"))
        check("READ is off until the source, the media and a label are picked",
              q("document.querySelector('#dk-body button.dk-go').disabled") and "pick the source" in text("#dk-body"))
        dk_tile("sda")
        dk_seg("HDD")
        dk_tap("TYPE A LABEL")
        c.wait("document.querySelector('#pane .kp-field')")
        tap_keys("WW-HDD")
        c.wait("document.querySelector('#dk-body').textContent.includes('LABEL: WW-HDD')", 10)
        check("... then READ is on, for the current machine, and says what an HDD read does",
              not q("document.querySelector('#dk-body button.dk-go').disabled") and "Gauntlet Legends" in text("#dk-body")
              and "one pass with no retries" in text("#dk-body"))
        shot("23a-disk-read")
        click("#dk-body button.dk-go")
        check("READ: the progress card", dk_has("READING WW-HDD"))
        shot("23b-disk-reading")
        dk_has("STORED", 40)
        stored = [f for f in os.listdir(f"{FD.store}/gauntlet-legends") if f.endswith(".img")] \
            if os.path.isdir(f"{FD.store}/gauntlet-legends") else []
        import re as _re
        check("... STORED: the dated name, read once, queued for the archive; it's in the store's list",
              len(stored) == 1 and _re.fullmatch(r"WW-HDD_\d{8}_[0-9a-f]{8}\.img", stored[0]) is not None
              and stored[0] in text("#dk-body") and "read once" in text("#dk-body")
              and settles(c, "[...document.querySelectorAll('#dk-body .dk-img b')].some(b => b.textContent.startsWith('WW-HDD'))", 10))
        side = json.load(open(f"{FD.store}/gauntlet-legends/{stored[0][:-4]}.json"))
        check("... and the image's fingerprint (non-zero bytes in its first 64 MiB, the bench number) is on the result card",
              f"fingerprint {side['fingerprint']['nonzero']:,} non-zero bytes in the first 64 MiB" in text("#dk-body"))
        shot("23c-disk-stored")
        # the MAME CHD check (Task 3): a button on each stored image of a machine; with no reference CHD in _chd/ it says which to copy there
        check("the images list has MAME CHECK on the image, and says how many reference CHDs the store has",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-img button')].some(b => b.textContent === 'MAME CHECK')", 10)
              and settles(c, "document.querySelector('#dk-body .dk-chds') && document.querySelector('#dk-body .dk-chds').textContent.includes('0 in the store')", 10))
        q("[...document.querySelectorAll('#dk-body .dk-img')].find(r => r.textContent.startsWith('WW-HDD')).querySelector('button').click()")
        check("MAME CHECK with no reference CHD: NO REFERENCE CHD, naming the CHD to copy into _chd (gauntleg)",
              dk_has("NO REFERENCE CHD", 30) and "gauntleg (gauntleg)" in text("#dk-body") and "Copy MAME's CHD" in text("#dk-body"))
        check("... the image's tag says so, and its button now reads MAME CHECK AGAIN",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-img')].some(r => r.textContent.includes('NO REFERENCE CHD') && r.textContent.includes('MAME CHECK AGAIN'))", 15))
        # NOT ARCADE and FILE UNDER (the owner, 2026-11-17: "an easy way to set the machine as unclassified or misc")
        WWROW = "[...document.querySelectorAll('#dk-body .dk-img')].find(r => r.textContent.startsWith('WW-HDD'))"
        check("each stored image has FILE UNDER", settles(c, f"!!{WWROW} && [...{WWROW}.querySelectorAll('button')].some(b => b.textContent === 'FILE UNDER')", 10))
        q(f"[...{WWROW}.querySelectorAll('button')].find(b => b.textContent === 'FILE UNDER').click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length > 1", 10)
        check("FILE UNDER's list starts with NOT ARCADE, then the roster",
              q("document.querySelector('#pane .picklist .row').textContent").startswith("NOT ARCADE"))
        q("document.querySelector('#pane .picklist .row').click()")
        check("picking NOT ARCADE files it there: FILED UNDER NOT ARCADE, and the image's line says NOT ARCADE",
              dk_has("FILED UNDER NOT ARCADE", 30) and settles(c, f"!!{WWROW} && {WWROW}.textContent.includes('NOT ARCADE ·')", 15)
              and json.load(open(f"{FD.store}/gauntlet-legends/{stored[0][:-4]}.json"))["machine"] == "unassigned")
        shot("23d-disk-filed")
        settles(c, f"!!{WWROW} && ![...{WWROW}.querySelectorAll('button')].find(b => b.textContent === 'FILE UNDER').disabled", 10)
        q("document.querySelector('#pane h2').textContent = ''")       # the closed sheet keeps its rows: mark it, and wait for the new one
        q(f"[...{WWROW}.querySelectorAll('button')].find(b => b.textContent === 'FILE UNDER').click()")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane h2').textContent.startsWith('File')", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('Gauntlet Legends') || r.textContent.includes('gauntlet-legends')).click()")
        check("... and FILE UNDER gauntlet-legends puts it back (its folder never moved)",
              dk_has("FILED UNDER gauntlet-legends", 30)
              and json.load(open(f"{FD.store}/gauntlet-legends/{stored[0][:-4]}.json"))["machine"] == "gauntlet-legends")
        # the drain to the archive (gatbox-disk-send, home network only): its line, then ARCHIVED and SENDING from its state
        check("before the sender ever ran: the images card says so, and that it sends over the home network only",
              settles(c, "!!document.querySelector('#dk-body .dk-send') && document.querySelector('#dk-body .dk-send').textContent.includes('not run yet')", 10)
              and "home network only" in text("#dk-body .dk-send"))
        ww_side = json.load(open(f"{FD.store}/gauntlet-legends/{stored[0][:-4]}.json"))
        os.makedirs(f"{T}/dsend", exist_ok=True)
        json.dump({"at": time.time(), "result": "sending", "why": "sending WW-HDD", "sent": {},
                   "current": {"sha256": ww_side["sha256"], "image": "x", "have": 1, "parts": 4}}, open(f"{T}/dsend/state.json", "w"))
        click('[data-view="meter"]'); click('[data-view="disk"]')
        check("an image going up: SENDING 25 %", settles(c, f"!!{WWROW} && {WWROW}.textContent.includes('SENDING 25 %')", 10))
        json.dump({"at": time.time(), "result": "away", "why": "no answer from 192.0.2.7:8081: not on the home network, or the archive is down",
                   "sent": {ww_side["sha256"]: {"at": time.time()}}}, open(f"{T}/dsend/state.json", "w"))
        click('[data-view="meter"]'); click('[data-view="disk"]')
        check("once the archive has it: ARCHIVED; and the line says how many are there and why nothing is moving now",
              settles(c, f"!!{WWROW} && {WWROW}.textContent.includes('ARCHIVED')", 10)
              and settles(c, "document.querySelector('#dk-body .dk-send').textContent.includes('1 image there') && document.querySelector('#dk-body .dk-send').textContent.includes('not on the home network')", 10))
        shot("23e-disk-archived")
        os.remove(f"{T}/dsend/state.json")
        # IMPORT (the owner, 2026-11-17: an image from the laptop): a file copied into the store's _incoming folder
        os.makedirs(f"{FD.store}/_incoming", exist_ok=True)
        open(f"{FD.store}/_incoming/carnevil-hdd.img", "wb").write(os.urandom(2 * MiB + 9))
        click('[data-view="meter"]'); click('[data-view="disk"]')
        check("the images card lists what is waiting to import, with IMPORT",
              settles(c, "!!document.querySelector('#dk-body .dk-incoming') && document.querySelector('#dk-body .dk-incoming').textContent.includes('carnevil-hdd.img')", 15)
              and "Waiting to import (1)" in text("#dk-body .dk-incoming")
              and q("[...document.querySelectorAll('#dk-body .dk-incoming button')].some(b => b.textContent === 'IMPORT')"))
        shot("23f-disk-incoming")
        q("document.querySelector('#pane h2').textContent = ''")
        q("[...document.querySelectorAll('#dk-body .dk-incoming button')].find(b => b.textContent === 'IMPORT').click()")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane h2').textContent.startsWith('Which machine')", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('Gauntlet Legends') || r.textContent.includes('gauntlet-legends')).click()")
        c.wait("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'HDD')", 10)
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'HDD').click()")
        c.wait("!!document.querySelector('#pane .kp-field')", 10)
        check("... the label is offered from the file's name", text("#pane .kp-field") == "carnevil-hdd")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAVE').click()")
        check("IMPORT runs as a disk job: IMPORTED, and the image is in the list tagged IMPORTED, the waiting list gone",
              dk_has("IMPORTED", 30) and settles(c, "[...document.querySelectorAll('#dk-body .dk-img')].some(r => r.textContent.startsWith('carnevil-hdd') && r.textContent.includes('IMPORTED'))", 15)
              and settles(c, "!document.querySelector('#dk-body .dk-incoming')", 10))
        shot("23g-disk-imported")
        for p_ in glob.glob(f"{FD.store}/gauntlet-legends/carnevil-hdd_*"):     # later checks count the store's images
            os.remove(p_)
        FORROW = "[...document.querySelectorAll('#dk-body .btnrow')].find(r => r.firstChild && r.firstChild.textContent === 'FOR')"
        check("the READ's FOR row has NOT ARCADE beside OTHER MACHINE",
              settles(c, f"!!{FORROW} && [...{FORROW}.querySelectorAll('button')].some(b => b.textContent === 'NOT ARCADE')", 10))
        q(f"[...{FORROW}.querySelectorAll('button')].find(b => b.textContent === 'NOT ARCADE').click()")
        check("one tap: FOR NOT ARCADE, the button gives way to CURRENT",
              settles(c, f"{FORROW}.querySelector('.chip').textContent === 'NOT ARCADE' && [...{FORROW}.querySelectorAll('button')].some(b => b.textContent === 'CURRENT')"
                         f" && ![...{FORROW}.querySelectorAll('button')].some(b => b.textContent === 'NOT ARCADE')", 10))
        q(f"[...{FORROW}.querySelectorAll('button')].find(b => b.textContent === 'CURRENT').click()")
        check("... CURRENT goes back to the session's machine", settles(c, f"{FORROW}.querySelector('.chip').textContent !== 'NOT ARCADE'", 10))

        FD.usb_disk("sde", MiB, "WIDGET", "OLDSPINNER 40", port=5, devnum="8:64", data=os.urandom(MiB))
        open(f"{T}/dk-bad", "w").close()
        c.wait("[...document.querySelectorAll('#dk-body .dk-drives button')].some(b => b.textContent.startsWith('sde ·'))", 15)
        dk_tile("sde")
        dk_tap("LABEL: WW-HDD")
        c.wait("document.querySelector('#pane .kp-field')")
        tap_keys("⌫⌫⌫OLD")
        c.wait("document.querySelector('#dk-body').textContent.includes('LABEL: WW-OLD')", 10)
        open(f"{T}/dk-slow", "w").close()
        click("#dk-body button.dk-go")
        check("READ in progress: the card says how much has been read from the drive, not just the step it began at",
              dk_has("read from the drive", 30) and q("!!document.querySelector('#dk-body .dk-live')")
              and 0.05 < float(q("parseFloat(document.querySelector('#dk-body .dk-bar > div').style.width) / 100")) < 0.78)
        shot("23c-disk-reading-live")
        os.remove(f"{T}/dk-slow")
        dk_has("BAD AREAS", 40)
        check("bad areas: BAD AREAS with the map's numbers, and RETRY is the tech's call",
              "% read · 1 bad area" in text("#dk-body") and "never retries on its own" in text("#dk-body")
              and q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent === 'RETRY THE BAD AREAS')"))
        shot("23d-disk-bad-areas")
        open(f"{T}/dk-heal", "w").close()
        dk_tap("RETRY THE BAD AREAS")
        check("RETRY: the bad areas read after all, STORED", dk_has("RETRYING", 10) and dk_has("STORED", 40)
              and "WW-OLD" in text("#dk-body"))
        os.remove(f"{T}/dk-bad")
        os.remove(f"{T}/dk-heal")
        # A READ whose bytes are already stored under ANOTHER machine's folder says where they are. The status file is rewritten to that
        # (what gatbox-disk writes then: tests/test-disk.py), the card is read, and the real one is put back.
        st_file = f"{T}/dspool/status.json"
        keep = open(st_file).read()
        stj = json.loads(keep)
        with open(st_file + ".tmp", "w") as f:
            json.dump(dict(stj, stored={"path": f"{FD.store}/unassigned/" + os.path.basename(stj["stored"]["path"]), "new": False, "machine": "unassigned"}), f)
        os.replace(st_file + ".tmp", st_file)
        check("a READ whose bytes are already under another machine's folder: ALREADY IN THE STORE, and the card says it is stored under unassigned and stays there",
              dk_has("ALREADY IN THE STORE", 15) and "stored under unassigned" in text("#dk-body") and "which stays there" in text("#dk-body")
              and "Queued for the archive" not in text("#dk-body"))
        with open(st_file + ".tmp", "w") as f:
            f.write(keep)
        os.replace(st_file + ".tmp", st_file)
        dk_has("STORED", 15)

        FD.usb_disk("sdh", 8 * MiB, "WIDGET", "WIDGET SSD 64G C", port=8, devnum="8:112", data=fat_card(8 * MiB))
        dk_seg("WRITE")
        c.wait("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent === 'PICK AN IMAGE')", 10)
        check("WRITE says it too", "wall supply" in text("#dk-body"))
        dk_tap("PICK AN IMAGE")
        c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.startsWith('WW-HDD'))", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('WW-HDD')).click()")
        c.wait("[...document.querySelectorAll('#dk-body .dk-drives button')].some(b => b.textContent.startsWith('sdh ·'))", 15)
        small = q("[...document.querySelectorAll('#dk-body .dk-drives .tile')].find(t => t.textContent.startsWith('sde ·'))")
        check("WRITE: a drive smaller than the image is a plain tile, TOO SMALL FOR THE IMAGE",
              q("(() => { const t = [...document.querySelectorAll('#dk-body .dk-drives .tile')].find(t => t.textContent.startsWith('sde ·'));"
                " return !!t && t.tagName === 'DIV' && t.textContent.includes('TOO SMALL'); })()") and small is not None)
        dk_tile("sda")
        dk_tap("TARGET CHECK")
        check("TARGET CHECK on the drive the image came from: TARGET REFUSED, it says why, and that RESTORE is the way",
              dk_has("TARGET REFUSED", 30) and "the drive the image came from" in text("#dk-body") and "use RESTORE" in text("#dk-body"))
        check("... and the refusal says when it was (the last job, at HH:MM:SS), so an old one doesn't read as now",
              re.search(r"the last job, at \d\d:\d\d:\d\d", text("#dk-body")) is not None)
        check("RESTORE CHECK sits beside TARGET CHECK, and the words under them say what it is for",
              q("[...document.querySelectorAll('#dk-body .btnrow button')].map(b => b.textContent).join('|').includes('TARGET CHECK|RESTORE CHECK')")
              and "RESTORE CHECK is the same for the drive this image came from" in text("#dk-body"))
        dk_tap("RESTORE CHECK")
        dk_has("TARGET OK", 30)
        check("RESTORE CHECK on the original while it still holds the image: it says so and offers VERIFY ONLY, nothing to restore",
              "sda already holds this image" in text("#dk-body")
              and q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent === 'VERIFY ONLY')"))
        dk_tap("TARGET CHECK")                                     # back to a refusal, so the next wait is not met by this result
        dk_has("TARGET REFUSED", 30)
        sda_path = f"{FD.dev}/sda"
        with open(sda_path, "r+b") as f:                           # a game has written to the original since the read
            sda_head = f.read(4096)
            f.seek(0)
            f.write(os.urandom(4096))
        dk_tap("RESTORE CHECK")
        dk_has("TARGET OK", 30)
        check("RESTORE CHECK on the original after something wrote to it: it says RESTORE, what is on it is replaced, and there is no hold "
              "before the acknowledgement",
              "RESTORE. sda is the drive this image came from" in text("#dk-body") and "replaces everything on it now" in text("#dk-body")
              and "NOT BLANK" not in text("#dk-body")
              and not q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD'))"))
        dk_tap("OVERWRITE THE ORIGINAL")
        check("... after the acknowledgement the hold names the restore, the image and the drive",
              settles(c, "[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD 3 S TO RESTORE WW-HDD → sda'))", 10))
        with open(sda_path, "r+b") as f:                           # the original as it was, for the checks that follow
            f.write(sda_head)
        dk_tap("TARGET CHECK")                                     # back to the refusal, so the next check's wait is not met by this one
        dk_has("TARGET REFUSED", 30)
        dk_tile("sdh")
        dk_tap("TARGET CHECK")
        dk_has("TARGET OK", 30)
        check("TARGET CHECK on the card: TARGET OK, NOT BLANK with what's on it, and no hold before the acknowledgement",
              "NOT BLANK" in text("#dk-body") and "vfat filesystem on the whole drive 'WIDGETCARD'" in text("#dk-body")
              and not q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD'))"))
        q("document.querySelector('#dk-body .dk-over').scrollIntoView({block: 'center'})")
        shot("23e-disk-check")
        dk_tap("OVERWRITE IT: I'M SURE")
        c.wait("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD 3 S TO OVERWRITE sdh WITH WW-HDD'))", 10)
        q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
        time.sleep(1.0)
        q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")
        time.sleep(1.0)
        card = open(f"{FD.dev}/sdh", "rb").read(512)
        check("let go early: nothing armed, nothing sent, the card as it was",
              not os.path.exists(f"{T}/dspool/request.json") and card == fat_card(8 * MiB)[:512])
        q("document.querySelector('#dk-body button.hold').scrollIntoView({block: 'center'})")
        shot("23f-disk-armable")
        q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
        check("held 3 s: WRITING", dk_has("WRITING", 15))
        dk_has("VERIFIED", 40)
        img = open(f"{FD.store}/gauntlet-legends/{stored[0]}", "rb").read() if stored else b"x"
        check("... VERIFIED: the card holds the image, the rest of it left as it was",
              open(f"{FD.dev}/sdh", "rb").read(len(img)) == img and "beyond the image left as it was" in text("#dk-body"))
        shot("23g-disk-verified")
        dk_tap("THE SWAP CHECKLIST")
        c.wait("document.querySelectorAll('#dk-body button.dk-tick').length === 10", 10)
        check("the swap checklist: this write's, ten lines, none ticked", "→ sdh" in text("#dk-body")
              and not q("document.querySelectorAll('#dk-body .dk-tick.done').length"))
        check("... its 5 V line shows the machine's last saved reading (note §1, row 9), with SAVE READING beside it",
              settles(c, "document.querySelector('#dk-body').textContent.includes('Last saved on this machine: CN1 5.02 V')", 10)
              and q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent === 'SAVE READING')"))
        q("[...document.querySelectorAll('#dk-body button')].find(b => b.textContent === 'SAVE READING').click()")
        check("... SAVE READING with no live meter says so, it doesn't do nothing",
              settles(c, "document.querySelector('#toast').textContent.includes('No live reading to save')", 5))
        q("[...document.querySelectorAll('#dk-body button.dk-tick')].find(b => b.textContent.includes('5 V at the board')).click()")
        c.wait("document.querySelectorAll('#dk-body .dk-tick.done').length === 1", 10)
        cl = api("GET", "/api/disk/checklist")
        check("... a tap ticks a line, with its time, and gatbox-web keeps it",
              [i["item"] for i in cl["items"] if i["done"]] == ["five_volts"])
        q("document.querySelector('#dk-body .dk-reading').scrollIntoView({block: 'center'})")
        shot("23h-disk-checklist")

        # FORMAT (docs/plans/2026-11-16-restore-and-format.md): a quick format from the 7", armed like a write. Skipped where mkfs.fat is missing.
        if not shutil.which("mkfs.fat", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin"):
            print("  skip  FORMAT on the 7\": mkfs.fat is not installed here (apt: dosfstools)")
        else:
            old_card = bytes(range(1, 256)) * (24 * MiB // 255 + 1)
            FD.usb_disk("sdp", 24 * MiB, "WIDGET", "WIDGET CF 24", port=16, devnum="8:240", data=fat_card(4096) + old_card[4096:24 * MiB], rotational=False)
            dk_seg("FORMAT")
            fbtn = lambda t: f"[...document.querySelectorAll('#dk-body button')].find(b => b.textContent.startsWith({json.dumps(t)}))"
            check("FORMAT is a mode of the DISK tab: it says what a quick format is, and FORMAT CHECK is off until a drive and a filesystem are picked",
                  dk_has("A quick format: a new partition table and one empty filesystem", 10) and settles(c, f"!!{fbtn('sdp ·')}", 15)
                  and q(f"{fbtn('FORMAT CHECK')}.disabled") and "After a FORMAT CHECK" in text("#dk-body"))
            check("... the seven modes fit the 7\" in one row, each label on one line, nothing wider than the screen, no text under 14 px",
                  q("document.querySelectorAll('#dk-body > .seg button').length") == 7
                  and q("new Set([...document.querySelectorAll('#dk-body > .seg button')].map(b => Math.round(b.getBoundingClientRect().top))).size") == 1
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1") and not q(BOX_SMALL))
            dk_tile("sdp")
            dk_tap("FAT32")
            check("a filesystem picked says what it is good for, and the label can stay empty", "No file over 4 GB" in text("#dk-body")
                  and "LABEL: NONE" in text("#dk-body") and not q(f"{fbtn('FORMAT CHECK')}.disabled"))
            dk_tap("LABEL: NONE")
            c.wait("document.querySelector('#pane .kp-field')")
            tap_keys("WIDGET")
            c.wait("document.querySelector('#dk-body').textContent.includes('LABEL: WIDGET')", 10)
            dk_tap("FORMAT CHECK")
            check("FORMAT CHECK on a card with files and no image in the store: it passes, says what the drive becomes (FAT16: too small for FAT32), "
                  "and WARNS in capitals that the store has no image, with IMAGE IT FIRST", dk_has("FORMAT CHECK PASSED", 30)
                  and "sdp becomes MBR with one FAT16 partition 'WIDGET'" in text("#dk-body") and "too small for FAT32: it gets FAT16" in text("#dk-body")
                  and "THE STORE HAS NO IMAGE OF WHAT IS ON THIS DRIVE NOW." in text("#dk-body") and "vfat filesystem on the whole drive" in text("#dk-body")
                  and q(f"!!{fbtn('IMAGE IT FIRST')}"))
            check("... no hold before the acknowledgement, and the acknowledgement names what is being given up",
                  not q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD'))") and q(f"!!{fbtn('ERASE IT WITH NO IMAGE')}"))
            q("document.querySelector('#dk-body .dk-over').scrollIntoView({block: 'center'})")
            check("... on the 7\": no text under 14 px, the buttons a finger tall", not q(BOX_SMALL)
                  and q(f"{fbtn('IMAGE IT FIRST')}.getBoundingClientRect().height") >= 55.5 and q(f"{fbtn('ERASE IT WITH NO IMAGE')}.getBoundingClientRect().height") >= 55.5)
            shot("23k-format-check")
            dk_tap("IMAGE IT FIRST")
            check("IMAGE IT FIRST goes to READ with this drive picked as the source", settles(c, "document.querySelector('#dk-body').textContent.includes('1 · SOURCE')", 10)
                  and q("[...document.querySelectorAll('#dk-body .dk-drives .tile.on')].some(t => t.textContent.startsWith('sdp ·'))"))
            dk_seg("FORMAT")
            dk_tap("ERASE IT WITH NO IMAGE")
            c.wait("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD 3 S TO FORMAT sdp AS FAT16'))", 10)
            q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            time.sleep(1.0)
            q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")
            time.sleep(1.0)
            check("let go early: nothing armed, nothing sent, the card as it was",
                  not os.path.exists(f"{T}/dspool/request.json") and open(f"{FD.dev}/sdp", "rb").read(4096) == fat_card(4096))
            q("document.querySelector('#dk-body button.hold').scrollIntoView({block: 'center'})")
            shot("23l-format-armable")
            q("document.querySelector('#dk-body button.hold').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            check("held 3 s: FORMATTED, said in full: MBR, one FAT16 partition, its label, read back twice",
                  dk_has("FORMATTED", 40) and "sdp is now MBR with one FAT16 partition 'WIDGET'" in text("#dk-body") and "read back twice: both match" in text("#dk-body"))
            card = open(f"{FD.dev}/sdp", "rb").read()
            check("... the card: a partition table at sector 0, a FAT boot sector labelled WIDGET at 1 MiB, and the old bytes further in untouched",
                  card[510:512] == b"\x55\xaa" and card[446 + 4] in (0x04, 0x06) and card[MiB + 54:MiB + 59] == b"FAT16" and card[MiB + 43:MiB + 49] == b"WIDGET"
                  and card[12 * MiB:13 * MiB] == old_card[12 * MiB:13 * MiB])
            check("... it says the Pi still lists the old partitions and to switch the dock off and on (this fake kernel never re-reads them)",
                  "still lists the old partitions" in text("#dk-body") and "Switch the dock off and on" in text("#dk-body"))
            check("... and the log on the screen has it: the drive, FAT16 'WIDGET', verified",
                  settles(c, "!!document.querySelector('#dk-body .dk-fmtrow') && document.querySelector('#dk-body .dk-fmtrow').textContent.includes(\"MBR, FAT16 'WIDGET': verified\")", 15)
                  and "WIDGET CF 24" in text("#dk-body .dk-fmtrow"))
            check("... after it, the hold is gone: the check is spent, and a new format needs a new check",
                  not q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.startsWith('HOLD'))") and "After a FORMAT CHECK" in text("#dk-body"))
            q("document.querySelector('#dk-body').scrollIntoView()")
            shot("23m-format-done")
            dk_tap("FORMAT CHECK")
            check("a second FORMAT CHECK of the same card: it still warns (nothing in the store starts with these bytes), and the plain refusal words are not used",
                  dk_has("FORMAT CHECK PASSED", 30) and "THE STORE HAS NO IMAGE" in text("#dk-body"))
            FD.unplug("sdp")
            dk_seg("READ")
        # DRIVE INFO, the CF adapter's warning, the swap's steps and the job record (the owner's bench procedure, 2026-11-12)
        sda_args = dict(port=1, devnum="8:0")

        def dk_step(text_part, mode=None):
            return q("(() => { const s = [...document.querySelectorAll('#dk-body .dk-step')].find(s => s.textContent.includes("
                     + json.dumps(text_part) + ")); return s ? s.className.replace('dk-step ', '') : null; })()")

        def dk_wait_step(text_part, state, timeout=20):
            return settles(c, "(() => { const s = [...document.querySelectorAll('#dk-body .dk-step')].find(s => s.textContent.includes("
                           + json.dumps(text_part) + ")); return !!s && s.classList.contains(" + json.dumps(state) + "); })()", timeout)
        dk_seg("READ")
        dk_tap("DRIVE INFO")
        c.wait("document.querySelector('#dk-body .dk-infocard')", 10)
        cards = q("[...document.querySelectorAll('#dk-body .dk-infocard')].map(c => c.textContent)")
        sda_card = next((t for t in cards if t.startswith("sda · WIDGET WIDGETDRIVE 1000")), "")
        check("DRIVE INFO: a card for each drive with something in it, none for the empty slot; sda's has its model, serial, "
              "exact size and sector size", bool(sda_card) and not any(t.startswith("sdd") for t in cards)
              and "Serial" in sda_card and "none the drive reported" in sda_card and "3,145,728 bytes" in sda_card
              and "512 logical" in sda_card)
        check("... a drive that passed no IDENTIFY says so, and shows only the kernel's facts (no firmware, SATA or cache lines)",
              "passed no IDENTIFY through the bridge" in sda_card and "Firmware" in sda_card and "not reported" in sda_card
              and "Write cache" not in sda_card)
        check("... and the page says nothing is sent to a drive from here, and why SMART and the native size aren't read",
              "Nothing is sent to a drive from here" in text("#dk-body") and "hangs on those commands" in text("#dk-body"))
        FD.usb_disk("sdk", 4 * MiB, "XinTop", "XT-U33502", port=11, devnum="8:160", data=os.urandom(4 * MiB), physical=4096,
                    ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"),
                    udev={"ID_REVISION": "10.0", "ID_ATA_ROTATION_RATE_RPM": "7200", "ID_ATA_SATA": "1",
                          "ID_ATA_SATA_SIGNAL_RATE_GEN2": "1", "ID_ATA_WRITE_CACHE": "1", "ID_ATA_WRITE_CACHE_ENABLED": "1",
                          "ID_ATA_FEATURE_SET_HPA": "1", "ID_ATA_FEATURE_SET_HPA_ENABLED": "1",
                          "ID_ATA_FEATURE_SET_SMART": "1", "ID_ATA_FEATURE_SET_SMART_ENABLED": "0"})
        check("DRIVE INFO on an ATA drive: its own model and serial, firmware, spin speed, SATA speed, cache, feature sets "
              "and sector sizes, and the dock's name as the bridge",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-infocard')].some(c => c.textContent.startsWith('sdk · WDC WD800JD-75MSA1'))", 20)
              and all(s in text("#dk-body") for s in ("WD-WIDGET0001", "10.0", "7,200 rpm", "signalling up to 3.0 Gb/s", "Write cache", "on",
                                                      "Host Protected Area (on), SMART (off)", "512 logical, 4096 physical",
                                                      "XinTop XT-U33502")))
        check("... and that the drive has the HPA feature set, which says nothing about a hidden area the dock can't show",
              "can't be read through this dock" in text("#dk-body"))
        q("document.querySelector('#dk-body .dk-infocard:nth-of-type(2)') && document.querySelector('#dk-body .dk-infobox').scrollIntoView()")
        small = q(BOX_SMALL)
        check("DRIVE INFO on the 7\": no text under 14 px", not small)
        shot("23j-disk-drive-info")
        # A CF card on the dock's IDE side arrives with each pair of bytes in its INQUIRY swapped (the Pi, 2026-11-16: ASSMNU G / FCA/AT for
        # SAMSUNG / CF/ATA). Shown turned back when the vendor is a known maker; display only, so the raw name is on the DRIVE INFO card.
        FD.usb_disk("sdl", 4 * MiB, "ASSMNU G", "FCA/AT", port=12, devnum="8:176", data=os.urandom(4 * MiB), rotational=False)
        FD.usb_disk("sdm", 4 * MiB, "QXZW", "YV", port=13, devnum="8:192", data=os.urandom(4 * MiB), rotational=False)
        FD.usb_disk("sdn", 4 * MiB, "ASSMNU G", "BAC", port=14, devnum="8:208", data=os.urandom(4 * MiB), rotational=False)   # "AB C", whose trimmed pair-swap is odd
        check("DRIVE INFO: a CF card whose INQUIRY came swapped is named the right way round (SAMSUNG CF/ATA), and says what the dock reported",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-infocard')].some(c => c.textContent.startsWith('sdl · SAMSUNG CF/ATA'))", 20)
              and "Reported as" in text("#dk-body") and "ASSMNU G FCA/AT" in text("#dk-body"))
        check("... a name whose swapped form has an odd length once its pad is trimmed (BAC for AB C) is turned back whole",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-infocard')].some(c => c.textContent.startsWith('sdn · SAMSUNG AB C'))", 20))
        check("... a swapped-looking name whose vendor is no known maker stays as the bridge said it, and the real ones are left alone",
              q("[...document.querySelectorAll('#dk-body .dk-infocard')].some(c => c.textContent.startsWith('sdm · QXZW YV'))")
              and q("[...document.querySelectorAll('#dk-body .dk-infocard')].some(c => c.textContent.startsWith('sda · WIDGET WIDGETDRIVE 1000'))")
              and q("[...document.querySelectorAll('#dk-body .dk-infocard')].filter(c => c.textContent.includes('Reported as')).length") == 2)
        check("... the drive tiles use the same name, never the swapped one",
              q("[...document.querySelectorAll('#dk-body .dk-drives button')].some(b => b.textContent.includes('SAMSUNG CF/ATA'))")
              and not q("[...document.querySelectorAll('#dk-body .dk-drives button')].some(b => b.textContent.includes('ASSMNU'))"))
        FD.unplug("sdl")
        FD.unplug("sdm")
        FD.unplug("sdn")
        dk_tap("HIDE DRIVE INFO")
        check("HIDE DRIVE INFO: the cards go", settles(c, "!document.querySelector('#dk-body .dk-infocard')", 5))
        # the CF adapter's power (procedure §2)
        check("READ: no CF warning until it's a CF card", not q("document.querySelector('#dk-body .dk-cf')"))
        q("[...document.querySelectorAll('#dk-body .seg button')].find(b => b.textContent === 'CF').click()")
        c.wait("document.querySelector('#dk-body .dk-cf')", 5)
        check("READ with CF picked: +5 V ONLY, red and black, the yellow +12 V never reaches the card, meter the plug first",
              all(s in text("#dk-body .dk-cf") for s in ("+5 V ONLY", "red is +5 V", "black is ground", "yellow +12 V wire must never reach the card",
                                                          "Meter the plug", "don't take a card hot", "dock's own Molex", "nothing else")))
        check("the CF warning on the 7\": no text under 14 px", not q(BOX_SMALL))
        shot("23k-disk-cf-warning")
        q("[...document.querySelectorAll('#dk-body .seg button')].find(b => b.textContent === 'HDD').click()")
        check("... HDD again: the warning goes", settles(c, "!document.querySelector('#dk-body .dk-cf')", 5))
        FD.usb_disk("sdj", 6 * MiB, "WIDGET", "WIDGET CF 6M", port=10, devnum="8:144", data=os.urandom(6 * MiB), rotational=False)
        dk_seg("WRITE")
        c.wait("[...document.querySelectorAll('#dk-body .dk-drives button')].some(b => b.textContent.startsWith('sdj ·'))", 20)
        dk_tile("sdj")
        check("WRITE to a target that doesn't spin: the CF warning, worded as a question (the dock can't tell a card from an SSD)",
              "CF-to-IDE adapter? +5 V ONLY" in text("#dk-body .dk-cf"))
        FD.unplug("sdj")

        # SWAP: the steps tick themselves from the live drive list (procedure A7, B5 and ground rule 7)
        dk_seg("SWAP")
        check("SWAP: IDE is not hot-swap, dock off for every change; a picker for the drive being replaced, no steps yet",
              "IDE is not hot-swap" in text("#dk-body") and "Which drive is being replaced?" in text("#dk-body")
              and q("document.querySelectorAll('#dk-body .dk-step').length") == 0)
        q("[...document.querySelectorAll('#dk-body .dk-drives button')].find(b => b.textContent.startsWith('sda ·')).click()")
        c.wait("document.querySelectorAll('#dk-body .dk-step').length === 6", 10)
        check("... picked: six steps; the first is NOW (the dock must go off), the rest wait",
              dk_step("Power the dock off") == "now" and dk_step("Pull the original") == "todo"
              and dk_step("Seat the new one") == "todo" and dk_step("Prove the swap") == "todo")
        shot("23l-disk-swap")
        FD.unplug("sda")
        check("the original leaves the list (the dock is off): step 1 is DONE by itself, and the pull is NOW",
              dk_wait_step("Power the dock off", "done") and dk_step("Pull the original") == "now")
        q("[...document.querySelectorAll('#dk-body button.dk-step')].find(b => b.textContent.includes('Pull the original')).click()")
        check("... tapping the pull ticks it, and the new drive is what the page waits for",
              dk_step("Pull the original") == "done" and dk_step("Seat the new one") == "now")
        FD.usb_disk("sda", 5 * MiB, "WIDGET", "WIDGETDRIVE 2000", data=os.urandom(5 * MiB), **sda_args)
        check("a different drive appears: seated, not the original, big enough with 512-byte sectors; TARGET CHECK is NOW",
              dk_wait_step("Seat the new one", "done") and dk_step("It is not the original") == "done"
              and dk_step("Big enough") == "done" and dk_step("Prove the swap") == "now"
              and "WIDGETDRIVE 2000" in text("#dk-body"))
        check("... a drive that was already on the list (sde, in another slot) is not taken for the new one",
              q("[...document.querySelectorAll('#dk-body .dk-step')].every(s => !s.textContent.includes('OLDSPINNER'))"))
        check("the swap's steps on the 7\": no text under 14 px, and each step button is at least 56 px tall (a finger)",
              not q(BOX_SMALL) and q("[...document.querySelectorAll('#dk-body button.dk-step')].every(b => b.getBoundingClientRect().height >= 55.5)"))
        shot("23m-disk-swap-seated")
        q("[...document.querySelectorAll('#dk-body button.dk-step')].find(b => b.textContent.includes('Prove the swap')).click()")
        check("the last step hands the new drive to WRITE's TARGET CHECK, as the target",
              settles(c, "[...document.querySelectorAll('#dk-body .dk-drives .tile.on')].some(t => t.textContent.startsWith('sda ·'))", 10)
              and q("document.querySelector('#dk-body .seg .on').textContent") == "WRITE")
        # the same drive put back: the swap did not take (procedure A7's STOP), by serial where there is one
        dk_seg("SWAP")
        dk_tap("START OVER")
        q("[...document.querySelectorAll('#dk-body .dk-drives button')].find(b => b.textContent.startsWith('sdk ·')).click()")
        c.wait("document.querySelectorAll('#dk-body .dk-step').length === 6", 10)
        FD.unplug("sdk")
        dk_wait_step("Power the dock off", "done")
        FD.usb_disk("sdk", 4 * MiB, "XinTop", "XT-U33502", port=11, devnum="8:160", data=os.urandom(4 * MiB),
                    ata=("WDC WD800JD-75MSA1", "WD-WIDGET0001"))
        check("the same drive back (same model and serial): STOP, the swap didn't take, dock off and reseat",
              dk_wait_step("It is not the original", "bad", 25)
              and "the swap didn't take" in text("#dk-body") and dk_step("Prove the swap") == "todo")
        shot("23n-disk-swap-same")
        q("[...document.querySelectorAll('#dk-body .seg button')].find(b => b.textContent === 'CF').click()")
        check("SWAP with CF picked: the CF adapter's +5 V warning is in the steps too",
              settles(c, "!!document.querySelector('#dk-body .dk-cf')", 5) and "+12 V" in text("#dk-body .dk-cf"))
        q("[...document.querySelectorAll('#dk-body .seg button')].find(b => b.textContent === 'HDD').click()")
        FD.unplug("sdk")
        dk_tap("START OVER")
        FD.usb_disk("sda", 3 * MiB, "WIDGET", "WIDGETDRIVE 1000", data=os.urandom(3 * MiB), **sda_args)

        # RECORDS: Appendix B, filled in from what the deck wrote down
        dk_seg("RECORDS")
        c.wait("document.querySelector('#dk-body button.dk-recrow')", 10)
        check("RECORDS: one row per stored image, newest first; no PRINT button on the 7\" (it has no printer)",
              "WW-HDD" in text("#dk-body") and not q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent.includes('PRINT'))"))
        q("[...document.querySelectorAll('#dk-body button.dk-recrow')].find(b => b.textContent.startsWith('WW-HDD')).click()")
        c.wait("document.querySelector('#dk-body .dk-rec')", 10)
        side = json.load(open(f"{FD.store}/gauntlet-legends/{stored[0][:-4]}.json"))
        rec_text = text("#dk-body .dk-rec")
        check("the record: the read (machine, source drive, fingerprint, bytes, SHA-1 and SHA-256), the write that went to sdh, "
              "and this image's cab checklist",
              "JOB RECORD · WW-HDD" in rec_text and "THE READ" in rec_text and "gauntlet-legends" in rec_text
              and "WIDGETDRIVE 1000" in rec_text and side["sha1"] in rec_text and side["sha256"] in rec_text
              and f"{side['bytes']:,}" in rec_text and "WRITTEN TO A DRIVE" in rec_text
              and "verified: read back twice, both equal the image" in rec_text and "IN THE CAB" in rec_text)
        check("... the cab line that was ticked shows done with its time; one the deck can't know says 'not recorded'",
              q("[...document.querySelectorAll('#dk-body .dk-rt tr')].some(r => r.textContent.includes('5 V at the board') && r.textContent.includes('done'))")
              and q("[...document.querySelectorAll('#dk-body .dk-rt tr')].some(r => r.textContent.startsWith('Notes') && r.textContent.includes('not recorded'))"))
        check("... a verified write is green, a record fits the 7\" without scrolling sideways",
              q("document.querySelector('#dk-body .dk-rt .ok')") is not None
              and q("document.querySelector('#dk-body .dk-rec').scrollWidth <= document.querySelector('#dk-body .dk-rec').clientWidth + 1"))
        q("document.querySelector('#dk-body .dk-rec').scrollIntoView()")
        check("the job record on the 7\": no text under 14 px", not q(BOX_SMALL))
        shot("23o-disk-record")
        dk_tap("ALL RECORDS")
        check("ALL RECORDS: back to the list", settles(c, "!document.querySelector('#dk-body .dk-rec') && !!document.querySelector('#dk-body button.dk-recrow')", 5))
        dk_seg("CHECKLIST")

        # FILES: look inside a stored image (docs/plans/2026-11-15-drive-browser.md). The tools it needs may be missing: skip, saying so.
        gone = fakeimage.missing()
        try:
            import PIL  # noqa: F401
        except ImportError:
            gone.append("python3-pil")
        if gone:
            print(f"  skip  FILES (the browser's tools are not installed here: {', '.join(gone)})")
        else:
            print("FILES (a real gatbox-browse over an invented ext4 image):")
            ftree = f"{T}/ftree"
            os.makedirs(ftree)
            fakeimage.sample_tree(ftree, many=230)
            # three files that are deleted in the image (F4b): a photo and a text file that read back, and a photo the drive cleared
            from PIL import Image as _Img
            _Img.new("RGB", (240, 160), (30, 160, 60)).save(f"{ftree}/Photos/2019/old.png")
            open(f"{ftree}/Docs/old-notes.txt", "w").write("Widget Wars deleted notes\n" * 40)
            _Img.effect_noise((300, 200), 60).convert("RGB").save(f"{ftree}/Docs/lost.jpg", "JPEG", quality=85)
            # files that may be copies of each other (F4c), under names no other check looks for: a photo twice (the same bytes), and a
            # 2 MB file twice (the same name and size, different bytes: possible, not certain)
            os.makedirs(f"{ftree}/Copies/Sub")
            _Img.effect_noise((320, 240), 60).convert("RGB").save(f"{ftree}/Copies/zeta.jpg", "JPEG", quality=85)
            shutil.copy(f"{ftree}/Copies/zeta.jpg", f"{ftree}/Copies/Sub/zeta.jpg")
            open(f"{ftree}/Copies/zeta.dat", "wb").write(os.urandom(2 * MiB))
            open(f"{ftree}/Copies/Sub/zeta.dat", "wb").write(os.urandom(2 * MiB))
            fimg = f"{T}/fimg.img"
            fakeimage.ext4_image(fimg, ftree)
            fakeimage.ext4_delete(fimg, "/Photos/2019/old.png", offset_sectors=2048)
            fakeimage.ext4_delete(fimg, "/Docs/old-notes.txt", offset_sectors=2048)
            fakeimage.ext4_delete(fimg, "/Docs/lost.jpg", clear=True, offset_sectors=2048)
            frel = fakeimage.put_in_store(FD.store, "gauntlet-legends", "BROWSE-EXT", fimg)
            nfiles = sum(1 for d, _, fs in os.walk(ftree) for f in fs if not os.path.islink(os.path.join(d, f))) - 3     # the three deleted ones are not files

            def fb_row(name):
                return f"[...document.querySelectorAll('#dk-body .dk-frow')].find(b => b.querySelector('b').textContent === {json.dumps(name)})"

            def fb_tap(name):
                q(fb_row(name) + ".click()")

            def fb_wait_row(name, timeout=25):
                return settles(c, "!!(" + fb_row(name) + ")", timeout)

            def fb_btn(text, timeout=25, exact=False):
                # the sort buttons carry an arrow once on ("NAME ↑"), so a prefix match; ALL must be exact (ALL IMAGES starts with it)
                test = f"(b.textContent === {json.dumps(text)} || {'false' if exact else 'b.textContent.startsWith(' + json.dumps(text + ' ') + ')'})"
                finder = f"[...document.querySelectorAll('#dk-body button:not(.dk-frow)')].find(b => {test})"
                ok = settles(c, f"!!({finder})", timeout)
                if ok:
                    q(finder + ".click()")
                return ok
            dk_seg("FILES")
            check("FILES: the stored images are listed, with how it works, and nothing is changed",
                  settles(c, "[...document.querySelectorAll('#dk-body button.dk-recrow')].some(b => b.textContent.startsWith('BROWSE-EXT'))", 15)
                  and "Nothing is mounted and nothing is changed" in text("#dk-body"))
            q("[...document.querySelectorAll('#dk-body button.dk-recrow')].find(b => b.textContent.startsWith('BROWSE-EXT')).click()")
            check("an image: its partitions. The Linux volume says Ext4, its size and what is used; the swap says it is swap space",
                  dk_has("Ext4", 25) and "swap space" in text("#dk-body") and "used" in text("#dk-body") and "free" in text("#dk-body"))
            check("... a volume with a filesystem has OPEN and COUNT WHAT'S ON IT; one without has neither, and shows its first bytes",
                  q("[...document.querySelectorAll('#dk-body .dk-vol')].filter(v => v.textContent.includes('OPEN')).length") == 1
                  and "COUNT WHAT'S ON IT" in text("#dk-body") and "first bytes:" in text("#dk-body"))
            check("... no text under 14 px on the 7\"", not q(BOX_SMALL))
            shot("25a-files-volumes")
            fb_btn("COUNT WHAT'S ON IT")
            check("COUNT WHAT'S ON IT: counted in the background, then the files, folders and kinds are shown",
                  dk_has(f"{nfiles} files", 40) and "photos:" in text("#dk-body") and "documents:" in text("#dk-body") and "commonest:" in text("#dk-body"))
            shot("25b-files-counted")
            fb_btn("OPEN")
            check("OPEN: the root folder, its folders first, then the files, with a tag on each and a size and date",
                  fb_wait_row("Photos") and fb_wait_row("big.bin") and q("[...document.querySelectorAll('#dk-body .dk-frow')][0].textContent.includes('FOLDER')")
                  and "2 MB" in q(fb_row("big.bin") + ".textContent"))
            check("... the breadcrumbs are buttons: ALL IMAGES, the image, the volume", q("[...document.querySelectorAll('#dk-body .dk-crumbs button')].map(b => b.textContent)")[0] == "ALL IMAGES")
            check("the sort and the kind buttons sit side by side in a bar as tall as a finger, none of it clipped (dk-bar is the progress bar's class)",
                  q("""(() => { const b = document.querySelector('#dk-body .dk-sortkind'); if (!b) return false;
                         const r = b.getBoundingClientRect(), segs = [...b.querySelectorAll('.seg')].map(s => s.getBoundingClientRect());
                         return r.height >= 55 && b.scrollHeight <= b.clientHeight + 1 && segs.length === 2 && Math.abs(segs[0].top - segs[1].top) < 2
                                && getComputedStyle(b).backgroundColor === 'rgba(0, 0, 0, 0)'
                                && [...b.querySelectorAll('button')].every(x => { const g = document.createRange(); g.selectNodeContents(x); return g.getClientRects().length === 1; }); })()"""))
            shot("25c-files-folder")
            fb_btn("SIZE")
            first_file = "[...document.querySelectorAll('#dk-body .dk-frow')].filter(b => !b.textContent.includes('FOLDER'))[0]"
            check("SIZE: largest first (the list is redrawn when the new order arrives), folders still above the files",
                  settles(c, "!!(" + first_file + ") && " + first_file + ".querySelector('b').textContent === 'huge.pdf'", 15)
                  and q("document.querySelectorAll('#dk-body .dk-frow')[0].textContent.includes('FOLDER')")
                  and q("document.querySelector('#dk-body .dk-files .seg button.on').textContent.startsWith('SIZE')"))
            fb_btn("PHOTOS")
            check("PHOTOS: the folders as rows, the photos as a grid (the ones that cannot be drawn still show their names), and nothing else",
                  settles(c, "![...document.querySelectorAll('#dk-body .dk-frow')].some(b => b.querySelector('b').textContent === 'big.bin')", 15)
                  and settles(c, "[...document.querySelectorAll('#dk-body .dk-cn')].some(n => n.textContent === 'bomb.png')", 15)
                  and sorted(q("[...document.querySelectorAll('#dk-body .dk-cn')].map(n => n.textContent)")) == ["bomb.png", "broken.jpg"]
                  and q("[...document.querySelectorAll('#dk-body .dk-frow')].every(b => b.textContent.includes('FOLDER'))"))
            fb_btn("ALL", 15, exact=True)
            fb_btn("NAME", 15)
            check("ALL and NAME: everything back, folders first, by name", fb_wait_row("big.bin") and fb_wait_row("Photos"))
            fb_tap("Photos")
            check("a folder: tap it, and its folders and files are listed; the breadcrumbs grow", fb_wait_row("2019") and "Photos" in text("#dk-body .dk-crumbs"))
            fb_tap("2019")
            check("... down again, by inode", fb_wait_row("IMG_0001.jpg") and fb_wait_row("red.png"))
            fb_tap("IMG_0001.jpg")
            check("a photo: tap it, and the viewer opens on it, drawn by gatbox-browse; no search and no PDF link",
                  settles(c, "!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').complete && document.querySelector('#vw-img').naturalWidth > 0", 40)
                  and "/api/browse/preview" in q("document.querySelector('#vw-img').src") and q("document.querySelector('#vw-title').textContent") == "IMG_0001.jpg"
                  and q("document.querySelector('#vw-search').classList.contains('hide')") and q("document.querySelector('#vw-pdf').classList.contains('hide')"))
            shot("25d-files-photo")
            q("document.querySelector('#vw-close').click()")
            check("CLOSE: back on the same folder, where it was", fb_wait_row("red.png") and "2019" in text("#dk-body .dk-crumbs"))
            fb_btn("PHOTOS", 15, exact=True)
            imgs_js = "[...document.querySelectorAll('#dk-body .dk-cell img')]"
            check("PHOTOS in a folder of photos: a grid of thumbnails, each really drawn, the name under it, and no list rows for the photos",
                  settles(c, f"{imgs_js}.length === 2 && {imgs_js}.every(i => i.complete && i.naturalWidth > 0)", 40)
                  and sorted(q("[...document.querySelectorAll('#dk-body .dk-cn')].map(n => n.textContent)")) == ["IMG_0001.jpg", "red.png"]
                  and q("document.querySelectorAll('#dk-body .dk-frow').length") == 0)
            check("... the thumbnails are small (192 px at most), the cells as tall as a finger, no text under 14 px, nothing wider than the 7\"",
                  q(f"{imgs_js}.every(i => Math.max(i.naturalWidth, i.naturalHeight) <= 192)")
                  and q("[...document.querySelectorAll('#dk-body .dk-cell')].every(b => b.getBoundingClientRect().height >= 55.5)") and not q(BOX_SMALL)
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1"))
            shot("27a-files-grid")
            q("[...document.querySelectorAll('#dk-body .dk-cell')].find(b => b.querySelector('.dk-cn').textContent === 'IMG_0001.jpg').click()")
            check("a thumbnail opens the photo in the viewer, titled with its name, at 1 of 2 (the folder's photos are the pages)",
                  settles(c, "!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').complete && document.querySelector('#vw-img').naturalWidth > 0", 40)
                  and q("document.querySelector('#vw-title').textContent") == "IMG_0001.jpg" and q("document.querySelector('#vw-page').textContent") == "1 / 2"
                  and q("document.querySelector('#vw-prev').disabled") and not q("document.querySelector('#vw-next').disabled"))
            q("document.querySelector('#vw-next').click()")
            check("NEXT: the folder's other photo, and the title follows it",
                  settles(c, "document.querySelector('#vw-title').textContent === 'red.png' && document.querySelector('#vw-page').textContent === '2 / 2' && document.querySelector('#vw-img').complete", 40))
            q("document.querySelector('#vw-close').click()")
            check("CLOSE: the grid again", settles(c, f"{imgs_js}.length === 2", 15))
            # The owner's card, 2026-11-16: thumbnails showed for a frame and left empty boxes. The store's free space moves with every
            # thumbnail written to it, the 5 s poll redrew the tab when it did, and every redraw made new blank <img> elements.
            q(f"{imgs_js}.forEach(i => {{ i.dataset.kept = '1'; }}); window.__redraws = 0;"
              " new MutationObserver(m => { window.__redraws += m.length; }).observe(document.querySelector('#dk-body'), {childList: true}); true")
            import shutil as _sh
            bucket = lambda: _sh.disk_usage(FD.store).free // (256 * MiB)
            b0 = bucket()
            for i in range(7):                                         # small writes move the store's free space, as thumbnails do, over more than one poll
                with open(f"{FD.store}/_noise{i}", "wb") as nf:
                    nf.write(os.urandom(256 * 1024))
                time.sleep(1.1)
            moved = bucket() != b0                                     # (a 256 MB step crossed by chance: the redraw would be right)
            check("the store's free space moving a little (as a thumbnail written does) does not redraw the grid: no redraw over a 5 s poll, "
                  "the same thumbnails, still drawn", moved or (q("window.__redraws") == 0 and q(f"{imgs_js}.length") == 2
                  and q(f"{imgs_js}.every(i => i.dataset.kept === '1' && i.complete && i.naturalWidth > 0)")))
            for i in range(7):
                os.remove(f"{FD.store}/_noise{i}")
            # F4b: a deleted photo in the grid, and the warning that follows it through the viewer
            tog = "document.querySelector('#dk-body .dk-count button')"
            check("DELETED is off to begin with: two photos, the line says one deleted is not shown, the toggle is a finger tall and not pressed",
                  "1 deleted not shown" in text("#dk-body") and q(f"{tog}.getBoundingClientRect().height") >= 55.5 and q(f"{tog}.getAttribute('aria-pressed')") == "false" and q(f"{tog}.textContent") == "DELETED")
            # A disk job running (the owner's card, 2026-11-16: a READ had run for an hour, and no thumbnail had ever been made): gatbox-browse
            # makes none while one runs, imaging has priority. The grid keeps its boxes, says why, and draws them when the job ends; the
            # pictures already loaded stay. Nothing is cached, so the deleted photo's thumbnail has to be made.
            _sh.rmtree(f"{FD.store}/_browse/thumbs", ignore_errors=True)
            with open(f"{T}/dspool/running.json", "w") as rj:
                rj.write(json.dumps({"op": "read", "id": "waitjob"}))
            c.wait("document.querySelector('#n-disk-s').textContent === 'reading'", 20)
            q(tog + ".click()")
            note_js = "[...document.querySelectorAll('#dk-body .mut')].find(x => x.textContent.startsWith('A disk job is running, so thumbnails wait'))"
            check("a disk job running: the deleted photo's cell has a box that says DISK JOB RUNNING, the line says why, the two photos already "
                  "drawn stay, and the cells are still a finger tall",
                  settles(c, "document.querySelectorAll('#dk-body .dk-cell').length === 3 && document.querySelectorAll('#dk-body .dk-ph').length === 1", 30)
                  and text("#dk-body .dk-ph") == "DISK JOB RUNNING" and q(f"!!({note_js}) && !({note_js}).classList.contains('hide')")
                  and q("[...document.querySelectorAll('#dk-body .dk-cell:not(.gone) img')].every(i => i.complete && i.naturalWidth > 0)")
                  and q("[...document.querySelectorAll('#dk-body .dk-cell')].every(b => b.getBoundingClientRect().height >= 55.5)") and not q(BOX_SMALL))
            shot("27c-files-grid-waiting")
            os.remove(f"{T}/dspool/running.json")
            check("the job ends: the thumbnail is made and drawn, and the placeholder and the line go",
                  settles(c, "document.querySelectorAll('#dk-body .dk-ph').length === 0 && document.querySelectorAll('#dk-body .dk-cell img').length === 3 "
                             "&& [...document.querySelectorAll('#dk-body .dk-cell img')].every(i => i.complete && i.naturalWidth > 0)", 40)
                  and q(f"!({note_js}) || ({note_js}).classList.contains('hide')"))
            check("DELETED: the deleted photo joins the grid, dashed and tagged in words, the live ones carry no tag, the toggle shows it is on",
                  settles(c, "document.querySelectorAll('#dk-body .dk-cell').length === 3", 15)
                  and q("[...document.querySelectorAll('#dk-body .dk-cell.gone .dk-cn')].map(n => n.textContent)") == ["old.png"]
                  and q("[...document.querySelectorAll('#dk-body .dk-cell.gone .tag')].map(t => t.textContent)") == ["DELETED"]
                  and q("document.querySelectorAll('#dk-body .dk-cell:not(.gone) .tag').length") == 0
                  and q(f"{tog}.getAttribute('aria-pressed')") == "true" and "1 deleted, dashed" in text("#dk-body")
                  and q("getComputedStyle(document.querySelector('#dk-body .dk-cell.gone')).borderStyle") == "dashed")
            check("... its thumbnail is really drawn (the deleted file's data was still there), and nothing is under 14 px",
                  settles(c, "[...document.querySelectorAll('#dk-body .dk-cell.gone img')].every(i => i.complete && i.naturalWidth > 0)", 40) and not q(BOX_SMALL))
            shot("27b-files-deleted-grid")
            check("the grid was redrawn for the new cell, and the two live photos kept their own <img> elements (moved, still drawn), not new blank ones",
                  q("[...document.querySelectorAll('#dk-body .dk-cell:not(.gone) img')].filter(i => i.dataset.kept === '1' && i.complete && i.naturalWidth > 0).length") == 2
                  and q("window.__redraws") > 0)
            q("[...document.querySelectorAll('#dk-body .dk-cell')].find(b => b.querySelector('.dk-cn').textContent === 'IMG_0001.jpg').click()")
            note = "document.querySelector('#vw-note')"
            check("a live photo in a folder that also shows deleted ones: the viewer has no warning, and it is one of three",
                  settles(c, "!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').complete && document.querySelector('#vw-page').textContent === '1 / 3'", 40)
                  and q(f"{note}.classList.contains('hide')"))
            q("document.querySelector('#vw-next').click()")
            check("NEXT to the deleted photo: the title follows, and the warning is shown, in words, above the photo",
                  settles(c, "document.querySelector('#vw-title').textContent === 'old.png' && document.querySelector('#vw-img').complete", 40)
                  and not q(f"{note}.classList.contains('hide')") and q(f"{note}.textContent").startswith("DELETED.") and "another file's data" in q(f"{note}.textContent")
                  and q(f"parseFloat(getComputedStyle({note}).fontSize)") >= 14 and not q(BOX_SMALL))
            shot("27c-files-deleted-viewer")
            q("document.querySelector('#vw-next').click()")
            check("NEXT again to a live photo: the warning goes away with it",
                  settles(c, "document.querySelector('#vw-title').textContent === 'red.png'", 40) and q(f"{note}.classList.contains('hide')"))
            q("document.querySelector('#vw-close').click()")
            q(tog + ".click()")
            check("DELETED off again: the two live photos only, and the line says one is not shown",
                  settles(c, "document.querySelectorAll('#dk-body .dk-cell').length === 2 && document.querySelectorAll('#dk-body .dk-cell.gone').length === 0", 15)
                  and "1 deleted not shown" in text("#dk-body"))
            fb_btn("ALL", 15, exact=True)
            fb_wait_row("red.png")
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")      # the volume's crumb: the root again
            fb_wait_row("Docs")
            fb_tap("Docs")
            fb_wait_row("manual.pdf")
            fb_tap("manual.pdf")
            check("a PDF: the viewer opens with its page count, and NEXT draws page 2",
                  settles(c, "document.querySelector('#vw-page').textContent === '1 / 3' && document.querySelector('#vw-img').complete && document.querySelector('#vw-img').naturalWidth > 0", 40))
            q("document.querySelector('#vw-next').click()")
            check("... page 2 of 3", settles(c, "document.querySelector('#vw-page').textContent === '2 / 3' && document.querySelector('#vw-img').complete", 40))
            q("document.querySelector('#vw-close').click()")
            fb_tap("notes.txt")
            check("a text file: its text, in a block, with its encoding", dk_has("Widget Wars cabinet notes", 30) and "UTF-8" in text("#dk-body"))
            shot("25e-files-text")
            fb_btn("BACK TO THE FOLDER", 15)
            # F4b: deleted files in a folder of documents
            fb_wait_row("notes.txt")
            check("Docs, DELETED off: no deleted file is a row, and the line says two are not shown",
                  q("!(" + fb_row("old-notes.txt") + ") && !(" + fb_row("lost.jpg") + ")") and "2 deleted not shown" in text("#dk-body"))
            fb_btn("DELETED", 15, exact=True)
            check("DELETED on: both are rows, dashed, each tagged DELETED in words beside its kind, sorted in with the live ones",
                  fb_wait_row("old-notes.txt") and fb_wait_row("lost.jpg")
                  and q(fb_row("old-notes.txt") + ".classList.contains('gone')") and q(fb_row("lost.jpg") + ".classList.contains('gone')")
                  and "DELETED" in q(fb_row("old-notes.txt") + ".textContent") and "DELETED" in q(fb_row("lost.jpg") + ".textContent")
                  and not q(fb_row("notes.txt") + ".classList.contains('gone')") and "DELETED" not in q(fb_row("notes.txt") + ".textContent")
                  and q("[...document.querySelectorAll('#dk-body .dk-frow b')].map(b => b.textContent)") == ["budget.xlsx", "lost.jpg", "manual.pdf", "notes.txt", "old-notes.txt"])
            fb_tap("old-notes.txt")
            check("a deleted text file: the warning first, then what could be read back, and the same BACK",
                  dk_has("Widget Wars deleted notes", 30) and "DELETED. Read from space the drive has marked free" in text("#dk-body .dk-gonenote")
                  and "another file's data" in text("#dk-body .dk-gonenote") and not q(BOX_SMALL))
            shot("27d-files-deleted-text")
            fb_btn("BACK TO THE FOLDER", 15)
            fb_tap("lost.jpg")
            check("a deleted photo the drive cleared (size 0): the warning, and a plain sentence that nothing is left, not a blank page",
                  dk_has("Nothing to read back", 30) and "DELETED. Read from space" in text("#dk-body .dk-gonenote"))
            fb_btn("BACK TO THE FOLDER", 15)
            fb_btn("DELETED", 15, exact=True)
            check("DELETED off again: the folder is as it was", settles(c, "!(" + fb_row("old-notes.txt") + ") && !(" + fb_row("lost.jpg") + ")", 15)
                  and "2 deleted not shown" in text("#dk-body"))
            # an entry whose inode another file now holds: its name only, never read, never drawn
            open(f"{T}/flsbin/ghost", "w").close()
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")           # the volume's crumb: the root again
            fb_wait_row("empty")
            fb_tap("empty")
            check("a folder whose only entry is deleted and reused: nothing without DELETED, and the line says one is not shown",
                  settles(c, "document.querySelector('#dk-body .dk-none') !== null", 15) and "1 deleted not shown" in text("#dk-body"))
            fb_btn("DELETED", 15, exact=True)
            check("DELETED: the row is tagged DELETED, NAME ONLY, and says in words that only the name is left",
                  fb_wait_row("ghost.png") and "DELETED, NAME ONLY" in q(fb_row("ghost.png") + ".textContent")
                  and "only the name is left" in q(fb_row("ghost.png") + ".textContent"))
            fb_tap("ghost.png")
            check("tapping it opens nothing: a notice says why, and the folder stays",
                  settles(c, "!document.querySelector('#toast').classList.contains('hide') && document.querySelector('#toast').textContent.includes('only the name is left')", 10)
                  and q("document.querySelector('#viewer').classList.contains('hide')") and "empty" in text("#dk-body .dk-crumbs") and q("!!(" + fb_row("ghost.png") + ")"))
            fb_btn("PHOTOS", 15, exact=True)
            check("in the grid: a cell with the name and NAME ONLY, and no thumbnail is asked for (it would draw the other file's picture)",
                  settles(c, "document.querySelectorAll('#dk-body .dk-cell').length === 1", 15)
                  and "NAME ONLY" in text("#dk-body .dk-cell") and q("[...document.querySelectorAll('#dk-body .dk-cell img')].every(i => !i.getAttribute('src'))"))
            q("document.querySelector('#dk-body .dk-cell').click()")
            check("... and tapping the cell opens no viewer", q("document.querySelector('#viewer').classList.contains('hide')"))
            os.remove(f"{T}/flsbin/ghost")
            fb_btn("ALL", 15, exact=True)
            fb_btn("DELETED", 15, exact=True)
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")
            fb_wait_row("Docs")
            fb_tap("Docs")
            fb_wait_row("manual.pdf")
            fb_tap("budget.xlsx")
            check("a file with no preview says so, in words", dk_has("no preview for this kind of file", 30))
            fb_btn("BACK TO THE FOLDER", 15)
            # F4d: COPY TO THE STORE (the only write in FILES: into the store's _exports folder, after a question, with a progress strip)
            exports = f"{FD.store}/_exports"
            fb_tap("notes.txt")
            dk_has("Widget Wars cabinet notes", 30)
            fb_btn("COPY TO THE STORE", 15, exact=True)
            ask = "#dk-body .dk-ask"
            check("a file: COPY TO THE STORE asks first, in words (what, from which image, into _exports, a new folder, nothing overwritten, read back with its SHA-1), and nothing is copied yet",
                  settles(c, f"!!document.querySelector('{ask}')", 10) and 'Copy "notes.txt" to the store?' in text(ask) and "BROWSE-EXT" in text(ask)
                  and "_exports" in text(ask) and "nothing already there is overwritten" in text(ask) and "SHA-1" in text(ask) and not os.path.exists(exports))
            check("... the question's text is 14 px or more and its buttons are a finger tall", not q(BOX_SMALL)
                  and q(f"[...document.querySelectorAll('{ask} button')].every(b => b.getBoundingClientRect().height >= 55.5)") and q(f"document.querySelector('{ask}').scrollWidth <= document.querySelector('{ask}').clientWidth + 1"))
            shot("29a-files-copy-ask")
            fb_btn("CANCEL", 15, exact=True)
            check("CANCEL: the question goes, and nothing was copied (no _exports folder at all)", settles(c, f"!document.querySelector('{ask}')", 10) and not os.path.exists(exports))
            fb_btn("COPY TO THE STORE", 15, exact=True)
            fb_btn("COPY", 15, exact=True)
            strip = "#dk-body .dk-exp"
            check("COPY: it says COPIED, with the count and size, where it is (a folder under the store's _exports) and that each file was read back",
                  settles(c, f"!!document.querySelector('{strip}') && document.querySelector('{strip}').textContent.includes('COPIED')", 40)
                  and 'COPIED "notes.txt": 1 file' in text(strip) and "read back from the store" in text(strip) and "_exports" in text(strip) and "_export.json" in text(strip))
            done = sorted(n for n in os.listdir(exports) if not n.endswith(".partial"))
            check("... the copy is on the store, byte for byte, with its list: the SHA-1 of the file, and the image it came from",
                  len(done) == 1 and open(f"{exports}/{done[0]}/notes.txt", "rb").read() == b"Widget Wars cabinet notes\nline two\n"
                  and json.load(open(f"{exports}/{done[0]}/_export.json"))["files"][0]["sha1"] == __import__("hashlib").sha1(b"Widget Wars cabinet notes\nline two\n").hexdigest()
                  and json.load(open(f"{exports}/{done[0]}/_export.json"))["image"]["label"] == "BROWSE-EXT")
            shot("29b-files-copy-done")
            fb_btn("BACK TO THE FOLDER", 15)
            check("the strip stays on screen in the folder view until it is dismissed, and OK dismisses it",
                  settles(c, f"!!document.querySelector('{strip}')", 10) and fb_btn("OK", 15, exact=True) and settles(c, f"!document.querySelector('{strip}')", 10))
            fb_btn("COPY FOLDER", 15, exact=True)
            check("a folder: COPY FOLDER asks about the folder and says every live file in it is copied",
                  settles(c, f"!!document.querySelector('{ask}')", 10) and 'Copy the folder "Docs" to the store?' in text(ask) and "Every live file in it is copied" in text(ask))
            fb_btn("COPY", 15, exact=True)
            check("... COPIED, the three live files (the deleted ones are not copied)",
                  settles(c, f"!!document.querySelector('{strip}') && document.querySelector('{strip}').textContent.includes('COPIED')", 60) and 'COPIED "Docs": 3 files' in text(strip))
            done2 = sorted(n for n in os.listdir(exports) if not n.endswith(".partial") and n not in done)
            m2 = json.load(open(f"{exports}/{done2[0]}/_export.json"))
            check("... the folder's copy: exactly budget.xlsx, manual.pdf and notes.txt; no deleted file; recovered is false",
                  sorted(r_["path"] for r_ in m2["files"]) == ["budget.xlsx", "manual.pdf", "notes.txt"] and m2["recovered"] is False)
            fb_btn("OK", 15, exact=True)
            fb_btn("DELETED", 15, exact=True)
            fb_wait_row("old-notes.txt")
            fb_tap("old-notes.txt")
            dk_has("Widget Wars deleted notes", 30)
            fb_btn("COPY TO THE STORE", 15, exact=True)
            check("a deleted file: the question carries the warning and says the copy is marked as a recovery",
                  settles(c, f"!!document.querySelector('{ask}')", 10) and "DELETED. Read from space the drive has marked free" in text(ask) and "marked as a recovery" in text(ask))
            fb_btn("COPY", 15, exact=True)
            check("... COPIED, and the strip repeats that it is a recovery",
                  settles(c, f"!!document.querySelector('{strip}') && document.querySelector('{strip}').textContent.includes('COPIED')", 40)
                  and "marked as a recovery" in text(strip))
            done3 = sorted(n for n in os.listdir(exports) if not n.endswith(".partial") and n not in done + done2)
            m3 = json.load(open(f"{exports}/{done3[0]}/_export.json"))
            check("... the copy is the deleted file's bytes, and its list says recovered, on the file and on the copy",
                  open(f"{exports}/{done3[0]}/old-notes.txt", "rb").read() == b"Widget Wars deleted notes\n" * 40 and m3["recovered"] is True and m3["files"][0]["deleted"] is True)
            os.makedirs(f"{exports}/20260101-000000_STALE_halfway.partial")            # a copy that was cut off, for the list below
            fb_btn("OK", 15, exact=True)
            earlier = ".dk-earlier"
            check("EARLIER COPIES: with copies on the store there is a button that counts them (three finished, one cut off), and the list is closed",
                  settles(c, f"!!document.querySelector('{earlier}') && document.querySelector('{earlier}').textContent.includes('EARLIER COPIES (4)')", 15)
                  and not q("!!document.querySelector('.dk-eplist')"))
            fb_btn("EARLIER COPIES (4)", 15, exact=True)
            check("... tapped: every copy has its name, its file count and size, and its folder under _exports; the copies are listed newest first",
                  settles(c, "!!document.querySelector('.dk-eplist')", 10)
                  and all(s_ in text(earlier) for s_ in ('old-notes.txt', 'Docs', 'notes.txt', '3 files', '1 file', f"_exports/{done[0]}", f"_exports/{done2[0]}", f"_exports/{done3[0]}"))
                  and text(earlier).index("old-notes.txt") < text(earlier).index("Docs") < text(earlier).index("_exports/" + done[0]))
            check("... the cut-off copy says so, and that what is in it is partial; nothing in the list can delete or open a copy",
                  "UNFINISHED COPY" in text(earlier) and "20260101-000000_STALE_halfway.partial" in text(earlier) and "partial" in text(earlier)
                  and not q("[...document.querySelectorAll('.dk-earlier button')].some(b => /DELETE|REMOVE|OPEN/i.test(b.textContent))"))
            check("... on the 7\": no text under 14 px, and the button is a finger tall", not q(BOX_SMALL)
                  and q("document.querySelector('.dk-earlier button').getBoundingClientRect().height") >= 55.5)
            shot("29c-files-earlier-copies")
            fb_btn("HIDE EARLIER COPIES", 15, exact=True)
            check("HIDE EARLIER COPIES: the list closes and the button counts again", settles(c, "!document.querySelector('.dk-eplist')", 10)
                  and "EARLIER COPIES (4)" in text(earlier))
            os.rmdir(f"{exports}/20260101-000000_STALE_halfway.partial")
            fb_btn("BACK TO THE FOLDER", 15)
            fb_btn("DELETED", 15, exact=True)
            # a disk job holds it back: the question is asked, the answer is a notice, and nothing is made
            open(f"{T}/dspool/request.json", "w").write("{}")
            fb_tap("notes.txt")
            dk_has("Widget Wars cabinet notes", 30)
            n_before = len(os.listdir(exports))
            fb_btn("COPY TO THE STORE", 15, exact=True)
            fb_btn("COPY", 15, exact=True)
            check("with a disk job queued: a notice says copying waits for it, and no folder is made",
                  settles(c, "!document.querySelector('#toast').classList.contains('hide') && document.querySelector('#toast').textContent.includes('a disk job is running')", 10)
                  and len(os.listdir(exports)) == n_before and not q("!!document.querySelector('#dk-body .dk-exp')"))
            os.remove(f"{T}/dspool/request.json")
            fb_btn("BACK TO THE FOLDER", 15)
            # a photo in the viewer has COPY too: it closes the viewer and asks on the screen behind
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")
            fb_wait_row("Photos")
            fb_tap("Photos")
            fb_wait_row("2019")
            fb_tap("2019")
            fb_wait_row("IMG_0001.jpg")
            fb_tap("IMG_0001.jpg")
            settles(c, "!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').complete && document.querySelector('#vw-img').naturalWidth > 0", 40)
            check("the photo viewer has a COPY button, a finger tall", not q("document.querySelector('#vw-copy').classList.contains('hide')")
                  and q("document.querySelector('#vw-copy').getBoundingClientRect().height") >= 55.5)
            q("document.querySelector('#vw-copy').click()")
            check("COPY in the viewer closes it and asks, on the screen behind, about that photo",
                  q("document.querySelector('#viewer').classList.contains('hide')") and settles(c, f"!!document.querySelector('{ask}')", 10)
                  and 'Copy "IMG_0001.jpg" to the store?' in text(ask))
            fb_btn("CANCEL", 15, exact=True)
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")
            fb_wait_row("Docs")
            fb_tap("Docs")
            fb_wait_row("manual.pdf")
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][2].click()")
            fb_wait_row("huge.pdf")
            fb_tap("huge.pdf")
            check("a file over the 64 MB limit: 'too large to preview', with its size", dk_has("too large to preview", 30) and "70 MB" in text("#dk-body"))
            shot("25f-files-too-large")
            fb_btn("BACK TO THE FOLDER", 15)
            fb_tap("bomb.png")
            check("a photo with too many pixels: refused in words, not drawn", dk_has("too large to draw", 30))
            fb_btn("BACK TO THE FOLDER", 15)
            fb_tap("broken.jpg")
            check("a damaged photo: 'could not be read'", dk_has("could not be read", 30))
            fb_btn("BACK TO THE FOLDER", 15)
            check("... and BACK still works: the folder is where it was", fb_wait_row("broken.jpg"))
            fb_tap("Notes")
            check("a folder of 232 items is paged: 'page 1 of 2', NEXT gives the rest",
                  dk_has("page 1 of 2", 25) and q("document.querySelectorAll('#dk-body .dk-frow').length") == 200)
            fb_btn("NEXT", 15)
            check("... page 2: the other 32", dk_has("page 2 of 2", 25) and q("document.querySelectorAll('#dk-body .dk-frow').length") == 32)
            check("rows are at least 56 px tall for a finger, and no text is under 14 px on the 7\"",
                  q("[...document.querySelectorAll('#dk-body .dk-frow')].every(b => b.getBoundingClientRect().height >= 55.5)") and not q(BOX_SMALL))
            shot("25g-files-paged")

            # the name index and search (F2)
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][0].click()")             # ALL IMAGES
            check("ALL IMAGES: each image shows its index state: not indexed, and what a tap does; SEARCH and INDEX ALL are there",
                  settles(c, "[...document.querySelectorAll('#dk-body button.dk-idx')].some(b => b.textContent.includes('NOT INDEXED'))", 15)
                  and "tap to index" in text("#dk-body") and q("[...document.querySelectorAll('#dk-body .btnrow button')].map(b => b.textContent)").count("SEARCH") == 1
                  and "INDEX ALL" in text("#dk-body"))
            shot("26a-files-images-index")
            q("document.querySelector('#dk-body button.dk-idx').click()")
            check("tap to index: it counts while it reads the list, then says INDEXED with the number of files",
                  settles(c, "[...document.querySelectorAll('#dk-body button.dk-idx')].some(b => /INDEXED \\d+ files/.test(b.textContent))", 60)
                  and "tap to remove" in text("#dk-body"))
            check("... the image's own row still opens it", q("[...document.querySelectorAll('#dk-body button.dk-recrow')].some(b => b.textContent.startsWith('BROWSE-EXT'))"))
            check("TEXT: the documents were read on their own after the names (a short job): READ n of m, and how many could not be read",
                  settles(c, "[...document.querySelectorAll('#dk-body button.dk-txt')].some(b => /TEXT READ \\d+ of \\d+/.test(b.textContent))", 60)
                  and "could not be read" in text("#dk-body"))
            shot("26a2-files-text-read")
            fb_btn("SEARCH", 15, exact=True)
            c.wait("document.querySelector('#pane .kp-field')", 10)
            tap_keys("IMG")
            check("SEARCH: the keypad asks for words, and the results come: the photo with its path, size, date and image",
                  dk_has("IMG_0001.jpg", 30) and "Photos/2019/" in text("#dk-body") and "BROWSE-EXT" in text("#dk-body") and "1 result" in text("#dk-body"))
            check("... it says how much it searched (1 of the store's 3 images) and what to do about the rest", "1 result · searched 1 of 3 images" in text("#dk-body")
                  and "INDEX it on ALL IMAGES" in text("#dk-body") and q("document.querySelectorAll('#dk-body .dk-hit').length") == 1)
            check("... a year chip comes from the matches", q("[...document.querySelectorAll('#dk-body .dk-years button')].some(b => /^\\d{4} · 1$/.test(b.textContent))"))
            check("... rows and buttons are as tall as a finger, no text under 14 px, and nothing is wider than the 7\"",
                  q("[...document.querySelectorAll('#dk-body .dk-hit button')].every(b => b.getBoundingClientRect().height >= 55.5)") and not q(BOX_SMALL)
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1"))
            shot("26b-files-search")
            fb_btn("INSIDE DOCUMENTS", 15, exact=True)
            check("INSIDE DOCUMENTS: IMG is a file's name, not in any document, so no results, and the line says how much was read and what could not be, with the reasons",
                  dk_has("No results", 20) and re.search(r"read \d+ of \d+ documents in 1 image", text("#dk-body")) and "could not be read (" in text("#dk-body")
                  and "not read yet" not in text("#dk-body") and not q("[...document.querySelectorAll('#dk-body .seg button')].some(b => b.textContent === 'PHOTOS')"))
            q("document.querySelector('#dk-body .dk-q').click()")
            c.wait("document.querySelector('#pane .kp-field')", 10)
            tap_keys("⌫⌫⌫WIDGET")
            check("a word inside a document finds it: the name, the folder, a snippet with the match marked, and the image",
                  dk_has("notes.txt", 30) and q("document.querySelector('#dk-body .dk-hit .dk-snip .hl').textContent.toLowerCase()") == "widget"
                  and "Docs/" in text("#dk-body .dk-hit") and "BROWSE-EXT" in text("#dk-body .dk-hit"))
            check("... rows and buttons as tall as a finger, no text under 14 px, nothing wider than the 7\"",
                  q("[...document.querySelectorAll('#dk-body .dk-hit button')].every(b => b.getBoundingClientRect().height >= 55.5)") and not q(BOX_SMALL)
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1"))
            shot("26e-files-search-text")
            fb_btn("NAMES", 15, exact=True)
            q("document.querySelector('#dk-body .dk-q').click()")
            c.wait("document.querySelector('#pane .kp-field')", 10)
            tap_keys("⌫⌫⌫⌫⌫⌫IMG")
            dk_has("IMG_0001.jpg", 20)
            fb_btn("PHOTOS", 15, exact=True)
            check("a kind chip narrows: PHOTOS keeps it, DOCS finds none and says so",
                  dk_has("IMG_0001.jpg", 15))
            fb_btn("DOCS", 15, exact=True)
            check("... DOCS: no results", dk_has("No results", 15))
            fb_btn("ALL", 15, exact=True)
            dk_has("IMG_0001.jpg", 15)
            q("document.querySelector('#dk-body .dk-hit .dk-where').click()")
            check("SHOW IN FOLDER: the folder the photo is in (ALL IMAGES / the image / the volume / Photos / 2019), with the row marked",
                  settles(c, "!!document.querySelector('#dk-body .dk-crumbs') && !!document.querySelector('#dk-body .dk-frow.hit')", 20)
                  and "2019" in text("#dk-body .dk-crumbs")
                  and q("[...document.querySelectorAll('#dk-body .dk-frow.hit')].map(b => b.querySelector('b').textContent)") == ["IMG_0001.jpg"])
            shot("26c-files-show-in-folder")
            q("[...document.querySelectorAll('#dk-body .dk-crumbs button')][1].click()")
            check("the image's crumb after SHOW IN FOLDER still opens its volumes", dk_has("COUNT WHAT'S ON IT", 20) or dk_has("OPEN", 20))
            fb_btn("ALL IMAGES", 15, exact=True)
            fb_btn("SEARCH", 15, exact=True)
            check("SEARCH again: the words are kept, the results are back", dk_has("FIND: IMG", 20) and dk_has("IMG_0001.jpg", 20))
            q("document.querySelector('#dk-body .dk-hit button.dk-frow').click()")
            check("a result's own tap opens the file as from a folder: the photo viewer; CLOSE lands on the results",
                  settles(c, "!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').complete && document.querySelector('#vw-img').naturalWidth > 0", 40))
            q("document.querySelector('#vw-close').click()")
            check("... the results, not the folder", dk_has("FIND: IMG", 15) and q("!!document.querySelector('#dk-body .dk-hit')"))
            fb_btn("ALL IMAGES", 15, exact=True)
            # F4c: possible duplicates, from the names in the index (this image is indexed here; two others in the store are not)
            check("ALL IMAGES has a DUPLICATES button beside SEARCH", q("[...document.querySelectorAll('#dk-body .btnrow button')].map(b => b.textContent)").count("DUPLICATES") == 1)
            fb_btn("DUPLICATES", 15, exact=True)
            check("DUPLICATES: it says what it is, in words: the same name and size, nothing read to list them, so only POSSIBLE, that COMPARE reads a group, and that nothing here deletes",
                  dk_has("Possible duplicates", 25) and "only possible duplicates" in text("#dk-body") and "Nothing is read to list them" in text("#dk-body")
                  and "COMPARE reads one group's copies" in text("#dk-body")
                  and "a copy under another name is not found" in text("#dk-body") and "Nothing here can delete anything" in text("#dk-body"))
            fb_btn("4 KB UP", 15, exact=True)
            gh = "[...document.querySelectorAll('#dk-body .dk-gh b')].map(b => b.textContent)"
            check("4 KB UP: the two groups, each with its name, tag, how many copies, the size of each and the space the extras take",
                  settles(c, f"{gh}.length === 2", 20) and sorted(q(gh)) == ["zeta.dat", "zeta.jpg"]
                  and "2 copies" in text("#dk-body .dk-gh") and "extra" in text("#dk-body .dk-gh"))
            check("... the line says how many groups, how much is in the extra copies, and that only 1 of 3 images was compared and why the others were not",
                  "2 groups" in text("#dk-body") and "in extra copies" in text("#dk-body") and "compared 1 of 3 images" in text("#dk-body")
                  and "INDEX it on ALL IMAGES" in text("#dk-body"))
            check("... each group lists where its copies are (path, image, volume), and a big file and a photo are not mixed up",
                  sorted(q("[...document.querySelectorAll('#dk-body .dk-mem .dk-path')].map(p => p.textContent)")) == ["Copies/Sub/zeta.dat", "Copies/Sub/zeta.jpg", "Copies/zeta.dat", "Copies/zeta.jpg"]
                  and "BROWSE-EXT" in text("#dk-body .dk-mem"))
            check("... on the 7\": no text under 14 px, a place is as tall as a finger, nothing wider than the screen, and each filter label is on one line",
                  not q(BOX_SMALL) and q("[...document.querySelectorAll('#dk-body button.dk-mem')].every(b => b.getBoundingClientRect().height >= 55.5)")
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1")
                  and q("""[...document.querySelectorAll('#dk-body .seg button')].every(x => { const g = document.createRange(); g.selectNodeContents(x); return g.getClientRects().length === 1; })"""))
            shot("28a-files-duplicates")
            # COMPARE: the tap that settles a possible duplicate by reading the copies and checking their SHA-1
            cmp_btns = "[...document.querySelectorAll('#dk-body button.dk-cmp')]"
            box_ = "#dk-body .dk-cmpbox"
            check("COMPARE: every group has a button, a finger tall, and says what it does (reads every copy in full and checks its SHA-1)",
                  q(f"{cmp_btns}.length") == 2 and q(f"{cmp_btns}.every(b => b.getBoundingClientRect().height >= 55.5 && !b.disabled)")
                  and "Reads every copy in full and checks its SHA-1" in text("#dk-body") and not q(BOX_SMALL))
            open(f"{T}/dspool/request.json", "w").write("{}")
            q("(() => { const g = [...document.querySelectorAll('#dk-body .dk-gh')].find(x => x.textContent.includes('zeta.jpg')); let n = g.nextElementSibling; while (n && !n.querySelector('button.dk-cmp')) n = n.nextElementSibling; n.querySelector('button.dk-cmp').click(); })()")
            check("with a disk job queued: a notice says comparing waits for it, and nothing starts",
                  settles(c, "!document.querySelector('#toast').classList.contains('hide') && document.querySelector('#toast').textContent.includes('a disk job is running')", 10)
                  and not q(f"!!document.querySelector('{box_}')"))
            os.remove(f"{T}/dspool/request.json")
            q("(() => { const g = [...document.querySelectorAll('#dk-body .dk-gh')].find(x => x.textContent.includes('zeta.jpg')); let n = g.nextElementSibling; while (n && !n.querySelector('button.dk-cmp')) n = n.nextElementSibling; n.querySelector('button.dk-cmp').click(); })()")
            check("the photo's two copies: THE SAME FILE, with the SHA-1 each row shows (the same 8 characters), and the other group's button is still there",
                  settles(c, f"!!document.querySelector('{box_}') && document.querySelector('{box_}').textContent.includes('THE SAME FILE')", 40)
                  and "all 2 copies" in text(box_) and len(set(q("[...document.querySelectorAll('#dk-body .dk-mem')].map(m => (m.textContent.match(/SHA-1 ([0-9a-f]{8})/) || [])[1]).filter(Boolean)"))) == 1
                  and len(q("[...document.querySelectorAll('#dk-body .dk-mem')].map(m => (m.textContent.match(/SHA-1 ([0-9a-f]{8})/) || [])[1]).filter(Boolean)")) == 2
                  and q(f"{cmp_btns}.length") == 1)
            check("... on the 7\": no text under 14 px, OK is a finger tall, nothing wider than the screen",
                  not q(BOX_SMALL) and q(f"document.querySelector('{box_} button').getBoundingClientRect().height") >= 55.5
                  and q("document.querySelector('#dk-body').scrollWidth <= document.querySelector('#dk-body').clientWidth + 1"))
            shot("28b-files-compare-same")
            q(f"{cmp_btns}[0].click()")
            check("the big file's two copies (a second comparison replaces the first, which goes back to a button): NOT THE SAME, two different files, each row in its own set",
                  settles(c, f"!!document.querySelector('{box_}') && document.querySelector('{box_}').textContent.includes('NOT THE SAME')", 60)
                  and "2 different files share this name and size" in text(box_)
                  and sorted(q("[...document.querySelectorAll('#dk-body .dk-mem')].map(m => (m.textContent.match(/SET ([AB])/) || [])[1]).filter(Boolean)")) == ["A", "B"]
                  and q(f"{cmp_btns}.length") == 1)
            shot("28c-files-compare-different")
            q(f"document.querySelector('{box_} button').click()")
            check("OK puts the answer away: the group has its COMPARE button again, and the rows lose their SHA-1 lines",
                  settles(c, f"!document.querySelector('{box_}') && {cmp_btns}.length === 2", 10) and "SHA-1 " not in text("#dk-body .dk-mem"))
            fb_btn("PHOTOS", 15, exact=True)
            check("PHOTOS: only the photo's group", settles(c, f"{gh}.length === 1 && {gh}[0] === 'zeta.jpg'", 20))
            fb_btn("OTHER", 15, exact=True)
            check("OTHER: only the big file's group", settles(c, f"{gh}.length === 1 && {gh}[0] === 'zeta.dat'", 20))
            fb_btn("DOCS", 15, exact=True)
            check("DOCS: no groups, said plainly", settles(c, f"{gh}.length === 0 && document.querySelector('#dk-body').textContent.includes('No groups')", 20))
            fb_btn("ALL", 15, exact=True)
            check("ALL again: both groups are back", settles(c, f"{gh}.length === 2", 20))
            fb_btn("10 MB UP", 15, exact=True)
            check("10 MB UP: nothing is that big, and it says so", settles(c, f"{gh}.length === 0 && document.querySelector('#dk-body').textContent.includes('No groups')", 20))
            fb_btn("1 MB UP", 15, exact=True)
            check("1 MB UP: the 2 MB file only (the photo is far under 1 MB)", settles(c, f"{gh}.length === 1 && {gh}[0] === 'zeta.dat'", 20))
            q("[...document.querySelectorAll('#dk-body button.dk-mem')].find(b => b.querySelector('.dk-path').textContent === 'Copies/Sub/zeta.dat').click()")
            check("a place is a SHOW IN FOLDER: the folder it is in (Copies / Sub), on the page that holds it, with the row marked",
                  settles(c, "!![...document.querySelectorAll('#dk-body .dk-frow.hit')].find(b => b.querySelector('b').textContent === 'zeta.dat')", 25)
                  and "Copies" in text("#dk-body .dk-crumbs") and "Sub" in text("#dk-body .dk-crumbs"))
            fb_btn("ALL IMAGES", 15, exact=True)
            q("document.querySelector('#dk-body button.dk-idx').click()")
            check("tap to remove: this image's names and its text are gone (the first row, not just any: the others were never indexed)",
                  settles(c, "document.querySelector('#dk-body button.dk-idx').textContent.includes('NOT INDEXED') "
                             "&& document.querySelector('#dk-body button.dk-txt').textContent.includes('index the names first')", 30))
            fb_btn("SEARCH", 15, exact=True)
            check("... and a search says 0 of 3 searched, with the reason, not 'nothing there'", dk_has("searched 0 of 3 images", 20) and "is not searched" in text("#dk-body"))
            shot("26d-files-search-not-indexed")
            # the image store unplugged: FILES says so
            FD.mount_table(store=False)
            check("with the store unplugged: FILES says the images are on the store", dk_has("plug it in", 25))
            FD.mount_table()
            dk_has("Inside an image", 25) or dk_seg("FILES")
        FD.mount_table(store=False)
        dk_seg("READ")
        check("the store unplugged: NOT CONNECTED, READ off and it says why, the nav says so",
              dk_has("NOT CONNECTED", 15) and q("document.querySelector('#dk-body button.dk-go').disabled")
              and "waits for the image store" in text("#dk-body") and text("#n-disk-s") == "no store")
        shot("23i-disk-no-store")
        FD.mount_table()
        c.wait("document.querySelector('#dk-body .dk-store .badge').textContent === 'CONNECTED'", 15)
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            c.open(f"http://{lan}:{PORT}/dash/?box=0", 390, 844)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
            click('[data-view="disk"]')
            c.wait("document.querySelector('#dk-body').textContent.includes('Drives on USB')", 15)
            settles(c, "document.querySelectorAll('#dk-body .dk-tick').length === 10", 10)
            time.sleep(0.5)
            shot("24-disk-phone")
            check("from a phone: the state (the last job's verdict), the drives, the images and the checklist; no READ, WRITE, FORMAT or hold",
                  not q("document.querySelector('#dk-body .seg')") and not q("document.querySelector('#dk-body button.hold')")
                  and q("document.querySelectorAll('#dk-body button.dk-tick').length") == 10
                  and "READ, WRITE and FORMAT are on the Pi's own screen" in text("#dk-body")
                  and ("FORMAT CHECK PASSED" if shutil.which("mkfs.fat", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin") else "VERIFIED") in text("#dk-body")
                  and not q("[...document.querySelectorAll('#dk-body .dk-drives button')].length"))
            q("[...document.querySelectorAll('#dk-body button.dk-tick')].find(b => b.textContent.includes('version matches')).click()")
            check("from a phone: a tap ticks a checklist line, and gatbox-web keeps it (the owner's 2026-11-03 answer)",
                  settles(c, "document.querySelectorAll('#dk-body .dk-tick.done').length === 2", 10)
                  and sorted(i["item"] for i in api("GET", "/api/disk/checklist")["items"] if i["done"]) == ["five_volts", "version_match"])
            check("from a phone: no horizontal scroll, and nothing runs past a card's edge",
                  q("document.documentElement.scrollWidth <= 391")
                  and q("[...document.querySelectorAll('#dk-body .card')].every(c => c.scrollWidth <= c.clientWidth + 1)"))
            q("[...document.querySelectorAll('#dk-body button')].find(b => b.textContent === 'DRIVE INFO').click()")
            check("from a phone: DRIVE INFO opens the drives' cards, without horizontal scroll",
                  settles(c, "!!document.querySelector('#dk-body .dk-infocard')", 5) and q("document.documentElement.scrollWidth <= 391")
                  and q("[...document.querySelectorAll('#dk-body .card')].every(c => c.scrollWidth <= c.clientWidth + 1)"))
            q("[...document.querySelectorAll('#dk-body button.dk-recrow')].find(b => b.textContent.startsWith('WW-HDD')).click()")
            check("from a phone: a tap on an image opens its job record, with PRINT OR SAVE AS PDF (the 7\" has none)",
                  settles(c, "!!document.querySelector('#dk-body .dk-rec')", 10)
                  and q("[...document.querySelectorAll('#dk-body button')].some(b => b.textContent === 'PRINT OR SAVE AS PDF')")
                  and q("document.documentElement.scrollWidth <= 391")
                  and q("[...document.querySelectorAll('#dk-body .dk-rec')].every(c => c.scrollWidth <= c.clientWidth + 1)"))
            q("document.querySelector('#dk-body .dk-rec').scrollIntoView()")
            time.sleep(0.5)
            shot("24b-disk-record-phone")
            # the print view: only the record, black on white, a rule where the deck has no value
            c.send("Emulation.setEmulatedMedia", {"media": "print"})
            q("document.body.classList.add('dk-printing')")
            check("the print view keeps only the record: no header, no nav, no other card, no buttons; black on white",
                  q("getComputedStyle(document.querySelector('header')).display") == "none"
                  and q("getComputedStyle(document.querySelector('nav')).display") == "none"
                  and q("[...document.querySelectorAll('#dk-body > *:not(.dk-rec)')].every(e => getComputedStyle(e).display === 'none')")
                  and q("getComputedStyle(document.querySelector('#dk-body .dk-rec')).display") != "none"
                  and q("getComputedStyle(document.querySelector('#dk-body .dk-noprint')).display") == "none"
                  and q("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
                  and q("getComputedStyle(document.body).color") == "rgb(0, 0, 0)")
            check("... and a value the deck doesn't have is a blank rule to write on, not text",
                  q("getComputedStyle(document.querySelector('#dk-body .dk-blank')).color") == "rgba(0, 0, 0, 0)"
                  and q("getComputedStyle(document.querySelector('#dk-body .dk-blank')).borderBottomStyle") == "solid")
            c.send("Emulation.setEmulatedMedia", {"media": ""})
            q("document.body.classList.remove('dk-printing')")
            c.open(B + "/dash/", 1024, 600)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        else:
            print("  skip  the phone's DISK view (no LAN address here)")
        dk_done.set()
        c.close_others()      # the pages the sections so far opened each still hold a live stream: six is all one host gets

        print("the 7\" box (html.box: the kiosk opens the dashboard at 127.0.0.1):")
        check("box: the kiosk's page is in box mode", q("document.documentElement.classList.contains('box')"))
        bad_frame, small = {}, {}
        for v in ("meter", "sessions", "machine", "library", "system", "devices", "dump", "disk"):
            click(f'[data-view="{v}"]')
            time.sleep(1.2)
            f, s = q(BOX_FRAME), q(BOX_SMALL)
            if f:
                bad_frame[v] = f
            if s:
                small[v] = s
        for d in (bad_frame, small):
            if d:
                print("    ", d)
        check("box: every view sits inside the bezel margin (20 px at the sides, 16 at top and bottom)", not bad_frame)
        check("box: no text under 14 px, header and nav included", not small)
        check("box: the eight tabs fit down the side, the nav doesn't scroll",
              q("(() => { const n = document.querySelector('nav'); return n.scrollHeight <= n.clientHeight + 1; })()"))
        # The kiosk has no back button: a link that leaves the dashboard strands it there.
        leaving = {}
        for v in ("meter", "sessions", "machine", "library", "system", "devices", "dump", "disk"):
            click(f'[data-view="{v}"]')
            time.sleep(0.8)
            if v == "sessions":                       # a session's report is open, as it is when you read one
                q("(() => { const r = document.querySelector('#s-list .row'); if (r) r.click(); return true; })()")
                time.sleep(1.5)
            a = q("[...document.querySelectorAll('a[href]')].filter(a => a.getClientRects().length).map(a => a.textContent)")
            if a:
                leaving[v] = a
        if leaving:
            print("    ", leaving)
        check("box: no link that leaves the dashboard (the kiosk has no way back)", not leaving)
        click('[data-view="meter"]')
        time.sleep(0.5)
        check("box: METER's action buttons say all of it (nothing cut off), and METER doesn't scroll",
              q("[...document.querySelectorAll('.act small')].every(s => s.scrollWidth <= s.clientWidth + 1 && s.scrollHeight <= s.clientHeight + 1)")
              and q("(() => { const v = document.querySelector('#v-meter'); return v.scrollHeight <= v.clientHeight + 1; })()"))
        click('[data-view="library"]')
        c.wait("[...document.querySelectorAll('#v-library h2')].some(h => h.textContent === 'Spec sheet')", 15)
        check("box: LIBRARY keeps the spec sheet's explanation behind HOW THIS WORKS",
              "The manual's numbers as" not in q("[...document.querySelectorAll('#v-library *')].filter(e => e.getClientRects().length"
                                                 " && e.children.length === 0).map(e => e.textContent).join(' ')")
              and q("[...document.querySelectorAll('#v-library button')].some(b => b.textContent.includes('HOW THIS WORKS'))"))
        click('[data-view="devices"]')
        time.sleep(1)
        check("box: DEVICES says where the T48 is used (no milestone talk)", "M7" not in text("#v-devices")
              and "DUMP" in text("#v-devices"))
        click('[data-view="dump"]')
        time.sleep(1)
        check("box: DUMP's header is one short line", "armed by a hold" not in text("#du-body"))
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-labels').disabled", 10)
        click("#mc-labels")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelectorAll('#pane .row').length >= 3", 10)
        check("box: LABEL LIST has no text box (the 7\" has no keyboard for one), it has SEARCH",
              not q("!!document.querySelector('#pane input')")
              and q("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'SEARCH')"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SEARCH').click()")
        c.wait("document.querySelector('#pane .keys')")
        tap_keys("LEG")
        c.wait("!document.querySelector('#pane .keys') && document.querySelectorAll('#pane .row').length >= 1", 10)
        check("box: LABEL LIST: SEARCH is the keyboard, and it filters", q("document.querySelectorAll('#pane .row').length") == 1
              and "gauntlet-legends" in text("#pane"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")
        # TOUCH TEST: is there a steady offset between where a finger aims and where the touch lands? Nine crosses; here
        # every tap lands 12 px low and 3 px right of its centre, the way a fingertip's contact patch would.
        click('[data-view="system"]')
        c.wait("document.querySelector('#sy-touch')", 10)
        check("box: SYSTEM has TOUCH TEST", q("document.querySelector('#sy-touch').getClientRects().length > 0"))
        click("#sy-touch")
        c.wait("document.querySelector('#tt:not(.hide) .tt-target')", 5)
        for _ in range(9):
            q("(() => { const t = document.querySelector('#tt .tt-target'), r = t.getBoundingClientRect();"
              " const x = r.left + r.width / 2 + 3, y = r.top + r.height / 2 + 12;"
              " document.querySelector('#tt').dispatchEvent(new PointerEvent('pointerdown',"
              " {clientX: x, clientY: y, bubbles: true, pointerType: 'touch'})); return true; })()")
            time.sleep(0.15)
        c.wait("document.querySelector('#tt .tt-result')", 5)
        res = text("#tt .tt-result")
        check("box: TOUCH TEST: nine crosses, and the verdict names the offset (12 px below, 3 px right)",
              "12 px below" in res and "3 px right" in res)
        shot("22-touch-test")
        tt = api("GET", "/api/touchtest")
        check("box: TOUCH TEST keeps the run for a calibration (GET /api/touchtest)",
              tt["runs"][-1]["n"] == 9 and abs(tt["runs"][-1]["dy"] - 12) < 0.6 and abs(tt["runs"][-1]["dx"] - 3) < 0.6)
        q("[...document.querySelectorAll('#tt button')].find(b => b.textContent === 'DONE').click()")
        check("box: DONE closes it", q("document.querySelector('#tt').classList.contains('hide')"))
        # A toast lies over the page for seconds: it must never be what a finger hits.
        q("(() => { const t = document.querySelector('#toast'); t.textContent = 'test'; t.classList.remove('hide'); })()")
        tst = q("(() => { const t = document.querySelector('#toast'), r = t.getBoundingClientRect();"
                " return {pe: getComputedStyle(t).pointerEvents, l: r.left, t: r.top, r: r.right, b: r.bottom}; })()")
        q("document.querySelector('#toast').classList.add('hide')")
        check("box: a toast never takes a tap, and sits inside the margin", tst["pe"] == "none" and tst["l"] >= 19.5
              and tst["t"] >= 15.5 and tst["r"] <= 1004.5 and tst["b"] <= 584.5)

        print("LIBRARY after the seed (the games):")
        penv = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=f"{T}/ctrl", GATBOX_MANUALS=f"{T}/manuals", GATBOX_ROMS=f"{T}/roms",
                    GATBOX_MAME_ROMS=f"{T}/mame-roms.json", GATBOX_LIBRARY=f"{T}/lib/library.sqlite")
        for step in (("seed", "--plan", "--out", f"{T}/lib-plan.json"), ("seed", "--apply", f"{T}/lib-plan.json")):
            subprocess.run([sys.executable, os.path.join(REPO, "tools/gatbox-library"), *step], env=penv, check=True, capture_output=True)
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})

        def lib_has(t, timeout=15):
            return settles(c, f"document.querySelector('#lb-body').textContent.includes({json.dumps(t)})", timeout)

        def lib_tap(label, sel="#lb-body button"):
            q(f"[...document.querySelectorAll({json.dumps(sel)})].find(b => b.textContent === {json.dumps(label)} || b.textContent.startsWith({json.dumps(label)})).click()")

        def type_text(txt, save=True):
            for ch in txt:
                label = "SPACE" if ch == " " else ch
                q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(label)}).click()")
            if save:
                q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAVE').click()")

        def pane_tap(label):
            c.wait(f"[...document.querySelectorAll('#pane button')].some(b => b.textContent === {json.dumps(label)})", 10)
            q(f"[...document.querySelectorAll('#pane button')].find(b => b.textContent === {json.dumps(label)}).click()")

        click('[data-view="meter"]')
        time.sleep(0.5)
        click('[data-view="library"]')
        check("seeded: the bar shows ALL GAMES (3) and MISSING (2), the machine's game with its key and its manual",
              lib_has("ALL GAMES (3)") and "MISSING (2)" in text("#lb-body") and "key gauntlet-legends" in text("#lb-body")
              and "1 manual" in text("#lb-body") and "Documents (1)" in text("#lb-body") and "Spec sheet" in text("#lb-body"))
        check("... the spec sheet still answers as before: the manual's limit, its page, CONFIRM / CONFIRMED, actuals for the machine",
              "4.75–5.25 V" in text("#lb-body") and "SET ACTUAL" in text("#lb-body"))
        shot("25a-library-seeded")
        # READ LIMITS (the owner, 2026-11-17): what the manuals job read off the manual, proposed until she confirms it on its page
        check("READ LIMITS is on the spec sheet (the Pi's own screen)", "READ LIMITS" in text("#lb-body"))
        json.dump({"doc_id": "gl-manual", "pages": 3, "ocr_pages": 1, "notes": [], "candidates": [
            {"rail": "+12V", "lo": 11.4, "hi": 12.6, "page": 2, "quote": "+12 VDC 11.4 to 12.6 V", "how": "ocr"}]},
                  open(f"{T}/manuals/gauntlet-legends/gl-manual.limits.json", "w"))
        click('[data-view="meter"]')
        click('[data-view="library"]')
        check("a limit read off the manual shows apart from the sheet, with its page, its words and READ BY OCR",
              lib_has("Read off the manuals (1)") and all(s_ in text("#lb-body .found") for s_ in (
                  "+12V", "11.4–12.6 V", "p.2 ▸", "READ BY OCR: CHECK THE NUMBERS", "+12 VDC 11.4 to 12.6 V", "NOT THIS")))
        shot("25a2-read-limits")
        q("[...document.querySelectorAll('#lb-body .found button')].find(b => b.textContent === 'CONFIRM').click()")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .pagebox img')", 10)
        check("CONFIRM shows the page first, and says OCR can misread a digit", "OCR, which can misread a digit" in text("#pane"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CONFIRM').click()")
        check("confirmed: gone from the proposals, on the sheet as +12V ✓ CONFIRMED, and the logger's window has it",
              settles(c, "!document.querySelector('#lb-body .found') && document.querySelector('#lb-body').textContent.includes('11.4–12.6 V · the manual, confirmed')", 15)
              and api("GET", "/api/specs/gauntlet-legends")["rails"][-1]["window"] == [11.4, 12.6])
        api("DELETE", "/api/specs/gauntlet-legends/confirm", {"rail": "+12V"})
        check("the game's page lists the drive images read for its machine (the owner, 2026-11-16: 'No disk image.'), each with its size and date",
              lib_has("Drive images (") and "Drive images (0)" not in text("#lb-body") and "WW-HDD" in text("#lb-body") and "Tap one to look inside it" in text("#lb-body"))
        q("[...document.querySelectorAll('#lb-body button.row')].find(b => b.textContent.startsWith('WW-HDD')).click()")
        check("... a tap opens that image in DISK → FILES", settles(c, "document.querySelector('#v-disk').classList.contains('on') && document.querySelector('#dk-body').textContent.includes('WW-HDD')", 20))
        click('[data-view="library"]')
        lib_has("ALL GAMES (3)")
        lib_tap("ALL GAMES")
        c.wait("document.querySelectorAll('#pane .picklist .row').length >= 3", 10)
        names = q("[...document.querySelectorAll('#pane .picklist .row')].map(r => r.textContent)")
        check("ALL GAMES: every game, by title, with what it has: 'NO MANUAL' for the ones without (the added machine's game too)",
              len(names) == 3 and names[0].startswith("Gauntlet") and "NO MANUAL" in names[0] and "1 manual" in names[1]
              and names[2].startswith("Zaxxon") and "NO MANUAL" in names[2])
        shot("25b-library-games")
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('Gauntlet') && r.textContent.includes('NO MANUAL')).click()")
        check("picking a game opens it: its title, no manual, and the missing reason line (none noted here)",
              lib_has("key gauntlet ·") and "NO MANUAL" in text("#lb-body") and "Nothing read off the manuals yet." in text("#lb-body"))
        lib_tap("MISSING")
        c.wait("document.querySelectorAll('#pane .picklist .row').length >= 2", 10)
        check("MISSING: only the games with no manual, each with why", q("document.querySelectorAll('#pane .picklist .row').length") == 2
              and "no reason noted" in text("#pane") and "Gauntlet Legends" not in text("#pane"))
        shot("25c-library-missing")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")

        lib_tap("+ ADD GAME")
        c.wait("document.querySelector('#pane .kp-field')")
        type_text("SPROCKET WARS")
        pane_tap("VIDEO")
        c.wait("document.querySelector('#pane .kp-field')")
        type_text("COGCO")
        c.wait("document.querySelector('#pane.num')")
        type_text("1984")
        c.wait("document.querySelector('#pane').textContent.includes('Key: sprocket-wars')", 10)
        check("ADD GAME: title, kind, maker, year, then the key it would get, with the words that it's permanent",
              "permanent ID" in text("#pane") and "never changes" in text("#pane"))
        shot("25d-library-add-key")
        pane_tap("ADD IT")
        check("... added: the game opens, with its key, its maker and year, no manual and no machine",
              lib_has("key sprocket-wars") and "SPROCKET WARS" in text("#lb-body") and "COGCO 1984" in text("#lb-body")
              and "no machine" in text("#lb-body") and "ALL GAMES (4)" in text("#lb-body"))
        check("... a game with no machine has a spec sheet and documents, and no actual-value controls (those are per machine)",
              "Nothing read off the manuals yet." in text("#lb-body") and "None here yet." in text("#lb-body")
              and "SET ACTUAL" not in text("#lb-body") and "+ ACTUAL" not in text("#lb-body"))
        shot("25e-library-added")
        lib_tap("EDIT GAME")
        pane_tap("TITLE")
        c.wait("document.querySelector('#pane .kp-field')")
        type_text("S")
        check("EDIT GAME: the title changes, the key never does",
              lib_has("SPROCKET WARSS") and "key sprocket-wars" in text("#lb-body")
              and api("GET", "/api/library/games/sprocket-wars")["title"] == "SPROCKET WARSS")
        check("SET MAME SET is offered for a game made without a MAME set (the audit, 2026-11-17: carnevil had no way to get one)",
              "SET MAME SET" in text("#lb-body") and " · MAME " not in text("#lb-body .lb-game"))
        bad_frame, small, short = {}, {}, {}
        for v in ("library",):
            f, sm = q(BOX_FRAME), q(BOX_SMALL)
            if f:
                bad_frame[v] = f
            if sm:
                small[v] = sm
        short = q("[...document.querySelectorAll('#lb-body .lb-bar-btn, #lb-body .btnrow button')].filter(b => !b.classList.contains('mini') && "
                  "!b.classList.contains('cite') && b.getClientRects().length && b.getBoundingClientRect().height < 55).map(b => b.textContent)")
        check("box: LIBRARY sits inside the bezel, no text under 14 px, every bar button is a full 56 px target",
              not bad_frame and not small and not short)
        if bad_frame or small or short:
            print("    ", bad_frame, small, short)
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            c.open(f"http://{lan}:{PORT}/dash/?box=0", 390, 844)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
            click('[data-view="library"]')
            lib_has("ALL GAMES (4)")
            time.sleep(0.5)
            shot("26-library-phone")
            check("from a phone: the games and MISSING read; no ADD GAME, no EDIT GAME, no FIND A MANUAL; no horizontal scroll",
                  "MISSING (" in text("#lb-body") and "+ ADD GAME" not in text("#lb-body") and "EDIT GAME" not in text("#lb-body")
                  and "FIND A MANUAL" not in text("#lb-body") and "SET MAME SET" not in text("#lb-body")
                  and q("document.documentElement.scrollWidth <= 391"))
            c.open(B + "/dash/", 1024, 600)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        else:
            print("  skip  the phone's LIBRARY view (no LAN address here)")

        print("FIND GAME (MAME's title list) and linking a machine to a game:")
        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        for nm in ("Pacman Cabinet", "Second Pacman"):
            api("POST", "/api/roster", {"name": nm, "mfr": "Namco", "kind": "video_games", "platform": "test_hdd"})
        api("PUT", "/api/machine", {"slug": "pacman-cabinet"})
        click('[data-view="library"]')
        check("a machine with no game: LIBRARY says so, in words, and offers FIND GAME (MAME) first",
              lib_has("Pacman Cabinet has no game in the library yet") and "FIND GAME (MAME)" in text("#lb-body")
              and q("[...document.querySelectorAll('#lb-body .btnrow button')].map(b => b.textContent).includes('FIND GAME (MAME)')"))
        shot("29a-library-no-game")
        lib_tap("FIND GAME")
        c.wait("document.querySelector('#pane .kp-field')")
        for _ in range(len("Pacman Cabinet")):
            q("[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === '⌫').click()")
        type_text("PAC MAN")
        c.wait("document.querySelectorAll('#pane .picklist .row').length >= 1", 10)
        row = q("document.querySelector('#pane .picklist .row').textContent")
        check("the keypad starts with the machine's name; the search finds MAME's Pac-Man, with its maker, year and set",
              row.startswith("Pac-Man") and "set pacman" in row and "1980" in row)
        shot("29b-library-find-game")
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#pane').textContent.includes('Its key will be pac-man')", 10)
        pane_tap("ADD IT AND USE IT")
        check("ADD IT AND USE IT: the game is made, with its key, and the machine uses it",
              lib_has("key pac-man") and "machines: Pacman Cabinet" in text("#lb-body")
              and [m["slug"] for m in api("GET", "/api/library/games/pac-man")["machines"]] == ["pacman-cabinet"]
              and api("GET", "/api/library/games/pac-man")["mame"] == "pacman")
        shot("29c-library-found")
        api("PUT", "/api/machine", {"slug": "second-pacman"})
        click('[data-view="meter"]')
        time.sleep(0.5)
        click('[data-view="library"]')
        lib_has("Second Pacman has no game in the library yet")
        lib_tap("ALL GAMES")
        c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.startsWith('Pac-Man'))", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('Pac-Man')).click()")
        c.wait("document.querySelector('#pane').textContent.includes('has no game yet')", 10)
        shot("29d-library-use-it")
        pane_tap("USE IT FOR SECOND PACMAN")
        check("ALL GAMES for a machine with no game: it can use the one picked, and says so",
              lib_has("machines: Pacman Cabinet, Second Pacman")
              and sorted(m["slug"] for m in api("GET", "/api/library/games/pac-man")["machines"]) == ["pacman-cabinet", "second-pacman"])
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})

        print("FIND A MANUAL (archive.org, through the manuals service):")
        archive = start_archive(8102, word="Gauntlet")
        mstop = threading.Event()

        def manuals_runner():                                  # gatbox-manuals.path
            while not mstop.is_set():
                if os.path.exists(f"{T}/mspool/request.json"):
                    time.sleep(1.0)
                    subprocess.run([sys.executable, os.path.join(REPO, "tools/gatbox-manuals"), "--job", f"{T}/mspool/request.json",
                                    "--status", f"{T}/mspool/status.json"], capture_output=True, timeout=120, check=False,
                                   env=dict(os.environ, GATBOX_MANUALS=f"{T}/manuals", GATBOX_ARCHIVE="http://127.0.0.1:8102",
                                            GATBOX_MANUAL_HOSTS="127.0.0.1", GATBOX_DATA=f"{T}/data"))
                time.sleep(0.2)
        threading.Thread(target=manuals_runner, daemon=True).start()
        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        click('[data-view="library"]')
        lib_has("ALL GAMES (")
        lib_tap("ALL GAMES")
        c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.startsWith('Gauntlet') && r.textContent.includes('NO MANUAL'))", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('Gauntlet') && r.textContent.includes('NO MANUAL')).click()")
        missing_before = int(re.search(r"MISSING \((\d+)\)", text("#lb-body")).group(1))
        check("a game with no manual: FIND A MANUAL is offered, and is the main button",
              lib_has("key gauntlet ·") and q("[...document.querySelectorAll('#lb-body .btnrow button')].some(b => b.textContent === 'FIND A MANUAL' && b.classList.contains('primary'))"))
        shot("30a-library-find-manual")
        lib_tap("FIND A MANUAL")
        c.wait("document.querySelector('#pane .kp-field')")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAVE').click()")
        c.wait("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.startsWith('Widget Pinball Manual'))", 30)
        rows = q("[...document.querySelectorAll('#pane .picklist .row')].map(r => r.textContent)")
        check("the search starts with the game's title; archive.org's items come back, with their identifiers and download counts",
              len(rows) == 2 and "widget-pinball-manual" in rows[0] and "90 downloads" in rows[0])
        shot("30b-library-manual-items")
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('Widget Pinball Manual')).click()")
        c.wait("document.querySelector('#pane').textContent.includes('Which PDF?')", 30)
        files = q("[...document.querySelectorAll('#pane .picklist .row')].map(r => r.textContent)")
        check("the item's PDFs, biggest first, with their sizes", files[0].startswith("Widget Pinball_text.pdf") and "0.0 MB" in files[0]
              and files[1].startswith("Widget Pinball.pdf"))
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.startsWith('Widget Pinball.pdf')).click()")
        c.wait("document.querySelector('#pane').textContent.includes('What is it?')", 10)
        shot("30c-library-manual-kind")
        pane_tap("MANUAL")
        left = lambda: int(re.search(r"MISSING \((\d+)\)", text("#lb-body")).group(1))
        check("the manual downloads, and becomes the game's document: Documents (1), and the game stops being missing (the "
              "adoption into the library reaches the screen through the live feed)",
              lib_has("Documents (1)", 40) and "Widget Pinball Manual" in text("#lb-body")
              and settles(c, "!document.querySelector('#lb-body').textContent.includes('NO MANUAL')", 20) and left() == missing_before - 1)
        shot("30d-library-manual-fetched")
        mstop.set()
        archive.shutdown()

        print("CHECK: a dump against MAME, the game's chips, her images and the machine's earlier dumps:")
        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        click('[data-view="machine"]')
        c.wait("!document.querySelector('#mc-labels').disabled", 10)
        c.wait("[...document.querySelectorAll('#mc-body button.fold')].some(b => b.textContent.includes('ROM CHIPS'))", 15)
        open_fold("ROM CHIPS")
        c.wait("[...document.querySelectorAll('#mc-body button')].some(b => b.textContent === 'CHECK ▸')", 15)
        q("[...document.querySelectorAll('#mc-body button')].find(b => b.textContent === 'CHECK ▸').click()")
        c.wait("document.querySelector('#pane').textContent.includes('THIS MACHINE')", 10)
        pane_text = text("#pane")
        check("CHECK from the chip list: the four comparisons, each a word and its explanation",
              all(w in pane_text for w in ("AGAINST MAME", "THE GAME'S CHIPS", "YOUR IMAGES", "THIS MACHINE'S EARLIER DUMPS", "MATCH", "RIGHT CHIP",
                                           "legend15.u10 of Gauntlet Legends", "NEVER SEEN", "FIRST DUMP", "the first time LEGEND15-U10 has been dumped")))
        check("... each result has a level class, and the marks are on the 7\"",
              q("document.querySelectorAll('#pane .ck-row .badge.ok').length") == 2 and q("document.querySelectorAll('#pane .ck-row .badge.mut').length") == 2
              and q("[...document.querySelectorAll('#pane button')].map(b => b.textContent).filter(t => ['KNOWN GOOD', 'KNOWN BAD', 'UNMARK'].includes(t)).length") == 3)
        shot("27a-check")
        pane_tap("KNOWN GOOD")
        c.wait("document.querySelector('#pane .kp-field')")
        type_text("ROM SET")
        c.wait("document.querySelector('#pane .ck-row .badge.ok') && document.querySelectorAll('#pane .ck-row .badge.ok').length === 3 "
               "&& document.querySelector('#pane').textContent.includes('ROM SET')", 15)
        check("MARK KNOWN GOOD with a note: CHECK reopens and says so, in the words you typed",
              "KNOWN GOOD" in text("#pane") and "ROM SET" in text("#pane") and q("document.querySelectorAll('#pane .ck-row .badge.ok').length") == 3)
        shot("27b-check-good")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")
        click('[data-view="library"]')
        check("LIBRARY: the game's ROM images, with the mark on it",
              lib_has("ROM images (1)") and "KNOWN GOOD" in text("#lb-body") and "ROM SET" in text("#lb-body"))
        # two loads at once (the owner's Time Crisis II page, 2026-11-16: "ROM images (0)" twice): tap LIBRARY twice, quickly
        q("document.querySelector('[data-view=\"library\"]').click(); document.querySelector('[data-view=\"library\"]').click()")
        lib_has("ROM images (1)")
        time.sleep(1.5)
        check("LIBRARY tapped twice while loading: each card is drawn once, not twice",
              q("document.querySelector('#lb-body').textContent.split('ROM images (').length - 1") == 1
              and q("document.querySelector('#lb-body').textContent.split('Drive images (').length - 1") <= 1)
        q("document.querySelector('#lb-body').lastElementChild.scrollIntoView({block: 'end'})")
        shot("27c-library-images")
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            c.open(f"http://{lan}:{PORT}/dash/?box=0", 390, 844)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
            click('[data-view="library"]')
            lib_has("ROM images (1)")
            q("[...document.querySelectorAll('#lb-body button.row')].find(b => b.textContent.includes('LEGEND15-U10')).click()")
            c.wait("document.querySelector('#pane').textContent.includes('AGAINST MAME')", 10)
            check("from a phone: CHECK reads, the marks aren't offered",
                  "KNOWN GOOD" in text("#pane") and not q("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'UNMARK' || b.textContent === 'KNOWN BAD')"))
            shot("28-check-phone")
            c.open(B + "/dash/", 1024, 600)
            c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)

    with Chrome(f"{T}/browser3") as c:     # a fresh browser: the one above has a tab for every open, each with its live feed
        print("CONFLICTS: the deck and the archive both changed a record, and the sync's numbers (D4):")
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})
        recs = api("GET", "/api/library/sync/changes?since=0&limit=500")["records"]
        gl = next(r for r in recs if r["kind"] == "game" and r["id"] == "gauntlet-legends")
        lim = next(r for r in recs if r["kind"] == "rail_limit" and r["data"]["model_key"] == "gauntlet-legends")
        theirs = dict(gl, rev=gl["rev"] + 2, hash="c" * 64, data=dict(gl["data"], mfr="Partner Maker", year=1998), by="partner@example.test", at=1790000000.0)
        base = {"s": "a" * 64, "data": dict(gl["data"], mfr="Original Maker", year=1998)}
        os.makedirs(f"{T}/sync/library", exist_ok=True)
        json.dump({"conflicts": [
            {"key": "game|gauntlet-legends", "why": "conflict", "since": 1790000100, "deck": gl, "archive": theirs, "base": base},
            {"key": f"rail_limit|{lim['id']}", "why": "conflict", "since": 1790000200, "deck": lim,
             "archive": dict(lim, rev=lim["rev"] + 1, hash="d" * 64, deleted=True, by="partner@example.test"),
             "base": {"s": "e" * 64, "data": dict(lim["data"], quote="the older words from the page")}}]}, open(f"{T}/sync/library/conflicts.json", "w"))
        st_path = f"{T}/sync/status.json"
        st0 = json.load(open(st_path)) if os.path.exists(st_path) else {"at": time.time(), "ok": True}
        json.dump(dict(st0, library={"at": time.time() - 120, "ok": True, "pending": 0, "identical": False}), open(st_path, "w"))
        # a hub machine whose key reads like a game the library has (the owner, 2026-11-17): FROM THE HUBS asks
        br = f"{T}/sync/hubs/bravo/roster.json"
        br0 = json.load(open(br))
        json.dump({"machines": br0["machines"] + [{"slug": "s-0043-the-gauntlet-legends", "name": "The Gauntlet Legends #2",
                                                   "model_key": "the-gauntlet-legends"}]}, open(br, "w"))
        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.length > 0", 15)
        q = lambda js: c.eval(js)                                  # noqa: E731
        text = lambda sel: q(f"(document.querySelector({json.dumps(sel)}) || {{}}).textContent || ''")   # noqa: E731
        click = lambda sel: q(f"document.querySelector({json.dumps(sel)}).click()")  # noqa: E731
        shot = lambda n: c.shot(os.path.join(OUT, n + ".png"))     # noqa: E731
        click('[data-view="library"]')
        check("the bar offers CONFLICTS (2), lit like MISSING, and the machine's game carries a CONFLICT ×2 badge",
              lib_has("CONFLICTS (2)") and q("[...document.querySelectorAll('#lb-body .btnrow button')].some(b => b.textContent === 'CONFLICTS (2)' && b.classList.contains('warn'))")
              and lib_has("CONFLICT ×2"))
        shot("31a-conflicts-bar")
        check("FROM THE HUBS (1): a hub's key that may be a game the library has, lit like MISSING",
              q("[...document.querySelectorAll('#lb-body .btnrow button')].some(b => b.textContent === 'FROM THE HUBS (1)' && b.classList.contains('warn'))"))
        lib_tap("FROM THE HUBS")
        c.wait("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'SAME AS Gauntlet Legends')", 10)
        check("... it names the key, the machine and its hub, the game it may be, and offers SAME AS and ADD AS NEW",
              all(x in text("#pane") for x in ("the-gauntlet-legends", "The Gauntlet Legends #2 (BRAVO)", "Gauntlet Legends (key gauntlet-legends)", "ADD AS NEW")))
        shot("31a2-from-the-hubs")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'SAME AS Gauntlet Legends').click()")
        check("SAME AS: the hub's key now stands for Gauntlet Legends, and the button goes",
              settles(c, "document.querySelector('#toast').textContent.includes('the-gauntlet-legends is now Gauntlet Legends')", 10)
              and settles(c, "![...document.querySelectorAll('#lb-body .btnrow button')].some(b => b.textContent.startsWith('FROM THE HUBS'))", 10)
              and api("GET", "/api/library/hubgames")["waiting"] == [])
        json.dump(br0, open(br, "w"))
        lib_tap("ALL GAMES")
        c.wait("document.querySelectorAll('#pane .picklist .row').length >= 3", 10)
        check("ALL GAMES says CONFLICT ×2 on the game, in words", q("[...document.querySelectorAll('#pane .picklist .row')].some(r => r.textContent.startsWith('Gauntlet') && r.textContent.includes('CONFLICT ×2'))"))
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")
        lib_tap("CONFLICTS")
        c.wait("document.querySelector('#lb-body').textContent.includes('Conflicts (2)')", 10)
        rows = q("[...document.querySelectorAll('#lb-body button.row')].map(r => r.textContent)")
        check("the list: each conflict by its game and what it is (GAME, LIMIT), with what differs, and the removal in words",
              len(rows) == 2 and rows[0].startswith("Gauntlet Legends GAME") and "MAKER" in rows[0] and "differ" in rows[0]
              and rows[1].startswith("Gauntlet Legends: +5V limit LIMIT") and "the archive removed it; this deck changed it" in rows[1])
        shot("31b-conflicts-list")
        q("[...document.querySelectorAll('#lb-body button.row')][0].click()")
        c.wait("document.querySelector('#sheet').classList.contains('hide') === false && (document.querySelector('#pane h2') || {}).textContent === 'CONFLICT · Gauntlet Legends'", 10)
        ptxt = text("#pane")
        check("a conflict: BEFORE / THIS DECK / THE ARCHIVE, the maker's three values, who changed it from what, and the actions",
              all(w in ptxt for w in ("BEFORE", "THIS DECK", "THE ARCHIVE", "MAKER", "Original Maker", "Partner Maker", "partner@example.test", "CHANGED"))
              and all(b in [x for x in q("[...document.querySelectorAll('#pane button')].map(b => b.textContent)")]
                      for b in ("KEEP THIS DECK'S", "KEEP THE ARCHIVE'S", "EDIT THIS DECK'S")))
        check("the fields that differ are lit on both sides; a field they agree on (the title) isn't shown",
              q("document.querySelectorAll('#pane td.cf-lit').length") >= 4 and "TITLE" not in ptxt)
        check("box: each action is a full 56 px target, no text under 14 px, nothing wider than the panel",
              not q("[...document.querySelectorAll('#pane button')].filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 55).map(b => b.textContent)")
              and not q("[...document.querySelectorAll('#pane .cf-t td, #pane .cf-t th, #pane .cf-tag, #pane .mut')].filter(e => parseFloat(getComputedStyle(e).fontSize) < 14).map(e => e.textContent)")
              and q("document.querySelector('#pane').scrollWidth <= document.querySelector('#pane').clientWidth + 1"))
        shot("31c-conflict-game")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")
        q("[...document.querySelectorAll('#lb-body button.row')][1].click()")
        c.wait("document.querySelector('#sheet').classList.contains('hide') === false && (document.querySelector('#pane h2') || {}).textContent === 'CONFLICT · Gauntlet Legends: +5V limit'", 10)
        check("an edit against a removal: the archive's side says REMOVED in words, only what this deck changed is listed, and a limit is kept "
              "either way but not EDITed here",
              "THE ARCHIVE REMOVED IT." in text("#pane") and "(removed)" in text("#pane") and "QUOTE" in text("#pane") and "RAIL" not in text("#pane")
              and "EDIT THIS DECK'S" not in q("[...document.querySelectorAll('#pane button')].map(b => b.textContent)"))
        shot("31d-conflict-removed")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CLOSE').click()")
        q("[...document.querySelectorAll('#lb-body button.row')][0].click()")
        c.wait("document.querySelector('#sheet').classList.contains('hide') === false && (document.querySelector('#pane h2') || {}).textContent === 'CONFLICT · Gauntlet Legends'", 10)
        pane_tap("KEEP THIS DECK'S")
        if not settles(c, "document.querySelector('#lb-body').textContent.includes('KEEPING THIS DECK')", 10):
            print("    toast:", text("#toast"), "| resolutions:", api("GET", "/api/library/sync/resolutions"))
        res = api("GET", "/api/library/sync/resolutions")["resolutions"]
        check("KEEP THIS DECK'S: noted for the sync with the two states she saw, shown on the list as chosen and applied at the next sync",
              len(res) == 1 and res[0]["key"] == "game|gauntlet-legends" and res[0]["keep"] == "deck" and res[0]["archive"] == "c" * 64
              and res[0]["deck"] == gl["hash"] and "APPLIES AT THE NEXT SYNC" in text("#lb-body"))
        shot("31e-conflict-chosen")
        q("[...document.querySelectorAll('#lb-body button.row')][0].click()")
        c.wait("document.querySelector('#sheet').classList.contains('hide') === false && (document.querySelector('#pane h2') || {}).textContent === 'CONFLICT · Gauntlet Legends' && document.querySelector('#pane').textContent.includes('Chosen: THIS DECK')", 10)
        pane_tap("EDIT THIS DECK'S")
        c.wait("[...document.querySelectorAll('#pane button')].some(b => b.textContent === 'MAKER')", 10)
        pane_tap("MAKER")
        c.wait("document.querySelector('#pane .kp-field')")
        for _ in range(20):
            q("[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === '\u232B').click()")
        type_text("HER MAKER")
        c.wait("document.querySelector('#sheet').classList.contains('hide') === false && (document.querySelector('#pane h2') || {}).textContent === 'CONFLICT · Gauntlet Legends' && document.querySelector('#pane').textContent.includes('HER MAKER')", 15)
        check("EDIT THIS DECK'S: the maker is typed on the keypad, and the conflict opens again with her version in THIS DECK's column; "
              "the earlier choice (about the old version) no longer counts", "HER MAKER" in text("#pane") and "Chosen:" not in text("#pane"))
        shot("31f-conflict-edited")
        pane_tap("KEEP THE ARCHIVE'S")
        c.wait("document.querySelector('#lb-body').textContent.includes('KEEPING THE ARCHIVE')", 10)
        res = api("GET", "/api/library/sync/resolutions")["resolutions"]
        edited = next(r for r in api("GET", "/api/library/sync/changes?since=0&limit=500")["records"] if r["kind"] == "game" and r["id"] == "gauntlet-legends")
        check("KEEP THE ARCHIVE'S replaces the choice (one row), about the version she edited", len(res) == 1 and res[0]["keep"] == "archive"
              and res[0]["deck"] == edited["hash"] and edited["data"]["mfr"] == "HER MAKER")

        click('[data-view="system"]')
        libcard2 = ("[...document.querySelectorAll('#sy-grid .card')]"
                    ".find(c => (c.querySelector('h2') || {}).textContent === 'Library')")
        check("SYSTEM's Library tile: the archive synced a couple of minutes ago, the conflicts and where to settle them, not yet identical",
              settles(c, f"!!({libcard2}) && {libcard2}.textContent.includes('synced 2 min ago') && {libcard2}.textContent.includes('2: LIBRARY')", 20)
              and "not yet" in q(f"{libcard2}.textContent"))
        q(f"{libcard2}.lastElementChild.scrollIntoView({{block: 'end'}})")
        shot("32a-system-sync")
        json.dump(dict(st0, library={"at": time.time(), "ok": False, "error": "the archive refused the request (HTTP 403)", "refused_by_archive": True}), open(st_path, "w"))
        check("... and when the archive has refused the deck's token it says a person has to act, in words",
              settles(c, f"{libcard2}.textContent.includes('TOKEN REFUSED')", 20) and "a person has to act" in q(f"{libcard2}.textContent"))
        q(f"{libcard2}.lastElementChild.scrollIntoView({{block: 'end'}})")
        shot("32b-system-sync-refused")
        json.dump(st0, open(st_path, "w"))
        os.remove(f"{T}/sync/library/conflicts.json")
        check("... and with no archive configured it says so in one line", settles(c, f"{libcard2}.textContent.includes('no archive configured')", 20))

    print("phone (390x844):")
    with Chrome(f"{T}/browser2") as c:
        # ?box=0: the test reaches gatbox-web at 127.0.0.1, which is the kiosk's address; a phone comes by name
        c.open(B + "/dash/?box=0", 390, 844)
        c.wait("document.querySelector('#m-profile').textContent.includes('rail')")
        time.sleep(1.5)
        c.shot(os.path.join(OUT, "18-phone.png"))
        check("phone: no horizontal scroll", c.eval("document.documentElement.scrollWidth <= 391"))
        check("phone: not box mode (the 7\" changes stay on the 7\")", not c.eval("document.documentElement.classList.contains('box')"))

    wlog = open(f"{T}/web.log").read()
    werr = [ln for ln in wlog.splitlines() if any(k in ln for k in ("Traceback", "error on", "live: ", "Error"))]
    if werr:
        print("    ", "\n     ".join(werr[:12]))
    check("server log: no errors", not any(k in wlog for k in ("Traceback", "error on", "live: ")))
    print(f"screenshots: {OUT}" if KEEP else "screenshots: not kept (pass a directory to keep them)")
    print(f"dash: {passed} passed, {failed} failed")


try:
    main()
finally:
    for n in list(procs):
        stop(n)
    shutil.rmtree(T, ignore_errors=True)
    if not KEEP:
        shutil.rmtree(OUT, ignore_errors=True)
sys.exit(1 if failed else 0)
