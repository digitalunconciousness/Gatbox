#!/usr/bin/env bash
# gatbox-ap-fallback's predicate and its idempotence, against a stub nmcli.
#
# The predicate has to tell "connected to a real network" from "running our own hotspot",
# because `nmcli device` reports the AP as connected either way -- so the obvious check says
# the box is online when all it is doing is talking to itself.
#   bash tests/test-ap-fallback.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

# A stub nmcli that prints whatever the case under test put in $T/device, and records the
# arguments it was called with so the raise/do-nothing behaviour can be asserted.
mkdir -p "$T/bin"
cat > "$T/bin/nmcli" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${NMCLI_LOG:?}"
case "$*" in
    *"-f DEVICE,STATE,CONNECTION device"*) cat "${NMCLI_DEVICE:?}" ;;
    *"device wifi list"*)                  cat "${NMCLI_SSIDS:-/dev/null}" ;;
    *"-f NAME connection show"*|*"-g NAME connection show"*) cat "${NMCLI_SAVED:-/dev/null}" ;;
    *"connection up gatbox-ap"*)           echo "Connection successfully activated" ;;
    *)                                      exit 0 ;;
esac
exit "${NMCLI_RC:-0}"
STUB
chmod +x "$T/bin/nmcli"
export PATH="$T/bin:$PATH" NMCLI_LOG="$T/calls" NMCLI_DEVICE="$T/device"
export NMCLI_SSIDS="$T/ssids" NMCLI_SAVED="$T/saved"
: > "$T/ssids"; : > "$T/saved"

# Source the predicate out of the script, the way tests/test-modes.sh sources mode_key.
sed -n '/^real_network_up() {/,/^}/p' "$REPO/backend/gatbox-ap-fallback" > "$T/fn.sh"
if [ ! -s "$T/fn.sh" ]; then
    echo "  FAIL  real_network_up not found in backend/gatbox-ap-fallback"
    echo "ap fallback: 0 passed, 1 failed"; exit 1
fi
# shellcheck disable=SC1091
source "$T/fn.sh"

state() { : > "$NMCLI_LOG"; printf '%s\n' "$@" > "$T/device"; }

echo "the predicate:"
state 'wlan0:connected:HomeNetwork'
check "a normal profile is a real network"      'real_network_up'
state 'wlan0:connected:gatbox-ap'
check "our own hotspot is not"                  '! real_network_up'
state 'wlan0:disconnected:' 'eth0:unavailable:'
check "nothing connected is not"                '! real_network_up'
state 'eth0:connected:Wired connection 1'
check "ethernet counts"                         'real_network_up'
state 'wlan0:connected:gatbox-ap' 'eth0:connected:Wired connection 1'
check "the hotspot plus ethernet still counts"  'real_network_up'
state 'wlan0:connecting:HomeNetwork'
check "connecting is not yet connected"         '! real_network_up'
: > "$T/device"
check "no output at all is not"                 '! real_network_up'
state 'wlan0:connected:My Network With Spaces'
check "a connection name with spaces counts"    'real_network_up'
# Not a subshell: check's counters would not survive one, so a failure here would be
# invisible -- the test would report green while asserting nothing.
state 'wlan0:connected:HomeNetwork'
export NMCLI_RC=1
check "nmcli failing is not a network"          '! real_network_up'
unset NMCLI_RC
# An empty PATH, not merely one without the stub: this machine has a real nmcli, so dropping
# the stub would have asked the host about its own network and passed or failed by accident.
check "nmcli missing entirely is not"           'PATH=/nonexistent real_network_up; [ $? != 0 ]'
export PATH="$T/bin:$PATH"

echo "the script, run repeatedly:"
# GATBOX_AP_WAIT=0 skips the boot-time grace period; the recurring run has no use for it.
run() { : > "$NMCLI_LOG"; GATBOX_AP_WAIT=0 bash "$REPO/backend/gatbox-ap-fallback" > "$T/out" 2>&1; }
called() { grep -q -- "$1" "$NMCLI_LOG"; }

state 'wlan0:disconnected:' 'eth0:unavailable:'
run
check "offline with no hotspot: raises it"      'called "connection up gatbox-ap"'

state 'wlan0:connected:gatbox-ap'
run
check "hotspot already up: does not raise it again" '! called "connection up gatbox-ap"'

state 'wlan0:connected:HomeNetwork'
run
check "a real network is up: does nothing"      '! called "connection up" && ! called "connection down"'

# The original complaint: "Reboot near known Wi-Fi to go back". Once the hotspot is up the box
# has to notice a saved network reappearing, or it stays on its own AP until someone
# power-cycles it inside a cabinet.
state 'wlan0:connected:gatbox-ap'
printf 'HomeNetwork
Neighbour
' > "$T/ssids"
printf 'HomeNetwork
gatbox-ap
' > "$T/saved"
run
check "hotspot up and a saved network in range: drops the hotspot" 'called "connection down gatbox-ap"'

printf 'Neighbour
SomeCafe
' > "$T/ssids"
run
check "hotspot up and nothing saved in range: stays up" '! called "connection down gatbox-ap"'

echo "ap fallback: $pass passed, $fail failed"
[ "$fail" = 0 ]
