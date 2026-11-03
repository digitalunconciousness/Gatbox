# Disk imaging on the deck: IDE, SATA, CF and SD media — design note

**Status: owner's answers recorded 2026-11-02 (§9). OK'd except for the open items in §9.2.** Its home in the repo
will be `docs/plans/2026-11-02-disk-imaging.md`. Per RULES §3, her OK on this note covers the code, the tests and the
push.

**Why now.** On 2026-11-02 the CarnEvil swap was done by hand: Midway Seattle, a Quantum Fireball ST 3.5" IDE drive
replaced by a SATA SSD on a StarTech IDE2SAT2. The work ran on the Arch laptop through the XinTop IDE/SATA dock, using
ddrescue, sha256sum and dd, with a hash of the SSD's first N bytes as the verify. It worked, and the game boots on the
SSD. That hand process is the spec. What went wrong doing it by hand becomes the requirements below.

---

## 1. What the hand job taught (each line is a requirement)

| # | What happened on 11-02 | So the deck must |
|---|---|---|
| 1 | The dock gave the Quantum and the SSD the **same** by-id name, `usb-XinTop_XT-U33502_20250830-0:0`, which comes from the dock's own USB serial. Only the size told them apart. | Identify a device by the drive itself: INQUIRY model, the ATA serial when the bridge passes it through, and the exact size. Never by path. Re-identify right before any write. |
| 2 | The CF multi-reader shows one device per slot, with empty slots at 0 B. Its by-id name belongs to the slot, not the card. | Same as 1. A 0-byte device is an empty slot. It's never listed as a source or target. |
| 3 | The "second copy" went to what looked like a stick. It was the laptop's built-in SD slot holding an Arch installer card (iso9660). Nothing was written, but only because the commands failed. | Pick targets only from a list the job built. A TARGET CHECK shows what's on the device now (partitions, filesystem types, labels), and a non-blank target has to be acknowledged before the write. |
| 4 | FAT32 can't hold the 3.2 GB image. | The image store is an ext4 drive the deck mounts. The off-deck copy is the homelab, and later the archive, never a stick. |
| 5 | Pasting several lines at once and a placeholder by-id name garbled the shell twice. | No typed paths. The 7" picks devices. The CLI prints the identity and asks for the model to be typed, like the T48 part name. |
| 6 | The verify run straight after a replug failed: the dock hadn't re-enumerated yet. | Verify with O_DIRECT reads, so no replug is needed. Anything that waits for a device waits for its identity, with a timeout. |
| 7 | The SSD is 128 GB and the image 3.2 GB, so a whole-device hash can never match. | Verify hashes exactly the image's length. |
| 8 | The Quantum dates from 1998 and may have few spin-ups left. It got one pass, with retries only if that pass found errors. | Set the read policy by media type, picked by the tech. USB bridges tend to report every drive as rotational, so the deck can't tell an HDD from flash on its own (to confirm on the Pi). |
| 9 | Measure 5 V at the board before blaming the drive. | The swap checklist links the machine's last 5 V reading from METER, or saves one then. |
| 10 | The laptop could have slept mid-clone. | The Pi doesn't suspend, but it must run from its wall supply during a swap, not the 12 V buck off the cabinet: the cabinet gets powered off to pull the drive. The read screen says so. |

## 2. Scope

**In (v1):**
- Image a source: IDE or SATA on the dock, or CF and SD through a reader or a CF-to-IDE adapter on the dock.
- Keep the raw image on the deck's image store.
- **Check the image against MAME (§7).**
- Check a target, then write an image to it and verify.
- The swap checklist.
- The CLI and a new DISK tab on the 7".
- Each image queued for the archive (§8).

**Out (v1):**
- The archive's upload itself (phase D).
- Sending records to the hubs (phase C items, §8).
- Partition edits and resizing.
- Anything that powers or boots a board (hard rule 8).

## 3. Hardware: owned, and facts to read off the Pi first (hard rule 12)

