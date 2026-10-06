#!/usr/bin/env bash
# gatbox-bootstrap.sh — GDD-GAT/01 · Greybard Diagnostics and Design
#
# Fresh Raspberry Pi OS Trixie (64-bit, Desktop) on a Pi 5  ->  GATBOX, installed straight from this git checkout:
#
#   git clone https://github.com/digitalunconciousness/Gatbox.git ~/gatbox && cd ~/gatbox
#   sudo bash bootstrap/gatbox-bootstrap.sh                                   # everything
#   sudo GATBOX_AP_PSK='at-least-8-chars' bash bootstrap/gatbox-bootstrap.sh  # + fallback "GATBOX" hotspot ('off' removes it)
#   bash bootstrap/gatbox-bootstrap.sh --check                                # what a run would change (no root, changes nothing)
#   bash bootstrap/gatbox-bootstrap.sh --extract DIR                          # install the files under DIR instead of / (review)
#
# Leaves the desktop alone. Idempotent: a re-run changes nothing unless the checkout changed, and restarts only the
# services whose files changed (a logger restart ends the live session, so it never happens for nothing). Once
# everything is installed a re-run needs no internet (the bar, the hotspot).
#
# Other knobs: GATBOX_PSU_5A=0 skips the PSU_MAX_CURRENT EEPROM change.
# RTC: trickle charging stays OFF, because the J5 cell is not rechargeable. Any active rtc_bbat_vchg line in
# config.txt gets commented out. GATBOX_RTC_CHARGE=ML2020 (that exact value) is reserved for a real ML2020 cell;
# anything else is refused. GATBOX_RTC_BATTERY was removed on 2026-09-28 and is refused too.
#
# What it does:
#   1  packages: sigrok-cli, python3-matplotlib, python3-qrcode (scanner labels), rsync, git, curl, util-linux-extra (hwclock), the minipro build
#      deps, mame (for `mame -romident` only). apt is skipped entirely when they're all present.
#   2  removes ModemManager (hijacks ttyUSB0) and brltty (grabs USB-serial adapters)
#   3  groups (dialout, plugdev, gpio, i2c, spi, video) for your user; I2C + SPI on; hostname catbox/raspberrypi -> gatbox
#   4  power fix: PSU_MAX_CURRENT=5000 (EEPROM) + usb_max_current_enable=1 (config.txt); RTC charging guard
#   5  files from this checkout (table below): logger, web view, RTC sync, hotspot fallback, tools, udev, journald,
#      menu launcher
#   6  services: the gatbox-dump user + /srv/gatbox/roms + its spool, the gatbox-manuals group + /srv/gatbox/manuals
#      (sysusers.d, tmpfiles.d), gatbox-raillog,
#      gatbox-web (+ its fonts), gatbox-scand (the barcode scanner), gatbox-dump.path (dashboard dumps), rtc-sync.timer
#   7  desktop (for the sudo user): the 7" kiosk autostart (gatbox-kiosk on|off), Pi OS autotouch off and its
#      port-pinned touch line removed, so the panel works on either HDMI port with real touch events
#   8  minipro (XGecu T48), built from a pinned upstream tag; its T48 part-name list for the dashboard
#   9  optional hotspot fallback

set -euo pipefail

REPO=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)

