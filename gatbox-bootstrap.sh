#!/usr/bin/env bash
# gatbox-bootstrap.sh — GDD-GAT/01 · Greybard Diagnostics and Design
#
# Fresh Raspberry Pi OS Trixie (64-bit, Desktop) on a Pi 5  ->  GATBOX rail flight recorder.
# Leaves the desktop alone. Safe to re-run.
#
#   sudo bash gatbox-bootstrap.sh                                   # base + DMM logger
#   sudo GATBOX_AP_PSK='at-least-8-chars' bash gatbox-bootstrap.sh  # + fallback "GATBOX" hotspot ('off' removes it)
#   bash gatbox-bootstrap.sh --extract DIR                          # write the payload to DIR for review, change nothing
#
# Other knobs: GATBOX_PSU_5A=0 skips the PSU_MAX_CURRENT EEPROM change.
# RTC: trickle charging stays OFF, because the J5 cell is not rechargeable. Any active rtc_bbat_vchg line in
# config.txt gets commented out. GATBOX_RTC_CHARGE=ML2020 (that exact value) is reserved for a real ML2020 cell;
# anything else is refused. GATBOX_RTC_BATTERY was removed on 2026-09-28 and is refused too.
#
# What it does:
#   1  packages: sigrok-cli, python3-matplotlib, rsync, git, curl, util-linux-extra (hwclock)
#   2  removes ModemManager (hijacks ttyUSB0) and brltty (grabs USB-serial adapters)
#   3  groups (dialout, plugdev, gpio, i2c, spi, video) for your user; enables I2C + SPI
#   4  power fix: PSU_MAX_CURRENT=5000 (EEPROM) + usb_max_current_enable=1 (config.txt); RTC charging guard
#   5  /dev/gatbox-dmm udev symlink for the PL-2303
#   6  gatbox-raillog service, gatbox-status, gatbox-rail-report, desktop menu launcher,
#      gatbox-web (phone view on port 80, Start/Stop, PDF) + its two fonts
#   7  persistent journal; gatbox-rtc-sync timer (RTC set from NTP, stamped); optional hotspot fallback

set -euo pipefail

put() { cat > "$1"; chmod "$2" "$1"; }

payload() {   # write every GATBOX file under root dir $1
    local R="${1%/}"
    install -d "$R/etc/systemd/journald.conf.d" "$R/etc/systemd/system" "$R/etc/udev/rules.d" "$R/usr/local/bin" "$R/usr/local/sbin" "$R/usr/share/applications"
    put "$R/usr/local/bin/gatbox-raillog" 755 <<'GATBOX_EOF'
#!/usr/bin/env bash
# gatbox-raillog — GDD-GAT/01 rail flight recorder (appliance mode)
#
# Waits for the UT61E chain (UT-D02 -> PL-2303 -> /dev/gatbox-dmm), logs every
# reading with wall-clock AND monotonic timestamps, and toggles the Pi's green
# ACT LED on every sample so you can see it's alive with the cabinet open.
#
#   ACT LED blinking ~1 Hz  = logging
#   ACT LED dark            = waiting (adapter unplugged, meter off, head off the IR window)
#
# One CSV per contiguous session. A file is only created when the first reading
# arrives and ends when the stream stops, so gaps between files are diagnostic.
#
#   /var/log/gatbox/rail_YYYYmmdd_HHMMSS.csv
#   iso_time,epoch,value,unit,flags,uptime_s
#   # clock=ntp | clock=rtc (...) | clock=unverified (...)     (line 2, decided at session start)
#   # clock-sync=ntp at <iso>                                   (once, if NTP arrives mid-session)
#
# uptime_s is monotonic: durations stay right even if the wall clock steps.
# Runs as root under systemd (the LED sysfs needs it).
#
#   gatbox-raillog --clock-label    print the clock label a session started now would get, and exit
#
# Clock label, first match wins:
#   ntp         the system clock is NTP-synced
#   rtc         the RTC can be trusted: gatbox-rtc-sync set it from NTP at least once (stamp file), it hasn't
#               gone backwards since (a cell that lost power reads 1970), and the system clock agrees with it
#               to 5 s, which shows the system clock came from the RTC and not from timesyncd's saved guess
#   unverified  anything else

set -u

DRIVER="${GATBOX_DMM_DRIVER:-uni-t-ut61e-ser}"
LINK="${GATBOX_DMM_PORT:-/dev/gatbox-dmm}"
LOGDIR="${GATBOX_LOGDIR:-/var/log/gatbox}"
RUNDIR="${RUNTIME_DIRECTORY:-/run/gatbox}"
# Start/Stop from the web page (gatbox-web). Flag files, polled with builtins only:
#   $CTRL/stopped        exists -> don't log (survives reboot)
#   $CTRL/start-request  newer than the current session -> end it and start a new file
CTRL="${GATBOX_CTRL:-/var/lib/gatbox-web}"
ctl_stopped() { [ -e "$CTRL/stopped" ]; }
ctl_restart() { [ "$CTRL/start-request" -nt "$RUNDIR/session-ref" ]; }
LED="${GATBOX_LED:-/sys/class/leds/ACT}"
CLOCK_WAIT="${GATBOX_CLOCK_WAIT:-90}"   # seconds to wait for NTP before logging starts
# Clock inputs. The GATBOX_RTC_* / GATBOX_NTP_* overrides are for the tests only.
RTC_SYSFS="${GATBOX_RTC_SYSFS:-/sys/class/rtc/rtc0}"
RTC_DEV="${GATBOX_RTC_DEV:-/dev/rtc0}"
RTC_STAMP="${GATBOX_RTC_STAMP:-/var/lib/gatbox/rtc-synced}"        # "<epoch> <iso>", from gatbox-rtc-sync
NTP_FLAG="${GATBOX_NTP_FLAG:-/run/systemd/timesync/synchronized}"  # timesyncd creates it once synced

# --- clock ------------------------------------------------------------------
clock_synced() {
    if [ -n "${GATBOX_NTP_SYNCED:-}" ]; then [ "$GATBOX_NTP_SYNCED" = yes ]; return; fi
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]
}
# 0 if the RTC can be trusted (see the header); sets RTC_SET_ISO to the last NTP->RTC write
rtc_trusted() {
    local rtc="" stamp_epoch="" d
    RTC_SET_ISO=""
    [ -r "$RTC_DEV" ] && [ -r "$RTC_STAMP" ] && [ -r "$RTC_SYSFS/since_epoch" ] || return 1
    read -r stamp_epoch RTC_SET_ISO < "$RTC_STAMP"
    read -r rtc < "$RTC_SYSFS/since_epoch"
    [[ $rtc =~ ^[0-9]+$ && $stamp_epoch =~ ^[0-9]+$ ]] || return 1
    (( rtc >= stamp_epoch )) || return 1                 # gone backwards: the cell lost power
    d=$(( EPOCHSECONDS - rtc ))
    (( d >= -5 && d <= 5 ))                              # the system clock came from the RTC
}
# Sets CLOCK_SRC (ntp | rtc | unverified) and CLOCK_LINE (what follows "# clock=")
clock_label() {
    if clock_synced; then
        CLOCK_SRC=ntp; CLOCK_LINE="ntp"
    elif rtc_trusted; then
        CLOCK_SRC=rtc; CLOCK_LINE="rtc (RTC-held time; last set from NTP ${RTC_SET_ISO:-?})"
    else
        CLOCK_SRC=unverified
        CLOCK_LINE="unverified (no NTP at session start - absolute times may be off; uptime_s durations are good)"
    fi
}

if [ "${1:-}" = "--clock-label" ]; then clock_label; echo "$CLOCK_LINE"; exit 0; fi

mkdir -p "$LOGDIR" "$RUNDIR"
chmod 755 "$LOGDIR"

say() { echo "gatbox-raillog: $*"; }

# --- ACT LED: take it over, give it back on exit ---------------------------
orig_trigger=""
[ -r "$LED/trigger" ] && orig_trigger=$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$LED/trigger")
led_trigger() { [ -w "$LED/trigger" ] && echo "$1" > "$LED/trigger" 2>/dev/null; }
led()         { [ -w "$LED/brightness" ] && echo "$1" > "$LED/brightness" 2>/dev/null; }
cleanup() {
    led_trigger "${orig_trigger:-mmc0}"
    rm -f "$RUNDIR/current"
    say "stopped"
    exit 0
}
trap cleanup TERM INT
led_trigger none
led 0

if clock_synced; then
    :
elif rtc_trusted; then
    say "clock from the RTC (last set from NTP $RTC_SET_ISO); not waiting for NTP"
else
    say "waiting up to ${CLOCK_WAIT}s for NTP so timestamps are real"
    for ((i = 0; i < CLOCK_WAIT; i += 3)); do clock_synced && break; sleep 3; done
    if clock_synced; then say "clock synced"
    else say "no NTP and the RTC isn't trusted yet (never set from NTP, reset, or off by >5 s); logging anyway"
    fi
fi

# --- one session: reads sigrok stdout, returns 0 if any reading was logged ---
session() {
    local F="" state=0 tag val unit rest up _ rc=0 synced=0
    # -t 5: the meter sends ~2 readings/s. When the D02 head comes off, sigrok keeps the port open
    # and just goes quiet, so 5 s of silence ends the session instead of hiding a gap in the file.
    while :; do
        read -t 5 -r tag val unit rest || { rc=$?; break; }   # >128 = timed out, 1 = sigrok exited
        if ctl_stopped || ctl_restart; then rc=-1; break; fi   # Stop / Start pressed on the web page
        if [ "$tag" != "P1:" ]; then
            [ -z "$tag" ] && continue
            if [ -n "$F" ]; then
                say "sigrok: $tag $val $unit $rest"            # mid-session chatter -> journal
            else
                echo "$tag $val $unit $rest" >> "$RUNDIR/last-error"   # pre-session -> rate-limited
            fi
            continue
        fi
        if [ -z "$F" ]; then
            F="$LOGDIR/rail_$(date +%Y%m%d_%H%M%S).csv"
            clock_label                                        # once per session; forks are fine here
            # already NTP, or timesyncd's flag was already there: no mid-session sync to report
            if [ "$CLOCK_SRC" = ntp ] || [ -e "$NTP_FLAG" ]; then synced=1; fi
            {
                echo "iso_time,epoch,value,unit,flags,uptime_s"
                echo "# clock=$CLOCK_LINE"
            } > "$F"
            chmod 644 "$F"
            echo "$F" > "$RUNDIR/current"
            rm -f "$RUNDIR/last-error"
            say "logging to $F"
        fi
        read -r up _ < /proc/uptime
        # bash-builtin time formatting + EPOCHREALTIME: no forks per sample
        printf '%(%FT%T)T,%s,%s,%s,%s,%s\n' -1 "$EPOCHREALTIME" "$val" "$unit" "$rest" "$up" >> "$F"
        # NTP arrived mid-session: say so once (a builtin file test, still no fork per sample)
        if (( ! synced )) && [ -e "$NTP_FLAG" ]; then
            printf '# clock-sync=ntp at %(%FT%T)T\n' -1 >> "$F"; synced=1
        fi
        state=$((1 - state)); led "$state"
    done
    if (( rc == -1 )); then
        say "ending session: $(ctl_stopped && echo Stop || echo Start) pressed on the web page"
        pkill -f -- "--driver $DRIVER:conn=$PORT"
    elif (( rc > 128 )); then
        say "no reading for 5 s (D02 head off the IR window, or meter off); ending session"
        pkill -f -- "--driver $DRIVER:conn=$PORT"
    fi
    rm -f "$RUNDIR/current"
    [ -n "$F" ]
}

