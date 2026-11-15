# The drive browser: look inside stored images, with search: design note

**Status: proposed 2026-11-15, waiting for the owner's OK (§9).** Its home in the repo is
`docs/plans/2026-11-15-drive-browser.md`. Per RULES §3, her OK on this note covers the code, the tests and the push; the system
config in §7 is shown to her as a bootstrap diff first (hard rule 5).

**Why now.** The disk job has imaged its first real drive (the 80 GB WD800JD, 2026-11-14: one pass, 100% rescued, no bad areas,
no kernel messages). The owner's next question was what can be done with what is in the store: browse a drive's files, see photos
and documents, and search. She has other, non-arcade drives to archive too. An image that cannot be opened is a safe copy and
nothing more.

---

## 1. What was asked, and what it means for the design

| Asked (owner, 2026-11-15) | So the browser must |
|---|---|
| "A file explorer with these hard drives if they have a file system" | List folders and files **from the stored image**, with sizes and dates, for the filesystems Linux tools can read. Say plainly when there is none. |
| "And stats about the drive" | DRIVE INFO already shows the hardware (IDENTIFY and sysfs). Add what is on it: filesystem, label, used and free, file counts, oldest and newest dates, photos and documents by count and size. |
| "See photos and documents" | Preview photos, PDF pages and text on the 7". Other formats show name, size and date, never a broken preview. |
| "A search feature" | Search by name, path, kind and date across every image, and then inside documents. |
| "For the 7"" (2026-11-15) | The Pi's own screen only. No phone, no LAN. |
| Archival, non-arcade drives | Nothing here may assume a machine. Personal data stays on the Pi. |

## 2. Scope

**In (F1 to F3, §10):**
- Browse an image: partitions, then a filesystem, then folders. Sort by name, date or size.
- Preview: photos, PDF pages, text. Zoom and pan the way LIBRARY's manual viewer does.
- Per-volume stats (§4.3).
- An index of every file's path, size and dates, kept on the store, and search over it.
- Text pulled out of PDFs, text files and Office files, and search over that, in the background.

**Out (v1):**
- Anything written to a drive or an image. Nothing here mounts, repairs, recovers deleted files or modifies a byte.
- A phone, the LAN, a login. Revisited in F4 if she asks.
- Copying files out (to a stick, the archive, the homelab). F4.
- Encrypted volumes (BitLocker, LUKS, FileVault) and RAID or LVM members: detected, named, not opened.
- Video playback, HEIC and camera RAW decoding, rendering Office documents, OCR of scanned pages.
- The drive's SMART health and native size: still not asked of this dock (BRINGUP, 2026-11-14).

## 3. Facts to settle first (hard rule 12), and what is already known

**Known, from the Debian package pages (checked 2026-11-15):**
- Debian 13 has `sleuthkit` 4.12.1+dfsg-3. Its description lists NTFS, FAT, exFAT, HFS+, Ext3, Ext4, UFS and YAFFS2.
  The upstream project also lists ISO 9660 and APFS. Debian's description does not, so **F0 tests them and nothing assumes them.**
- Debian 13 has `python3-pil` 11.1.0, `catdoc` 0.95 (reads Word, Excel and PowerPoint 97 to 2003 files) and, already installed
  by the bootstrap, `poppler-utils` (`pdftoppm`, `pdftotext`).
- SQLite's FTS5 full-text search works in the Python the deck uses (tried here on 3.45; the Pi's build is to be confirmed in F0).
- No Python bindings for sleuthkit turned up in Debian 13, so the design calls its command line tools (`mmls`, `fsstat`,
  `fls`, `icat`), whose output is stable, and does not depend on a binding.

**To read off the Pi in F0 (a read-only stage, like Task 0), against the WD800JD image already in the store:**
- What is on it: partition table, filesystem type, label. This is the first real answer to "does it browse".
- `fls -r` time and memory for that image; `icat` time for a small file.
- That `fsstat` reports used and free for that filesystem.
- Whether the Pi's SQLite has FTS5.
- Whether an ISO or an exFAT card (any she has) is read, so the support list is measured and not copied from a package page.
- How much room the SD card has, in case something has to live on it (§6.3 puts the large data on the store).

## 4. The flow (the 7")

