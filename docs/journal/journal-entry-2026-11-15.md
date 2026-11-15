## 2026-11-15: The disk job installed; what the first install showed; the four DISK buttons
- Task 4 ran on the Pi on 11-14: the bootstrap installed the disk job (user, groups, spool, udev rule, units, the
  `gatbox-disk` tool, `gddrescue`), the 4 TB drive was erased and formatted as the image store with the guarded script
  (`docs/plans/2026-11-02-disk-imaging-store.sh`), and `srv-gatbox-disks.mount` mounts it by UUID. `/api/disk` lists the
  store and the 80 GB drive on the dock. No READ or WRITE has been run from the 7″ on real media yet.
- Three things the install showed, each a mistake in the bootstrap or the library, each fixed and merged:
  - The `gatbox-disk` udev group did not apply. The bootstrap loaded the rule before sysusers made the group, and the
    reload I added after it did nothing; the group only took after `systemd-udevd` was restarted. Why a reload is not
    enough was not established. Step 6 now restarts it, and a re-run mends a Pi where a USB disk is still outside the group.
  - smartmontools' postinst started smartd, whose SMART ENABLE hung the dock's bridge until the kernel reset it, twice.
    The bootstrap now holds a temporary `policy-rc.d` that refuses to start that one unit while apt installs it.
  - gatbox-sync's library push returned HTTP 500 `attempt to write a readonly database`. `library.sqlite` was owned by the
    owner's account at mode 0644, so gatbox-web, another account in the group, could read it and not write it. Nothing
    had ever set a mode. The library now makes its file group-writable, and a tmpfiles line mends the files that exist.
  A fourth, smaller one: `--check` without root said "create" for files under `/etc/gatbox` that exist (the folder is 0750
  root). It now says it could not look.
- What the dock does, measured on the Pi: `hdparm -I` works through it (the drive's own sector count, 156250000, matches
  the kernel's, so nothing is cut short). `hdparm -N` hangs it for about 16 seconds until the kernel resets the USB device,
  the same as smartd's SMART ENABLE. The IS611 bridge answers IDENTIFY and hangs on some other ATA pass-through commands.
  So nothing in the deck sends those to a drive behind it, and whether the 80 GB drive hides area above its visible size
  cannot be read through this dock. The owner's own bench procedure says SMART and `hdparm -I` "may not pass through the
  bridge", which fits.
- The four buttons from the owner's bench procedure (rev 1, 2026-11-12), none of which sends anything to a drive:
  DRIVE INFO (what udev kept from the drive's own IDENTIFY, plus sysfs), SWAP (the procedure's steps for a change of drive,
  ticking themselves from the live drive list), RECORDS (her Appendix B, filled in from the sidecar, `writes.jsonl` and the
  cab checklist) and the CF adapter's +5 V only warning. The first version of SWAP took any drive already on the dock for
  the new one; the browser test caught it, and it now remembers what was on the list when the swap began.
- Not done: a first READ and a first WRITE from the 7″ on real media; the hidden-area question; the cab-side checks in
  the procedure's 5.4 are the owner's. Task 3 (the MAME check on an image) still waits on a `chdman` output. The deck
  does not record the device letter, the two hash passes with their times, or a write's time and speed, so those lines of
  Appendix B stay blank on the record.
- Owner, next: run the bootstrap from main (`--check` first, then the real run, with the dock idle), then the first READ of
  the 80 GB drive from the 7″.
