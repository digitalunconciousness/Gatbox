# The drive browser: look inside stored images, with search: design note

**Status: OK'd 2026-11-15 ("recommended": all seven decisions in §9 as recommended). F0 done (its report is in §3). F1 is next.**
Its home in the repo is `docs/plans/2026-11-15-drive-browser.md`. Per RULES §3, her OK on this note covers the code, the tests
and the push; the system config in §7 is shown to her as a bootstrap diff first (hard rule 5).

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

**Read so far (2026-11-15, the WD800JD image, with `file`, `fdisk -l` and `blkid -p` at each partition's byte offset; nothing
mounted, no loop device, nothing sent to the dock):**
- A DOS (MBR) partition table and three partitions: a bootable Linux partition of 73.5 GB, an extended partition, and a
  1020 MB swap inside it. The ext4 filesystem fills its partition exactly. The image is 80,000,000,000 bytes, which is
  156,250,000 sectors of 512 bytes.
- So this drive is a Linux system disk, and ext4 is on sleuthkit's list. It should browse, but nothing has been listed yet.
  It is a good first test of listing speed on a large tree; it is not a test of photos and documents, which will come with her
  other drives.
- `blkid -p` on the whole image names only the partition table, so the filesystem types needed the offsets. FILES finds each
  volume the same way: from the partition table, then at each offset.

**F0's report, read on the Pi 2026-11-15 (sleuthkit 4.12.1 on Debian 13 arm64, Python 3.13.5, SQLite 3.46.1, 4 GB of memory with
2.8 GB free, 47 GB free on the SD card and 3.9 TB on the store), against the WD800JD image (an ext4 Linux system disk):**

| What | Measured |
|---|---|
| `mmls`, `fsstat` | 0.35 s and 3.7 s. The ext4 filesystem is 78.9 GB with 4.95 GB of it used (6%) and 185,000 of 4.8 million inodes. |
| A full recursive listing (`fls -r`) | **200,331 entries in 33.5 s, about 6,000 a second, using 19 MB.** 105,970 files, 16,011 folders, 63,914 links, 13,981 deleted entries. |
| What the drive holds | By kind: 8,779 "photos" (59 MB; they are system icons), 749 documents (22 MB), 96,442 other (3.0 GB). Dates from 1994 to 2016. The most in one folder: 6,875. Fifteen folders deep. |
| `icat`, a 3.7 KB file | 0.18 s |
| `icat`, a 36 MB file | **6.0 s, about 6 MB/s** |
| `icat`, an 85 KB file | **3.0 s**, and 87 MB of memory. Unexplained: one sample. |
| FTS5 on the store | Present. 41,000 rows a second, **76 MB per million files**, a two-word search over 100,000 rows in 3 ms, a prefix search in 5 ms. |
| Pillow | 109 ms for a 12 megapixel noise JPEG, 18 ms for a real 1146x780 photo. |

**What this changes in the design:**
1. **Browsing needs no index.** A listing is fast and small (6,000 entries a second, 19 MB), so a folder, a volume's stats and
   the file counts can come straight from the image. F1 stays as planned. ext4 is the only filesystem measured; NTFS, FAT and
   exFAT numbers wait for a real image of each.
2. **The name index is cheap.** One recursive pass is minutes per drive (a million entries is about three minutes at this rate),
   and FTS5 stores it in about 76 MB per million files and answers in milliseconds. F2 stays as planned.
3. **Reading a file out of the image is the slow part**, and the only thing in the design that is not cheap. About 6 MB/s on a
   large file, a fifth of a second on a small one, and one small file took three seconds. The store is a spinning drive and
   the image is a sparse 76 GB file, so these look seek-bound, but three samples cannot say. So:
   - A preview loads one file at a time, with a visible wait and a way to go BACK.
   - Thumbnails are made in the background, never in the way of a folder view, one at a time, and cached.
   - **F3 (text search) begins with a measurement before its design is final:** 200 random files, in random order and in inode
     order, to see the spread and whether reading in inode order, which keeps the drive's head in one area, is worth building
     around. A drive with 50,000 documents is hours at three seconds each and minutes at a fifth of a second, and F3's
     promise about indexing time depends on which. Until then no indexing time for content is promised.