- **Dock:** XinTop XT-U33502, with a 40-pin IDE header, a SATA port, Molex power and its own PSU. Owned.
  - Known from the laptop: its by-id name. Through it, `lsblk` showed the SSD's own model (`SPCC Solid State Disk`)
    and LOG-SEC 512.
  - **Read on the Pi:**
    - its USB ID;
    - whether it runs as UAS or usb-storage on the Pi 5;
    - whether `smartctl -i -d sat` and `hdparm -I` reach the drive's own model, serial and default CHS through it, for
      an IDE drive and for a SATA one;
    - what `/sys/block/sdX/queue/rotational` reports for an SSD on it.
- **Image store:** a 4 TB Western Digital external drive. Owned and unused.
  - **To read or ask:**
    - the model, and whether it's bus-powered (2.5") or has its own PSU (3.5");
    - whether anything on it needs keeping before it's reformatted.
  - Bus-powered matters for the Pi's USB budget: about 1.6 A shared with `PSU_MAX_CURRENT=5000`.
- **CF reader:** the current four-slot reader has a broken pin, and the owner is buying a new one. Read its USB ID and
  LUN layout on the Pi before code relies on it. Until it arrives, CF goes on the dock through a CF-to-IDE adapter.
- **USB ports.** The Pi 5 has four ports, but the deck now wants seven devices: the DMM adapter, T48, touch, scanner,
  dock, CF reader and image store.
  - The owner has the UUGear MEGA4 (bought by 10-29). CLAUDE.md still lists it as not owned, so update that. It's
    used as a plain hub here; uhubctl stays out of scope.
  - **Proposed layout:**
    - the dock and the image store on the Pi's own two USB 3 ports, so a dock → store read doesn't share one link;
    - the MEGA4 on a USB 2 port, carrying the DMM adapter, T48, touch, scanner and the CF reader.
  - Before deciding, read `lsusb -t` and re-run DEVICES. Existing udev rules key on USB IDs, not ports.
- **Not owned, so not assumed:** a reader with a write-protect switch, and the master guide's panel-mount media bay.

## 4. The flow

This mirrors the T48 dump and burn. gatbox-web only validates and queues. A oneshot job with its own identity does
all the device work.

```
7" → POST /api/disk/… → gatbox-web validates → /var/spool/gatbox-disk/request.json
gatbox-disk.path → gatbox-disk.service (user gatbox-disk) → claims it (running.json) → works → status.json
gatbox-web watches status.json → SSE "disk" → the DISK tab's progress and result cards
```

### 4.1 READ: image a source

1. **The image store is mounted** (§5), or the job stops before touching anything.
2. **Identify:** model, ATA serial (if the bridge passes it), default CHS (if readable), size in bytes, logical
   sector size, by-id name, the dock's USB ID. **Refuse:**
   - size 0 (an empty slot);
   - not on USB;
   - any partition mounted;
   - the Pi's own boot or root device;
   - the image store itself.
3. **Media type** is picked by the tech: HDD, CF, SD or SSD.
4. **Health,** HDD only. Optional, and it never blocks: `smartctl -a -d sat`. A failure through the bridge is noted,
   not fatal.
5. **Pass 1:** `ddrescue -n --sparse SRC <store>/<slug>/<label>.img.part <…>.map`.
6. **If pass 1 found errors:** stop and show the map summary (rescued %, bad areas, bad bytes) with **RETRY**, which
   runs `ddrescue -r3` on the same map. The deck never retries on its own: an old spinner's spin-ups are the tech's
   call.
7. **Second read, flash media only:** an O_DIRECT read of the whole device. Its SHA-1 must equal pass 1's, or the
   job stops with "the card reads differently twice: flaky". For an HDD it's skipped, to save spin time.
8. **Hash:** SHA-1 and SHA-256.
9. **Rename** to `<label>_<sha1:8>.img` and write the `.json` sidecar beside it. The `.map` is kept too.
   **The raw image is kept** (owner, 2026-11-02). There's no compression on the deck.
