## 2026-11-16: One supply for the CF adapter; the automount fixed; RESTORE and FORMAT decided
- The owner's answers, 2026-11-16: the dock's switch cuts its Molex (the adapter takes its power from there, one supply); the one
  `error -71` reset was probably a slow cable pull; the WRITE bar "works pretty well"; a READ of a drive already stored under another
  machine works (the "already stored under" line); DELETED on the NIKON card showed nothing deleted; no thumbnails showed for the
  card's photos (open: the file types and the thumbnail markers on the Pi are not known yet); the CF adapter on the dock is going to be
  the CF reader.
- The udev rule now ignores a USB disk's partitions too, so the desktop no longer mounts a CF card as it appears (merged; the
  install was the owner's). The CF-adapter warning on the 7″ says to take its power from the dock's own Molex and nothing else.
- Decided, same day: RESTORE (an image back on the drive it came from) is built, not installed. FORMAT (quick only: FAT32, exFAT or ext4,
  MBR up to 2 TB and GPT above, a warning and an offer to image first when the store has no image of the drive) is decided, not built,
  and waits for what the Pi has installed and what the job's sandbox allows. `docs/plans/2026-11-16-restore-and-format.md`.
- The owner first said the card's photos were `.nef` (a TIFF whose raw data Pillow cannot draw), and the browser now draws the JPEG embedded in
  such a file (`gatboxlib/rawpreview.py`, built on invented files, not a real D100's). **They were JPG after all**, and the thumbnails flashed
  for a frame and left empty boxes: the DISK tab's 5 s poll redrew the whole tab whenever `/api/disk` differed, and the image store's free
  space, which is in it, changes with every thumbnail written, so each redraw made new blank `<img>` elements. Fixed in the dashboard (the
  free space is compared in 256 MB steps, and a thumbnail's element is kept across redraws); not seen on the Pi yet. The owner's laptop gave a `chdman info -i` output (MAME 0.289, a regression-test CHD with a
  `GDDD` CHS metadata line), which is the format Task 3's parser was waiting for.
- The first real RESTORE ran that night on the 80 GB donor: image check 32.7 min, write 36.7 min, then two read-backs at about 33 MB/s when last
  seen (about 40 minutes each, estimated), so about 2.5 hours against a unit timeout of 3. The owner's screenshots of the disk spool were what showed the job was alive and what it was. The
  timeout is too tight for a drive of any size beyond this; the fix is a unit change and comes to her as a diff. Thumbnails may now be made
  while a write is in its read-back, when the store is idle.
- Approved, queued: the CF card's model name read byte-swapped (`ASSMNU G` for `SAMSUNG`), shown de-swapped; hashing the possible
  duplicates on a tap; a list of earlier copies on the 7″.