FILES is a new segment of the DISK tab, after RECORDS. It is not a ninth tab: the nav's eight already fill the side.

### 4.1 Images
The list is the store's images, newest first, each with its filesystem chip (`NTFS`, `FAT32`, `ext4`, `no filesystem`) and an
index state (`INDEXED`, `INDEXING 42%`, `NOT INDEXED`). A SEARCH button opens the keypad. Tapping an image opens its volumes.

### 4.2 Volumes and folders
- **Volumes:** a card per partition, or one for a bare filesystem: type, label, size, used and free. A volume with no
  filesystem, an encrypted one, or one on an unsupported layout is a plain card that says which, and shows the partition
  table and a short hex view of its first sector (read from the image directly).
- **Folders:** breadcrumb buttons, then rows with name, size and date. Sort buttons NAME, DATE, SIZE. Filter chips ALL, PHOTOS,
  DOCUMENTS. 200 rows a page with PREV and NEXT. Deleted entries are not shown in v1.
- **Preview:** a tap opens the file full screen.
  - A photo or a PDF page opens in the same pinch and pan viewer LIBRARY already uses. PREV and NEXT step through the folder's
    photos or the document's pages.
  - Text opens as text (the first 64 KB, with a note if it was cut).
  - Anything else shows name, size, dates and its type, and says it has no preview.
  - BACK always returns to the same folder and position.

### 4.3 Stats
Per volume, from `fsstat` and the listing: filesystem, label, size, used, free, files, folders, oldest and newest modified date,
and photos, documents and other counted by number and by size. Hardware stays under DRIVE INFO.

### 4.4 Search
- **By name (F2):** words in the path, with kind (photo, document, other), date range and size filters. Results show path, size,
  date and the image it is on. Tapping one opens the preview with SHOW IN FOLDER.
- **Inside documents (F3):** the same box. A result shows a snippet around the match. Each image says what share of its files
  have been read for text, so an empty result is never mistaken for "not there".

## 5. Safety and privacy

1. **Read-only, by construction.** The browser never mounts anything and never opens a block device. It reads image files in the
   store through sleuthkit, which parses the filesystem itself. A symlink inside an image cannot lead anywhere, because files are
   read by inode and never by following a path on this machine.
2. **Its sandbox enforces it:** the store is mounted read-only in the service's own view (`ReadOnlyPaths`), whatever the
   file modes say, with one small writable folder for the index and thumbnails (§6.3). No network at all. No device access.
3. **Filesystem parsers and file decoders are attack surface**, and these images come from drives other people used. So they run
   in the sandbox above, with an empty capability set, a system-call filter, a memory limit, and a time limit on every
   subprocess. Photos have a pixel limit and a size limit; PDF pages and Office archives have size limits (§8).
4. **Only the Pi's own screen sees any of it.** Every route is loopback-only, like the write arm (`h.is_local()`); a phone
   gets a 403 that says so. This is a change in what gatbox-web serves: until now it has never served bytes from an image. It
   would serve rendered previews and text, to loopback only, and never an image's raw bytes. CLAUDE.md will say so.
5. **Nothing leaves the Pi.** The index and thumbnails are not synced, and not queued for the archive.
6. **To check before F1 ships:** the READ job queues every new image for the archive (`archive-queue.jsonl`). I have not
   confirmed whether anything sends a disk image from that queue. A personal drive's image must never go without a per-image
   yes. F1 begins by reading what drains the queue; if disk images can be sent, the READ gets an explicit "archive this" choice
   that defaults to no for anything that is not a machine's drive.
7. **Imaging keeps priority.** A READ, RETRY or WRITE must never be slowed by indexing on the same USB bus. gatbox-web does not
   start index work while a disk job is queued or running, and tells a running indexer to pause the moment one is queued (§6.2).

## 6. Design

### 6.1 One new service: `gatbox-browse`
A small stdlib Python daemon, started by systemd, idle until asked. gatbox-web forwards loopback requests to it over a unix
socket; the daemon has no network at all. This is chosen over the disk job's spool pattern because browsing is a conversation
(a tap, a listing, a tap), and a request file polled for a result would make every folder feel slow. The spool pattern stays the
right one for jobs that write to hardware. This one writes none.

