#!/usr/bin/env bash
# Task 0 for disk imaging (docs/plans/2026-11-02-disk-imaging.md §3 and §10): the facts read off the Pi before any
# code relies on them (hard rule 12). READ-ONLY: it mounts nothing, writes to no device, changes no setting and saves
# no file. It prints; paste the output back. sudo is asked for once, for smartctl, hdparm, blkid and dmesg.
#
# Before running it:
#   - the Pi on its wall supply;
#   - the dock (with its own PSU on) holding a spare IDE drive and a SATA SSD. NOT the CarnEvil Quantum or any drive
#     whose spin-ups matter: IDENTIFY spins a drive up. If the dock shows one drive at a time, run this once per drive;
#   - the 4 TB drive plugged in;
#   - the CF reader out for now (it comes back for step 9's card test, or use a CF card on the dock's CF-to-IDE adapter).
# Run, from ~/gatbox after `git fetch origin disk-imaging`:
#   bash <(git show origin/disk-imaging:docs/plans/2026-11-02-disk-imaging-task0.sh)
#   bash <(git show origin/disk-imaging:docs/plans/2026-11-02-disk-imaging-task0.sh) /path/to/a.chd   # + chdman info
set -u
CHD=${1:-}

h() { printf '\n===== %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

h "0. the Pi"
date -u '+%Y-%m-%dT%H:%M:%SZ'
tr -d '\0' </proc/device-tree/model; echo
uname -r
grep -m1 PRETTY_NAME /etc/os-release
sudo -v || { echo "sudo is needed for steps 3 and 5"; }

h "1. USB: devices, and the tree (Driver=uas or usb-storage, and which port each is on)"
lsusb
echo
lsusb -t

h "2. block devices"
lsblk -dpo NAME,SIZE,MODEL,SERIAL,TRAN,LOG-SEC,PHY-SEC,ROTA,RM,TYPE
echo
ls -l /dev/disk/by-id/

h "3. each USB disk"
found=0
for d in /dev/sd?; do
    [ -b "$d" ] || continue
    found=1
    n=${d#/dev/}
    s=/sys/block/$n
    printf '\n----- %s\n' "$d"
    echo "sysfs: bytes=$(( $(cat "$s/size") * 512 )) logical=$(cat "$s/queue/logical_block_size")" \
         "physical=$(cat "$s/queue/physical_block_size") rotational=$(cat "$s/queue/rotational")" \
         "removable=$(cat "$s/removable")"
    echo "INQUIRY: vendor=$(cat "$s/device/vendor" 2>/dev/null) model=$(cat "$s/device/model" 2>/dev/null)" \
         "rev=$(cat "$s/device/rev" 2>/dev/null)"
    echo "-- udevadm info"
    udevadm info -q property -n "$d"
    echo "-- lsblk -f (filesystems, labels, mount points; nothing is mounted by this script)"
    lsblk -fp "$d"
    echo "-- blkid -p (a low-level, read-only probe)"
    sudo blkid -p "$d" || echo "(no signatures, or blkid found none)"
    if have smartctl; then
        echo "-- smartctl -i -d sat"
        sudo smartctl -i -d sat "$d"
    else
        echo "-- smartctl: not installed"
    fi
    if have hdparm; then
        echo "-- hdparm -I (first 30 lines: model, serial, and the default CHS under Configuration)"
        sudo hdparm -I "$d" 2>&1 | head -n 30
    else
        echo "-- hdparm: not installed"
    fi
done
[ "$found" = 1 ] || echo "no /dev/sd? devices: is the dock on and its drive powered?"

h "4. power: throttling with everything plugged in and spinning, and what each device asks for"
if have vcgencmd; then vcgencmd get_throttled; else echo "vcgencmd not found"; fi
lsusb -v 2>/dev/null | grep -E '^Bus [0-9]+ Device [0-9]+|MaxPower'

h "5. the kernel's view of the USB storage (UAS, resets, over-current, under-voltage)"
sudo dmesg | grep -iE 'uas|usb-storage|over-?current|under-?volt|reset (high|super)speed' | tail -n 40

h "6. free space"
df -h / /srv

h "7. packages"
apt policy gddrescue smartmontools hdparm mame-tools 2>/dev/null

h "8. chdman"
if have chdman; then
    chdman 2>&1 | head -n 2
    if [ -n "$CHD" ]; then chdman info -i "$CHD"; else echo "(no CHD given: pass one as the first argument)"; fi
else
    echo "chdman not installed here (mame-tools). If a CHD is on the laptop, run there:"
    echo "  chdman 2>&1 | head -n 2; chdman info -i <file>.chd"
fi

h "9. what the desktop does with an inserted card"
echo "udisks2: $(systemctl is-active udisks2 2>/dev/null)"
grep -sH -iE 'mount' "$HOME"/.config/pcmanfm/*/pcmanfm.conf /etc/xdg/pcmanfm/*/pcmanfm.conf || echo "(no pcmanfm mount settings found)"
before=$(findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS)
echo
echo "Now put a FAT-formatted card you don't need into the reader (or a CF card on the dock's adapter),"
printf 'wait 10 seconds, then press Enter (or type s and Enter to skip): '
ans=s
read -r ans </dev/tty 2>/dev/null || echo "(no terminal: skipped)"
if [ "$ans" != s ]; then
    after=$(findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS)
    echo "-- mounts that appeared:"
    diff <(echo "$before") <(echo "$after") | grep '^>' || echo "(none: the desktop did not auto-mount it)"
    echo "If it was mounted, eject it in the file manager before you pull it out."
fi

h "done: paste everything above back into the session"
