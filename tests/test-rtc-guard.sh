#!/usr/bin/env bash
# The bootstrap's RTC charging guard: rtc_guard_cfg on sample config.txt files, and the env checks that
# refuse GATBOX_RTC_BATTERY and any GATBOX_RTC_CHARGE other than ML2020 (they run before the root check,
# so this works as a normal user and changes nothing).
#   bash tests/test-rtc-guard.sh [path/to/gatbox-bootstrap.sh]
set -u
BOOT=$(readlink -f "${1:-$(dirname "$0")/../bootstrap/gatbox-bootstrap.sh}")
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

# pull the function out of the bootstrap, as written
sed -n '/^rtc_guard_cfg() {/,/^}/p' "$BOOT" > "$T/guard.sh"; source "$T/guard.sh"
check "rtc_guard_cfg found in the bootstrap"  'declare -F rtc_guard_cfg >/dev/null'

printf '[all]\ndtparam=rtc_bbat_vchg=3000000\nusb_max_current_enable=1\n' > "$T/c1"
rtc_guard_cfg "$T/c1" 2026-09-28
check "single line: commented, dated"         'grep -qx "# GATBOX 2026-09-28: RTC trickle charging disabled (the cell is not rechargeable). Was: dtparam=rtc_bbat_vchg=3000000" "$T/c1"'
check "single line: no active rtc_bbat left"  '! grep -qE "^[^#]*rtc_bbat_vchg" "$T/c1"'
check "single line: other lines untouched"    'grep -qx "usb_max_current_enable=1" "$T/c1" && [ "$(head -1 "$T/c1")" = "[all]" ]'

printf '[pi5]\n  dtparam=audio=on,rtc_bbat_vchg=3000000,i2c_arm=on\n' > "$T/c2"
rtc_guard_cfg "$T/c2" 2026-09-28
check "combined line: other params kept"      'grep -qx "dtparam=audio=on,i2c_arm=on" "$T/c2"'
check "combined line: charging gone"          '! grep -qE "^[^#]*rtc_bbat_vchg" "$T/c2"'

printf '# dtparam=rtc_bbat_vchg=3000000\ndtparam=spi=on\n' > "$T/c3"; cp "$T/c3" "$T/c3.orig"
rtc_guard_cfg "$T/c3" 2026-09-28
check "already-commented line: unchanged"     'cmp -s "$T/c3" "$T/c3.orig"'

printf 'dtparam=spi=on\n' > "$T/c4"; cp "$T/c4" "$T/c4.orig"; rtc_guard_cfg "$T/c4" 2026-09-28
check "no rtc line: file unchanged"           'cmp -s "$T/c4" "$T/c4.orig"'

cp "$T/c1" "$T/c1.once"; rtc_guard_cfg "$T/c1" 2026-09-29
check "idempotent: second run changes nothing" 'cmp -s "$T/c1" "$T/c1.once"'

out=$(GATBOX_RTC_BATTERY=1 bash "$BOOT" 2>&1); rc=$?
check "GATBOX_RTC_BATTERY=1 refused"          '[ $rc = 1 ] && [[ $out == *"GATBOX_RTC_BATTERY was removed"* ]]'
out=$(GATBOX_RTC_CHARGE=CR2032 bash "$BOOT" 2>&1); rc=$?
check "GATBOX_RTC_CHARGE=CR2032 refused"      '[ $rc = 1 ] && [[ $out == *"accepts only ML2020"* ]]'
out=$(GATBOX_RTC_CHARGE=ml2020 bash "$BOOT" 2>&1); rc=$?
check "GATBOX_RTC_CHARGE=ml2020 refused (exact)" '[ $rc = 1 ] && [[ $out == *"accepts only ML2020"* ]]'
out=$(GATBOX_RTC_CHARGE=ML2020 bash "$BOOT" 2>&1); rc=$?
check "GATBOX_RTC_CHARGE=ML2020 passes the check (then needs root)" '[[ $out == *"run with sudo"* ]]'

echo "rtc guard: $pass passed, $fail failed"
[ "$fail" = 0 ]
