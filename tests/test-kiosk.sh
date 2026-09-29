#!/usr/bin/env bash
# The 7" kiosk without touching the real desktop: gatbox-kiosk on/off state, and gatbox-kiosk-launch against a test
# copy of gatbox-web (spare port, temp state) with a fake browser that records its arguments. Covers: off = no
# launch, waits for gatbox-web, the flags, crash-restart, clearing Chromium's crash state, EXIT KIOSK and SHUT DOWN
# (local only; fake `systemctl` and `logger` first on PATH, so nothing powers off or reaches the real journal), and
# stopping screen blanking (a fake `swayidle`). Normal user; temp dirs only.
#   bash tests/test-kiosk.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); PORT=8093
cleanup() { [ -n "${lpid:-}" ] && kill "$lpid" 2>/dev/null; [ -n "${wpid:-}" ] && kill "$wpid" 2>/dev/null
            [ -f "$T/browser.pid" ] && kill "$(cat "$T/browser.pid")" 2>/dev/null
            pkill -f -- "$T/bin/" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
waitfor() { for _ in $(seq 50); do eval "$1" && return 0; sleep 0.2; done; return 1; }
mkdir -p "$T/bin" "$T/home" "$T/run" "$T/state" "$T/cache" "$T/logs"
cat > "$T/bin/fake-chromium" <<EOF
#!/usr/bin/env bash
echo \$\$ > "$T/browser.pid"
echo "\$*" >> "$T/launches"
exec sleep 300
EOF
cat > "$T/bin/systemctl" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$T/systemctl.log"
exit \$(cat "$T/systemctl.rc" 2>/dev/null || echo 0)
EOF
printf '#!/bin/sh\nexit 0\n' > "$T/bin/logger"
cp "$(command -v sleep)" "$T/bin/swayidle"; chmod +x "$T/bin/fake-chromium" "$T/bin/systemctl" "$T/bin/logger"
export PATH="$T/bin:$PATH"
export HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config" XDG_DATA_HOME="$T/home/.local/share" XDG_RUNTIME_DIR="$T/run"
export GATBOX_KIOSK_LAUNCH="$REPO/backend/gatbox-kiosk-launch" GATBOX_KIOSK_BASE="http://127.0.0.1:$PORT" \
       GATBOX_KIOSK_BROWSER="$T/bin/fake-chromium" GATBOX_KIOSK_WAIT=15
K="$REPO/tools/gatbox-kiosk"

echo "gatbox-kiosk:"
check "default is on"                        '[[ $(bash "$K" status) == "kiosk on "* ]]'
bash "$K" off >/dev/null
check "off writes the state"                 '[ "$(cat "$T/home/.config/gatbox/kiosk")" = off ]'
check "start refuses while off"              '! bash "$K" start >/dev/null'
check "launcher does nothing while off"      'bash "$GATBOX_KIOSK_LAUNCH" | grep -q "plain desktop"; [ ! -e "$T/launches" ]'
bash "$K" on >/dev/null
check "on writes the state"                  '[ "$(cat "$T/home/.config/gatbox/kiosk")" = on ]'

echo "launcher:"
# the launcher stops the user's swayidle; don't let the test touch a real one
pgrep -u "$(id -u)" -x swayidle >/dev/null && { echo "  a real swayidle is running (screen blanking on): skipping the launcher tests"; exit 1; }
mkdir -p "$T/home/.local/share/gatbox-kiosk/Default"
echo '{"profile":{"exit_type":"Crashed","exited_cleanly":false}}' > "$T/home/.local/share/gatbox-kiosk/Default/Preferences"
"$T/bin/swayidle" 300 & sw=$!
bash "$GATBOX_KIOSK_LAUNCH" > "$T/launch.log" 2>&1 & lpid=$!
sleep 1.5
check "waits for gatbox-web before the browser" '[ ! -e "$T/launches" ]'
check "stopped screen blanking (swayidle)"   '! kill -0 $sw 2>/dev/null'
GATBOX_WEB_PORT=$PORT STATE_DIRECTORY="$T/state" CACHE_DIRECTORY="$T/cache" GATBOX_LOGDIR="$T/logs" \
    MPLCONFIGDIR="$T/cache/mpl" python3 "$REPO/backend/gatbox-web" > "$T/web.log" 2>&1 & wpid=$!
waitfor '[ -s "$T/launches" ]'
check "browser started once gatbox-web answered" '[ "$(wc -l < "$T/launches")" = 1 ]'
args=$(head -1 "$T/launches")
for f in --kiosk --ozone-platform=wayland --no-first-run --password-store=basic --overscroll-history-navigation=0 \
         --hide-crash-restore-bubble --disable-pinch "--user-data-dir=$T/home/.local/share/gatbox-kiosk"; do
    check "flag $f"                          '[[ " $args " == *" $f "* ]]'
done
check "opens the dashboard, /dash/"          '[[ $args == *" http://127.0.0.1:$PORT/dash/" ]]'
check "crash state cleared before launch"    'grep -q "\"exited_cleanly\":true" "$T/home/.local/share/gatbox-kiosk/Default/Preferences" && grep -q "\"exit_type\":\"Normal\"" "$T/home/.local/share/gatbox-kiosk/Default/Preferences"'
kill "$(cat "$T/browser.pid")"; waitfor '[ "$(wc -l < "$T/launches")" = 2 ]'
check "browser restarted after it died"      '[ "$(wc -l < "$T/launches")" = 2 ]'

echo "EXIT KIOSK:"
lan=$(hostname -I | awk '{print $1}')
check "controls shown to the Pi itself"      'curl -fs "http://127.0.0.1:$PORT/" | grep -q "data-act=\"exit\"" && curl -fs "http://127.0.0.1:$PORT/" | grep -q "data-act=\"shutdown\""'
check "controls hidden from the network"     '! curl -fs "http://$lan:$PORT/" | grep -q "data-act="'
check "exit from the network: 403"           '[ "$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://$lan:$PORT/kiosk/exit")" = 403 ]'
check "shutdown from the network: 403"       '[ "$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://$lan:$PORT/kiosk/shutdown")" = 403 ]'
check "network attempts left the state alone" '[ "$(curl -fs "http://127.0.0.1:$PORT/kiosk/state")" = "{\"exit_at\": 0, \"shutdown_at\": 0}" ]'
check "exit from the Pi: 204"                '[ "$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$PORT/kiosk/exit")" = 204 ]'
waitfor '! kill -0 $lpid 2>/dev/null'
check "launcher closed the browser and exited" '! kill -0 $lpid 2>/dev/null && ! kill -0 "$(cat "$T/browser.pid")" 2>/dev/null'
check "kiosk still on for the next login"    '[ "$(cat "$T/home/.config/gatbox/kiosk")" = on ]'
check "old URLs still answer"                'for u in / /font/x /kiosk/state; do curl -s -o /dev/null -w "%{http_code} " "http://127.0.0.1:$PORT$u"; done | grep -q "^200 404 200 $"'
check "no poweroff so far"                   '[ ! -e "$T/systemctl.log" ]'

echo "SHUT DOWN:"
bash "$GATBOX_KIOSK_LAUNCH" > "$T/launch2.log" 2>&1 & lpid=$!
n0=$(wc -l < "$T/launches"); waitfor '[ "$(wc -l < "$T/launches")" -gt $n0 ]'
check "shutdown from the Pi: 204"            '[ "$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$PORT/kiosk/shutdown")" = 204 ]'
waitfor '[ -s "$T/systemctl.log" ]'
check "launcher ran systemctl poweroff"      '[ "$(cat "$T/systemctl.log")" = poweroff ]'
waitfor '! kill -0 $lpid 2>/dev/null'
check "browser closed, launcher done"        '! kill -0 $lpid 2>/dev/null && ! kill -0 "$(cat "$T/browser.pid")" 2>/dev/null'
echo 1 > "$T/systemctl.rc"; : > "$T/systemctl.log"          # now a poweroff that fails
bash "$GATBOX_KIOSK_LAUNCH" > "$T/launch3.log" 2>&1 & lpid=$!
n0=$(wc -l < "$T/launches"); waitfor '[ "$(wc -l < "$T/launches")" -gt $n0 ]'
n1=$(wc -l < "$T/launches"); curl -s -o /dev/null -X POST "http://127.0.0.1:$PORT/kiosk/shutdown"
waitfor '[ -s "$T/systemctl.log" ] && [ "$(wc -l < "$T/launches")" -gt $n1 ]'
check "failed poweroff: the kiosk comes back" '[ "$(wc -l < "$T/launches")" -gt $n1 ] && kill -0 $lpid 2>/dev/null && grep -q "poweroff failed" "$T/launch3.log"'
kill "$lpid" 2>/dev/null; wait "$lpid" 2>/dev/null

echo "kiosk: $pass passed, $fail failed"
[ "$fail" = 0 ]