# --- main loop ----------------------------------------------------------------
misses=0
while :; do
    if ctl_stopped; then
        say "stopped from the web page; waiting for Start"
        led 0
        while ctl_stopped; do sleep 1; done
        say "started from the web page"
        misses=0
    fi
    if [ ! -e "$LINK" ]; then
        say "waiting for $LINK (PL-2303 adapter)"
        until [ -e "$LINK" ]; do led 0; sleep 2; done
        misses=0
    fi
    PORT=$(readlink -f "$LINK")
    : > "$RUNDIR/last-error"
    : > "$RUNDIR/session-ref"     # mtime = when this attempt began; see ctl_restart

    # stdbuf: line-buffered, so samples hit the file as they happen (matters if power dies)
    # --samples, not --continuous: --continuous stops at once when stdin is not a terminal
    # (systemd gives it /dev/null). 1e9 samples at ~2/s is ~15 years.
    if stdbuf -oL sigrok-cli --driver "$DRIVER:conn=$PORT" -O analog --samples 1000000000 2>&1 | session; then
        if ctl_stopped || ctl_restart; then
            led 0; sleep 1        # let the killed sigrok release the port
            continue
        fi
        say "stream ended (meter off, head knocked off the IR window, or adapter pulled); retrying"
        misses=0
    else
        if (( misses % 60 == 0 )); then
            say "adapter $PORT present but no readings: meter on? V DC selected? D02 head on the IR window?" \
                "sigrok: $(tr '\n' ' ' < "$RUNDIR/last-error" 2>/dev/null)"
        fi
        misses=$((misses + 1))
    fi
    led 0
    sleep 5
done
GATBOX_EOF
    put "$R/usr/local/bin/gatbox-status" 755 <<'GATBOX_EOF'
#!/usr/bin/env bash
# gatbox-status — one-screen health check for the GATBOX rail logger.
#
#   gatbox-status        snapshot: service, adapter, session, clock, power, disk, network
#   gatbox-status -f     snapshot, then follow the live readings (Ctrl-C to stop)
#
# Re-runs itself under sudo (vcgencmd PMIC reads and the journal need it).

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

LOGDIR="${GATBOX_LOGDIR:-/var/log/gatbox}"
RUNDIR=/run/gatbox
M=$'\e[38;5;199m'; C=$'\e[36m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'
ok()   { printf '  %s●%s %-8s %s\n' "$G" "$N" "$1" "$2"; }
warn() { printf '  %s●%s %-8s %s\n' "$Y" "$N" "$1" "$2"; }
bad()  { printf '  %s●%s %-8s %s\n' "$R" "$N" "$1" "$2"; }
latest() { ls -1t "$LOGDIR"/rail_*.csv 2>/dev/null | head -n 1; }

printf '\n%sGDD-GAT/01%s  rail logger  %s%s  %s%s\n\n' "$M" "$N" "$C" "$(hostname)" "$(date '+%F %T %Z')" "$N"

# service
svc=$(systemctl is-active gatbox-raillog 2>/dev/null)
if [ "$svc" = active ]; then ok service "gatbox-raillog running"
else bad service "gatbox-raillog is '$svc'  ->  sudo systemctl status gatbox-raillog"; fi

# adapter
if [ -e /dev/gatbox-dmm ]; then ok adapter "/dev/gatbox-dmm -> $(readlink -f /dev/gatbox-dmm)"
else bad adapter "no /dev/gatbox-dmm: PL-2303 unplugged?  (lsusb | grep -i prolific)"; fi

# session
cur=$(cat "$RUNDIR/current" 2>/dev/null)
if [ -n "$cur" ] && [ -f "$cur" ]; then
    n=$(grep -vc '^#' "$cur"); n=$((n - 1))
    age=$(( $(date +%s) - $(stat -c %Y "$cur") ))
    last=$(tail -n 1 "$cur" | cut -d, -f3,4 | tr ',' ' ')
    if (( age <= 5 )); then ok logging "$(basename "$cur")  $n samples"
    else warn logging "$(basename "$cur")  $n samples, but last write ${age}s ago"; fi
    ok reading "$last"
else
    warn logging "no active session: meter off, not on V DC, or D02 head off the IR window"
    [ -s "$RUNDIR/last-error" ] && warn sigrok "$(tr '\n' ' ' < "$RUNDIR/last-error")"
fi

# clock: what a session started now would be labelled. Only ask a logger that knows --clock-label:
# an older one would ignore the flag and start logging (a second logger on the port).
lg="${GATBOX_RAILLOG:-/usr/local/bin/gatbox-raillog}"      # override for tests only
lbl=""
grep -q -- '--clock-label' "$lg" 2>/dev/null && lbl=$("$lg" --clock-label 2>/dev/null)
case "$lbl" in
    ntp) ok clock "NTP synced  (new sessions: clock=ntp)" ;;
    rtc*) ok clock "no NTP, but the RTC is trusted  (new sessions: clock=rtc)" ;;
    *)   warn clock "no NTP and the RTC isn't trusted: wall times may be off  (new sessions: clock=unverified)" ;;
esac

# RTC + backup cell on J5. Charging must stay OFF: the fitted cell is not rechargeable.
rtc=/sys/class/rtc/rtc0
if [ -r "$rtc/since_epoch" ]; then
    d=$(( $(cat "$rtc/since_epoch") - $(date +%s) ))
    stamp=$(cut -d' ' -f2 /var/lib/gatbox/rtc-synced 2>/dev/null)
    batt=$(vcgencmd pmic_read_adc BATT_V 2>/dev/null | sed -n 's/.*=\([0-9.]*\)V.*/\1/p')
    info="RTC-system ${d}s, last set from NTP ${stamp:-never}${batt:+, cell $(printf '%.2f' "$batt") V}"
    chg=$(cat "$rtc/charging_voltage" 2>/dev/null)
    if [ -n "$chg" ] && [ "$chg" != 0 ]; then
        v=$(awk -v u="$chg" 'BEGIN {printf "%.2f", u / 1e6}')
        bad rtc "CHARGING at $v V, but the cell is not rechargeable: re-run gatbox-bootstrap.sh (it comments out rtc_bbat_vchg in config.txt), then reboot.  $info"
    elif [ -z "$chg" ]; then
        warn rtc "charging state unreadable (older firmware?).  $info"
    elif [ -z "$stamp" ] || (( d > 5 || d < -5 )); then
        warn rtc "charging OFF.  $info"
    else
        ok rtc "charging OFF.  $info"
    fi
else
    warn rtc "no RTC at $rtc"
fi

# power
thr=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
ext=$(vcgencmd pmic_read_adc EXT5V_V 2>/dev/null | sed -n 's/.*=\([0-9.]*\)V.*/\1/p')
[ -n "$ext" ] && ext=$(printf '  EXT5V %.2f V' "$ext")
case "$thr" in
    0x0) ok power "no undervoltage or throttling since boot${ext}" ;;
    "")  warn power "vcgencmd unavailable" ;;
    *)   bad power "get_throttled=$thr${ext}  (bit 0/16 = undervoltage): check the Pi's supply" ;;
esac
temp=$(vcgencmd measure_temp 2>/dev/null | cut -d= -f2)
[ -n "$temp" ] && ok temp "$temp"

# storage
nlogs=$(ls -1 "$LOGDIR"/rail_*.csv 2>/dev/null | wc -l)
ok disk "$(df -h / | awk 'NR==2 {print $4 " free"}'),  $nlogs session file(s), $(du -sh "$LOGDIR" 2>/dev/null | cut -f1) in $LOGDIR"

# network
ips=$(hostname -I 2>/dev/null | xargs)
con=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep -v ':lo$' | cut -d: -f1 | paste -sd, -)
if [ -n "$ips" ]; then ok network "${con:-?}  $ips"
else warn network "offline (no IP)"; fi

printf '\n%srecent log:%s\n' "$D" "$N"
journalctl -u gatbox-raillog -n 5 --no-pager -o cat 2>/dev/null | sed 's/^/    /'
echo

if [ "${1:-}" = "-f" ]; then
    f=${cur:-$(latest)}
    [ -n "$f" ] || { echo "no session files yet"; exit 1; }
    printf '%sfollowing %s  (Ctrl-C to stop; re-run after a new session starts)%s\n' "$C" "$f" "$N"
    exec tail -n 15 -F "$f"
fi
GATBOX_EOF
    put "$R/usr/local/bin/gatbox-rail-report" 755 <<'GATBOX_EOF'
