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

## FORMAT (decided, not built)

A quick format of a target drive, from the 7″. Shape, mirroring WRITE:
- A FORMAT step on the DISK tab, with WRITE's refusals (never the store, the system disk, a mounted drive, or one that fails the
  identity check), a check no older than 5 minutes, a hold naming the drive and the filesystem, and an identity re-check before the first byte.
- The check shows what is on the drive now, and whether the store holds an image of it. When it does not, it warns and offers IMAGE FIRST
  (a READ of that drive into the store); the owner can still go on.
- After the format it re-reads the partition table and the filesystem and checks them (`blkid`, plus an fsck check where the filesystem has one).
- One line per format in `writes.jsonl` (`op: format`, the filesystem, the label, what was on the drive before, the result, when it was
  armed), and a row in the job record.

What has to be read on the Pi before any code (rule 12), and decided with the owner if it needs system config (rule 5):
- Which of `mkfs.vfat`, `mkfs.exfat`, `mkfs.ext4`, `sfdisk` and `wipefs` are installed, and their versions.
- Whether the disk job's sandbox can write a partition table and a filesystem without root. The partition device nodes are `root:disk`
  and the job's user is not in `disk`, and re-reading a partition table needs a capability the unit does not have. A way that needs no
  kernel re-read is preferred: write the table's bytes through the exclusive descriptor, and make the filesystem with the tool's offset option.
- Whether any package has to be added to the bootstrap (apt before pip).
