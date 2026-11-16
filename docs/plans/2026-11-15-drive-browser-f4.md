# The drive browser, F4: what comes after search, and what each piece would cost

Date: 2026-11-15. Follows `2026-11-15-drive-browser.md` (the design, F0 to F3 built) and F3's report in its §3. **Not covered by the
owner's earlier OK** (that note's §10 said so): everything below except §1, which was already approved and is built.

The owner asked to start F4. F4 is not one feature. It is six, and four of them touch something she has held back on purpose
(a phone, copying files out, new packages, a hard rule). So this note does not build them. It says what each one is, what it
would cost, what could go wrong, what I recommend, and asks the questions only she can answer. The answers go in §9 as
"recommended" or her own words, as before.

## 1. Thumbnails and stepping through photos: already approved, built with this note

The first design (§6.3, §4.2 and decision 3: "the index and thumbnails live on the 4 TB drive and are disposable") promised
thumbnails and a PREV and NEXT through a folder's photos. F1 left thumbnails out ("they are background work") and F2 did not bring
them. They are the part of "see photos" that makes browsing a photo drive practical, so they come first and need no new decision.

- **`thumb`**: a JPEG, at most 192 pixels on its longest side, made once from the photo (the same decoder and the same limits as the
  preview: 50 million pixels, 64 MB, a time limit) and kept under `<store>/_browse/thumbs/`, named by a hash of the image's SHA-1,
  the volume and the inode, so nothing the client typed becomes a path. The orientation in the photo's own EXIF is applied. A photo
  that cannot be drawn is remembered with its reason, so a grid does not try it again on every look. Capped at 2 GB, the
  oldest-looked-at going first; delete the folder and they are made again. While a disk job is queued or running a thumbnail that has
  to be made is refused (409) and the screen shows the name alone.
- **The screen**: with the PHOTOS filter on, a folder's photos are a grid of thumbnails, four across, with the name under each;
  the folders stay rows above it. A thumbnail opens the viewer, and **PREV and NEXT step through the folder's photos** (this page of
  it) with the title following the photo.
- **What it costs**: reading a photo out of the image is the slow part (F0 and F3: about 20 ms for a small file, bytes at 6 to 11 MB/s
  for a big one), so a first look at a folder of 200 four-megabyte photos fills in over a minute or two, one thumbnail at a time and in
  the order they scroll into view; the second look is instant. Not done: making a folder's thumbnails ahead of time in the
  background, and using the small thumbnail that camera JPEGs carry inside them (it would turn a 4 MB read into a 30 KB one;
  it is the obvious next step if the first look feels slow on a real photo drive).

## 2. Deleted files

**What it is.** Today a listing hides deleted entries (`fls` marks them) and the index skips them. The question is whether to show
them, and whether to try to read what was in them.

**What is known.** sleuthkit lists a deleted entry on FAT, NTFS and ext. Reading the content back depends on the filesystem:
FAT and NTFS usually keep the data until it is overwritten, so a recently deleted photo from a camera card is often whole; ext4
clears the file's block pointers when it deletes, so what `icat` gives back is usually empty. A recovered file can also be partly
overwritten and still look like a photo or a document. Nothing here has been tried on a real image: the one drive measured is an
ext4 Linux disk with 13,981 deleted entries and nothing she would want back.

**Odds.** Showing deleted entries: nearly certain, small (a `deleted=1` parameter on the listing, a toggle, a tag). Reading them
back: works for FAT and NTFS, mostly not for ext4, and the screen must say "may be incomplete or overwritten" and never present a
recovered file as the original.

**Risks.** Read-only, so no hardware risk. The privacy risk is the same as the rest of FILES. The honest-labelling risk is the
real one: a half-overwritten PDF that opens is worse than one that does not.

**Recommendation.** Yes, in two steps: first a SHOW DELETED toggle on the folder view (a DELETED tag, greyed rows, a note that
the contents may be gone), then previews of deleted files with the warning. Not in the index: deleted files stay out of search,
because a search result that leads to a half-file is not what she asked for. Needs a real FAT or NTFS image to test the reading
honestly, and she has none yet.

