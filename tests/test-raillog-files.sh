#!/usr/bin/env bash
# gatbox-raillog's M4b behaviour, driven by tools/gatbox-replay: header lines (mode, profile, window, alarm_hi,
# machine), one file per dial mode (the first reading after a turn becomes '# settling='), same-second file names,
# /run/gatbox/mode, and marks from gatbox-web's spool (during a file, before a file, old ones not replayed).
# Temp dirs only; the real logger, port and logs are never touched.
#   bash tests/test-raillog-files.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
REPLAY="$REPO/tools/gatbox-replay"; LOGGER="$REPO/backend/gatbox-raillog"
T=$(mktemp -d); trap 'pkill -f -- "conn=$T/port" 2>/dev/null; rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
mkdir -p "$T/led"; echo '[mmc0] none' > "$T/led/trigger"; : > "$T/led/brightness"; : > "$T/port"
OHM=$'Ω'
mkcsv() { local out=$1; shift
          { echo "iso_time,epoch,value,unit,flags,uptime_s"; echo "# clock=ntp"; e=1790000000
            for r in "$@"; do echo "2026-09-21T00:00:00,$e.5,$r,$((e - 1789999000)).00"; e=$((e + 1)); done; } > "$out.csv"; }

# run <name> <csv> [rate] [hold 0|1]: the logger over a replay, until the stream ends (or ~3 s with hold)
run() {
    local name=$1 src=$2 rate=${3:-fast} hold=${4:-0} delay=${5:-0} out="$T/$1"
    mkdir -p "$out/log" "$out/run" "$out/ctrl"
    [ -d "$T/state-$name" ] && cp "$T/state-$name"/* "$out/ctrl/"
    env GATBOX_SIGROK="$REPLAY" GATBOX_REPLAY_FILE="$src" $([ "$rate" = fast ] && echo GATBOX_REPLAY_FAST=1 || echo GATBOX_REPLAY_RATE=$rate) \
        GATBOX_REPLAY_HOLD=$hold GATBOX_REPLAY_DELAY=$delay GATBOX_DMM_PORT="$T/port" GATBOX_LOGDIR="$out/log" RUNTIME_DIRECTORY="$out/run" \
        GATBOX_CTRL="$out/ctrl" GATBOX_LED="$T/led" GATBOX_CLOCK_WAIT=0 GATBOX_NTP_SYNCED=yes \
        GATBOX_META="$REPO/backend/gatbox-meta" setsid bash "$LOGGER" > "$out/journal" 2>&1 &
    echo $! > "$out/pid"
}
stop() { local out="$T/$1"
         for _ in $(seq 100); do grep -q "stream ended\|no reading for 5 s" "$out/journal" && break; sleep 0.1; done
         kill -TERM -- -"$(cat "$out/pid")" 2>/dev/null; wait "$(cat "$out/pid")" 2>/dev/null; }
files() { grep -o 'logging to .*' "$T/$1/journal" | cut -d' ' -f3; }   # in the order they were opened
# a logger file back to what was sent, in order: '# settling(-unused)=' notes and samples -> value,unit,flags
rebuild() { awk -F, '/^# settling(-unused)?=/ {sub(/^# settling(-unused)?=[^,]*,[^,]*,/, ""); print; next}
                    /^#|^iso/ {next} {print $3 "," $4 "," $5}' "$1"; }

echo "header, default state:"
mkcsv "$T/rail" "5.01,V,DC AUTO" "5.02,V,DC AUTO" "5.03,V,DC AUTO"
run plain "$T/rail.csv"; stop plain
f=$(files plain)
check "one file"                                   '[ "$(files plain | wc -l)" = 1 ]'
check "clock, mode, profile, window, alarm lines"  '[ "$(sed -n 2,6p "$f" | cut -d= -f1 | paste -sd" ")" = "# clock # mode # profile # window # alarm_hi" ]'
check "mode=VDC, +5V profile, 4.75..5.25, 5.775"   'grep -qx "# mode=VDC" "$f" && grep -qx "# profile=rail-5v" "$f" && grep -qx "# window=4.75..5.25 source=profile" "$f" && grep -qx "# alarm_hi=5.775" "$f"'
check "no machine line without a machine"          '! grep -q "^# machine=" "$f"'
check "3 samples"                                  '[ "$(grep -vc "^#\|^iso" "$f")" = 3 ]'
check "/run/gatbox/mode cleared at session end"    '[ ! -e "$T/plain/run/mode" ]'

echo "header, Gauntlet Legends set:"
mkdir -p "$T/state-gl"; echo gauntlet-legends > "$T/state-gl/machine"
run gl "$T/rail.csv"; stop gl
f=$(files gl)
check "window from the machine spec, alarm 5.61"    'grep -qx "# window=4.9..5.1 source=machine:gauntlet-legends" "$f" && grep -qx "# alarm_hi=5.61" "$f"'
check "machine line"                               'grep -qx "# machine=gauntlet-legends" "$f"'

echo "one file per dial mode:"
mkcsv "$T/dial" "5.01,V,DC AUTO" "0.35,mV,DC AUTO" "726.4,V,AC AUTO" "7.44,V,AC AUTO" "7.45,V,AC AUTO HOLD" \
      "0.00,$OHM,AUTO" "inf,T$OHM,AUTO" "1,," "0,AUTO," "4.99,V,DC AUTO" "5.00,V,DC AUTO"
run dial "$T/dial.csv"; stop dial
mapfile -t F < <(files dial)
check "4 files: VDC, VAC, OHM, CONT, then VDC again? (5)" '[ "${#F[@]}" = 5 ]'
check "modes in order"                             '[ "$(for f in "${F[@]}"; do sed -n "s/^# mode=//p" "$f"; done | paste -sd" ")" = "VDC VAC OHM CONT VDC" ]'
check "mV and V DC stay in one file"               '[ "$(grep -vc "^#\|^iso" "${F[0]}")" = 2 ]'
check "junk 726.4 V AC is a settling note"         'grep -qx "# settling=.*,726.4,V,AC AUTO" "${F[1]}" && ! grep -q "^[^#].*,726.4," "${F[1]}"'
check "HOLD doesn't split the file"                '[ "$(grep -vc "^#\|^iso" "${F[1]}")" = 2 ]'
check "each ended file says why (mode-change)"      '[ "$(grep -l "^# mode-change=" "${F[@]}" | wc -l)" = 4 ]'
check "same-second names get _2, _3 …"             '[[ ${F[1]} == *_2.csv ]]'
while read -r f; do rebuild "$f"; done < <(files dial) > "$T/dial.back"
check "samples + settling notes = everything sent" 'diff <(grep -v "^#\|^iso" "$T/dial.csv" | cut -d, -f3-5) "$T/dial.back" >/dev/null'

echo "marks:"
mkdir -p "$T/state-marks"; printf '1790000000.0\t1.0\tscan\tan old mark from before the logger started\n' > "$T/state-marks/marks.spool"
rows=(); for i in $(seq 40); do rows+=("5.0$((i % 10)),V,DC AUTO"); done; mkcsv "$T/long" "${rows[@]}"
run marks "$T/long.csv" 10 1 2          # the "meter" comes on 2 s after the logger
for _ in $(seq 30); do [ -e "$T/marks/run/marks-seen" ] && break; sleep 0.1; done
sleep 0.3; printf '%s\t%s\tdashboard\tbefore the first sample\n' "$EPOCHREALTIME" 5.0 >> "$T/marks/ctrl/marks.spool"
for _ in $(seq 30); do [ -n "$(files marks)" ] && break; sleep 0.1; done
sleep 1; printf '%s\t%s\tscan\tit just crashed, with, commas\n' "$EPOCHREALTIME" 9.0 >> "$T/marks/ctrl/marks.spool"
sleep 1.5; printf '%s\t%s\tbutton\tsecond one\n' "$EPOCHREALTIME" 9.5 >> "$T/marks/ctrl/marks.spool"
sleep 1.5; kill -TERM -- -"$(cat "$T/marks/pid")" 2>/dev/null; wait "$(cat "$T/marks/pid")" 2>/dev/null
f=$(files marks | head -1)
check "old spool lines not replayed"               '! grep -q "an old mark" "$f"'
check "mark made between files → mark-before-start" 'grep -q "^# mark-before-start=.*,dashboard,before the first sample$" "$f"'
check "marks during the file, label kept whole"     'grep -q "^# mark=.*,scan,it just crashed, with, commas$" "$f" && grep -q "^# mark=.*,button,second one$" "$f"'
check "each mark taken once"                       '[ "$(grep -c "^# mark" "$f")" = 3 ]'
check "mark lines carry iso,epoch,uptime"          'grep -qE "^# mark=20[0-9-]+T[0-9:]+,[0-9]+\.[0-9]+,9\.0,scan," "$f"'

echo "raillog files: $pass passed, $fail failed"
[ "$fail" = 0 ]
