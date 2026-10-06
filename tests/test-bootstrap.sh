#!/usr/bin/env bash
# The bootstrap without root: --extract installs every manifest file into a scratch root, byte-identical to the
# checkout with the right modes, and a second --extract writes nothing (the idempotence the real run relies on);
# --check runs read-only; the RTC env guards refuse before the root check (see test-rtc-guard.sh for the rest).
#   bash tests/test-bootstrap.sh [path/to/gatbox-bootstrap.sh]
set -u
BOOT=$(readlink -f "${1:-$(dirname "$0")/../bootstrap/gatbox-bootstrap.sh}")
REPO=$(cd "$(dirname "$BOOT")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

check "bash -n"                                'bash -n "$BOOT"'
out1=$(bash "$BOOT" --extract "$T/root" 2>&1)
n=$(sed -n '/^MANIFEST="/,/^"/p;/^USER_MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]' | while read -r s _ _ o; do
      [ "$o" = optional ] && [ ! -f "$REPO/$s" ] || echo; done | wc -l)
check "--extract writes all $n manifest files"  '[[ $out1 == "$n file(s) written under $T/root" ]]'
bad=0
while read -r src dst mode opt; do
    [ "$opt" = optional ] && [ ! -f "$REPO/$src" ] && continue
    cmp -s "$REPO/$src" "$T/root$dst" && [ "$(stat -c %a "$T/root$dst")" = "$mode" ] || { bad=1; echo "        mismatch: $dst"; }
done < <(sed -n '/^MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]')
while read -r src rel mode; do
    dst="/home/$(id -un)/$rel"
    cmp -s "$REPO/$src" "$T/root$dst" && [ "$(stat -c %a "$T/root$dst")" = "$mode" ] || { bad=1; echo "        mismatch: $dst"; }
done < <(sed -n '/^USER_MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]')
check "every file byte-identical, right mode"   '[ $bad = 0 ]'
out2=$(bash "$BOOT" --extract "$T/root" 2>&1)
check "second --extract writes nothing"         '[[ $out2 == "0 file(s) written under $T/root" ]]'
echo tampered >> "$T/root/usr/local/bin/gatbox-status"; chmod 700 "$T/root/usr/local/bin/gatbox-web"
out3=$(bash "$BOOT" --extract "$T/root" 2>&1)
check "changed content + mode get rewritten"     '[[ $out3 == "2 file(s) written under $T/root" ]] && cmp -s "$REPO/tools/gatbox-status" "$T/root/usr/local/bin/gatbox-status"'
# The hotspot fallback is driven by a timer, not by the service's own [Install]: it used to
# run once at boot and tell you to reboot to get back on Wi-Fi, which strands a box in a
# cabinet. The timer has to be installed and enabled, and the old enablement cleaned up.
check "MANIFEST installs the AP fallback timer"  'grep -qE "^backend/gatbox-ap-fallback\.timer +/etc/systemd/system/" "$BOOT"'
check "bootstrap enables the timer"              'grep -q "enable gatbox-ap-fallback.timer" "$BOOT"'
# Not a bare grep: the removal branch (GATBOX_AP_PSK=off) has always disabled the service, so
# that would pass without the arming branch cleaning up the old boot-time enablement at all.
check "and the arming branch cleans up the old enablement" \
    '[ "$(grep -c "disable gatbox-ap-fallback.service" "$BOOT")" -ge 2 ]'
check "the service no longer installs itself"    '! grep -q "WantedBy" "$REPO/backend/gatbox-ap-fallback.service"'
# Not the unconditional unit list: the hotspot is optional, so a Pi that deliberately has
# none must not be told every run that it is missing a unit. --check reports the timer only
# where the hotspot is armed at all.
check "--check reports the timer only when armed" \
    'grep -q "is-enabled gatbox-ap-fallback.timer" "$BOOT" && grep -q "replaces the boot-only service" "$BOOT"'

# gatbox-wifi: the root helper, its .path trigger, the spool it watches, and the group
# gatbox-web joins to write into that spool.
for f in gatbox-wifi gatbox-wifi.service gatbox-wifi.path; do
    check "MANIFEST installs $f"                 'grep -qE "^backend/$f +/" "$BOOT"'
done
check "the helper installs to sbin, 755"         'grep -qE "^backend/gatbox-wifi +/usr/local/sbin/gatbox-wifi +755" "$BOOT"'
check "bootstrap enables the .path"              'grep -q "svc gatbox-wifi.path" "$BOOT"'
check "sysusers creates the gatbox-wifi group"   'grep -q "^g gatbox-wifi" "$REPO/bootstrap/files/sysusers-gatbox.conf"'
# 2770: setgid so the dashboard's files keep the group, and not world-readable, because a
# request file in here holds a plaintext Wi-Fi key until the helper takes it.
check "tmpfiles creates the spool 2770 root:gatbox-wifi" \
    'grep -qE "^d /var/spool/gatbox-wifi +2770 +root +gatbox-wifi" "$REPO/bootstrap/files/tmpfiles-gatbox.conf"'
check "gatbox-web may write to the spool"        'grep -q "ReadWritePaths=-/var/spool/gatbox-wifi" "$REPO/backend/gatbox-web.service"'
check "gatbox-web is in the gatbox-wifi group"   'grep -q "SupplementaryGroups=gatbox-wifi" "$REPO/backend/gatbox-web.service"'
check "--check reports the spool"                'grep -q "/var/spool/gatbox-wifi" "$BOOT"'

bash "$BOOT" --check > "$T/check" 2>&1; rc=$?
check "--check runs without root (exit 0 or 3)" '[ $rc = 0 ] || [ $rc = 3 ]'
out=$(bash "$BOOT" 2>&1); rc=$?
check "a real run refuses without root"         '[ $rc = 1 ] && [[ $out == *"run with sudo"* ]]'
# A mistyped flag used to fall through to a real run: "--checksudo", from a mangled
# paste, installed files and restarted services on a live Pi instead of reporting.
for bad in --chekc --checksudo --dry-run -n extract --check=1; do
    out=$(bash "$BOOT" "$bad" 2>&1); rc=$?
    check "unknown argument '$bad' refuses (exit 2, usage)" \
          '[ $rc = 2 ] && [[ $out == *"unknown argument"* ]] && [[ $out == *"--check"* ]]'
done

echo "--- --check on this Pi:"; sed 's/^/    /' "$T/check"
echo "bootstrap: $pass passed, $fail failed"
[ "$fail" = 0 ]
