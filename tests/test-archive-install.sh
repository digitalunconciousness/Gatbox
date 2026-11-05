#!/usr/bin/env bash
# shellcheck disable=SC2016
# archive/install.sh against a temp prefix (ARCHIVE_PREFIX, ARCHIVE_NO_SYSTEM): what it installs, that a re-run changes
# nothing, that a changed file is put back, that the config is never overwritten, the private-network drop-in, that the
# installed tree runs on its own, and that it refuses a data directory that isn't a mount. No root, no network.
#   bash tests/test-archive-install.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
INSTALL="$REPO/archive/install.sh"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
run() { ARCHIVE_PREFIX="$T/root" ARCHIVE_NO_SYSTEM=1 bash "$INSTALL" "$@" 2>&1; }
L="$T/root/usr/local/lib/gatbox"

echo "a fresh target:"
out=$(run --check)
check "--check lists every file and the config, and writes nothing" '[ ! -e "$T/root" ] && echo "$out" | grep -q "install    /usr/local/bin/gatbox-archive" && echo "$out" | grep -q "create     /etc/gatbox-archive/archive.conf"'
out=$(run)
check "installs the library, the file store, the server and the launcher" '[ -f "$L/gatboxlib/library.py" ] && [ -f "$L/gatboxlib/filestore.py" ] && [ -f "$L/gatboxarchive/server.py" ] && [ -x "$T/root/usr/local/bin/gatbox-archive" ]'
check "the unit goes in, mode 644; the launcher 755" '[ "$(stat -c %a "$T/root/etc/systemd/system/gatbox-archive.service")" = 644 ] && [ "$(stat -c %a "$T/root/usr/local/bin/gatbox-archive")" = 755 ]'
check "the config is created from the example" 'cmp -s "$REPO/archive/archive.conf.example" "$T/root/etc/gatbox-archive/archive.conf"'
check "nothing from tests or docs leaks in: only the manifest's files" '[ "$(find "$L" -type f | wc -l)" = 9 ]'
S="$T/root/usr/local/share/gatbox-archive/static"
check "the pages' stylesheet and script are installed, mode 644, and no font was fetched in a test run" 'cmp -s "$REPO/archive/static/archive.css" "$S/archive.css" && cmp -s "$REPO/archive/static/archive.js" "$S/archive.js" && [ "$(stat -c %a "$S/archive.css")" = 644 ] && [ ! -d "$S/fonts" ]'

echo "a re-run:"
before=$(find "$T/root" -type f -printf '%p %T@\n' | sort | md5sum)
out=$(run)
after=$(find "$T/root" -type f -printf '%p %T@\n' | sort | md5sum)
check "changes nothing and says so" '[ "$before" = "$after" ] && echo "$out" | grep -q "nothing to change"'
check "--check agrees" 'run --check | grep -q "nothing to change"'

echo "a changed file, and the config:"
echo "# edited" >> "$L/gatboxarchive/api.py"
check "--check sees it" 'run --check | grep -q "install    /usr/local/lib/gatbox/gatboxarchive/api.py"'
run >/dev/null
check "a re-run puts it back" 'cmp -s "$REPO/archive/gatboxarchive/api.py" "$L/gatboxarchive/api.py"'
echo "ARCHIVE_PEOPLE=her@example.test" >> "$T/root/etc/gatbox-archive/archive.conf"
run >/dev/null
check "the config is never overwritten" 'grep -q "ARCHIVE_PEOPLE=her@example.test" "$T/root/etc/gatbox-archive/archive.conf"'

echo "the pages' fonts (a local copy stands in for google/fonts):"
FSRC="$T/fontsrc"; mkdir -p "$FSRC/ofl/x"
printf 'font-one' > "$FSRC/ofl/x/One.ttf"; printf 'licence' > "$FSRC/ofl/x/OFL.txt"
printf 'ofl/x/One.ttf One.ttf %s\nofl/x/OFL.txt One-OFL.txt %s\n' "$(sha256sum < "$FSRC/ofl/x/One.ttf" | cut -d' ' -f1)" \
    "$(sha256sum < "$FSRC/ofl/x/OFL.txt" | cut -d' ' -f1)" > "$T/fonts.table"
frun() { ARCHIVE_FONTS_BASE="file://$FSRC" ARCHIVE_FONTS_TABLE="$T/fonts.table" run "$@"; }
check "--check lists the fonts it would fetch" 'frun --check | grep -q "fetch      .*static/fonts/One.ttf"'
frun >/dev/null
check "they are fetched, checked against their checksums and installed with the licence beside them" \
    'cmp -s "$FSRC/ofl/x/One.ttf" "$S/fonts/One.ttf" && cmp -s "$FSRC/ofl/x/OFL.txt" "$S/fonts/One-OFL.txt" && [ "$(stat -c %a "$S/fonts/One.ttf")" = 644 ]'
check "a re-run changes nothing" 'frun | grep -q "nothing to change"'
check "an offline re-run (the source is gone) still succeeds, since everything is installed" \
    'ARCHIVE_FONTS_BASE="file://$T/nowhere" ARCHIVE_FONTS_TABLE="$T/fonts.table" run | grep -q "nothing to change"'
