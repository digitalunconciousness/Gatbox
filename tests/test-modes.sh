#!/usr/bin/env bash
# gatbox-raillog's bash mode_key must give the same key as gatboxlib/modes.py for every unit + flags: all SI
# prefixes, OHM SIGN (U+2126) and Greek Ω, µ (U+00B5) and μ (U+03BC), OL's T prefix, flags in the unit column
# (continuity), units the UT61E never sends, plus every combination in the real logs on this Pi.
#   bash tests/test-modes.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
sed -n '/^mode_key() {/,/^}/p' "$REPO/backend/gatbox-raillog" > "$T/mode_key.sh"
source "$T/mode_key.sh"
declare -F mode_key >/dev/null || { echo "mode_key not found in gatbox-raillog"; exit 1; }

# vectors: unit<US>flags (0x1F: tab is whitespace to `read`, which would swallow an empty unit)
{
  for u in V mV µV μV kV TV A mA µA μA TA $'Ω' $'kΩ' $'MΩ' $'TΩ' $'Ω' $'kΩ' $'mΩ' \
           F pF nF µF mF TF Hz kHz MHz % "" dBV °C K S s mS; do
      for f in "" DC "DC AUTO" "AC AUTO" AC "DC DIODE" "AC DC" "DC AUTO HOLD" "AC REL" AUTO HOLD "AUTO MAX"; do
          printf '%s\037%s\n' "$u" "$f"; done; done
  for f in "" HOLD "AUTO HOLD"; do printf 'AUTO\037%s\n' "$f"; printf 'HOLD\037%s\n' "$f"; done
  for f in /var/log/gatbox/rail_*.csv; do
      [ -r "$f" ] && tr -d '\000' < "$f" | awk -F, 'NR > 1 && !/^#/ && NF >= 5 {print $4 "\037" $5}'
  done
} | sort -u > "$T/vectors"

while IFS=$'\037' read -r u f; do mode_key "$u" "$f"; printf '%s\t%s\t%s\n' "$u" "$f" "$MODE"; done < "$T/vectors" > "$T/bash"
python3 - "$REPO/backend" "$T/vectors" > "$T/py" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from gatboxlib.modes import mode
for line in open(sys.argv[2], encoding="utf-8"):
    u, f = line.rstrip("\n").split("\x1f")
    print(f"{u}\t{f}\t{mode(u, f).key}")
EOF
n=$(wc -l < "$T/vectors")
if diff "$T/bash" "$T/py" > "$T/diff"; then
    echo "  ok    bash and Python agree on all $n unit/flag combinations"
    printf '        e.g. %s\n' "$(grep -P '^T\x{2126}\tAUTO\t' "$T/bash" | tr '\t' ' ')" "$(grep -P '^\tAUTO\t' "$T/bash" | head -1 | tr '\t' ' ')"
    echo "modes: 1 passed, 0 failed"
else
    echo "  FAIL  bash and Python disagree:"; head -20 "$T/diff"; echo "modes: 0 passed, 1 failed"; exit 1
fi