# repo path -> installed path, mode [optional]. Everything the bootstrap installs from the checkout. "optional" = skip
# it if the checkout doesn't have it (the roster: workplace data, git-ignored, so a fresh clone lacks it).
MANIFEST="
backend/gatbox-raillog                          /usr/local/bin/gatbox-raillog                         755
backend/gatbox-raillog.service                  /etc/systemd/system/gatbox-raillog.service            644
backend/gatbox-web                              /usr/local/bin/gatbox-web                             755
backend/gatbox-web.service                      /etc/systemd/system/gatbox-web.service                644
backend/gatbox-scand                            /usr/local/bin/gatbox-scand                           755
backend/gatbox-scand.service                    /etc/systemd/system/gatbox-scand.service              644
backend/gatbox-dump.service                     /etc/systemd/system/gatbox-dump.service               644
backend/gatbox-dump.path                        /etc/systemd/system/gatbox-dump.path                  644
bootstrap/files/sysusers-gatbox.conf            /etc/sysusers.d/gatbox.conf                           644
bootstrap/files/tmpfiles-gatbox.conf            /etc/tmpfiles.d/gatbox.conf                           644
backend/gatbox-rtc-sync                         /usr/local/sbin/gatbox-rtc-sync                       755
backend/gatbox-rtc-sync.service                 /etc/systemd/system/gatbox-rtc-sync.service           644
backend/gatbox-rtc-sync.timer                   /etc/systemd/system/gatbox-rtc-sync.timer             644
backend/gatbox-ap-fallback                      /usr/local/sbin/gatbox-ap-fallback                    755
backend/gatbox-sync                              /usr/local/bin/gatbox-sync                            755
backend/gatbox-sync.service                      /etc/systemd/system/gatbox-sync.service               644
backend/gatbox-sync.timer                        /etc/systemd/system/gatbox-sync.timer                 644
backend/gatbox-wifi                              /usr/local/sbin/gatbox-wifi                           755
backend/gatbox-wifi.service                      /etc/systemd/system/gatbox-wifi.service               644
backend/gatbox-wifi.path                         /etc/systemd/system/gatbox-wifi.path                  644
backend/gatbox-ap-fallback.service              /etc/systemd/system/gatbox-ap-fallback.service        644
backend/gatbox-ap-fallback.timer                /etc/systemd/system/gatbox-ap-fallback.timer          644
tools/gatbox-status                             /usr/local/bin/gatbox-status                          755
tools/gatbox-rail-report                        /usr/local/bin/gatbox-rail-report                     755
tools/gatbox-kiosk                              /usr/local/bin/gatbox-kiosk                           755
backend/gatbox-kiosk-launch                     /usr/local/bin/gatbox-kiosk-launch                    755
backend/gatbox-meta                             /usr/local/bin/gatbox-meta                            755
tools/gatbox-replay                             /usr/local/bin/gatbox-replay                          755
tools/gatbox-labels                             /usr/local/bin/gatbox-labels                          755
tools/gatbox-dump                               /usr/local/bin/gatbox-dump                            755
tools/gatbox-mame-roms                          /usr/local/bin/gatbox-mame-roms                       755
tools/gatbox-manuals                            /usr/local/bin/gatbox-manuals                         755
backend/gatboxlib/__init__.py                   /usr/local/lib/gatbox/gatboxlib/__init__.py           644
backend/gatboxlib/modes.py                      /usr/local/lib/gatbox/gatboxlib/modes.py              644
backend/gatboxlib/profiles.py                   /usr/local/lib/gatbox/gatboxlib/profiles.py           644
backend/gatboxlib/csvlog.py                     /usr/local/lib/gatbox/gatboxlib/csvlog.py             644
backend/gatboxweb/__init__.py                   /usr/local/lib/gatbox/gatboxweb/__init__.py           644
backend/gatboxweb/config.py                     /usr/local/lib/gatbox/gatboxweb/config.py             644
backend/gatboxweb/server.py                     /usr/local/lib/gatbox/gatboxweb/server.py             644
backend/gatboxweb/phone.py                      /usr/local/lib/gatbox/gatboxweb/phone.py              644
backend/gatboxweb/sessions.py                   /usr/local/lib/gatbox/gatboxweb/sessions.py           644
backend/gatboxweb/report.py                     /usr/local/lib/gatbox/gatboxweb/report.py             644
backend/gatboxweb/live.py                       /usr/local/lib/gatbox/gatboxweb/live.py               644
backend/gatboxweb/meter.py                      /usr/local/lib/gatbox/gatboxweb/meter.py              644
backend/gatboxweb/captures.py                   /usr/local/lib/gatbox/gatboxweb/captures.py           644
backend/gatboxweb/roster.py                     /usr/local/lib/gatbox/gatboxweb/roster.py             644
backend/gatboxweb/system.py                     /usr/local/lib/gatbox/gatboxweb/system.py             644
backend/gatboxweb/devices.py                    /usr/local/lib/gatbox/gatboxweb/devices.py            644
backend/gatboxweb/dump.py                       /usr/local/lib/gatbox/gatboxweb/dump.py               644
backend/gatboxweb/mame.py                       /usr/local/lib/gatbox/gatboxweb/mame.py               644
backend/gatboxweb/manuals.py                    /usr/local/lib/gatbox/gatboxweb/manuals.py            644
backend/gatboxweb/wifi.py                       /usr/local/lib/gatbox/gatboxweb/wifi.py               644
web/dash/index.html                             /usr/local/share/gatbox-web/dash/index.html           644
web/dash/dash.css                               /usr/local/share/gatbox-web/dash/dash.css             644
web/dash/dash.js                                /usr/local/share/gatbox-web/dash/dash.js              644
web/dash/chart.js                               /usr/local/share/gatbox-web/dash/chart.js             644
data/profiles.json                              /usr/local/share/gatbox/profiles.json                 644
data/gatbox-machine-specs.json                  /usr/local/share/gatbox/gatbox-machine-specs.json     644
data/eproms.json                                /usr/local/share/gatbox/eproms.json                   644
data/gatbox-barcade-roster.json                 /usr/local/share/gatbox/gatbox-barcade-roster.json    644  optional
data/gatbox-mame-sets.json                      /usr/local/share/gatbox/gatbox-mame-sets.json         644  optional
data/gatbox-manuals.json                        /usr/local/share/gatbox/gatbox-manuals.json           644  optional
bootstrap/files/99-gatbox-dmm.rules             /etc/udev/rules.d/99-gatbox-dmm.rules                 644
bootstrap/files/minipro-0.7.4/60-minipro.rules  /etc/udev/rules.d/60-minipro.rules                    644
bootstrap/files/minipro-0.7.4/61-minipro-plugdev.rules /etc/udev/rules.d/61-minipro-plugdev.rules    644
bootstrap/files/minipro-0.7.4/61-minipro-uaccess.rules /etc/udev/rules.d/61-minipro-uaccess.rules    644
bootstrap/files/journald-gatbox.conf            /etc/systemd/journald.conf.d/gatbox.conf              644
bootstrap/files/gatbox-rail-monitor.desktop     /usr/share/applications/gatbox-rail-monitor.desktop   644
"
# repo path -> path under the desktop user's home, mode (owned by that user). XDG autostart entries: the kiosk, and a
# Hidden=true override that turns off Pi OS's autotouch (it pins touch to one USB + one HDMI port, with mouse emulation).
USER_MANIFEST="
bootstrap/files/user/gatbox-kiosk.desktop       .config/autostart/gatbox-kiosk.desktop                644
bootstrap/files/user/autotouch-off.desktop      .config/autostart/autotouch.desktop                   644
"
# the line autotouch wrote into ~/.config/labwc/rc.xml for the Waveshare: removed, so touch stays unmapped (either
# HDMI port, any USB port) and sends real touch events (no mouse emulation) for the dashboard's gestures
TOUCH_LINE='<touch[^>]*deviceName="WaveShare WS170120'
PKGS=(sigrok-cli python3-matplotlib python3-qrcode rsync git curl util-linux-extra
      build-essential pkg-config libusb-1.0-0-dev zlib1g-dev       # minipro build
      libarchive-tools                                              # bsdtar: XGecu .rar -> T48 firmware (minipro's dump-alg script)
      poppler-utils                                                 # pdftoppm / pdftotext / pdfinfo: the manuals viewer
      mame)                                                        # mame -romident only, no gameplay
