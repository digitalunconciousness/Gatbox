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
- F2 of the browser, the name index and search, built on top of F1 and not installed. One SQLite file with an FTS5 table in
  `_browse/` on the store, keyed by the image's SHA-1 and safe to delete. A volume is indexed in one `fls -r` pass and becomes
  searchable only when the pass is whole; a stop, a crash or a restart leaves nothing a search can see, and the pass stands still
  while a disk job runs. The search says how many of the store's images it looked at, so an empty answer is not read as "not
  there". Three things the tests showed me: the browser starts its tools with a scrubbed environment, so a stand-in `fls` has to
  be steered by files beside it, not by environment variables (the first "slow" test was only slow by accident); my
  "a stopped pass leaves no rows" test passed for the wrong reason, because the rows it counted belonged to another image and the
  pass had not written any yet, which a mutation check caught; and asking for the index's state created the index file and started
  a worker, which now does neither unless there is work. And on the screen, SEARCH showed the results from before an image was
  removed from the index until a new search was typed.
- F3a of the browser, the text readers and the measurement, built and not installed. The readers pull text out of plain files,
  HTML, PDFs, the zipped Office, OpenDocument and ePub formats and, through catdoc (a new package in the bootstrap), the old
  Word, Excel and PowerPoint ones. They are written to refuse: no XML parser, a limit on parts and on how far a zip may expand,
  a password-protected part refused, every program through the one runner with a time limit and the whole process group killed. A
  file that cannot be read says why in one of seven words, so a search that finds nothing can say how much was not read. The
  measurement script is the F3 step the note asked for before promising an indexing time: it reads 200 random documents out of an
  image three times, in random, inode and random order again, and projects the time for the whole volume. The same lesson as in F1
  came back in it: a timed-out `icat` held the pipe through a grandchild, so the script kills the whole group, and a test with a
  stand-in that sleeps found it by hanging. Two of my first tests were also wrong: a zip's "password" bit is reset by Python's own
  writer, so the test sets it in the bytes, and a check for "too many parts" passed on a zip that was refused for having no
  document part. F3b, the index and the screen, waits for her numbers.
- Her F3 report came back the same evening and changed the design: reading a document out of the image is about 20 ms and
  extracting it 3 ms, and none of 600 reads took a second, so F0's one 3-second file did not come back. Inode order was 15%
  faster against 6% noise between two identical runs, so the text pass sorts by it and builds nothing around it. The script's first
  version printed "0.0 h" for 744 documents (right and useless) and, by chance, tried no PDF; it now prints readable times, reads
  the largest documents for a big-file rate and tries every kind.
- F3b of the browser, the text search, built on that and not installed. Text is a second phase after the names, one document at a
  time in inode order, each committed as it is read, so nothing is lost by a stop or a crash; a short job starts on its own and a
  long one waits for a tap, with the estimate shown. Four things the tests showed me: looking at the index took the write lock
  (every connection ran the schema script), which kept a search from opening while the worker wrote, so a connection now makes the
  schema only for a new file; committing every twenty documents would have held a transaction open across slow ones and kept the
  screen's own stop waiting, so each document is committed; a file of random bytes was read as Windows-1252 text about one time in
  twenty (it passed my test most runs), so a NUL byte now makes a file binary; and a dashboard check that waited for "some image
  says NOT INDEXED" was true from the start, because the other images were never indexed, and it hid a race until the removal got
  slower.
- F4, asked for the same evening: it is six features, four of them behind something she has held back on purpose, so I wrote
  `docs/plans/2026-11-15-drive-browser-f4.md` (deleted files, HEIC, OCR, duplicates, copy out, a phone with a PIN, each with what
  it would cost, what could go wrong and what I recommend, and six questions) and built only what the first design had already
  promised and I had left out twice: photo thumbnails. They are a JPEG of 192 pixels made once and kept on the store, with a grid
  under the PHOTOS filter and the viewer stepping through a folder's photos. Facts checked on Debian's package pages: Debian 13
  has no `python3-pillow-heif`, so HEIC would go through `libheif-examples`' `heif-convert` (or a venv, her decision), and
  `tesseract-ocr` 5.5.0 is there for arm64. Her re-run of the F3 script: big documents read at 11.2 MB/s, seven of eight PDFs read
  at a median 26 ms, and the eighth is a scan.
- She answered the F4 questions (yes to deleted files, possible duplicates, COPY TO THE STORE and no phone; no HEIC photos to look at; OCR
  waits). F4b is built: a DELETED toggle on the folder view. On images where files were really deleted, a deleted FAT file reads back
  byte for byte, until something is written over its space; a cleared ext4 inode reads back empty; FAT keeps no first letter for a deleted
  short name. So a deleted row is dashed and tagged in words, and opening one says it may be incomplete or another file's data.
- F4c is built: DUPLICATES on ALL IMAGES lists files with the same name and exactly the same size across the indexed images, the most wasted
  space first, from the name index alone. Nothing is read, so it says "possible": two different files can match, and a copy under another name
  is not found. The test image has a pair like that on purpose.
- F4d is built: COPY TO THE STORE. A file or a folder goes out of an image into a new folder under `_exports` on the 4 TB drive, never over anything,
  each file read back from the store and its SHA-1 written down beside it; a deleted file can be copied and is marked as a recovery. It runs inside the
  browser service, so the system change is two lines in the browser's unit, which is not installed yet. Her reading of hard rule 6 (a write into the store's
  exports folder is not a hardware write) is in CLAUDE.md with the rule's other decisions.
- Not done: a first READ from the 7″ and any WRITE on real media; the hidden-area question; the cab-side checks in
  the procedure's 5.4 are the owner's. Task 3 (the MAME check on an image) still waits on a `chdman` output. The deck
  does not record the device letter, the two hash passes with their times, or a write's time and speed, so those lines of
  Appendix B stay blank on the record.
- Owner, next: run the bootstrap from main (`--check` first, then the real run, with the dock idle), then the first READ of
  the 80 GB drive from the 7″.
- **That evening she did, from the Pi:** the bootstrap from main installed the drive browser (10 files, all its own, the logger untouched), the 80 GB
  drive had been READ from the 7″ (the first real READ) and FILES opened it, and a first COPY TO THE STORE completed. It showed one bug the
  tests could not: the copy's folders were 0700 and unreadable to her, because the unit's `RestrictSUIDSGID=yes` refuses the `chmod 2775` the copy asked
  for and the copy swallowed the refusal. Fixed (the folders are made with the umask cleared, and a refused mode now stops the copy) and installed in
  a second run (3 files). The three `usb 1-2` resets in `dmesg` were the dock's, from the night before.
- **Later that night, at the bench:** a new COPY's folder listed with a plain `ls -l` (the mode fix held). A CF card went on the dock through a
  CF-to-IDE adapter fed from a USB charger, and the dock stopped coming back after a power cycle. A diagnosis on the Pi (recorded in BRINGUP,
  Task 0) found the cause in the supplies: with the dock's barrel plug and the adapter's own feed both in, 1 of 6 power-ups came back right;
  with one supply, every cycle did. No reboot is needed to clear it: pull the barrel plug, the USB cable and the feed. The desktop also mounted
  the card's partition on every plug-in, because the udev rule covered the disk and not its partitions (fixed in its own PR, not installed). The
  first WRITE from the 7″ ran: the card's image onto the 80 GB donor, armed on the screen, read back twice and verified.
