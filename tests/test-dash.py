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
    for d in ("log", "run", "ctrl", "cache", "data", "led", "spool", "roms"):
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
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_REPORT=os.path.join(REPO, "tools/gatbox-rail-report"),
               GATBOX_DATA=f"{T}/data", MPLCONFIGDIR=f"{T}/cache/mpl", GATBOX_DUMP_SPOOL=f"{T}/spool",
               GATBOX_ROMS=f"{T}/roms", GATBOX_MINIPRO_PARTS=f"{T}/parts.txt")
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

        print("DUMP (M7; a runner stands in for gatbox-dump.path):")
        if any(open(p_).read().strip() == "a466" for p_ in __import__("glob").glob("/sys/bus/usb/devices/*/idVendor")):
            import threading
            done = threading.Event()

            def runner():
                while not done.is_set():
                    if os.path.exists(f"{T}/spool/request.json"):
                        time.sleep(1.5)                          # long enough to see the progress card
                        subprocess.run(["python3", os.path.join(REPO, "tools/gatbox-dump"), "--job", f"{T}/spool/request.json",
                                        "--status", f"{T}/spool/status.json"], capture_output=True, timeout=60,
                                       env=dict(os.environ, GATBOX_MINIPRO=os.path.join(REPO, "tests/fake-minipro"),
                                                GATBOX_MAME=os.path.join(REPO, "tests/fake-mame"), GATBOX_ROMS=f"{T}/roms",
                                                GATBOX_API=B, FAKE_ROM=f"{T}/rom.bin", FAKE_ID=os.environ.get("FAKE_ID_NEXT", ""),
                                                FAKE_MAME_MATCH="epr-15781c.ic18 sonic SegaSonic The Hedgehog (Japan, rev. C)"))
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
