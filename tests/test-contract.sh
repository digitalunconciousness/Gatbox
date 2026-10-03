#!/usr/bin/env bash
# GATBOX's end of contract v1: the vendored copy is unchanged, the uids the hub expects are
# reproducible here, every field a rail_session carries has a source in the report JSON, and
# report.verdict gives one answer for every state.
#
# The contract is owned by the hub (arcade-tracker/contract/v1) and vendored to
# docs/contract/v1. Two copies of anything drift, so each repository checks its own against
# the shared CHECKSUMS.
#   bash tests/test-contract.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
C="$REPO/docs/contract/v1"
REPORT="$REPO/tools/gatbox-rail-report"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
py() { PYTHONPATH="$REPO/backend" python3 -c "$1"; }

echo "the vendored contract:"
check "docs/contract/v1 exists with its examples" \
  '[ -f "$C/README.md" ] && [ -f "$C/CHECKSUMS" ] && [ -d "$C/examples" ]'
check "every file matches CHECKSUMS (no drift from the hub's copy)" \
  'cd "$C" && grep -v "^#" CHECKSUMS | grep -v "^$" | sha256sum -c --quiet -'
check "CHECKSUMS covers every file present" \
  '[ "$(cd "$C" && find . -type f ! -name CHECKSUMS | wc -l)" = "$(grep -cv "^#\|^$" "$C/CHECKSUMS")" ]'