#!/usr/bin/env python3
"""gatbox-rail-report — morning-after analysis for gatbox-raillog CSVs.

Usage:
    gatbox-rail-report                        # newest session in /var/log/gatbox
    gatbox-rail-report rail_20260924_021500.csv [--lo 4.75] [--hi 5.25] [--plot out.png]

Reads the format written by gatbox-raillog
(iso_time,epoch,value,unit,flags[,uptime_s], plus "# ..." note lines) and reports:

  * session duration and sample count
  * the meter MODE (DC voltage, AC voltage, resistance, ...) from the unit + flags
    columns, MODE CHANGES mid-session, and a WARNING if HOLD / REL / MAX/MIN was on
  * steady stats per mode: mean / min / max / p-p
  * EXCURSIONS: contiguous runs outside [lo, hi], with start time, duration,
    and worst value. This is the "what was the rail doing when it crashed" list.
    Only DC voltage readings are checked against the window
  * OPEN-INPUT events (inf/OL): a lead fell off, or the rail died entirely
  * GAPS in the timeline (>10 s between samples): optical link dropped
  * CLOCK STEPS: the wall clock jumped mid-session (NTP arrived late, or date -s).
    Durations use the monotonic uptime_s column when present, so they stay right.

A plot is written if matplotlib is available (python3-matplotlib), one panel per
meter mode; the text report works without it. If the CSV's folder isn't writable,
the plot goes to the current directory.
"""

import argparse
import csv
import functools
import glob
import math
import os
import statistics as st
import sys
from collections import Counter, namedtuple
from datetime import datetime, timezone

Row = namedtuple("Row", "epoch iso v unit flags up mode")
Mode = namedtuple("Mode", "label base scale unit warn")
DEFAULT_DIR = "/var/log/gatbox"

# Meter mode from the unit + flags columns. gatbox-raillog splits sigrok's "P1: 119.68 V AC AUTO" into
# value, unit (SI prefix + unit) and flags; continuity has no unit, so its first flag can land in the
# unit column ("1,AUTO,"). Same rules as mode() in gatbox-web: keep the two in step.
FLAGS = {"AC", "DC", "RMS", "DIODE", "HOLD", "MAX", "MIN", "AUTO", "REL", "AVG", "REF", "UNSTABLE"}
PREFIX = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0,
          "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}   # sigrok can tag OL as e.g. "inf TV"
KIND = {"V": "voltage", "A": "current", "Ω": "resistance", "F": "capacitance", "Hz": "frequency",
        "%": "duty cycle", "": "continuity"}
WARN = {"HOLD": ("HOLD", "the meter repeats one frozen reading"),     # these make the log misleading
        "REL": ("REL (Δ)", "readings are offsets, not the real value"),
        "MAX": ("MAX/MIN", "the meter sends its max or min, not the live value"),
        "MIN": ("MAX/MIN", "the meter sends its max or min, not the live value")}
RAIL = "DC voltage"   # the only mode the V window applies to


@functools.lru_cache(maxsize=64)
def mode(unit, flags):
    """CSV unit + flags -> Mode, e.g. ('kΩ', 'AUTO') -> label 'Resistance', base 'Ω', scale 1e3."""
    fl = flags.split()
    unit = unit.replace("\u2126", "\u03a9")          # sigrok writes OHM SIGN; "Ω" here is Greek omega
    if unit in FLAGS:
        fl, unit = [unit] + fl, ""
    base, scale = unit, 1.0
    for b in ("V", "A", "Ω", "F", "Hz", "%"):
        if unit.endswith(b) and unit[:-len(b)] in PREFIX:
            base, scale = b, PREFIX[unit[:-len(b)]]
            break
    if "DIODE" in fl:
        label = "diode"
    elif base in ("V", "A"):
        ac, dc = "AC" in fl, "DC" in fl
        label = ("AC+DC " if ac and dc else "AC " if ac else "DC " if dc else "") + KIND[base]
    elif base in KIND:
        label = KIND[base]
    else:
        label = unit                                   # not a UT61E unit: show it as sigrok wrote it
    if base in KIND:
        label = label[:1].upper() + label[1:]
    return Mode(label, base, scale, unit, frozenset(f for f in fl if f in WARN))


def load(path):
    notes, body = [], []
    with open(path, newline="") as f:
        for line in f:
            if line.startswith("#"):
                notes.append(line[1:].strip())
            else:
                body.append(line)
    r = csv.reader(body)
    next(r, None)  # header
    rows = []
    for line in r:
        if len(line) < 5:
            continue
        iso, epoch, val, unit, flags = line[:5]
        try:
            e = float(epoch)
        except ValueError:
            continue
        m = mode(unit, flags)
        try:
            v = float(val)  # 'inf' / '-inf' parse as infinities
        except ValueError:
            v = math.nan
        # to base units: the meter autoranges (near 0 V it logs in mV, 1.2 kΩ, ...)
        v *= m.scale
        up = None
        if len(line) >= 6:
            try:
                up = float(line[5])
            except ValueError:
                up = None
        rows.append(Row(e, iso, v, unit, flags, up, m))
    return rows, notes


def group_runs(rows, predicate):
    """Yield (start_idx, end_idx_inclusive) for contiguous rows where predicate holds."""
    start = None
    for i, row in enumerate(rows):
        if predicate(row):
            if start is None:
                start = i
        elif start is not None:
            yield (start, i - 1)
            start = None
    if start is not None:
        yield (start, len(rows) - 1)


