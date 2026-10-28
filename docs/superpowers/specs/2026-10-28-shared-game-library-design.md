# The game library: the deck and the archive — design

Status: **draft for the owner's approval, 2026-10-28. No code yet.** Third revision, the same
day:
1. cabcrawl and the Tech Tracker never talk, not even through the deck;
2. **the knowledge gets its own home, a container on the owner's Proxmox node: the archive.**

This is the detailed spec behind §4 of `2026-10-28-gatbox-integration-plan.md`, which comes
first.

The goal: everything the owner learns about each game (a model, not a cabinet) is kept in one
place that's hers, and the deck carries a full copy to the bench. That covers:
- manuals (title, kind, source URL, sha256, pages, and the PDF);
- the MAME set and ROM list;
- known-good ROM images, and every image she dumps;
- rail limits with the manual page and quote;
- facts (fuses, monitor, PSU).

It's for the bench (less searching on the spot) and for the archive (the knowledge is
disappearing). She enters and fixes entries on the 7"; her partner reads and fixes them from
his phone. The deck works with no network, and its offline edits wait in an outbox. When two
places change the same entry, she sees both side by side and chooses; nothing is overwritten
silently. Machines link to a game by `model_key`.

---

## 0. The design in one paragraph

**The archive is the library's home:** a small always-on server in its own container on her
Proxmox node, belonging to neither business. **The deck keeps a full copy**, PDFs included, so
the bench works offline. The two stay in step through one two-way sync. Every write is
compare-and-set, and same-entry conflicts wait on the 7" for her choice. Her partner uses the
archive's own pages on his phone, behind a login. **The apps hold no library at all:** a
machine page (cabcrawl) or an asset page (the Tech Tracker) shows the game's key and a link to
the archive's page. The link opens in the person's browser, and nothing passes between an app
and the archive. So the two worlds share nothing, and the archive is reached only by people
and the deck.

```
   cabcrawl (the barcade)       Tech Tracker (side work)       links only: a machine's game key
   Game.model_key ─ link ─┐     Asset.model_key ─ link ─┐      opens the archive's page in the
                          ▼                              ▼      person's browser; no app ever
              THE ARCHIVE (an LXC on her Proxmox node)          sends it anything
              whole library + every PDF + every ROM image
              Cloudflare Access: her + partner (phone); the deck by token
                          ▲   │  library protocol (compare-and-set, changes feed, files)
                          │   ▼
              THE DECK: gatbox-sync ↔ gatbox-web library.sqlite + /srv/gatbox/manuals
              a full offline copy; the 7" edits here; unsent edits = the outbox
```

---

## 1. Recon: how each app stores machines and games today

### 1.1 GATBOX

The library exists only here, keyed by **barcade roster slug**, and a roster slug names a
cabinet.