# minipro: pinned upstream release. Bump deliberately: check its T48 support and changelog, and refresh
# bootstrap/files/minipro-<tag>/ (its udev rules, which its `make install` skips on Pi OS) at the same time.
MINIPRO_TAG=0.7.4
PARTS_LIST=/usr/local/share/gatbox/minipro-parts-T48.txt    # the dashboard's T48 part names: minipro -q T48 -l
parts_ok() { head -n 1 "$PARTS_LIST" 2>/dev/null | grep -qxF "# minipro $(minipro_version) T48"; }
# each machine's ROM chips (the dashboard's checklist), built from MAME's own list and gatbox-mame-sets.json (a
# git-ignored floor list, like the roster); rebuilt when MAME or the list changes. The checkout's copy of the tool
# answers --check, so a --check run before the first install sees the same answer.
MAME_ROMS=/usr/local/share/gatbox/mame-roms.json
mame_roms_wanted() { [ -f "$REPO/data/gatbox-mame-sets.json" ] && [ -x /usr/games/mame ]; }
mame_roms_ok() { GATBOX_DATA="$REPO/data" python3 "$REPO/tools/gatbox-mame-roms" --check "$MAME_ROMS"; }
MINIPRO_URL=https://gitlab.com/DavidGriffith/minipro.git
MINIPRO_SRC=/usr/local/src/minipro
# gatbox-web's fonts: Chakra Petch + Share Tech Mono (SIL OFL), the project's design-token fonts, pinned to one
# google/fonts commit and checked by sha256. The Pi serves them itself, since the hotspot has no internet.
FONTS_REV=23e54b51ddffbc7713c583748e3bd86f62b1fa4a
FONTDIR=/usr/local/share/gatbox-web/fonts
FONTS="
ofl/chakrapetch/ChakraPetch-SemiBold.ttf      ChakraPetch-SemiBold.ttf   45264de3204ddbd5fb3e14a2402acd5c630d16650ae5fc221d2c52da46a6734b
ofl/chakrapetch/OFL.txt                       ChakraPetch-OFL.txt        13831d02389d917d22fcfa6c79f98f8acbf61d230add0e73a4d4dc5f3bfb9e56
ofl/sharetechmono/ShareTechMono-Regular.ttf   ShareTechMono-Regular.ttf  9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677
ofl/sharetechmono/OFL.txt                     ShareTechMono-OFL.txt      9d96f445b6e9c701428811d0177f894874f8d6f07ecc30d568c506542368f3ff
"

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

