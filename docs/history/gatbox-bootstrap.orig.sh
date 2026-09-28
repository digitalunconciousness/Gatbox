#!/usr/bin/env bash
# gatbox-bootstrap.sh — GDD-GAT/01 · Greybard Diagnostics and Design
#
# Fresh Raspberry Pi OS Trixie (64-bit, Desktop) on a Pi 5  ->  GATBOX rail flight recorder.
# Leaves the desktop alone. Safe to re-run.
#
#   sudo bash gatbox-bootstrap.sh                                   # base + DMM logger
#   sudo GATBOX_AP_PSK='at-least-8-chars' bash gatbox-bootstrap.sh  # + fallback "GATBOX" hotspot ('off' removes it)
#   sudo GATBOX_RTC_BATTERY=1 bash gatbox-bootstrap.sh              # ONLY with the official ML2020 RTC battery fitted
#   bash gatbox-bootstrap.sh --extract DIR                          # write the payload to DIR for review, change nothing
#
# Other knobs: GATBOX_PSU_5A=0 skips the PSU_MAX_CURRENT EEPROM change.
#
# What it does:
#   1  packages: sigrok-cli, python3-matplotlib, rsync, git
#   2  removes ModemManager (hijacks ttyUSB0) and brltty (grabs USB-serial adapters)
#   3  groups (dialout, plugdev, gpio, i2c, spi, video) for your user; enables I2C + SPI
#   4  power fix: PSU_MAX_CURRENT=5000 (EEPROM) + usb_max_current_enable=1 (config.txt)
#   5  /dev/gatbox-dmm udev symlink for the PL-2303
#   6  gatbox-raillog service, gatbox-status, gatbox-rail-report, desktop menu launcher
#   7  persistent journal; optional hotspot fallback; optional RTC trickle charge

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
#   # clock=ntp | clock=unverified ...        (line 2: was NTP synced at session start?)
#
# uptime_s is monotonic: durations stay right even if the wall clock steps.
# Runs as root under systemd (the LED sysfs needs it).

set -u

DRIVER="${GATBOX_DMM_DRIVER:-uni-t-ut61e-ser}"
LINK="${GATBOX_DMM_PORT:-/dev/gatbox-dmm}"
LOGDIR="${GATBOX_LOGDIR:-/var/log/gatbox}"
RUNDIR="${RUNTIME_DIRECTORY:-/run/gatbox}"
LED="${GATBOX_LED:-/sys/class/leds/ACT}"
CLOCK_WAIT="${GATBOX_CLOCK_WAIT:-90}"   # seconds to wait for NTP before logging starts

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