def fmt_dur(seconds):
    if seconds < 90:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def si(vals, base):
    """(prefix, factor) that suits the largest |value|. Volts stay in V, like the window."""
    if base in ("V", "%", "") or base not in KIND:
        return "", 1.0
    big = max((abs(v) for v in vals if math.isfinite(v)), default=0.0)
    for p, k in (("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if big >= k:
            return p, k
    return "", 1.0


def stats(label, vals, base):
    if label == "Continuity":
        closed = sum(1 for v in vals if v > 0)
        return f"closed {closed} of {len(vals)} samples ({100 * closed / len(vals):.0f}%)"
    fin = [v for v in vals if math.isfinite(v)]
    if not fin:
        return "no readings, all OL"
    if base == "V":
        return (f"mean {st.mean(fin):+.4f} V   min {min(fin):+.4f}   max {max(fin):+.4f}   "
                f"p-p {(max(fin) - min(fin)) * 1000:.1f} mV")
    p, k = si(fin, base)
    return (f"mean {st.mean(fin) / k:.4g} {p}{base}   min {min(fin) / k:.4g}   max {max(fin) / k:.4g}   "
            f"p-p {(max(fin) - min(fin)) / k:.4g} {p}{base}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csvfile", nargs="?",
                    help=f"session CSV (default: newest in {DEFAULT_DIR})")
    ap.add_argument("--lo", type=float, default=4.75,
                    help="low threshold, V (default 4.75 = 5V - 5%%)")
    ap.add_argument("--hi", type=float, default=5.25,
                    help="high threshold, V (default 5.25 = 5V + 5%%)")
    ap.add_argument("--plot", default=None,
                    help="write PNG here (default: <csvfile>.png)")
    ap.add_argument("--light", action="store_true",
                    help="white background, dark ink (for printing / PDF)")
    args = ap.parse_args()

    if not args.csvfile:
        files = sorted(glob.glob(os.path.join(DEFAULT_DIR, "rail_*.csv")), key=os.path.getmtime)
        if not files:
            sys.exit(f"no rail_*.csv in {DEFAULT_DIR}")
        args.csvfile = files[-1]

    rows, notes = load(args.csvfile)
    if len(rows) < 2:
        sys.exit("no usable samples in file")

    mono = all(r.up is not None for r in rows)

    def t(r):
        return r.up if mono else r.epoch

    dts = sorted(t(b) - t(a) for a, b in zip(rows, rows[1:]))
    period = max(dts[len(dts) // 2], 0.0)   # median sample interval (~0.5 s on the UT61E)

    def span(a, b):                         # a run of N samples lasts ~N sample periods
        return t(rows[b]) - t(rows[a]) + period

    dur = t(rows[-1]) - t(rows[0])
    count = Counter(r.mode.label for r in rows)
    labels = list(count)                    # meter modes, in order of first appearance
    base = {r.mode.label: r.mode.base for r in rows}
    n_rail = count[RAIL]

    print(f"file:      {args.csvfile}")
    for n in notes:
        print(f"note:      {n}")
    print(f"session:   {rows[0].iso}  ->  {rows[-1].iso}   ({fmt_dur(dur)})")
    print(f"samples:   {len(rows)}   ({len(rows) / max(dur, 1):.2f} S/s)"
          f"   timebase: {'monotonic uptime' if mono else 'wall clock'}")
    for i, l in enumerate(labels):
        print("mode:      " if i == 0 else " " * 11, end="")
        print(l if len(labels) == 1 else f"{l} ({count[l]} sample{'s' * (count[l] != 1)})")
    for l in labels:
        s = stats(l, [r.v for r in rows if r.mode.label == l], base[l])
        print(f"stats:     {s}" if len(labels) == 1 else f"stats:     {l}: {s}")
    print(f"window:    [{args.lo}, {args.hi}] V" + (
        "" if n_rail == len(rows) else
        ", DC voltage readings only" if n_rail else ", not applied (no DC voltage readings)"))
    held = {}
    for r in rows:
        for name, what in {WARN[f] for f in r.mode.warn}:
            held.setdefault(name, [0, r.iso, what])[0] += 1
    for name, (n, first, what) in held.items():
        print(f"WARNING:   {name} on for {n} of {len(rows)} samples, first at {first}:")
        print(f"           {what}")
    print()

    # --- mode changes -----------------------------------------------------------
    changes = [(b.iso, a.mode.label, b.mode.label)
               for a, b in zip(rows, rows[1:]) if a.mode.label != b.mode.label]
    if changes:
        print(f"MODE CHANGES ({len(changes)}): the meter was switched mid-session:")
        for iso, a, b in changes[:20]:
            print(f"  {iso}  {a} -> {b}")
        if len(changes) > 20:
            print(f"  ... and {len(changes) - 20} more")
        print()

    # --- clock steps (only detectable with the monotonic column) -----------
    steps = []
    if mono:
        for a, b in zip(rows, rows[1:]):
            jump = (b.epoch - a.epoch) - (b.up - a.up)
            if abs(jump) > 5:
                steps.append((b.iso, jump))
        if steps:
            print(f"CLOCK STEPS ({len(steps)}): wall clock jumped mid-session; "
                  f"durations below are still correct:")
            for iso, jump in steps:
                print(f"  {jump:+.0f}s  at {iso}")
            print()

    # --- timeline gaps -------------------------------------------------------
    gaps = [(t(b) - t(a), b.iso) for a, b in zip(rows, rows[1:]) if t(b) - t(a) > 10]
    if gaps:
        print(f"GAPS ({len(gaps)}): optical link dropped or sigrok stalled:")
        for d, iso in gaps:
            print(f"  {fmt_dur(d):>9}  ending at {iso}")
        print()

    # --- open-input / dead-rail events ---------------------------------------
    ol = list(group_runs(rows, lambda r: not math.isfinite(r.v)))
    if ol:
        if n_rail == len(rows):
            print(f"OPEN-INPUT events ({len(ol)}): lead detached or rail fully dead:")
        else:
            print(f"OL events ({len(ol)}): the meter read OL (open input or over range):")
        for a, b in ol:
            print(f"  {rows[a].iso}  for {fmt_dur(span(a, b))}"
                  + (f"   ({rows[a].mode.label})" if len(labels) > 1 else ""))
        print()

    # --- excursions (DC voltage only) -------------------------------------------
    def outside(r):
        return r.mode.label == RAIL and math.isfinite(r.v) and not (args.lo <= r.v <= args.hi)

    exc = list(group_runs(rows, outside))
    if exc:
        mid = (args.lo + args.hi) / 2
        print(f"EXCURSIONS ({len(exc)}): outside [{args.lo}, {args.hi}] V:")
        for a, b in exc:
            seg = [rows[i].v for i in range(a, b + 1)]
            worst = max(seg, key=lambda v: abs(v - mid))
            print(f"  {rows[a].iso}  {fmt_dur(span(a, b)):>8}"
                  f"   worst {worst:+.3f} V   ({b - a + 1} samples)")
        print()
    elif not n_rail:
        print("EXCURSIONS: not checked. The meter was never on DC voltage.")
        print()
    elif n_rail < len(rows):
        print(f"EXCURSIONS: none in the {n_rail} DC voltage readings. The rest of the")
        print("session was in other modes, so this only covers part of it.")
        print()
    else:
        print("EXCURSIONS: none. The rail held the window all session.")
        print("If the game still crashed, this log exonerates this rail;")
        print("next suspects: the other rails, on-board regulation, thermals.")
        print()

    # --- plot: one panel per meter mode -------------------------------------------
    out = args.plot or (args.csvfile.rsplit(".", 1)[0] + ".png")
    if not args.plot and not os.access(os.path.dirname(os.path.abspath(out)), os.W_OK):
        out = os.path.join(os.getcwd(), os.path.basename(out))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(no matplotlib; skipped plot. sudo apt install python3-matplotlib)")
        return

    CYAN, DIM = "#7dfaff", "#9080b0"
    BG, PANEL, GRID, TXT = "#150a28", "#1f1240", "#3d1e6b", "#ece3ff"
    BAD = "#ff4d6d"
    if args.light:  # print-friendly: same layout, paper-white with dark ink
        CYAN, DIM = "#0b5fa5", "#555555"
        BG, PANEL, GRID, TXT = "#ffffff", "#ffffff", "#c8c8c8", "#111111"
        BAD = "#e0304f"
    if steps:  # wall clock is unreliable: plot elapsed monotonic minutes instead
        xs = [(r.up - rows[0].up) / 60 for r in rows]
        xlabel = "elapsed (min, monotonic; wall clock stepped mid-session)"
    else:
        import matplotlib.dates as mdates
        stamps = [datetime.fromtimestamp(r.epoch, tz=timezone.utc).astimezone() for r in rows]
        xs = mdates.date2num(stamps)   # convert once, not per panel (slow on a long night)
        xlabel = None

    height = 4.5 if len(labels) == 1 else min(2 + 2.2 * len(labels), 11)
    fig, axes = plt.subplots(len(labels), 1, sharex=True, squeeze=False, figsize=(11, height), dpi=150)
    fig.patch.set_facecolor(BG)
    for ax, l in zip(axes[:, 0], labels):
        p, k = si([r.v for r in rows if r.mode.label == l], base[l])
        vs = [r.v / k if r.mode.label == l and math.isfinite(r.v) else math.nan for r in rows]
        ax.set_facecolor(PANEL)
        # dots too for a brief mode in a mixed session: one sample on its own is otherwise invisible
        ax.plot(xs, vs, color=CYAN, lw=0.9, drawstyle="steps-post" if l == "Continuity" else "default",
                **({"marker": ".", "ms": 2} if len(labels) > 1 and count[l] < 2000 else {}))
        if l == RAIL:
            ax.axhline(args.lo, color=DIM, ls="--", lw=0.8)
            ax.axhline(args.hi, color=DIM, ls="--", lw=0.8)
            for a, b in exc:
                ax.axvspan(xs[a], xs[b], color=BAD, alpha=0.35 if not args.light else 0.2)
        if l == "Continuity":
            ax.set_ylim(-0.15, 1.15)
            ax.set_yticks([0, 1])
            ax.set_yticklabels(["open", "closed"])
            ylabel = "continuity"
        else:
            kind = l.split()[0] if l.split()[0] in ("DC", "AC", "AC+DC") else "diode" if l == "Diode" else ""
            ylabel = f"{p}{base[l]} {kind}".strip()
        ax.set_ylabel(ylabel, family="monospace", color=TXT)
        if not steps:
            ax.xaxis_date(stamps[0].tzinfo)   # ticks in local time, as with datetime xs
        for s in ax.spines.values():
            s.set_color(GRID)
        ax.tick_params(colors=TXT, labelsize=8)
        ax.grid(color=GRID, lw=0.4, alpha=0.6)
    if xlabel:
        axes[-1, 0].set_xlabel(xlabel, family="monospace", color=TXT, fontsize=8)
    title = f"GDD-GAT/01 rail log · {rows[0].iso[:10]}"
    if len(labels) == 1 and labels[0] != RAIL:
        title += f" · {labels[0]}"
    axes[0, 0].set_title(title, family="monospace", color=TXT, fontsize=10)
    if not steps:
        fig.autofmt_xdate()
    plt.tight_layout()
    plt.savefig(out, facecolor=BG)
    print(f"plot:      {out}")


if __name__ == "__main__":
    main()
GATBOX_EOF
    put "$R/etc/systemd/system/gatbox-raillog.service" 644 <<'GATBOX_EOF'
[Unit]
Description=GATBOX rail flight recorder (UT61E -> UT-D02 -> PL-2303)
After=local-fs.target time-sync.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gatbox-raillog
Restart=always
RestartSec=5
RuntimeDirectory=gatbox

[Install]
WantedBy=multi-user.target
GATBOX_EOF
    put "$R/etc/udev/rules.d/99-gatbox-dmm.rules" 644 <<'GATBOX_EOF'
# GATBOX: stable name for the DMM's Prolific PL-2303 USB-RS232 adapter (UT61E via UT-D02).
# Any Prolific adapter (VID 067b) gets /dev/gatbox-dmm. If you ever plug in a second
# Prolific adapter, pin this one with ATTRS{serial}=="..." (udevadm info -a /dev/ttyUSB0).
SUBSYSTEM=="tty", ATTRS{idVendor}=="067b", SYMLINK+="gatbox-dmm", GROUP="dialout", MODE="0660"
GATBOX_EOF
    put "$R/etc/systemd/journald.conf.d/gatbox.conf" 644 <<'GATBOX_EOF'
# GATBOX: keep the logger's journal across reboots (why did it stop at 3am?)
[Journal]
Storage=persistent
SystemMaxUse=200M
GATBOX_EOF
    put "$R/usr/share/applications/gatbox-rail-monitor.desktop" 644 <<'GATBOX_EOF'
[Desktop Entry]
Type=Application
Name=GATBOX Rail Monitor
Comment=Logger health + live UT61E readings
Exec=x-terminal-emulator -e gatbox-status -f
Icon=utilities-terminal
Terminal=false
Categories=Utility;
GATBOX_EOF
    put "$R/usr/local/sbin/gatbox-ap-fallback" 755 <<'GATBOX_EOF'
#!/usr/bin/env bash
# gatbox-ap-fallback — if no known network shows up after boot, raise the GATBOX
# hotspot so a phone or laptop can still reach the Pi inside a cabinet.
# Pi is then 10.42.0.1 (also gatbox.local). Reboot near known Wi-Fi to go back.
set -u
WAIT="${GATBOX_AP_WAIT:-60}"
online() { nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -Eq '^(wlan0|eth0):connected$'; }
for ((i = 0; i < WAIT; i += 5)); do
    online && { echo "gatbox-ap: network up, hotspot not needed"; exit 0; }
    sleep 5
done
echo "gatbox-ap: no known network after ${WAIT}s, raising hotspot"
exec nmcli connection up gatbox-ap
GATBOX_EOF
    put "$R/etc/systemd/system/gatbox-ap-fallback.service" 644 <<'GATBOX_EOF'
[Unit]
Description=GATBOX: raise the GATBOX Wi-Fi hotspot if no known network appears after boot
Wants=NetworkManager.service
After=NetworkManager.service

[Service]
Type=exec
ExecStart=/usr/local/sbin/gatbox-ap-fallback

[Install]
WantedBy=multi-user.target
GATBOX_EOF
    put "$R/usr/local/bin/gatbox-web" 755 <<'GATBOX_EOF'
#!/usr/bin/env python3
"""gatbox-web: phone-friendly read-only view of the rail logs.

  /                      sessions, newest first, plus what's logging right now
  /s/<csv>?lo=&hi=       gatbox-rail-report text + plot for one session
  /png/<csv>?lo=&hi=     the plot on its own
  /pdf/<csv>?lo=&hi=&from=&to=   header + plot + report as a PDF to save/share
                         (from/to are Pi wall-clock times, YYYY-MM-DDTHH:MM[:SS]; on /s and /png too)
  /csv/<csv>             download the raw CSV
  /font/<file>           the two UI fonts, from $GATBOX_WEB_FONTS (the hotspot has no internet)
  POST /control          action=start|stop: flag files in $STATE_DIRECTORY that gatbox-raillog polls
                         ("stopped" exists = don't log; "start-request" newer than the session = new file)

Stdlib only. Runs gatbox-rail-report for the analysis so the numbers match the CLI.
Plots are cached in $CACHE_DIRECTORY, keyed by file size+mtime and window.
The meter mode (DC voltage, resistance, ...) comes from the CSV's unit + flags columns.
"""
import functools, html, io, math, os, re, subprocess, sys, textwrap, threading, time
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

LOGDIR = os.environ.get("GATBOX_LOGDIR", "/var/log/gatbox")
CACHE = os.environ.get("CACHE_DIRECTORY", "/tmp/gatbox-web")
CTRL = os.environ.get("STATE_DIRECTORY", "/var/lib/gatbox-web")
PORT = int(os.environ.get("GATBOX_WEB_PORT", "80"))
REPORT = os.environ.get("GATBOX_REPORT", "/usr/local/bin/gatbox-rail-report")
FONTS = os.environ.get("GATBOX_WEB_FONTS", "/usr/local/share/gatbox-web/fonts")   # fetched by gatbox-bootstrap.sh
FONT_FILES = {"ChakraPetch-SemiBold.ttf", "ShareTechMono-Regular.ttf"}
NAME = re.compile(r"^rail_\d{8}_\d{6}\.csv$")
TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?$")
PDF_LOCK = threading.Lock()  # matplotlib isn't thread-safe
LIVE_S = 10  # a session whose last sample is newer than this is "logging now"

# gatbox-raillog splits sigrok's "P1: 119.68 V AC AUTO" into value, unit (SI prefix + unit) and flags.
# Continuity has no unit, so its first flag (if any) lands in the unit column: "1,AUTO,".
FLAGS = {"AC", "DC", "RMS", "DIODE", "HOLD", "MAX", "MIN", "AUTO", "REL", "AVG", "REF", "UNSTABLE"}
PREFIX = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0,
          "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}   # sigrok can tag OL as e.g. "inf TV"
KIND = {"V": "voltage", "A": "current", "Ω": "resistance", "F": "capacitance", "Hz": "frequency",
        "%": "duty cycle", "": "continuity"}
WARN = {"HOLD": ("HOLD", "the meter repeats one frozen reading"),     # these make the log misleading
        "REL": ("REL (Δ)", "readings are offsets, not the real value"),
        "MAX": ("MAX/MIN", "the meter sends its max or min, not the live value"),
        "MIN": ("MAX/MIN", "the meter sends its max or min, not the live value")}
RAIL = "DC voltage"   # the only mode the V window and gatbox-rail-report are meant for
Mode = namedtuple("Mode", "label base scale unit warn")


@functools.lru_cache(maxsize=64)
def mode(unit, flags):
    """CSV unit + flags -> Mode, e.g. ('kΩ', 'AUTO') -> label 'Resistance', base 'Ω', scale 1e3."""
    fl = flags.split()
    unit = unit.replace("\u2126", "\u03a9")          # sigrok writes OHM SIGN; "Ω" here is Greek omega
    if unit in FLAGS:
        fl, unit = [unit] + fl, ""
    base, scale = unit, 1.0
    for b in ("V", "A", "Ω", "F", "Hz", "%"):
        if unit.endswith(b) and unit[:-len(b)] in PREFIX:
            base, scale = b, PREFIX[unit[:-len(b)]]
            break
    if "DIODE" in fl:
        label = "diode"
    elif base in ("V", "A"):
        ac, dc = "AC" in fl, "DC" in fl
        label = ("AC+DC " if ac and dc else "AC " if ac else "DC " if dc else "") + KIND[base]
    elif base in KIND:
        label = KIND[base]
    else:
        label = unit                                   # not a UT61E unit: show it as sigrok wrote it
    if base in KIND:
        label = label[:1].upper() + label[1:]
    return Mode(label, base, scale, unit, frozenset(f for f in fl if f in WARN))


def fmt(v, base):
    """Value in base units -> text: volts as before (3 decimals), other units with an SI prefix."""
    if base == "V":
        return f"{v:.3f} V"
    if base not in KIND or base in ("%", ""):
        return f"{v:g} {base}".rstrip()
    for p, k in (("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if abs(v) >= k:
            return f"{v / k:.4g} {p}{base}"
    return f"{v:g} {base}"


def reading(raw, m):
    """The live value the way the meter's display would put it."""
    if raw.lstrip("+-") == "inf":
        return "OL"
    if m.label == "Continuity":
        try:
            return "closed" if float(raw) > 0 else "open"
        except ValueError:
            pass
    return f"{raw} {m.unit}".rstrip()


def warnings(flags, when):
    w = dict(WARN[f] for f in sorted(flags))
    return "".join(f'<div class="warn">{html.escape(k)} {when}: {v}</div>' for k, v in w.items())

# Project design tokens (synthwave). The fonts come from this Pi, since the hotspot has no internet. If they're
# missing, /font/ 404s and each stack falls back to the system fonts after it.
CSS = """
@font-face{font-family:"Chakra Petch";font-weight:600;font-display:swap;src:url(/font/ChakraPetch-SemiBold.ttf)}
@font-face{font-family:"Share Tech Mono";font-display:swap;src:url(/font/ShareTechMono-Regular.ttf)}
:root{--bg:#150a28;--bg2:#1f0f3d;--panel:#1f1240;--line:#3d1e6b;--mag:#ff2e9f;--cyan:#7dfaff;--ok:#5ef2b0;
--bad:#ff4d6d;--warn:#ffb74d;--fg:#ece3ff;--mut:#9080b0;--disp:"Chakra Petch",system-ui,sans-serif;
--mono:"Share Tech Mono",ui-monospace,monospace}
*{box-sizing:border-box}html{background:var(--bg) linear-gradient(180deg,var(--bg2),var(--bg) 100vh) no-repeat}
body{margin:0 auto;padding:16px;max-width:900px;color:var(--fg);font:15px/1.45 var(--mono)}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px)}
a{color:var(--cyan)}.mut{color:var(--mut);font-size:14px}
h1{font:600 22px/1.2 var(--disp);letter-spacing:.03em;margin:0 0 10px;text-shadow:0 0 12px rgba(255,46,159,.5)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.card a{text-decoration:none;display:block;color:var(--fg)}
.big{font:600 32px/1.2 var(--disp);color:var(--cyan);text-shadow:0 0 10px rgba(125,250,255,.35)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
pre{white-space:pre-wrap;font:13px/1.4 var(--mono);overflow-x:auto;margin:0}
img{width:100%;height:auto;border-radius:6px;border:1px solid var(--line)}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input{width:5.5em;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);
font:inherit}input:focus{outline:1px solid var(--cyan)}
button,.btn{padding:7px 14px;border-radius:6px;border:1px solid var(--mag);background:var(--mag);color:var(--bg);
font:600 15px var(--disp);text-decoration:none;display:inline-block;cursor:pointer}
button.stop{background:transparent;color:var(--bad);border-color:var(--bad);margin-left:8px}
.rng{display:flex;gap:8px;flex-wrap:wrap;width:100%}.rng label{color:var(--mut);font-size:14px}.rng input{width:auto}
.mode{display:inline-block;padding:0 8px;border:1px solid var(--cyan);border-radius:999px;color:var(--cyan);
background:rgba(125,250,255,.08);box-shadow:0 0 6px rgba(125,250,255,.25);font:13px/1.5 var(--mono);
vertical-align:middle;text-shadow:none}.modes div{margin-top:5px}.modes .warn{font-size:14px}
.cyan{color:var(--cyan)}.clk{display:inline-block;padding:0 6px;border:1px solid currentColor;border-radius:4px;
font:12px/1.5 var(--mono);vertical-align:middle;font-weight:normal}
"""


def page(title, body, refresh=None):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="theme-color" content="#150a28">{meta}'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>')


def sessions():
    try:
        names = [n for n in os.listdir(LOGDIR) if NAME.match(n)]
    except OSError:
        names = []
    return sorted(names, reverse=True)


def summary(name, t0=None, t1=None):
    """First/last sample, and per meter mode the sample count and cheap min/max in base units
    (V, Ω, ...), without running the full report. Optionally only the samples from t0 to t1."""
    first = last = None
    modes, warn, n = {}, set(), 0
    clock = clock_note = clock_sync = None
    with open(os.path.join(LOGDIR, name), errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                if line.startswith("# clock="):            # ntp | rtc (...) | unverified (...)
                    clock_note = line[2:].strip()
                    clock = line[8:].split("(")[0].strip()
                elif line.startswith("# clock-sync="):     # NTP arrived mid-session
                    clock_sync = line.strip().rsplit(" at ", 1)[-1]
                continue
            p = line.rstrip("\n").split(",")
            if len(p) < 4 or p[0] == "iso_time":
                continue
            if (t0 and p[0] < t0) or (t1 and p[0] > t1):
                continue
            m = mode(p[3], p[4] if len(p) > 4 else "")
            try:
                e, v = float(p[1]), float(p[2]) * m.scale
            except ValueError:
                continue
            n += 1
            if first is None:
                first = (p[0], e)
            last = (p[0], e, m, p[2])
            s = modes.setdefault(m.label, [0, float("inf"), float("-inf"), m.base])
            s[0] += 1
            if math.isfinite(v):
                s[1], s[2] = min(s[1], v), max(s[2], v)
            warn |= m.warn
    return dict(first=first, last=last, n=n, modes=modes, warn=warn,
                clock=clock, clock_note=clock_note, clock_sync=clock_sync)


CLOCK_BADGE = {"ntp": ("NTP", "ok"), "rtc": ("RTC", "cyan"), "unverified": ("UNVERIFIED", "warn")}


def clock_badge(s):
    """NTP / RTC / UNVERIFIED from the session's # clock= line, plus NTP-from-<time> if it synced later."""
    if not s.get("clock"):
        return ""
    txt, cls = CLOCK_BADGE.get(s["clock"], (s["clock"].upper(), "mut"))
    b = f'<span class="clk {cls}" title="{html.escape(s["clock_note"] or "")}">{html.escape(txt)}</span>'
    if s.get("clock_sync"):
        b += f' <span class="clk ok" title="NTP synced mid-session">NTP from {html.escape(s["clock_sync"][11:19])}</span>'
    return b


def mode_lines(s, lo, hi, when="was on"):
    """One line per meter mode seen: chip, min/max, and for DC voltage the rail-window tag.
    Then a warning for HOLD/REL/MAX/MIN."""
    out = []
    for label, (n, vmin, vmax, base) in s["modes"].items():
        line = f'<span class="mode">{html.escape(label)}</span>'
        if vmin <= vmax and label != "Continuity":   # all-OL has no finite values
            line += f' <span class="mut">min {html.escape(fmt(vmin, base))} · max {html.escape(fmt(vmax, base))}</span>'
        if label == RAIL:
            line += (' · <span class="ok">in window</span>' if lo <= vmin and vmax <= hi
                     else ' · <span class="bad">left the window</span>')
        out.append(f"<div>{line}</div>")
    return f'<div class="modes">{"".join(out)}{warnings(s["warn"], when)}</div>'


def window(q):
    def f(k, d):
        try:
            return float(q.get(k, [d])[0])
        except ValueError:
            return d
    return f("lo", 4.75), f("hi", 5.25)


def span(q):
    """from/to query values -> (t0, t1) as 'YYYY-MM-DDTHH:MM:SS', or None if absent/invalid.
    Compared as strings against the CSV's iso_time column (same local wall-clock format)."""
    def f(k, pad):
        v = q.get(k, [""])[0]
        return (v if len(v) == 19 else v + pad) if TS.match(v) else None
    return f("from", ":00"), f("to", ":59")


def span_qs(t0, t1):
    return (f"&from={t0}" if t0 else "") + (f"&to={t1}" if t1 else "")


def report(name, lo, hi, t0=None, t1=None, light=False):
    """Run gatbox-rail-report (on the from/to slice if given); returns (text, png path or None)."""
    src = os.path.join(LOGDIR, name)
    st = os.stat(src)
    os.makedirs(CACHE, exist_ok=True)
    rng = f"_{t0 or ''}_{t1 or ''}".replace(":", "") if (t0 or t1) else ""
    ver = f"{st.st_size}_{int(st.st_mtime)}_{int(os.stat(REPORT).st_mtime)}"   # new report -> new cache
    key = f"{name[:-4]}_{ver}_{lo:g}_{hi:g}{rng}{'_light' if light else ''}"
    png, txt = os.path.join(CACHE, key + ".png"), os.path.join(CACHE, key + ".txt")
    if not os.path.exists(txt):
        for old in os.listdir(CACHE):          # drop stale versions of this session (file grew)
            if old.startswith(name[:-4] + "_") and f"_{ver}_" not in old:
                try: os.remove(os.path.join(CACHE, old))
                except OSError: pass
        run_on = src
        if rng:                                # slice the CSV, keep header + "# ..." notes
            run_on, kept = os.path.join(CACHE, key + ".csv"), 0
            with open(src, errors="replace") as fi, open(run_on, "w") as fo:
                for i, line in enumerate(fi):
                    if i == 0 or line.startswith("#"):
                        fo.write(line)
                        continue
                    iso = line[:19]
                    if (not t0 or iso >= t0) and (not t1 or iso <= t1):
                        fo.write(line); kept += 1
            if not kept:
                with open(txt, "w") as f:
                    f.write(f"No samples between {t0 or 'start'} and {t1 or 'end'}.\n")
                return report_text(txt, name, t0, t1), None
        r = subprocess.run([REPORT, run_on, "--lo", str(lo), "--hi", str(hi), "--plot", png]
                           + (["--light"] if light else []),
                           capture_output=True, text=True, timeout=120)
        with open(txt, "w") as f:
            f.write(r.stdout + (("\n" + r.stderr) if r.returncode else ""))
        if rng:
            os.remove(run_on)
    return report_text(txt, name, t0, t1), (png if os.path.exists(png) else None)


def report_text(txt, name, t0, t1):
    out = []
    with open(txt) as f:
        for l in f:
            if l.startswith("plot:"):          # cache path means nothing on a phone
                continue
            if l.startswith("file:"):          # show the real session, not the cache slice
                l = f"file:      {name}\n"
                if t0 or t1:
                    l += f"range:     {t0 or 'start'}  ->  {t1 or 'end'}\n"
            out.append(l)
    return "".join(out)


def make_pdf(name, lo, hi, t0, t1):
    """Letter-size PDF: header, plot, then the report text (continued on more pages if long)."""
    text, png = report(name, lo, hi, t0, t1, light=True)
    s = summary(name, t0, t1)
    clk = (s["clock"] or "?").upper() + (f" (NTP from {s['clock_sync'][11:19]})" if s["clock_sync"] else "")
    mode_txt = ("clock: " + clk + "   ·   meter mode: " + (", ".join(s["modes"]) or "no samples")
                + "".join(f"   ·   {k} was on" for k in dict(WARN[f] for f in sorted(s["warn"]))))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.image as mpimg
    lines = text.rstrip("\n").split("\n")
    buf = io.BytesIO()
    with PDF_LOCK, PdfPages(buf, metadata={"Title": f"GATBOX rail report {name}", "Creator": "gatbox-web"}) as pdf:
        first, per_page, page_no = True, 72, 1
        while first or lines:
            fig = Figure(figsize=(8.5, 11))
            fig.text(0.06, 0.965, "GATBOX rail report (GDD-GAT/01)", fontsize=14, weight="bold")
            fig.text(0.94, 0.965, f"page {page_no}", fontsize=8, ha="right", color="0.4")
            if first:
                sub = (f"{name}   ·   window {lo:g}–{hi:g} V   ·   "
                       f"range {(t0 or 'start').replace('T', ' ')} → {(t1 or 'end').replace('T', ' ')}")
                fig.text(0.06, 0.945, sub, fontsize=8.5, color="0.25")
                rows = textwrap.wrap(mode_txt, 110)
                for i, row in enumerate(rows):
                    fig.text(0.06, 0.93 - 0.0135 * i, row, fontsize=8.5,
                             color="#c0304a" if s["warn"] else "0.25")
                y = 0.93 - 0.0135 * len(rows)
                fig.text(0.06, y, f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} (Pi clock)",
                         fontsize=8, color="0.45")
                top = y - 0.015
                if png:
                    img = mpimg.imread(png)
                    h = 0.88 * 8.5 * img.shape[0] / img.shape[1] / 11   # keep aspect, 88% width
                    ax = fig.add_axes([0.06, top - h, 0.88, h]); ax.imshow(img); ax.axis("off")
                    top -= h + 0.02
                n = max(1, int((top - 0.04) / 0.0125))
            else:
                top, n = 0.935, per_page
            chunk, lines = lines[:n], lines[n:]
            fig.text(0.06, top, "\n".join(chunk), fontsize=7.5, family="monospace", va="top",
                     linespacing=1.25)
            pdf.savefig(fig)
            first, page_no = False, page_no + 1
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    server_version = "gatbox-web"

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=(), cache="no-store"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        parts = u.path.strip("/").split("/")
        try:
            if u.path in ("/", ""):
                return self.index(q)
            if len(parts) == 2 and parts[0] == "font" and parts[1] in FONT_FILES:
                try:
                    with open(os.path.join(FONTS, parts[1]), "rb") as f:
                        return self.send(200, f.read(), "font/ttf", cache="public, max-age=31536000, immutable")
                except OSError:
                    pass                               # not installed: 404, and the page uses system fonts
            if len(parts) == 2 and NAME.match(parts[1]) and os.path.exists(os.path.join(LOGDIR, parts[1])):
                kind, name = parts
                if kind == "s":
                    return self.detail(name, q)
                if kind == "png":
                    _, png = report(name, *window(q), *span(q))
                    if png:
                        with open(png, "rb") as f:
                            return self.send(200, f.read(), "image/png")
                if kind == "pdf":
                    t0, t1 = span(q)
                    fn = f"gatbox_{name[:-4]}" + (f"_{(t0 or 'start')}_{(t1 or 'end')}".replace(":", "")
                                                  if (t0 or t1) else "") + ".pdf"
                    return self.send(200, make_pdf(name, *window(q), t0, t1), "application/pdf",
                                     [("Content-Disposition", f'inline; filename="{fn}"')])
                if kind == "csv":
                    with open(os.path.join(LOGDIR, name), "rb") as f:
                        return self.send(200, f.read(), "text/csv",
                                         [("Content-Disposition", f'attachment; filename="{name}"')])
            self.send(404, page("Not found", "<h1>Not found</h1><a href='/'>Back</a>"))
        except Exception as e:  # keep serving; show the error instead of a dropped connection
            self.send(500, page("Error", f"<h1>Error</h1><pre>{html.escape(repr(e))}</pre>"))

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(min(n, 1024)).decode(errors="replace"))
        action = form.get("action", [""])[0]
        if u.path != "/control" or action not in ("start", "stop"):
            return self.send(400, page("Bad request", "<h1>Bad request</h1><a href='/'>Back</a>"))
        os.makedirs(CTRL, exist_ok=True)
        stopped = os.path.join(CTRL, "stopped")
        if action == "stop":
            open(stopped, "w").close()
        else:
            with open(os.path.join(CTRL, "start-request"), "w") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S\n"))
            try: os.remove(stopped)
            except FileNotFoundError: pass
        sys.stderr.write(f"{self.address_string()} control: {action}\n")
        # POST-redirect-GET, so a refresh doesn't press the button again
        self.send(303, b"", extra=[("Location", "/?" + u.query if u.query else "/")])

    def controls(self, is_live):
        stopped = os.path.exists(os.path.join(CTRL, "stopped"))
        if stopped:
            state = '<span class="bad">Stopped</span> from this page. Nothing is being recorded.'
        elif is_live:
            state = '<span class="ok">Running</span>'
        else:
            state = '<span class="ok">Running</span>, waiting for readings from the meter'
        start_lbl = "Start new session" if (is_live and not stopped) else "Start"
        return (f'<div class="card"><div>Logger: {state}</div>'
                f'<form method="post" action="/control" style="margin-top:8px">'
                f'<button name="action" value="start">{start_lbl}</button>'
                + ('' if stopped else
                   '<button name="action" value="stop" class="stop" '
                   'onclick="return confirm(\'Stop logging? Nothing will be recorded until you press Start.\')">Stop</button>')
                + '</form></div>')

    def index(self, q):
        lo, hi = window(q)
        qs = f"?lo={lo:g}&hi={hi:g}"
        now = time.time()
        cards = []
        live = ""
        for name in sessions():
            s = summary(name)
            if not s["first"]:
                continue
            last_iso, last_e, m, raw = s["last"]
            dur = last_e - s["first"][1]
            is_live = now - last_e < LIVE_S
            if is_live:
                live = (f'<div class="card"><div class="mut">Logging now · {html.escape(name)}</div>'
                        f'<div class="big">{html.escape(reading(raw, m))} '
                        f'<span class="mode">{html.escape(m.label)}</span></div>'
                        f'{warnings(m.warn, "is on")}'
                        f'<div class="mut">{s["n"]} samples · {dur/3600:.2f} h so far</div></div>')
            cards.append(
                f'<div class="card"><a href="/s/{name}{qs}">'
                f'<b>{html.escape(s["first"][0].replace("T", " "))}</b> {clock_badge(s)}'
                f'{" · <span class=ok>live</span>" if is_live else ""}<br>'
                f'<span class="mut">{dur/3600:.2f} h · {s["n"]} samples</span>'
                f'{mode_lines(s, lo, hi)}</a></div>')
        is_live = bool(live)
        if not live and not os.path.exists(os.path.join(CTRL, "stopped")):
            live = ('<div class="card"><div class="mut">Not logging right now</div>'
                    '<div class="mut">Meter off, D02 head out of the top slot, or adapter unplugged.</div></div>')
        live = self.controls(is_live) + live
        body = (f'<h1>GATBOX rail log</h1><div class="mut">Pi time {time.strftime("%Y-%m-%d %H:%M:%S")}'
                f' · refreshes every 30 s</div>{live}'
                f'<form method="get" action="/"><span class="mut">Window (V)</span>'
                f'<input name="lo" value="{lo:g}" inputmode="decimal"><input name="hi" value="{hi:g}" inputmode="decimal">'
                f'<button>Apply</button></form>'
                + ("".join(cards) or '<div class="card mut">No sessions yet.</div>'))
        self.send(200, page("GATBOX", body, refresh=30))

    def detail(self, name, q):
        lo, hi = window(q)
        t0, t1 = span(q)
        qs = f"?lo={lo:g}&hi={hi:g}"
        s = summary(name)
        start = s["first"][0] if s["first"] else ""
        end = s["last"][0] if s["last"] else ""
        text, png = report(name, lo, hi, t0, t1)
        view = summary(name, t0, t1) if (t0 or t1) else s
        full = qs + span_qs(t0, t1)
        src = f"/png/{name}{full}&t={int(time.time())}"
        img = f'<a href="{src}"><img src="{src}" alt="plot (tap for full size)"></a>' if png else \
              '<div class="mut">No plot (python3-matplotlib missing?)</div>'
        body = (f'<a href="/{qs}">← all sessions</a><h1>{html.escape(name)}</h1>'
                f'<div class="card"><div>{clock_badge(s)} <span class="mut">{html.escape(s["clock_note"] or "no clock line")}</span></div>'
                f'<span class="mut">Meter mode{" in this range" if (t0 or t1) else ""}</span>'
                f'{mode_lines(view, lo, hi) if view["n"] else "<div class=mut>no samples</div>"}</div>'
                f'<form method="get"><span class="mut">Window (V)</span>'
                f'<input name="lo" value="{lo:g}" inputmode="decimal"><input name="hi" value="{hi:g}" inputmode="decimal">'
                f'<div class="rng"><label>From <input type="datetime-local" step="1" name="from" '
                f'value="{t0 or start}" min="{start}" max="{end}"></label>'
                f'<label>To <input type="datetime-local" step="1" name="to" '
                f'value="{t1 or end}" min="{start}" max="{end}"></label></div>'
                f'<button>Apply</button>'
                f'{f"<a href=/s/{name}{qs}>whole session</a>" if (t0 or t1) else ""}</form>'
                f'<p><a class="btn" href="/pdf/{name}{full}">PDF of this view</a></p>'
                f'<div class="card">{img}</div><div class="card"><pre>{html.escape(text)}</pre></div>'
                f'<a href="/csv/{name}">Download CSV</a>')
        self.send(200, page(name, body))


if __name__ == "__main__":
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("", PORT), H)
    print(f"gatbox-web on :{PORT}, logs {LOGDIR}", flush=True)
    srv.serve_forever()
GATBOX_EOF
    put "$R/etc/systemd/system/gatbox-web.service" 644 <<'GATBOX_EOF'
[Unit]
Description=GATBOX rail log web view (http://10.42.0.1 on the GATBOX hotspot)
After=network.target

[Service]
ExecStart=/usr/local/bin/gatbox-web
Restart=always
RestartSec=5
# unprivileged throwaway user; only needs to read /var/log/gatbox and bind port 80
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
CacheDirectory=gatbox-web
# Start/Stop flag files, read by gatbox-raillog (root) at /var/lib/gatbox-web
StateDirectory=gatbox-web
# matplotlib wants a writable config/font-cache dir; without one it rebuilds the font cache every run
Environment=MPLCONFIGDIR=/var/cache/gatbox-web/mpl
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
Nice=10

[Install]
WantedBy=multi-user.target
GATBOX_EOF
    put "$R/usr/local/sbin/gatbox-rtc-sync" 755 <<'GATBOX_EOF'
#!/usr/bin/env bash
# gatbox-rtc-sync — set the Pi 5's RTC from the system clock, but only while that clock is NTP-synced,
# and stamp each write in /var/lib/gatbox/rtc-synced as "<epoch> <iso>".
#
# gatbox-raillog trusts the RTC (and labels offline sessions clock=rtc) only if this stamp exists and the RTC
# hasn't gone backwards since. The kernel's own 11-minute sync (CONFIG_RTC_SYSTOHC) may also write the RTC;
# the stamp is the proof that it was set from real time. Run by gatbox-rtc-sync.timer: ~2 min after boot,
# then hourly. Never writes the RTC while unsynced. Never touches the backup cell's charging.
set -u

STAMP="${GATBOX_RTC_STAMP:-/var/lib/gatbox/rtc-synced}"
say() { echo "gatbox-rtc-sync: $*"; }

if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != yes ]; then
    say "system clock not NTP-synced; RTC left alone"
    exit 0
fi
[ -e /dev/rtc0 ] || { say "no /dev/rtc0; nothing to set"; exit 0; }
if ! hwclock --systohc --utc; then
    say "hwclock --systohc failed"
    exit 1
fi
now=$EPOCHSECONDS
mkdir -p "${STAMP%/*}"
printf '%s %(%FT%T)T\n' "$now" "$now" > "$STAMP.tmp" && mv -f "$STAMP.tmp" "$STAMP"
say "RTC set from NTP time (stamp: $(< "$STAMP"))"
GATBOX_EOF
    put "$R/etc/systemd/system/gatbox-rtc-sync.service" 644 <<'GATBOX_EOF'
[Unit]
Description=GATBOX: set the RTC from NTP time and stamp it (never while unsynced)
After=time-sync.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/gatbox-rtc-sync
# /var/lib/gatbox (root, 0755): the stamp gatbox-raillog reads
StateDirectory=gatbox
GATBOX_EOF
    put "$R/etc/systemd/system/gatbox-rtc-sync.timer" 644 <<'GATBOX_EOF'
[Unit]
Description=GATBOX: RTC sync from NTP, 2 min after boot and then hourly

[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
AccuracySec=1min

[Install]
WantedBy=timers.target
GATBOX_EOF
}

# ---------------------------------------------------------------------------
if [ "${1:-}" = "--extract" ]; then
    [ -n "${2:-}" ] || { echo "usage: $0 --extract DIR" >&2; exit 2; }
    payload "$2"
    echo "payload written under $2"
    exit 0
fi

M=$'\e[38;5;199m'; C=$'\e[36m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
step() { printf '\n%s== %s%s\n' "$M" "$*" "$N"; }
log()  { printf '   %s\n' "$*"; }
warn() { printf '   %s!! %s%s\n' "$Y" "$*" "$N"; }
die()  { printf '%sXX %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# RTC trickle charging: refused unless GATBOX_RTC_CHARGE=ML2020 exactly (checked first, before anything runs)
[ -z "${GATBOX_RTC_BATTERY:-}" ] || die "GATBOX_RTC_BATTERY was removed (2026-09-28): RTC charging stays off." \
    "Only GATBOX_RTC_CHARGE=ML2020 enables it, and only for a real ML2020 cell."
RTC_CHARGE="${GATBOX_RTC_CHARGE:-}"
[ -z "$RTC_CHARGE" ] || [ "$RTC_CHARGE" = ML2020 ] || die "GATBOX_RTC_CHARGE accepts only ML2020 (the official" \
    "rechargeable cell). A non-rechargeable cell must never be charged."

# Comment out every active rtc_bbat_vchg in config.txt, with a dated note. A dtparam line that also sets other
# parameters keeps them: "dtparam=a=1,rtc_bbat_vchg=3000000" -> "# GATBOX ...Was: <line>" + "dtparam=a=1".
rtc_guard_cfg() {   # <config.txt> <date>
    local tmp; tmp=$(mktemp)
    awk -v d="$2" '
        /^[[:space:]]*#/ || !/rtc_bbat_vchg/ { print; next }
        {
            print "# GATBOX " d ": RTC trickle charging disabled (the cell is not rechargeable). Was: " $0
            line = $0; sub(/^[[:space:]]+/, "", line)
            if (line ~ /^dtparam=/) {
                n = split(substr(line, 9), kv, ","); keep = ""
                for (i = 1; i <= n; i++) if (kv[i] !~ /^rtc_bbat_vchg/) keep = keep (keep == "" ? "" : ",") kv[i]
                if (keep != "") print "dtparam=" keep
            }
        }' "$1" > "$tmp" && cat "$tmp" > "$1"
    rm -f "$tmp"
}

[ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo bash $0"
[ "$(uname -m)" = aarch64 ] || die "expected 64-bit Pi OS (aarch64), got $(uname -m)"
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || die "$CFG not found. Is this Raspberry Pi OS Bookworm/Trixie?"
MODEL=$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo unknown)
case "$MODEL" in *"Pi 5"*) ;; *) warn "this is '$MODEL', not a Pi 5. Continuing, but the power fix is Pi 5-specific." ;; esac
U="${SUDO_USER:-}"
[ -n "$U" ] && [ "$U" != root ] || die "run via sudo from your normal user (so groups land on the right account)"
AP="${GATBOX_AP_PSK:-}"
if [ -n "$AP" ] && [ "$AP" != off ]; then
    [ "${#AP}" -ge 8 ] && [ "${#AP}" -le 63 ] || die "GATBOX_AP_PSK must be 8-63 characters (or 'off')"
fi
export DEBIAN_FRONTEND=noninteractive
REBOOT=0

printf '%sGDD-GAT/01 bootstrap%s  %s  %s  user=%s\n' "$M" "$N" "$MODEL" "$(. /etc/os-release; echo "$PRETTY_NAME")" "$U"

# 1 ---------------------------------------------------------------------------
step "1/7 packages"
apt-get update -q || warn "apt-get update failed (offline?): using the package lists already on the Pi"
apt-get install -y -q sigrok-cli python3-matplotlib rsync git curl util-linux-extra
log "sigrok-cli $(sigrok-cli --version 2>/dev/null | head -n 1 | awk '{print $2}')"
drivers=$(sigrok-cli -L 2>/dev/null || true)
if grep -q 'uni-t-ut61e-ser' <<<"$drivers"; then log "driver uni-t-ut61e-ser present"
else warn "uni-t-ut61e-ser not listed by sigrok-cli -L; check before trusting the logger"; fi

# 2 ---------------------------------------------------------------------------
step "2/7 serial-port squatters"
safe_purge() {   # purge only if nothing else would go with it; otherwise mask the services
    local pkg=$1; shift
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then log "$pkg not installed"; return; fi
    local gone
    gone=$(apt-get -s purge "$pkg" | awk '/^Purg /{print $2}' | sort -u | xargs)
    if [ "$gone" = "$pkg" ]; then
        apt-get purge -y -q "$pkg"
        log "purged $pkg"
    else
        systemctl mask --now "$@" >/dev/null 2>&1 || true
        warn "purging $pkg would also remove: $gone. Masked $* instead."
    fi
}
safe_purge modemmanager ModemManager.service
safe_purge brltty brltty.service brltty-udev.service

# 3 ---------------------------------------------------------------------------
step "3/7 groups, I2C, SPI"
grps=()
for g in dialout plugdev gpio i2c spi video; do getent group "$g" >/dev/null && grps+=("$g"); done
usermod -aG "$(IFS=,; echo "${grps[*]}")" "$U"
log "$U += ${grps[*]}"
if command -v raspi-config >/dev/null; then
    raspi-config nonint do_i2c 0 && raspi-config nonint do_spi 0 && log "I2C + SPI enabled"
fi
case "$(hostname)" in
    raspberrypi|catbox)
        old=$(hostname)
        raspi-config nonint do_hostname gatbox
        log "hostname $old -> gatbox"; REBOOT=1 ;;
    *)  log "hostname: $(hostname) (left as is)" ;;
