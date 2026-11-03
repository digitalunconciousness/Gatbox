# GATBOX — disk imaging (IDE / SATA / CF / SD): Claude Code prompt

You're on my Arch PC, working in `~/gatbox`. I code here, push a branch, open a PR, and deploy to the Pi myself
(`git pull` + re-run `bootstrap/gatbox-bootstrap.sh`). You never touch the Pi or any other host. When something has
to happen there, write the exact commands, mark the step ⏸, and stop.

## Read first, in this order
1. `docs/agents/RULES.md`. It wins where it disagrees with anything below.
2. `CLAUDE.md`, all of it, especially hard rules 5, 6, 8, 10, 12 and 13.
3. `BRINGUP.md`: the M7 notes on how the T48 dump/burn path unit and sandbox work.
4. `docs/plans/2026-09-29-t48-burn.md` and `tools/gatbox-dump`, `backend/gatboxweb/dump.py`,
   `backend/gatbox-dump.{path,service}`, `tests/test-dump.py`, `tests/fake-minipro`. The disk job copies this pattern.
5. The design note, which I'll paste or drop in as `docs/plans/2026-11-02-disk-imaging.md`. It's the spec. If the
   code disagrees with it, tell me what you found. Don't paper over it.

## Session 1: no code
1. `git pull`. Make the branch `disk-imaging`, and commit the design note on it.
2. Check the note against the code at HEAD: names, paths, the dump job's sandbox, how SSE events are wired, how the
   bootstrap MANIFEST, sysusers and tmpfiles are laid out. List anything the note gets wrong.
3. Write **Task 0** as one ⏸ block of read-only commands for me to run on the Pi, with the dock and the CF reader
   plugged in. I'll paste back the output. It needs:
   - `lsusb`, `lsusb -t` (UAS vs usb-storage);
   - per device: `lsblk -dpo NAME,SIZE,MODEL,SERIAL,TRAN,LOG-SEC,ROTA`, `ls -l /dev/disk/by-id/`,
     `udevadm info -q property -n /dev/sdX`;
   - `sudo smartctl -i -d sat /dev/sdX` and `sudo hdparm -I /dev/sdX | head -n 20`, for an IDE drive and a SATA SSD
     on the dock;
   - the 4 TB WD image-store drive: `lsblk -dpo …` as above, `udevadm info`, `lsblk -f` (what's on it now; don't
     mount it);
   - `df -h / /srv`;
   - `apt policy gddrescue smartmontools mame-tools`;
   - if I have a CHD to hand, `chdman info -i <file>.chd`, for the exact output format the parser needs;
   - what the desktop does when a FAT-formatted card goes into the reader: `findmnt` before and after.
   Nothing in it may write.
4. §9.1 is answered. Ask me only what's still open in §9.2, in one batch, with your recommendation first. Then stop.

## After my §9.2 answers
Work through Tasks 1–7 in the note, in order.
- **Commits:** small, each with a test that fails without it.
- **Test commands:** `bash tests/test-disk.sh` (new, like `test-dump.sh`) and the full suite before the push.
- **Commit as** `digitalunconciousness <240414701+digitalunconciousness@users.noreply.github.com>` with
  `TZ=UTC git commit …`.
- **Before the push:** run `scripts/privacy_check.sh`, and stop if my patterns file is missing.
- **The PR shows:** the diff stat, the test results, the privacy-check output (clean or not), and screenshots of the
  7" layout and a phone.
- **Never** merge, deploy, or push to `main`.

## Non-negotiables for this change
- **No test opens a real block device.** Use a fake sysfs tree, plain files as devices, and `tests/fake-ddrescue`.
- **gatbox-web never opens a block device** and never serves image bytes. It gets no route to `/srv/gatbox/disks`.
- **Identity, not paths.** A drive is its model, serial and size. The by-id name on my dock is the dock's and is the
  same for every drive. Re-identify right before writing.
- **Verify exactly the image's bytes,** read with O_DIRECT. Nothing assumes a replug.
- **No automatic retries on an HDD.**
- **Hard rule 6 changes exactly as §5 of the note words it.** Disk writes from the 7" are armed at the Pi, like a
  burn.
- **The image store is the 4 TB drive mounted at `/srv/gatbox/disks`.** Nothing is ever written under that path
  unless it's mounted with its marker present. Its UUID is site config, never in git.
- **The MAME check reports and never judges.** A tier 2 miss proves nothing, and the result text says so. Parse
  `chdman info` only in the format from my Task 0 output.
- **Archive upload is phase D's job.** This change only fills the sidecar's `archive` field and `archive-queue.jsonl`.
- **System config** (the udev rule, units, sysusers, tmpfiles) goes through the bootstrap, and I see the diff first
  (hard rule 5). The bootstrap stays idempotent, and `--check` stays accurate.
- **Hardware facts come from my Task 0 output,** never guessed. The dock goes into CLAUDE.md's Owned list only with
  its real USB ID.
- **Stdlib Python and apt packages only.** Dashboard: plain HTML/CSS/JS, no browser storage, Synthwave tokens, 56 px
  touch targets on the 7".
- **Nothing personal in git:** no customer, no site, no real roster, no images, no hashes of my customers' disks in
  fixtures. Fixture data is invented (Widget Wars).

## Resuming
Re-read the note's §10 and BRINGUP's progress, `git pull`, and carry on from the first unchecked task.
