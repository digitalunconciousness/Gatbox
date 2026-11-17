# Disk images off the deck: the archive-queue drain (design note, 2026-11-17)

Status: **a note for the owner's decisions; nothing built.** Disk imaging plan §8 says each finished image gets a line in the
store's `archive-queue.jsonl` and that "phase D's sync drains it". Phase D shipped without that drain (checked 2026-11-15, the
drive browser note §5.6): today the only copy of every disk image is the 4 TB drive on the deck.

## What stands in the way (read off the code, 2026-11-17)

1. **The archive refuses big files.** `ARCHIVE_MAX_FILE` defaults to 400 MiB (`archive/gatboxarchive/server.py`). The donor
   drive's image is 80 GB, and a CF card is 65 MB to a few GB. Raising the limit is one line in `archive.conf`, but the
   archive's container then has to hold every image: its free disk is a fact only the owner has.
2. **No record type for a disk image.** The library has `rom_image` (at most 2 GiB, a chip) and no `disk_image`. A new kind is
   a library-v1 change: the archive's tests and the read contract (vendored into cabcrawl and the Tech Tracker) say which
   kinds exist, so it is one change across the three repos even if the apps never download an image.
3. **Time.** Parts are 8 MiB through the Cloudflare tunnel, resumable. 80 GB at the home connection's upload rate is many hours
   (at 20 Mbit/s, about 9). The drain must stop for any disk job (imaging keeps priority on the USB bus, as indexing does) and
   resume where it left off.
4. **What's on a drive.** A machine's drive holds the game. A customer's drive, a camera card, or a donor drive can hold
   other people's data. The disk plan says no customer data on the deck beyond the record. Copying an image to the archive
   puts that data on a second machine reached through the internet (behind Access).

## Options

- **A. Drain to the archive (§8 as written).** gatbox-sync sends one image at a time through the resumable file endpoints,
  reading parts through a gatbox-web loopback route (as dump images go today), plus a `disk_image` record (machine, size,
  SHA-256, SHA-1, the drive's model and serial, the MAME result). It needs: the owner's cap and free-space figure,
  the library-v1 change in three repos, and a rule for which images go.
- **B. A pull from the homelab (§8's interim plan).** A timer on a homelab host runs `rsync` over SSH, with a key on the Pi
  limited to `rrsync -ro /srv/gatbox/disks`. No deck code, nothing through the internet, and big files are rsync's
  strength. It's host changes only (marked ⏸ for the owner). The DISK tab can't say "uploaded" unless the homelab writes
  something back.
- **C. Both.** B for every image (the bulk copy, on the LAN), and A only for images the owner marks, small ones (CF cards)
  first.

## Recommendation

**B now, A later only for what the owner marks.** The size problem goes away on the LAN, nothing new goes through the tunnel,
and no contract changes. The deck side of B is one small optional piece: a `SENT` mark the DISK tab can show, written by the
homelab job into a file the store already has room for (`<image>.offsite.json`), read-only for everything on the Pi.

## Questions for the owner

1. Is there a homelab host with room for the images (the 4 TB store can fill), and do you want the rsync pull (B)?
2. If A: the archive container's free disk, and the largest file you'll let it take.
3. Which images leave the deck at all: every image filed under a machine (today's queue), or only ones you mark?
4. Images not for a machine (`unassigned`, e.g. the NIKON card): never leave the deck (today's rule). Keep that?
