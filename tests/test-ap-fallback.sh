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
# Where the hotspot hold-off is remembered. Exported before anything runs the script, so no
# case here can reach the installed /var/lib path.
export GATBOX_AP_STATE="$T/apstate"
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

echo "a saved network that will not join does not flap the hotspot:"
# The password at a venue changed. The profile is still saved and the SSID is still in range,
# so every run drops the hotspot to rejoin, the join fails, the next run raises the hotspot
# again -- every two minutes, for ever, cutting anyone on GATBOX each cycle.
rm -rf "$GATBOX_AP_STATE"
printf 'HomeNetwork\n' > "$T/ssids"
printf 'HomeNetwork\ngatbox-ap\n' > "$T/saved"
state 'wlan0:connected:gatbox-ap'
run
check "the first sighting still drops the hotspot" 'called "connection down gatbox-ap"'
state 'wlan0:connected:gatbox-ap'
run
check "the second holds the hotspot instead"    '! called "connection down gatbox-ap"'
check "and says how long it is holding for"     'grep -q "holding the hotspot" "$T/out"'
# A hold-off has to expire, or fixing the password at the router would need a reboot.
mkdir -p "$GATBOX_AP_STATE"
printf '2 1000000000 HomeNetwork\n' > "$GATBOX_AP_STATE/attempts"
state 'wlan0:connected:gatbox-ap'
run
check "a hold-off from long ago has expired"    'called "connection down gatbox-ap"'
# A clock that went backwards must not hold the box off for ever -- "clock=unverified" is a
# state this box really records, and the RTC is deliberately not trickle-charged.
mkdir -p "$GATBOX_AP_STATE"
printf '2 9000000000 HomeNetwork\n' > "$GATBOX_AP_STATE/attempts"
state 'wlan0:connected:gatbox-ap'
run
check "a record from the future is not trusted" 'called "connection down gatbox-ap"'
# The network comes back: the count clears, so the next genuine need to rejoin is immediate.
rm -rf "$GATBOX_AP_STATE"
state 'wlan0:connected:gatbox-ap'
run
state 'wlan0:connected:HomeNetwork'
run
check "being on a network clears the hold-off"  '! [ -s "$GATBOX_AP_STATE/attempts" ]'
state 'wlan0:connected:gatbox-ap'
run
check "so the next sighting drops the hotspot"  'called "connection down gatbox-ap"'
# Matched by name, which may contain spaces: the record keeps the ssid last for that reason.
rm -rf "$GATBOX_AP_STATE"
printf 'My Network With Spaces\n' > "$T/ssids"
printf 'My Network With Spaces\ngatbox-ap\n' > "$T/saved"
state 'wlan0:connected:gatbox-ap'
run
check "an ssid with spaces drops the hotspot once" 'called "connection down gatbox-ap"'
state 'wlan0:connected:gatbox-ap'
run
check "and is then held off by that same name"  '! called "connection down gatbox-ap"'
# A corrupt record is not a reason to strand the box on its own hotspot.
mkdir -p "$GATBOX_AP_STATE"
printf 'nonsense\n' > "$GATBOX_AP_STATE/attempts"
state 'wlan0:connected:gatbox-ap'
run
check "a corrupt record falls back to trying"   'called "connection down gatbox-ap"'
rm -rf "$GATBOX_AP_STATE"
printf 'HomeNetwork\nNeighbour\n' > "$T/ssids"
printf 'HomeNetwork\ngatbox-ap\n' > "$T/saved"

echo "the timer is the only grace period:"
# The Task 1 ruling said GATBOX_AP_WAIT defaults to 0 and OnBootSec is the one place the
# delay lives. It shipped defaulting to 60, so the hotspot came up ~150 s after boot (not the
# ~90 s three documents claim) and every recurring run slept a minute before it would even
# look at dropping the hotspot.
check "GATBOX_AP_WAIT defaults to 0"            '! grep -q "GATBOX_AP_WAIT:-60" "$REPO/backend/gatbox-ap-fallback"'
state 'wlan0:disconnected:'
: > "$NMCLI_LOG"
start=$SECONDS
bash "$REPO/backend/gatbox-ap-fallback" > "$T/out" 2>&1
check "an unset GATBOX_AP_WAIT does not sleep"  '[ $((SECONDS - start)) -lt 5 ]'

