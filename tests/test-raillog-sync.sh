#!/usr/bin/env bash
# gatbox-raillog flushes the session file to the card every SYNC_EVERY samples and at session end.
# A fake `sync` first on PATH records its arguments (then runs the real one); a fake sigrok-cli sends
# 45 readings and exits (= meter switched off). Temp dir only, like test-raillog-clock.sh.
#   bash tests/test-raillog-sync.sh [path/to/gatbox-raillog]
set -u
LOGGER=$(readlink -f "${1:-$(dirname "$0")/../backend/gatbox-raillog}")
T=$(mktemp -d); trap 'pkill -f -- "$T/bin/sigrok-cli" 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/led"
cat > "$T/bin/sigrok-cli" <<'EOF'
#!/usr/bin/env bash
for i in $(seq 45); do echo "P1: 5.01$((i % 10)) V DC AUTO"; sleep 0.02; done
EOF
REAL_SYNC=$(command -v sync)
cat > "$T/bin/sync" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$T/sync.log"
exec "$REAL_SYNC" "\$@"
EOF
chmod +x "$T/bin/sigrok-cli" "$T/bin/sync"; echo '[mmc0] none' > "$T/led/trigger"; : > "$T/led/brightness"; : > "$T/port"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

PATH="$T/bin:$PATH" GATBOX_DMM_PORT="$T/port" GATBOX_LOGDIR="$T/log" RUNTIME_DIRECTORY="$T/run" GATBOX_CTRL="$T/ctrl" \
    GATBOX_LED="$T/led" GATBOX_CLOCK_WAIT=0 GATBOX_NTP_SYNCED=yes setsid bash "$LOGGER" > "$T/journal" 2>&1 &
pid=$!
sleep 3                                  # 45 samples (~1.5 s), then EOF ends the session; retry waits 5 s
kill -TERM -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null

f=$(ls "$T"/log/rail_*.csv 2>/dev/null | head -1)
check "one session file"                      '[ "$(ls "$T"/log | wc -l)" = 1 ]'
check "all 45 samples written"                '[ "$(grep -c ",V,DC AUTO," "$f")" = 45 ]'
check "periodic flush at samples 20 and 40"   '[ "$(grep -cx -- "-d $f" "$T/sync.log")" -ge 2 ]'
check "3 flushes in all (20, 40, session end)" '[ "$(grep -cx -- "-d $f" "$T/sync.log")" = 3 ]'
check "session ended on EOF"                  'grep -q "stream ended" "$T/journal"'

echo "raillog sync: $pass passed, $fail failed"
[ "$fail" = 0 ]