4. **Memory is small.** 19 MB for a listing and under 90 MB for a file; the service's memory ceiling can be modest.
5. **CPU is not the bottleneck for photos**: a thumbnail is a fifth of a second at worst.
6. This drive says little about photos and documents: its "photos" are icons. The browser's by-kind stats and previews need a
   real archive drive to test against, and the owner's other drives will be it.

**F3's report, read on the Pi 2026-11-15, against the same WD800JD image (`docs/plans/2026-11-15-drive-browser-f3.py`):**

| What | Measured |
|---|---|
| Documents a reader could read | 744 (21.5 MB): 362 `.txt`, 354 `.html`, 14 `.pdf`, 6 `.md`, 4 `.odt`, 3 `.csv`, 1 `.doc`. Median 4 KB, 90th percentile 46 KB, largest 4.8 MB. |
| Listing it again | 200,331 entries in 8.7 s (F0: 33.5 s, so the first pass was partly cold) |
| 200 random documents out of the image, random order | 4.6 s: **median 15 ms, mean 23 ms, 90th percentile 40 ms, slowest 0.26 s, none over 1 s** |
| ... in inode order | 4.0 s: mean 20 ms, **15% faster** |
| ... in random order again | 4.9 s: **two identical runs differ by 6%** |
| First file of the first run | 0.26 s (a drive that was idle; no large spin-up) |
| Extracting the text | **3 ms a file on average**: `.txt`, `.html`, `.csv`, `.odt` and the one real `.doc` (7,068 characters, the first real catdoc output) all read |

**What this changes in the design:**
1. **For small documents, reading is cheap: about 25 ms a document in all (20 to read, 3 to extract)**, so 10,000 documents is
   about four minutes, not hours. F0's one 3-second read (an 85 KB file) did not come back in 600 reads here: none took even
   a second. It stays unexplained; a per-file time limit contains it.
2. **Inode order is worth sorting by and not worth more than that:** 15% faster against 6% noise. It is free, so the text pass
   reads in inode order, and nothing is built around it.
3. **For large documents, time is bytes, not files:** F0 measured 6 MB/s on a 36 MB file, so 100 GB of PDFs is about
   four and a half hours. This image has nothing over 4.8 MB, so it could not say; the script now reads its largest documents
   and reports the rate, and prints the projection from files and bytes together. Its first version printed "0.0 h" for 744
   documents, which was right and useless, and chose no PDF to try by chance: both fixed.
