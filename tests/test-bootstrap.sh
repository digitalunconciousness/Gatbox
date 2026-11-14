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
check "gatbox-web may write there (and is in the group)" 'grep -qx "ReadWritePaths=-/srv/gatbox/library" "$WEBU" && grep -qx "SupplementaryGroups=gatbox-manuals" "$WEBU"'

# Disk imaging's read-only tools (hdparm -I/-N, smartctl -i/-H). Installing the two packages has side effects that are
# switched off on purpose: smartmontools enables smartd, which polls every disk it finds, and hdparm ships a udev rule
# that sets an APM level on each drive as it appears (its USB guard knows pci-*-usb- paths, and a Pi 5's are
# platform-xhci-hcd, so it would not skip the dock).
PKGLINES=$(sed -n '/^PKGS=(/,/^      mame)/p' "$BOOT")
check "the package list names hdparm and smartmontools"   'grep -qw hdparm <<<"$PKGLINES" && grep -qw smartmontools <<<"$PKGLINES"'
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
check "--check tells a Pi with smartd enabled to disable it" 'grep -q "disable    smartd (smartmontools.service)" "$T/check-none"'
printf '#!/bin/sh\nprintf "install ok installed"\n' > "$FB/dpkg-query" # every package is installed
printf '#!/bin/sh\ncase "$*" in *smartmontools*) exit 1;; esac\nexit 0\n' > "$FB/systemctl"   # smartd is off
PATH="$FB:$PATH" bash "$BOOT" --check > "$T/check-have" 2>&1
check "--check says neither when both are installed and smartd is off" \
    '! grep -qE "package (hdparm|smartmontools)|disable    smartd" "$T/check-have"'

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
