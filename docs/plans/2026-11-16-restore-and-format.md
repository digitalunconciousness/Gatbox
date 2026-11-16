# RESTORE and FORMAT (2026-11-16)

Both are disk writes, so both fall under hard rule 6: armed at the Pi (the 3 s hold on the 7″), checked first, verified, and
logged. The owner asked for both on 2026-11-16 and answered the design questions the same day.

## The owner's decisions

1. FORMAT filesystems: FAT32, exFAT and ext4.
2. FORMAT is quick only: a partition table and a filesystem, seconds. No zero-fill.
3. FORMAT of a drive the image store holds no image of: warn, and offer to image it first. It is not a refusal.
4. FORMAT partition table: MBR up to 2 TB, GPT above.
5. RESTORE: approved as designed below, with the rule 6 wording recorded in `CLAUDE.md`.

## RESTORE TO THE ORIGINAL (built, not installed)

Putting an image back on the drive it came from undoes what has been done to the drive since the read. The plan's TARGET CHECK
refused that on purpose (§4.2 item 4, the guard from the 2026-11-02 hand job, where the dock gave two drives the same name). The
guard stays for a plain TARGET CHECK. RESTORE is a separate, deliberate check that lifts that one refusal and nothing else.

What RESTORE CHECK requires:
- **The drive is the image's own, or not positively another.** Both sides have a serial: the serials must match, or it is refused
  ("its serial is different"). No serial on either side: the make and model must match and the size must equal the source's.
  The check says how it matched (`restore_proof`: the serial, or the make, model and size because the drive has no serial).
  A drive that is not the image's own is still written with WRITE, as before.
- **The image is a complete read.** No unread areas (the sidecar's ddrescue summary says `complete`), and a card or other flash
  image was read twice with both reads matching. A hard disk is read once by design, so a clean single pass is the bar for it.
- **The overwrite is always acknowledged**, even when the drive looks blank: the original is being replaced. The 7″ says so in words
  (`RESTORE. <drive> is the drive this image came from. Writing puts the image from <date> back and replaces everything on it now`),
  and the acknowledgement button reads OVERWRITE THE ORIGINAL: I'M SURE.
- **Everything else is WRITE's:** the check no older than 5 minutes, the 3 s hold on the Pi's own screen (30 s arm), the image checked
  against its own SHA-1 before the first byte, the identity re-checked after the claim, the exclusive open, the two read-backs.
- **A drive that already holds the image** offers VERIFY ONLY, as for any write.
- **The log:** the line in `<store>/writes.jsonl` and the job record's "WRITTEN TO A DRIVE" section say `restore`, with the proof.

CLI: `gatbox-disk check -i IMAGE -d DEV --restore`, and `write ... --restore --overwrite` (the model typed, as for any terminal
write). Dashboard: RESTORE CHECK beside TARGET CHECK in WRITE's step 3.

Not done, said plainly: nothing here has run on a real drive, and a restore of a hard disk is hours at USB 2 speed.

## FORMAT (built 2026-11-16, not installed)

A quick format of a target drive, from the 7″ (DISK → FORMAT) or the terminal (`gatbox-disk format-check` / `format`).

What the Pi had (the owner's paste, 2026-11-16): `mkfs.vfat`, `mkfs.exfat`, `mkfs.ext4`, `sfdisk` and `wipefs` are all installed
(`mkfs.fat` 4.2, exfatprogs 1.2.9); `mkfs.fat` has `--offset` and `mkfs.exfat` has not.

**How it writes.** The disk job can open whole USB disks and nothing else: partition nodes are root's, and it has no capability to
make the kernel read a partition table again. So no mkfs ever sees the drive, and the unit does not change:
1. The filesystem is made in a sparse scratch file on the store (`_format/`, the partition's size, removed afterwards) and checked
   there with its own fsck (`fsck.fat -n`, `fsck.exfat -n`, `e2fsck -fn`). A mkfs or an fsck that fails stops the format before the
   drive is touched.
2. The drive is opened with `O_EXCL` and identified again, as for a write.
3. The drive's first MiB is zeroed and synced, so the old table is gone before anything else.
4. The scratch file's data is copied to the partition, with the partition's first and last MiB written whole (zeros included) so no
   older filesystem's signature survives at either end; the drive's last MiB is zeroed (a GPT's copy goes there); the first MiB,
   with the new table, is written last. A format that stops partway leaves a drive with no table.
5. Everything written is read back twice (past the cache) and compared, then the drive is parsed again: one partition, where it
   should be, with the filesystem and label asked for.
6. It reports whether the kernel's partition list shows the new partition. It does not assume so: when it does not, the 7″ says to
   switch the dock off and on.

**The layout.** MBR while the drive's sectors fit 32 bits (under 2 TiB), GPT above (protective MBR, both headers, 128 entries). One
partition from 1 MiB to the last whole MiB that leaves at least 1 MiB free at the end. FAT32 is refused past 2 TB. A partition under
33 MiB cannot be FAT32 (too few clusters): it gets the FAT that fits (FAT16 or FAT12), and the check says so before the hold.

**ext4, found while building.** On a file, `mke2fs` records what it zeroes as holes, and takes the file's holes for a drive that
discards to zeros. A copy of the data alone would then leave the old drive's bytes in the journal and flag the inode tables as
already zeroed. It is run with `-E nodiscard,lazy_itable_init=1,lazy_journal_init=0` and `UNIX_IO_NOZEROOUT=1`, which makes it
write its zeros as it would on a drive with neither feature; the tool then checks that the journal really is data in the scratch
file, and refuses ext4 if it is not. The test cuts the partition out of a drive full of old bytes and runs `e2fsck` on it.

**The check and the image.** FORMAT CHECK reads what is on the drive and looks through the store's sidecars: an image read from
this drive (by serial; with no serial, by make, model and size), and whether the drive still starts with that image's bytes (the
first 64 MiB, or the whole image when it is smaller). No image whose bytes the drive still starts with means the warning, in
capitals, and IMAGE IT FIRST, which opens READ with the drive picked. It is a warning and never a refusal (decision 3). It compares
64 MiB, not the whole drive, and says so.

**Refusals, each leaving the drive as it was:** the store, the system disk, a mounted drive, an empty slot; sectors that are not
512 bytes; under 16 MiB; a label that is not up to 11 of A-Z, 0-9, `_` and `-`; not blank without the acknowledgement; no passing
FORMAT CHECK of this drive in the last 5 minutes; an arm older than 30 s; another drive under the same name; contents that changed
since the check; under 2 GB free on the store.

**The log.** One line per format in `<store>/writes.jsonl` (`op: format`, verified or stopped, the filesystem, the table, the label,
what was on the drive, whether the store had an image, whether anything was written, the arm). The 7″ lists the latest six. A format
is not on any image's job record: it is not about an image.

**Not done, said plainly:** nothing here has run on a real drive. Whether udev makes the kernel re-read the table when the job
closes the drive is not known for this Pi; the result says what it found. `mkfs.fat` 4.2 and exfatprogs 1.2.9 are the Pi's versions;
the tests ran on 4.2 and 1.2.2. ext4's root folder belongs to root, as `mkfs.ext4` makes it.
