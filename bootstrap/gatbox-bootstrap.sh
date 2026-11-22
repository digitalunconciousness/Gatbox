#!/usr/bin/env bash
# gatbox-bootstrap.sh — GDD-GAT/01 · Greybard Diagnostics and Design
#
# Fresh Raspberry Pi OS Trixie (64-bit, Desktop) on a Pi 5  ->  GATBOX, installed straight from this git checkout:
#
#   git clone https://github.com/digitalunconciousness/Gatbox.git ~/gatbox && cd ~/gatbox
#   sudo bash bootstrap/gatbox-bootstrap.sh                                   # everything
#   sudo GATBOX_AP_PSK='at-least-8-chars' bash bootstrap/gatbox-bootstrap.sh  # + fallback "GATBOX" hotspot ('off' removes it)
#   bash bootstrap/gatbox-bootstrap.sh --check                                # what a run would change (no root, changes nothing)
#   bash bootstrap/gatbox-bootstrap.sh --extract DIR                          # install the files under DIR instead of /
#   bash bootstrap/gatbox-bootstrap.sh --print-store-unit UUID                # the image store's mount unit for that UUID (no root) (review)
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
#      deps, mame (for `mame -romident` only), hdparm + smartmontools (disk imaging: read-only queries for a drive's own
#      model, serial, geometry and size through the dock; smartd is switched off and hdparm's udev rule masked, see step 1),
#      sleuthkit + python3-pil (the drive browser: reading the files inside a stored image, never mounting it), and catdoc (its
#      text search reads old Word, Excel and PowerPoint files with it). Step 6 adds its
#      user gatbox-browse and its socket-activated service (gatbox-browse.socket), which sees the image store read-only.
#      apt is skipped entirely when they're all present.
#   2  removes ModemManager (hijacks ttyUSB0) and brltty (grabs USB-serial adapters)
#   3  groups (dialout, plugdev, gpio, i2c, spi, video) for your user; I2C + SPI on; hostname catbox/raspberrypi -> gatbox
#   4  power fix: PSU_MAX_CURRENT=5000 (EEPROM) + usb_max_current_enable=1 (config.txt); RTC charging guard
#   5  files from this checkout (table below): logger, web view, RTC sync, hotspot fallback, tools, udev, journald,
#      menu launcher
#   6  services: the gatbox-dump user + /srv/gatbox/roms + its spool, the gatbox-manuals group + /srv/gatbox/manuals
#      (sysusers.d, tmpfiles.d), gatbox-raillog,
#      gatbox-web (+ its fonts), gatbox-scand (the barcode scanner), gatbox-dump.path (dashboard dumps), rtc-sync.timer,
#      the disk job: the gatbox-disk user, the gatbox-disk-queue group, /var/spool/gatbox-disk, gatbox-disk.path, and the
#      image store's mount unit (srv-gatbox-disks.mount, written only when /etc/gatbox/disks.conf holds a UUID),
#      the manuals job: the gatbox-fetch user, /var/spool/gatbox-manuals, gatbox-manuals.path (the LIBRARY's FIND A MANUAL)
#      the chip-list job: the gatbox-mame group, /var/spool/gatbox-mame, gatbox-mame-roms.path (ROM lists for games added later)
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
backend/gatbox-disk.service                     /etc/systemd/system/gatbox-disk.service               644
backend/gatbox-browse.socket                    /etc/systemd/system/gatbox-browse.socket              644
backend/gatbox-browse.service                   /etc/systemd/system/gatbox-browse.service             644
backend/gatbox-disk.path                        /etc/systemd/system/gatbox-disk.path                  644
backend/gatbox-disk-send.service                /etc/systemd/system/gatbox-disk-send.service          644
backend/gatbox-disk-send.timer                  /etc/systemd/system/gatbox-disk-send.timer            644
backend/gatbox-manuals.service                  /etc/systemd/system/gatbox-manuals.service            644
backend/gatbox-manuals.path                     /etc/systemd/system/gatbox-manuals.path               644
backend/gatbox-mame-roms.service                /etc/systemd/system/gatbox-mame-roms.service          644
backend/gatbox-mame-roms.path                   /etc/systemd/system/gatbox-mame-roms.path             644
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
tools/gatbox-disk                               /usr/local/bin/gatbox-disk                            755
tools/gatbox-disk-send                          /usr/local/bin/gatbox-disk-send                       755
backend/gatbox-browse                           /usr/local/bin/gatbox-browse                          755
tools/gatbox-mame-roms                          /usr/local/bin/gatbox-mame-roms                       755
tools/gatbox-library                            /usr/local/bin/gatbox-library                         755
tools/gatbox-manuals                            /usr/local/bin/gatbox-manuals                         755
backend/gatboxlib/__init__.py                   /usr/local/lib/gatbox/gatboxlib/__init__.py           644
backend/gatboxlib/modes.py                      /usr/local/lib/gatbox/gatboxlib/modes.py              644
backend/gatboxlib/profiles.py                   /usr/local/lib/gatbox/gatboxlib/profiles.py           644
backend/gatboxlib/library.py                    /usr/local/lib/gatbox/gatboxlib/library.py            644
backend/gatboxlib/libview.py                    /usr/local/lib/gatbox/gatboxlib/libview.py            644
backend/gatboxlib/seed.py                       /usr/local/lib/gatbox/gatboxlib/seed.py               644
backend/gatboxlib/filestore.py                  /usr/local/lib/gatbox/gatboxlib/filestore.py          644
backend/gatboxlib/sparse.py                     /usr/local/lib/gatbox/gatboxlib/sparse.py             644
backend/gatboxlib/libsync.py                    /usr/local/lib/gatbox/gatboxlib/libsync.py            644
backend/gatboxlib/csvlog.py                     /usr/local/lib/gatbox/gatboxlib/csvlog.py             644
backend/gatboxlib/disks.py                      /usr/local/lib/gatbox/gatboxlib/disks.py              644
backend/gatboxlib/browse.py                     /usr/local/lib/gatbox/gatboxlib/browse.py             644
backend/gatboxlib/browseindex.py                /usr/local/lib/gatbox/gatboxlib/browseindex.py        644
backend/gatboxlib/browsetext.py                 /usr/local/lib/gatbox/gatboxlib/browsetext.py         644
backend/gatboxlib/browsecopy.py                 /usr/local/lib/gatbox/gatboxlib/browsecopy.py         644
backend/gatboxlib/rawpreview.py                 /usr/local/lib/gatbox/gatboxlib/rawpreview.py         644
backend/gatboxlib/browsehash.py                 /usr/local/lib/gatbox/gatboxlib/browsehash.py         644
backend/gatboxlib/chd.py                        /usr/local/lib/gatbox/gatboxlib/chd.py                644
backend/gatboxlib/railread.py                   /usr/local/lib/gatbox/gatboxlib/railread.py           644
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
backend/gatboxweb/romsync.py                    /usr/local/lib/gatbox/gatboxweb/romsync.py            644
backend/gatboxweb/system.py                     /usr/local/lib/gatbox/gatboxweb/system.py             644
backend/gatboxweb/devices.py                    /usr/local/lib/gatbox/gatboxweb/devices.py            644
backend/gatboxweb/dump.py                       /usr/local/lib/gatbox/gatboxweb/dump.py               644
backend/gatboxweb/disk.py                       /usr/local/lib/gatbox/gatboxweb/disk.py               644
backend/gatboxweb/browse.py                     /usr/local/lib/gatbox/gatboxweb/browse.py             644
backend/gatboxweb/mame.py                       /usr/local/lib/gatbox/gatboxweb/mame.py               644
backend/gatboxweb/libsource.py                  /usr/local/lib/gatbox/gatboxweb/libsource.py          644
backend/gatboxweb/libsync_api.py                /usr/local/lib/gatbox/gatboxweb/libsync_api.py        644
backend/gatboxweb/libconflicts.py                /usr/local/lib/gatbox/gatboxweb/libconflicts.py        644
backend/gatboxweb/mame_request.py                /usr/local/lib/gatbox/gatboxweb/mame_request.py        644
backend/gatboxweb/games.py                      /usr/local/lib/gatbox/gatboxweb/games.py              644
backend/gatboxweb/rom_check.py                  /usr/local/lib/gatbox/gatboxweb/rom_check.py          644
backend/gatboxweb/titles.py                     /usr/local/lib/gatbox/gatboxweb/titles.py             644
backend/gatboxweb/manual_request.py             /usr/local/lib/gatbox/gatboxweb/manual_request.py     644
backend/gatboxweb/manuals.py                    /usr/local/lib/gatbox/gatboxweb/manuals.py            644
backend/gatboxweb/wifi.py                       /usr/local/lib/gatbox/gatboxweb/wifi.py               644
backend/gatboxweb/orders.py                     /usr/local/lib/gatbox/gatboxweb/orders.py             644
backend/gatboxweb/hubs.py                       /usr/local/lib/gatbox/gatboxweb/hubs.py               644
backend/gatboxweb/hubgames.py                   /usr/local/lib/gatbox/gatboxweb/hubgames.py           644
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
bootstrap/files/85-hdparm-off.rules             /etc/udev/rules.d/85-hdparm.rules                     644
bootstrap/files/98-gatbox-disk.rules            /etc/udev/rules.d/98-gatbox-disk.rules                644
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
      tesseract-ocr                                                 # READ LIMITS: OCR for a scanned manual page with no text (the manuals job runs it)
      hdparm smartmontools                                          # disk imaging: hdparm -I/-N and smartctl -i/-H, run on demand
      gddrescue                                                     # ddrescue: the disk job's READ
      mame-tools                                                    # chdman: the disk job's MAME CHD check (info and extracthd only; Task 3)
      dosfstools exfatprogs e2fsprogs                               # the disk job's FORMAT: mkfs and fsck for FAT, exFAT and ext4, run on a scratch file in the store, never on a drive
      catdoc                                                        # the drive browser's text search (F3): catdoc, xls2csv, catppt read old Word, Excel and PowerPoint files (poppler-utils reads PDFs)
      sleuthkit python3-pil                                         # the drive browser (docs/plans/2026-11-15-drive-browser.md): fls, fsstat, mmls, icat read a stored image, nothing is mounted; Pillow makes thumbnails
      uhubctl                                                       # POWER CYCLE T48: the MEGA4's port power, the T48's port only (docs/plans/2026-11-22-mega4-t48-power-cycle.md)
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
# MAME's own title list for the LIBRARY's title search: every game's description, year and maker, once per MAME version
MAME_TITLES=/usr/local/share/gatbox/mame-titles.json
mame_titles_wanted() { [ -x /usr/games/mame ]; }
mame_titles_ok() { python3 "$REPO/tools/gatbox-mame-roms" --titles-check "$MAME_TITLES"; }
# What this run installed, for the SYSTEM tab: the release tag (or the commit), the commit, the
# branch, and whether the checkout had local changes. No time in it, so --check stays quiet
# when nothing changed. git runs as root over the owner's checkout: safe.directory for that one.
INSTALLED=/usr/local/share/gatbox/installed.json
installed_json() {
    local g=(git -c safe.directory="$REPO" -C "$REPO")
    python3 - "$("${g[@]}" describe --tags --always --dirty 2>/dev/null)" "$("${g[@]}" rev-parse HEAD 2>/dev/null)" \
        "$("${g[@]}" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$("${g[@]}" status --porcelain --untracked-files=no 2>/dev/null)" <<'PY'
import json, sys
describe, commit, branch, dirty = (sys.argv[1:] + [""] * 4)[:4]
print(json.dumps({"describe": describe or None, "commit": commit or None,
                  "branch": branch if branch not in ("", "HEAD") else None,   # a checked-out tag has none
                  "local_changes": bool(dirty)}, sort_keys=True))
PY
}
installed_ok() { [ "$(cat "$INSTALLED" 2>/dev/null)" = "$(installed_json)" ]; }
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
# The image store (docs/plans/2026-11-02-disk-imaging.md §5): the 4 TB drive, mounted by its filesystem UUID at
# /srv/gatbox/disks through a mount unit that is written only when /etc/gatbox/disks.conf holds a `UUID=...` line.
DISKS_CONF=/etc/gatbox/disks.conf
STORE_MOUNT=/etc/systemd/system/srv-gatbox-disks.mount
valid_uuid() { [[ ${1:-} =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; }
store_uuid() {   # the UUID in disks.conf, lower-cased: empty when the file has none, is empty, or cannot be read
    local v
    v=$(awk -F= '/^[[:space:]]*UUID[[:space:]]*=/ { gsub(/[[:space:]\r]/, "", $2); print tolower($2); exit }' "$DISKS_CONF" 2>/dev/null)
    valid_uuid "$v" && printf '%s\n' "$v"
}
store_mount_unit() {   # <lower-case uuid>: the mount unit text
    local u=$1 esc=${1//-/\\x2d}
    cat <<EOF
# GATBOX image store. Written by gatbox-bootstrap.sh from /etc/gatbox/disks.conf: edit that, not this.
[Unit]
Description=GATBOX image store (the drive with filesystem UUID $u)

[Mount]
What=/dev/disk/by-uuid/$u
Where=/srv/gatbox/disks
Type=ext4
# Images are data: never programs, never device nodes.
Options=noatime,nodev,nosuid,noexec

[Install]
# Mounted when the drive appears, at boot or plugged in later. Nothing waits for it and nothing requires it, so an
# unplugged drive is not an error: the DISK tab says IMAGE STORE NOT CONNECTED (note §9.3, item 5).
WantedBy=dev-disk-by\\x2duuid-${esc}.device
EOF
}
# gatbox-disk-send may reach one address: the archive's private listener, ARCHIVE_LAN_URL in archive.conf (docs/plans/
# 2026-11-17-disk-image-drain.md). Its unit denies everything; this drop-in, written from that line, allows that address only.
ARCHIVE_CONF=/etc/gatbox/archive.conf
SEND_DROPIN=/etc/systemd/system/gatbox-disk-send.service.d/home.conf
lan_host() {   # the address in ARCHIVE_LAN_URL=http://<address>:<port>, when it is a private IP address; else nothing
    local h
    h=$(awk -F= '/^[[:space:]]*ARCHIVE_LAN_URL[[:space:]]*=/ { sub(/^[^=]*=[[:space:]]*/, ""); gsub(/[[:space:]\r]/, ""); print; exit }' "$ARCHIVE_CONF" 2>/dev/null)
    h=${h#http://}; h=${h%%/*}
    if [[ $h == \[* ]]; then h=${h#[}; h=${h%%]*}; else h=${h%%:*}; fi
    # Python's own idea of "private" (RFC 1918, ULA, link-local), not a list typed in here; never loopback.
    python3 -c 'import ipaddress, sys
a = ipaddress.ip_address(sys.argv[1])
sys.exit(0 if a.is_private and not a.is_loopback else 1)' "$h" 2>/dev/null && printf '%s\n' "$h"
}
send_dropin() {   # <address>: the drop-in text
    printf '# Written by gatbox-bootstrap.sh from ARCHIVE_LAN_URL in /etc/gatbox/archive.conf: edit that, not this.\n[Service]\nIPAddressAllow=%s\n' "$1"
}
# The disk rule (98-gatbox-disk.rules) puts every whole disk behind USB in the group gatbox-disk. udev works out a rule's
# GROUP= when it loads the rule, and a daemon that loaded this one before the group existed kept leaving drives root:disk
# after it was told to reload (the Pi, 2026-11-14: only a restart fixed it; why a reload was not enough was not established).
# This lists the USB disks that are not in the group yet, so a re-run mends an install that was cut short, and --check can
# say so. Nothing is listed while the rule or the group is missing: there is nothing to compare with.
DISK_RULE=/etc/udev/rules.d/98-gatbox-disk.rules
SYS_BLOCK=/sys/block
DEV_DIR=/dev
unfixed_disk_nodes() {
    local gid n real
    [ -f "$DISK_RULE" ] || return 0
    gid=$(getent group gatbox-disk 2>/dev/null | cut -d: -f3)
    [ -n "$gid" ] || return 0
    for n in "$SYS_BLOCK"/sd*; do
        [ -e "$n" ] && [ -e "$DEV_DIR/${n##*/}" ] || continue
        real=$(readlink -f "$n")
        [[ $real == */usb[0-9]*/* ]] || continue               # the rule's SUBSYSTEMS=="usb": not behind USB, not ours
        [ "$(stat -c %g "$DEV_DIR/${n##*/}" 2>/dev/null)" = "$gid" ] || printf '%s\n' "${n##*/}"
    done
}
# minipro --version exits 1 when no programmer is plugged in, but still prints its version and supported list
minipro_info() { minipro --version 2>&1 || true; }
minipro_version() { minipro_info | sed -n 's/^minipro version \([^ ]*\).*/\1/p' | head -n 1; }
font_ok() { echo "$2  $FONTDIR/$1" | sha256sum -c --status 2>/dev/null; }

[ -f "$REPO/backend/gatbox-raillog" ] || die "run this from a GATBOX checkout (no backend/gatbox-raillog under $REPO)"

if [ "${1:-}" = "--print-store-unit" ]; then   # for a test and for review: no root, nothing changes
    uuid=$(tr 'A-F' 'a-f' <<<"${2:-}")
    valid_uuid "$uuid" || { echo "usage: $0 --print-store-unit <filesystem UUID, 36 characters>" >&2; exit 2; }
    store_mount_unit "$uuid"
    exit 0
fi
if [ "${1:-}" = "--extract" ]; then
    [ -n "${2:-}" ] || { echo "usage: $0 --extract DIR" >&2; exit 2; }
    ROOT=${2%/}; U=$(id -un); HOME_U=/home/$U
    while read -r src dst mode opt; do put_file "$src" "$dst" "$mode" "$opt"; done < <(manifest)
    while read -r src rel mode; do put_user_file "$src" "$rel" "$mode"; done < <(user_manifest)
    echo "${#CHANGED[@]} file(s) written under $ROOT"
    exit 0
fi

if [ "${1:-}" = "--check" ]; then
    # The desktop user's account, as the real run takes it: the sudo user, not root. Under sudo, $HOME and id -un are root's, and
    # --check listed root's autostart files and group memberships (the Pi, 2026-11-17).
    CU=${SUDO_USER:-$(id -un)}
    CHOME=$(getent passwd "$CU" | cut -d: -f6); CHOME=${CHOME:-$HOME}
    DISKS_CONF=${GATBOX_DISKS_CONF:-$DISKS_CONF}; STORE_MOUNT=${GATBOX_STORE_MOUNT:-$STORE_MOUNT}   # a test points these elsewhere; --check only reads
    ARCHIVE_CONF=${GATBOX_ARCHIVE_CONF:-$ARCHIVE_CONF}; SEND_DROPIN=${GATBOX_SEND_DROPIN:-$SEND_DROPIN}
    DISK_RULE=${GATBOX_DISK_RULE:-$DISK_RULE}; SYS_BLOCK=${GATBOX_SYS_BLOCK:-$SYS_BLOCK}; DEV_DIR=${GATBOX_DEV:-$DEV_DIR}
    ETC_GATBOX=${GATBOX_ETC:-/etc/gatbox}
    NOTES=()   # what this check could not look at, said after the list: not a change
    while read -r src dst mode opt; do check_file "$src" "$dst" "$mode" "$opt"; done < <(manifest)
    while read -r src rel mode; do check_file "$src" "$CHOME/$rel" "$mode"; done < <(user_manifest)
    grep -qE "$TOUCH_LINE" "$CHOME/.config/labwc/rc.xml" 2>/dev/null \
        && DIFFS+=("edit       ~/.config/labwc/rc.xml: remove autotouch's port-pinned Waveshare touch line")
    for p in "${PKGS[@]}"; do pkg_ok "$p" || DIFFS+=("install    package $p"); done
    [ "$(minipro_version)" = "$MINIPRO_TAG" ] || DIFFS+=("build      minipro $MINIPRO_TAG (installed: $(minipro_version))")
    while read -r _ name sum; do font_ok "$name" "$sum" || DIFFS+=("fetch      font $name"); done < <(grep -v '^$' <<<"$FONTS")
    for l in usb_max_current_enable=1; do grep -qxF "$l" /boot/firmware/config.txt || DIFFS+=("config.txt += $l"); done
    grep -qE '^[^#]*rtc_bbat_vchg' /boot/firmware/config.txt && [ -z "$RTC_CHARGE" ] && DIFFS+=("config.txt: comment out rtc_bbat_vchg (RTC CHARGING IS ON)")
    for u in gatbox-raillog.service gatbox-web.service gatbox-scand.service gatbox-dump.path gatbox-rtc-sync.timer \
             gatbox-sync.timer gatbox-disk.path gatbox-manuals.path gatbox-mame-roms.path gatbox-browse.socket gatbox-disk-send.timer; do
        systemctl -q is-enabled "$u" 2>/dev/null || DIFFS+=("enable     $u"); done
    systemctl -q is-enabled smartmontools.service 2>/dev/null \
        && DIFFS+=("disable    smartd (smartmontools.service): the disk jobs call smartctl on demand, nothing polls the drives")
    { systemctl -q is-enabled rpcbind.socket 2>/dev/null || systemctl -q is-active rpcbind.service 2>/dev/null; } \
        && DIFFS+=("mask       rpcbind (.socket, .service): port 111 open to the LAN and the hotspot, and nothing here uses NFS")
    getent passwd gatbox-dump >/dev/null || DIFFS+=("create     user gatbox-dump (sysusers.d)")
    getent passwd gatbox-disk >/dev/null || DIFFS+=("create     user gatbox-disk (sysusers.d)")
    getent passwd gatbox-browse >/dev/null || DIFFS+=("create     user gatbox-browse (sysusers.d)")
    getent passwd gatbox-disk-send >/dev/null || DIFFS+=("create     user gatbox-disk-send (sysusers.d: disk images to the archive, home network only)")
    getent passwd gatbox-fetch >/dev/null || DIFFS+=("create     user gatbox-fetch (sysusers.d: the manuals job, in group gatbox-manuals)")
    getent group gatbox-disk-queue >/dev/null || DIFFS+=("create     group gatbox-disk-queue (sysusers.d)")
    [ -d /var/spool/gatbox-disk ] || DIFFS+=("create     /var/spool/gatbox-disk (tmpfiles.d)")
    [ -d /var/spool/gatbox-manuals ] || DIFFS+=("create     /var/spool/gatbox-manuals (tmpfiles.d)")
    getent group gatbox-mame >/dev/null || DIFFS+=("create     group gatbox-mame (sysusers.d: the chip-list job's spool)")
    [ -d /var/spool/gatbox-mame ] || DIFFS+=("create     /var/spool/gatbox-mame (tmpfiles.d)")
    [ -d /srv/gatbox/disks ] || DIFFS+=("create     /srv/gatbox/disks, the image store's bare mountpoint (tmpfiles.d)")
    id -nG "$CU" | grep -qw gatbox-disk-queue || DIFFS+=("group      $CU += gatbox-disk-queue (the disk spool)")
    stale=$(unfixed_disk_nodes | xargs)
    [ -z "$stale" ] || DIFFS+=("restart    systemd-udevd and re-apply the disk rule: $stale is not in group gatbox-disk yet")
    # disks.conf is 0600 root: without root there is nothing to compare, so nothing is said.
    if [ -r "$DISKS_CONF" ]; then
        uuid=$(store_uuid)
        [ -z "$uuid" ] || [ "$(store_mount_unit "$uuid")" = "$(cat "$STORE_MOUNT" 2>/dev/null)" ] \
            || DIFFS+=("write      srv-gatbox-disks.mount (the image store, UUID $uuid)")
    fi
    # archive.conf is 0600 root too.
    if [ -r "$ARCHIVE_CONF" ]; then
        lan=$(lan_host || true)
        if [ -n "$lan" ]; then
            [ "$(send_dropin "$lan")" = "$(cat "$SEND_DROPIN" 2>/dev/null)" ] || DIFFS+=("write      gatbox-disk-send.service.d/home.conf (IPAddressAllow=$lan, the archive at home)")
        elif [ -e "$SEND_DROPIN" ]; then
            DIFFS+=("remove     gatbox-disk-send.service.d/home.conf (no private ARCHIVE_LAN_URL in archive.conf)")
        fi
    fi
    getent passwd gatbox-sync >/dev/null || DIFFS+=("create     user gatbox-sync (sysusers.d)")
    getent group gatbox-wifi >/dev/null || DIFFS+=("create     group gatbox-wifi (sysusers.d)")
    [ -d /var/spool/gatbox-wifi ] || DIFFS+=("create     /var/spool/gatbox-wifi (tmpfiles.d)")
    [ -d /srv/gatbox/roms ] && [ -d /var/spool/gatbox-dump ] || DIFFS+=("create     /srv/gatbox/roms + /var/spool/gatbox-dump (tmpfiles.d)")
    [ -d "$ETC_GATBOX" ] || DIFFS+=("create     /etc/gatbox (tmpfiles.d; hub.conf goes in by hand)")
    if [ -d "$ETC_GATBOX" ] && [ ! -x "$ETC_GATBOX" ]; then
        # 0750 root: without root a test for what is inside it fails whether the file is there or not, and would say "create"
        # for files that exist.
        NOTES+=("/etc/gatbox is readable by root only: its hubs.d, hub.conf, archive.conf and disks.conf were not looked at. Run this with sudo to include them.")
    else
        [ -d "$ETC_GATBOX/hubs.d" ] || DIFFS+=("create     /etc/gatbox/hubs.d (tmpfiles.d; one <name>.conf per extra hub, by hand)")
        [ -e "$ETC_GATBOX/hub.conf" ] || DIFFS+=("create     /etc/gatbox/hub.conf, empty (tmpfiles.d; empty means no hub)")
        [ -e "$ETC_GATBOX/archive.conf" ] || DIFFS+=("create     /etc/gatbox/archive.conf, empty (tmpfiles.d; empty means no archive, the library is not synced)")
    fi
    # Only when the hotspot is armed at all: an unarmed Pi is not missing anything.
    if systemctl -q is-enabled gatbox-ap-fallback.service 2>/dev/null \
       || systemctl -q is-enabled gatbox-ap-fallback.timer 2>/dev/null; then
        systemctl -q is-enabled gatbox-ap-fallback.timer 2>/dev/null \
            || DIFFS+=("enable     gatbox-ap-fallback.timer (replaces the boot-only service)")
    fi
    id -nG "$CU" | grep -qw gatbox-dump || DIFFS+=("group      $CU += gatbox-dump (the dump archive)")
    getent group gatbox-manuals >/dev/null || DIFFS+=("create     group gatbox-manuals (sysusers.d)")
    [ -d /srv/gatbox/manuals ] || DIFFS+=("create     /srv/gatbox/manuals (tmpfiles.d)")
    [ -d /srv/gatbox/library ] || DIFFS+=("create     /srv/gatbox/library (tmpfiles.d; the game library's folder)")
    getent group gatbox-manuals >/dev/null && ! getent group gatbox-manuals | cut -d: -f4 | tr ',' '\n' | grep -qx "$CU" \
        && DIFFS+=("group      $CU += gatbox-manuals (the manuals folder)")
    command -v minipro >/dev/null && ! parts_ok && DIFFS+=("generate   $PARTS_LIST")
    mame_roms_wanted && ! mame_roms_ok && DIFFS+=("generate   $MAME_ROMS (from mame -listxml: a minute or two)")
    mame_titles_wanted && ! mame_titles_ok && DIFFS+=("generate   $MAME_TITLES (MAME's title list, once per MAME version: a minute or two)")
    installed_ok || DIFFS+=("record     $INSTALLED ($(git -c safe.directory="$REPO" -C "$REPO" describe --tags --always --dirty 2>/dev/null))")
    if ((${#DIFFS[@]})); then printf 'a run would change:\n'; printf '  %s\n' "${DIFFS[@]}"; ((${#NOTES[@]})) && printf 'note: %s\n' "${NOTES[@]}"; exit 3; fi
    echo "nothing to change: this Pi matches the checkout"
    ((${#NOTES[@]})) && printf 'note: %s\n' "${NOTES[@]}"
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
# smartmontools' postinst starts smartd, which polls every drive it finds. On the Pi's first install (2026-11-14) its SMART
# ENABLE hung the dock's bridge until the kernel reset it, twice, with the 80 GB drive on it. The disable step below came too
# late to prevent that. dpkg's own switch for this is policy-rc.d: while apt runs, one that refuses to start smartmontools
# (exit 101) and allows everything else (deb-systemd-invoke reads it, and calls it with the unit name and the action). It is
# taken away again as soon as apt finishes, and it is skipped when the Pi already has a policy-rc.d of its own.
POLICY=/usr/sbin/policy-rc.d
POLICY_MARK="# GATBOX bootstrap: temporary, removed when apt finishes"
[ -f "$POLICY" ] && grep -qxF "$POLICY_MARK" "$POLICY" && rm -f "$POLICY"      # ours, left behind by a run that was cut short
hold_smartd=0
if printf '%s\n' "${missing[@]:-}" | grep -qx smartmontools && [ ! -e "$POLICY" ]; then
    printf '#!/bin/sh\n%s\ncase "$1" in smartmontools|smartmontools.service|smartd|smartd.service) exit 101;; esac\nexit 0\n' "$POLICY_MARK" > "$POLICY"
    chmod 755 "$POLICY"; hold_smartd=1
fi
if ((${#missing[@]})); then
    apt-get update -q || warn "apt-get update failed (offline?): using the package lists already on the Pi"
    apt-get install -y -q "${missing[@]}"; apt_rc=$?
    [ "$hold_smartd" = 1 ] && rm -f "$POLICY"
    [ "$apt_rc" = 0 ] || die "couldn't install: ${missing[*]} (offline?)"
    log "installed: ${missing[*]}"
else
    log "all present (apt skipped): ${PKGS[*]}"
fi
# smartmontools' own postinst enables smartd, a daemon that polls every disk it finds on a timer. The deck reads a drive
# when a disk job asks (smartctl -i/-H) and nothing else should talk to a drive in the middle of a read, so the tool stays
# and the daemon goes. A unit an admin has disabled stays disabled across the package's upgrades.
if systemctl -q is-enabled smartmontools.service 2>/dev/null || systemctl -q is-active smartmontools.service 2>/dev/null; then
    systemctl disable --now smartmontools.service >/dev/null 2>&1 && log "smartd (smartmontools.service) disabled: smartctl stays, the daemon polling drives does not"
fi
# rpcbind comes with Pi OS's nfs-common and listens on port 111 on every interface, the hotspot included (the Pi's audit,
# 2026-11-17). Nothing on the deck mounts or serves NFS, so it is masked (the owner, 2026-11-17: "turn the bind off if nothing
# uses it"). Masked rather than purged: no package goes, nfs-common can't start it again, and `systemctl unmask rpcbind.socket
# rpcbind.service` brings it back if NFS is ever wanted.
if systemctl -q is-enabled rpcbind.socket 2>/dev/null || systemctl -q is-active rpcbind.service 2>/dev/null; then
    systemctl mask --now rpcbind.socket rpcbind.service >/dev/null 2>&1 \
        && log "rpcbind masked (port 111 closed): nothing here uses NFS; systemctl unmask rpcbind.socket rpcbind.service undoes it"
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
    udevadm trigger --action=change --subsystem-match=block --property-match=DEVTYPE=disk   # drives already on the dock get the disk rule
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
   || ! getent passwd gatbox-sync >/dev/null || ! getent group gatbox-wifi >/dev/null \
   || ! getent passwd gatbox-disk >/dev/null || ! getent group gatbox-disk-queue >/dev/null || ! getent passwd gatbox-browse >/dev/null \
   || ! getent passwd gatbox-fetch >/dev/null || ! getent group gatbox-mame >/dev/null || ! getent passwd gatbox-disk-send >/dev/null; then
    systemd-sysusers /etc/sysusers.d/gatbox.conf && SYSUSERS_RAN=1 && log "users gatbox-dump (T48 dumps, group plugdev), gatbox-sync (hub push), gatbox-disk (disk jobs, its own devices' group) and gatbox-browse (the drive browser) and gatbox-fetch (the manuals job) and gatbox-disk-send (disk images to the archive), groups gatbox-manuals, gatbox-disk-queue and gatbox-mame (the chip-list spool)"
fi
# udev works out GROUP= when it LOADS a rule. Step 5 loaded 98-gatbox-disk.rules before this step could make the gatbox-disk
# group, so on a first install udev ignored GROUP="gatbox-disk" ("Unknown group": udevadm verify on the Pi said so) and left the
# dock's drive root:disk. Telling the running udevd to reload did not mend that on the Pi (2026-11-14): the group only took
# after systemd-udevd was restarted, so it is restarted here (it starts again with the rules, and the group, it finds now), and
# the rule is re-applied to the drives already plugged in. This runs when sysusers has just made the group, and also when a
# USB disk is still outside the group, so a re-run mends an install that was cut short.
if [ -f "$DISK_RULE" ] && { [ "${SYSUSERS_RAN:-0}" = 1 ] || [ -n "$(unfixed_disk_nodes)" ]; }; then
    systemctl restart systemd-udevd
    udevadm trigger --action=change --subsystem-match=block --property-match=DEVTYPE=disk
    udevadm settle --timeout=10
    log "systemd-udevd restarted and the disk rule re-applied: the gatbox-disk group exists now"
    still=$(unfixed_disk_nodes | xargs)
    [ -z "$still" ] || warn "still outside group gatbox-disk after that: $still (unplug it and plug it back in, or reboot)"
fi
if changed /etc/tmpfiles.d/gatbox.conf || [ ! -d /var/spool/gatbox-disk ] || [ ! -d /var/spool/gatbox-manuals ] || [ ! -d /var/spool/gatbox-mame ] || [ ! -d /srv/gatbox/disks ] || [ ! -d /srv/gatbox/roms ] || [ ! -d /var/spool/gatbox-dump ] || [ ! -d /srv/gatbox/manuals ] || [ ! -d /srv/gatbox/library ] || [ ! -d /etc/gatbox ] || [ ! -d /etc/gatbox/hubs.d ] || [ ! -e /etc/gatbox/hub.conf ] || [ ! -e /etc/gatbox/archive.conf ] || [ ! -d /var/spool/gatbox-wifi ]; then
    systemd-tmpfiles --create /etc/tmpfiles.d/gatbox.conf && log "/srv/gatbox/roms + /srv/gatbox/manuals + /srv/gatbox/library + /var/spool/gatbox-dump + /var/spool/gatbox-disk + /var/spool/gatbox-manuals + /srv/gatbox/disks (bare) + /etc/gatbox (+ hubs.d, disks.conf)"
fi
if ! id -nG "$U" | grep -qw gatbox-dump; then
    usermod -aG gatbox-dump "$U" && log "$U += gatbox-dump (gatbox-dump from the terminal archives too; log out/in)"
fi
if ! id -nG "$U" | grep -qw gatbox-disk-queue; then
    usermod -aG gatbox-disk-queue "$U" && log "$U += gatbox-disk-queue (gatbox-disk from the terminal queues through the spool, and reads the store; log out/in)"
fi
if ! id -nG "$U" | grep -qw gatbox-manuals; then
    usermod -aG gatbox-manuals "$U" && log "$U += gatbox-manuals (gatbox-manuals fetch, PDFs copied in; log out/in)"
fi
svc gatbox-raillog.service /usr/local/bin/gatbox-raillog /etc/systemd/system/gatbox-raillog.service
svc gatbox-web.service /usr/local/bin/gatbox-web /etc/systemd/system/gatbox-web.service \
    '/usr/local/lib/gatbox/gatboxweb/*' '/usr/local/lib/gatbox/gatboxlib/*'   # its package and the shared lib
svc gatbox-scand.service /usr/local/bin/gatbox-scand /etc/systemd/system/gatbox-scand.service   # after gatbox-web
svc gatbox-dump.path /etc/systemd/system/gatbox-dump.path /etc/systemd/system/gatbox-dump.service   # dashboard dumps
# Dashboard disk jobs (READ, TARGET CHECK, WRITE). After gatbox-web, which queues them.
svc gatbox-disk.path /usr/local/bin/gatbox-disk '/etc/systemd/system/gatbox-disk.*' '/usr/local/lib/gatbox/gatboxlib/*'
# FIND A MANUAL (the LIBRARY on the 7"): gatbox-web writes a request, this path unit starts the job as gatbox-fetch. After gatbox-web.
svc gatbox-manuals.path /usr/local/bin/gatbox-manuals '/etc/systemd/system/gatbox-manuals.*' '/usr/local/lib/gatbox/gatboxlib/*'
# ROM lists for games added to the library after the seed (phase D4): gatbox-web writes a request, this path unit starts the job
# (a DynamicUser with the spool's group, no network, no devices). After gatbox-web.
svc gatbox-mame-roms.path /usr/local/bin/gatbox-mame-roms '/etc/systemd/system/gatbox-mame-roms.*' '/usr/local/lib/gatbox/gatboxlib/*'
# The drive browser (FILES on the 7"): the socket is enabled and listening, and systemd starts the service when gatbox-web asks.
# Restarting the socket on a change stops the service too (it Requires= it), so the next ask runs the new code.
svc gatbox-browse.socket /usr/local/bin/gatbox-browse '/etc/systemd/system/gatbox-browse.*' '/usr/local/lib/gatbox/gatboxlib/*'
# The image store's mount unit, from /etc/gatbox/disks.conf. Written only when that file holds a UUID: before the drive is
# formatted there is nothing to mount, and the DISK tab says IMAGE STORE NOT CONNECTED. An unplugged drive is not an error.
store_uuid_now=$(store_uuid)
if [ -z "$store_uuid_now" ]; then
    log "image store: no UUID in $DISKS_CONF yet (format the drive with docs/plans/2026-11-02-disk-imaging-store.sh, then re-run this)"
else
    if [ "$(store_mount_unit "$store_uuid_now")" != "$(cat "$STORE_MOUNT" 2>/dev/null)" ]; then
        systemctl disable srv-gatbox-disks.mount >/dev/null 2>&1 || true        # an older UUID's device wants, if there were any
        store_mount_unit "$store_uuid_now" > "$STORE_MOUNT.tmp" && chmod 644 "$STORE_MOUNT.tmp" && mv "$STORE_MOUNT.tmp" "$STORE_MOUNT"
        systemctl daemon-reload
        log "srv-gatbox-disks.mount written for UUID $store_uuid_now"
    fi
    systemctl enable srv-gatbox-disks.mount >/dev/null 2>&1
    if mountpoint -q /srv/gatbox/disks; then log "image store mounted at /srv/gatbox/disks"
    elif [ -e "/dev/disk/by-uuid/$store_uuid_now" ]; then
        systemctl start srv-gatbox-disks.mount && log "image store mounted at /srv/gatbox/disks" || warn "the image store did not mount: systemctl status srv-gatbox-disks.mount"
    else log "image store: the drive with UUID $store_uuid_now is not plugged in; it mounts when it appears"; fi
fi
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
# The hub push. Its .service runs only when hub.conf is not empty or hubs.d holds a file, so
# on a Pi with no token the timer runs and the job condition-skips rather than failing every 2 min.
svc gatbox-sync.timer '/usr/local/bin/gatbox-sync' '/etc/systemd/system/gatbox-sync.*'
# Disk images to the archive, over the home network only (docs/plans/2026-11-17-disk-image-drain.md). Its .service runs only
# when archive.conf is not empty, and sends nothing without ARCHIVE_LAN_URL in it.
lan_now=$(lan_host || true)
if [ -n "$lan_now" ] && [ "$(send_dropin "$lan_now")" != "$(cat "$SEND_DROPIN" 2>/dev/null)" ]; then
    mkdir -p "$(dirname "$SEND_DROPIN")" && send_dropin "$lan_now" > "$SEND_DROPIN.tmp" && chmod 644 "$SEND_DROPIN.tmp" && mv "$SEND_DROPIN.tmp" "$SEND_DROPIN"
    systemctl daemon-reload
    log "gatbox-disk-send may reach $lan_now (the archive at home, from ARCHIVE_LAN_URL)"
elif [ -z "$lan_now" ] && [ -e "$SEND_DROPIN" ]; then
    rm -f "$SEND_DROPIN" && systemctl daemon-reload && log "gatbox-disk-send: no private ARCHIVE_LAN_URL in archive.conf, so it reaches nothing"
fi
svc gatbox-disk-send.timer '/usr/local/bin/gatbox-disk-send' '/etc/systemd/system/gatbox-disk-send.*' '/usr/local/lib/gatbox/gatboxlib/*'
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
# MAME's title list, for LIBRARY -> FIND GAME. Once per MAME version, built the same way.
if mame_titles_wanted && ! mame_titles_ok; then
    log "building MAME's title list from mame -listxml (a minute or two)"
    mh=$(mktemp -d)
    HOME=$mh /usr/local/bin/gatbox-mame-roms --titles "$MAME_TITLES" 2>&1 | sed 's/^/     /' || true
    rm -rf "$mh"
    mame_titles_ok || warn "MAME's title list didn't build: LIBRARY's FIND GAME has nothing to search"
fi

# 9 ---------------------------------------------------------------------------
step "9/9 optional: fallback hotspot"
if [ "$AP" = off ]; then
    systemctl disable --now gatbox-ap-fallback.timer >/dev/null 2>&1 || true
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
    systemctl enable --now gatbox-ap-fallback.timer >/dev/null
    log "hotspot 'GATBOX' armed: comes up ~90 s after boot if no known Wi-Fi/Ethernet, and
        drops again when a saved network comes back into range"
elif systemctl -q is-enabled gatbox-ap-fallback.timer 2>/dev/null; then
    # Already on the timer. Clear a leftover service enablement if one is still there --
    # idempotent, and a half-migrated Pi is exactly what a re-run should tidy -- but do not
    # re-enable what is already enabled.
    systemctl disable gatbox-ap-fallback.service >/dev/null 2>&1 || true
    log "hotspot fallback already armed (left as is; GATBOX_AP_PSK=off removes it)"
elif systemctl -q is-enabled gatbox-ap-fallback.service 2>/dev/null; then
    # Armed the old way, by the service's own [Install]. Move it to the timer: a re-run
    # after a git pull is the documented upgrade path and has no passphrase to hand, and
    # migrating needs none. Without this the box keeps the boot-only behaviour -- the
    # dangling multi-user.target.wants symlink still works -- and the recurring recovery
    # this phase exists for never happens.
    systemctl disable gatbox-ap-fallback.service >/dev/null 2>&1 || true
    systemctl enable --now gatbox-ap-fallback.timer >/dev/null
    log "hotspot fallback moved from boot-only to the 2-minute timer (GATBOX_AP_PSK=off removes it)"
else
    log "skipped (set GATBOX_AP_PSK to enable)"
fi

# What is installed, written last: only a run that got this far records itself.
if ! installed_ok; then
    tmp=$(mktemp)
    installed_json > "$tmp" && install -D -m 644 "$tmp" "$INSTALLED"
    rm -f "$tmp"
    log "installed: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["describe"], *(["on", d["branch"]] if d["branch"] else []))' "$INSTALLED")"
fi

# ---------------------------------------------------------------------------
printf '\n%sdone.%s  %d file(s) changed this run\n' "$C" "$N" "${#CHANGED[@]}"
log "known Wi-Fi:  $(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print $1}' | paste -sd, -)"
log "timezone:     $(timedatectl show -p Timezone --value)"
if [ "$REBOOT" = 1 ]; then
    printf '\n   %sREBOOT NOW:%s  sudo reboot   (EEPROM/config.txt/hostname changes need it; new groups need a fresh login)\n\n' "$Y" "$N"
fi
