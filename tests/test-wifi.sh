#!/usr/bin/env bash
# gatbox-wifi, the root helper that actually changes the network, against a stub nmcli.
#
# gatbox-web cannot do this itself: it runs DynamicUser with ProtectSystem=strict and has no
# D-Bus, so nmcli fails there. It writes a request to a spool and this job acts on it, the
# shape gatbox-dump already uses for the T48.
#   bash tests/test-wifi.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
WIFI="$REPO/backend/gatbox-wifi"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

KEY='hunter2-not-a-real-key'
SPOOL="$T/spool"; mkdir -p "$SPOOL"

# A stub nmcli that records its arguments one per line (so an SSID containing a space stays
# one argument) and exits with whatever the case under test asked for.
cat > "$T/nmcli" <<'STUB'
#!/usr/bin/env bash
{ printf '%s\n' "--- $#"; printf '%s\n' "$@"; } >> "${NMCLI_LOG:?}"
case "$*" in
    *"DEVICE,STATE,CONNECTION device"*) cat "${NMCLI_DEVICE:-/dev/null}" ;;
    *"device wifi list"*) cat "${NMCLI_SSIDS:-/dev/null}" ;;
    *"connection show"*)  cat "${NMCLI_SAVED:-/dev/null}" ;;
esac
exit "${NMCLI_RC:-0}"
STUB
chmod +x "$T/nmcli"
printf '#!/usr/bin/env bash\necho ran >> "%s"\n' "$T/ap-called" > "$T/ap"; chmod +x "$T/ap"
: > "$T/ap-called"
export GATBOX_NMCLI="$T/nmcli" GATBOX_WIFI_SPOOL="$SPOOL" NMCLI_LOG="$T/calls"
export GATBOX_AP_FALLBACK="$T/ap"

