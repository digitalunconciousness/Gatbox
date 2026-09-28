#!/usr/bin/env bash
# End-to-end: run gatbox-raillog against a fake sigrok-cli and check the session file's clock lines.
# Everything lives in a temp dir (log, run, ctrl, LED, port); the real service, /dev/gatbox-dmm and
# /var/log/gatbox are never touched. Runs as a normal user.
#   bash tests/test-raillog-clock.sh [path/to/gatbox-raillog]
set -u
LOGGER=$(readlink -f "${1:-$(dirname "$0")/../backend/gatbox-raillog}")
T=$(mktemp -d); trap 'pkill -f -- "$T/bin/sigrok-cli" 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/led" "$T/rtc0"
cat > "$T/bin/sigrok-cli" <<'EOF'
#!/usr/bin/env bash
# fake: 20 readings at 10/s, then silence with the port "open" (like the D02 head coming off)
for i in $(seq 20); do echo "P1: 5.0$((i % 10))2 V DC AUTO"; sleep 0.1; done
sleep 60
EOF
chmod +x "$T/bin/sigrok-cli"; echo '[mmc0] none' > "$T/led/trigger"; : > "$T/led/brightness"; : > "$T/rtc0-dev"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

# run <ntp yes|no> <flag at start 0|1> <touch flag after 0.8 s 0|1>: one logger run, prints the CSV
run() {
    rm -rf "$T/log" "$T/run" "$T/ctrl" "$T/ntpflag"; : > "$T/port"
    [ "$2" = 1 ] && : > "$T/ntpflag"
    PATH="$T/bin:$PATH" GATBOX_DMM_PORT="$T/port" GATBOX_LOGDIR="$T/log" RUNTIME_DIRECTORY="$T/run" \
        GATBOX_CTRL="$T/ctrl" GATBOX_LED="$T/led" GATBOX_CLOCK_WAIT=0 GATBOX_NTP_SYNCED="$1" \
        GATBOX_RTC_SYSFS="$T/rtc0" GATBOX_RTC_DEV="$T/rtc0-dev" GATBOX_RTC_STAMP="$T/stamp" \
        GATBOX_NTP_FLAG="$T/ntpflag" setsid bash "$LOGGER" > "$T/journal" 2>&1 &
    local pid=$!
    sleep 0.8; [ "$3" = 1 ] && : > "$T/ntpflag"
    sleep 1.8
    kill -TERM -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    pkill -f -- "$T/bin/sigrok-cli" 2>/dev/null
    cat "$T"/log/rail_*.csv 2>/dev/null
}

now=$(date +%s); date +%s > "$T/rtc0/since_epoch"; echo "$((now - 600)) $(date -d @$((now - 600)) +%FT%T)" > "$T/stamp"

echo "RTC trusted, NTP arrives mid-session:"
csv=$(run no 0 1)
check "one session file"                    '[ "$(ls "$T"/log | wc -l)" = 1 ]'
check "header is clock=rtc with the stamp"  '[[ $(sed -n 2p <<<"$csv") == "# clock=rtc (RTC-held time; last set from NTP $(date -d @$((now - 600)) +%FT%T))" ]]'
check "exactly one clock-sync line"         '[ "$(grep -c "^# clock-sync=ntp at 20" <<<"$csv")" = 1 ]'
check "clock-sync comes after samples"      '[ "$(grep -n "^# clock-sync" <<<"$csv" | cut -d: -f1)" -gt 3 ]'
check "samples logged"                      '[ "$(grep -c ",V,DC AUTO," <<<"$csv")" -ge 10 ]'
check "journal says not waiting for NTP"    'grep -q "clock from the RTC" "$T/journal"'

echo "RTC reset, timesyncd flag already there at start:"
echo 13 > "$T/rtc0/since_epoch"
csv=$(run no 1 0)
check "header is clock=unverified"          '[[ $(sed -n 2p <<<"$csv") == "# clock=unverified ("* ]]'
check "no clock-sync line"                  '! grep -q "^# clock-sync" <<<"$csv"'

echo "NTP synced at start:"
csv=$(run yes 0 1)
check "header is clock=ntp"                 '[ "$(sed -n 2p <<<"$csv")" = "# clock=ntp" ]'
check "no clock-sync line"                  '! grep -q "^# clock-sync" <<<"$csv"'

echo "raillog clock end-to-end: $pass passed, $fail failed"
[ "$fail" = 0 ]
