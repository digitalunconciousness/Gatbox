#!/usr/bin/env bash
# The bootstrap without root: --extract installs every manifest file into a scratch root, byte-identical to the
# checkout with the right modes, and a second --extract writes nothing (the idempotence the real run relies on);
# --check runs read-only; the RTC env guards refuse before the root check (see test-rtc-guard.sh for the rest).
#   bash tests/test-bootstrap.sh [path/to/gatbox-bootstrap.sh]
set -u
BOOT=$(readlink -f "${1:-$(dirname "$0")/../bootstrap/gatbox-bootstrap.sh}")
REPO=$(cd "$(dirname "$BOOT")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

check "bash -n"                                'bash -n "$BOOT"'
out1=$(bash "$BOOT" --extract "$T/root" 2>&1)
n=$(sed -n '/^MANIFEST="/,/^"/p;/^USER_MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]' | while read -r s _ _ o; do
      [ "$o" = optional ] && [ ! -f "$REPO/$s" ] || echo; done | wc -l)
check "--extract writes all $n manifest files"  '[[ $out1 == "$n file(s) written under $T/root" ]]'
bad=0
while read -r src dst mode opt; do
    [ "$opt" = optional ] && [ ! -f "$REPO/$src" ] && continue
    cmp -s "$REPO/$src" "$T/root$dst" && [ "$(stat -c %a "$T/root$dst")" = "$mode" ] || { bad=1; echo "        mismatch: $dst"; }
done < <(sed -n '/^MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]')
while read -r src rel mode; do
    dst="/home/$(id -un)/$rel"
    cmp -s "$REPO/$src" "$T/root$dst" && [ "$(stat -c %a "$T/root$dst")" = "$mode" ] || { bad=1; echo "        mismatch: $dst"; }
done < <(sed -n '/^USER_MANIFEST="/,/^"/p' "$BOOT" | grep '^[a-z]')
check "every file byte-identical, right mode"   '[ $bad = 0 ]'
out2=$(bash "$BOOT" --extract "$T/root" 2>&1)
check "second --extract writes nothing"         '[[ $out2 == "0 file(s) written under $T/root" ]]'
echo tampered >> "$T/root/usr/local/bin/gatbox-status"; chmod 700 "$T/root/usr/local/bin/gatbox-web"
out3=$(bash "$BOOT" --extract "$T/root" 2>&1)
check "changed content + mode get rewritten"     '[[ $out3 == "2 file(s) written under $T/root" ]] && cmp -s "$REPO/tools/gatbox-status" "$T/root/usr/local/bin/gatbox-status"'
# The hotspot fallback is driven by a timer, not by the service's own [Install]: it used to
# run once at boot and tell you to reboot to get back on Wi-Fi, which strands a box in a
# cabinet. The timer has to be installed and enabled, and the old enablement cleaned up.
check "MANIFEST installs the AP fallback timer"  'grep -qE "^backend/gatbox-ap-fallback\.timer +/etc/systemd/system/" "$BOOT"'
check "bootstrap enables and starts the timer"   'grep -qE "enable --now gatbox-ap-fallback.timer" "$BOOT"'
# Not a bare grep: the removal branch (GATBOX_AP_PSK=off) has always disabled the service, so
# that would pass without the arming branch cleaning up the old boot-time enablement at all.
check "and the arming branch cleans up the old enablement" \
    '[ "$(grep -c "disable gatbox-ap-fallback.service" "$BOOT")" -ge 2 ]'
check "the service no longer installs itself"    '! grep -q "WantedBy" "$REPO/backend/gatbox-ap-fallback.service"'
# Not the unconditional unit list: the hotspot is optional, so a Pi that deliberately has
# none must not be told every run that it is missing a unit. --check reports the timer only
# where the hotspot is armed at all.
check "--check reports the timer only when armed" \
    'grep -q "is-enabled gatbox-ap-fallback.timer" "$BOOT" && grep -q "replaces the boot-only service" "$BOOT"'

# gatbox-wifi: the root helper, its .path trigger, the spool it watches, and the group
# gatbox-web joins to write into that spool.
for f in gatbox-wifi gatbox-wifi.service gatbox-wifi.path; do
    check "MANIFEST installs $f"                 'grep -qE "^backend/$f +/" "$BOOT"'
done
# Without this the module never reaches the Pi and gatbox-web fails to import at startup.
# Every module in the package, read off the directory rather than listed here. A per-file
# check used to name wifi.py alone, which is the shape of hand-maintained list that let
# gatboxweb/orders.py ship un-installed: server.py imported it, the Pi did not have it, and
# gatbox-web would not start. The same for gatboxlib, which the logger also loads.
for d in gatboxweb gatboxlib; do
    for f in "$REPO/backend/$d"/*.py; do
        b=$(basename "$f")
        check "MANIFEST installs $d/$b" \
              'grep -qE "^backend/'"$d/${b//./\\.}"' +/usr/local/lib/gatbox/'"$d/${b//./\\.}"'" "$BOOT"'
    done
done
check "the helper installs to sbin, 755"         'grep -qE "^backend/gatbox-wifi +/usr/local/sbin/gatbox-wifi +755" "$BOOT"'
check "bootstrap enables the .path"              'grep -q "svc gatbox-wifi.path" "$BOOT"'
check "sysusers creates the gatbox-wifi group"   'grep -q "^g gatbox-wifi" "$REPO/bootstrap/files/sysusers-gatbox.conf"'
# 2770: setgid so the dashboard's files keep the group, and not world-readable, because a
# request file in here holds a plaintext Wi-Fi key until the helper takes it.
check "tmpfiles creates the spool 2770 root:gatbox-wifi" \
    'grep -qE "^d /var/spool/gatbox-wifi +2770 +root +gatbox-wifi" "$REPO/bootstrap/files/tmpfiles-gatbox.conf"'
check "gatbox-web may write to the spool"        'grep -q "ReadWritePaths=-/var/spool/gatbox-wifi" "$REPO/backend/gatbox-web.service"'
check "gatbox-web is in the gatbox-wifi group"   'grep -q "SupplementaryGroups=gatbox-wifi" "$REPO/backend/gatbox-web.service"'
check "--check reports the spool"                'grep -q "/var/spool/gatbox-wifi" "$BOOT"'

# Phase 8: several hubs. Both credential paths must always exist, or the unit fails outright.
TMPF="$REPO/bootstrap/files/tmpfiles-gatbox.conf"; SYNCU="$REPO/backend/gatbox-sync.service"
check "tmpfiles creates hubs.d 0700 root"        'grep -qE "^d /etc/gatbox/hubs\.d +0700 +root +root" "$TMPF"'
check "and an empty hub.conf 600 root, no content" 'grep -qE "^f /etc/gatbox/hub\.conf +0600 +root +root +- *$" "$TMPF"'
check "the unit loads hub.conf and hubs.d"       'grep -qx "LoadCredential=hub:/etc/gatbox/hub.conf" "$SYNCU" && grep -qx "LoadCredential=hubs:/etc/gatbox/hubs.d" "$SYNCU"'
check "and runs when either has something (OR)"  'grep -qx "ConditionFileNotEmpty=|/etc/gatbox/hub.conf" "$SYNCU" && grep -qx "ConditionDirectoryNotEmpty=|/etc/gatbox/hubs.d" "$SYNCU" && ! grep -q "^ConditionPathExists" "$SYNCU"'
check "--check reports hubs.d"                   'grep -q "/etc/gatbox/hubs.d (tmpfiles.d" "$BOOT"'
# The game library (phase B): gatbox-library creates library.sqlite under /srv/gatbox/library, which only the install can make
# (/srv/gatbox is root's). Without these, `seed --apply` stops with "Permission denied".
WEBU="$REPO/backend/gatbox-web.service"
check "tmpfiles creates the library folder 2775 root:gatbox-manuals" 'grep -qE "^d /srv/gatbox/library +2775 +root +gatbox-manuals" "$TMPF"'
check "the bootstrap makes it, and --check reports it"  'grep -q "! -d /srv/gatbox/library" "$BOOT" && grep -q "/srv/gatbox/library (tmpfiles.d" "$BOOT"'
# library.sqlite and the -wal/-shm beside it are written by two accounts, so they must be group-writable (the Pi, 2026-11-14: the
# owner's file was 0644 and gatbox-web failed every write with "attempt to write a readonly database").
check "tmpfiles makes library.sqlite* group-writable (0664, group gatbox-manuals), adjusting only what exists (z, not f)" \
    'grep -qE "^z /srv/gatbox/library/library\.sqlite\* +0664 +- +gatbox-manuals" "$TMPF" && ! grep -qE "^f /srv/gatbox/library" "$TMPF"'
if [ "$(id -u)" = 0 ] && command -v systemd-tmpfiles >/dev/null 2>&1; then
    # The real line, run by systemd-tmpfiles against a scratch root: the three files change, the owner stays, a stranger is left alone.
    R="$T/tmpf-root"; mkdir -p "$R/etc/tmpfiles.d" "$R/srv/gatbox/library"
    printf 'root:x:0:0:root:/root:/bin/sh\n' > "$R/etc/passwd"; printf 'root:x:0:\ngatbox-manuals:x:983:\n' > "$R/etc/group"
    grep -E "^(d|z) /srv/gatbox/library" "$TMPF" > "$R/etc/tmpfiles.d/t.conf"
    for f in library.sqlite library.sqlite-wal library.sqlite-shm other.txt; do : > "$R/srv/gatbox/library/$f"; chmod 644 "$R/srv/gatbox/library/$f"; done
    chown 1000:0 "$R/srv/gatbox/library/library.sqlite"
    systemd-tmpfiles --root="$R" --create t.conf >/dev/null 2>&1
    check "tmpfiles line, run for real: the three files become 0664 group 983, the owner is kept, other.txt is untouched" \
        '[ "$(stat -c "%a %g" "$R/srv/gatbox/library/library.sqlite")" = "664 983" ] && [ "$(stat -c "%a %g" "$R/srv/gatbox/library/library.sqlite-wal")" = "664 983" ] \
         && [ "$(stat -c "%a %g" "$R/srv/gatbox/library/library.sqlite-shm")" = "664 983" ] && [ "$(stat -c %u "$R/srv/gatbox/library/library.sqlite")" = 1000 ] \
         && [ "$(stat -c %a "$R/srv/gatbox/library/other.txt")" = 644 ]'
    # A disk job that was running when the Pi lost power leaves its claim behind, and nothing treats an old one as dead (the Pi, 2026-11-16:
    # a 2.5 hour write against a 3 hour unit limit). The claim goes at boot, and only at boot: a bootstrap run (--create) must never remove
    # the claim of a job that is running now. A queued request and the last status are left.
    R2="$T/tmpf-root2"; mkdir -p "$R2/etc/tmpfiles.d" "$R2/var/spool/gatbox-disk"
    printf 'root:x:0:0:root:/root:/bin/sh\n' > "$R2/etc/passwd"; printf 'root:x:0:\n' > "$R2/etc/group"
    grep -E "^r! /var/spool/gatbox-disk/running.json" "$TMPF" > "$R2/etc/tmpfiles.d/t.conf"
    for f in running.json request.json status.json; do : > "$R2/var/spool/gatbox-disk/$f"; done
    systemd-tmpfiles --root="$R2" --create t.conf >/dev/null 2>&1
    still=$([ -e "$R2/var/spool/gatbox-disk/running.json" ] && echo yes || echo no)
    systemd-tmpfiles --root="$R2" --create --remove t.conf >/dev/null 2>&1
    still_noboot=$([ -e "$R2/var/spool/gatbox-disk/running.json" ] && echo yes || echo no)
    systemd-tmpfiles --root="$R2" --boot --remove t.conf >/dev/null 2>&1
    check "tmpfiles line, run for real: a bootstrap run or a create-and-remove without --boot leaves a running job's claim; a boot removes it and only it" \
        '[ "$still" = yes ] && [ "$still_noboot" = yes ] && [ ! -e "$R2/var/spool/gatbox-disk/running.json" ] \
         && [ -e "$R2/var/spool/gatbox-disk/request.json" ] && [ -e "$R2/var/spool/gatbox-disk/status.json" ]'
else
    echo "  skip  the tmpfiles line run against a scratch root (needs root and systemd-tmpfiles)"
fi
check "the disk unit's limit is 12 h (an 80 GB write measured about 2.5 h on the Pi, against the 3 h it had), and a boot removes a dead job's claim" \
    'grep -qx "TimeoutStartSec=12h" "$REPO/backend/gatbox-disk.service" && grep -qx "r! /var/spool/gatbox-disk/running.json" "$TMPF"'
check "gatbox-web may write there (and is in the group)" 'grep -qx "ReadWritePaths=-/srv/gatbox/library" "$WEBU" && grep -qx "SupplementaryGroups=gatbox-manuals" "$WEBU"'

# Disk imaging's read-only tools (hdparm -I/-N, smartctl -i/-H). Installing the two packages has side effects that are
# switched off on purpose: smartmontools enables smartd, which polls every disk it finds, and hdparm ships a udev rule
# that sets an APM level on each drive as it appears (its USB guard knows pci-*-usb- paths, and a Pi 5's are
# platform-xhci-hcd, so it would not skip the dock).
PKGLINES=$(sed -n '/^PKGS=(/,/^      mame)/p' "$BOOT")
check "the package list names mame-tools (chdman, for the MAME CHD check), and the CHD module is in the manifest" \
    'grep -qw mame-tools <<<"$PKGLINES" && grep -q "^backend/gatboxlib/chd.py " "$BOOT"'
check "the package list names what FORMAT runs (dosfstools, exfatprogs, e2fsprogs): without them a format stops and says which is missing" \
    'grep -qw dosfstools <<<"$PKGLINES" && grep -qw exfatprogs <<<"$PKGLINES" && grep -qw e2fsprogs <<<"$PKGLINES"'
check "the package list names hdparm and smartmontools"   'grep -qw hdparm <<<"$PKGLINES" && grep -qw smartmontools <<<"$PKGLINES"'
# The drive browser (docs/plans/2026-11-15-drive-browser.md) lists the files in a stored image with sleuthkit and makes thumbnails with Pillow.
check "the package list names sleuthkit and python3-pil (the drive browser)" 'grep -qw sleuthkit <<<"$PKGLINES" && grep -qw python3-pil <<<"$PKGLINES"'
check "the hdparm udev rule is masked by a same-named /etc file" \
    'grep -qE "^bootstrap/files/85-hdparm-off\.rules +/etc/udev/rules\.d/85-hdparm\.rules +644" "$BOOT"'
check "and that file exists and holds no rule, only comments" \
    '[ -s "$REPO/bootstrap/files/85-hdparm-off.rules" ] && ! grep -qvE "^[[:space:]]*(#|$)" "$REPO/bootstrap/files/85-hdparm-off.rules"'
check "the install step switches smartd off"              'grep -q "disable --now smartmontools.service" "$BOOT"'
# --check with stand-ins for dpkg-query and systemctl, so the answer does not depend on what this machine has installed.
FB="$T/fakebin"; mkdir -p "$FB"
printf '#!/bin/sh\nexit 1\n' > "$FB/dpkg-query"                       # no package is installed
printf '#!/bin/sh\nexit 0\n' > "$FB/systemctl"                        # every unit is enabled and active
chmod +x "$FB/dpkg-query" "$FB/systemctl"
PATH="$FB:$PATH" bash "$BOOT" --check > "$T/check-none" 2>&1
check "--check tells a Pi without them to install both"   'grep -q "install    package hdparm" "$T/check-none" && grep -q "install    package smartmontools" "$T/check-none"'
check "--check tells a Pi without sleuthkit and python3-pil to install both" \
    'grep -q "install    package sleuthkit" "$T/check-none" && grep -q "install    package python3-pil" "$T/check-none"'
check "the package list names catdoc (the drive browser's text search reads old Office files with it)" 'grep -qw catdoc <<<"$PKGLINES"'
check "--check tells a Pi without catdoc to install it" 'grep -q "install    package catdoc" "$T/check-none"'
check "--check tells a Pi with smartd enabled to disable it" 'grep -q "disable    smartd (smartmontools.service)" "$T/check-none"'
printf '#!/bin/sh\nprintf "install ok installed"\n' > "$FB/dpkg-query" # every package is installed
printf '#!/bin/sh\ncase "$*" in *smartmontools*) exit 1;; esac\nexit 0\n' > "$FB/systemctl"   # smartd is off
PATH="$FB:$PATH" bash "$BOOT" --check > "$T/check-have" 2>&1
check "--check says neither when both are installed and smartd is off" \
    '! grep -qE "package (hdparm|smartmontools)|disable    smartd" "$T/check-have"'

# Task 4: the disk job. A user and two groups, a spool, a bare mountpoint, a udev rule, two units, a tool, and the image
# store's mount unit, which is written from /etc/gatbox/disks.conf. Every system-config piece is read by the owner first.
SYSU="$REPO/bootstrap/files/sysusers-gatbox.conf"; RULE="$REPO/bootstrap/files/98-gatbox-disk.rules"
DISKU="$REPO/backend/gatbox-disk.service"; DISKP="$REPO/backend/gatbox-disk.path"
for f in "tools/gatbox-disk +/usr/local/bin/gatbox-disk +755" "backend/gatbox-disk.service +/etc/systemd/system/gatbox-disk.service +644" \
         "backend/gatbox-disk.path +/etc/systemd/system/gatbox-disk.path +644" \
         "bootstrap/files/98-gatbox-disk.rules +/etc/udev/rules.d/98-gatbox-disk.rules +644"; do
    check "MANIFEST installs ${f%% *}"             "grep -qE '^$f' \"\$BOOT\""
done
check "ddrescue is in the package list"           'grep -qw gddrescue <<<"$PKGLINES"'
check "sysusers: the job's user and the spool's group" 'grep -qE "^u gatbox-disk - " "$SYSU" && grep -qE "^g gatbox-disk-queue -" "$SYSU"'
check "tmpfiles: the spool is 2770 gatbox-disk:gatbox-disk-queue" \
    'grep -qE "^d /var/spool/gatbox-disk +2770 +gatbox-disk +gatbox-disk-queue" "$TMPF"'
# With the 4 TB drive mounted here, a line with a mode and an owner would reset the drive's own root directory at every run.
check "tmpfiles: the store's bare mountpoint sets no mode, owner or group (it must not touch the mounted drive)" \
    'grep -qE "^d /srv/gatbox/disks +- +- +- +-[[:space:]]*$" "$TMPF"'
check "tmpfiles: disks.conf is created empty, 600 root" 'grep -qE "^f /etc/gatbox/disks\.conf +0600 +root +root +-" "$TMPF"'
# ENV{ID_BUS}=="usb" would miss the dock's SATA drive: udev sets ID_BUS=ata on it (Task 0, 2026-11-06).
check "udev: whole disks behind any USB parent, group gatbox-disk 0660, the desktop told to ignore them" \
    'grep -qE "^SUBSYSTEM==\"block\", ENV\{DEVTYPE\}==\"disk\", SUBSYSTEMS==\"usb\", GROUP=\"gatbox-disk\", MODE=\"0660\", ENV\{UDISKS_IGNORE\}=\"1\"" "$RULE"'
check "... and it does not match on ID_BUS"        '! grep -v "^#" "$RULE" | grep -q "ID_BUS"'
# udisks reads UDISKS_IGNORE per block device: the disk's flag did not stop the desktop mounting a CF card's partition (the Pi,
# 2026-11-15). The partition line sets that flag and nothing else, so partitions keep root:disk.
check "udev: partitions behind any USB parent are ignored by the desktop too, with no group or mode of their own" \
    'grep -qE "^SUBSYSTEM==\"block\", ENV\{DEVTYPE\}==\"partition\", SUBSYSTEMS==\"usb\", ENV\{UDISKS_IGNORE\}=\"1\"[[:space:]]*$" "$RULE"'
check "gatbox-web holds the spool's group and may write the spool, not the devices' group" \
    'grep -qx "SupplementaryGroups=gatbox-disk-queue" "$WEBU" && grep -qx "ReadWritePaths=-/var/spool/gatbox-disk" "$WEBU" && ! grep -qE "^SupplementaryGroups=.*gatbox-disk($| )" "$WEBU"'
check "the job runs as gatbox-disk with the queue group" \
    'grep -qx "User=gatbox-disk" "$DISKU" && grep -qx "SupplementaryGroups=gatbox-disk-queue" "$DISKU"'
check "the job is ordered after the store's mount and never requires it (an unplugged drive must not jam the spool)" \
    'grep -qx "After=srv-gatbox-disks.mount" "$DISKU" && ! grep -qE "^(Requires|RequiresMountsFor|BindsTo)=" "$DISKU"'
check "gatbox-disk.path is enabled and --check lists it" 'grep -q "svc gatbox-disk.path" "$BOOT" && grep -q "gatbox-disk.path gatbox-manuals.path gatbox-mame-roms.path gatbox-browse.socket gatbox-disk-send.timer; do" "$BOOT"'
all_in() { local f=$1 w; shift; for w in "$@"; do grep -qF -- "$w" "$f" || return 1; done; }     # every string is in the file
# FIND A MANUAL (the owner, 2026-11-16): the manuals job, which was written in the repo (2026-11-05) and never wired into the bootstrap, so
# nothing on the Pi made its user, its spool or its units. It is installed the way the disk job is, and the owner reads the diff first.
MANU="$REPO/backend/gatbox-manuals.service"
for f in "backend/gatbox-manuals.service +/etc/systemd/system/gatbox-manuals.service +644" \
         "backend/gatbox-manuals.path +/etc/systemd/system/gatbox-manuals.path +644"; do
    check "MANIFEST installs ${f%% *}"             "grep -qE '^$f' \"\$BOOT\""
done
check "sysusers: the manuals job's own user, in the manuals folder's group" \
    'grep -qE "^u gatbox-fetch - " "$SYSU" && grep -qx "m gatbox-fetch gatbox-manuals" "$SYSU"'
check "tmpfiles: the manuals spool is 2770 gatbox-fetch:gatbox-fetch" 'grep -qE "^d /var/spool/gatbox-manuals +2770 +gatbox-fetch +gatbox-fetch" "$TMPF"'
check "gatbox-web holds the spool's group and may write the spool, starting anyway before it exists" \
    'grep -qx "SupplementaryGroups=gatbox-fetch" "$WEBU" && grep -qx "ReadWritePaths=-/var/spool/gatbox-manuals" "$WEBU"'
check "the job runs as gatbox-fetch with the manuals group, no capabilities, and may write only the manuals folder and its spool" \
    'grep -qx "User=gatbox-fetch" "$MANU" && grep -qx "SupplementaryGroups=gatbox-manuals" "$MANU" && grep -qx "CapabilityBoundingSet=" "$MANU" && grep -qx "ReadWritePaths=/srv/gatbox/manuals /var/spool/gatbox-manuals" "$MANU"'
check "the user is made before gatbox-web restarts with that group (a missing group would stop it starting), and the sysusers step runs when the user is missing" \
    '[ "$(grep -n "systemd-sysusers /etc/sysusers.d" "$BOOT" | head -1 | cut -d: -f1)" -lt "$(grep -n "^svc gatbox-web.service" "$BOOT" | head -1 | cut -d: -f1)" ] && grep -q "! getent passwd gatbox-fetch" "$BOOT"'
check "gatbox-manuals.path is enabled and --check lists it, the user and the spool" \
    'grep -q "^svc gatbox-manuals.path" "$BOOT" && grep -q "gatbox-manuals.path gatbox-mame-roms.path gatbox-browse.socket gatbox-disk-send.timer; do" "$BOOT" && all_in "$BOOT" "user gatbox-fetch" "/var/spool/gatbox-manuals"'
# ROM lists for games added after the seed (phase D4, the owner, 2026-11-16: "wire in the ROM-list job"): the units were in the repo
# marked NOT INSTALLED, and nothing in the bootstrap installed them.
MRU="$REPO/backend/gatbox-mame-roms.service"
for f in "backend/gatbox-mame-roms.service +/etc/systemd/system/gatbox-mame-roms.service +644" \
         "backend/gatbox-mame-roms.path +/etc/systemd/system/gatbox-mame-roms.path +644"; do
    check "the manifest installs ${f%% *}" "grep -qE \"^$f\" \"\$BOOT\""
done
check "the chip-list units no longer say NOT INSTALLED" '! grep -q "NOT INSTALLED" "$MRU" "$REPO/backend/gatbox-mame-roms.path"'
check "sysusers: the chip-list spool's group; tmpfiles: the spool 2770 root:gatbox-mame" \
    'grep -qx "g gatbox-mame -" "$SYSU" && grep -qE "^d /var/spool/gatbox-mame +2770 +root +gatbox-mame" "$TMPF"'
check "gatbox-web holds the group and may write the spool, nothing more" \
    'grep -qx "SupplementaryGroups=gatbox-mame" "$WEBU" && grep -qx "ReadWritePaths=-/var/spool/gatbox-mame" "$WEBU"'
check "the job: a DynamicUser with the spool's group, no network, no devices, no capabilities, writing only its spool" \
    'grep -qx "DynamicUser=yes" "$MRU" && grep -qx "SupplementaryGroups=gatbox-mame" "$MRU" && grep -qx "PrivateNetwork=yes" "$MRU" && grep -qx "PrivateDevices=yes" "$MRU" && grep -qx "CapabilityBoundingSet=" "$MRU" && grep -qx "ReadWritePaths=/var/spool/gatbox-mame" "$MRU"'
check "gatbox-mame-roms.path is enabled and restarted on change, sysusers and tmpfiles run when the group or spool is missing, and --check lists them" \
    'grep -q "^svc gatbox-mame-roms.path" "$BOOT" && grep -q "getent group gatbox-mame >/dev/null || ! getent passwd gatbox-disk-send >/dev/null; then" "$BOOT" && grep -q "\[ ! -d /var/spool/gatbox-mame \]" "$BOOT" && all_in "$BOOT" "group gatbox-mame" "/var/spool/gatbox-mame (tmpfiles.d)"'
check "--check reports the user, group, spool, mountpoint and the owner's group" \
    'all_in "$BOOT" "user gatbox-disk" "group gatbox-disk-queue (sysusers" "/var/spool/gatbox-disk" "/srv/gatbox/disks" "+= gatbox-disk-queue"'
# udev works out GROUP= when it loads a rule. On the Pi (2026-11-14) telling the running udevd to reload did not make it see the
# group the bootstrap had just created; only restarting systemd-udevd did. So after sysusers: restart it, then re-apply the rule.
check "after sysusers, systemd-udevd is restarted and then the rule is re-applied to the drives already plugged in" \
    'awk "/systemd-sysusers \/etc\/sysusers/ { s = NR } /systemctl restart systemd-udevd/ { r = NR } /udevadm trigger --action=change --subsystem-match=block/ { if (r && NR > r) t = NR } END { exit !(s && r > s && t > r) }" "$BOOT"'
check "... and not just told to reload after sysusers (that did nothing on the Pi)" \
    '! awk "/systemd-sysusers \/etc\/sysusers/ { s = NR } /udevadm control --reload-rules/ { if (s && NR > s) bad = 1 } END { exit !bad }" "$BOOT"'
want='[ "${SYSUSERS_RAN:-0}" = 1 ] || [ -n "$(unfixed_disk_nodes)" ]'
check "... when sysusers has just made the group, and also when a USB disk is still outside it (a run cut short is mended by a re-run)" \
    'grep -qF -- "$want" "$BOOT"'
# unfixed_disk_nodes, through --check, with a fake /sys/block and /dev and a stand-in getent: a disk behind USB whose node is
# not in the group is named; a disk that is not behind USB, a missing group and a missing rule say nothing.
S="$T/udev"; mkdir -p "$S/sys/devices/platform/xhci/usb1/1-2/host0/block/sdb" "$S/sys/devices/platform/emmc/block/sdc" "$S/sys/block" "$S/dev" "$S/bin"
ln -s ../devices/platform/xhci/usb1/1-2/host0/block/sdb "$S/sys/block/sdb"
ln -s ../devices/platform/emmc/block/sdc "$S/sys/block/sdc"          # not behind USB
: > "$S/dev/sdb"; : > "$S/dev/sdc"; : > "$S/rule"
nodegid=$(stat -c %g "$S/dev/sdb"); REAL_GETENT=$(command -v getent)
udev_check() {   # <gid getent reports for gatbox-disk, or "none"> [rule file]
    if [ "$1" = none ]; then printf '#!/bin/sh\n[ "$2" = gatbox-disk ] && exit 2\nexec %s "$@"\n' "$REAL_GETENT" > "$S/bin/getent"
    else printf '#!/bin/sh\n[ "$2" = gatbox-disk ] && { echo "gatbox-disk:x:%s:"; exit 0; }\nexec %s "$@"\n' "$1" "$REAL_GETENT" > "$S/bin/getent"; fi
    chmod +x "$S/bin/getent"
    PATH="$S/bin:$PATH" GATBOX_DISK_RULE="${2:-$S/rule}" GATBOX_SYS_BLOCK="$S/sys/block" GATBOX_DEV="$S/dev" bash "$BOOT" --check 2>&1
}
udev_check $((nodegid + 1)) > "$T/udev-stale"
check "--check: a USB disk whose node is outside group gatbox-disk: told to restart udevd and re-apply the rule, naming only it" \
    'grep -qxF "  restart    systemd-udevd and re-apply the disk rule: sdb is not in group gatbox-disk yet" "$T/udev-stale"'
udev_check "$nodegid" > "$T/udev-ok"
check "--check: every USB disk already in the group: nothing said" '! grep -q "systemd-udevd" "$T/udev-ok"'
udev_check none > "$T/udev-nogroup"
check "--check: the group does not exist yet: nothing to compare with, nothing said" '! grep -q "restart    systemd-udevd" "$T/udev-nogroup"'
udev_check $((nodegid + 1)) "$S/no-such-rule" > "$T/udev-norule"
check "--check: the rule is not installed yet: nothing said" '! grep -q "restart    systemd-udevd" "$T/udev-norule"'
# smartmontools' postinst starts smartd, whose SMART ENABLE hung the dock's bridge twice on the Pi's first install
# (2026-11-14). apt runs with a policy-rc.d that refuses that one unit; the bootstrap's own printf is run here.
pol="$T/policy-rc.d"; POLICY=$pol
POLICY_MARK=$(sed -n 's/^POLICY_MARK="\(.*\)"$/\1/p' "$BOOT")
eval "$(grep -m1 -F 'case "$1" in smartmontools' "$BOOT" | sed 's/^ *//')"; chmod +x "$pol" 2>/dev/null
check "the policy-rc.d refuses to start smartmontools, by either unit name (exit 101)" \
    '"$pol" smartmontools.service start >/dev/null 2>&1; a=$?; "$pol" smartd start >/dev/null 2>&1; b=$?; [ $a = 101 ] && [ $b = 101 ]'
check "... and allows every other unit (exit 0)" \
    '"$pol" ssh.service restart >/dev/null 2>&1 && "$pol" systemd-udevd.service restart >/dev/null 2>&1 && "$pol" gatbox-raillog.service start >/dev/null 2>&1'
check "... it carries the mark the bootstrap uses to recognise its own leftover" 'grep -qxF "$POLICY_MARK" "$pol"'
check "the policy is put in before apt runs and taken out right after it, before a failed install can die" \
    'awk "/^POLICY=/ { p = 1 } /> \"\\\$POLICY\"/ { w = NR } /apt-get install -y -q/ { a = NR } /rm -f \"\\\$POLICY\"$/ { if (a) r = NR } /couldn.t install/ { d = NR } END { exit !(w && a > w && r > a && d > r) }" "$BOOT"'
check "... only when smartmontools is about to be installed, and never over a policy-rc.d the Pi already has" \
    'grep -qF "grep -qx smartmontools && [ ! -e \"\$POLICY\" ]" "$BOOT"'
check "... a leftover of its own from a run that was cut short is removed first" \
    'grep -qF "grep -qxF \"\$POLICY_MARK\" \"\$POLICY\" && rm -f \"\$POLICY\"" "$BOOT"'
# /etc/gatbox is 0750 root: without root a test for what is inside it fails whether or not the file is there, so --check used to
# say "create hub.conf ..." on a Pi that has them. A readable folder is still checked.
E="$T/etcg"; mkdir -p "$E"
GATBOX_ETC="$E" bash "$BOOT" --check > "$T/etc-empty" 2>&1
check "--check: a readable /etc/gatbox with nothing in it: hubs.d, hub.conf and archive.conf are listed as missing" \
    'grep -q "create     /etc/gatbox/hubs.d" "$T/etc-empty" && grep -q "create     /etc/gatbox/hub.conf" "$T/etc-empty" && grep -q "create     /etc/gatbox/archive.conf" "$T/etc-empty"'
if [ "$(id -u)" = 0 ]; then
    echo "  skip  --check on a /etc/gatbox that cannot be opened (this is root, which can open any folder)"
else
    chmod 000 "$E"
    GATBOX_ETC="$E" bash "$BOOT" --check > "$T/etc-closed" 2>&1; chmod 755 "$E"
    check "--check: a /etc/gatbox this user cannot open: no false 'create' lines, and a note says what was not looked at" \
        '! grep -qE "create     /etc/gatbox/(hubs.d|hub.conf|archive.conf)" "$T/etc-closed" && grep -q "^note: /etc/gatbox is readable by root only" "$T/etc-closed"'
fi
# Disk images to the archive (docs/plans/2026-11-17-disk-image-drain.md): the home network only, the store read-only, imaging first.
DSS="$REPO/backend/gatbox-disk-send.service"; DST="$REPO/backend/gatbox-disk-send.timer"
for f in "tools/gatbox-disk-send +/usr/local/bin/gatbox-disk-send +755" "backend/gatbox-disk-send.service +/etc/systemd/system/gatbox-disk-send.service +644" \
         "backend/gatbox-disk-send.timer +/etc/systemd/system/gatbox-disk-send.timer +644"; do
    check "MANIFEST installs ${f%% *}" 'grep -qE "^$f" "$BOOT"'
done
check "the sender's timer is enabled by svc and listed by --check" \
    'grep -q "^svc gatbox-disk-send.timer" "$BOOT" && grep -q "gatbox-browse.socket gatbox-disk-send.timer; do" "$BOOT" && grep -q "user gatbox-disk-send" "$BOOT"'
check "sysusers: its own user, in the disk spool's group only (to read the images), never the devices' group" \
    'grep -qE "^u gatbox-disk-send - " "$SYSU" && grep -qx "m gatbox-disk-send gatbox-disk-queue" "$SYSU" && ! grep -qE "^m gatbox-disk-send gatbox-disk$" "$SYSU"'
check "the sender: the store and the spool read-only, nothing writable but its own state" \
    'grep -qx "ProtectSystem=strict" "$DSS" && grep -qx "ReadOnlyPaths=/srv/gatbox/disks" "$DSS" && grep -qx "StateDirectory=gatbox-disk-send" "$DSS" && ! grep -qE "^ReadWritePaths=" "$DSS"'
check "... every address denied in the unit itself; only the bootstrap's drop-in opens the archive's one address" \
    'grep -qx "IPAddressDeny=any" "$DSS" && grep -qx "IPAddressAllow=localhost" "$DSS"'
# the drop-in from ARCHIVE_LAN_URL (--check reads the two paths from the environment, as for the store's mount unit)
lan_check() { printf '%s\n' "$1" > "$T/archive.conf"; GATBOX_ARCHIVE_CONF="$T/archive.conf" GATBOX_SEND_DROPIN="$T/${2:-none}.conf" bash "$BOOT" --check 2>&1; }
check "--check: a private ARCHIVE_LAN_URL: the drop-in that allows that one address" \
    'lan_check "ARCHIVE_LAN_URL=http://192.0.2.7:8081" | grep -qF "write      gatbox-disk-send.service.d/home.conf (IPAddressAllow=192.0.2.7, the archive at home)"'
check "... an IPv6 one in brackets too" \
    'lan_check "ARCHIVE_LAN_URL = http://[2001:db8::7]:8081/" | grep -qF "(IPAddressAllow=2001:db8::7, the archive at home)"'
for bad in "ARCHIVE_LAN_URL=http://8.8.8.8:8081" "ARCHIVE_LAN_URL=http://archive.example.test:8081" "ARCHIVE_LAN_URL=http://127.0.0.1:8081" \
           "ARCHIVE_URL=https://archive.example.test" "ARCHIVE_LAN_URL=http://192.0.2.1;touch $T/pwned:8081"; do
    check "--check: '$bad' is not a private address: no drop-in" '! lan_check "$bad" | grep -q "home.conf" && [ ! -e "$T/pwned" ]'
done
printf '# Written by gatbox-bootstrap.sh from ARCHIVE_LAN_URL in /etc/gatbox/archive.conf: edit that, not this.\n[Service]\nIPAddressAllow=192.0.2.7\n' > "$T/have.conf"
check "--check: the drop-in already matches: nothing said" '! lan_check "ARCHIVE_LAN_URL=http://192.0.2.7:8081" have | grep -q "home.conf"'
check "--check: ARCHIVE_LAN_URL gone but a drop-in left: told to remove it" 'lan_check "ARCHIVE_URL=https://archive.example.test" have | grep -qF "remove     gatbox-disk-send.service.d/home.conf"'
check "... the token as a credential, the lowest priority, no devices, and it skips when archive.conf is empty" \
    'grep -qx "LoadCredential=archive:/etc/gatbox/archive.conf" "$DSS" && all_in "$DSS" "IOSchedulingClass=idle" "Nice=19" "PrivateDevices=yes" && grep -qx "ConditionFileNotEmpty=/etc/gatbox/archive.conf" "$DSS"'
check "... the timer counts from the end of the last run (a run can take hours)" 'grep -qx "OnUnitInactiveSec=15min" "$DST" && ! grep -q "OnUnitActiveSec" "$DST"'
check "gatbox-web reads the sender's state, read-only" 'grep -qx "ReadOnlyPaths=-/var/lib/gatbox-disk-send" "$WEBU"'
# The drive browser (docs/plans/2026-11-15-drive-browser.md): its user, its socket-activated service, and what the service cannot do.
BRS="$REPO/backend/gatbox-browse.service"; BRK="$REPO/backend/gatbox-browse.socket"
for f in "backend/gatbox-browse +/usr/local/bin/gatbox-browse +755" "backend/gatbox-browse.socket +/etc/systemd/system/gatbox-browse.socket +644" \
         "backend/gatbox-browse.service +/etc/systemd/system/gatbox-browse.service +644"; do
    check "MANIFEST installs ${f%% *}" 'grep -qE "^$f" "$BOOT"'
done
check "sysusers: the drive browser's user" 'grep -qE "^u gatbox-browse - " "$SYSU"'
check "gatbox-web joins the browser's group, to reach its socket" \
    'grep -qx "SupplementaryGroups=gatbox-browse" "$WEBU"'
check "the socket: listens under /run/gatbox-browse, mode 660, for the group gatbox-browse only" \
    'grep -qx "ListenStream=/run/gatbox-browse/browse.sock" "$BRK" && grep -qx "SocketMode=0660" "$BRK" && grep -qx "SocketGroup=gatbox-browse" "$BRK" && grep -qx "WantedBy=sockets.target" "$BRK"'
check "the service has no network, no devices, no capabilities, and only the unix socket" \
    'all_in "$BRS" "PrivateNetwork=yes" "PrivateDevices=yes" "RestrictAddressFamilies=AF_UNIX" "NoNewPrivileges=yes" && grep -qx "CapabilityBoundingSet=" "$BRS" && grep -qx "AmbientCapabilities=" "$BRS"'
check "... it sees the image store read-only, with its own two folders (_browse and _exports) the only writable places there" \
    'grep -qx "ProtectSystem=strict" "$BRS" && grep -qx "ReadOnlyPaths=/srv/gatbox/disks" "$BRS" && grep -qx "ReadWritePaths=-/srv/gatbox/disks/_browse -/srv/gatbox/disks/_exports" "$BRS" && ! grep -qE "^ReadWritePaths=.*(/srv/gatbox/disks( |$)|/var/spool|/srv/gatbox/disks/[^_])" "$BRS"'
check "... it runs as its own user, joins the disk queue group to read images and never the devices' group" \
    'grep -qx "User=gatbox-browse" "$BRS" && grep -qx "SupplementaryGroups=gatbox-disk-queue" "$BRS" && ! grep -qE "^(Supplementary)?Groups?=.*gatbox-disk( |$)" "$BRS"'
check "... a syscall filter, a memory ceiling and a task ceiling" 'grep -qx "SystemCallFilter=@system-service" "$BRS" && grep -qE "^MemoryMax=" "$BRS" && grep -qE "^TasksMax=" "$BRS"'
check "... ordered after the store's mount and never requiring it (an unplugged drive must not stop it saying so)" \
    'grep -qx "After=srv-gatbox-disks.mount" "$BRS" && ! grep -qE "^(Requires|RequiresMountsFor|BindsTo)=.*(srv-gatbox-disks|/srv/gatbox/disks)" "$BRS"'
check "... its folder on the store is made only when the store is mounted and marked, and by the one line that runs outside the sandbox" \
    'grep -qE "^ExecStartPre=\+/bin/sh -c .mountpoint -q /srv/gatbox/disks && \[ -f /srv/gatbox/disks/.gatbox-image-store \] && install -d -o gatbox-browse" "$BRS"'
check "... the exports folder is made by the same line, group-writable by the spool's group (setgid) so the owner can delete a copy, and the browser's own library file is installed" \
    'grep -q "install -d -o gatbox-browse -g gatbox-disk-queue -m 2775 /srv/gatbox/disks/_exports" "$BRS" && grep -q "^backend/gatboxlib/browsecopy.py " "$BOOT"'
check "the camera-raw preview reader is in the manifest: the browser imports it for a NEF, and a missing file would make every photo's thumbnail fail" \
    'grep -q "^backend/gatboxlib/rawpreview.py " "$BOOT"'
check "the COMPARE module is in the manifest: gatbox-browse imports it at start, and a missing file would stop the whole drive browser, not only COMPARE" \
    'grep -q "^backend/gatboxlib/browsehash.py " "$BOOT"'
check "the bootstrap enables and starts the socket, and --check lists it and the user" \
    'grep -q "^svc gatbox-browse.socket" "$BOOT" && grep -q "gatbox-browse.socket gatbox-disk-send.timer; do" "$BOOT" && grep -q "create     user gatbox-browse" "$BOOT"'
if command -v systemd-analyze >/dev/null 2>&1; then
    # the unit files read cleanly: no unknown directive and no syntax error (an unknown user or a missing program is fine here)
    out=$(systemd-analyze verify "$BRK" "$BRS" 2>&1 | grep -iE "unknown (key|section|lvalue)|failed to parse|bad (unit|setting)|invalid" | head -3)
    check "systemd-analyze reads both units with no unknown directive or syntax error" '[ -z "$out" ]'
    out=$(systemd-analyze verify "$REPO/backend/gatbox-manuals.path" "$MANU" "$REPO/backend/gatbox-mame-roms.path" "$MRU" 2>&1 | grep -iE "unknown (key|section|lvalue)|failed to parse|bad (unit|setting)|invalid" | head -3)
    check "systemd-analyze reads the manuals job's and the chip-list job's units with no unknown directive or syntax error" '[ -z "$out" ]'
else
    echo "  skip  systemd-analyze verify of the drive browser's and the manuals job's units (systemd-analyze not installed)"
fi
# The store's mount unit
UUID=3F2A7C19-8D4E-4B60-9A15-6E0C2B7D4F83; uuid=${UUID,,}
unit=$(bash "$BOOT" --print-store-unit "$UUID" 2>&1); rc=$?
check "--print-store-unit prints a mount unit for that UUID, lower-cased (exit 0)" \
    '[ $rc = 0 ] && grep -qx "What=/dev/disk/by-uuid/$uuid" <<<"$unit" && grep -qx "Where=/srv/gatbox/disks" <<<"$unit" && grep -qx "Type=ext4" <<<"$unit"'
check "... mounted nodev, nosuid and noexec"       'grep -qx "Options=noatime,nodev,nosuid,noexec" <<<"$unit"'
check "... mounted when the drive appears, nothing requires it" \
    'grep -q "^WantedBy=dev-disk-by" <<<"$unit" && ! grep -qE "^(Requires|RequiresMountsFor|BindsTo|Before|After)=" <<<"$unit"'
if command -v systemd-escape >/dev/null 2>&1; then
    check "... its device unit is the name systemd itself gives /dev/disk/by-uuid/<uuid>" \
        'grep -qxF "WantedBy=$(systemd-escape -p --suffix=device "/dev/disk/by-uuid/$uuid")" <<<"$unit"'
else
    echo "  skip  the device unit name against systemd-escape (not installed here)"
fi
for bad in "" nope 3f2a7c19-8d4e-4b60-9a15-6e0c2b7d4f8 "3f2a7c19-8d4e-4b60-9a15-6e0c2b7d4f83; rm -rf /"; do
    out=$(bash "$BOOT" --print-store-unit "$bad" 2>&1); rc=$?
    check "--print-store-unit refuses '$bad' (exit 2, usage, no unit)" '[ $rc = 2 ] && [[ $out == *"usage"* ]] && [[ $out != *"[Mount]"* ]]'
done
# --check compares what disks.conf says with the unit on disk (it reads the two paths from the environment only in this mode)
printf '# the 4 TB drive\n  uuid = ignored lower-case key\n  UUID = %s \n' "$UUID" > "$T/disks.conf"
GATBOX_DISKS_CONF="$T/disks.conf" GATBOX_STORE_MOUNT="$T/none.mount" bash "$BOOT" --check > "$T/check-store1" 2>&1
check "--check: a UUID in disks.conf and no unit: told to write it" \
    'grep -qF "write      srv-gatbox-disks.mount (the image store, UUID $uuid)" "$T/check-store1"'
printf '%s\n' "$unit" > "$T/srv.mount"
GATBOX_DISKS_CONF="$T/disks.conf" GATBOX_STORE_MOUNT="$T/srv.mount" bash "$BOOT" --check > "$T/check-store2" 2>&1
check "--check: the unit already matches: nothing said" '! grep -q "srv-gatbox-disks.mount" "$T/check-store2"'
printf 'UUID=not-a-uuid\n' > "$T/disks.conf"
GATBOX_DISKS_CONF="$T/disks.conf" GATBOX_STORE_MOUNT="$T/none.mount" bash "$BOOT" --check > "$T/check-store3" 2>&1
check "--check: disks.conf without a real UUID: nothing to write" '! grep -q "srv-gatbox-disks.mount" "$T/check-store3"'
: > "$T/disks.conf"
GATBOX_DISKS_CONF="$T/disks.conf" GATBOX_STORE_MOUNT="$T/none.mount" bash "$BOOT" --check > "$T/check-store4" 2>&1
check "--check: an empty disks.conf (the state before the drive is formatted): nothing to write" '! grep -q "srv-gatbox-disks.mount" "$T/check-store4"'

bash "$BOOT" --check > "$T/check" 2>&1; rc=$?
check "--check runs without root (exit 0 or 3)" '[ $rc = 0 ] || [ $rc = 3 ]'
# Without root the script refuses at its first check. As root it would go on and run for real (on a Pi, `sudo bash
# tests/test-bootstrap.sh` has everything it asks for), so this is not run as root.
if [ "$(id -u)" = 0 ]; then
    echo "  skip  a real run refuses without root (this is root: the script would go on to install)"
else
    out=$(bash "$BOOT" 2>&1); rc=$?
    check "a real run refuses without root"         '[ $rc = 1 ] && [[ $out == *"run with sudo"* ]]'
fi
# A mistyped flag used to fall through to a real run: "--checksudo", from a mangled
# paste, installed files and restarted services on a live Pi instead of reporting.
for bad in --chekc --checksudo --dry-run -n extract --check=1; do
    out=$(bash "$BOOT" "$bad" 2>&1); rc=$?
    check "unknown argument '$bad' refuses (exit 2, usage)" \
          '[ $rc = 2 ] && [[ $out == *"unknown argument"* ]] && [[ $out == *"--check"* ]]'
done

echo "--- --check on this Pi:"; sed 's/^/    /' "$T/check"
echo "bootstrap: $pass passed, $fail failed"
[ "$fail" = 0 ]