req() { : > "$NMCLI_LOG"; printf '%s' "$1" > "$SPOOL/request.json"; chmod 600 "$SPOOL/request.json"; }
# req_py ACTION SSID [PSK]: the same, without threading someone else's quote through bash.
# Every argument is passed explicitly, including an empty one: "$@" plus a trailing path made
# the path land in the psk slot whenever a psk was omitted, and those requests were never
# written at all.
req_py() { : > "$NMCLI_LOG"; python3 -c '
import json, sys
action, ssid, psk, path = sys.argv[1:5]
d = {"action": action, "at": 1790000000.0}
if ssid:
    d["ssid"] = ssid
if psk != "<none>":
    d["psk"] = psk
json.dump(d, open(path, "w"))
' "$1" "${2-}" "${3-<none>}" "$SPOOL/request.json"; chmod 600 "$SPOOL/request.json"; }
run() { : > "$T/ap-called"; "$WIFI" > "$T/out" 2>&1; echo $? > "$T/rc"; }
status() { python3 -c "
import json,sys
try: print(json.load(open('$SPOOL/status.json')).get(sys.argv[1], '<missing>'))
except Exception as e: print('<no status:', e, '>')
" "$1"; }
# What nmcli was handed, one argument per line, for exact matching.
got() { grep -qxF -- "$1" "$NMCLI_LOG"; }

echo "a join that works:"
req "{\"action\":\"join\",\"ssid\":\"BenchNet\",\"psk\":\"$KEY\",\"at\":1790000000.0}"
run
check "exits 0"                                 '[ "$(cat "$T/rc")" = 0 ]'
check "asked nmcli to connect"                  'got "connect"'
check "with the ssid as its own argument"       'got "BenchNet"'
check "and the key"                             'got "$KEY"'
check "status says joined"                      '[ "$(status state)" = joined ]'
check "status says ok"                          '[ "$(status ok)" = True ]'
check "status names the ssid"                   '[ "$(status ssid)" = BenchNet ]'

check "the request file is gone"                '[ ! -e "$SPOOL/request.json" ]'

echo "the key goes to nmcli and nowhere else:"
# The call log has it, because nmcli necessarily receives it. Nothing else may.
check "not in the status file"                  '! grep -qF -- "$KEY" "$SPOOL/status.json"'
check "not on stdout or stderr"                 '! grep -qF -- "$KEY" "$T/out"'
check "not left anywhere in the spool"          '! grep -rqF -- "$KEY" "$SPOOL"'

echo "a join that fails:"
req_py join BenchNet wrong-key
NMCLI_RC=1 run
check "exits non-zero"                          '[ "$(cat "$T/rc")" != 0 ]'
check "the request file is gone even so"        '[ ! -e "$SPOOL/request.json" ]'
# A wrong key must cost thirty seconds, not a trip to wherever the box is.
check "the hotspot is brought back"             'grep -q . "$T/ap-called"'
check "and the status says so"                  '[ "$(status state)" = restored-hotspot ]'
check "ok is false"                             '[ "$(status ok)" = False ]'
check "the wrong key is not in the status"      '! grep -qF -- "wrong-key" "$SPOOL/status.json"'

echo "nmcli hanging, and nmcli failing loudly:"
# TimeoutExpired.__str__ is "Command '<argv>' timed out after N seconds" -- and the argv
# holds `password <psk>`. That string became status.json's detail and went straight out of
# GET /api/wifi. Reachable whenever NetworkManager wedges, which is exactly when someone
# goes looking at the status.
cat > "$T/nmcli-hang" <<'STUB'
#!/usr/bin/env bash
{ printf '%s\n' "--- $#"; printf '%s\n' "$@"; } >> "${NMCLI_LOG:?}"
sleep 30
STUB
chmod +x "$T/nmcli-hang"
req_py join BenchNet "$KEY"
GATBOX_NMCLI="$T/nmcli-hang" GATBOX_WIFI_CONNECT_TIMEOUT=1 run
check "a hung nmcli does not strand the run"    '[ -f "$SPOOL/status.json" ]'
check "and the key is not in the status"        '! grep -qF -- "$KEY" "$SPOOL/status.json"'
check "nor on stdout"                           '! grep -qF -- "$KEY" "$T/out"'
check "the failure is still described"          '[ "$(status ok)" = False ] && [ -n "$(status detail)" ]'

# nmcli's own stderr is written verbatim too. If a version of it ever echoes the key back,
# scrubbing by value is the only thing that helps.
cat > "$T/nmcli-echo" <<'STUB'
#!/usr/bin/env bash
{ printf '%s\n' "--- $#"; printf '%s\n' "$@"; } >> "${NMCLI_LOG:?}"
echo "Error: 802-11-wireless-security.psk: '$4' is not valid" >&2
exit 4
STUB
chmod +x "$T/nmcli-echo"
req_py join BenchNet "$KEY"
GATBOX_NMCLI="$T/nmcli-echo" run
check "a key echoed back by nmcli is scrubbed"  '! grep -qF -- "$KEY" "$SPOOL/status.json"'

echo "a join while sitting on our own hotspot:"
# The primary scenario -- box in a cabinet, no network, hotspot up, someone walks over with
# a QR -- and the design doc prescribed "bring the hotspot down, then join". That clause was
# dropped with no ruling. A wlan0 in AP mode cannot scan on this hardware, so a join issued
# while the AP is up is the one most likely to fail.
printf 'wlan0:connected:gatbox-ap\n' > "$T/device"
req_py join BenchNet "$KEY"
NMCLI_DEVICE="$T/device" run
check "the hotspot is taken down before connecting" \
    'python3 -c "
import sys
lines = open(\"$NMCLI_LOG\").read().splitlines()
down = next((i for i, l in enumerate(lines) if l == \"down\"), None)
conn = next((i for i, l in enumerate(lines) if l == \"connect\"), None)
sys.exit(0 if down is not None and conn is not None and down < conn else 1)"'
printf 'wlan0:disconnected:\n' > "$T/device"
req_py join BenchNet "$KEY"
NMCLI_DEVICE="$T/device" run
check "and not when the hotspot is not up"      '! got "down"'

echo "an open network:"
req_py join CafeOpen ""
run
check "no password argument at all"             '! got "password"'
check "still joined"                            '[ "$(status state)" = joined ]'

echo "an ssid that looks like an option:"
req_py join "-x --help" "$KEY"
run
check "reaches nmcli intact, as one argument"   'got "-x --help"'

echo "an ssid with a space and a quote:"
req_py join "The Cafe's Wi-Fi" "$KEY"
run
check "survives as one argument"                'got "The Cafe'"'"'s Wi-Fi"'

echo "forget:"
req_py forget OldArcade
run
check "deletes the connection"                  'got "delete" && got "OldArcade"'
check "status says forgotten"                   '[ "$(status state)" = forgotten ]'
req_py forget NeverSaved
NMCLI_RC=10 run
check "an unknown ssid is reported, not a crash" '[ "$(status ok)" = False ] && ! grep -q Traceback "$T/out"'

echo "scanning for what is in range:"
# The web process cannot run nmcli either, so in-range networks come from here too.
printf 'HomeNetwork
CafeOpen

HomeNetwork
' > "$T/ssids"
req_py scan ""
NMCLI_SSIDS="$T/ssids" run
check "writes scan.json"                        '[ -f "$SPOOL/scan.json" ]'
check "with the ssids, deduplicated, no blanks" '[ "$(python3 -c "
import json; print(json.load(open(\"$SPOOL/scan.json\"))[\"ssids\"])")" = "['"'"'CafeOpen'"'"', '"'"'HomeNetwork'"'"']" ]'
check "and no key anywhere in it"               '! grep -qF -- "$KEY" "$SPOOL/scan.json"'
# The dashboard also needs to know what is already saved, and it cannot ask nmcli either --
# so the same scan records it. Names only: there is no path anywhere that reads a key back.
printf 'HomeNetwork\ngatbox-ap\nOldArcade\n' > "$T/saved"
req_py scan ""
NMCLI_SSIDS="$T/ssids" NMCLI_SAVED="$T/saved" run
check "writes saved.json"                       '[ -f "$SPOOL/saved.json" ]'
check "with the saved profiles, ours excluded"  '[ "$(python3 -c "
import json; print(json.load(open(\"$SPOOL/saved.json\"))[\"ssids\"])")" = "['"'"'HomeNetwork'"'"', '"'"'OldArcade'"'"']" ]'
check "and no key in it"                        '! grep -qF -- "$KEY" "$SPOOL/saved.json"'

echo "the hotspot restore reports what actually happened:"
# "restored-hotspot" was written whenever the script *ran*, including when it exited
# non-zero because no gatbox-ap profile exists. The box is then off its old network with no
# hotspot, while the status and the runbook both promise it came back.
printf '#!/usr/bin/env bash\necho "no such connection" >&2\nexit 1\n' > "$T/ap-bad"; chmod +x "$T/ap-bad"
req_py join BenchNet wrong-key
NMCLI_RC=1 GATBOX_AP_FALLBACK="$T/ap-bad" run
check "a failed restore is not reported as one" '[ "$(status state)" != restored-hotspot ]'
check "and it says the hotspot did not come back" '[ "$(status state)" = failed ]'

echo "forget refuses to delete the way back in:"
# Loopback-only means "any process on the Pi", not "only the FORGET button". The AP profile
# is the box's recovery path and its passphrase is deliberately not in the repo.
req_py forget gatbox-ap
run
check "the hotspot profile cannot be forgotten"  '[ "$(cat "$T/rc")" != 0 ] && ! got "delete"'
check "and it says why"                          '[ "$(status ok)" = False ]'

echo "nmcli failing is not a list of networks:"
# (stdout + stderr) was parsed as SSIDs, so an error line became a joinable network with a
# JOIN button, and overwrote a good list.
cat > "$T/nmcli-err" <<'STUB'
#!/usr/bin/env bash
{ printf '%s\n' "--- $#"; printf '%s\n' "$@"; } >> "${NMCLI_LOG:?}"
echo "Error: NetworkManager is not running." >&2
exit 8
STUB
chmod +x "$T/nmcli-err"
printf 'GoodNetwork\n' > "$T/ssids"; printf 'GoodNetwork\n' > "$T/saved"
req_py scan ""; NMCLI_SSIDS="$T/ssids" NMCLI_SAVED="$T/saved" run        # a good scan first
req_py scan ""; GATBOX_NMCLI="$T/nmcli-err" run                         # then a failing one
check "an error line is not offered as a network" '! grep -q "NetworkManager is not running" "$SPOOL/scan.json"'
check "and the previous good list is not destroyed" 'grep -q GoodNetwork "$SPOOL/scan.json"'

echo "nmcli terse output is escaped:"
# nmcli -t backslash-escapes : and \ inside values, so "Cafe:Wifi" arrives as "Cafe\:Wifi"
# and the JOIN that follows sends the backslash to nmcli, which matches nothing.
printf 'Cafe\\:Wifi\nPlain\n' > "$T/ssids"; printf 'Cafe\\:Wifi\n' > "$T/saved"
req_py scan ""; NMCLI_SSIDS="$T/ssids" NMCLI_SAVED="$T/saved" run
check "an ssid with a colon is not left escaped" '! grep -q "Cafe\\\\:Wifi" "$SPOOL/scan.json"'

echo "a request that is not usable:"
printf 'not json at all' > "$SPOOL/request.json"; run
check "malformed: reported, no traceback"       '! grep -q Traceback "$T/out" && grep -q "request" "$T/out"'
check "malformed: the file is removed"          '[ ! -e "$SPOOL/request.json" ]'
rm -f "$SPOOL/request.json"; run
check "missing: exits without a traceback"      '! grep -q Traceback "$T/out"'
req_py reboot-everything ""; run
check "an unknown action is refused"            '[ "$(cat "$T/rc")" != 0 ] && grep -qi "unknown action" "$T/out"'
check "and its request is removed"              '[ ! -e "$SPOOL/request.json" ]'

echo "wifi: $pass passed, $fail failed"
[ "$fail" = 0 ]