| what | where | keyed by | written by |
|---|---|---|---|
| title, maker, kind | barcade roster (git-ignored) + `roster-added.json` (state) | roster slug | the owner (data of record shared with the maintenance app); ADD MACHINE |
| document list | `data/gatbox-manuals.json` (git-ignored, installed to `/usr/local/share/gatbox`): `docs[{id,title,kind,url,sha256,source}]`, `specs{rails[],sheet[]}`, `why`, `note` | slug | by hand / Claude sessions; `gatbox-manuals fetch --pin` |
| PDFs | `/srv/gatbox/manuals/<slug>/<id>.pdf` + `.json` sidecar (title, kind, url, source, sha256, pages, bytes, added, date) + `.txt` | slug / doc id | `gatbox-manuals fetch` (the owner), gatbox-web upload (phone or 7"), dropped in by hand (no sidecar) |
| MAME set | `data/gatbox-mame-sets.json` (git-ignored): `{set, prefer, status: sure\|check\|none, why}` | slug | by hand |
| ROM list | `/usr/local/share/gatbox/mame-roms.json`, built at install by `gatbox-mame-roms` from `mame -listxml` | slug | the bootstrap |
| ROM dumps | `/srv/gatbox/roms/<machine>/<label>_<sha1:8>.bin` + sidecar (part, label, size, sha1, crc32, `mame -romident` result, reads compared); `burns.jsonl` | machine slug | `gatbox-dump` (read twice, identify, archive) |
| rail limits | three layers in `profiles.machine_specs()`: `gatbox-machine-specs.json` (in git, one machine) < `machine-specs-confirmed.json` (state: CONFIRM of a list entry) < `machine-actuals.json` (state: per machine) | slug | CONFIRM on the dashboard; actuals typed or from the meter |
| facts | `specs.sheet[{what,value,doc,page,quote}]` in the manuals list; per-machine actual facts in `machine-actuals.json` | slug | by hand; dashboard |

- **Size on 2026-10-28** (the owner ran `gatbox-manuals list` and `du`): **1.13 GB of manuals**
  (about 12 MB per game on average) and **under 1 MB of dumps** (one or two chips so far). Both
  will grow; see §6.
- Phase 8 lets a hub machine borrow a library entry: `roster.library(slug)` returns the slug
  when it's on the local roster, else the hub roster's `model_key` **if that is a local roster
  slug**, else nothing. A customer's machine therefore gets manuals only if the barcade has
  the same game.
- Housekeeping: BRINGUP.md still shows the Phase 8 exit unchecked, while shop-hub's
  PROGRESS.md records it as passed on 2026-10-27. Worth ticking here once the owner confirms.
- The patterns this design reuses: gatbox-sync is the only process that talks off the box. It
  reads gatbox-web over loopback HTTP and imports nothing from it, and gatbox-web reads its
  state directory read-only. Outboxes drain over HTTP, uids are minted once, and a re-send comes
  back `duplicate`. Writes are the Pi's own screen only (`is_local()`).

### 1.2 cabcrawl (the barcade)

- `Game` is **one cabinet**: name, `barcode` (= the roster slug, printed on labels, permanent),
  manufacturer, year, genre, location, status, counters. There's no title/model entity.
- Manuals: none stored. The machine page links out to KLOV, with a URL built from the name.
- Devices: `gbx_` tokens, each allowed every machine or an allow-list. Tests and dev run on
  SQLite, production on PostgreSQL.
- v1 roster entries: `slug, name, status, location`, with no `model_key`. Its contract test
  asserts the **exact key set** of `roster-response.json`.

### 1.3 shop-hub (the Tech Tracker)

- `Asset` is a customer's machine or board: a permanent `tag` (`s-NNNN-name`), name,
  manufacturer, free-text `model`, and **`model_key`** (String(60), slug format, "slug of the
  GATBOX manuals library entry"; no table behind it). The v1 roster sends it.
- File store: `SHOP_FILES_DIR/<aa>/<sha256>.<ext>`, content-addressed, login-only, backed up.

### 1.4 The contract today

- v1 is owned by cabcrawl and vendored into Gatbox and shop-hub. **The copies have drifted:**
  cabcrawl has the 2026-10-26 additions, and the other two don't. Each repo's test checks only
  its own copy.
- The Phase 8 extras (health `app`/`capabilities`, roster `model_key`, orders `work_order`/`nte`,
  `phase`) are built but **not written in the contract**.

### 1.5 What follows from this

- Neither app has a "game", and neither needs one: a link by `model_key` is enough.
- `model_key` is the right hook, but today it means "a barcade slug this Pi has".

---

## 2. Where the library lives

| copy | holds | role |
|---|---|---|
| **the archive** (an LXC on her Proxmox node) | **everything**: every model she has seen from either world or neither, every PDF, every ROM image | **the source of truth.** Always on, backed up with her Proxmox backups, hers alone |
| **the deck** | **everything**, a full copy | offline at the bench; the 7" is where she enters and fixes things |
| cabcrawl, the Tech Tracker | **nothing**: a machine's or asset's `model_key`, and a link | they show the game and send people to the archive |

### 2.1 The archive

- **What it runs: the deck's own library code.** `archive/` in the Gatbox repo runs the same
  store and protocol as gatbox-web's library module: stdlib Python, SQLite and files on disk,
  under a systemd unit. The deck and the archive therefore can't disagree about the format,
  and the code is written once. An idempotent `archive/install.sh` (like the bootstrap) sets
  it up. **The owner runs it in the container; agents never touch the host.**
- **The container:** a Debian 13 LXC, unprivileged, 1–2 cores, 1 GB RAM. The data sits on its
  own mount point (start at about 50 GB and grow it in Proxmox when needed) and is included in
  her Proxmox backups.
- **Reaching it:** a Cloudflare tunnel, like the two apps.
  - **People** come through **Cloudflare Access**: her and her partner, by email login. The free
    plan covers teams under 50 users.
  - **The deck** carries an Access **service token** plus the archive's own device token
    (`gbx_` format, hashed, as in the apps).
  - The archive listens only on localhost, with `cloudflared` in the same container, so nothing
    reaches it without passing Access.
  - The exact Access settings (application, policies, the identity header the archive reads for
    "who changed this") are checked against Cloudflare's documentation when it's built.
- **Its pages:** the deck's LIBRARY pages, served by the archive, in synthwave like the deck,
  and phone-first for her partner. They cover games, a game (documents and the PDF viewer, MAME
  and the ROM list, known images, limits, facts), search, upload a PDF, add a game, and edit.
  CONFIRM and conflicts stay on the 7" (§5.3, §5.4).
- **Never talks to either app.** It's reached by people's browsers and by the deck, nothing
  else.

### 2.2 The apps: a link, nothing more

- cabcrawl gains `Game.model_key` (nullable, set by a one-off script from the seed plan, and
  editable by managers). The Tech Tracker already has `Asset.model_key`.
- Each app has one setting, the archive's address. Its machine or asset page shows **"Game:
  `<model_key>` → open in the library"**, linking to `<archive>/g/<model_key>`, and keeps
  cabcrawl's KLOV link as it is. Who can open the library is decided by Cloudflare Access: her
  and her partner, plus anyone else she adds later.
- Nothing is stored in or sent from an app. **That is how "never talk" holds for knowledge.**

### 2.3 What is in the library, and what is not

| in the library (knowledge, hers) | not library (it belongs to one machine, in one world) |
|---|---|
| game: title, maker, year, kind, notes | the cabinet: location, status, counters, the roster's faults/parts/risk (the roster file's schema is frozen) |
| documents and their PDFs | customers, sites, assets, jobs, money |
| MAME set; the ROM list it gives | the machine's **dump record** (which machine, which socket, when, for which order or job), sent to its world (plan §5) |
| **ROM images, by hash:** every image she dumps, and the ones she marks known-good (or bad) for a game and chip, including chips MAME doesn't have | per-machine **actuals** (the deck, keyed by machine slug, as today) |
| rail limits read off a manual page, with page, quote and whether confirmed | the machine → game link (`Game.model_key` / `Asset.model_key`), each owned by its app |
| facts read off a manual (fuses, monitor, PSU) | |

**No library record names a machine, a customer, a site or a world.** A ROM image is stored by
its hash, so a game's code is kept without who owned the board. That's why the archive gets
every image: game code is the knowledge most at risk of being lost, and the barcade's copy
isn't hers to keep.

---

## 3. Game identity: `model_key`

Rules (one function in the deck and the archive, with test vectors in the protocol):

- `^[a-z0-9]+(-[a-z0-9]+)*$`, at most **60** characters (shop-hub's column; the deck's slug
  check allows 64).
- Made from the title the way the roster makes slugs (`roster.slugify`): lower-case words
  joined by hyphens, apostrophes dropped, `&` read as `and`, accents folded. Pinball keys start
  with `pin-`, the roster's own convention.
- **One key per game, not per cabinet or version.** Revisions, regions and kits of the same game
  share a key; MAME's versions live inside its `mame` record. Two different games that share a
  title get the maker as a suffix, then the year if that's still not enough:
  `spider-man-sega` against `pin-spider-man`.
- **Permanent.** The title can change; the key never does, like slugs and asset tags. A mistaken
  duplicate is retired with `replaced_by: <survivor>`, and every reader follows it.
- No machine, place or customer in a key. `unassigned` is reserved (captures and dumps use it).

Alternatives considered:

- **MAME short names:** pinball, PC-based games and newer games have none. They also name
  versions rather than games, and the partner can't read `gauntleg` on a phone. They stay as
  data in the `mame` record, and they're how the deck *suggests* a key (plan §4.2).
- **UUIDs:** the Phase 8 roster and the 7" already use readable slugs, and the partner types the
  key into the Tech Tracker.

**Seeding keeps every link that exists today:** wherever a barcade slug is a game title (almost
all of them, e.g. `gauntlet-legends`), that slug *becomes* the key. Any `model_key` already set
to a barcade slug keeps resolving. Two cabinets of one game, or a slug with a cabinet-specific
part, are mapped to one key in the seed review (§7).

---

## 4. The protocol: Gatbox's own, not in the apps' contract

Only the deck and the archive speak it, and both live in this repo. So it's **not** part of
the contract vendored into the apps (plan §8). It sits in `docs/contract/library-v1/`, with
examples and `library-vectors.json` (canonical-JSON hashes and `model_key` slugs both sides'
tests reproduce).

### 4.1 Endpoints (the archive; gatbox-web serves the same on loopback)

They take the device token (and, at the archive, the Access service token in front).

| method | path | |
|---|---|---|
| GET | `/api/v1/library/changes?since=<cursor>&limit=500` | changed records since the cursor, oldest first; `cursor`, `more` |
| PUT | `/api/v1/library/records` | up to 100 writes, each compare-and-set |
| GET | `/api/v1/library/digest` | `{count, digest}` over the whole library |
| GET | `/api/v1/files/<sha256>` | a file (PDF or ROM image), `Range` honoured for resume |
| GET | `/api/v1/files/<sha256>/status` | `{have: true}` or `{have: false, parts: [...], size}` |
| PUT | `/api/v1/files/<sha256>/parts/<n>?of=<N>&size=<bytes>&type=<pdf\|rom>` | one part, at most 8 MB |

The file endpoints have the same shape as the ones the apps get for dumps (plan §5), so the
deck has one uploader.

A record in the change feed:

```json
{"kind": "rail_limit", "id": "3f2a…", "rev": 118, "hash": "<sha256 of canonical data>",
 "deleted": false, "at": 1790000000.0, "by": "<who: the 7\", or the Access user>",
 "data": {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25",
          "doc_id": "widget-wars-manual", "page": 2, "quote": "+5 VDC 4.75 to 5.25 V",
          "confirmed": {"by": "gatbox", "at": 1789990000.0}}}
```

A write:

```json
{"device": "a1b2c3d4e5f6",
 "records": [{"kind": "rail_limit", "id": "3f2a…", "uid": "<minted once per edit>",
              "if_rev": 117, "at": 1790000100.0, "by": "gatbox", "data": {…}}]}
```

- `if_rev: 0` means create only if absent. `if_rev: n` means replace only if the current
  revision is `n`. Deletes send `deleted: true` the same way.
- **`uid` makes a retry harmless.** The archive keeps the uid that produced each revision, so a
  resend after a lost reply is `duplicate`, not a false conflict.
- Each record gets a result: `created`, `updated`, `duplicate`, `conflict` (with `current`;
  nothing written), or `rejected` (with a reason). A malformed request is a 400 that writes
  nothing.
- **Parents first:** a game before its children, and a record that names a sha256 only once the
  other side has that file.

Record kinds (`data`):

| kind | id | data |
|---|---|---|
| `game` | the `model_key` | `title, mfr, year, kind (video\|pinball\|other), notes, replaced_by` |
| `document` | `<model_key>/<doc_id>` | `model_key, doc_id, title, kind (manual\|schematics\|parts\|kit\|bulletin\|other\|yours), source_url, source, sha256, pages, bytes`; `sha256: null` = listed but not fetched yet |
| `mame` | the `model_key` | `set, prefer, status (sure\|check\|null), why` |
| `rom_list` | the `model_key` | `mame` (version), `set`, `versions[{name, parent, description, roms[{name, size, crc, sha1}]}]`. **Written only by the deck,** which builds it from `mame -listxml`. |
| `rom_image` | the image's sha256 | `size, crc32, sha1, label` (as printed on the chip), `part`, `romident` (MAME's answer), `model_key` (when known), `chip` (location, when known), `verdict (good\|bad\|unknown)`, `revision, note`. The image itself is the file with that hash. |
| `rail_limit` | uuid | `model_key, rail, lo, hi, doc_id, page, quote, confirmed (null \| {by, at})` |
| `fact` | uuid | `model_key, group (fuse\|monitor\|psu\|power\|other), what, value, doc_id, page, quote` |

