#!/usr/bin/env bash
# gatbox-rail-report (M4c): header window/profile/machine/limit, powered vs off, power cycles, over-voltage with
# suspect autorange glitches, excursions, marks, --json. The 09-25 Gauntlet Legends file is the regression fixture
# (a copy; never the original). Synthetic files cover the new header format. Plots go to a temp dir.
#   bash tests/test-report.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd); REPORT="$REPO/tools/gatbox-rail-report"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }
rep() { python3 "$REPORT" "$@" --plot "$T/plot.png"; }
jq_() { python3 -c "import json,sys; d=json.load(sys.stdin); print(*($1))"; }   # space-separated values

FX=/var/log/gatbox/rail_20260925_021402.csv
if [ -r "$FX" ]; then
    cp "$FX" "$T/fx.csv"; sha=$(sha256sum < "$FX")
    echo "09-25 fixture (Gauntlet Legends):"
    out=$(rep "$T/fx.csv")
    check "old header lines unchanged"         'grep -qx "samples:   10251   (2.00 S/s)   timebase: monotonic uptime" <<<"$out" && grep -qx "stats:     mean +4.9362 V   min -0.0370   max +20.2840   p-p 20321.0 mV" <<<"$out" && grep -qx "window:    \[4.75, 5.25\] V" <<<"$out"'
    check "no profile line → the +5V default"  'grep -q "^profile:   +5V rail (rail-5v): the default" <<<"$out"'
    check "limit 5.775 V"                      'grep -qx "limit:     over-voltage above 5.775 V (magnitude)" <<<"$out"'
    check "powered mean 5.01–5.03 V, 100% in"  'grep -qE "^powered:   mean \+5\.0(1|2)[0-9]{2} V" <<<"$out" && grep -qx "in window: 100.00% of powered readings" <<<"$out"'
    check "4 power cycles, first at the start" 'grep -q "^POWER CYCLES (4)" <<<"$out" && grep -q "02:14:02  down for    61.9s.*at the start of the log" <<<"$out"'
    check "3 over-voltage readings, all suspect" 'grep -q "^OVER-VOLTAGE (3): above 5.775 V; 3 suspect" <<<"$out" && [ "$(grep -c "suspect: its neighbours read" <<<"$out")" = 3 ]'
    check "no excursions while powered"        'grep -qx "EXCURSIONS: none. The rail held the window whenever the board was on." <<<"$out"'
    gl=$(rep "$T/fx.csv" --lo 4.90 --hi 5.10)
    check "at the GL spec: limit 5.61, 100% in" 'grep -qx "limit:     over-voltage above 5.61 V (magnitude)" <<<"$gl" && grep -qx "in window: 100.00% of powered readings" <<<"$gl"'
    js=$(rep "$T/fx.csv" --json)
    check "--json: 3 suspect OV, 4 cycles"      '[ "$(jq_ "sum(o[\"suspect\"] for o in d[\"over_voltage\"]), len(d[\"power_cycles\"]), d[\"window\"][\"source\"][:7]" <<<"$js")" = "3 4 default" ]'
    check "fixture untouched"                   '[ "$(sha256sum < "$FX")" = "$sha" ]'
else
    echo "  skip  09-25 fixture (not on this machine)"
fi

mk() {   # mk <file> <header lines…> -- <value,unit,flags…>: 2 readings/s from 00:00:00
    local f=$1; shift; { echo "iso_time,epoch,value,unit,flags,uptime_s"
    while [ "$1" != -- ]; do echo "$1"; shift; done; shift
    local i=0; for r in "$@"; do printf '2026-09-21T00:%02d:%02d,%s,%s,%s\n' $((i / 120)) $((i / 2 % 60)) "$((1790000000 + i / 2)).$((i % 2 * 5))" "$r" "$((100 + i / 2)).$((i % 2 * 5))"; i=$((i + 1)); done; } > "$f"; }

echo "Gauntlet Legends header, real over-voltage, marks:"
rows=(); for i in $(seq 20); do rows+=("5.01,V,DC AUTO"); done
rows+=("5.20,V,DC AUTO" "5.21,V,DC AUTO" "6.10,V,DC AUTO" "6.20,V,DC AUTO" "5.02,V,DC AUTO"); for i in $(seq 10); do rows+=("5.02,V,DC AUTO"); done
mk "$T/gl.csv" "# clock=rtc (RTC-held time; last set from NTP 2026-09-20T23:00:00)" "# mode=VDC" "# profile=rail-5v" \
   "# window=4.9..5.1 source=machine:gauntlet-legends" "# alarm_hi=5.61" "# machine=gauntlet-legends" \
   "# mark-before-start=2026-09-20T23:59:59,1789999999.0,99.0,dashboard,before" -- "${rows[@]}"
