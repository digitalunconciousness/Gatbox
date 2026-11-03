## 2026-11-03 — disk imaging: the design settled, the code built up to the install
- Owner's note: image IDE, SATA, CF and SD media on the deck. Decided (11-02, 11-03): a disk write from the 7" is
  allowed, armed like a burn; a new DISK tab; the 4 TB WD external becomes the image store; the raw `.img` is
  kept; the MAME CHD check is in v1; images are queued for the archive from v1 (phase D uploads them). Image names
  carry a date: `<label>_<YYYYmmdd>_<sha1:8>.img`.
- The 4 TB drive is bus-powered and comes through the MEGA4 (eventually). It holds a Batocera install that can
  go, and a couple of MAME sets that are copied off first, on the laptop. There is no donor drive yet, so Task 0
  runs in three stages: the USB tree and packages now, the 4 TB drive tomorrow, a drive on the dock when there is one.
- Built against fakes, no real block device opened by any test: identity from sysfs (a dock gives every drive the
  same by-id name, which is how the 11-02 near miss happened), the store's mount-and-marker guard, READ with
  ddrescue (one pass on an HDD, RETRY on bad areas as the tech's call, a second read for flash), TARGET CHECK,
  WRITE (a fresh check, the drive re-identified, O_EXCL, fsync, two O_DIRECT read-backs, a line in
  `writes.jsonl`). Each guard was broken on purpose to see a test fail.
- gatbox-web's `/api/disk` and the DISK tab: a write only from the Pi's own screen, after a passing check
  under 5 minutes old, armed 30 s; a non-blank target needs an extra OVERWRITE IT tap; a stopped write says if the
  drive was partly written; the swap checklist is kept per write. A phone sees the state and can tick the checklist
  (owner: "phone ticking off works"), but has no READ, WRITE or hold.
- The 12 V buck powers the Pi in a cabinet when there's no easy AC plug (owner, 11-03). So the READ and WRITE screens
  say to run the Pi from its wall supply for a swap, since the cabinet gets switched off to pull the drive. The
  checklist's 5 V line shows the machine's last saved reading, with SAVE READING beside it.
- The job's units are written and not installed. The udev rule, the two groups, the spool, the store's mount unit
  (UUID from `/etc/gatbox/disks.conf`) and `gddrescue` / `mame-tools` come with Task 4, after Task 0, with the diff
  shown first.
- ⏸ Owner: run Task 0 (`docs/plans/2026-11-02-disk-imaging-task0.sh`, read-only) when the 4 TB drive is here.