| Piece | Choice |
|---|---|
| Parsing | `mmls` (partitions), `fsstat` (volume facts), `fls -r -m` or `fls -l` (listings), `icat` (a file's bytes, by inode) |
| Photos | Pillow: thumbnails and a screen-sized render, with a decompression-bomb guard; EXIF date and camera model read, never GPS |
| PDF | `pdftoppm -r 80 -f N -l N` for a page; `pdftotext` for search |
| Office | `docx`, `xlsx`, `pptx`, `odt`: `zipfile` and an XML parser from the standard library, with an uncompressed-size cap. `.doc`, `.xls`, `.ppt`: `catdoc` (F3, §9 question 5) |
| Text | UTF-8 first, then a Latin-1 fallback; cut at 64 KB for preview and 1 MB for the index |
| Unit | its own static user `gatbox-browse`; `ProtectSystem=strict`, `ReadOnlyPaths=/srv/gatbox/disks`, `PrivateNetwork=yes`, `PrivateDevices=yes`, `NoNewPrivileges`, `CapabilityBoundingSet=` empty, `RestrictAddressFamilies=AF_UNIX`, `SystemCallFilter=@system-service`, `MemoryMax`, `TasksMax`, low CPU and I/O weight |

The service needs to read images the disk job wrote (group `gatbox-disk-queue`, mode 660), so it joins that group. The
read-only mount is what keeps that group's write bit from meaning anything here.

### 6.2 Not in the way of the disk job
gatbox-web already knows when a disk job is queued or running. It starts the indexer only when none is, and when one is queued it
tells the daemon to pause. Indexing is resumable by design: progress is per file, so a pause costs nothing. Listings and previews,
which are short, are never blocked.

### 6.3 The index is disposable
- **Where:** `<store>/_browse/browse.sqlite` and `<store>/_browse/thumbs/`, on the 4 TB drive, so the SD card never fills. Created
  at service start by `ExecStartPre=+`, only when the store is mounted and carries its marker.
- **Keyed by the image's SHA-1.** An image is immutable, so everything derived from it can be rebuilt. **Deleting `_browse`
  loses nothing of record**; the image and its sidecar are the record.
- **Tables:** `volumes` (image, offset, filesystem, label, sizes), `files` (volume, inode, path, name, extension, kind, size,
  dates), an FTS5 table over paths, and a second FTS5 table over extracted text (capped per file). Thumbnails are
  `thumbs/<image sha1>/<inode>.jpg` with a size cap that drops the oldest.
- The store drive carries the data; if it is unplugged, FILES says IMAGE STORE NOT CONNECTED like the rest of the tab.

### 6.4 API (loopback only)
`GET /api/browse/images` · `/volumes?image=` · `/ls?image=&vol=&path=&sort=&page=` · `/preview?image=&vol=&inode=&page=` (a
rendered JPEG or text, never raw image bytes) · `/search?q=&kind=&from=&to=` · `GET /api/browse/index` for its state ·
`POST /api/browse/index` to start, pause or resume. The index state also arrives as an SSE `browse` event.
Bad image names, paths out of the store and unknown volumes are refused with the disk API's own errors.

## 7. System config (shown to the owner before it is installed, hard rule 5)

- apt: `sleuthkit`, `python3-pil`, and in F3 `catdoc` if she wants old Office files (§9). `poppler-utils` is already there.
- sysusers: `gatbox-browse` (user and group), and gatbox-web joins the group to reach the socket.
- A unit `gatbox-browse.service` (§6.1), with its runtime directory for the socket.
- `gatbox-web.service`: `SupplementaryGroups=gatbox-browse`.
- tmpfiles: nothing on the store itself (the service makes `_browse` when the drive is mounted, as §6.3 says); the bare
  mountpoint stays untouched.
- No udev, `/boot` or `/etc/gatbox` change.

## 8. Limits, so a bad file cannot hurt the Pi

| What | Limit |
|---|---|
| Any subprocess (`fls`, `icat`, `pdftoppm`, `pdftotext`, `catdoc`) | a wall-clock timeout (20 s listing and render, 60 s per indexed file), then killed |
| A file read for preview or text | 64 MB at most for any file; 1 MB of text kept for the index |
| Photo decoding | a pixel limit (Pillow's bomb guard, tightened) and a 50 MB source limit |
| Office archives | an uncompressed-size cap and a member-count cap before anything is parsed |
| A folder | paged at 200 rows, streamed, never held whole |
| The daemon | a memory ceiling and a task ceiling in the unit |
| Thumbnails | a total size cap, oldest dropped first |
| Indexing | one file at a time, paused for any disk job |

A file that exceeds a limit is skipped and counted as "not read", never as "nothing in it".

## 9. Owner decisions

**Answered 2026-11-15:** the 7" only, no phone for now; the aim is archival, including non-arcade drives, with photos,
documents and search.

**To answer** (each has a recommendation; "recommended" accepts every one):
1. **FILES as a segment of DISK, not a ninth tab.** Recommended.
2. **A daemon, not a spool of request files** (§6.1). Recommended.
3. **The index and thumbnails live on the 4 TB drive and are disposable** (§6.3). Recommended.
4. **Deleted files are hidden in v1.** Showing them, and recovering them, is its own feature (F4). Recommended.
5. **Old Word, Excel and PowerPoint files (`.doc`, `.xls`, `.ppt`) searchable in F3 through `catdoc`?** It is one small package.
   Recommended: yes, with `.docx`, `.xlsx`, `.pptx` and `.odt` read without any package.
6. **The archive queue (§5.6): a per-image choice that defaults to no for anything that is not a machine's drive.**
   Recommended, after I read what drains the queue.
7. **F0 first:** the bootstrap installs `sleuthkit` and `python3-pil`, and a read-only stage looks at the WD800JD image,
   before any code is written. Recommended.

## 10. Tasks (one branch and PR each; small commits, each with a test that fails without it)

- **F0 (the owner runs the bootstrap diff; read-only on the Pi):** the facts in §3. They go into this note.
- **F1: browse and preview, no index.** `gatbox-browse` (volumes, listings, stats, previews), the loopback routes, the FILES view,
  the unit, sysusers, the bootstrap lines, and the archive-queue check from §5.6.
- **F2: the index and name search.** The indexer with pause and resume, `browse.sqlite`, search routes and the results list.
- **F3: text search.** PDF, text and Office extraction, snippets, per-image coverage, the optional `catdoc`.
- **F4 (not covered by this OK):** a phone with a pairing PIN, copying files out, deleted files, OCR, HEIC, duplicate
  detection across drives.

**Tests** use only invented content and are built in the test itself: an ext2 image with `mke2fs -d`, a FAT image with
`mkfs.fat` and `mtools`, a partition table with `sfdisk`, a JPEG made by Pillow, a minimal PDF, a `.docx` made with `zipfile`.
They run the real sleuthkit; if the tools are not installed they skip with a printed reason, the way the tests skip a missing
browser. Each limit in §8 gets a test: a photo over the pixel limit, a zip that expands past its cap, a PDF over its size limit,
a corrupt image giving a clean error and not a crash, a hung tool killed at its timeout, a path out of the store, and a phone's
403. The dashboard test drives FILES at 1024x600 with 14 px text and 56 px rows, and each new behaviour is broken on purpose to
see a test fail.

## 11. Odds and risks, said plainly

| Risk | What happens, and what limits it |
|---|---|
| The drive has no filesystem Linux tools read (a proprietary arcade format) | FILES says "no filesystem recognized" and shows the partition table and the first sector. The owner's Gauntlet Legends card read as no filesystem in her bench job. |
| NTFS, FAT, exFAT, ext3 and ext4 | Expected to work: all are in Debian's package description. F0 confirms on a real image. |
| APFS, ISO 9660, HFS+ | Not assumed. F0 tests what she has. |
| Encrypted drives | Recognized by their signature and named. Not opened. |
| A very large drive | Browsing works at once, because a folder listing needs no index. The index and text search build in the background, in minutes to hours. **I have measured none of these times yet;** F0 and F3's first runs will. |
| Indexing slows an imaging job | Prevented by §5.7, and by pausing. |
| A parser bug in a hostile or damaged image | Contained by the sandbox (§5.3, §6.1), and the listing or file is skipped. |
| Personal data goes somewhere it should not | Loopback only; nothing queued for the archive by default (§5.6); the index is on the store and never synced. |
