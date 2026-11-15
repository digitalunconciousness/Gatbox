#!/usr/bin/env bash
# docs/plans/2026-11-02-disk-imaging-store.sh, the script that ERASES the image store's drive. It is run on a stand-in machine:
# lsblk, stat, getent, id and every command that changes a drive are replaced by stubs that read their facts from variables
# and log what they were asked to do, so what is tested is the one thing that matters: nothing destructive runs unless every
# check passes and the serial and ERASE were typed. No real device is ever opened.
#   bash tests/test-disk-store.sh [path/to/2026-11-02-disk-imaging-store.sh]
set -u
STORE=$(readlink -f "${1:-$(dirname "$0")/../docs/plans/2026-11-02-disk-imaging-store.sh}")
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
B=$T/bin; LOG=$T/log; mkdir -p "$B" "$T/run"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

# --- the stand-in machine ----------------------------------------------------------------------------------------------
cat > "$B/id" <<'EOF'
#!/bin/sh
[ "$1" = "-u" ] && { echo "${FAKE_UID:-0}"; exit 0; }
exec /usr/bin/id "$@"
EOF
cat > "$B/lsblk" <<'EOF'
#!/bin/sh
case "$*" in
    *TRAN*) echo "$FAKE_TRAN" ;;
    *MOUNTPOINTS*) printf '%s' "$FAKE_MOUNTS" ;;
    *SIZE*) echo "$FAKE_SIZE" ;;
    *MODEL*) echo "$FAKE_MODEL" ;;
    *SERIAL*) echo "$FAKE_SERIAL" ;;
    -f*) echo "NAME FSTYPE LABEL"; echo "sdb1 ext4 olddata" ;;
esac
EOF
cat > "$B/stat" <<'EOF'
#!/bin/sh
echo "block special file"
EOF
cat > "$B/getent" <<'EOF'
#!/bin/sh
[ -n "${FAKE_NO_USER:-}" ] && exit 2
exit 0
EOF
cat > "$B/blkid" <<'EOF'
#!/bin/sh
echo "${FAKE_UUID:-5210f177-0573-4cb3-9080-ab25f051f744}"
EOF
# mktemp: the store script asks for a directory under /run; the test gives it one of its own
cat > "$B/mktemp" <<EOF
#!/bin/bash
exec /usr/bin/mktemp "\${@/\\/run\\//$T/run/}"
EOF
# the commands that change a drive, or the system: logged, never run
for c in wipefs sfdisk udevadm mkfs.ext4 mount chown chmod sync; do
    cat > "$B/$c" <<EOF
