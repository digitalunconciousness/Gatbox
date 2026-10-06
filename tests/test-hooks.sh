#!/usr/bin/env bash
# .githooks/pre-push on a throwaway repo: UTC timestamps, forbidden paths, generic secret shapes and the
# owner's own patterns from a patterns.local the test supplies via GATBOX_HOOK_PATTERNS. Never touches the
# real repo, never pushes anywhere: the hook is fed the stdin git would give it.
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
check "a clean range passes"                      '[ $rc = 0 ] && [[ $out == *clean* ]]'
check "it reports the loaded pattern count"       '[[ $out == *"loaded 2 local pattern(s)"* ]]'

echo "the ssid is MySSID3.0" > notes.txt; git add notes.txt; commit -m "leak the ssid"
out=$(run_hook); rc=$?
check "an added line matching patterns.local is refused" '[ $rc = 1 ] && [[ $out == *"local site pattern"* ]]'
check "the refusal never prints the secret itself"       '[[ $out != *MySSID3.0* ]]'
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
check "a key-shaped added line is refused"        '[ $rc = 1 ] && [[ $out == *"forbidden content"* ]]'
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
check "an expression assigned to psk is not a secret" '[ $rc = 0 ] && [[ $out == *clean* ]]'
git reset -q --hard "$base"

printf 'psk=%s\n' "$VALUE" > wifi.conf; git add wifi.conf; commit -m "a keyfile"
out=$(run_hook); rc=$?
check "a bare psk= literal is refused"            '[ $rc = 1 ] && [[ $out == *"forbidden content"* ]]'
git reset -q --hard "$base"

printf '{"ssid": "Somewhere", "psk": "%s"}\n' "$VALUE" > req.json; git add req.json; commit -m "a request"
out=$(run_hook); rc=$?
check "a quoted psk literal is refused"           '[ $rc = 1 ] && [[ $out == *"forbidden content"* ]]'
git reset -q --hard "$base"

printf 'password: %s   # the bench box\n' "$VALUE" > notes.md; git add notes.md; commit -m "a note"
out=$(run_hook); rc=$?
check "a trailing comment does not hide one"      '[ $rc = 1 ] && [[ $out == *"forbidden content"* ]]'
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
check "a brand-new branch is still inspected"     '[ $rc = 1 ] && [[ $out == *"local site pattern"* ]]'

out=$(printf 'refs/heads/leaky %s refs/heads/leaky %s\n' "$ZERO" "$(git rev-parse HEAD)" | bash "$HOOK" 2>&1); rc=$?
check "a branch deletion is allowed"              '[ $rc = 0 ]'
git checkout -q main; git reset -q --hard "$base"

# Without patterns.local the generic checks must still run, with a warning.
GATBOX_HOOK_PATTERNS="$T/nope" bash -c ':' 
echo "the ssid is MySSID3.0" > notes.txt; git add notes.txt; commit -m "leak again"
out=$(GATBOX_HOOK_PATTERNS="$T/nope" run_hook); rc=$?
check "no patterns.local: warns, does not refuse the site value" '[ $rc = 0 ] && [[ $out == *"site values are NOT being checked"* ]]'
git reset -q --hard "$base"

echo "hooks: $pass passed, $fail failed"
[ "$fail" = 0 ]