- **Numbers:** volts are decimal strings (`"4.75"`), normalized with no trailing zeros. Counts and
  pages are JSON integers.
- **Canonical JSON for `hash`:** sorted keys, no whitespace, UTF-8, NFC strings.
- **Games are never deleted:** they retire with `replaced_by`. Children can be deleted
  (tombstones).
- **Limits:** title 120, notes 2000, quote 500, what 60, value 120, why 500, source 200,
  `source_url` 500 (https), `rom_list` 2 MB. Files: 400 MB (the deck's fetch limit); parts 8 MB,
  under Cloudflare's request-body cap on the tunnels (100 MB on the Free and Pro plans).
  Responses aren't capped, so a download is one ranged GET.

---

## 5. Sync and conflict handling

### 5.1 On the deck

- **gatbox-web owns the deck's copy:** `/var/lib/gatbox-web/library.sqlite` (stdlib `sqlite3`,
  WAL) for records, the feed and uids, plus the PDF folder it already has. It serves the same
  protocol on loopback, with writes from the Pi's own screen and loopback only.
- **The 7"'s edits are local writes; the outbox is "local changes the archive hasn't taken
  yet".**
- **gatbox-sync runs a library step** after sessions and orders, against the archive configured
  in **`/etc/gatbox/archive.conf`** (`ARCHIVE_URL`, `ARCHIVE_TOKEN`, the Access service-token
  pair). It's kept apart from `hubs.d`, so Phase 8's routing never sees it. It moves file bytes
  in 8 MB parts through gatbox-web's loopback file endpoints, so it never needs write access to
  `/srv/gatbox/manuals`. **The new config file means a change to the sync unit's credentials,
  shown before install (rule 5).**
