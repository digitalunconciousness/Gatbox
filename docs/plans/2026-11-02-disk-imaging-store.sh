#!/usr/bin/env bash
# Prepare the disk imaging store (docs/plans/2026-11-02-disk-imaging.md §5): ERASES the drive you name, makes one ext4
# partition on it, marks it as the image store, and writes its UUID to /etc/gatbox/disks.conf. Run it yourself, as root, at
# the Pi, with only the drives you mean plugged in. The bootstrap then writes and starts the mount unit from that UUID.
#
#   lsblk -d -o NAME,SIZE,MODEL,SERIAL,TRAN                      # find the drive; type its letter by hand below
#   sudo bash docs/plans/2026-11-02-disk-imaging-store.sh /dev/sdX
#   sudo bash bootstrap/gatbox-bootstrap.sh                      # afterwards
#
# Nothing is changed until you have typed the drive's serial number and the word ERASE, and these are refused outright:
# anything not a whole /dev/sd? disk; anything not behind USB (the Pi's own SD card, an NVMe drive); a drive with any
# partition mounted; a drive under 1 TB (the 80 GB donor on the dock is not the store); a drive that reports no serial
# number to confirm it by; and a Pi on which the bootstrap has not yet made the gatbox-disk user and group.
set -u

MIN_BYTES=${GATBOX_STORE_MIN_BYTES:-1000000000000}          # the store is the 4 TB drive; a test lowers this
CONF=${GATBOX_DISKS_CONF:-/etc/gatbox/disks.conf}
MARKER=.gatbox-image-store                                 # gatboxlib/disks.py looks for this file
die() { echo "refused: $*" >&2; exit 2; }
say() { printf '%s\n' "$*"; }
run() { printf '  $ %s\n' "$*"; "$@" || { echo "failed: $*" >&2; exit 1; }; }      # every change is shown, and stops the job if it fails
is_block() { [ "$(stat -c %F -- "$1" 2>/dev/null)" = "block special file" ]; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo bash $0 /dev/sdX"
[ $# -eq 1 ] || die "give the drive, typed by hand: sudo bash $0 /dev/sdX   (find it with: lsblk -d -o NAME,SIZE,MODEL,SERIAL,TRAN)"
dev=$1
[[ $dev =~ ^/dev/sd[a-z]+$ ]] || die "$dev is not a whole disk named /dev/sdX (a partition, a by-id path, an SD card and an NVMe drive are all refused)"
is_block "$dev" || die "$dev is not a block device"

tran=$(lsblk -dno TRAN "$dev" 2>/dev/null | tr -d ' ')
[ "$tran" = usb ] || die "$dev is not behind USB (transport: ${tran:-unknown}); the store is an external drive"
if [ -n "$(lsblk -nro MOUNTPOINTS "$dev" 2>/dev/null | tr -d '[:space:]')" ]; then
    die "something on $dev is mounted ($(lsblk -nro MOUNTPOINTS "$dev" | tr '\n' ' ')): unmount it, and check this is the right drive"
fi
bytes=$(lsblk -dnbo SIZE "$dev" 2>/dev/null | tr -d ' ')
[[ ${bytes:-} =~ ^[0-9]+$ ]] || die "could not read the size of $dev"
[ "$bytes" -ge "$MIN_BYTES" ] || die "$dev holds $bytes bytes: the store needs at least $MIN_BYTES (it is the 4 TB drive). Is this the dock's drive?"
model=$(lsblk -dno MODEL "$dev" 2>/dev/null | sed 's/ *$//')
serial=$(lsblk -dno SERIAL "$dev" 2>/dev/null | tr -d ' ')
[ -n "$serial" ] || die "$dev reports no serial number, so it cannot be confirmed by one"
getent passwd gatbox-disk >/dev/null && getent group gatbox-disk-queue >/dev/null \
    || die "the gatbox-disk user and gatbox-disk-queue group do not exist yet: run  sudo bash bootstrap/gatbox-bootstrap.sh  first"
if [ -s "$CONF" ] && grep -q '^[[:space:]]*UUID=' "$CONF" 2>/dev/null; then
    say "note: $CONF already names a store ($(grep -m1 '^[[:space:]]*UUID=' "$CONF")). It is replaced by this drive's UUID."
fi

say
say "THIS WILL ERASE EVERYTHING ON:"
say "  device   $dev"
say "  model    ${model:-unknown}"
say "  serial   $serial"
say "  size     $bytes bytes"
say "  on it now:"
lsblk -f "$dev" 2>/dev/null | sed 's/^/    /'
say
printf 'To confirm this is the drive, type its serial number exactly: '
read -r typed
[ "$typed" = "$serial" ] || die "that is not the serial number ($serial). Nothing was changed"
printf 'Type ERASE to erase %s: ' "$dev"
read -r word
[ "$word" = ERASE ] || die "not ERASE. Nothing was changed"

say
say "erasing and formatting $dev ..."
part=${dev}1
run wipefs -a "$dev"
printf 'label: gpt\n,,L\n' | run sfdisk --wipe always --wipe-partitions always "$dev"
run udevadm settle
for _ in 1 2 3 4 5 6 7 8 9 10; do is_block "$part" && break; sleep 1; done
is_block "$part" || { echo "failed: the partition $part did not appear" >&2; exit 1; }
# One inode per 4 MiB (the images are huge files), and no space kept back for root: this is a data drive, not a system disk.
run mkfs.ext4 -F -L gatbox-images -m 0 -T largefile4 "$part"
uuid=$(blkid -p -s UUID -o value "$part")
[[ $uuid =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || { echo "failed: no filesystem UUID on $part" >&2; exit 1; }

# The marker says "this is the store" (the job refuses to write anywhere else), and the drive's root belongs to the disk job's
# queue group, setgid, so everything written under it can be read by gatbox-web and by you.
mnt=$(mktemp -d /run/gatbox-store.XXXXXX) || exit 1
run mount -o nodev,nosuid,noexec "$part" "$mnt"
printf 'GATBOX image store\n' > "$mnt/$MARKER"
run chown gatbox-disk:gatbox-disk-queue "$mnt" "$mnt/$MARKER"
run chmod 2770 "$mnt"
run chmod 0660 "$mnt/$MARKER"
run sync
run umount "$mnt"
rmdir "$mnt"

tmp=$(mktemp "$CONF.XXXXXX") || exit 1
printf '# The image store drive (written by 2026-11-02-disk-imaging-store.sh). The bootstrap mounts the drive with this UUID.\nUUID=%s\n' "$uuid" > "$tmp"
chmod 0600 "$tmp"
mv "$tmp" "$CONF"

say
say "done. The store is $part, UUID $uuid, written to $CONF."
say "Next:  sudo bash bootstrap/gatbox-bootstrap.sh   (it writes and starts srv-gatbox-disks.mount), then the DISK tab says IMAGE STORE CONNECTED."