#!/bin/sh
echo "$c \$*" >> "$LOG"
[ "$c" = sfdisk ] && cat >> "$LOG.sfdisk"
exit 0
EOF
done
cat > "$B/umount" <<EOF
#!/bin/sh
echo "umount \$*" >> "$LOG"
ls -A "\$1" >> "$LOG.mnt" 2>/dev/null
exit 0
EOF
chmod +x "$B"/*

fresh() {   # a good 4 TB external drive behind USB, nothing mounted
    : > "$LOG"; : > "$LOG.sfdisk"; : > "$LOG.mnt"; rm -f "$T/disks.conf"
    export FAKE_TRAN=usb FAKE_MOUNTS= FAKE_SIZE=4000787030016 FAKE_MODEL="My Passport 25E2" FAKE_SERIAL=WX12AA3456 FAKE_UID=0
    unset FAKE_NO_USER
}
go() {      # go <stdin> <args...>: the script on the stand-in machine
    local input=$1; shift
    printf '%b' "$input" | env PATH="$B:$PATH" GATBOX_DISKS_CONF="$T/disks.conf" bash "$STORE" "$@" > "$T/out" 2>&1
}
changed() { grep -qE '^(wipefs|sfdisk|mkfs|mount|chown|umount) ' "$LOG"; }

check "bash -n"                                     'bash -n "$STORE"'

# --- refused before anything is asked --------------------------------------------------------------------------------------
fresh; go "WX12AA3456\nERASE\n"; rc=$?
check "no argument: refused (exit 2), nothing changed"  '[ $rc = 2 ] && ! changed && grep -q "give the drive" "$T/out"'
for bad in sdb /dev/sdb1 /dev/mmcblk0 /dev/nvme0n1 /dev/disk/by-id/usb-X /dev/sdb2 "/dev/sdb; rm -rf /"; do
    fresh; go "WX12AA3456\nERASE\n" "$bad"; rc=$?
    check "'$bad' is not a whole /dev/sdX disk: refused (exit 2), nothing changed" '[ $rc = 2 ] && ! changed && grep -q "refused" "$T/out"'
done
fresh; export FAKE_UID=1000; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "not root: refused, nothing changed"           '[ $rc = 2 ] && ! changed && grep -q "run as root" "$T/out"'
fresh; export FAKE_TRAN=sata; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "not behind USB (the Pi's own drive, an NVMe): refused" '[ $rc = 2 ] && ! changed && grep -q "not behind USB" "$T/out"'
fresh; export FAKE_MOUNTS="/media/user/data"; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "something on it is mounted: refused"           '[ $rc = 2 ] && ! changed && grep -q "is mounted" "$T/out"'
fresh; export FAKE_SIZE=80000000000 FAKE_MODEL="WDC WD800JD-75MSA1" FAKE_SERIAL=WD-WIDGET0001; go "WD-WIDGET0001\nERASE\n" /dev/sdb; rc=$?
check "the 80 GB donor on the dock is not the 4 TB store: refused even with the right serial typed" \
      '[ $rc = 2 ] && ! changed && grep -q "needs at least" "$T/out"'
fresh; export FAKE_SERIAL=; go "\nERASE\n" /dev/sdb; rc=$?
check "a drive that reports no serial cannot be confirmed: refused" '[ $rc = 2 ] && ! changed && grep -q "no serial number" "$T/out"'
fresh; export FAKE_NO_USER=1; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "the gatbox-disk user and group missing (the bootstrap has not run): refused" '[ $rc = 2 ] && ! changed && grep -q "bootstrap" "$T/out"'

# --- the confirmation --------------------------------------------------------------------------------------------------------
fresh; go "WX12AA3456\nerase\n" /dev/sdb; rc=$?
check "the serial right and 'erase' in lower case: refused, nothing changed" '[ $rc = 2 ] && ! changed && [ ! -e "$T/disks.conf" ]'
fresh; go "wx12aa3456\nERASE\n" /dev/sdb; rc=$?
check "the serial in the wrong case: refused, nothing changed" '[ $rc = 2 ] && ! changed'
fresh; go "\n\n" /dev/sdb; rc=$?
check "nothing typed: refused, nothing changed"       '[ $rc = 2 ] && ! changed'
fresh; go "My Passport 25E2\nERASE\n" /dev/sdb; rc=$?
check "the model typed instead of the serial: refused" '[ $rc = 2 ] && ! changed'
fresh; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "it shows the drive before asking: device, model, serial, size, and what is on it" \
      'grep -q "device   /dev/sdb" "$T/out" && grep -q "model    My Passport 25E2" "$T/out" && grep -q "serial   WX12AA3456" "$T/out" && grep -q "4000787030016 bytes" "$T/out" && grep -q "olddata" "$T/out"'

# --- the good path -----------------------------------------------------------------------------------------------------------
check "the right drive, its serial and ERASE: done (exit 0)" '[ $rc = 0 ] && grep -q "^done" "$T/out"'
check "the drive is wiped, partitioned as GPT with one Linux partition, then formatted: in that order, on the right nodes" \
      '[ "$(grep -nE "^(wipefs|sfdisk|mkfs)" "$LOG" | cut -d: -f2 | cut -d" " -f1 | tr "\n" " ")" = "wipefs sfdisk mkfs.ext4 " ] \
       && grep -qx "wipefs -a /dev/sdb" "$LOG" && grep -q "^sfdisk --wipe always --wipe-partitions always /dev/sdb" "$LOG" \
       && grep -q "^mkfs.ext4 -F -L gatbox-images -m 0 -T largefile4 /dev/sdb1" "$LOG"'
check "the partition table is GPT with one partition filling the drive" 'grep -qx "label: gpt" "$LOG.sfdisk" && grep -qx ",,L" "$LOG.sfdisk"'
check "nothing is run against the whole drive after the format except the partition table (no second wipe, no mkfs on it)" \
      '[ "$(grep -c "^wipefs" "$LOG")" = 1 ] && ! grep -qE "^mkfs.* /dev/sdb( |$)" "$LOG"'
check "the store is mounted nodev,nosuid,noexec for the marking, marked, handed to the queue group setgid, synced and unmounted" \
      'grep -q "^mount -o nodev,nosuid,noexec /dev/sdb1 " "$LOG" && grep -q "^chown gatbox-disk:gatbox-disk-queue " "$LOG" \
       && grep -q "^chmod 2770 " "$LOG" && grep -q "^sync" "$LOG" && grep -q "^umount " "$LOG" && grep -qx ".gatbox-image-store" "$LOG.mnt"'
check "disks.conf holds the new filesystem UUID" 'grep -qx "UUID=5210f177-0573-4cb3-9080-ab25f051f744" "$T/disks.conf"'
check "disks.conf is made 600 and replaced in one move" 'grep -q "^chmod 0600 " "$LOG"'
check "it says what to do next (the bootstrap)" 'grep -q "bootstrap/gatbox-bootstrap.sh" "$T/out"'
fresh; echo "UUID=00000000-0000-0000-0000-000000000000" > "$T/disks.conf"; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "an older store in disks.conf is named before it is replaced" '[ $rc = 0 ] && grep -q "already names a store" "$T/out" && grep -qx "UUID=5210f177-0573-4cb3-9080-ab25f051f744" "$T/disks.conf"'
fresh; export FAKE_UUID=not-a-uuid; go "WX12AA3456\nERASE\n" /dev/sdb; rc=$?
check "a format that leaves no UUID stops before anything is written to disks.conf" '[ $rc = 1 ] && [ ! -e "$T/disks.conf" ] && grep -q "no filesystem UUID" "$T/out"'

echo "disk store: $pass passed, $fail failed"
[ "$fail" = 0 ]
