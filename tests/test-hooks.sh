#!/usr/bin/env bash
# .githooks/pre-push (and scripts/privacy_check.sh, the shared check it runs) on a throwaway repo: noreply
# emails, UTC timestamps, forbidden paths, private addresses, generic secret shapes and the owner's own
# patterns from a patterns.local the test supplies via GATBOX_HOOK_PATTERNS. Never touches the real repo,
# never pushes anywhere: the hook is fed the stdin git would give it.
#   bash tests/test-hooks.sh
set -u
HOOK=$(readlink -f "${1:-$(dirname "$0")/../.githooks/pre-push}")
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

ZERO=0000000000000000000000000000000000000000
export GATBOX_HOOK_PATTERNS="$T/patterns"
printf 'MySSID[0-9.]*\nsecretplace\n' > "$T/patterns"

# A bare "remote" plus a working clone, so the hook sees a real ref pair.
git init -q --bare "$T/remote.git"
git init -q "$T/work"; cd "$T/work"
git config user.name  tester
git config user.email tester@example.invalid
git remote add origin "$T/remote.git"
commit() { TZ=UTC git commit -q --date="2026-01-01T00:00:00+0000" "$@"; }

echo base > README.md; git add README.md; commit -m "base"
git push -q origin HEAD:refs/heads/main 2>/dev/null
base=$(git rev-parse HEAD)

# Feed the hook what git feeds it, for HEAD against the pushed base.
run_hook() { printf 'refs/heads/main %s refs/heads/main %s\n' "$(git rev-parse HEAD)" "${1:-$base}" | bash "$HOOK" 2>&1; }

out=$(run_hook); rc=$?
check "nothing new to push passes"                '[ $rc = 0 ] && [[ $out == *"nothing to check"* ]]'
echo "Widget Wars needs a belt" > notes.txt; git add notes.txt; commit -m "a clean note"
out=$(run_hook); rc=$?
check "a clean range passes"                      '[ $rc = 0 ] && [[ $out == *"privacy check: clean"* ]]'
check "it reports the loaded pattern count"       '[[ $out == *"(2 pattern(s) loaded)"* ]]'
git reset -q --hard "$base"

echo "the ssid is MySSID3.0" > notes.txt; git add notes.txt; commit -m "leak the ssid"
out=$(run_hook); rc=$?
check "an added line matching patterns.local is refused" '[ $rc = 1 ] && [[ $out == *"pattern #1 in notes.txt:1"* ]]'
check "the refusal never prints the secret itself"       '[[ $out != *MySSID3.0* ]]'
check "nor the pattern that matched it"                  '[[ $out != *MySSID* ]]'
git reset -q --hard "$base"

printf '{"machines":[]}' > roster.json; mkdir -p data; git mv roster.json data/gatbox-barcade-roster.json 2>/dev/null || mv roster.json data/gatbox-barcade-roster.json
git add data/gatbox-barcade-roster.json; commit -m "add the roster"
out=$(run_hook); rc=$?
check "the roster file is refused by name"        '[ $rc = 1 ] && [[ $out == *gatbox-barcade-roster* ]]'
git reset -q --hard "$base"

printf '%%PDF-1.4 fake\n' > manual.pdf; git add manual.pdf; commit -m "add a pdf"
out=$(run_hook); rc=$?
check "a PDF is refused"                          '[ $rc = 1 ] && [[ $out == *manual.pdf* ]]'
git reset -q --hard "$base"

head -c 32 /dev/zero > dump.bin; git add dump.bin; commit -m "add a rom dump"
out=$(run_hook); rc=$?
check "a .bin is refused"                         '[ $rc = 1 ] && [[ $out == *dump.bin* ]]'
git reset -q --hard "$base"

echo "SITE values" > SITE.local.md; git add -f SITE.local.md; commit -m "add site notes"
out=$(run_hook); rc=$?
check "a *.local.md is refused"                   '[ $rc = 1 ] && [[ $out == *SITE.local.md* ]]'
git reset -q --hard "$base"

# Assembled at runtime so this test file does not itself carry a key-shaped literal
# (it is pushed through the very hook it is testing).
printf 'api%skey = "%s"\n' _ A1b2C3d4E5f6G7h8 > cfg.py; git add cfg.py; commit -m "add a key"
out=$(run_hook); rc=$?
check "a key-shaped added line is refused"        '[ $rc = 1 ] && [[ $out == *"cfg.py:1"* ]] && [[ $out != *A1b2C3d4* ]]'
git reset -q --hard "$base"

# A literal is a secret; an expression is code. A module about Wi-Fi assigns a variable called
# psk, and the generic pattern matched that -- so every branch carrying Wi-Fi code was refused.
# Values are assembled at runtime, as the api_key case above is, because this file is itself
# pushed through the hook it tests.
VALUE=Sup3rSecretValue
{ printf 'psk = body.get("psk")\n'
  printf 'psk = unescape(fields.get("P", ""))\n'
  printf 'body["psk"] = psk\n'
  printf '# nmcli necessarily receives it, as password <psk>\n'; } > code.py