- **Sync's state** lives in `/var/lib/gatbox-sync/library/`: a cursor; per record, the last
  revision and hash seen on each side; the last agreed content (the *base*, shown in
  conflicts); and `conflicts.json`. A `library` block in `status.json` holds edits waiting to go
  up, files moving, conflicts and digest match. gatbox-web reads all of it read-only.
- **Why SQLite:** a numbered feed, compare-and-set, atomic writes across many records, and
  power-cut safety in a cabinet overnight. It's in the standard library. The archive uses the
  same code.
- **The logger never reads it.** After each library change, gatbox-web writes
  `library-specs.json` (confirmed limits by `model_key`), and `profiles.machine_specs()` reads
  that as its "confirmed" layer. Until the library is seeded, every reader keeps using today's
  files through today's code (hard rule 3).

### 5.2 The merge: two copies, per record, each run

- **The archive changed it** if its revision is past the one sync last saw there *and* its hash
  differs. **The deck changed it** if its revision is past the one sync last pushed.
- **Only the archive changed it:** take it onto the deck. **Only the deck changed it:** push it,
  with `if_rev` = the revision last seen there.
- **Both changed it, the same way:** nothing to do. **Both changed it, differently:** a
  **conflict**. Nothing is written on either side, and both versions and the base wait on the
  7".