4. **So text is read automatically after the names when the estimate is short, and waits for a tap when it is long.** The estimate
   is files × 25 ms plus bytes at 5 MB/s (a little under F0's rate), shown with its basis on the screen; once the pass is running
   the screen shows the running average of the files already read instead.

**Still unmeasured:** NTFS, FAT, exFAT, HFS+ and ISO on a real image; the rate for big documents (the script now measures it);
PDFs and Office files from a real archive (this drive's 14 PDFs and its one `.doc` are the only real ones read so far).

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
6. **The archive queue, checked 2026-11-15 against main:** the READ job writes a line to `archive-queue.jsonl` for every new
   image and sets the sidecar's `archive` to `pending`, which the DISK tab shows as ARCHIVE PENDING. **Nothing reads that
   file.** gatbox-sync, the archive and gatbox-web never open the disk store, so no disk image has left the Pi, and none can
   until a drain is written. Two things follow. F1 stops a READ that is not for a machine (`unassigned`) from queueing
   anything, and the tab says "not for the archive" for it instead of ARCHIVE PENDING. And when a drain is built, it sends
   only what the owner has said yes to, one image at a time.
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
- **Tables (F2 built the first three, see §10):** `volumes` (image, offset, filesystem, label, sizes), `files` (volume, inode, path,
  name, extension, kind, size, dates), an FTS5 table over paths, and (F3) a second FTS5 table over extracted text (capped per file). Thumbnails are
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
- `gatbox-browse.socket` (`/run/gatbox-browse/browse.sock`, mode 660, group `gatbox-browse`) and `gatbox-browse.service`
  (§6.1), started by the socket. As built (F1), the unit sets no network (`PrivateNetwork`, `AF_UNIX` only), no devices, no
  capabilities, a read-only view of the store with only `_browse` writable, no sight of `/etc/gatbox`, `/var/lib/gatbox-web`,
  the library, manuals or ROM folders, `SystemCallFilter=@system-service`, `MemoryMax=512M`, `TasksMax=64`.
- `gatbox-web.service`: `SupplementaryGroups=gatbox-browse`.
- tmpfiles: nothing on the store itself. The service's own `ExecStartPre=+` makes `_browse` (mode 0700) when the store is
  mounted and carries its marker, and does nothing otherwise; the bare mountpoint stays untouched.
- No udev, `/boot` or `/etc/gatbox` change.

## 8. Limits, so a bad file cannot hurt the Pi

| What | Limit |
|---|---|
| Any subprocess (`fls`, `icat`, `pdftoppm`, `pdftotext`, `catdoc`) | a wall-clock timeout (20 s listing and render, 60 s per indexed file), then killed |
| A file read for preview or text | 64 MB at most for any file; 1 MB of text kept for the index |
| Photo decoding | a pixel limit (Pillow's bomb guard, tightened) and a 50 MB source limit |
| Office archives | an uncompressed-size cap and a member-count cap before anything is parsed |
| A folder | sent 200 rows a page, but held whole in memory while it is sorted (F1 builds the list from `fls`, then sorts and pages it), so a folder is cut at 100,000 entries and says so |
| The daemon | a memory ceiling and a task ceiling in the unit |
| Thumbnails | a total size cap, oldest dropped first |
| Indexing | one file at a time, paused for any disk job |

A file that exceeds a limit is skipped and counted as "not read", never as "nothing in it".

## 9. Owner decisions

**Answered 2026-11-15:** the 7" only, no phone for now; the aim is archival, including non-arcade drives, with photos,
documents and search. Then, to the seven below: **"recommended"**, all of them.

1. **FILES as a segment of DISK, not a ninth tab.** Yes.
2. **A daemon, not a spool of request files** (§6.1). Yes.
3. **The index and thumbnails live on the 4 TB drive and are disposable** (§6.3). Yes.
4. **Deleted files are hidden in v1.** Showing them, and recovering them, is its own feature (F4). Yes.
5. **Old Word, Excel and PowerPoint files (`.doc`, `.xls`, `.ppt`) searchable in F3 through `catdoc`.** Yes, with `.docx`, `.xlsx`,
   `.pptx` and `.odt` read without any package.
6. **The archive queue (§5.6): a per-image choice that defaults to no for anything that is not a machine's drive.** Yes. The
   check is done: nothing drains the queue today.
7. **F0 first:** the bootstrap installs `sleuthkit` and `python3-pil`, and a read-only stage looks at the WD800JD image,
   before any code is written. Yes.

## 10. Tasks (one branch and PR each; small commits, each with a test that fails without it)

- **F0 (built 2026-11-15):** `sleuthkit` and `python3-pil` in the bootstrap's package list, and `docs/plans/2026-11-15-drive-browser-f0.py`,
  with `tests/test-drive-f0.sh` (invented ext4, FAT32 and random images; the report never carries a name or label, the image
  is unchanged, each failure path stops cleanly). The owner read the bootstrap diff, ran it, and ran the script on 2026-11-15;
  the report and what it changes are in §3. **Done.**
- **F1: browse and preview, no index (built 2026-11-15, not installed).** `gatbox-browse` (volumes, listings, stats, previews),
  the loopback routes, the FILES view, the unit and its socket, sysusers, the bootstrap lines, and the archive-queue change from §5.6.
  What it does and does not do, as built:
  - A socket-activated daemon (`gatbox-browse.socket` starts it, it exits after 300 s idle, and also when it sees the store
    NOT CONNECTED, so the next start sees the store's current mounts). gatbox-web forwards six loopback GETs (`volumes`, `ls`,
    `stats`, `info`, `text`, `preview`) and refuses all of them to a phone with a 403 that names the Pi's own screen.
  - A folder is named by its inode and every row carries its own, so FILES walks down by inode and never builds a path.
  - Counts per volume (`stats`) read the whole listing once, in the background, and are cached on the store under `_browse/stats`;
    they wait for a disk job (the daemon looks for `request.json` or `running.json` in the disk spool) and say so.
  - Previews: a photo as a JPEG at the viewer's width, a PDF page through `pdftoppm` from a cached copy, text as the first
    64 KB (UTF-8, then Latin-1). Anything over 64 MB, over 50 million pixels, damaged, or of a kind with no preview says so in words.
    An extracted file is cached (256 MB, oldest dropped) so PDF pages and a zoom do not read the image again.
  - **No thumbnails in F1.** The note's §6.3 had them; with a file coming out of the image at about 6 MB/s they are background
    work, which F2 brings with the index.
  - No file is ever opened by the web process. `ReadOnlyPaths` on the store, no network, no devices, `MemoryMax=512M`.
  - The same change stops a READ for no machine from queueing for the archive: the sidecar says `archive: none, not for a machine`.
- **F2: the index and name search (built 2026-11-15, stacked on F1, not installed).** `gatboxlib/browseindex.py`, three more routes
  (`/search`, `/locate`, `/index`) and the one POST, and the FILES search view. As built, and where it differs from the sketch above:
  - One SQLite file, `<store>/_browse/browse.sqlite` (WAL), schema version 1 (a different version is deleted and rebuilt from the
    images). `volumes` (state `pending`, `indexing`, `done`, `error`), `files` (inode, name, kind, size, mtime, and the parent
    folder's row, so SHOW IN FOLDER can open the folder), an FTS5 table over the path (`unicode61`, prefix indexes), and `wanted`,
    the queue. Keyed by the image's SHA-1: the same bytes under two machines are indexed once.
  - **A volume is indexed in one pass over `fls -r` and is searchable only when the pass is complete.** A stop or a crash leaves
    nothing a search can see, and the next start deletes the rows of a pass that never finished. The note said "progress is per
    file"; the unit is a volume instead, because killing and redoing a pass costs minutes (F0: about 6,000 entries a second)
    where a per-folder walk would start a process per folder.
  - It stands still while a disk job is queued or running (it stops reading `fls`, which stalls on its full pipe) and says so
    on the screen. It runs only while the browser runs; the browser does not leave while it is indexing. After a reboot nothing
    runs until FILES is opened, which asks for the state and goes on with the queue.
  - Words, not substrings: every word is a prefix of a word in the path, all are required, case does not matter, and what is typed
    is cut to words before it reaches FTS, so a query cannot be syntax. Filters: kind (photos, documents, other, folders), year (chips
    built from the matches), size (in the API). **A date range is not in the screen**, only the year chips.
  - The answer always says how many of the store's images it searched ("1 result · searched 1 of 3 images. One that is not
    indexed is not searched"), so an empty result is never read as "not there". An image with no filesystem says so.
  - SHOW IN FOLDER is a button on each result, not inside the viewer: it opens the folder on the page that holds the file
    (`ls` takes `at=<inode>`) with the row marked. A result's own tap opens the file as from a folder.
  - Not indexed: deleted entries and links (hidden in v1, as in the folder view). Limits: 10,000,000 entries a volume, 500,000
    folders remembered (a file below the rest opens from the top of its volume), results counted up to 10,000.
- **F3: text search.** PDF, text and Office extraction, snippets, per-image coverage, `catdoc`.
  - **F3a (built 2026-11-15, stacked on F2, not installed): the readers and the measurement.** `gatboxlib/browsetext.py` reads
    plain text (UTF-8, UTF-16 with a mark, else Windows-1252), HTML, PDF (`pdftotext`), `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`,
    `.odp` and `.epub` (zips read by hand), and `.doc`, `.xls`, `.ppt` (catdoc's `catdoc`, `xls2csv`, `catppt`, which the
    bootstrap now installs). It keeps at most 1 MB of folded text a file. A file it cannot read is refused with one of six
    codes the screen will count, so an empty result can say why: `encrypted`, `damaged`, `no text` (a scan: OCR is F4), `too big`,
    `too slow`, `binary`, or `no reader`. The limits are built to refuse: no XML parser at all (the text is lifted out of the XML
    with patterns, so there are no entity tricks), at most 4,000 parts in a zip, 32 MB from a part and 128 MB from a file, a zip
    that expands more than 200 times refused, a password-protected part refused, and the tools through the browse core's runner
    (a time limit, the whole process group killed, output cut at 4 MB). Not read: `.rtf` and anything else with no reader.
    `docs/plans/2026-11-15-drive-browser-f3.py` is the measurement §3 asked for: it lists one volume, takes 200 random
    documents, reads them out of the image three times (random, inode order, random again, the image's cached pages dropped
    before each run) so that inode order's gain can be told from the noise between two identical runs, times the readers per
    kind, and projects the time for every document in the volume both by file count and by bytes. It prints no name and changes
    nothing. **No indexing time is promised until her report is in.**
  - **F3b (built 2026-11-15, stacked on F3a, not installed): the text index, search and screen.** The report (§3) settled it: for
    small documents reading is cheap, so text is read on its own when it is short and waits for a tap when it is long.
    - A second phase after the names, **per document, in inode order, one at a time, each committed as it is read**: a pause, a
      stop, a crash or a restart costs nothing, and the next pass starts after what was read. (The first version committed every
      twenty documents; a transaction held open across slow documents would have kept the screen's own stop and remove waiting.)
      Names go first: asking for another image's names while text is being read lets the names run, and the text goes on after.
    - **On its own when the estimate is 30 minutes or less** (25 ms a document plus bytes at 5 MB/s, §3), shown on the screen with
      its basis (`TEXT NOT READ · 38,000 documents · 61 GB · about 3 h (tap to read)` when it is longer). Once it runs, the screen
      shows the running average of the documents already read, never a prediction.
    - It stands still for a disk job, and for an unplugged store. It can be stopped (what was read stays and is searchable; the cell
      says the count and a tap goes on), removed (text only; the names stay), and is dropped with the names when an image is.
    - Every document with a reader is accounted for: read, or not with the reason (`encrypted`, `damaged`, `no text`, `too big`,
      `too slow`, `binary`). The search says "read 234 of 237 documents in 1 image; 3 could not be read (1 damaged, 1 no text, 1 too
      big)" and, when some are unread, that they are not searched and where to read them. A reader that raises on a hostile file is
      counted `damaged` and the pass goes on. `.rtf` and the kinds with no reader are not counted at all.
    - The search takes a scope, **NAMES** or **INSIDE DOCUMENTS**. A result has its path, folder, image and a **snippet with the
      match marked** (FTS5 `snippet()`; the screen draws it as text, never markup). Words, as in the names: each a prefix, all
      required. **No phrase search and no "near"**, and no OCR: a scanned PDF is `no text`, which is F4.
    - Limits: 1 MB of text a document (a word past it is not found, and the document says nothing about being cut on the screen yet), 64 MB a
      file, 120 s to read one out of the image, 60 s to extract it. The schema is version 2 (a version 1 file from F2 is rebuilt).
    - What it found in F3a's readers: a file with fewer than 10% control bytes was read as Windows-1252 text even when it was random
      binary (a 4% chance per file); a NUL byte now makes a file binary.
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