git add code.py; commit -m "code that handles a key"
out=$(run_hook); rc=$?
check "an expression assigned to psk is not a secret" '[ $rc = 0 ] && [[ $out == *"privacy check: clean"* ]]'
git reset -q --hard "$base"

printf 'psk=%s\n' "$VALUE" > wifi.conf; git add wifi.conf; commit -m "a keyfile"
out=$(run_hook); rc=$?
check "a bare psk= literal is refused"            '[ $rc = 1 ] && [[ $out == *"wifi.conf:1"* ]]'
git reset -q --hard "$base"

printf '{"ssid": "Somewhere", "psk": "%s"}\n' "$VALUE" > req.json; git add req.json; commit -m "a request"
out=$(run_hook); rc=$?
check "a quoted psk literal is refused"           '[ $rc = 1 ] && [[ $out == *"req.json:1"* ]]'
git reset -q --hard "$base"

printf 'password: %s   # the bench box\n' "$VALUE" > notes.md; git add notes.md; commit -m "a note"
out=$(run_hook); rc=$?
check "a trailing comment does not hide one"      '[ $rc = 1 ] && [[ $out == *"notes.md:1"* ]] && [[ $out != *Sup3r* ]]'
git reset -q --hard "$base"

# The hotspot's address is NetworkManager's default, the same on every Pi (.githooks/privacy.conf
# allow_address); any other private address is site data. Built at runtime, like the keys above.
printf 'the hotspot is %s.%s.0.1\n' 10 42 > net.md; git add net.md; commit -m "the hotspot"
out=$(run_hook); rc=$?
check "the hotspot's documented address is allowed" '[ $rc = 0 ]'
printf 'the bench is at %s.%s.7.20\n' 192 168 > net.md; git add net.md; commit -m "a LAN address"
out=$(run_hook); rc=$?
check "a LAN address is refused"                  '[ $rc = 1 ] && [[ $out == *"net.md:1"* ]]'
git reset -q --hard "$base"

echo ok > who.txt; git add who.txt
GIT_AUTHOR_EMAIL="pat.someone@elsewhere-mail$(printf .net)" commit -m "a personal address"
out=$(run_hook); rc=$?
check "a commit by a non-noreply address is refused" '[ $rc = 1 ] && [[ $out == *"email pat.someone"* ]]'
git reset -q --hard "$base"

echo ok > tz.txt; git add tz.txt
# Etc/GMT-6 is +0600 and, unlike a city name, is not something a privacy pattern looks for.
TZ=Etc/GMT-6 git commit -q --date="2026-01-01T12:00:00+0600" -m "local timestamp"
out=$(run_hook); rc=$?
check "a non-UTC commit timestamp is refused"     '[ $rc = 1 ] && [[ $out == *"non-UTC"* ]]'
git reset -q --hard "$base"

# A new branch has no remote sha: the hook must still inspect the commits.
git checkout -q -b leaky
echo "visit secretplace today" > x.txt; git add x.txt; commit -m "new branch leak"
out=$(printf 'refs/heads/leaky %s refs/heads/leaky %s\n' "$(git rev-parse HEAD)" "$ZERO" | bash "$HOOK" 2>&1); rc=$?
check "a brand-new branch is still inspected"     '[ $rc = 1 ] && [[ $out == *"pattern #2 in x.txt:1"* ]]'

out=$(printf 'refs/heads/leaky %s refs/heads/leaky %s\n' "$ZERO" "$(git rev-parse HEAD)" | bash "$HOOK" 2>&1); rc=$?
check "a branch deletion is allowed"              '[ $rc = 0 ]'
git checkout -q main; git reset -q --hard "$base"

# Without a patterns file nothing is pushed (the shared rule since 2026-10-28; until then this hook
# only warned and ran the generic checks).
echo "Widget Wars needs a belt" > notes.txt; git add notes.txt; commit -m "a clean note"
out=$(GATBOX_HOOK_PATTERNS="$T/nope" run_hook); rc=$?
check "no patterns file: the push is refused"     '[ $rc = 1 ] && [[ $out == *MISSING* ]] && [[ $out == *REFUSED* ]]'
git reset -q --hard "$base"

# The same script, byte for byte, in the hubs' sibling checkouts when they're here.
R=$(cd "$(dirname "$HOOK")/.." && pwd)
P=$(dirname "$R")
for sib in "$P/cabcrawl" "$P/arcadetech-tracker" "$P/ArcadeTech-Tracker" "$P/shop-hub" "$P/arcade-tracker"; do
  [ -f "$sib/scripts/privacy_check.sh" ] && [ -f "$sib/.githooks/pre-push" ] || continue
  check "$sib has the same check and hook" \
    'cmp -s "$sib/scripts/privacy_check.sh" "$R/scripts/privacy_check.sh" && cmp -s "$sib/.githooks/pre-push" "$HOOK"'
done

echo "hooks: $pass passed, $fail failed"
[ "$fail" = 0 ]
