#!/usr/bin/env bash
# GATBOX's end of contract v1: the vendored copy is unchanged, the uids the hub expects are
# reproducible here, every field a rail_session carries has a source in the report JSON, and
# report.verdict gives one answer for every state.
#
# GATBOX owns the contract (docs/contract/v1, since 2026-10-28) and each hub vendors it at
# contract/v1. Copies drift, so every repository checks its own against the shared CHECKSUMS,
# and against the copies in any sibling checkouts it finds (CONTRACT_SIBLINGS, a space-separated
# list of repo directories, overrides where it looks).
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
check "every file matches CHECKSUMS" \
  'cd "$C" && grep -v "^#" CHECKSUMS | grep -v "^$" | sha256sum -c --quiet -'
check "CHECKSUMS covers every file present" \
  '[ "$(cd "$C" && find . -type f ! -name CHECKSUMS | wc -l)" = "$(grep -cv "^#\|^$" "$C/CHECKSUMS")" ]'
check "docs/contract/checksums.sh agrees" 'bash "$REPO/docs/contract/checksums.sh" >/dev/null'
check "a regenerated CHECKSUMS is the one committed" '
  cp -r "$C" "$T/v1" && bash "$REPO/docs/contract/checksums.sh" --write "$T/v1" >/dev/null &&
  cmp -s "$C/CHECKSUMS" "$T/v1/CHECKSUMS"'