echo "the uid rules, computed here and compared with the hub's examples:"
# If these disagree, every item GATBOX sends is a row the hub cannot match again.
check "rail_session uid reproduces the example" 'py "
import hashlib, json
r = json.load(open(\"$C/examples/ingest-request.json\"))
pub = r[\"device\"]
s = next(i for i in r[\"items\"] if i[\"kind\"] == \"rail_session\")
fn = s[\"file\"]
want = hashlib.sha256(f\"{pub}|rail_session|{fn}\".encode()).hexdigest()[:32]
assert s[\"uid\"] == want, (s[\"uid\"], want)
"'
check "reading uids reproduce, with epoch at exactly 3 places" 'py "
import hashlib, json
r = json.load(open(\"$C/examples/ingest-request.json\"))
pub = r[\"device\"]
s = next(i for i in r[\"items\"] if i[\"kind\"] == \"rail_session\")
fn = s[\"file\"]
for d in s[\"readings\"]:
    ep = d[\"epoch\"]
    want = hashlib.sha256(f\"{pub}|reading|{fn}|{ep:.3f}\".encode()).hexdigest()[:32]
    assert d[\"uid\"] == want, (ep, d[\"uid\"], want)
"'
check "six places would be a different uid (the rule has teeth)" 'py "
import hashlib
f, e, p = \"rail_20260925_021402.csv\", 1790000000.0, \"a1b2c3d4e5f6\"
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:32]
assert h(f\"{p}|reading|{f}|{e:.3f}\") != h(f\"{p}|reading|{f}|{e:.6f}\")
"'

echo "report.verdict, the one definition:"
check "no window (bench, free) has no verdict"  'py "
from gatboxweb import report
assert report.verdict({}) is None and report.verdict({\"window\": None}) is None
"'
check "held, all session / whenever the board was on" 'py "
from gatboxweb import report
w = {\"window\": {\"lo\": 4.75, \"hi\": 5.25}}
assert report.verdict(w)[\"state\"] == \"held\"
assert report.verdict(w)[\"detail\"] == \"all session\"
assert report.verdict({**w, \"power_cycles\": [{}]})[\"detail\"] == \"whenever the board was on\"
"'
check "an excursion is 'left'"                  'py "
from gatboxweb import report
v = report.verdict({\"window\": {\"lo\": 1, \"hi\": 2}, \"excursions\": [{}, {}]})
assert v[\"state\"] == \"left\" and v[\"detail\"] == \"2 excursions\", v
"'
check "real over-voltage beats an excursion"    'py "
from gatboxweb import report
v = report.verdict({\"window\": {\"lo\": 1, \"hi\": 2}, \"alarm_hi\": 5.775,
                    \"over_voltage\": [{\"suspect\": False}], \"excursions\": [{}]})
assert v[\"state\"] == \"over\" and \"above 5.775 V\" in v[\"detail\"], v
"'
check "all-suspect over-voltage still held, and says so" 'py "
from gatboxweb import report
v = report.verdict({\"window\": {\"lo\": 1, \"hi\": 2}, \"alarm_hi\": 5.775,
                    \"over_voltage\": [{\"suspect\": True}] * 3})
assert v[\"state\"] == \"held\", v
assert \"3 suspect readings set aside\" in v[\"detail\"], v
assert v[\"suspect\"] == 3
"'
check "state, title and css stay in step"       'py "
from gatboxweb import report
for state, (title, css) in report.VERDICTS.items():
    assert state in (\"held\", \"left\", \"over\") and title.isupper() and css in (\"ok\", \"warn\", \"bad\")
assert len(report.VERDICTS) == 3
"'

echo "a rail_session's fields all have a source in the report:"
mk() { local f=$1; shift; { echo "iso_time,epoch,value,unit,flags,uptime_s"
    while [ "$1" != -- ]; do echo "$1"; shift; done; shift
    local i=0; for r in "$@"; do printf '2026-09-21T00:%02d:%02d,%s,%s,%s\n' $((i / 120)) $((i / 2 % 60)) "$((1790000000 + i / 2)).$((i % 2 * 5))" "$r" "$((100 + i / 2)).$((i % 2 * 5))"; i=$((i + 1)); done; } > "$f"; }
rows=(); for i in $(seq 12); do rows+=("5.01,V,DC AUTO"); done
rows+=("6.10,V,DC AUTO" "6.20,V,DC AUTO"); for i in $(seq 6); do rows+=("5.02,V,DC AUTO"); done
mk "$T/s.csv" "# clock=rtc (RTC-held time; last set from NTP 2026-09-20T23:00:00)" "# mode=VDC" \
   "# profile=rail-5v" "# window=4.9..5.1 source=machine:widget-wars" "# alarm_hi=5.61" \
   "# machine=widget-wars" -- "${rows[@]}"
python3 "$REPORT" "$T/s.csv" --json --no-plot > "$T/s.json" 2>"$T/s.err" || { echo "  FAIL  report run: $(cat "$T/s.err")"; fail=$((fail+1)); }

check "every contract field maps to a report field" 'py "
import json
from gatboxweb import report
J = json.load(open(\"$T/s.json\"))
J[\"verdict\"] = report.verdict(J)
# The hub stores these and recomputes none of them, so each must be here to be sent.
ses, prof, win, pw = J[\"session\"], J[\"profile\"], J[\"window\"], J[\"powered\"]
for d, keys in ((ses, (\"start\", \"end\", \"duration_s\", \"samples\", \"rate\", \"timebase\")),
                (prof, (\"id\", \"label\", \"kind\")),
                (win, (\"lo\", \"hi\", \"unit\", \"source\")),
                (pw, (\"mean\", \"min\", \"max\", \"readings\", \"in_window_pct\"))):
    missing = [k for k in keys if k not in d]
    assert not missing, missing
for k in (\"alarm_hi\", \"machine\", \"clock\", \"mode_key\", \"over_voltage\",
          \"excursions\", \"power_cycles\", \"gaps\", \"ol_events\", \"marks\", \"verdict\"):
    assert k in J, k
assert J[\"machine\"] == \"widget-wars\"
assert J[\"verdict\"][\"state\"] == \"over\", J[\"verdict\"]
assert J[\"clock\"][\"source\"] == \"rtc\"
"'
check "the reducer's default is the ceiling the contract names" 'py "
import inspect, re
from gatboxweb import sessions
# The contract says <=2000 readings per session because that is what this reducer produces.
# If its default moves and the contract does not, the two numbers quietly disagree.
default = inspect.signature(sessions.samples).parameters[\"max_points\"].default
assert default == 2000, default
readme = open(\"$C/README.md\", encoding=\"utf-8\").read()
assert re.search(r\"readings per session \\s*\\|\\s*2000\", readme), \"README no longer says 2000\"
"'
check "the reducer keeps a lone spike (what the ceiling is safe to apply to)" 'py "
import json
J = json.load(open(\"$T/s.json\"))
# The two 6.1/6.2 V samples out of 20 are the point: a reduction that averaged buckets
# would lose them, and the contract leans on them surviving.
peak = max(o[\"peak\"] for o in J[\"over_voltage\"])
assert abs(peak - 6.2) < 1e-6, peak
"'

echo "contract: $pass passed, $fail failed"
[ "$fail" = 0 ]