**Built as F4b (2026-11-15), after her "Fine".** What sleuthkit gave on real deleted entries, tried on a FAT32 and an ext4 image (`mdel`,
`mdeltree`, `debugfs`): both list a deleted entry with " (deleted)" after its name; a deleted FAT file reads back whole, byte for byte,
until something is written over its clusters, after which `icat` returns whatever is there now under the old name; FAT keeps no first
letter for a deleted short name (`a.txt` comes back as `_.txt`; a long name keeps it); an ext4 file deleted by a real system has its
inode cleared and lists as size 0 with nothing to read; and a deleted entry whose inode a newer file now holds is marked
"(deleted-realloc)", which is why the screen offers only its name. A deleted folder can be listed on FAT. Not tried: NTFS (no tool here
makes an NTFS image with a deleted file) and a real camera card. Search, the index and the counts leave deleted entries out; they are
counted as deleted.

## 3. HEIC photos

**What it is.** Photos from an iPhone are HEIC, and Pillow cannot open them.

**What is known (Debian package pages, checked 2026-11-15).** `python3-pil` is 11.1.0-5 on Debian 13 and has no HEIF support. There is
no `python3-pillow-heif` package in Debian 13 (the page says "No such package"). `libheif-examples` 1.23.6 is in Debian 13
for arm64 (149 kB) and provides `heif-convert`, `heif-dec`, `heif-enc` and `heif-info`. Rule 9 says apt before pip and ask before a
venv, so the choice is `heif-convert` through the browse core's runner (a time limit, the process group killed), or `pip` into a
venv, which is a decision she has to make.

**Odds.** High that `heif-convert` turns a HEIC into a JPEG we can then draw with the existing code. Unmeasured: its speed on a Pi
(a 12-megapixel HEIC is a heavy decode) and its memory.

**Risks.** A new decoder for hostile files. It runs in the browser's sandbox (no network, the store read-only), under the same time
and memory ceilings as `pdftoppm`; a crash costs one thumbnail.

**Recommendation.** Yes, with `libheif-examples` (one apt package, one line in the bootstrap, as `catdoc` was), after a measurement
on a real HEIC from her phone, because the decode speed decides whether HEIC thumbnails are made lazily or only on a tap. Only worth
doing if she has HEIC photos to look at.

## 4. OCR

**What it is.** A scanned PDF has no text layer. F3's reader says `no text` for it, and F3's report found one in eight PDFs on the
80 GB drive.

**What is known.** `tesseract-ocr` 5.5.0-1 is in Debian 13 for arm64 (380 kB, 2.8 MB installed). How fast it is on this Pi is not
known: I have not measured it, and my expectation is seconds a page, which is a guess.

**Odds.** It works on clean scans and is poor on photographs of paper, handwriting and anything low-contrast. At seconds a page,
10,000 scanned pages are most of a day.

**Risks.** Time and heat (all four cores for hours), and a false sense of coverage: OCR text is wrong in places, so a search that
does not find a word does not mean it is not on the page.

**Recommendation.** Not yet. If she wants it, a **third phase, per image, started by a tap, never automatic**, with its own measurement
first (the F3 pattern: a script that OCRs ten pages of hers and says how long and how well, with no text in the report). Results are
marked as OCR in the snippet. Decide after she has a drive full of scans.

## 5. Duplicates across drives

**What it is.** The same file on two drives (or twice on one).

**What is known.** To be sure two files are the same you read both. Reading every file of every drive is hours (F3: a drive of
small files is minutes, a drive of large ones is bytes at 6 to 11 MB/s: 100 GB is about three hours). A cheap first pass finds the
candidates without reading anything: the same size and the same name, or the same size and modified time, from the name index that
already exists. Then only the candidates are read and hashed.

**Odds.** Good. On a typical archive the candidate set is a small part of the files.

**Risks.** Hashing 10,000 candidate files reads the drive for a long time, so it is a job like text, per candidate, in the
background, standing still for a disk job. The screen must never offer to delete anything: there is nothing here that writes.

**Recommendation.** Yes, in the order: (1) "possible duplicates" from the index alone, instant and free, labelled as possible;
(2) a hash pass on the candidates only, on a tap. Worth doing once she has two overlapping drives (it would be most useful for
her photo and document drives); not before.

## 6. Copying files out

**What it is.** Getting a file from an image to somewhere else: a USB stick, her phone, another machine.

**Why it is the hard one.** There is no destination today. The deck writes hardware only in two places, a T48 burn and a disk
write, each armed at the Pi (hard rule 6). Copying a file out is a third:

- **To a USB stick or card plugged into the Pi**: a hardware write, so rule 6 has to be amended by her. It would need the same
  care as the disk write: pick the target, check it, a hold to arm, verify. A stick can also be a drive she cares about.