echo "tampered" >> "$S/fonts/One.ttf"
check "a font that no longer matches its checksum is seen and put back" 'frun --check | grep -q "fetch      .*One.ttf" && frun >/dev/null && cmp -s "$FSRC/ofl/x/One.ttf" "$S/fonts/One.ttf"'
rm -f "$S/fonts/One.ttf"; printf 'swapped-in' > "$FSRC/ofl/x/One.ttf"
out=$(frun); rc=$?
check "a download that doesn't match the checksum is never installed (no font, no half file), and the run still finishes" \
    '[ ! -e "$S/fonts/One.ttf" ] && [ ! -e "$S/fonts/One.ttf.part" ] && [ "$rc" = 0 ] && echo "$out" | grep -q "couldn.t fetch the fonts"'
printf 'font-one' > "$FSRC/ofl/x/One.ttf"
frun >/dev/null

echo "the private listener's network:"
DROP="$T/root/etc/systemd/system/gatbox-archive.service.d/private.conf"
check "no ARCHIVE_PRIVATE_ALLOW: no drop-in" '[ ! -e "$DROP" ]'
sed -i 's|^ARCHIVE_PRIVATE_ALLOW=.*|ARCHIVE_PRIVATE_ALLOW=192.0.2.0/24|' "$T/root/etc/gatbox-archive/archive.conf"
check "--check sees the drop-in it would write" 'run --check | grep -q "drop-in    .*192.0.2.0/24"'
run >/dev/null
check "set: a drop-in that allows that network" 'grep -q "^IPAddressAllow=192.0.2.0/24$" "$DROP"'
run >/dev/null
check "and a re-run leaves it alone" 'run --check | grep -q "nothing to change"'
sed -i 's|^ARCHIVE_PRIVATE_ALLOW=.*|ARCHIVE_PRIVATE_ALLOW=|' "$T/root/etc/gatbox-archive/archive.conf"
run >/dev/null
check "emptied: the drop-in is removed" '[ ! -e "$DROP" ]'

echo "archive.conf and the service's restart (the service reads the conf only when it starts):"
CONFF="$T/root/etc/gatbox-archive/archive.conf"
STAMPF="$T/root/var/lib/gatbox-archive/conf.sha256"
conf_hash() { sha256sum < "$CONFF" | cut -d' ' -f1; }
run >/dev/null
check "a settled install remembers the conf the service was last started with" '[ "$(cat "$STAMPF")" = "$(conf_hash)" ]'
check "and --check has nothing to say" 'run --check | grep -q "nothing to change"'
touch "$CONFF"
check "touching the conf without changing it asks for nothing" 'run --check | grep -q "nothing to change"'
echo "ARCHIVE_PRIVATE_BIND=192.0.2.50:8081" >> "$CONFF"
check "an edit to the conf: --check says the service would restart, and why" 'run --check | grep -q "restart    gatbox-archive (archive.conf changed"'
out=$(run)
check "a run takes it up and does not claim there was nothing to change" '[ "$(cat "$STAMPF")" = "$(conf_hash)" ] && ! echo "$out" | grep -q "nothing to change"'
check "then it is settled again" 'run --check | grep -q "nothing to change"'
rm -f "$STAMPF"
check "no stamp (an install from before this existed): --check says it would restart, once" 'run --check | grep -q "restart    gatbox-archive"'
run >/dev/null
check "and the run leaves a stamp, so the next one is quiet" '[ "$(cat "$STAMPF")" = "$(conf_hash)" ] && run --check | grep -q "nothing to change"'

echo "the installed tree on its own (no checkout):"
check "imports and reports its library" 'cd "$T" && ARCHIVE_DATA="$T/data" python3 -c "
import sys; sys.path.insert(0, \"$L\")
from gatboxarchive.cli import main
sys.exit(main([\"status\"]))" | grep -q "library"'
check "a token can be minted from it" 'cd "$T" && ARCHIVE_DATA="$T/data" python3 -c "
import sys; sys.path.insert(0, \"$L\")
from gatboxarchive.cli import main
sys.exit(main([\"token\", \"add\", \"deck\", \"--role\", \"device\"]))" | grep -q "gbx_"'

echo "the unit and the real thing's guards:"
if command -v systemd-analyze >/dev/null 2>&1; then
    v=$(cd "$T" && systemd-analyze verify "$REPO/archive/gatbox-archive.service" 2>&1 | grep -v "is not executable\|Command /usr/local/bin\|User gatbox-archive\|Group gatbox-archive\|EnvironmentFile" )
    check "systemd-analyze finds nothing to complain about in the unit" '[ -z "$v" ]'
else echo "  skip  no systemd-analyze"; fi
check "the unit never lets the archive call out or see the system" 'grep -q "^IPAddressDeny=any" "$REPO/archive/gatbox-archive.service" && grep -q "^ProtectSystem=strict" "$REPO/archive/gatbox-archive.service" && grep -q "^NoNewPrivileges=yes" "$REPO/archive/gatbox-archive.service"'
out=$(ARCHIVE_PREFIX="$T/real" bash "$INSTALL" --check 2>&1)
check "without the test knobs, --check says a data directory that isn't a mount would be refused" 'echo "$out" | grep -q "REFUSE .*not a mount point"'
check "and a real run as a non-root user refuses before changing anything" '! ARCHIVE_PREFIX="$T/real" bash "$INSTALL" >/dev/null 2>&1 || [ "$(id -u)" = 0 ]; [ ! -e "$T/real/usr" ]'
check "an unknown option is refused" '! bash "$INSTALL" --nope >/dev/null 2>&1'

echo "archive install: $pass passed, $fail failed"
[ "$fail" = 0 ]