echo "a connected state that nmcli qualifies:"
# NM reports "connected (externally)" and localizes these strings. An exact match reads that
# as offline, and with a recurring timer that no longer costs "an unnecessary hotspot" -- it
# raises the AP on wlan0 and takes the radio off a working connection.
state 'wlan0:connected (externally):HomeNetwork'
check "connected (externally) is a real network" 'real_network_up'
check "both units pin the locale"               'grep -q "LC_ALL=C" "$REPO/backend/gatbox-ap-fallback.service" && grep -q "LC_ALL=C" "$REPO/backend/gatbox-wifi.service"'
check "the ap service bounds its start"         'grep -q "TimeoutStartSec" "$REPO/backend/gatbox-ap-fallback.service"'
check "and gives the hold-off somewhere to live" 'grep -q "^StateDirectory=gatbox-ap-fallback$" "$REPO/backend/gatbox-ap-fallback.service"'

echo "the three hotspot branches, against a fake systemctl:"
# Grepping the bootstrap's source cannot see which of three branches enables what, which is
# how I7/I8/I9 all got through. This runs the branches.
mkdir -p "$T/fake"
cat > "$T/fake/systemctl" <<'STUB'
#!/usr/bin/env bash
printf '%s
' "$*" >> "${SYSTEMCTL_LOG:?}"
case "$*" in
    *"is-enabled gatbox-ap-fallback.service"*) [ -n "${AP_SERVICE_ENABLED:-}" ] ;;
    *"is-enabled gatbox-ap-fallback.timer"*)   [ -n "${AP_TIMER_ENABLED:-}" ] ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$T/fake/systemctl"
branch() {   # branch <AP value> [env...]: run just section 9 with a fake systemctl
    : > "$T/sysctl-log"
    sed -n '/^step "9\/9 optional: fallback hotspot"/,/^fi$/p' "$REPO/bootstrap/gatbox-bootstrap.sh" > "$T/sec9.sh"
    env PATH="$T/fake:$T/bin:$PATH" SYSTEMCTL_LOG="$T/sysctl-log" AP="$1" "${@:2}"         bash -c 'step() { :; }; log() { :; }; warn() { :; }; set -u; source "$0"' "$T/sec9.sh" >/dev/null 2>&1
}
# -E and an optional --now: the patterns must not break the moment a unit is also
# started rather than only enabled, which is the fix they exist to check.
said() { grep -qE -- "$1" "$T/sysctl-log"; }

branch off AP_SERVICE_ENABLED=1 AP_TIMER_ENABLED=1
check "removing the hotspot disables the timer too" 'said "disable( --now)? gatbox-ap-fallback.timer"'

branch "a-test-passphrase"
check "arming enables the timer"                'said "enable( --now)? gatbox-ap-fallback.timer"'
check "arming starts it, not just enables it"   'said -- "--now"'
check "arming clears the old boot-time service" 'said "disable gatbox-ap-fallback.service"'

# The documented upgrade path: git pull, re-run the bootstrap, no passphrase to hand.
branch "" AP_SERVICE_ENABLED=1
check "an already-armed Pi is migrated to the timer" 'said "enable( --now)? gatbox-ap-fallback.timer"'
check "and its boot-only service is disabled"   'said "disable gatbox-ap-fallback.service"'

branch "" AP_SERVICE_ENABLED=1 AP_TIMER_ENABLED=1
check "an already-migrated Pi is left alone"    '! said "enable( --now)? gatbox-ap-fallback.timer"'

echo "ap fallback: $pass passed, $fail failed"
[ "$fail" = 0 ]
