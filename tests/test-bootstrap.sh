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
n=$(sed -n '/^MANIFEST="/,/^"/p;/^USER_MANIFEST="/,/^"/p' "$BOOT" | grep -c '^[a-z]')
check "--extract writes all $n manifest files"  '[[ $out1 == "$n file(s) written under $T/root" ]]'
bad=0
while read -r src dst mode; do
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
bash "$BOOT" --check > "$T/check" 2>&1; rc=$?
check "--check runs without root (exit 0 or 3)" '[ $rc = 0 ] || [ $rc = 3 ]'
out=$(bash "$BOOT" 2>&1); rc=$?
check "a real run refuses without root"         '[ $rc = 1 ] && [[ $out == *"run with sudo"* ]]'

echo "--- --check on this Pi:"; sed 's/^/    /' "$T/check"
echo "bootstrap: $pass passed, $fail failed"
[ "$fail" = 0 ]
