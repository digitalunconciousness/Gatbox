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
- The first real READ, from the terminal: the 80 GB WD800JD, one pass, 100% rescued, no bad areas, 77 minutes (45 to read at
  about 30 MB/s, 32 to hash it back off the store), no kernel messages, no throttling. The drive is a Linux system disk: a
  bootable ext4 partition and a swap, behind a DOS partition table.
- A second READ of the same drive, started from the 7″ for the machine "simpsons", showed what the first one hid: the bar sat at
  5% through the whole ddrescue pass and at 80% through the whole hash, 45 and 32 minutes of a 7″ that looked hung. `gatbox-disk`
  set a percentage when a step began and said nothing more until the next one. It now reports where it is: during the first
  ddrescue pass, the bytes ddrescue has read (`rchar` in `/proc/PID/io`, because ddrescue saves its map only every five minutes), and
  during a hash, the bytes hashed, each at most every five seconds and as a line under the bar ("55.4 GB of 80.0 GB read from the
  drive"). A retry only says it is still going. ddrescue's own output now goes to unlinked files beside the image, not a pipe
  nobody reads. Not changed: the same gap exists in WRITE (the pre-hash, the write loop, the two read-backs), which is the
  audited path and was not part of what was reported. Also not changed, and the owner's call: the duplicate check looks only in
  the image's own machine folder, so that second read stores a second 75 GB copy beside the one under `unassigned`.
- What to do with the images: the owner wants to browse them, see photos and documents, and search, for her non-arcade drives
  too. `docs/plans/2026-11-15-drive-browser.md` is the design, OK'd with all seven decisions as recommended: read-only through
  sleuthkit with nothing mounted, in a sandboxed service with no network, on the Pi's own screen only, an index on the store that
  can be deleted without loss. Checking it showed that nothing reads `archive-queue.jsonl`, so no disk image has been sent
  anywhere. A real filesystem UUID from one of her drives had also ended up in two tests; it is replaced by an invented one.
  F0, the facts script and the packages, was built, and the owner ran the bootstrap and the script on the Pi the same evening: a full
  listing of the 80 GB drive's ext4 volume (200,331 entries) took 33.5 s using 19 MB, and the full-text index will take about 76 MB per
  million files. Reading files out of the image is the slow part (about 6 MB/s on a large file), so previews load one at a time,
  thumbnails are background work, and text search starts with a measurement. The drive's "photos" are system icons: a real archive
  drive is still needed to try the browser on.
- F1 of the browser, built the same evening and not installed: FILES on the DISK tab, with `gatbox-browse`, a socket-activated
  service in its own sandbox (no network, no devices, the store read-only, 512 MB, 64 tasks), and six loopback routes behind it.
  A folder is named by its inode and every row carries its own, so nothing builds a path. What came out of building it:
  killing a hung `fls` did not free the pipe, because a grandchild (`sleep` in the test) held it, so every tool runs in its own
  session and the whole group is killed; trimming the extraction cache under a small cap deleted the file just extracted; an
  empty inode passed validation and read the root; and I had written "streamed, never held whole" about a folder in the
  note, when sorting needs the whole folder in memory (it is cut at 100,000 entries and says so). The unit's sandbox has not run
  on the Pi: the install step has read-only checks for that.
- Not done: a first READ from the 7″ and any WRITE on real media; the hidden-area question; the cab-side checks in
  the procedure's 5.4 are the owner's. Task 3 (the MAME check on an image) still waits on a `chdman` output. The deck
  does not record the device letter, the two hash passes with their times, or a write's time and speed, so those lines of
  Appendix B stay blank on the record.
- Owner, next: run the bootstrap from main (`--check` first, then the real run, with the dock idle), then the first READ of
  the 80 GB drive from the 7″.
