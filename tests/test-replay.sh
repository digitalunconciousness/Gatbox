#!/usr/bin/env bash
# tools/gatbox-replay round trip: CSV -> sigrok lines -> gatbox-raillog (GATBOX_SIGROK=gatbox-replay) -> CSV, and the
# value/unit/flags of every sample must come back identical. Covers a synthetic file with every awkward case (mode
# switches, HOLD, REL, OL as "inf T…", Ω as U+2126, unit-less continuity, diode, Hz, %, µA) and copies of real logs
# when they're on this Pi (never the originals). Temp dirs only; the real logger, port and logs are never touched.
#   bash tests/test-replay.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
REPLAY="$REPO/tools/gatbox-replay"; LOGGER="$REPO/backend/gatbox-raillog"
T=$(mktemp -d); trap 'pkill -f -- "conn=$T/port" 2>/dev/null; rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
mkdir -p "$T/led"; echo '[mmc0] none' > "$T/led/trigger"; : > "$T/led/brightness"; : > "$T/port"
OHM=$'Ω'
{ echo "iso_time,epoch,value,unit,flags,uptime_s"; echo "# clock=ntp"
  e=1790000000; u=100
  for r in "5.012,V,DC AUTO" "5.013,V,DC AUTO HOLD" "0.4,mV,DC AUTO" "inf,TV,DC" "119.5,V,AC AUTO" "4.2,V,AC REL" \
           "1.2345,k$OHM,AUTO" "inf,T$OHM,AUTO" "1,," "0,AUTO," "1,AUTO,HOLD" "0.5432,V,DC DIODE" "60.00,Hz,AC AUTO" \
           "50.0,%," "1.234,µF,AUTO" "12.3,µA,DC AUTO" "0.412,A,AC REL" "5.00,V,DC AUTO MAX"; do
      echo "2026-09-21T00:00:$((e % 60 + 10)),$e.5,$r,$u.00"; e=$((e + 1)); u=$((u + 1)); done
  printf '\0\0\0\0'; } > "$T/synthetic.csv"

cols() { tr -d '\000' < "$1" | grep -v '^#\|^iso_time' | cut -d, -f3-5; }
# a logger file back to what was sent, in order: '# settling(-unused)=' notes and samples -> value,unit,flags
rebuild() { awk -F, '/^# settling(-unused)?=/ {sub(/^# settling(-unused)?=[^,]*,[^,]*,/, ""); print; next}
                    /^#|^iso_time/ {next} {print $3 "," $4 "," $5}' "$1"; }

echo "replay lines parse back to the same columns:"
"$REPLAY" --fast "$T/synthetic.csv" > "$T/lines"
check "18 lines, all 'P1: …'"                   '[ "$(grep -c "^P1: " "$T/lines")" = 18 ]'
check "continuity keeps sigrok's spacing"        'grep -qx "P1: 1 " "$T/lines" && grep -qx "P1: 0  AUTO" "$T/lines"'
check "Ω stays U+2126, OL keeps its T prefix"   'grep -qx "P1: inf T$OHM AUTO" "$T/lines" && grep -qx "P1: inf TV DC" "$T/lines"'
while read -r tag val unit rest; do printf '%s,%s,%s\n' "$val" "$unit" "$rest"; done < "$T/lines" > "$T/parsed"
check "bash read gives back value,unit,flags"    'diff <(cols "$T/synthetic.csv") "$T/parsed" >/dev/null'

roundtrip() {   # <name> <csv>: replay it through the real logger into $T/out-<name>, compare samples
    local name=$1 src=$2 out="$T/out-$1" pid
    rm -rf "$out"; mkdir -p "$out/log" "$out/run"
    GATBOX_SIGROK="$REPLAY" GATBOX_REPLAY_FILE="$src" GATBOX_REPLAY_FAST=1 GATBOX_DMM_PORT="$T/port" \
        GATBOX_LOGDIR="$out/log" RUNTIME_DIRECTORY="$out/run" GATBOX_CTRL="$out/ctrl" GATBOX_LED="$T/led" \
        GATBOX_CLOCK_WAIT=0 GATBOX_NTP_SYNCED=yes GATBOX_META="$REPO/backend/gatbox-meta" setsid bash "$LOGGER" > "$out/journal" 2>&1 &
    pid=$!
    for _ in $(seq 600); do grep -q "stream ended" "$out/journal" && break; sleep 0.1; done
    kill -TERM -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    # files in the order the logger opened them; a dial turn moves that turn's first reading to '# settling='
    while read -r f; do rebuild "$f"; done < <(grep -o 'logging to .*' "$out/journal" | cut -d' ' -f3) > "$out/samples"
    local nf; nf=$(grep -c 'logging to' "$out/journal")
    check "$name: every reading round-trips ($(wc -l < "$out/samples") rows, $nf file(s))" 'diff <(cols "$src") "$out/samples" >/dev/null'
}
echo "round trip through gatbox-raillog:"
roundtrip synthetic "$T/synthetic.csv"
for f in rail_20260925_021402.csv rail_20260928_110134.csv; do
    if [ -r "/var/log/gatbox/$f" ]; then cp "/var/log/gatbox/$f" "$T/$f"; roundtrip "${f%.csv}" "$T/$f"
    else echo "  skip  $f (not on this machine)"; fi
done

echo "replay: $pass passed, $fail failed"
[ "$fail" = 0 ]
