# Disk imaging on the deck: IDE, SATA, CF and SD media — design note

**Status: owner's answers recorded 2026-11-02 and 2026-11-03 (§9). OK'd, with §9.3 (found in session 1) folded in.** Its home in the repo
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
- **Image store:** a 4 TB Western Digital external drive. Owned; with the owner from 2026-11-04.
  - **The owner, 2026-11-03:**
    - It's **bus-powered**. The MEGA4 is how it will be powered ("I have a mega4 hub for power. Eventually.").
    - It holds a Batocera install that can go, and **a couple of MAME sets to save** first. They're copied off (on the
      laptop) before it's reformatted.
  - **To read:** its model and serial (Task 0), and how the MEGA4 itself is powered, before it carries the store.
  - Bus-powered matters for the Pi's USB budget: about 1.6 A shared with `PSU_MAX_CURRENT=5000`.
- **CF reader:** the current four-slot reader has a broken pin, and the owner is buying a new one. Read its USB ID and
  LUN layout on the Pi before code relies on it. Until it arrives, CF goes on the dock through a CF-to-IDE adapter.
- **USB ports.** The Pi 5 has four ports, but the deck now wants seven devices: the DMM adapter, T48, touch, scanner,
  dock, CF reader and image store.
  - The owner has the UUGear MEGA4 (bought by 10-29). CLAUDE.md still lists it as not owned, so update that. It's
    used as a plain hub here; uhubctl stays out of scope.
  - **Layout** (§9.2 d, adjusted 2026-11-03 because the store is bus-powered and the MEGA4 powers it):
    - the dock on one of the Pi's USB 3 ports;
    - the MEGA4 on the other USB 3 port, carrying the image store, the T48, the scanner and the CF reader. A dock →
      store read still crosses two links, and the store keeps USB 3 speed;
    - the DMM adapter and the touch screen on the Pi's own USB 2 ports. The logger's adapter is never behind the
      hub, so a hub's power trouble can't stop the logger (hard rule 3).
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
9. **Rename** to `<label>_<YYYYmmdd>_<sha1:8>.img` and write the `.json` sidecar beside it. The `.map` is kept too,
   under the same name. The date is the Pi's local date when the read started, as in the rail CSVs' names (owner,
   2026-11-03: "the images filename should have a date also"). The sidecar keeps the exact time and the clock label.
   **The raw image is kept** (owner, 2026-11-02). There's no compression on the deck.
   **The same bytes are never stored twice**, as in the dump archive: when the machine's folder already holds that
   SHA-1, the job reports "already in the store as `<name>`", and that sidecar gains this read's date.
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
5. **Verify:** two O_DIRECT reads of exactly the image's bytes, after the write's fsync. Both SHA-1s must equal the
   image's. O_DIRECT already goes past the page cache, so there's no `blockdev --flushbufs` (it would need
   `CAP_SYS_ADMIN`; §9.3 item 4).
