#!/usr/bin/env bash
# Check, or regenerate, a contract directory's CHECKSUMS.
#
#   bash docs/contract/checksums.sh               # check docs/contract/v1
#   bash docs/contract/checksums.sh --write       # regenerate after an edit (GATBOX only)
#   bash docs/contract/checksums.sh DIR           # check another copy, e.g. a hub's contract/v1
#
# GATBOX owns contract v1 (docs/contract/v1) and the hubs vendor it byte-identical. Regenerate
# only here, after an edit made on purpose; a mismatch anywhere else means a copy was edited,
# and the fix is to copy the files again from GATBOX.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
write=0
dir="$REPO/docs/contract/v1"
for a in "$@"; do
  case "$a" in
    --write) write=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) dir=$a ;;
  esac
done
[ -d "$dir" ] || { echo "no such directory: $dir" >&2; exit 2; }
cd "$dir" || exit 2

if [ "$write" = 1 ]; then
  tmp=$(mktemp) || exit 2
  {
    cat <<'EOF'
# sha256 of every file in this directory, CHECKSUMS itself excepted.
#
# Contract v1 is owned by GATBOX (docs/contract/v1/) and vendored byte-identical
# into each hub at contract/v1/. A test in every repository checks its copy
# against this list, and against any sibling checkout it finds. Edit GATBOX's
# copy, then regenerate there with:
#   bash docs/contract/checksums.sh --write

EOF
    find . -type f ! -name CHECKSUMS | sed 's|^\./||' | LC_ALL=C sort | while read -r f; do
      sha256sum "$f"
    done
  } > "$tmp" && cat "$tmp" > CHECKSUMS
  rm -f "$tmp"
  echo "wrote $dir/CHECKSUMS ($(grep -cv '^#\|^$' CHECKSUMS) files)"
  exit 0
fi

bad=0
grep -v '^#' CHECKSUMS | grep -v '^$' | sha256sum -c --quiet - || bad=1
listed=$(grep -v '^#' CHECKSUMS | grep -v '^$' | sed 's/^[0-9a-f]*  //' | LC_ALL=C sort)
present=$(find . -type f ! -name CHECKSUMS | sed 's|^\./||' | LC_ALL=C sort)
if [ "$listed" != "$present" ]; then
  echo "CHECKSUMS and the files present differ:" >&2
  diff <(echo "$listed") <(echo "$present") >&2
  bad=1
fi
[ "$bad" = 0 ] && echo "$dir matches CHECKSUMS"
exit "$bad"