10. **Run the MAME check (§7)** when the machine has MAME disks. Its result goes into the sidecar.
11. **Queue it for the archive (§8).**

**Sidecar:** label, machine, realm, media type, source identity {model, serial, CHS, bytes, logical sector, by-id, USB
ID}, SHA-1, SHA-256, the ddrescue summary, reads (1 or 2, identical), the MAME check, archive status, started and
finished times, the clock label, and a free-text note.

### 4.2 TARGET CHECK: before every write (the burn's BLANK CHECK)

1. **Identify** the target, as in READ step 2, with the same refusals.
2. **Size:** the target must hold at least the image's bytes. Show both numbers exactly.
3. **Sector size:** logical sector 512. The IDE2SAT2 can't handle anything else.
4. **Not the source:** the same model and serial as the image's source (or the same model and size when there's no
   serial) is refused with "this is the drive the image came from". This is the guard against requirement 1.
5. **What's on it now:** the partition table and each filesystem's type and label, read-only (`blkid -p` or
   `lsblk -f`). Otherwise "no signatures found".
6. **Already holds this image** (the hash of the first N bytes equals the image's SHA-1): show "already written" and
   offer **VERIFY ONLY**.
7. **Result:** `target_ok`, the identity, the contents, and `needs_ack` (true for a non-blank target).

### 4.3 WRITE (allowed from the 7": owner, 2026-11-02)

1. **Two preconditions:**
   - a passing TARGET CHECK of the same image and target, at most 5 minutes old;
   - the arm: HOLD 3 S on the 7", as for a burn, and the physical ARM button once it's wired.
   A non-blank target's hold screen names what gets overwritten.
2. **Re-identify** the device. Its identity must equal the checked one. If it differs, stop: nothing is written.
3. **Claim the device:** open it with `O_EXCL` and keep that open until the verify is done. The kernel refuses that
   open (EBUSY) for a disk with anything mounted, and no one can mount it while it's held. It's a guard that doesn't
   depend on the job's own checks.
4. **Write** the `.img` with `conv=fsync`, through dd or the job's own writer.
5. **Verify:** `blockdev --flushbufs`, then two O_DIRECT reads of exactly the image's bytes. Both SHA-1s must equal
   the image's.
6. **Log** one line in `<store>/writes.jsonl`, for stopped writes too.
7. **The rest of a larger target is left untouched.** The result card says so ("3.2 GB written, 124.8 GB beyond it
   untouched").

### 4.4 SWAP CHECKLIST (after a VERIFIED write; also usable on its own)

**Before:**
- [ ] power-up screen: version noted (phone photo);
- [ ] settings and audits photographed;
- [ ] 5 V at the board under load: the last reading on this machine from METER, or SAVE READING now; window 5.0–5.2 V;
- [ ] jumper, pin-1 stripe and power plug photographed.

**After:**
- [ ] version matches;
- [ ] settings and audits match;
- [ ] 3+ full games played (guns if fitted);
- [ ] 5 cold boots, about 30 s apart;
- [ ] 30–60 min of attract mode;
- [ ] the original labelled and left with the machine's owner.

Each tick is saved with the write record, with its time. The checklist blocks nothing.

## 5. Safety

- **gatbox-web never opens a block device.**
  - It lists candidates from sysfs, as `devices.py` does, and reads sidecars.
  - **It never serves image bytes.** It's an open web server on the barcade Wi-Fi, and the images are game code. The
    store gets no route.
- **gatbox-disk can open only whole USB disks:**
  - a udev rule gives group `gatbox-disk` rw on `SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb"`;
  - mmcblk and nvme stay root:disk;
  - the unit: `DevicePolicy=closed`, `DeviceAllow=block-sd rw`, `ProtectSystem=strict`,
    `ReadWritePaths=/srv/gatbox/disks /var/spool/gatbox-disk`, `IPAddressDeny=any`;
  - plus the job's own refusals and the O_EXCL claim. That's three independent locks.
- **The image store:**
  - The 4 TB drive is formatted ext4 and mounted by its filesystem UUID at `/srv/gatbox/disks`, through a systemd
    mount unit with `nofail`. The UUID is site config in `/etc`, not git.
  - **It must never fall through to the SD card.** The disk service gets `RequiresMountsFor=/srv/gatbox/disks`, and
    the job checks `os.path.ismount()` plus a marker file on the drive before every read.
  - Without the drive, the DISK tab says "IMAGE STORE NOT CONNECTED", and READ and WRITE are off.
  - The store drive is shown as IMAGE STORE in the device list, never as a source or target. It's mounted, so the
    refusals and O_EXCL also cover it.
- **The desktop must not auto-mount dock or reader media.**
  - Option: `ENV{UDISKS_IGNORE}="1"` on the same udev match.
  - Option: pcmanfm's automount off.
  - Which one the Trixie desktop honours is read on the Pi, not assumed. The image store is mounted by its own unit,
    not by the desktop.
- **One disk job at a time,** with 409 while one is queued or running. T48 and disk jobs don't block each other.
- **Timeouts:** not the dump's 20 minutes. Size `TimeoutStartSec` for a 40 GB HDD at USB 2 speed (about 3 h). An
  interrupted read resumes from its map.
- **Hard rule 6 becomes:** "The only hardware writes from the dashboard are a T48 burn and a disk write, each armed at
  the Pi (a hold on the 7″ for now, the physical ARM button later), checked first (blank check / target check) and
  verified. Nothing else writes hardware. No part auto-detect."

## 6. API (gatbox-web)

| route | what |
|---|---|
| `GET /api/disk` | devices from sysfs (identity, size, empty slots, image store marked), store mounted + free space, busy, last status |
| `GET /api/disk/images` | the store's sidecars (no bytes), grouped by machine, with MAME check and archive status |
| `GET /api/disk/chd` | the reference CHDs in `_chd/` and the MAME disk each one is (§7) |
| `POST /api/disk/read {device, identity, machine, label, media}` | queue a READ; the job re-checks the identity |
| `POST /api/disk/retry {work_id}` | `ddrescue -r3` on the same map |
| `POST /api/disk/mame {image}` | re-run the MAME check, e.g. after adding a CHD |
| `POST /api/disk/check {image, device, identity}` | queue a TARGET CHECK |
| `POST /api/disk/write {image, device, ack}` | **loopback only (403 otherwise)**; 409 without a passing check of the same pair ≤ 5 min old; armed 30 s |
| `POST /api/disk/checklist {write_id, item, done}` | tick or untick a checklist item |
| SSE `disk` | progress and results |

## 7. The MAME CHD check (in v1; owner, 2026-11-02)

**The facts behind it** (MAME source and the chdman docs; re-check the output format on the Pi):
- A v5 CHD carries two SHA-1s.
  - The **SHA1** covers the raw data plus metadata.
  - The **Data SHA1** covers only the data. It's the same for any CHD holding the same data, whatever the compression
    or hunk size.
- MAME's driver hash (what `-listxml` gives for a `<disk>`, and what `gatbox-mame-roms` already stores per version)
  is the combined one. Different metadata, such as the cylinder/head/sector geometry, gives a different combined
  SHA-1 for the same data.
- **So a raw drive image's SHA-1 never equals MAME's hash directly.** The comparison goes through a CHD.
- Debian Trixie's `mame-tools` (0.276, the same version as the Pi's MAME) has chdman with `info`, `verify`,
  `extracthd` and `createhd` (`-chs`, `-ss`, `-c none`). Add it to the bootstrap's apt list. The exact text `info`
  prints is read on the Pi (Task 0), not assumed.

**Tier 1, with a reference CHD (definitive):**
1. The owner drops MAME CHDs into `/srv/gatbox/disks/_chd/`, the way ROM images go into `_images/`.
2. `chdman info` on each gives its combined SHA1, Data SHA1, logical size and CHS metadata.
3. The combined SHA1 is matched against the versions in `mame-roms.json`. That names the CHD, e.g. "carnevil, v1.0.3".
   A CHD that matches no MAME disk is listed as "unknown CHD".
4. For an image on a machine with a matching CHD: hash exactly the CHD's logical size of the image and compare it with
   the CHD's **Data SHA1**. No extraction is needed.
   - **Equal:** **MATCH, MAME `<set>`**. That's the exact version, from the bytes. The image must be at least that
     long.
   - **Not equal:** `chdman extracthd` to a temp file on the store, then `cmp -l`. It reports the number of
     differing bytes and where they cluster.
     - **A few small clusters:** "same version, the game has written to these areas" (audits or settings).
     - **Widespread:** "a different version, or damage".
     - The temp file is deleted afterwards.
5. Every version's CHD that's present is tried, so a drive is identified even when nobody wrote its version down.

**Tier 2, no reference CHD (a best-effort try):**
- The job builds a CHD from the image with `chdman createhd -c none -chs <the drive's own CHS>`, then compares its
  combined SHA1 with the machine's MAME disks. It needs about 1× the image size of temp space on the store.
- A **match is definitive.** A miss proves nothing: MAME's dump may use other geometry or extra metadata, or the game
  may have written to the disk. The result says exactly that.
- It runs only if the source's CHS was readable. **Whether this tier ever matches for Seattle or Vegas drives is the
  first thing to check on real data** (the CarnEvil image is on the laptop). If it never does, drop it.

**What it never does:**
- It never turns a CHD into a write source silently. A reference image only becomes writable if the owner extracts
  it into the store as an image of its own (a later option, not v1).
- It never decides a drive is bad. It reports.

## 8. The archive, worlds and records

- **Automatic upload (owner: wants it). Phase D builds the archive.**
  - From v1, each finished image gets an `archive` entry in its sidecar: `pending`, then `uploaded <sha256> <when>`.
  - It also gets a line in the store's `archive-queue.jsonl`, keyed by SHA-256.
  - When phase D's deck ↔ archive sync lands, it drains that queue through the resumable file endpoints (8 MB parts),
    so every image taken before then uploads with no rework. Reference CHDs in `_chd/` go too, as knowledge.
- **Until phase D, the off-deck copy (§9.2 b):**
  - **Recommended:** a pull from the homelab. A timer there runs rsync over SSH, with a key on the Pi limited by
    `rrsync -ro /srv/gatbox/disks`.
  - No deck code and no credentials on the deck beyond that one restricted key. It's host changes only (⏸).
  - Phase D replaces it.
- **Realm:** each image and write record is stamped with its machine's realm (barcade or side work) when it's made,
  as dumps are. CarnEvil-style customer jobs are side work.
- **No customer data on the deck:** a record names the machine or asset, nothing else.
- **Phase C** extension: new `disk_image` and `disk_write` contract items, one change across all three repos.

## 9. Owner decisions

### 9.1 Answered (2026-11-02)
1. **Hard rule 6:** yes, a disk write from the 7", armed like a burn (§5 has the new wording).
2. **A new DISK tab.**
3. **Storage:** the 4 TB WD external drive becomes the image store. The Pi's SD card is too small.
4. **Keep the raw `.img`.** No compression on the deck.
5. **A new CF reader** will be bought.
6. **The MAME CHD check is wanted:** in v1, §7.
7. **Automatic upload to the archive is wanted:** queued from v1, uploaded by phase D, §8.

### 9.2 Still open (recommendation first)
a. **The 4 TB drive:** which model, bus-powered or its own PSU, and is there anything on it to keep? It gets
   reformatted ext4.
b. **Off-deck copy until the archive exists:** the homelab pull with an `rrsync -ro` key (recommended), or a manual
   rsync after each job.
c. **Reference CHDs:** does she have the CarnEvil and Gauntlet Legends CHDs to drop into `_chd/`? Without them, only
   tier 2 runs.
d. **USB layout:** the dock and store on the Pi's USB 3 ports, everything else on the MEGA4 (recommended), once
   `lsusb -t` and DEVICES confirm it.

## 10. Tasks (one branch, one PR; small commits, each with a test that fails without it)

**Task 0 ⏸, read-only on the Pi, run by the owner:**
- the §3 facts: the dock, the 4 TB drive, `lsusb -t`;
- the Pi's free space;
- the package names: `apt policy gddrescue smartmontools mame-tools`;
- `chdman info -i` on any CHD (the output format);
- the auto-mount behaviour.

Paste them into BRINGUP before any code depends on them.

**Task 1, test doubles:**
- a fake sysfs tree (`GATBOX_SYSFS`);
- regular files standing in for devices;
- `tests/fake-ddrescue` with injectable bad areas and stalls;
- `tests/fake-chdman` with `info`, `extracthd` and `createhd`, in the format read in Task 0;
- a tmpfs or plain directory with and without the store marker;
- **no test ever opens a real block device.**

**Task 2, `tools/gatbox-disk` core and CLI:**
- identify, read, retry, check, write, mame;
- `gatbox-disk list | read | check | write | mame`;
- the CLI write prints both identities and asks for the target's model to be typed.

**Task 3, the MAME check (§7):** tier 1 (Data SHA1, then extract and `cmp` clusters), tier 2 (createhd), the `_chd/`
listing named from `mame-roms.json`.

**Task 4, job mode and install:**
- `gatbox-disk.path/.service`, the store's mount unit (UUID from a git-ignored site file), the udev rule (group and
  auto-mount), sysusers, tmpfiles, `mame-tools` and `gddrescue` in the apt list, and the bootstrap MANIFEST;