# --- files from the checkout ----------------------------------------------------
ROOT=""          # --extract DIR: install under DIR instead of /
CHANGED=()       # installed paths this run created or changed
DIFFS=()         # --check: what a run would change
manifest() { grep -v '^[[:space:]]*$' <<<"$MANIFEST"; }
put_file() {     # <repo path> <installed path> <mode> [optional]
    local src="$REPO/$1" dst="$ROOT$2"
    [ -f "$src" ] || { [ "${4:-}" = optional ] && return 0; die "missing in the checkout: $1"; }
    if [ -f "$dst" ] && cmp -s "$src" "$dst" && [ "$(stat -c %a "$dst")" = "$3" ]; then return 0; fi
    install -D -m "$3" "$src" "$dst"
    CHANGED+=("$2")
}
check_file() {   # <repo path> <installed path> <mode> [optional]
    [ -f "$REPO/$1" ] || { [ "${4:-}" = optional ] && return 0; DIFFS+=("MISSING    $1 (not in the checkout)"); return 0; }
    if [ ! -f "$2" ]; then DIFFS+=("new file   $2")
    elif ! cmp -s "$REPO/$1" "$2"; then DIFFS+=("update     $2")
    elif [ "$(stat -c %a "$2")" != "$3" ]; then DIFFS+=("mode $3   $2"); fi
}
user_manifest() { grep -v '^[[:space:]]*$' <<<"$USER_MANIFEST"; }
put_user_file() {   # <repo path> <path under $HOME_U> <mode>: installed owned by the desktop user $U
    local src="$REPO/$1" dst="$ROOT$HOME_U/$2"
    [ -f "$src" ] || die "missing in the checkout: $1"
    if [ -f "$dst" ] && cmp -s "$src" "$dst" && [ "$(stat -c %a "$dst")" = "$3" ]; then return 0; fi
    if [ -n "$ROOT" ]; then
        install -D -m "$3" "$src" "$dst"
    else
        install -d -m 755 -o "$U" -g "$(id -gn "$U")" "$(dirname "$dst")"
        install -m "$3" -o "$U" -g "$(id -gn "$U")" "$src" "$dst"
    fi
    CHANGED+=("~$U/$2")
}
changed() {      # changed <glob>...: did this run change an installed path matching one of them?
    local p g
    for p in "${CHANGED[@]}"; do for g in "$@"; do [[ $p == $g ]] && return 0; done; done
    return 1
}
pkg_ok() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'ok installed'; }
# minipro --version exits 1 when no programmer is plugged in, but still prints its version and supported list
minipro_info() { minipro --version 2>&1 || true; }
minipro_version() { minipro_info | sed -n 's/^minipro version \([^ ]*\).*/\1/p' | head -n 1; }
font_ok() { echo "$2  $FONTDIR/$1" | sha256sum -c --status 2>/dev/null; }

[ -f "$REPO/backend/gatbox-raillog" ] || die "run this from a GATBOX checkout (no backend/gatbox-raillog under $REPO)"

if [ "${1:-}" = "--extract" ]; then
    [ -n "${2:-}" ] || { echo "usage: $0 --extract DIR" >&2; exit 2; }
    ROOT=${2%/}; U=$(id -un); HOME_U=/home/$U
    while read -r src dst mode opt; do put_file "$src" "$dst" "$mode" "$opt"; done < <(manifest)
    while read -r src rel mode; do put_user_file "$src" "$rel" "$mode"; done < <(user_manifest)
    echo "${#CHANGED[@]} file(s) written under $ROOT"
    exit 0
fi

