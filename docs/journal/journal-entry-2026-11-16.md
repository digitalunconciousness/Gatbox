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
- The card's photos are `.nef`, which is why the grid showed only names: a NEF is a TIFF whose raw data Pillow cannot draw. The browser now
  draws the JPEG embedded in the file (`gatboxlib/rawpreview.py`). Built on invented NEF-shaped files, not on a real D100's; the first look
  at the owner's card on the Pi is the test. The owner's laptop gave a `chdman info -i` output (MAME 0.289, a regression-test CHD with a
  `GDDD` CHS metadata line), which is the format Task 3's parser was waiting for.
- Approved, queued: the CF card's model name read byte-swapped (`ASSMNU G` for `SAMSUNG`), shown de-swapped; hashing the possible
  duplicates on a tap; a list of earlier copies on the 7″.