- **system config is shown to the owner, ⏸, before install (hard rule 5).**

**Task 5, gatbox-web:** the routes in §6, SSE `disk`, and the loopback gate.

**Task 6, the DISK tab:**
- READ: SOURCE → READ → image card with the MAME result;
- WRITE: IMAGE → TARGET → CHECK → HOLD → VERIFIED;
- the checklist, the store status, archive pending/uploaded;
- the phone view shows status only, with no hold;
- screenshots of the 7" and a phone (RULES §3.4).

**Task 7, docs:**
- CLAUDE.md: hard rule 6 as in §5; the dock, the 4 TB store and the MEGA4 under Owned; the installed list;
- BRINGUP: progress, Task 0 facts, the homelab pull ⏸ (if 9.2 b);
- a journal entry.

**Tests that must exist (each one a §1 lesson or a §5/§7 guard):**
- the source and target share a by-id name and the target **is** the source → refused, nothing written;
- the device is swapped between CHECK and WRITE → stopped, nothing written;
- a target one byte too small → refused; logical sector 4096 → refused;
- a mounted partition → refused (the O_EXCL claim too); a 0-byte slot → not listed;
- an mmcblk or nvme device in the fake sysfs → never listed, never opened;
- the image store → listed as IMAGE STORE, never a source or target;
- the store not mounted (no marker) → READ and WRITE refused, nothing written under the mountpoint;
- HDD: exactly one read; flash: a second read that differs → stopped, "flaky";
- ddrescue bad areas → stopped with the summary; RETRY resumes the same map;
- a write verify mismatch → stopped, with a `writes.jsonl` line;
- MAME tier 1:
  - Data SHA1 equal → MATCH with the set name;
  - three small clusters → "game writes";
  - widespread → "different version or damage";
  - a CHD matching no MAME disk → "unknown CHD";
- MAME tier 2: createhd's SHA1 equal → MATCH; unequal → "no match without a reference CHD", never "bad";
- a finished image → `archive: pending` plus one `archive-queue.jsonl` line; a re-read of the same bytes → no
  duplicate line;
- a write from a non-loopback address → 403; no check, or an expired one → 409; a non-blank target without `ack` →
  409; an expired arm → stopped;
- no route serves store bytes (a GET on any store path → 404)