- A push refused as `conflict` (her partner edited between the pull and the push) is treated as
  a newly pulled change and judged again. It's never forced.
- **The archive out of reach:** nothing happens; the deck keeps working, and the outbox grows.
- **First contact** (an empty archive, or a fresh deck): records only one side has are copied.
  The same id with different content on both sides is a conflict.
- **Deletes** are versions too, so an edit against a delete is a conflict.

### 5.3 Conflicts on the 7"

- **SYSTEM gets a Library tile:** edits waiting to go up, files moving, open conflicts, and
  "identical" (the digests match) or not. A conflicted entry carries a red CONFLICT badge
  wherever it shows.
- **LIBRARY → CONFLICTS:** one card per entry, with columns side by side (BEFORE, THIS DECK,
  THE ARCHIVE: who changed it and when, e.g. her partner by his Access login), the fields that
  differ highlighted, and a limit's manual page one tap away. **KEEP THIS** under either
  column, or **EDIT** starting from either.
- **Her choice is a local write marked `resolves`**, naming the archive revision she saw. If the
  archive changed again meanwhile, that's a new conflict, never overwritten.

### 5.4 Rail limits: what the logger trusts

- The logger uses only a limit with `confirmed` set (hard rule 12).
- **A change to `lo`, `hi`, `doc_id` or `page` clears `confirmed`,** enforced by the store on
  both sides.