check "every roster machine has a model_key, a string or null" 'py "
import json
for m in json.load(open(\"$C/examples/roster-response.json\"))[\"machines\"]:
    assert \"model_key\" in m and (m[\"model_key\"] is None or isinstance(m[\"model_key\"], str)), m
"'

echo "the hubs' copies, in sibling checkouts (each found one must be byte-identical):"
P=$(cd "$REPO/.." && pwd)
siblings=${CONTRACT_SIBLINGS:-"$P/cabcrawl $P/arcade-tracker $P/arcadetech-tracker $P/ArcadeTech-Tracker $P/shop-hub /opt/refs/arcade-tracker /opt/refs/shop-hub"}
found=0
for sib in $siblings; do
  [ -f "$sib/contract/v1/CHECKSUMS" ] || continue
  found=$((found + 1))
  check "$sib/contract/v1 is the same as docs/contract/v1" 'diff -r "$C" "$sib/contract/v1" >/dev/null'
done
[ "$found" = 0 ] && echo "  skip  no sibling checkout found"

echo "the library's read side (docs/contract/library-v1/read), vendored by each app at contract/library-v1:"
R="$REPO/docs/contract/library-v1/read"
check "it has its README, examples and CHECKSUMS" '[ -f "$R/README.md" ] && [ -f "$R/CHECKSUMS" ] && [ -d "$R/examples" ]'
check "every file matches CHECKSUMS, and CHECKSUMS covers every file" 'bash "$REPO/docs/contract/checksums.sh" "$R" >/dev/null'
check "a regenerated CHECKSUMS is the one committed" '
  cp -r "$R" "$T/read" && bash "$REPO/docs/contract/checksums.sh" --write "$T/read" >/dev/null &&
  cmp -s "$R/CHECKSUMS" "$T/read/CHECKSUMS"'
check "the real archive reproduces every example (tests/test-archive-read.py)" 'python3 "$REPO/tests/test-archive-read.py" >/dev/null'
lfound=0
for sib in $siblings; do
  [ -f "$sib/contract/library-v1/CHECKSUMS" ] || continue
  lfound=$((lfound + 1))
  check "$sib/contract/library-v1 is the same as docs/contract/library-v1/read" 'diff -r "$R" "$sib/contract/library-v1" >/dev/null'
done
[ "$lfound" = 0 ] && echo "  skip  no sibling checkout has the library's read side yet"

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

echo "ROM images (capability roms): the example image, the files calls and the two items:"
check "the example image is what its hashes say (hex in JSON, never a .bin in git)" 'py "
import hashlib, json, zlib
i = json.load(open(\"$C/examples/rom-image.json\"))
b = bytes.fromhex(i[\"hex\"])
assert len(b) == i[\"size\"] == 32
assert hashlib.sha1(b).hexdigest() == i[\"sha1\"] and hashlib.sha256(b).hexdigest() == i[\"sha256\"]
assert format(zlib.crc32(b) & 0xFFFFFFFF, \"08x\") == i[\"crc32\"]
"'
check "the files examples describe that image, cut at 8 MiB" 'py "
import json
E = \"$C/examples/\"
i, j = json.load(open(E + \"rom-image.json\")), lambda n: json.load(open(E + n))
assert j(\"files-begin-request.json\") == {\"sha256\": i[\"sha256\"], \"size\": 32}
b = j(\"files-begin-response.json\")
assert (b[\"state\"], b[\"part_size\"], b[\"parts\"], b[\"have\"]) == (\"open\", 8 * 1024 * 1024, 1, []), b
assert j(\"files-part-response.json\")[\"have\"] == [0]
for n in (\"files-begin-response-stored.json\", \"files-finish-response.json\"):
    assert j(n) == {\"state\": \"stored\", \"sha256\": i[\"sha256\"], \"size\": 32}, n
"'
check "the README's numbers for a file are the examples' (8 MiB parts, 32 MiB files)" 'py "
import json, re
r = open(\"$C/README.md\", encoding=\"utf-8\").read()
assert \"33554432\" in r and \"8388608\" in r and \"32 MiB\" in r
assert json.load(open(\"$C/examples/files-begin-response.json\"))[\"part_size\"] == 8388608
"'
check "rom_dump and burn uids reproduce the examples (burn: created at exactly 3 places)" 'py "
import hashlib, json
r = json.load(open(\"$C/examples/rom-ingest-request.json\"))
pub = r[\"device\"]
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:32]
d = next(i for i in r[\"items\"] if i[\"kind\"] == \"rom_dump\")
b = next(i for i in r[\"items\"] if i[\"kind\"] == \"burn\")
assert d[\"uid\"] == h(f\"{pub}|rom_dump|{d[\"machine\"]}|{d[\"label\"]}|{d[\"sha1\"]}\"), d[\"uid\"]
assert b[\"uid\"] == h(f\"{pub}|burn|{b[\"machine\"]}|{b[\"image_sha1\"]}|{b[\"created\"]:.3f}\"), b[\"uid\"]
assert b[\"uid\"] != h(f\"{pub}|burn|{b[\"machine\"]}|{b[\"image_sha1\"]}|{b[\"created\"]:.6f}\"), \"the rule has teeth\"
"'
check "the items name the example image, and carry what the contract says" 'py "
import json
E = \"$C/examples/\"
img = json.load(open(E + \"rom-image.json\"))
items = json.load(open(E + \"rom-ingest-request.json\"))[\"items\"]
d = next(i for i in items if i[\"kind\"] == \"rom_dump\")
b = next(i for i in items if i[\"kind\"] == \"burn\")
assert (d[\"sha256\"], d[\"sha1\"], d[\"crc32\"], d[\"size\"]) == (img[\"sha256\"], img[\"sha1\"], img[\"crc32\"], 32)
assert (b[\"image\"], b[\"image_sha1\"], b[\"size\"]) == (img[\"sha256\"], img[\"sha1\"], 32)
assert d[\"reads\"] == 2 and d[\"identical\"] is True and d[\"clock\"][\"source\"] in (\"ntp\", \"rtc\", \"unverified\", \"unknown\")
assert b[\"state\"] in (\"verified\", \"stopped\") and \"error\" in b and \"order\" not in d and \"order\" not in b
assert len(d[\"label\"]) <= 40 and len(d[\"part\"]) <= 64 and len(b[\"part\"]) <= 64
"'
check "the replies list both items in order: created, then duplicate" 'py "
import json
E = \"$C/examples/\"
req = json.load(open(E + \"rom-ingest-request.json\"))[\"items\"]
for name, status in ((\"rom-ingest-response.json\", \"created\"), (\"rom-ingest-response-duplicate.json\", \"duplicate\")):
    r = json.load(open(E + name))
    assert r[\"received\"] == 2 and [(x[\"uid\"], x[\"kind\"], x[\"status\"]) for x in r[\"results\"]] == [(i[\"uid\"], i[\"kind\"], status) for i in req], name
"'
check "the read-back example carries the same items" 'py "
import json
E = \"$C/examples/\"
items = json.load(open(E + \"rom-ingest-request.json\"))[\"items\"]
m = json.load(open(E + \"machine-dumps-response.json\"))
assert m[\"machine\"] == \"widget-wars\" and m[\"dumps\"] == [i for i in items if i[\"kind\"] == \"rom_dump\"] and m[\"burns\"] == [i for i in items if i[\"kind\"] == \"burn\"]
"'
check "the README names the capability, the five calls and both uid rules" 'py "
r = open(\"$C/README.md\", encoding=\"utf-8\").read()
for t in (\"\\\"roms\\\"\", \"/api/v1/files\", \"/parts/<n>\", \"/finish\", \"/machines/<slug>/dumps\", \"rom_dump      sha256(\", \"burn          sha256(\"):
    assert t in r, t
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
                (prof, (\"id\", \"label\", \"kind\", \"rail\")),
                (win, (\"lo\", \"hi\", \"unit\", \"source\")),
                (pw, (\"mean\", \"min\", \"max\", \"readings\", \"in_window_pct\"))):
    missing = [k for k in keys if k not in d]
    assert not missing, missing
for k in (\"alarm_hi\", \"machine\", \"clock\", \"mode_key\", \"over_voltage\",
          \"excursions\", \"power_cycles\", \"gaps\", \"ol_events\", \"marks\", \"verdict\"):
    assert k in J, k
assert J[\"machine\"] == \"widget-wars\"
assert J[\"profile\"][\"rail\"] == \"+5V\", J[\"profile\"]
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