esac

# 4 ---------------------------------------------------------------------------
step "4/7 power: no-PD supply fix"
if [ "${GATBOX_PSU_5A:-1}" = 1 ]; then
    cur=$(rpi-eeprom-config 2>/dev/null || true)
    if grep -q '^PSU_MAX_CURRENT=5000$' <<<"$cur"; then
        log "EEPROM already has PSU_MAX_CURRENT=5000"
    else
        tmp=$(mktemp)
        { grep -v '^PSU_MAX_CURRENT=' <<<"$cur"; echo "[all]"; echo "PSU_MAX_CURRENT=5000"; } > "$tmp"
        rpi-eeprom-config --apply "$tmp"
        rm -f "$tmp"
        log "EEPROM PSU_MAX_CURRENT=5000 staged (takes effect on reboot)"; REBOOT=1
    fi
else
    log "skipped (GATBOX_PSU_5A=0)"
fi
want=(usb_max_current_enable=1)
# RTC backup cell on J5. Charging stays OFF: the fitted cell is a primary (non-rechargeable) cell, and charging
# one can make it leak or vent. Only a real ML2020 may ever be charged (GATBOX_RTC_CHARGE=ML2020, checked above).
if [ "$RTC_CHARGE" = ML2020 ]; then
    want+=(dtparam=rtc_bbat_vchg=3000000)
    warn "RTC trickle charge ON (GATBOX_RTC_CHARGE=ML2020): only for the official rechargeable ML2020 cell"
