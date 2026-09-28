#!/usr/bin/env bash
# Unit tests for gatbox-raillog's session clock label (ntp / rtc / unverified), through --clock-label.
# Fakes the RTC sysfs, /dev/rtc0, the gatbox-rtc-sync stamp and NTP state with the logger's test-only env
# overrides. Runs as a normal user and touches nothing outside a temp dir.
#   bash tests/test-clock-label.sh [path/to/gatbox-raillog]
set -u
LOGGER=$(readlink -f "${1:-$(dirname "$0")/../backend/gatbox-raillog}")
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/rtc0"; : > "$T/rtc0-dev"
pass=0; fail=0

# case <name> <expect-prefix> <ntp yes|no> <rtc since_epoch|-> <stamp "epoch iso"|-> [dev-readable 1|0]
case_() {
    local name=$1 want=$2 ntp=$3 rtc=$4 stamp=$5 dev=${6:-1} got
    rm -f "$T/rtc0/since_epoch" "$T/stamp"
    [ "$rtc" = - ] || echo "$rtc" > "$T/rtc0/since_epoch"
    [ "$stamp" = - ] || echo "$stamp" > "$T/stamp"
    chmod "$([ "$dev" = 1 ] && echo 644 || echo 000)" "$T/rtc0-dev"
    got=$(GATBOX_NTP_SYNCED=$ntp GATBOX_RTC_SYSFS="$T/rtc0" GATBOX_RTC_DEV="$T/rtc0-dev" GATBOX_RTC_STAMP="$T/stamp" \
          GATBOX_NTP_FLAG="$T/none" bash "$LOGGER" --clock-label)
    if [[ $got == "$want"* ]]; then pass=$((pass + 1)); printf '  ok    %-44s %s\n' "$name" "$got"
    else fail=$((fail + 1)); printf '  FAIL  %-44s want "%s…", got "%s"\n' "$name" "$want" "$got"; fi
}

now=$(date +%s); hour_ago=$((now - 3600)); iso_ago=$(date -d @$hour_ago +%FT%T)
case_ "NTP synced (RTC reset, ignored)"            "ntp"            yes 13               -
case_ "RTC good: stamped, forward, agrees"         "rtc (RTC-held time; last set from NTP $iso_ago)" \
                                                                    no  "$now"           "$hour_ago $iso_ago"
case_ "RTC reset (reads 1970)"                     "unverified ("   no  13               "$hour_ago $iso_ago"
case_ "RTC never stamped"                          "unverified ("   no  "$now"           -
case_ "system != RTC (saved-clock guess): +2 d"    "unverified ("   no  $((now + 172800)) "$hour_ago $iso_ago"
case_ "system != RTC: RTC 1 h behind"              "unverified ("   no  $((now - 3600))  "$((now - 7200)) x"
case_ "RTC went backwards past the stamp"          "unverified ("   no  "$now"           "$((now + 600)) later"
case_ "RTC 5 s off: still trusted"                 "rtc ("          no  $((now + 5))     "$hour_ago $iso_ago"
case_ "RTC 7 s off: not trusted"                   "unverified ("   no  $((now + 7))     "$hour_ago $iso_ago"
case_ "/dev/rtc0 unreadable"                       "unverified ("   no  "$now"           "$hour_ago $iso_ago" 0
case_ "garbage stamp"                              "unverified ("   no  "$now"           "not-a-number x"
case_ "garbage since_epoch"                        "unverified ("   no  "12a4"           "$hour_ago $iso_ago"
case_ "no RTC sysfs at all"                        "unverified ("   no  -                "$hour_ago $iso_ago"

echo "clock label: $pass passed, $fail failed"
[ "$fail" = 0 ]