6. **Log** one line in `<store>/writes.jsonl`, for stopped writes too.
7. **The rest of a larger target is left untouched.** The result card says so ("3.2 GB written, 124.8 GB beyond it
   untouched").

**Added by the audit of 2026-11-05**, before any of this has run on a real drive. Each came from reading the write
path as an adversary, has a test that fails without it (`tests/test-disk.py`, "WRITE, what the audit of this path
found"), and none changes what a good write looks like:
- **The image is checked before the target is touched.** Its size must equal the check's, and its own bytes must hash to
  the sidecar's SHA-1. The sidecar's hash was made when the image was stored; a store drive that has gone wrong since
  would otherwise be written to the target and found out only by the read-back.
- **What's on the target is read again at write time.** `needs_ack` from the check is five minutes old, and a different
  drive of the same make and size is the same drive until the serial passes through the dock (§3). A target that was
  blank at the check and holds something now needs the acknowledgement like any other.
- **After the claim, the drive is identified again**, and the descriptor's own size compared, before the first byte.
- **An arm is a dict with a numeric expiry.** `false`, `{}`, a string for a time and a bool are not arms (an arm skips the
  typed model, so it is never taken on trust). A string used to crash the job with a `TypeError`.
- **One read or write at a time on the store**, whoever started it (the 7" job or the terminal): an exclusive `flock` on
  `<store>/.gatbox-disk.lock`, released by the kernel if the process dies.
- **A short `pwrite` finishes its piece** (the return value was ignored). A target that refuses the open as read-only
  or write-protected is a stop in words, not a traceback. VERIFY ONLY claims the drive read-only.
- **What is typed at the terminal can't be empty.** A bridge that reports no model made a bare Enter, or a script's end
  of input, count as the model typed; the device name is typed instead.

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
  - It lists candidates from `/sys/block` and reads sidecars. That's new code: `devices.py` reads
    `/sys/bus/usb/devices`.
  - **It never serves image bytes.** It's an open web server on the barcade Wi-Fi, and the images are game code. The
    store gets no route.
- **gatbox-disk can open only whole USB disks:**
  - a udev rule gives group `gatbox-disk` rw on `SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb"`;
  - **two groups** (§9.3 item 3): `gatbox-disk` is the devices' group, and only the job's user holds it. The spool
    is group `gatbox-disk-queue`, held by the job and gatbox-web. The owner's login joins the queue's group, never the
    devices', so the CLI hands its work to the job through the spool (or runs under sudo);
  - mmcblk and nvme stay root:disk;
  - the unit: `DevicePolicy=closed`, `DeviceAllow=block-sd rw`, `ProtectSystem=strict`,
    `ReadWritePaths=/srv/gatbox/disks /var/spool/gatbox-disk`, `IPAddressAllow=localhost` with `IPAddressDeny=any`
    (the clock label comes from gatbox-web, as the dump's does; §9.3 item 2), and
    `AmbientCapabilities=CAP_SYS_RAWIO` with `CapabilityBoundingSet=CAP_SYS_RAWIO` and nothing else (ATA
    pass-through for the drive's own serial and CHS; §9.3 item 4);
  - plus the job's own refusals and the O_EXCL claim. That's three independent locks.
- **The image store:**
  - The 4 TB drive is formatted ext4 and mounted by its filesystem UUID at `/srv/gatbox/disks`, through a systemd
    mount unit with `nofail`. The UUID is site config in `/etc/gatbox/disks.conf`, not git: created empty by
    tmpfiles like `hub.conf`, and the bootstrap writes the mount unit only when it holds a UUID (§9.3 item 6).
  - **It must never fall through to the SD card** (§9.3 item 5):
    - the job checks that the store is a mount point and holds its marker file before every read and write. If not,
      it reports "IMAGE STORE NOT CONNECTED" and clears the request;
    - the bare mountpoint on the SD card isn't writable by the job's user;
    - the service is ordered `After=` the mount unit, but doesn't require it. With `RequiresMountsFor=`, an unplugged
      drive would fail the service before the job runs, leave `request.json` behind and jam the path unit.
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
- **Realm:** each image and write record is stamped with its machine's realm (barcade or side work) when it's made.
  gatbox-web looks up the machine's hub when it queues the request and puts the realm in `request.json` (dumps don't
  record one yet; §9.3 item 1). CarnEvil-style customer jobs are side work.
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

### 9.2 Answered (2026-11-03: "9.2 recommended is fine")
a. **The 4 TB drive:** bus-powered, and powered through the MEGA4 (eventually). The Batocera install can go. A couple
   of MAME sets are saved first, then it's reformatted ext4 (§3). Its model and serial come from Task 0.
b. **Off-deck copy until the archive exists:** the homelab pull with an `rrsync -ro` key.
c. **Reference CHDs:** any CHDs she has go into `_chd/`. The MAME sets on the 4 TB drive may include CarnEvil's or
   Gauntlet Legends': check while copying them off. Otherwise only tier 2 runs.
d. **USB layout:** as recommended, adjusted for the bus-powered store (§3): the dock on a Pi USB 3 port, the MEGA4 on
   the other with the store on it, and the DMM adapter and touch on the Pi's own USB 2 ports. `lsusb -t` confirms it.

### 9.3 Found in session 1 (2026-11-03): decided ("9.3 as recommended"), and folded into §4, §5, §8 and §10
Checked against the code at HEAD. Everything else matched: the spool and path unit, one job at a time, the 30 s arm
and 5-minute check window, the screen-only write, the 20-minute timeout, the archive's naming, the sidecar's clock
label, the SSE relay, and `gatbox-mame-roms` keeping disk hashes for every version.
1. **Realm.** Dumps don't record a realm today. gatbox-web looks up the machine's hub when it queues a request and puts
   the realm in `request.json`.
2. **The clock label.** The dump job asks gatbox-web on localhost for it, which `IPAddressDeny=any` alone would block.
   The disk unit also gets `IPAddressAllow=localhost`, as the dump's does.
3. **Two groups.** The spool's group isn't the devices' group. gatbox-web joins the spool's group to queue work, and
   must not hold the group the udev rule gives write access to every USB disk (its empty `/dev` would then be the
   only lock). The owner's login stays out of the devices' group too, so the CLI hands its work to the job through the
   spool, or uses sudo.
4. **Capabilities.** As a non-root user, ATA pass-through (what `smartctl -d sat` and `hdparm -I` send) needs
   `CAP_SYS_RAWIO`, and `blockdev --flushbufs` needs `CAP_SYS_ADMIN`. The unit grants `CAP_SYS_RAWIO` alone (bounding
   set the same). The flush is dropped: the write's fsync and the O_DIRECT reads already go past the page cache. The
   first job run on the Pi confirms it.
5. **No `RequiresMountsFor=`.** With the drive unplugged it fails the service before the job runs, `request.json`
   stays, and the path unit refires until it gives up, so the DISK tab never gets a status. Instead: `After=` the
   mount unit; the job's own mount and marker check reports "IMAGE STORE NOT CONNECTED" and clears the request; and
   the bare mountpoint isn't writable by the job, so an unplugged store can't fill the SD card.
6. **The UUID** lives in `/etc/gatbox/disks.conf` (§5 said `/etc`, Task 4 a git-ignored site file). It's created empty
   by tmpfiles, like `hub.conf`, and the bootstrap writes the mount unit only when it holds a UUID.
7. **Smaller:** the disk listing reads `/sys/block`, not the `/sys/bus/usb/devices` that `devices.py` reads, so it and
   `GATBOX_SYSFS` are new code. The 12 V buck (§1, row 10) isn't recorded in the repo: if it powers the Pi in a
   cabinet, it belongs in CLAUDE.md's Owned list.

### 9.4 Answered (2026-11-03, after Tasks 5 and 6)
1. **The 12 V buck** powers the Pi in a cabinet when there's no easy AC plug. So §1 row 10 applies in practice, and the
   READ and WRITE screens tell the tech to run the Pi from its wall supply for a swap. CLAUDE.md lists the buck.
2. **The phone can tick the swap checklist** ("phone ticking off works"). It blocks nothing. READ, WRITE and the hold
   stay on the 7".

## 10. Tasks (one branch, one PR; small commits, each with a test that fails without it)

**Task 0 ⏸, read-only on the Pi, run by the owner** (`docs/plans/2026-11-02-disk-imaging-task0.sh`):
- the §3 facts: the dock, the 4 TB drive, `lsusb -t`;
- the Pi's free space;
- the package names: `apt policy gddrescue smartmontools mame-tools`;
- `chdman info -i` on any CHD (the output format);
- the auto-mount behaviour.

It runs in stages, since there's no donor drive yet (owner, 2026-11-03). The script reports whatever is plugged in:
1. **Now:** the USB tree, the dock's USB ID if its bridge shows up with no drive in it, packages, free space, auto-mount
   with a spare card.
2. **When the 4 TB drive is here (11-04):** its model, serial, partitions and power, with it spinning.
3. **When a drive is on the dock** (a donor, or the next real job): what the bridge passes through for IDE and SATA.
   Until then the code treats the ATA serial and CHS as "if the bridge passes them", as §4.1 already says, and
   nothing ships that depends on an unread fact.

Paste them into BRINGUP before any code depends on them.

**Before the reformat ⏸:** the owner copies the MAME sets off the 4 TB drive (on the laptop). The format commands are
written from Task 0's identity of that drive (model and serial), never from a `/dev` name.

**When the code goes in** (owner, 2026-11-03: "code slides in where it makes sense"): the parts that need neither
the archive nor an unread hardware fact come first. That's Tasks 1 and 2: the fakes, identity from sysfs, the store
guard, READ, TARGET CHECK and WRITE, all against files. The chdman parser in Task 3 waits for Task 0's real output.
Task 4's udev and auto-mount rules wait for stages 2 and 3.

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
- `gatbox-disk.path/.service`, the store's mount unit (UUID from `/etc/gatbox/disks.conf`), the udev rule (group and
  auto-mount), sysusers (the two groups), tmpfiles (the spool, `disks.conf`, the unwritable bare mountpoint),
  `mame-tools` and `gddrescue` in the apt list, and the bootstrap MANIFEST;
- **system config is shown to the owner, ⏸, before install (hard rule 5).**

**Task 5, gatbox-web:** the routes in §6, SSE `disk`, and the loopback gate.

**Task 6, the DISK tab:**
- READ: SOURCE → READ → image card with the MAME result;
- WRITE: IMAGE → TARGET → CHECK → HOLD → VERIFIED;
- the checklist, the store status, archive pending/uploaded;
- the phone view shows status only, with no hold;
- screenshots of the 7" and a phone (RULES §3.4).

**Task 7, docs:**
- CLAUDE.md: hard rule 6 as in §5; the dock, the 4 TB store and the MEGA4 under Owned (and the 12 V buck, if it powers
  the Pi in a cabinet); the installed list;
- BRINGUP: progress, Task 0 facts, the homelab pull ⏸ (if 9.2 b);
- a journal entry.

**Tests that must exist (each one a §1 lesson or a §5/§7 guard):**
- the source and target share a by-id name and the target **is** the source → refused, nothing written;
- the device is swapped between CHECK and WRITE → stopped, nothing written;
- a target one byte too small → refused; logical sector 4096 → refused;
- a mounted partition → refused (the O_EXCL claim too); a 0-byte slot → not listed;
- an mmcblk or nvme device in the fake sysfs → never listed, never opened;
- the image store → listed as IMAGE STORE, never a source or target;
- the store not mounted (no marker) → READ and WRITE refused with "IMAGE STORE NOT CONNECTED", nothing written under
  the mountpoint;
- a request's realm → kept in the sidecar and the `writes.jsonl` line;
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
- the image's name carries the read's date; the same bytes read again on another day → not stored twice, and the
  existing sidecar gains the date;
- a write from a non-loopback address → 403; no check, or an expired one → 409; a non-blank target without `ack` →
  409; an expired arm → stopped;
- no route serves store bytes (a GET on any store path → 404)