elif grep -qE '^[^#]*rtc_bbat_vchg' "$CFG"; then
    [ -e "$CFG.pre-gatbox" ] || cp "$CFG" "$CFG.pre-gatbox"
    cp "$CFG" "$CFG.pre-rtc-guard"
    rtc_guard_cfg "$CFG" "$(date +%F)"
    warn "config.txt was CHARGING the RTC cell (rtc_bbat_vchg): commented out (backup: $CFG.pre-rtc-guard)."
    warn "REBOOT NOW so charging stops. The fitted cell is not rechargeable."
    REBOOT=1
else
    log "RTC charging off (no rtc_bbat_vchg in config.txt)"
fi
missing=()
for l in "${want[@]}"; do grep -qxF "$l" "$CFG" || missing+=("$l"); done
if ((${#missing[@]})); then
    [ -e "$CFG.pre-gatbox" ] || cp "$CFG" "$CFG.pre-gatbox"
    { echo; echo "[all]"; echo "# GATBOX (gatbox-bootstrap.sh)"; printf '%s\n' "${missing[@]}"; } >> "$CFG"
    log "config.txt += ${missing[*]}  (backup: $CFG.pre-gatbox)"; REBOOT=1
else
    log "config.txt already has ${want[*]}"
fi

# 5 + 6 -------------------------------------------------------------------------
step "5/7 udev symlink + 6/7 logger, tools, web view, launcher"
payload /
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
log "installed: gatbox-raillog  gatbox-status  gatbox-rail-report  gatbox-web  (in /usr/local/bin)"
log "menu: Accessories > GATBOX Rail Monitor"
[ -e /dev/gatbox-dmm ] && log "adapter already visible: /dev/gatbox-dmm -> $(readlink -f /dev/gatbox-dmm)" \
                       || log "adapter not plugged in yet (fine)"
mkdir -p /var/log/journal
systemctl restart systemd-journald
systemctl daemon-reload
systemctl enable gatbox-raillog.service >/dev/null
systemctl restart gatbox-raillog.service
log "gatbox-raillog enabled + started"

# gatbox-web's fonts: Chakra Petch + Share Tech Mono (SIL OFL), the project's design-token fonts. Downloaded rather
# than embedded, pinned to one google/fonts commit and checked by sha256. The Pi serves them itself, since the
# hotspot has no internet. Without them the page falls back to system fonts.
FONTS_REV=23e54b51ddffbc7713c583748e3bd86f62b1fa4a
FONTDIR=/usr/local/share/gatbox-web/fonts
fetch_font() {   # <path in google/fonts> <local name> <sha256>
    local f="$FONTDIR/$2"
    echo "$3  $f" | sha256sum -c --status 2>/dev/null && return 0
    curl -fsSL --retry 2 -o "$f.part" "https://raw.githubusercontent.com/google/fonts/$FONTS_REV/$1" \
        && echo "$3  $f.part" | sha256sum -c --status && mv "$f.part" "$f" && chmod 644 "$f" && return 0
    rm -f "$f.part"
    return 1
}
install -d -m 755 "$FONTDIR"
if fetch_font ofl/chakrapetch/ChakraPetch-SemiBold.ttf ChakraPetch-SemiBold.ttf 45264de3204ddbd5fb3e14a2402acd5c630d16650ae5fc221d2c52da46a6734b \
    && fetch_font ofl/chakrapetch/OFL.txt ChakraPetch-OFL.txt 13831d02389d917d22fcfa6c79f98f8acbf61d230add0e73a4d4dc5f3bfb9e56 \
    && fetch_font ofl/sharetechmono/ShareTechMono-Regular.ttf ShareTechMono-Regular.ttf 9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677 \
    && fetch_font ofl/sharetechmono/OFL.txt ShareTechMono-OFL.txt 9d96f445b6e9c701428811d0177f894874f8d6f07ecc30d568c506542368f3ff; then
    log "web fonts in $FONTDIR"
else
    warn "couldn't fetch the web fonts (no internet?). The page falls back to system fonts; re-run later to add them."
fi
systemctl enable gatbox-web.service >/dev/null
systemctl restart gatbox-web.service
log "gatbox-web enabled + started: http://gatbox.local/ (http://10.42.0.1/ on the GATBOX hotspot)"
systemctl enable gatbox-rtc-sync.timer >/dev/null
systemctl restart gatbox-rtc-sync.timer
systemctl start gatbox-rtc-sync.service || warn "gatbox-rtc-sync failed: journalctl -u gatbox-rtc-sync"
log "gatbox-rtc-sync.timer enabled (2 min after boot, then hourly). Stamp:" \
    "$(cat /var/lib/gatbox/rtc-synced 2>/dev/null || echo 'none yet (not NTP-synced?)')"
log "RTC charging_voltage: $(cat /sys/class/rtc/rtc0/charging_voltage 2>/dev/null || echo n/a) (0 = off)"

# 7 ---------------------------------------------------------------------------
step "7/7 optional: fallback hotspot"
if [ "$AP" = off ]; then
    systemctl disable gatbox-ap-fallback.service >/dev/null 2>&1 || true
    nmcli connection delete gatbox-ap >/dev/null 2>&1 || true
    log "hotspot fallback removed"
elif [ -n "$AP" ]; then
    nmcli connection delete gatbox-ap >/dev/null 2>&1 || true
    nmcli connection add type wifi ifname wlan0 con-name gatbox-ap autoconnect no ssid GATBOX \
        802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
        ipv4.method shared ipv6.method disabled \
        wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
        wifi-sec.psk "$AP" >/dev/null
    systemctl enable gatbox-ap-fallback.service >/dev/null
    log "hotspot 'GATBOX' armed: comes up ~60 s after boot only if no known Wi-Fi/Ethernet"
elif systemctl -q is-enabled gatbox-ap-fallback.service 2>/dev/null; then
    log "hotspot fallback already armed (left as is; GATBOX_AP_PSK=off removes it)"
else
    log "skipped (set GATBOX_AP_PSK to enable)"
fi

# ---------------------------------------------------------------------------
printf '\n%sdone.%s\n' "$C" "$N"
log "known Wi-Fi:  $(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print $1}' | paste -sd, -)"
log "timezone:     $(timedatectl show -p Timezone --value)"
if [ "$REBOOT" = 1 ]; then
    printf '\n   %sREBOOT NOW:%s  sudo reboot   (EEPROM/config.txt/hostname changes need it; new groups need a fresh login)\n\n' "$Y" "$N"
else
    printf '\n   Log out/in (or reboot) once so the new groups apply.\n\n'
fi