- **CONFIRM happens on the 7" only,** where she sees the page beside the numbers. In the archive
  her partner can add or fix a limit, which arrives unconfirmed.
- For a machine, precedence stays Phase 8's: its own actual window > the confirmed library limit
  for its model > user > profile.

---

## 6. Files: PDFs and ROM images, and room to grow

- **One file per sha256** on each side; records point at the hash.
- **The deck:** `/srv/gatbox/manuals/<model_key>/<doc_id>.pdf` plus the sidecar (today's
  layout; the seed keys equal today's slugs). ROM images stay in `/srv/gatbox/roms/` as
  `gatbox-dump` writes them; the `rom_image` record points at the hash. A PDF dropped into the
  folder by hand still works: gatbox-web offers ADD TO LIBRARY.
- **The archive:** `<data>/files/<aa>/<sha256>.<ext>` on its own mount point, served only
  behind Access.
- **Git: never.** Gatbox already ignores `*.pdf` and `*.bin`.
- **Room to grow.** Today it's 1.13 GB of manuals (about 12 MB per game) and under 1 MB of dumps.
  Both will grow:
  - manuals for a few hundred games is several GB;
  - a board's ROMs are typically 0.5–8 MB, so even hundreds of boards is a few GB.
  - **Plan for about 20 GB over the next years.**
  - The archive's mount point starts at about 50 GB and grows in Proxmox.
  - **The deck:** the SYSTEM tile shows the library's size and the free space on `/srv`, and
    warns when it's low. `df -h /srv` on the Pi says where it stands today. If the deck ever
    runs short, an option for later is to keep every record but only some PDFs on the deck,
    and fetch the rest from the archive on demand. Nothing in this design rules that out.
- **Transfers:** resumable 8 MB parts after sessions and orders in each run, inside the 240 s
  budget. The first upload of 1.13 GB spans many runs without holding up a trace.

---

## 7. Seeding: the deck's current library, once

A new CLI, **`gatbox-library`**, run as the owner (group `gatbox-manuals`):

1. **`gatbox-library seed --plan`** (read-only) reads every source in the table below and writes
   a plan to the owner's home directory, never into git. The plan lists each proposed
   `model_key` (= the slug), flags slugs that look like the same game, and prints counts.
2. The owner edits the plan where needed: two cabinets to one key, or a slug with a
   cabinet-specific part.
3. **`gatbox-library seed --apply PLAN`** writes the records into the deck's copy in one
   transaction. Re-running it changes nothing.
4. **The first sync** with an empty archive sends every file, then every record (`if_rev: 0`).
5. **Linking machines:** cabcrawl's `Game.model_key` is set by a one-off script from the plan
   (slug → key). Tech Tracker assets already carry `model_key`.

| today | becomes |
|---|---|
| roster name, maker, kind (`video_games` / `pinball`) | `game` title, mfr, kind |
| `why` / `note` in the manuals list | `game` notes; the "not in the archives" notes feed LIBRARY → MISSING |
| manuals list `docs` + sidecars in `/srv/gatbox/manuals` (hand-dropped PDFs: sha256 and page count computed, kind `yours`) | `document` (+ the file) |
| `specs.rails` | `rail_limit`, `confirmed` where `machine-specs-confirmed.json` matches its doc, page, lo and hi |
| `gatbox-machine-specs.json` (in git: one machine, cited "per the owner") | `rail_limit`, confirmed, its source line as the quote |
| `specs.sheet` | `fact` |
| `gatbox-mame-sets.json` / `mame-roms.json` | `mame` / `rom_list` |
| dumps in `/srv/gatbox/roms` | `rom_image` (by hash), and dump records for their machine's world (plan §5) |
| `machine-actuals.json` | **not library:** stays per machine |

After the seed, **the library is the source.** The git-ignored list files are read only by
`gatbox-library import FILE`, which can be re-run for updates prepared off the deck; every
record an import changes is a local edit. `gatbox-mame-roms` builds from the `mame` records
and writes `rom_list` records back.

---

## 8. What changes in each repo

**Gatbox**
- `backend/gatboxweb/library.py`: the store and the protocol, shared by the deck (loopback) and
  the archive. `manuals.py`, `mame.py` and the spec sheet read through it. Every existing URL
  stays.
- `roster.library(slug)`: the `model_key` from the machine's hub roster if the library has it,
  else the slug if the library has a game by that key, else nothing.
- The LIBRARY view (games, a game's documents, MAME, images, limits, facts; ADD GAME; MISSING;
  CONFLICTS), under the existing `html.box` rules on the 7", and phone-first at the archive.
- `archive/`: the server (the same library module behind a small stdlib HTTP server, bound to
  localhost), `archive/install.sh`, its systemd unit, and a RUNBOOK. The Proxmox and Cloudflare
  steps are written as exact commands for the owner, marked ⏸.
- gatbox-sync: the library step, `/etc/gatbox/archive.conf`, status, and `GATBOX_SYNC_DRYRUN`
  printing the merge plan.
- `gatbox-library` (seed, import). `gatbox-manuals` and `gatbox-mame-roms` read the library.
- **System config, shown before install (rule 5):**
  - the sync unit loads `archive.conf` as a credential;
  - a `gatbox-mame-roms.path` / `.service` pair rebuilds a ROM list when a set changes.
- Tests:
  - `test-library.py`: store, compare-and-set, feed, retried uid, the `confirmed` rule;
  - `test-sync.sh`: against a stub archive (the real `archive/` server on a temp port), one
    side changed, both the same way, a conflict, out of reach, a lost reply, edit against
    delete, a resumed file, digests;
  - `test-dash.py`: LIBRARY and CONFLICTS;
  - `test-contract.sh`: the library vectors;
  - the raillog tests: the window from `library-specs.json`, and today's behaviour without it.

**cabcrawl:** `Game.model_key` (Alembic, registered in `check_schema.py`), the archive-address
setting, the "Game: … → open in the library" line on the machine page, the link script, and
screenshots.

**Tech Tracker:** the archive-address setting and the same line on the asset page. The
`model_key` column exists.

---

## 9. Order of work

See plan §9:
1. The library on the deck (phase B, Gatbox only, offline).
2. Dumps and burns to their worlds (C).
3. The archive and its sync (D).

---

## 10. Decisions

| # | question | decided / recommended |
|---|---|---|
| S | Where does knowledge live? | **Decided (the owner, 2026-10-28): a container on her Proxmox node.** This spec's answer to "the best way": the archive above, running the deck's own library code. |
| L1 | Where does the archive's code live? | **Recommended: in the Gatbox repo (`archive/`),** since it's the deck's library made always-on. A fourth repo would mean a fourth copy of the rules and checks. |
| L2 | Who can open the archive? | **Recommended: her and her partner** (Cloudflare Access, by email). Barcade coworkers only if she adds them. |
| L3 | Who can CONFIRM a rail limit? | **The 7" only.** |
| L4 | `model_key` naming | **Title slug** (`pin-` for pinball, maker/year suffix on a clash), permanent, seeded from the barcade slugs. |
| L5 | Library edits from a phone on the deck's own Wi-Fi? | **No:** read access and PDF upload as today. Phone editing is what the archive is for. |

Decision K from the earlier draft (ADOPT between the worlds) is gone: the apps hold no
knowledge, so there's nothing to cross.

---

## 11. Risks

1. **A merge bug could cost data.** Mitigations: compare-and-set everywhere, no blind writes,
   refusals never forced, tests on the real archive server, and the first sync in dry-run mode.
   Proxmox backups of the archive, plus the deck's own copy, are two independent copies.
2. **The archive reachable from the internet** through the tunnel. Cloudflare Access in front,
   a service token plus a device token for the deck, and localhost-only behind `cloudflared`.
   Copyrighted files are never public.
3. **Growth** on the deck's SD card: watched on SYSTEM; the partial-PDF option is kept open.
4. **Big transfers over home Wi-Fi and the tunnel:** resumable parts inside the run budget,
   after sessions and orders.
5. **Clocks:** ordering never depends on time, only on revisions.
6. **Phase 8 stays as it is:** with no `archive.conf`, the deck behaves as today, and an
   unseeded deck reads today's files through today's code.