if [ "${1:-}" = "--check" ]; then
    while read -r src dst mode opt; do check_file "$src" "$dst" "$mode" "$opt"; done < <(manifest)
    while read -r src rel mode; do check_file "$src" "$HOME/$rel" "$mode"; done < <(user_manifest)
    grep -qE "$TOUCH_LINE" "$HOME/.config/labwc/rc.xml" 2>/dev/null \
        && DIFFS+=("edit       ~/.config/labwc/rc.xml: remove autotouch's port-pinned Waveshare touch line")
    for p in "${PKGS[@]}"; do pkg_ok "$p" || DIFFS+=("install    package $p"); done
    [ "$(minipro_version)" = "$MINIPRO_TAG" ] || DIFFS+=("build      minipro $MINIPRO_TAG (installed: $(minipro_version))")
    while read -r _ name sum; do font_ok "$name" "$sum" || DIFFS+=("fetch      font $name"); done < <(grep -v '^$' <<<"$FONTS")
    for l in usb_max_current_enable=1; do grep -qxF "$l" /boot/firmware/config.txt || DIFFS+=("config.txt += $l"); done
    grep -qE '^[^#]*rtc_bbat_vchg' /boot/firmware/config.txt && [ -z "$RTC_CHARGE" ] && DIFFS+=("config.txt: comment out rtc_bbat_vchg (RTC CHARGING IS ON)")
    for u in gatbox-raillog.service gatbox-web.service gatbox-scand.service gatbox-dump.path gatbox-rtc-sync.timer \
             gatbox-sync.timer; do
        systemctl -q is-enabled "$u" 2>/dev/null || DIFFS+=("enable     $u"); done
    getent passwd gatbox-dump >/dev/null || DIFFS+=("create     user gatbox-dump (sysusers.d)")
    getent passwd gatbox-sync >/dev/null || DIFFS+=("create     user gatbox-sync (sysusers.d)")
    getent group gatbox-wifi >/dev/null || DIFFS+=("create     group gatbox-wifi (sysusers.d)")
    [ -d /var/spool/gatbox-wifi ] || DIFFS+=("create     /var/spool/gatbox-wifi (tmpfiles.d)")
    [ -d /srv/gatbox/roms ] && [ -d /var/spool/gatbox-dump ] || DIFFS+=("create     /srv/gatbox/roms + /var/spool/gatbox-dump (tmpfiles.d)")
    [ -d /etc/gatbox ] || DIFFS+=("create     /etc/gatbox (tmpfiles.d; hub.conf goes in by hand)")
    # Only when the hotspot is armed at all: an unarmed Pi is not missing anything.
    if systemctl -q is-enabled gatbox-ap-fallback.service 2>/dev/null \
       || systemctl -q is-enabled gatbox-ap-fallback.timer 2>/dev/null; then
        systemctl -q is-enabled gatbox-ap-fallback.timer 2>/dev/null \
            || DIFFS+=("enable     gatbox-ap-fallback.timer (replaces the boot-only service)")
    fi
    id -nG "$(id -un)" | grep -qw gatbox-dump || DIFFS+=("group      $(id -un) += gatbox-dump (the dump archive)")
    getent group gatbox-manuals >/dev/null || DIFFS+=("create     group gatbox-manuals (sysusers.d)")
    [ -d /srv/gatbox/manuals ] || DIFFS+=("create     /srv/gatbox/manuals (tmpfiles.d)")
    getent group gatbox-manuals >/dev/null && ! getent group gatbox-manuals | cut -d: -f4 | tr ',' '\n' | grep -qx "$(id -un)" \
        && DIFFS+=("group      $(id -un) += gatbox-manuals (the manuals folder)")
    command -v minipro >/dev/null && ! parts_ok && DIFFS+=("generate   $PARTS_LIST")
    mame_roms_wanted && ! mame_roms_ok && DIFFS+=("generate   $MAME_ROMS (from mame -listxml: a minute or two)")
    if ((${#DIFFS[@]})); then printf 'a run would change:\n'; printf '  %s\n' "${DIFFS[@]}"; exit 3; fi
    echo "nothing to change: this Pi matches the checkout"
    exit 0
fi

# Anything that is not exactly --check or --extract reached here, which means a real
# run. A mistyped flag must not install files and restart services on a live Pi: that
# happened with "--checksudo", a paste that ran the whole install instead of the
# read-only report it asked for. Refuse before the root check, so the message is the
# same with or without sudo.
if [ -n "${1:-}" ]; then
    printf 'unknown argument: %s\n\n' "$1" >&2
    printf 'usage:\n' >&2
    printf '  sudo bash %s                 install\n' "$0" >&2
    printf '  bash %s --check              what a run would change (no root, changes nothing)\n' "$0" >&2
    printf '  bash %s --extract DIR        install under DIR instead of / (review)\n' "$0" >&2
    exit 2
fi

[ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo bash $0"
[ "$(uname -m)" = aarch64 ] || die "expected 64-bit Pi OS (aarch64), got $(uname -m)"
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || die "$CFG not found. Is this Raspberry Pi OS Bookworm/Trixie?"
MODEL=$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo unknown)
case "$MODEL" in *"Pi 5"*) ;; *) warn "this is '$MODEL', not a Pi 5. Continuing, but the power fix is Pi 5-specific." ;; esac
U="${SUDO_USER:-}"
[ -n "$U" ] && [ "$U" != root ] || die "run via sudo from your normal user (so groups land on the right account)"
HOME_U=$(getent passwd "$U" | cut -d: -f6)
[ -d "$HOME_U" ] || die "no home directory for $U"
AP="${GATBOX_AP_PSK:-}"
if [ -n "$AP" ] && [ "$AP" != off ]; then
    [ "${#AP}" -ge 8 ] && [ "${#AP}" -le 63 ] || die "GATBOX_AP_PSK must be 8-63 characters (or 'off')"
fi
export DEBIAN_FRONTEND=noninteractive
REBOOT=0

printf '%sGDD-GAT/01 bootstrap%s  %s  %s  user=%s  checkout=%s (%s)\n' "$M" "$N" "$MODEL" \
    "$(. /etc/os-release; echo "$PRETTY_NAME")" "$U" "$REPO" \
    "$(git -c safe.directory="$REPO" -C "$REPO" describe --always --dirty 2>/dev/null || echo ?)"

# 1 ---------------------------------------------------------------------------
step "1/9 packages"
missing=()
for p in "${PKGS[@]}"; do pkg_ok "$p" || missing+=("$p"); done
if ((${#missing[@]})); then
    apt-get update -q || warn "apt-get update failed (offline?): using the package lists already on the Pi"
    apt-get install -y -q "${missing[@]}" || die "couldn't install: ${missing[*]} (offline?)"
    log "installed: ${missing[*]}"
else
    log "all present (apt skipped): ${PKGS[*]}"
fi
log "sigrok-cli $(sigrok-cli --version 2>/dev/null | head -n 1 | awk '{print $2}'), mame $(dpkg-query -W -f='${Version}' mame 2>/dev/null)"
drivers=$(sigrok-cli -L 2>/dev/null || true)
if grep -q 'uni-t-ut61e-ser' <<<"$drivers"; then log "driver uni-t-ut61e-ser present"
else warn "uni-t-ut61e-ser not listed by sigrok-cli -L; check before trusting the logger"; fi

# 2 ---------------------------------------------------------------------------
step "2/9 serial-port squatters"
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
step "3/9 groups, I2C, SPI, hostname"
need=()
for g in dialout plugdev gpio i2c spi video; do
    getent group "$g" >/dev/null || continue
    id -nG "$U" | tr ' ' '\n' | grep -qx "$g" || need+=("$g")
done
if ((${#need[@]})); then usermod -aG "$(IFS=,; echo "${need[*]}")" "$U"; log "$U += ${need[*]} (log out/in to apply)"
else log "$U already in dialout plugdev gpio i2c spi video"; fi
if command -v raspi-config >/dev/null; then
    for bus in i2c spi; do
        if [ "$(raspi-config nonint "get_$bus")" = 0 ]; then log "${bus^^} already on"
        else raspi-config nonint "do_$bus" 0 && log "${bus^^} enabled"; fi
    done
fi
case "$(hostname)" in
    raspberrypi|catbox)
        old=$(hostname)
        raspi-config nonint do_hostname gatbox
        log "hostname $old -> gatbox"; REBOOT=1 ;;
    *)  log "hostname: $(hostname) (left as is)" ;;
esac

# 4 ---------------------------------------------------------------------------
step "4/9 power: no-PD supply fix; RTC charging guard"
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

# 5 ---------------------------------------------------------------------------
step "5/9 files from the checkout"
while read -r src dst mode opt; do put_file "$src" "$dst" "$mode" "$opt"; done < <(manifest)
if ((${#CHANGED[@]})); then printf '   written: %s\n' "${CHANGED[@]}"; else log "all $(manifest | wc -l) files already match the checkout"; fi
if changed '/etc/udev/rules.d/*'; then
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=tty
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=a466      # the T48, if it's plugged in
    log "udev rules reloaded"
fi
[ -e /dev/gatbox-dmm ] && log "DMM adapter: /dev/gatbox-dmm -> $(readlink -f /dev/gatbox-dmm)" \
                       || log "DMM adapter not plugged in (fine)"
if [ ! -d /var/log/journal ] || changed '/etc/systemd/journald.conf.d/*'; then
    mkdir -p /var/log/journal
    systemctl restart systemd-journald
    log "persistent journal on"
fi
changed '/etc/systemd/system/*' && systemctl daemon-reload && log "systemd units reloaded"

# 6 ---------------------------------------------------------------------------
step "6/9 services"
svc() {   # <unit> <installed path glob>...: enable it; restart only if its files changed; start it if it's down
    local unit=$1; shift
    systemctl enable "$unit" >/dev/null 2>&1
    if changed "$@"; then systemctl restart "$unit"; log "$unit restarted (updated)"
    elif ! systemctl -q is-active "$unit"; then systemctl start "$unit"; log "$unit started"
    else log "$unit up to date, left running"; fi
}
# the dump job's user, the archive and the spool (before gatbox-web, which joins the gatbox-dump group)
if changed /etc/sysusers.d/gatbox.conf || ! getent passwd gatbox-dump >/dev/null || ! getent group gatbox-manuals >/dev/null \
   || ! getent passwd gatbox-sync >/dev/null || ! getent group gatbox-wifi >/dev/null; then
    systemd-sysusers /etc/sysusers.d/gatbox.conf && log "users gatbox-dump (T48 dumps, group plugdev) and gatbox-sync (hub push), group gatbox-manuals"
fi
if changed /etc/tmpfiles.d/gatbox.conf || [ ! -d /srv/gatbox/roms ] || [ ! -d /var/spool/gatbox-dump ] || [ ! -d /srv/gatbox/manuals ] || [ ! -d /etc/gatbox ] || [ ! -d /var/spool/gatbox-wifi ]; then
    systemd-tmpfiles --create /etc/tmpfiles.d/gatbox.conf && log "/srv/gatbox/roms + /srv/gatbox/manuals + /var/spool/gatbox-dump + /etc/gatbox"
fi
if ! id -nG "$U" | grep -qw gatbox-dump; then
    usermod -aG gatbox-dump "$U" && log "$U += gatbox-dump (gatbox-dump from the terminal archives too; log out/in)"
fi
if ! id -nG "$U" | grep -qw gatbox-manuals; then
    usermod -aG gatbox-manuals "$U" && log "$U += gatbox-manuals (gatbox-manuals fetch, PDFs copied in; log out/in)"
fi
svc gatbox-raillog.service /usr/local/bin/gatbox-raillog /etc/systemd/system/gatbox-raillog.service
svc gatbox-web.service /usr/local/bin/gatbox-web /etc/systemd/system/gatbox-web.service \
    '/usr/local/lib/gatbox/gatboxweb/*' '/usr/local/lib/gatbox/gatboxlib/*'   # its package and the shared lib
svc gatbox-scand.service /usr/local/bin/gatbox-scand /etc/systemd/system/gatbox-scand.service   # after gatbox-web
svc gatbox-dump.path /etc/systemd/system/gatbox-dump.path /etc/systemd/system/gatbox-dump.service   # dashboard dumps
# Wi-Fi requests from the dashboard and from a scanned code; the helper is the only thing
# here that can change the network.
svc gatbox-wifi.path /usr/local/sbin/gatbox-wifi '/etc/systemd/system/gatbox-wifi.*'
fetch_font() {   # <path in google/fonts> <local name> <sha256>
    local f="$FONTDIR/$2"
    echo "$3  $f" | sha256sum -c --status 2>/dev/null && return 0
    curl -fsSL --retry 2 -o "$f.part" "https://raw.githubusercontent.com/google/fonts/$FONTS_REV/$1" \
        && echo "$3  $f.part" | sha256sum -c --status && mv "$f.part" "$f" && chmod 644 "$f" && return 0
    rm -f "$f.part"
    return 1
}
install -d -m 755 "$FONTDIR"
fonts_ok=1
while read -r path name sum; do fetch_font "$path" "$name" "$sum" || fonts_ok=0; done < <(grep -v '^$' <<<"$FONTS")
if [ "$fonts_ok" = 1 ]; then log "web fonts in $FONTDIR"
else warn "couldn't fetch the web fonts (no internet?). The page falls back to system fonts; re-run later to add them."; fi
log "web view: http://gatbox.local/ (http://10.42.0.1/ on the GATBOX hotspot)"
svc gatbox-rtc-sync.timer '/usr/local/sbin/gatbox-rtc-sync' '/etc/systemd/system/gatbox-rtc-sync.*'
# The hub push. Its .service carries ConditionPathExists=/etc/gatbox/hub.conf, so on a Pi
# with no token the timer runs and the job condition-skips rather than failing every 2 min.
svc gatbox-sync.timer '/usr/local/bin/gatbox-sync' '/etc/systemd/system/gatbox-sync.*'
if changed '/usr/local/sbin/gatbox-rtc-sync' '/etc/systemd/system/gatbox-rtc-sync.*' || [ ! -e /var/lib/gatbox/rtc-synced ]; then
    systemctl start gatbox-rtc-sync.service || warn "gatbox-rtc-sync failed: journalctl -u gatbox-rtc-sync"
fi
log "RTC stamp: $(cat /var/lib/gatbox/rtc-synced 2>/dev/null || echo 'none yet (not NTP-synced?)');" \
    "charging_voltage $(cat /sys/class/rtc/rtc0/charging_voltage 2>/dev/null || echo n/a) (0 = off)"

# 7 ---------------------------------------------------------------------------
step "7/9 desktop: 7\" kiosk + touch (either HDMI port)"
while read -r src rel mode; do put_user_file "$src" "$rel" "$mode"; done < <(user_manifest)
changed "~$U/.config/autostart/*" && log "autostart: gatbox-kiosk on; Pi OS autotouch off (for $U)"
RC="$HOME_U/.config/labwc/rc.xml"
if [ -f "$RC" ] && grep -qE "$TOUCH_LINE" "$RC"; then
    cp -p "$RC" "$RC.pre-gatbox-touch"
    sed -i -E "/$TOUCH_LINE/d" "$RC"
    chown "$U:$(id -gn "$U")" "$RC"
    CHANGED+=("~$U/.config/labwc/rc.xml")
    log "rc.xml: autotouch's port-pinned Waveshare touch line removed (backup: $RC.pre-gatbox-touch)"
    pkill -HUP -x -u "$U" labwc && log "labwc config reloaded"
fi
log "kiosk: $(cat "$HOME_U/.config/gatbox/kiosk" 2>/dev/null || echo on) at login  (as $U: gatbox-kiosk on|off|start|status)"

# 8 ---------------------------------------------------------------------------
step "8/9 minipro $MINIPRO_TAG (XGecu T48)"
if [ "$(minipro_version)" = "$MINIPRO_TAG" ]; then
    log "minipro $MINIPRO_TAG already installed"
else
    rm -rf "${MINIPRO_SRC:?}"
    if git clone -q -c advice.detachedHead=false --depth 1 --branch "$MINIPRO_TAG" "$MINIPRO_URL" "$MINIPRO_SRC"; then
        if make -C "$MINIPRO_SRC" -j4 >"$MINIPRO_SRC/build.log" 2>&1 \
           && make -C "$MINIPRO_SRC" install PREFIX=/usr/local >>"$MINIPRO_SRC/build.log" 2>&1; then
            log "minipro $(minipro_version) built and installed (/usr/local/bin/minipro)"
        else
            tail -n 5 "$MINIPRO_SRC/build.log" | sed 's/^/     /'
            warn "minipro build failed: see $MINIPRO_SRC/build.log. Everything else is installed."
        fi
    else
        warn "couldn't fetch minipro $MINIPRO_TAG (offline?): no T48 support until a re-run with internet"
    fi
fi
grep -q 'T48' <<<"$(minipro_info)" && log "T48 is in minipro's supported programmers (udev: plugdev group)"
# the dashboard's part names: minipro's own list for the T48, regenerated whenever minipro changes
if command -v minipro >/dev/null && ! parts_ok; then
    tmp=$(mktemp)
    { echo "# minipro $(minipro_version) T48"; minipro -q T48 -l 2>/dev/null; } > "$tmp"
    if [ "$(wc -l < "$tmp")" -gt 1000 ]; then
        install -D -m 644 "$tmp" "$PARTS_LIST" && log "T48 part list: $(($(wc -l < "$PARTS_LIST") - 1)) names ($PARTS_LIST)"
    else
        warn "minipro -q T48 -l gave $(wc -l < "$tmp") lines: T48 part list not updated"
    fi
    rm -f "$tmp"
fi
# each machine's ROM chips from MAME: the installed tool and list (just copied), MAME's own driver list. HOME in a
# throwaway dir so MAME, run as root here, leaves nothing behind.
if mame_roms_wanted && ! mame_roms_ok; then
    log "building the MAME ROM checklist from mame -listxml (a minute or two)"
    mh=$(mktemp -d)
    HOME=$mh /usr/local/bin/gatbox-mame-roms --build "$MAME_ROMS" 2>&1 | sed 's/^/     /' || true
    rm -rf "$mh"
    mame_roms_ok || warn "the MAME ROM checklist didn't build: the dashboard shows machines without it"
fi

# 9 ---------------------------------------------------------------------------
step "9/9 optional: fallback hotspot"
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
    # The service used to carry its own [Install] and run once at boot. Disable that first,
    # or an upgraded Pi keeps a dangling boot-time symlink alongside the timer.
    systemctl disable gatbox-ap-fallback.service >/dev/null 2>&1 || true
    systemctl enable gatbox-ap-fallback.timer >/dev/null
    log "hotspot 'GATBOX' armed: comes up ~90 s after boot if no known Wi-Fi/Ethernet, and
        drops again when a saved network comes back into range"
elif systemctl -q is-enabled gatbox-ap-fallback.service 2>/dev/null; then
    log "hotspot fallback already armed (left as is; GATBOX_AP_PSK=off removes it)"
else
    log "skipped (set GATBOX_AP_PSK to enable)"
fi

# ---------------------------------------------------------------------------
printf '\n%sdone.%s  %d file(s) changed this run\n' "$C" "$N" "${#CHANGED[@]}"
log "known Wi-Fi:  $(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print $1}' | paste -sd, -)"
log "timezone:     $(timedatectl show -p Timezone --value)"
if [ "$REBOOT" = 1 ]; then
    printf '\n   %sREBOOT NOW:%s  sudo reboot   (EEPROM/config.txt/hostname changes need it; new groups need a fresh login)\n\n' "$Y" "$N"
fi
