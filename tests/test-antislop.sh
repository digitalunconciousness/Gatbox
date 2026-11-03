#!/usr/bin/env bash
# The antislop skills: a pinned copy of someone else's rules for agents (docs/antislop/PROVENANCE.md),
# byte-identical in all three repositories, owned by GATBOX. This copy matches its CHECKSUMS, grants
# its skills no standing permissions, and equals any sibling checkout's. DESIGN.md is the owner's
# direction, which says the deck's Synthwave is deliberate.
#   bash tests/test-antislop.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
SK="$REPO/.claude/skills"
DOC="$REPO/docs/antislop"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

echo "the pinned copy:"
check "six skills, each with a SKILL.md named for its folder" '
  [ "$(ls -d "$SK"/antislop* | wc -l)" = 6 ] && (for d in "$SK"/antislop*; do
    grep -q "^name: $(basename "$d")$" "$d/SKILL.md" || exit 1; done)'
check "every file matches CHECKSUMS" 'cd "$SK" && grep -v "^#" "$DOC/CHECKSUMS" | grep -v "^$" | sha256sum -c --quiet -'
check "CHECKSUMS covers every file present" \
  '[ "$(cd "$SK" && find antislop* -type f | wc -l)" = "$(grep -cv "^#\|^$" "$DOC/CHECKSUMS")" ]'
check "no skill grants itself tools (no allowed-tools line)" '! grep -rq "allowed-tools" "$SK"/antislop*'
check "its licence and provenance are here" '[ -f "$DOC/LICENSE" ] && [ -f "$DOC/PROVENANCE.md" ]'
check "the version in PROVENANCE is the copy version" \
  'grep -q "3.2.24" "$DOC/PROVENANCE.md" && [ "$(cat "$SK/antislop/VERSION")" = "3.2.24" ]'

echo "the direction:"
check "DESIGN.md says Synthwave is deliberate and the only look" \
  'grep -q "Synthwave, and only Synthwave" "$REPO/DESIGN.md" && grep -q "deliberate" "$REPO/DESIGN.md"'
check "CLAUDE.md sends a session to DESIGN.md and the skills" \
  'grep -q "DESIGN.md" "$REPO/CLAUDE.md" && grep -q ".claude/skills" "$REPO/CLAUDE.md"'
check "RULES.md sets the mode to during" 'grep -q "The mode is \*\*during\*\*" "$REPO/docs/agents/RULES.md"'

echo "the hubs' copies, in sibling checkouts (each found one must be byte-identical):"
P=$(cd "$REPO/.." && pwd)
siblings=${CONTRACT_SIBLINGS:-"$P/cabcrawl $P/arcade-tracker $P/arcadetech-tracker $P/ArcadeTech-Tracker $P/shop-hub /opt/refs/arcade-tracker /opt/refs/shop-hub"}
found=0
for sib in $siblings; do
  [ -f "$sib/docs/antislop/CHECKSUMS" ] || continue
  found=$((found + 1))
  check "$sib has the same skills and provenance" \
    'diff -r "$DOC" "$sib/docs/antislop" >/dev/null && (for d in "$SK"/antislop*; do diff -r "$d" "$sib/.claude/skills/$(basename "$d")" >/dev/null || exit 1; done)'
done
[ "$found" = 0 ] && echo "  skip  no sibling checkout found"

echo "antislop: $pass passed, $fail failed"
[ "$fail" = 0 ]
