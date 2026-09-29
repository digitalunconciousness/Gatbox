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
    for d in ("log", "run", "ctrl", "cache", "data", "led", "spool"):
        os.makedirs(os.path.join(T, d))
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
    with open(f"{T}/roms/gauntlet-legends/LEGEND15-U10_aaaaaaaa.json", "w") as f:
        json.dump({"label": "LEGEND15-U10", "part": "27C040@DIP32", "sha1": "a" * 40, "date": "2026-09-29T10:00:00-05:00",
                   "romident": {"match": True, "matches": [{"set": "gauntleg", "rom": "legend15.u10", "description": "x"}]}}, f)
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
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_REPORT=os.path.join(REPO, "tools/gatbox-rail-report"),
               GATBOX_DATA=f"{T}/data", MPLCONFIGDIR=f"{T}/cache/mpl", GATBOX_DUMP_SPOOL=f"{T}/spool",
               GATBOX_ROMS=f"{T}/roms", GATBOX_MINIPRO_PARTS=f"{T}/parts.txt", GATBOX_MAME_ROMS=f"{T}/mame-roms.json",
               GATBOX_MANUALS=f"{T}/manuals")
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


def main():
    setup()
    with Chrome(f"{T}/browser") as c:
        q = lambda js: c.eval(js)                                  # noqa: E731
        text = lambda sel: q(f"(document.querySelector({json.dumps(sel)}) || {{}}).textContent || ''")   # noqa: E731
        banners = lambda: q("[...document.querySelectorAll('#banners .banner')].map(b => b.textContent).join(' | ')")  # noqa: E731
        click = lambda sel: q(f"document.querySelector({json.dumps(sel)}).click()")  # noqa: E731
        shot = lambda n: c.shot(os.path.join(OUT, n + ".png"))     # noqa: E731

        def tap_keys(s):
            for ch in s:
                q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(ch)}).click()")
            q("[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === 'OK').click()")

        c.open(B + "/dash/", 1024, 600)
        c.wait("document.querySelector('#m-profile').textContent.includes('rail')")
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
        click("#mc-pick")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2")
        tap_keys_filter = "LEG"
        for ch in tap_keys_filter:
            q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(ch)}).click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 1")
        q("document.querySelector('#pane .picklist .row').click()")
        c.wait("document.querySelector('#mc-body').textContent.includes('Gauntlet Legends')", 10)
        time.sleep(0.5)
        check("machine card: name, risk, critical action, rail spec", all(s in text("#mc-body") for s in
              ("Gauntlet Legends", "aging drive", "image drive now", "+5V  4.9–5.1 V")))
        shot("14-machine")
        c.wait("document.querySelector('#mc-body').textContent.includes('OF 2 DUMPED')", 10)
        check("ROM chips (MAME): 1 of 2 dumped, ✓ with the dump's label, the disk not a T48 job",
              all(s_ in text("#mc-body") for s_ in ("ROM chips (MAME)", "1 OF 2 DUMPED", "← LEGEND15-U10", "not a T48 job",
                                                     "512 KB · 27C040 / 27C4001 or 27C4096 / 27C400")))
        q("document.querySelector('#v-machine').scrollTop = 10000")
        shot("14b-machine-chips")
        q("[...document.querySelectorAll('#mc-body button')].find(b => b.textContent === 'gauntleg ▸').click()")
        c.wait("document.querySelectorAll('#pane .picklist .row').length === 2", 10)
        q("[...document.querySelectorAll('#pane .picklist .row')].find(r => r.textContent.includes('1.2')).click()")
        c.wait("document.querySelector('#mc-body').textContent.includes('0 OF 1 DUMPED')", 10)
        check("the version switch: MAME's other version, its own chips", "legend12.u10" in text("#mc-body")
              and "Gauntlet Legends (version 1.2)" in text("#mc-body"))
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
            q(f"[...document.querySelectorAll('#pane .keys button')].find(b => b.textContent === {json.dumps(ch)}).click()")

        def typed(label, keys_):
            field(label)
            c.wait("document.querySelector('#pane .keys')")
            for ch in keys_:
                key(ch)
            key("OK")
            c.wait("document.querySelector('#pane .seg')")

        click("#mc-add")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .seg')")
        check("the form: name, maker, video/pinball, platform, notes; ADD disabled",
              [x for x in q("[...document.querySelectorAll('#pane .fld span')].map(s => s.textContent)")] == ["NAME", "MAKER", "PLATFORM", "NOTES"]
              and q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'ADD MACHINE').disabled"))
        typed("NAME", ["Z", "abc", "a", "x", "x", "o", "n"])
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

        print("MANUALS: the spec sheet, the viewer, CONFIRM, an actual:")
        api("PUT", "/api/machine", {"slug": "gauntlet-legends"})
        click('[data-view="manuals"]')
        c.wait("document.querySelector('#mn-body').textContent.includes('Documents (1)')", 15)
        check("spec sheet: the manual's limit with its page and CONFIRM, the specs file's beside it, a fact",
              all(s_ in text("#mn-body") for s_ in ("Spec sheet", "4.75–5.25 V", "p.2 ▸", "CONFIRM", "specs file: 4.9–5.1 V",
                                                     "Fuse F1", "5 A slow-blow", "Gauntlet Legends Operations Manual")))
        shot("18a-manuals")
        q("document.querySelector('#mn-body .row.doc').click()")
        c.wait("!document.querySelector('#viewer').classList.contains('hide') && document.querySelector('#vw-img').naturalWidth > 0", 30)
        check("the viewer: page 1 of 3 drawn by the Pi", text("#vw-page") == "1 / 3")
        click("#vw-next")
        c.wait("document.querySelector('#vw-page').textContent === '2 / 3' && document.querySelector('#vw-img').complete "
               "&& document.querySelector('#vw-img').naturalWidth > 0", 30)
        shot("18b-viewer")
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
        q("[...document.querySelectorAll('#mn-body button')].find(b => b.textContent === 'CONFIRM').click()")
        c.wait("!document.querySelector('#sheet').classList.contains('hide') && document.querySelector('#pane .pagebox img')", 10)
        c.wait("document.querySelector('#pane .pagebox img').naturalWidth > 0", 30)
        check("CONFIRM shows the page and the manual's words first", "“+5 VDC 4.75 to 5.25 V”" in text("#pane"))
        shot("18c-confirm")
        q("[...document.querySelectorAll('#pane button')].find(b => b.textContent === 'CONFIRM').click()")
        c.wait("document.querySelector('#mn-body').textContent.includes('✓ CONFIRMED')", 15)
        check("confirmed: the window in use is the manual's, and the logger's too",
              "4.75–5.25 V · the manual, confirmed" in text("#mn-body")
              and api("GET", "/api/meter/profile")["profile"]["window"] == [4.75, 5.25])
        api("PUT", "/api/actuals/gauntlet-legends", {"rail": "+5V", "value": 5.2, "window": [5.1, 5.3], "note": "boosted at the PSU"})
        c.wait("document.querySelector('#mn-body').textContent.includes('own window')", 15)
        check("an actual from the field beside the manual (kept, still confirmed), its own window in use",
              all(s_ in text("#mn-body") for s_ in ("5.20 V", "own window 5.1–5.3 V", "boosted at the PSU", "4.75–5.25 V",
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
        api("DELETE", "/api/machine")
        c.wait("!document.querySelector('#du-body').textContent.includes('CHIP: legend12')", 10)
        check("no machine: the chip and its label go with it", not q("document.querySelectorAll('#du-body .tile.on').length")
              and "TYPE THE CHIP'S LABEL" in text("#du-body"))

        print("DUMP (M7; a runner stands in for gatbox-dump.path):")
        if any(open(p_).read().strip() == "a466" for p_ in __import__("glob").glob("/sys/bus/usb/devices/*/idVendor")):
            import threading
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
            lan = next((a for a in subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split() if "." in a), None)
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
        check("system: kiosk controls on the Pi's own screen", q("!document.querySelector('#sy-kiosk').classList.contains('hide')"))
        check("devices: NOT FITTED cards greyed", q("document.querySelectorAll('#dv-grid .notfit').length") == 3)

    print("phone (390x844):")
    with Chrome(f"{T}/browser2") as c:
        c.open(B + "/dash/", 390, 844)
        c.wait("document.querySelector('#m-profile').textContent.includes('rail')")
        time.sleep(1.5)
        c.shot(os.path.join(OUT, "18-phone.png"))
        check("phone: no horizontal scroll", c.eval("document.documentElement.scrollWidth <= 391"))

    check("server log: no errors", not any(k in open(f"{T}/web.log").read() for k in ("Traceback", "error on", "live: ")))
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