echo "# mark=2026-09-21T00:00:11,1790000011.2,111.2,scan,it just crashed" >> "$T/gl.csv"
out=$(rep "$T/gl.csv")
check "window from the machine spec"         'grep -qx "window:    \[4.9, 5.1\] V" <<<"$out" && grep -q "window from machine:gauntlet-legends" <<<"$out"'
check "machine + limit lines"                'grep -qx "machine:   gauntlet-legends" <<<"$out" && grep -qx "limit:     over-voltage above 5.61 V (magnitude)" <<<"$out"'
check "clock=rtc shown as a note, no warning" 'grep -q "^note:      clock=rtc" <<<"$out" && ! grep -qi "warning.*clock" <<<"$out"'
check "2-sample over-voltage is real (not suspect)" 'grep -q "^OVER-VOLTAGE (1): above 5.61 V:$" <<<"$out" && grep -q "peak +6.200 V   (2 samples)$" <<<"$out"'
check "excursion covers 5.20 → 6.20 V"        'grep -q "^EXCURSIONS (1)" <<<"$out" && grep -q "worst +6.200 V   (4 samples)" <<<"$out"'
check "marks listed, before-start tagged"     'grep -q "^MARKS (2):" <<<"$out" && grep -q "dashboard  before   (before this file started)" <<<"$out" && grep -q "scan       it just crashed$" <<<"$out"'
check "--json marks + machine"               '[ "$(rep "$T/gl.csv" --json | jq_ "len(d[\"marks\"]), d[\"machine\"], d[\"over_voltage\"][0][\"suspect\"]")" = "2 gauntlet-legends False" ]'
check "--no-plot: no PNG anywhere, plot null" '[ "$(cd "$T" && python3 "$REPORT" "$T/gl.csv" --json --no-plot | jq_ "d[\"plot\"], len(d[\"over_voltage\"])")" = "None 1" ] && [ ! -e "$T/gl.png" ]'

echo "-5V rail:"
mk "$T/neg.csv" "# clock=ntp" "# mode=VDC" "# profile=rail-neg5v" "# window=-5.25..-4.75 source=profile" "# alarm_hi=5.775" -- \
   "-5.01,V,DC AUTO" "-5.02,V,DC AUTO" "-5.90,V,DC AUTO" "-5.95,V,DC AUTO" "-5.01,V,DC AUTO" "0.00,mV,DC AUTO" "0.01,mV,DC AUTO" "-5.00,V,DC AUTO" "-5.01,V,DC AUTO"
out=$(rep "$T/neg.csv")
check "over-voltage by magnitude"            'grep -q "^OVER-VOLTAGE (1): above 5.775 V:$" <<<"$out" && grep -q "peak -5.950 V" <<<"$out"'
check "0 V is a power cycle"                  'grep -q "^POWER CYCLES (1)" <<<"$out"'

echo "ripple (bench, your ceiling) and free:"
mk "$T/rip.csv" "# clock=ntp" "# mode=VAC" "# profile=ripple" "# window=0..0.15 source=user" "# alarm_hi=none" -- \
   "40.0,mV,AC AUTO" "0.0,mV,AC AUTO" "210.0,mV,AC AUTO" "50.0,mV,AC AUTO"
out=$(rep "$T/rip.csv")
check "user window, no limit"                 'grep -qx "window:    \[0.0, 0.15\] V" <<<"$out" && grep -qx "limit:     none" <<<"$out"'
check "bench: no power cycles, 0 V is fine"   '! grep -q "POWER CYCLES" <<<"$out" && grep -q "^EXCURSIONS (1)" <<<"$out" && grep -q "worst +0.210 V" <<<"$out"'
mk "$T/free.csv" "# clock=ntp" "# mode=OHM" "# profile=free" "# window=none source=profile" "# alarm_hi=none" -- \
   $'1.2,kΩ,AUTO' $'1.3,kΩ,AUTO'
out=$(rep "$T/free.csv")
check "free: no window, no OV/excursion sections" 'grep -qx "window:    none (Free)" <<<"$out" && ! grep -q "^OVER-VOLTAGE\|^EXCURSIONS" <<<"$out" && grep -qx "mode:      Resistance" <<<"$out"'

echo "report: $pass passed, $fail failed"
[ "$fail" = 0 ]
