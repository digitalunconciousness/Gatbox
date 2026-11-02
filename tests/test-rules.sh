#!/usr/bin/env bash
# The shared agent rules: docs/agents/RULES.md matches its CHECKSUMS, every repository's
# CLAUDE.md points at it, and the hubs' copies in sibling checkouts are byte-identical. GATBOX
# owns the rules; cabcrawl and the tech tracker vendor them at docs/agents/.
#   bash tests/test-rules.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
A="$REPO/docs/agents"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

echo "the rules:"
check "docs/agents has RULES.md and CHECKSUMS" '[ -f "$A/RULES.md" ] && [ -f "$A/CHECKSUMS" ]'
check "every file matches CHECKSUMS" 'cd "$A" && grep -v "^#" CHECKSUMS | grep -v "^$" | sha256sum -c --quiet -'
check "CHECKSUMS covers every file present" \
  '[ "$(cd "$A" && find . -type f ! -name CHECKSUMS | wc -l)" = "$(grep -cv "^#\|^$" "$A/CHECKSUMS")" ]'
check "CLAUDE.md sends a session to the rules first" 'grep -q "docs/agents/RULES.md" "$REPO/CLAUDE.md"'

echo "the hubs' copies, in sibling checkouts (each found one must be byte-identical):"
P=$(cd "$REPO/.." && pwd)
siblings=${CONTRACT_SIBLINGS:-"$P/cabcrawl $P/arcade-tracker $P/arcadetech-tracker $P/ArcadeTech-Tracker $P/shop-hub /opt/refs/arcade-tracker /opt/refs/shop-hub"}
found=0
for sib in $siblings; do
  [ -f "$sib/docs/agents/CHECKSUMS" ] || continue
  found=$((found + 1))
  check "$sib/docs/agents is the same as docs/agents" 'diff -r "$A" "$sib/docs/agents" >/dev/null'
done
[ "$found" = 0 ] && echo "  skip  no sibling checkout found"

echo "rules: $pass passed, $fail failed"
[ "$fail" = 0 ]
