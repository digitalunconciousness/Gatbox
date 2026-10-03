#!/usr/bin/env bash
# gatbox-web's JSON API (M4d) against a real gatbox-raillog driven by tools/gatbox-replay, plus every URL from
# before the API. Covers: the SSE stream (hello, backlog, samples as the replay produces them, session rollovers,
# the spike / alarm rule, heartbeats), /api/meter, profile / alarm switch / machine changes (and the new files they
# start), marks (live and pending -> mark-before-start), captures, NEW, sessions + report, system, devices, roster,
# and the validation rules. A synthetic roster (two entries) stands in for the real one, which is workplace data.
# Temp dirs and spare ports only: the real logger, port, logs and state are never touched.
#   bash tests/test-web-api.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
REPLAY="$REPO/tools/gatbox-replay"; LOGGER="$REPO/backend/gatbox-raillog"
T=$(mktemp -d); PORT=8095; B="http://127.0.0.1:$PORT"
cleanup() { for p in "$T"/*.pid; do [ -f "$p" ] && kill -TERM -- -"$(cat "$p")" 2>/dev/null; done
            if [ -n "${TEST_KEEP:-}" ]; then echo "kept $T"; else rm -rf "$T"; fi; }
trap cleanup EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
waitfor() { for _ in $(seq "${2:-100}"); do eval "$1" && return 0; sleep 0.1; done; return 1; }
mkdir -p "$T"/{log,run,ctrl,cache,data,led}; echo '[mmc0] none' > "$T/led/trigger"; : > "$T/led/brightness"; : > "$T/port"
cp "$REPO/data/profiles.json" "$REPO/data/gatbox-machine-specs.json" "$T/data/"
cat > "$T/data/gatbox-barcade-roster.json" <<'EOF'
{"meta": {"platforms": {"test_hdd": {"desc": "Test platform with a hard drive", "faults": ["drive failure"],
                                     "parts": ["CF adapter"], "pm": "image the drive"}},
          "risk_legend": {"hdd": "aging drive"},
          "critical_actions": {"image_drive_now": {"note": "Image the drive.", "ide_to_cf_ssd": ["Gauntlet Legends"]},
                               "control_wear_hotspots": {"four_player_harness": ["Gauntlet"]}}},
 "video_games": [{"slug": "gauntlet-legends", "name": "Gauntlet Legends", "platform": "test_hdd", "mfr": "Atari/Midway",
                  "risk": ["hdd"], "faults": [], "parts": [], "notes": "test entry"},
                 {"slug": "gauntlet", "name": "Gauntlet", "platform": "test_hdd", "mfr": "Atari", "risk": [],
                  "faults": [], "parts": [], "notes": ""}],
 "pinball": [], "retired": []}
EOF
export GATBOX_DATA="$T/data"
OHM=$'\u2126'; OMEGA=$'\u03a9'     # sigrok writes OHM SIGN; the display uses Greek omega (gatboxlib.modes)

# replay sources: 2026-09-21 timestamps; the logger stamps its own
mkcsv() { local out=$1; shift
          { echo "iso_time,epoch,value,unit,flags,uptime_s"; echo "# clock=ntp"; e=1790000000
            for r in "$@"; do echo "2026-09-21T00:00:00,$e.5,$r,$((e - 1789999000)).00"; e=$((e + 1)); done; } > "$out"; }
rep() { local n=$1 r=$2 i; for ((i = 0; i < n; i++)); do echo "$r"; done; }
logger_start() {   # <csv> <lines/s>: the logger over a replay; HOLD keeps the "port" open when the file is done
    env GATBOX_SIGROK="$REPLAY" GATBOX_REPLAY_FILE="$1" GATBOX_REPLAY_RATE="$2" GATBOX_REPLAY_HOLD=1 \
        GATBOX_DMM_PORT="$T/port" GATBOX_LOGDIR="$T/log" RUNTIME_DIRECTORY="$T/run" GATBOX_CTRL="$T/ctrl" \
        GATBOX_LED="$T/led" GATBOX_CLOCK_WAIT=0 GATBOX_NTP_SYNCED=yes GATBOX_META="$REPO/backend/gatbox-meta" \
        setsid bash "$LOGGER" >> "$T/journal" 2>&1 &
    echo $! > "$T/logger.pid"
}
logger_stop() { kill -TERM -- -"$(cat "$T/logger.pid")" 2>/dev/null; wait "$(cat "$T/logger.pid")" 2>/dev/null; rm -f "$T/logger.pid"; }
nfiles() { find "$T/log" -name 'rail_*.csv' | wc -l; }
newest() { ls -1 "$T/log" | LC_ALL=C sort | tail -1; }   # C order: _2 after the plain name
# api <METHOD> <path> [json]: prints the HTTP code; the body lands in $T/body
api() { curl -s -o "$T/body" -w '%{http_code}' -X "$1" -H 'Content-Type: application/json' ${3:+--data "$3"} "$B$2"; }
js() { python3 -c "import json,sys; d=json.load(open('$T/body')); print($1)"; }

# an old-format session (no profile lines) and a copy of the 09-25 fixture, if this Pi has it
mkcsv "$T/log/rail_20260921_000000.csv" "5.01,V,DC AUTO" "5.02,V,DC AUTO" "5.03,V,DC AUTO" "4.99,V,DC AUTO"
FX=/var/log/gatbox/rail_20260925_021402.csv
[ -r "$FX" ] && cp "$FX" "$T/log/"

GATBOX_WEB_PORT=$PORT STATE_DIRECTORY="$T/ctrl" CACHE_DIRECTORY="$T/cache" GATBOX_LOGDIR="$T/log" GATBOX_RUNDIR="$T/run" \
GATBOX_REPORT="$REPO/tools/gatbox-rail-report" GATBOX_WEB_HEARTBEAT=1 MPLCONFIGDIR="$T/cache/mpl" \
    setsid python3 "$REPO/backend/gatbox-web" > "$T/web.log" 2>&1 &
echo $! > "$T/web.pid"
waitfor 'curl -fs -o /dev/null "$B/kiosk/state"' 50 || { echo "gatbox-web didn't start"; cat "$T/web.log"; exit 1; }

echo "old URLs:"
O=rail_20260921_000000.csv
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
check "/ lists the old session"              'curl -fs "$B/" | grep -q "2026-09-21 00:00:00"'
check "/ tags each session with the report's verdict" 'curl -fs "$B/" | grep -q "held the window"'
check "/s/ 200, /png/ 200, /csv/ 200"        '[ "$(code "$B/s/$O") $(code "$B/png/$O") $(code "$B/csv/$O")" = "200 200 200" ]'
check "/s/ with lo/hi still works"           'curl -fs "$B/s/$O?lo=5.0&hi=5.02" | grep -q "EXCURSIONS (1)"'
check "/pdf/ is a PDF"                       'curl -fs "$B/pdf/$O" | head -c 5 | grep -q "%PDF-"'
check "/pdf/ with from/to"                   '[ "$(code "$B/pdf/$O?from=2026-09-21T00:00&to=2026-09-21T00:01")" = 200 ]'
check "/csv/ is the file itself"             'cmp -s <(curl -fs "$B/csv/$O") "$T/log/$O"'
f=$([ -d /usr/local/share/gatbox-web/fonts ] && echo 200 || echo 404)
check "/font/ (installed: $f), unknown: 404"  '[ "$(code "$B/font/ShareTechMono-Regular.ttf") $(code "$B/font/x.ttf")" = "$f 404" ]'
check "/kiosk/state"                         '[ "$(curl -fs "$B/kiosk/state")" = "{\"exit_at\": 0, \"shutdown_at\": 0}" ]'
check "POST /control stop -> 303, flag"      '[ "$(code -X POST -d action=stop "$B/control")" = 303 ] && [ -e "$T/ctrl/stopped" ]'
check "POST /control start -> 303, flags"    '[ "$(code -X POST -d action=start "$B/control")" = 303 ] && [ ! -e "$T/ctrl/stopped" ] && [ -e "$T/ctrl/start-request" ]'
check "POST /control junk -> 400"            '[ "$(code -X POST -d action=boom "$B/control")" = 400 ]'
check "unknown session -> 404"               '[ "$(code "$B/s/rail_20990101_000000.csv") $(code "$B/csv/../etc/passwd")" = "404 404" ]'
if [ -r "$FX" ]; then
    check "09-25 fixture: page + 3 suspect OV" 'curl -fs "$B/s/rail_20260925_021402.csv" | grep -q "OVER-VOLTAGE (3): above 5.775 V; 3 suspect"'
    check "09-25 fixture: verdict + tiles + tags, text collapsed" 'p=$(curl -fs "$B/s/rail_20260925_021402.csv"); grep -q "HELD THE WINDOW" <<<"$p" && [ "$(grep -o "tag warn\">suspect" <<<"$p" | wc -l)" = 3 ] && grep -q "<details><summary>Full text report" <<<"$p" && ! grep -q "left the window" <<<"$p"'
fi

echo "SSE + the alarm rule (replay at 20/s):"
rows=(); mapfile -t rows < <(rep 10 "5.01,V,DC AUTO"; echo "6.20,V,DC AUTO"; rep 5 "5.02,V,DC AUTO"
    echo "6.10,V,DC AUTO"; echo "6.25,V,DC AUTO"; echo "6.15,V,DC AUTO"; rep 5 "5.01,V,DC AUTO"
    echo "inf,TV,DC AUTO"; rep 3 "5.01,V,DC AUTO"; rep 6 "1.20,k$OHM,AUTO")
mkcsv "$T/a.csv" "${rows[@]}"
curl -sN "$B/api/rail/live?backlog=5" > "$T/sse" & echo $! > "$T/sse.pid"
waitfor 'grep -q "^event: hello" "$T/sse"' 30
logger_start "$T/a.csv" 20
waitfor 'grep -q "^event: session" "$T/sse" && grep -c "^event: sample" "$T/sse" | grep -q "^3[0-9]$"' 100
sleep 0.5
api GET /api/meter >/dev/null
check "GET /api/meter while logging: OHM, 1.20 kΩ" '[ "$(js "d[\"logging\"], d[\"mode\"], d[\"display\"], d[\"dial_ok\"]")" = "True OHM 1.20 k$OMEGA False" ]'
waitfor 'grep -q "\"file\": null" "$T/sse"' 120                 # 5 s of silence ends the session
logger_stop
sleep 1.2; kill "$(cat "$T/sse.pid")"; rm -f "$T/sse.pid"
ev() { python3 - "$T/sse" "$1" <<'EOF'
import json, sys
evs, cur = [], {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.rstrip("\n")
    if not line:
        if "event" in cur: evs.append(cur)
        cur = {}
        continue
    k, _, v = line.partition(": ")
    cur[k] = v
r = eval(sys.argv[2], {"evs": evs, "json": json, "D": lambda e: json.loads(e["data"])})
print(*r) if isinstance(r, tuple) else print(r)
EOF
}
check "hello first, with the meter snapshot"  '[ "$(ev "evs[0][\"event\"], \"profile\" in D(evs[0])")" = "hello True" ]'
check "backlog event (empty at connect)"      '[ "$(ev "[D(e)[\"rows\"] for e in evs if e[\"event\"] == \"backlog\"]")" = "[[]]" ]'
check "one sample event per reading (33)"     '[ "$(ev "sum(e[\"event\"] == \"sample\" for e in evs)")" = 33 ]'   # 34 sent; a dial turn's first reading is '# settling='
check "samples in replay order, base units"   '[ "$(ev "[D(e)[\"raw\"] for e in evs if e[\"event\"] == \"sample\"][9:12]")" = "['"'"'5.01'"'"', '"'"'6.20'"'"', '"'"'5.02'"'"']" ]'
check "OL: v null, ol true, display OL"       '[ "$(ev "[(D(e)[\"v\"], D(e)[\"ol\"], D(e)[\"display\"]) for e in evs if e[\"event\"] == \"sample\" and D(e)[\"ol\"]]")" = "[(None, True, '"'"'OL'"'"')]" ]'
check "per-sample alarm: spike, then alarm x2" '[ "$(ev "[D(e)[\"alarm\"] for e in evs if e[\"event\"] == \"sample\" and D(e)[\"alarm\"] not in (\"ok\", None)]")" = "['"'"'spike'"'"', '"'"'spike'"'"', '"'"'alarm'"'"', '"'"'alarm'"'"']" ]'
check "alarm events: spike, closed, spike, alarm, closed" '[ "$(ev "[(D(e)[\"kind\"], D(e)[\"open\"]) for e in evs if e[\"event\"] == \"alarm\"]")" = "[('"'"'spike'"'"', True), ('"'"'spike'"'"', False), ('"'"'spike'"'"', True), ('"'"'alarm'"'"', True), ('"'"'alarm'"'"', False)]" ]'
check "the alarm's peak + count"              '[ "$(ev "[(D(e)[\"peak\"], D(e)[\"n\"]) for e in evs if e[\"event\"] == \"alarm\"][-1]")" = "6.25 3" ]'
check "OL is not over-voltage"                '[ "$(ev "[D(e)[\"alarm\"] for e in evs if e[\"event\"] == \"sample\" and D(e)[\"ol\"]]")" = "['"'"'ok'"'"']" ]'
check "dial turn: a session event, new file"  '[ "$(ev "[bool(D(e)[\"file\"]) for e in evs if e[\"event\"] == \"session\"]")" = "[True, True, False]" ]'
check "OHM file: no alarm evaluation"         '[ "$(ev "{D(e)[\"alarm\"] for e in evs if e[\"event\"] == \"sample\" and D(e)[\"mode\"] == \"OHM\"}")" = "{None}" ]'
check "heartbeats"                            '[ "$(ev "sum(e[\"event\"] == \"heartbeat\" for e in evs) >= 2")" = True ]'
check "event ids increase"                    '[ "$(ev "(lambda i: i == sorted(i) and len(set(i)) == len(i))([int(e[\"id\"]) for e in evs if \"id\" in e])")" = True ]'
A1=$(ls -1 "$T/log" | LC_ALL=C sort | grep -v "^$O$\|20260925" | head -1)

echo "sessions + report:"
api GET /api/rail/sessions >/dev/null
check "sessions: newest first, both new files" '[ "$(js "len(d[\"sessions\"]) >= 3 and d[\"sessions\"][0][\"file\"] > d[\"sessions\"][-1][\"file\"]")" = True ]'
check "the VDC file: 2 OV, profile rail-5v"   '[ "$(js "[(s[\"ov\"], s[\"profile\"][\"id\"], s[\"mode\"], s[\"window\"][\"lo\"]) for s in d[\"sessions\"] if s[\"file\"] == \"$A1\"]")" = "[(2, '"'"'rail-5v'"'"', '"'"'VDC'"'"', 4.75)]" ]'
check "old file: default window, clock ntp"   '[ "$(js "[(s[\"window\"][\"source\"][:7], s[\"clock\"][\"source\"]) for s in d[\"sessions\"] if s[\"file\"] == \"$O\"]")" = "[('"'"'default'"'"', '"'"'ntp'"'"')]" ]'
check "sessions: the verdict travels, decided once"  '[ "$(js "[(s[\"verdict\"][\"state\"], s[\"verdict\"][\"css\"]) for s in d[\"sessions\"] if s[\"file\"] == \"$A1\"]")" = "[('"'"'over'"'"', '"'"'bad'"'"')]" ]'
api GET "/api/rail/report/$A1" >/dev/null
check "report: carries the verdict too"       '[ "$(js "d[\"verdict\"][\"state\"], d[\"verdict\"][\"title\"]")" = "over OVER-VOLTAGE" ]'
check "report: text, OV sections, links"      '[ "$(js "\"OVER-VOLTAGE (2)\" in d[\"text\"], len(d[\"over_voltage\"]), d[\"png\"].startswith(\"/png/\"), d[\"csv\"]")" = "True 2 True /csv/$A1" ]'
check "report PNG link works"                 '[ "$(code "$B$(js "d[\"png\"]")")" = 200 ]'
check "report with lo/hi + range"             '[ "$(api GET "/api/rail/report/$A1?lo=5.0&hi=5.3&from=2000-01-01T00:00")" = 200 ] && [ "$(js "d[\"window\"][\"lo\"], d[\"range\"][\"from\"]")" = "5.0 2000-01-01T00:00:00" ]'
check "report: unknown / traversal -> 404"    '[ "$(api GET /api/rail/report/nope.csv) $(api GET /api/rail/report/..%2F..%2Fetc%2Fpasswd)" = "404 404" ]'
check "samples: lone = spike, a run of 3 = alarm x3" '[ "$(api GET "/api/rail/samples/$A1")" = 200 ] && [ "$(js "[r[6] for r in d[\"rows\"] if r[6] in (\"spike\", \"alarm\")], d[\"n\"], d[\"alarm_hi\"]")" = "['"'"'spike'"'"', '"'"'alarm'"'"', '"'"'alarm'"'"', '"'"'alarm'"'"'] 28 5.775" ]'
if [ -r "$FX" ]; then
    check "samples: 09-25 fixture squeezed to 200 points keeps its 3 spikes" '[ "$(api GET "/api/rail/samples/rail_20260925_021402.csv?max=200")" = 200 ] && [ "$(js "d[\"downsampled\"], len(d[\"rows\"]) <= 210, sum(r[6] == \"spike\" for r in d[\"rows\"]), d[\"n\"]")" = "True True 3 10251" ]'
fi
check "samples: unknown -> 404"               '[ "$(api GET /api/rail/samples/nope.csv)" = 404 ]'
check "/dash/: page, files; nothing else"     '[ "$(code "$B/dash/") $(code "$B/dash/dash.js") $(code "$B/dash/chart.js") $(code "$B/dash/dash.css")" = "200 200 200 200" ] && [ "$(code "$B/dash/x.py") $(code "$B/dash/..%2F..%2Fbackend%2Fgatbox-web") $(code "$B/dash/nope.js")" = "404 404 404" ] && [ "$(code "$B/dash")" = 301 ]'
check "phone view links the dashboard"        'curl -fs "$B/" | grep -q "href=\"/dash/\""'

echo "profile, alarm switch, machine, NEW, marks, captures (live session):"
mapfile -t rows < <(rep 1200 "5.01,V,DC AUTO"); mkcsv "$T/b.csv" "${rows[@]}"
logger_start "$T/b.csv" 20
n0=$(nfiles); waitfor '[ "$(nfiles)" -gt $n0 ] && api GET /api/meter >/dev/null && [ "$(js "d[\"logging\"]")" = True ]'
check "meter: VDC, dial ok, +5V profile"      '[ "$(js "d[\"mode\"], d[\"dial_ok\"], d[\"profile\"][\"id\"], d[\"leads_reversed\"]")" = "VDC True rail-5v False" ]'
check "PUT without JSON type -> 415"          '[ "$(curl -s -o /dev/null -w "%{http_code}" -X PUT --data "{\"id\":\"ripple\"}" "$B/api/meter/profile")" = 415 ]'
check "PUT unknown profile -> 400"            '[ "$(api PUT /api/meter/profile "{\"id\":\"nope\"}")" = 400 ]'
check "PUT bad JSON -> 400"                   '[ "$(api PUT /api/meter/profile "{nope")" = 400 ]'
check "PUT NaN ceiling -> 400"                '[ "$(api PUT /api/meter/profile "{\"id\":\"ripple\",\"ceiling\":NaN}")" = 400 ]'
check "PUT window on a no-window profile -> 400" '[ "$(api PUT /api/meter/profile "{\"id\":\"diode\",\"window\":[0,1]}")" = 400 ]'
check "PUT lo >= hi -> 400"                   '[ "$(api PUT /api/meter/profile "{\"id\":\"current\",\"window\":[2,1]}")" = 400 ]'
check "PUT body over 4 KB -> 413"             '[ "$(api PUT /api/meter/profile "{\"id\":\"$(head -c 5000 /dev/zero | tr "\0" x)\"}")" = 413 ]'
check "DELETE /api/meter -> 405"              '[ "$(api DELETE /api/meter)" = 405 ]'
n0=$(nfiles)
check "PUT ripple ceiling 0.15 -> new file"   '[ "$(api PUT /api/meter/profile "{\"id\":\"ripple\",\"ceiling\":0.15}")" = 200 ] && [ "$(js "d[\"new_file\"], d[\"profile\"][\"window\"], d[\"profile\"][\"source\"]")" = "True [0.0, 0.15] user" ]'
# the logger makes the file, then gatbox-meta's lines land a moment later: wait for them, not just the file
waitfor '[ "$(nfiles)" -gt $n0 ] && grep -q "^# alarm_hi=" "$T/log/$(newest)"'; F=$T/log/$(newest)
check "new file's header: ripple, user window, no alarm" 'grep -qx "# profile=ripple" "$F" && grep -qx "# window=0..0.15 source=user" "$F" && grep -qx "# alarm_hi=none" "$F"'
waitfor 'api GET /api/meter >/dev/null && [ "$(js "d[\"file\"]")" = "$(basename "$F")" ]'
check "meter: dial VDC vs ripple (VAC): dial_ok false" '[ "$(js "d[\"dial_ok\"], [e[\"key\"] for e in d[\"expected\"]]")" = "False ['"'"'VAC'"'"']" ]'
check "ripple's ceiling remembered"           '[ "$(api PUT /api/meter/profile "{\"id\":\"rail-5v\"}")" = 200 ] && [ "$(api PUT /api/meter/profile "{\"id\":\"ripple\"}")" = 200 ] && [ "$(js "d[\"profile\"][\"window\"]")" = "[0.0, 0.15]" ]'
api PUT /api/meter/profile '{"id":"rail-5v"}' >/dev/null
check "ALARM OFF"                             '[ "$(api PUT /api/meter/alarm "{\"on\":false}")" = 200 ] && [ "$(js "d[\"alarm\"][\"on\"]")" = False ] && [ -e "$T/ctrl/alarm.json" ]'
check "alarm switch: bad body -> 400"         '[ "$(api PUT /api/meter/alarm "{\"on\":\"no\"}")" = 400 ]'
check "another profile turns it back on"      '[ "$(api PUT /api/meter/profile "{\"id\":\"rail-12v\"}")" = 200 ] && [ "$(js "d[\"alarm\"][\"on\"]")" = True ]'
api PUT /api/meter/profile '{"id":"rail-5v"}' >/dev/null
check "machine: unknown slug -> 404"          '[ "$(api PUT /api/machine "{\"slug\":\"../../etc\"}")" = 404 ]'
# settle after the profile changes above: the logger is on the rail-5v file and no new-file request is pending (a PUT
# landing in the gap between files rightly says new_file=false: the next file gets the machine anyway)
waitfor 'api GET /api/meter >/dev/null && [ "$(js "d[\"logging\"]")" = True ] && [ "$(js "d[\"file\"]")" = "$(newest)" ] && grep -qx "# profile=rail-5v" "$T/log/$(newest)" && ! [ "$T/ctrl/start-request" -nt "$T/run/session-ref" ]' 150
n0=$(nfiles)
check "PUT machine -> changed, new file"      '[ "$(api PUT /api/machine "{\"slug\":\"gauntlet-legends\"}")" = 200 ] && [ "$(js "d[\"changed\"], d[\"new_file\"], d[\"entry\"][\"name\"]")" = "True True Gauntlet Legends" ]'
waitfor '[ "$(nfiles)" -gt $n0 ] && grep -q "^# machine=" "$T/log/$(newest)"'; F=$T/log/$(newest)
check "header: machine + its spec window"     'grep -qx "# machine=gauntlet-legends" "$F" && grep -qx "# window=4.9..5.1 source=machine:gauntlet-legends" "$F" && grep -qx "# alarm_hi=5.61" "$F"'
waitfor 'api GET /api/meter >/dev/null && [ "$(js "d[\"file\"]")" = "$(basename "$F")" ]'
n0=$(nfiles)
check "same machine again: nothing"           '[ "$(api PUT /api/machine "{\"slug\":\"gauntlet-legends\"}")" = 200 ] && [ "$(js "d[\"changed\"], d[\"new_file\"]")" = "False False" ]'
sleep 0.5
check "... and no new file"                   '[ "$(nfiles)" = $n0 ]'
check "GET /api/machine: the merged entry"    '[ "$(api GET /api/machine)" = 200 ] && [ "$(js "d[\"slug\"], d[\"entry\"][\"platform_info\"][\"desc\"], d[\"entry\"][\"spec\"][\"rails\"][\"+5V\"]")" = "gauntlet-legends Test platform with a hard drive [4.9, 5.1]" ]'
check "roster: critical actions by name, longest wins" '[ "$(api GET /api/roster/gauntlet-legends)" = 200 ] && [ "$(js "[c[\"path\"] for c in d[\"critical_actions\"]]")" = "['"'"'image_drive_now/ide_to_cf_ssd'"'"']" ] && [ "$(api GET /api/roster/gauntlet)" = 200 ] && [ "$(js "[c[\"path\"] for c in d[\"critical_actions\"]]")" = "['"'"'control_wear_hotspots/four_player_harness'"'"']" ]'
check "roster: listing, unknown -> 404"       '[ "$(api GET /api/roster)" = 200 ] && [ "$(js "len(d[\"machines\"])")" = 2 ] && [ "$(api GET /api/roster/nope)" = 404 ]'
check "mark: bad source / long / tab -> 400"  '[ "$(api POST /api/mark "{\"source\":\"x\"}") $(api POST /api/mark "{\"source\":\"phone\",\"label\":\"$(printf %041d 0)\"}") $(api POST /api/mark "{\"source\":\"phone\",\"label\":\"a\\tb\"}")" = "400 400 400" ]'
check "mark while live -> 201, not pending"   '[ "$(api POST /api/mark "{\"source\":\"dashboard\",\"label\":\"it just crashed\"}")" = 201 ] && [ "$(js "d[\"pending\"]")" = False ]'
waitfor 'grep -q "^# mark=.*,dashboard,it just crashed$" "$F"'
check "... in the live file at the next sample" 'grep -q "^# mark=.*,dashboard,it just crashed$" "$F"'
check "capture: label required -> 400"        '[ "$(api POST /api/captures "{}")" = 400 ]'
check "capture the live reading -> 201"       '[ "$(api POST /api/captures "{\"label\":\"U12 pin 3\"}")" = 201 ] && [ "$(js "d[\"value\"], d[\"unit\"], d[\"machine\"], d[\"mode\"]")" = "5.01 V gauntlet-legends VDC" ]'
check "GET captures for the machine"          '[ "$(api GET /api/captures)" = 200 ] && [ "$(js "d[\"machine\"], [(c[\"label\"], c[\"value\"], c[\"profile\"]) for c in d[\"captures\"]]")" = "gauntlet-legends [('"'"'U12 pin 3'"'"', '"'"'5.01'"'"', '"'"'rail-5v'"'"')]" ]'
check "captures: unknown machine -> 404"      '[ "$(api GET "/api/captures?machine=..%2Fprofile")" = 404 ]'
n0=$(nfiles)
check "DELETE machine -> new file"            '[ "$(api DELETE /api/machine)" = 200 ] && [ "$(js "d[\"changed\"], d[\"new_file\"]")" = "True True" ]'
waitfor '[ "$(nfiles)" -gt $n0 ] && grep -q "^# alarm_hi=" "$T/log/$(newest)"'
check "... without a machine line"            '! grep -q "^# machine=" "$T/log/$(newest)"'
waitfor 'api GET /api/meter >/dev/null && [ "$(js "d[\"file\"]")" = "$(newest)" ]'
n0=$(nfiles)
check "POST /api/session/new -> 202, new file" '[ "$(api POST /api/session/new)" = 202 ] && waitfor "[ \"\$(nfiles)\" -gt $n0 ]"'
logger_stop

echo "no live session:"
waitfor 'api GET /api/meter >/dev/null && [ "$(js "d[\"logging\"]")" = False ]' 150
check "meter: not logging, no value"          '[ "$(js "d[\"logging\"], d[\"value\"], d[\"display\"]")" = "False None None" ]'
check "capture without a live reading -> 409" '[ "$(api POST /api/captures "{\"label\":\"x\"}")" = 409 ]'
check "mark with no session -> pending"       '[ "$(api POST /api/mark "{\"source\":\"phone\",\"label\":\"before power-on\"}")" = 201 ] && [ "$(js "d[\"pending\"]")" = True ]'
check "... listed in /api/meter"              'api GET /api/meter >/dev/null && [ "$(js "[m[\"label\"] for m in d[\"marks_pending\"]]")" = "['"'"'before power-on'"'"']" ]'
mapfile -t rows < <(rep 100 "5.01,V,DC AUTO"); mkcsv "$T/c.csv" "${rows[@]}"
n0=$(nfiles); logger_start "$T/c.csv" 20; waitfor '[ "$(nfiles)" -gt $n0 ] && grep -q "^# mark-before-start=" "$T/log/$(newest)"'
check "... lands in the next file as mark-before-start" 'grep -q "^# mark-before-start=.*,phone,before power-on$" "$T/log/$(newest)"'
waitfor 'api GET /api/meter >/dev/null && [ "$(js "len(d[\"marks_pending\"]), len(d[\"session\"][\"marks\"])")" = "0 1" ]'   # the follower reads it ≤0.25 s later
check "... and is no longer pending"          '[ "$(js "len(d[\"marks_pending\"]), [m[\"label\"] for m in d[\"session\"][\"marks\"]]")" = "0 ['"'"'before power-on'"'"']" ]'
logger_stop

check "POST /api/session/stop -> 202, stopped" '[ "$(api POST /api/session/stop)" = 202 ] && [ -e "$T/ctrl/stopped" ] && [ "$(api POST /api/session/new)" = 202 ] && [ ! -e "$T/ctrl/stopped" ]'

echo "system + devices:"
check "/api/system: the panel's fields"       '[ "$(api GET /api/system)" = 200 ] && [ "$(js "all(k in d for k in (\"temp_c\", \"throttled\", \"ext5v_v\", \"disk\", \"network\", \"clock\", \"kiosk\", \"versions\", \"logger\"))")" = True ]'
check "/api/system: clock source + disk"      '[ "$(js "d[\"clock\"][\"source\"] in (\"ntp\", \"rtc\", \"unverified\"), d[\"disk\"][\"system\"][\"total\"] > 0")" = "True True" ]'
check "/api/devices: dmm, t48, scanner, touch" '[ "$(api GET /api/devices)" = 200 ] && [ "$(js "sorted(k for k in d if k != \"usb\")")" = "['"'"'dmm'"'"', '"'"'scanner'"'"', '"'"'t48'"'"', '"'"'touch'"'"']" ]'
check "unknown /api path -> 404 JSON"         '[ "$(api GET /api/nope)" = 404 ] && [ "$(js "d[\"error\"]")" = "not found" ]'
check "server log: no errors"                 '! grep -q "Traceback\|error on\|^live: \|^warm-up: " "$T/web.log"'

echo "web api: $pass passed, $fail failed"
[ "$fail" = 0 ]