# --- clock ------------------------------------------------------------------
clock_synced() { [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; }
clock_label() {
    if clock_synced; then echo "ntp"
    else echo "unverified (no NTP at session start - absolute times may be off; uptime_s durations are good)"
    fi
}

if ! clock_synced; then
    say "waiting up to ${CLOCK_WAIT}s for NTP so timestamps are real"
    for ((i = 0; i < CLOCK_WAIT; i += 3)); do clock_synced && break; sleep 3; done
    if clock_synced; then say "clock synced"
    else say "no NTP; logging anyway. Fit the RTC battery or push time from the laptop."
    fi
fi

# --- one session: reads sigrok stdout, returns 0 if any reading was logged ---
session() {
    local F="" state=0 tag val unit rest up _
    while read -r tag val unit rest; do
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
            {
                echo "iso_time,epoch,value,unit,flags,uptime_s"
                echo "# clock=$(clock_label)"
            } > "$F"
            chmod 644 "$F"
            echo "$F" > "$RUNDIR/current"
            rm -f "$RUNDIR/last-error"
            say "logging to $F"
        fi
        read -r up _ < /proc/uptime
        # bash-builtin time formatting + EPOCHREALTIME: no forks per sample
        printf '%(%FT%T)T,%s,%s,%s,%s,%s\n' -1 "$EPOCHREALTIME" "$val" "$unit" "$rest" "$up" >> "$F"
        state=$((1 - state)); led "$state"
    done
    rm -f "$RUNDIR/current"
    [ -n "$F" ]
}

# --- main loop ----------------------------------------------------------------
misses=0
while :; do
    if [ ! -e "$LINK" ]; then
        say "waiting for $LINK (PL-2303 adapter)"
        until [ -e "$LINK" ]; do led 0; sleep 2; done
        misses=0
    fi
    PORT=$(readlink -f "$LINK")
    : > "$RUNDIR/last-error"

    # stdbuf: line-buffered, so samples hit the file as they happen (matters if power dies)
    if stdbuf -oL sigrok-cli --driver "$DRIVER:conn=$PORT" -O analog --continuous 2>&1 | session; then
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

# clock
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then
    ok clock "NTP synced"
else
    warn clock "not NTP synced: wall times may be off (RTC battery, or from the laptop: ssh gatbox.local \"sudo date -s @\$(date +%s)\")"
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
  * steady stats: mean / min / max / p-p
  * EXCURSIONS: contiguous runs outside [lo, hi], with start time, duration,
    and worst value. This is the "what was the rail doing when it crashed" list
  * OPEN-INPUT events (inf/OL): a lead fell off, or the rail died entirely
  * GAPS in the timeline (>10 s between samples): optical link dropped
  * CLOCK STEPS: the wall clock jumped mid-session (NTP arrived late, or date -s).
    Durations use the monotonic uptime_s column when present, so they stay right.

A plot is written if matplotlib is available (python3-matplotlib); the text
report works without it. If the CSV's folder isn't writable, the plot goes to
the current directory.
"""

import argparse
import csv
import glob
import math
import os
import statistics as st
import sys
from collections import namedtuple
from datetime import datetime, timezone

Row = namedtuple("Row", "epoch iso v unit flags up")
DEFAULT_DIR = "/var/log/gatbox"


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
        try:
            v = float(val)  # 'inf' / '-inf' parse as infinities
        except ValueError:
            v = math.nan
        up = None
        if len(line) >= 6:
            try:
                up = float(line[5])
            except ValueError:
                up = None
        rows.append(Row(e, iso, v, unit, flags, up))
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
    finite = [r.v for r in rows if math.isfinite(r.v)]

    print(f"file:      {args.csvfile}")
    for n in notes:
        print(f"note:      {n}")
    print(f"session:   {rows[0].iso}  ->  {rows[-1].iso}   ({fmt_dur(dur)})")
    print(f"samples:   {len(rows)}   ({len(rows) / max(dur, 1):.2f} S/s)"
          f"   timebase: {'monotonic uptime' if mono else 'wall clock'}")
    if finite:
        print(f"stats:     mean {st.mean(finite):+.4f} V   "
              f"min {min(finite):+.4f}   max {max(finite):+.4f}   "
              f"p-p {(max(finite) - min(finite)) * 1000:.1f} mV")
    print(f"window:    [{args.lo}, {args.hi}] V")
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
        print(f"OPEN-INPUT events ({len(ol)}): lead detached or rail fully dead:")
        for a, b in ol:
            print(f"  {rows[a].iso}  for {fmt_dur(span(a, b))}")
        print()

    # --- excursions -----------------------------------------------------------
    def outside(r):
        return math.isfinite(r.v) and not (args.lo <= r.v <= args.hi)

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
    else:
        print("EXCURSIONS: none. The rail held the window all session.")
        print("If the game still crashed, this log exonerates this rail;")
        print("next suspects: the other rails, on-board regulation, thermals.")
        print()

    # --- plot -----------------------------------------------------------------
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
    if steps:  # wall clock is unreliable: plot elapsed monotonic minutes instead
        xs = [(r.up - rows[0].up) / 60 for r in rows]
        xlabel = "elapsed (min, monotonic; wall clock stepped mid-session)"
    else:
        xs = [datetime.fromtimestamp(r.epoch, tz=timezone.utc).astimezone() for r in rows]
        xlabel = None
    vs = [r.v if math.isfinite(r.v) else math.nan for r in rows]

    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)
    ax.plot(xs, vs, color=CYAN, lw=0.9)
    ax.axhline(args.lo, color=DIM, ls="--", lw=0.8)
    ax.axhline(args.hi, color=DIM, ls="--", lw=0.8)
    for a, b in exc:
        ax.axvspan(xs[a], xs[b], color="#ff4d6d", alpha=0.35)
    ax.set_ylabel("V DC", family="monospace", color=TXT)
    if xlabel:
        ax.set_xlabel(xlabel, family="monospace", color=TXT, fontsize=8)
    ax.set_title(f"GDD-GAT/01 rail log · {rows[0].iso[:10]}",
                 family="monospace", color=TXT, fontsize=10)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=8)
    ax.grid(color=GRID, lw=0.4, alpha=0.6)
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
apt-get update -q
apt-get install -y -q sigrok-cli python3-matplotlib rsync git
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
if [ "${GATBOX_RTC_BATTERY:-0}" = 1 ]; then
    want+=(dtparam=rtc_bbat_vchg=3000000)
    warn "RTC trickle charge ON: only for the rechargeable ML2020 cell, never a CR2032"
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
step "5/7 udev symlink + 6/7 logger, tools, launcher"
payload /
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
log "installed: gatbox-raillog  gatbox-status  gatbox-rail-report  (in /usr/local/bin)"
log "menu: Accessories > GATBOX Rail Monitor"
[ -e /dev/gatbox-dmm ] && log "adapter already visible: /dev/gatbox-dmm -> $(readlink -f /dev/gatbox-dmm)" \
                       || log "adapter not plugged in yet (fine)"
mkdir -p /var/log/journal
systemctl restart systemd-journald
systemctl daemon-reload
systemctl enable gatbox-raillog.service >/dev/null
systemctl restart gatbox-raillog.service
log "gatbox-raillog enabled + started"

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