- **To a folder on the store** (`/srv/gatbox/exports/`): the store is already written by READ jobs, so this is not a new device.
  But a file in a folder on a drive in a cabinet is not delivered anywhere. It needs a second step to leave the Pi.
- **To a phone or another computer over the network**: that is the phone feature in §7, with its privacy cost.

**Odds.** The mechanics are easy (`icat` to a file). The design is the work: which destination, and who is allowed.

**Recommendation.** The smallest safe version is "COPY TO THE STORE": a file or a folder from an image into
`/srv/gatbox/exports/<image>/...` on the store, never overwriting, with the file's SHA-1 recorded, by the same kind of job as the
disk job (the spool, its own user). It writes only to a folder on the drive that is already the store, which I would say is inside
rule 6's intent, but **that is her call**, because the rule says "the only hardware writes". Getting it off the store needs §6's other
two options, or the drive in her hand: the store is a USB drive and it can be plugged into any computer. For many archive jobs that
is the whole answer: copy what she wants to the store, take the store to the machine.

## 7. A phone, with a pairing PIN

**What it is.** Today FILES answers the Pi's own screen only; gatbox-web refuses a phone with a 403 (her answer on 2026-11-15: "the 7"
only, no phone for now"). This would let a phone browse, and, with §6, download.

**Why it needs her most.** The Pi goes into a cabinet at a barcade, on that building's Wi-Fi or on its own hotspot (SSID GATBOX).
What is inside a drive is private: her family's photos and documents, perhaps other people's. A PIN on a page is the weakest
protection that works at all:

- anyone on the same network can reach port 80 and try PINs; it needs rate limiting and a lockout that cannot be reset from the
  network, and a long PIN;
- hard rule 10 forbids cookies and storage for state, so the session token would live in the page's address or its memory, which
  means it can be lost or shared by a copied link;
- the traffic is plain HTTP on the LAN (there is no TLS on the Pi), so anyone on the Wi-Fi can read the PIN and every photo that
  goes by.

**Odds.** It can be built. It cannot be made as safe as "only the screen on the Pi", and on the barcade's network it is the
least safe place to have it.

**Recommendation.** Not on the LAN. If she wants her partner or herself to browse the archive from a phone, there is already a path
built for that: the archive server (phase D), reached through the Cloudflare tunnel with Access in front, which has real
authentication. The drive browser's index of names could be offered there as a read-only list of what is on each image (no file
contents), if she wants that. Better to decide after F4a to F4e have been used on the Pi.

## 8. Order, if she says yes to all of it

1. **Thumbnails and photo stepping: built** (§1).
2. Deleted files, the toggle first (§2). Small, read-only, honest.
3. Duplicates from the index (§5, step 1). Free once the index exists.
4. HEIC, after a measurement (§3), if she has HEIC photos.
5. COPY TO THE STORE (§6), if she accepts the rule 6 reading.
6. Duplicates with hashing (§5, step 2), OCR (§4) and the phone (§7) only when there is a drive that needs them.

## 9. Questions for the owner

**Answered 2026-11-15:** "Fine" to 1, 3, 4, 5 and 6, and to 2 "no HEIC photos". So: deleted files shown with a warning and kept out of search
(F4b, built); HEIC skipped; OCR left until a drive of scans turns up; possible duplicates from names and sizes first, hashing only on a
tap (F4c); COPY TO THE STORE, with hard rule 6 read as allowing a write into the store's exports folder (F4d); no phone, FILES stays on the
Pi's own screen. The questions are kept below as asked.

Each has a recommendation. "Recommended" on all of them is a complete answer, as before.

1. **Deleted files: show them in a folder, with a warning that the contents may be gone, and keep them out of search?**
   Recommended: yes.
2. **HEIC: add `libheif-examples` (one apt package) and measure on a real HEIC of yours before building?**
   Recommended: yes, if you have HEIC photos; otherwise skip.
3. **OCR: leave it for now, and decide when a drive of scans turns up?** Recommended: yes, leave it.
4. **Duplicates: first "possible duplicates" from the names and sizes alone, and hashing only when you ask?** Recommended: yes.
5. **Copy out: build COPY TO THE STORE (a file or folder into an exports folder on the 4 TB drive, never overwriting, checked and
   verified), and do you read hard rule 6 as allowing a write to the store?** Recommended: yes to both. If not, copy-out waits for a
   destination you are happy to arm at the Pi.
6. **Phone: keep FILES on the Pi's own screen, and use the archive server for anything remote?** Recommended: yes.
