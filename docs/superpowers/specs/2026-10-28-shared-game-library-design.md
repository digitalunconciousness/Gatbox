# The game library (the knowledge archive) — design

Status: **draft for the owner's approval, 2026-10-28. No code yet.** Revised the same day after
the owner's rule that **cabcrawl and the Tech Tracker never talk, not even through the deck.**
This is the detailed spec behind §4 of `2026-10-28-gatbox-integration-plan.md`, which comes
first.

Repos: Gatbox (this one), cabcrawl (the barcade's floor app, formerly arcade-tracker),
ArcadeTech-Tracker (the side business, deployed as shop-hub). Each repo's CLAUDE.md still
governs its own code.

The goal: one place, the deck, holds everything the owner has learned about each game (a model,
not a cabinet): manuals (title, kind, source URL, sha256, pages, and the PDF), the MAME set and
ROM list, known-good ROM images, rail limits with the manual page and quote, and facts (fuses,
monitor, PSU). It's for the bench (less searching on the spot) and for the archive (the
knowledge is disappearing). Each app holds the entries for **its own machines' models**, so the
barcade's staff and the partner can read and fix them. The owner enters and fixes entries on
the 7"; the partner reads and fixes them in the Tech Tracker from his phone. The deck works with
no network, and its offline edits wait in an outbox. When two places change the same entry, she
sees both side by side and chooses; nothing is overwritten silently. Machines link to a game by
`model_key`.

---

## 0. The design in one paragraph

**The deck is the archive's home.** It holds every entry for every machine she has ever seen,
PDFs included, in SQLite plus the PDF folder it already has, and it works with no network.
**Each app holds a copy of the entries its own machines use** (by their `model_key`), in its own
database and private file store. The deck syncs with each app **separately**. It pulls the
app's library changes and merges them with its own, then pushes what the app lacks. Every write
is compare-and-set: it names the revision it replaces, and a stale write is refused with the
current version, so nothing anyone typed is replaced unseen. Same-entry conflicts wait on the 7"
for her choice. **Nothing goes from one world to the other on its own:** a change made in one
app reaches the other app's copy only once she adopts it on the deck (decision K in the plan).
The contract change is additive to v1, behind a `library` capability, as Phase 8 added `jobs`.

```
   Tech Tracker (side work)                      cabcrawl (the barcade)
   entries for its assets' models                entries for its games' models
   partner edits (phone)                         read-only for everyone
          ▲   │ changes                                  ▲   │ changes
   CAS PUT│   ▼ GET /api/v1/library/…             CAS PUT│   ▼ GET /api/v1/library/…
          │                                               │
          └──── gatbox-sync: one world at a time ─────────┘  ← never carries one world's
                         ▲   │ the same protocol on loopback   changes to the other unless
                         │   ▼                                 she ADOPTs them on the 7"
                gatbox-web: library.sqlite + /srv/gatbox/manuals
                THE WHOLE ARCHIVE; the 7" edits here; unsent edits = the outbox
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

- Phase 8 lets a hub machine borrow a library entry: `roster.library(slug)` returns the slug
  when it's on the local roster, else the hub roster's `model_key` **if that is a local roster
  slug**, else nothing. A customer's machine therefore gets manuals only if the barcade has
  the same game.
- Housekeeping: BRINGUP.md still shows the Phase 8 exit unchecked, while shop-hub's
  PROGRESS.md records it as passed on 2026-10-27. Worth ticking here once the owner confirms.
- The patterns this design reuses: gatbox-sync is the only process that talks to hubs. It runs
  as its own user, reads gatbox-web over loopback HTTP and imports nothing from it. gatbox-web
  reads sync's state directory read-only. The order outbox belongs to gatbox-web: sync drains
  it with `GET /api/orders`, and gatbox-web learns what went out from sync's sent ledger. Uids
  are minted once, so a re-send comes back `duplicate`. Each hub has its own state directory,
  and routing by machine already keeps each world's traces apart. New behaviour is gated by a
  hub's capabilities. Writes are the Pi's own screen only (`is_local()`).

### 1.2 cabcrawl (the barcade)

- `Game` is **one cabinet**: name, `barcode` (= the roster slug, printed on labels,
  permanent), manufacturer, year, genre, location, status, counters. There's no title/model
  entity, so two cabinets of one game are two unrelated rows.
- Manuals: none stored. The machine page links out to KLOV, with a URL built from the name
  (`services/machines.klov_url`).
- Rail data: `RailSession` / `Reading` exactly as GATBOX computed them (floats). No per-machine
  limits.
- Files: work-order photos live under `static/maintenance_photos`, a public static path. That's
  the wrong model for copyrighted PDFs and ROM images.
- Devices: `gbx_` tokens, each allowed every machine or an allow-list (2026-10-26). Roles go
  readonly < operator < manager < admin. Tests and dev run on SQLite, production on PostgreSQL.
- v1 roster entries: `slug, name, status, location`, with no `model_key`. Its contract test
  asserts the **exact key set** of `roster-response.json`.

### 1.3 shop-hub (the Tech Tracker)

- `Asset` is a customer's machine or board: `tag` (`s-NNNN-name`, permanent, enforced by a
  trigger), name, manufacturer, `model` (free text), and **`model_key`** (String(60),
  `^[a-z0-9]+(-[a-z0-9]+)*$`, commented "slug of the GATBOX manuals library entry"). No table
  stands behind `model_key`: it's a bare string.
- The v1 roster sends `model_key`. Health says `app: "shop-hub"`, `capabilities: ["jobs"]`.
- File store: `SHOP_FILES_DIR/<aa>/<sha256>.<ext>`. It's content-addressed and atomic, never
  served without a login, and archived by `backup.sh`. Photos and signatures only, today.
- PostgreSQL only, `NUMERIC`, `timestamptz`, `created_at/updated_at/created_by`, explicit grants
  per table. Roles: owner / tech / viewer. No game/model entity either.

### 1.4 The contract today

- v1 is owned by cabcrawl and vendored into Gatbox and shop-hub. Each repo checks **its own copy
  against its own CHECKSUMS**.
- **The copies have drifted.** cabcrawl's README has the 2026-10-26 additions: device machine
  lists, and per-item text limits (`order.issue`, `technician`, `note`, `file`). The Gatbox and
  shop-hub copies are the older README, identical to each other. All three tests are green,
  because nothing compares one repo's copy with another's.
- The Phase 8 extras are built in Gatbox and shop-hub but **not written in the contract** text or
  examples: health `app`/`capabilities`, roster `model_key`, orders `work_order`/`nte`, and
  `phase`. `model_key` is in the code and in `docs/PLAN.md`, not in `contract/v1/`.

### 1.5 What follows from this

- Neither app has a "game". The deck's library is the only one, and it's keyed by cabinet slugs.
- `model_key` is the right hook, but today it means "a barcade slug this Pi has".
- Everything the library needs has a pattern already: pull/push with idempotent uids, an
  outbox drained over HTTP, per-hub state, capability gating, a content-addressed private file
  store, and device tokens.

---

## 2. Where the library lives

| copy | holds | why |
|---|---|---|
| **the deck** | **everything**: every model she has seen, from either world or neither, PDFs included | her one place; the bench works offline; the archive |
| cabcrawl | the entries for models its `Game`s use (`Game.model_key`, new) | the barcade's staff read their own machines' manuals in their own app |
| Tech Tracker | the entries for models its `Asset`s use (`Asset.model_key`, exists), plus entries created there | the partner reads and fixes them from his phone |
| off-deck backup | everything (plan §4.4: USB drive and/or a host she names) | an SD card in a cyberdeck is the most fragile place for lost knowledge |

- **Scoping to each app's models is what keeps the worlds apart.** The barcade's copy never
  shows which models turned up on side jobs, and the Tech Tracker's copy never shows the
  barcade's floor.
- The two apps never talk, so neither can be the single source of truth for knowledge both
  use. **The deck is the source of truth for the archive, and each app is the source of truth
  for its own edits until they reach the deck.**
- An edit made in an app always reaches the deck's archive (that's the point of letting the
  partner fix entries). Whether it then reaches the *other* app's copy is decision K: the
  recommended answer is only when she ADOPTs it on the 7" (§5.2).

### What is in the library, and what is not

| in the library (model-level knowledge) | not library (it belongs to one machine, in one world) |
|---|---|
| game: title, maker, year, kind, notes | the cabinet: location, status, counters, the roster's faults/parts/risk (the roster file's schema is frozen) |
| documents and their PDFs | customers, sites, assets, jobs, money |
| MAME set; the ROM list it gives | ROM **dumps** of a machine, and burns: bench records, sent to that machine's world (plan §5) |
| **known-good ROM images:** a hash (and optionally the image) she has marked good for a model and chip, including chips MAME doesn't have | per-machine **actuals** (the deck, keyed by machine slug, as today) |
| rail limits read off a manual page, with page, quote and whether confirmed | the machine → game link: `Game.model_key` / `Asset.model_key`, each owned by its app |
| facts read off a manual (fuses, monitor, PSU) | |

**No library record names a machine, a customer, a site or a world.**

---

## 3. Game identity: `model_key`

Rules (one function, the same in all three repos, with shared test vectors):

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
  key into shop-hub.

**Seeding keeps every link that exists today:** wherever a barcade slug is a game title (almost
all of them, e.g. `gauntlet-legends`), that slug *becomes* the key. Any `model_key` already set
to a barcade slug keeps resolving. Two cabinets of one game, or a slug with a cabinet-specific
part, are mapped to one key in the seed review (§7).

Whoever adds a game (the 7" or the Tech Tracker) mints the key from the title with that rule,
and sees it before saving (a dry run, like ADD MACHINE). If two places mint the same key with
different content, that's a conflict like any other (§5).

---

## 4. The contract: additive in v1

**Recommendation: additive to v1, behind a `library` capability.** The full contract plan,
including drift, ownership and the world extensions, is in plan §8. For the library:

- Nothing existing changes: no request shape, uid rule or endpoint. An old Pi never asks, and a
  hub that doesn't list `library` is never asked.
- "GATBOX pushes, the hub never connects to GATBOX" stays true: the Pi pulls library changes,
  exactly as it pulls rosters and orders today.
- The library protocol is **core** contract (both apps implement the same shape), in
  `library.md`, with examples and `library-vectors.json` (canonical-JSON hashes and `model_key`
  slugs that every repo's test reproduces, like the uid rules).

### 4.1 The library protocol (a sketch; the final wording goes in `library.md`)

These endpoints take the device token. They're offered only when health lists `library` **and**
the device has library access switched on at the hub (a new per-device switch, off by default).

| method | path | |
|---|---|---|
| GET | `/api/v1/library/changes?since=<cursor>&limit=500` | changed records since the cursor, oldest first; `cursor`, `more` |
| PUT | `/api/v1/library/records` | up to 100 writes, each compare-and-set |
| GET | `/api/v1/library/digest` | `{count, digest}` over the hub's copy |
| GET | `/api/v1/files/<sha256>` | a file (PDF or ROM image), `Range` honoured for resume |
| GET | `/api/v1/files/<sha256>/status` | `{have: true}` or `{have: false, parts: [...], size}` |
| PUT | `/api/v1/files/<sha256>/parts/<n>?of=<N>&size=<bytes>&type=<pdf\|rom>` | one part, at most 8 MB |

The file endpoints are shared with ROM dumps (plan §5), so they sit at `/api/v1/files/`, not
under `library/`.

A record in the change feed:

```json
{"kind": "rail_limit", "id": "3f2a…", "rev": 118, "hash": "<sha256 of canonical data>",
 "deleted": false, "at": 1790000000.0, "by": "<who, as that hub shows them>",
 "data": {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25",
          "doc_id": "widget-wars-manual", "page": 2, "quote": "+5 VDC 4.75 to 5.25 V",
          "confirmed": {"by": "gatbox", "at": 1789990000.0}}}
```

A write:

```json
{"contract": "v1", "device": "a1b2c3d4e5f6",
 "records": [{"kind": "rail_limit", "id": "3f2a…", "uid": "<minted once per edit>",
              "if_rev": 117, "at": 1790000100.0, "by": "gatbox", "data": {…}}]}
```

- `if_rev: 0` means create only if absent. `if_rev: n` means replace only if the current
  revision is `n`. Deletes send `deleted: true` the same way.
- **`uid` makes a retry harmless.** The hub keeps the uid that produced each revision, so a
  resend after a lost reply is `duplicate`, not a false conflict.
- Each record gets a result: `created`, `updated`, `duplicate`, `conflict` (with `current`, the
  hub's record; nothing written), or `rejected` (with a reason). A malformed request is a 400
  that writes nothing, as with ingest.
- **Parents first:** a game before its children, and a record that names a sha256 only once the
  hub has that file. Otherwise the record is `rejected` and goes again next run, the same rule
  as "send the session before the order".
- **Writes from a device are attributed to the deck,** never to a person in the other world.
  The `by` a hub sends stays on the deck.

Record kinds (`data`):

| kind | id | data |
|---|---|---|
| `game` | the `model_key` | `title, mfr, year, kind (video\|pinball\|other), notes, replaced_by` |
| `document` | `<model_key>/<doc_id>` | `model_key, doc_id, title, kind (manual\|schematics\|parts\|kit\|bulletin\|other\|yours), source_url, source, sha256, pages, bytes`; `sha256: null` = listed but not fetched yet |
| `mame` | the `model_key` | `set, prefer, status (sure\|check\|null), why` |
| `rom_list` | the `model_key` | `mame` (version), `set`, `versions[{name, parent, description, roms[{name, size, crc, sha1}]}]`. **Written only by a device:** the deck builds it from `mame -listxml`, and people can't edit it in the apps. |
| `rom_image` | uuid | `model_key, chip` (location / label), `size, crc32, sha1, sha256, verdict (good\|bad\|unknown), revision, note, image` (sha256 of the file, or null for hash only) |
| `rail_limit` | uuid | `model_key, rail, lo, hi, doc_id, page, quote, confirmed (null \| {by, at})` |
| `fact` | uuid | `model_key, group (fuse\|monitor\|psu\|power\|other), what, value, doc_id, page, quote` |

- **Numbers:** volts are decimal strings (`"4.75"`), normalized with no trailing zeros. Counts and
  pages are JSON integers. shop-hub parses floats as `Decimal` and cabcrawl as `float`, so a
  number written as a string is the only way all three hash the same bytes.
- **Canonical JSON for `hash`:** sorted keys, no whitespace, UTF-8, NFC strings. The vectors
  file pins the results.
- **Games are never deleted:** they retire with `replaced_by`. Children can be deleted
  (tombstones).
- **Limits:** title 120, notes 2000, quote 500, what 60, value 120, why 500, source 200,
  `source_url` 500 (https), `rom_list` 2 MB. Files: 400 MB (the deck's fetch limit); parts 8 MB,
  under both the ingest body limit and Cloudflare's request-body cap on the tunnels (100 MB on
  the Free and Pro plans; a larger body is a 413 at the edge). Responses aren't capped, so a
  download is one ranged GET.
- **Files** are content-addressed by sha256. When the last part lands, the hub checks the hash,
  the type (`%PDF-` for a PDF) and the size. Parts already received survive across runs.

---

## 5. Sync and conflict handling

### 5.1 On the deck

- **gatbox-web owns the archive:** `/var/lib/gatbox-web/library.sqlite`, written with stdlib
  `sqlite3` in WAL mode. It holds records, the change feed, the uid behind each revision and
  each record's **origin** (the deck, or the world an edit came from). It serves **the same
  protocol on loopback** (`/api/library/changes`, `/records`, `/digest`, `/api/files/…`), with
  writes from the Pi's own screen and loopback only, as for orders and Wi-Fi.
- **The 7"'s edits are local writes, and the outbox is "local changes the world's copy hasn't
  taken yet".** Nothing extra to keep or prune.
- **gatbox-sync syncs each world on its own.** After sessions and orders, a new step runs once
  per hub that lists `library`, between the archive and that hub, never between two hubs. It
  moves PDF bytes over HTTP in 8 MB parts, so it never needs write access to
  `/srv/gatbox/manuals`. **No change to the sync or web unit files is expected.**
- **Sync's state** lives in `/var/lib/gatbox-sync/library/<hub>/`: a cursor; per record, the
  last revision and hash seen; the last agreed content (the *base*, shown in conflicts); and
  `conflicts.json`. A `library` block in `status.json` holds, per hub: waiting to go up, files
  moving, conflicts, digest match. gatbox-web reads all of it read-only.
- **Why SQLite:** a numbered feed, compare-and-set, atomic writes across many records (a
  resolution or the seed touches hundreds), and power-cut safety in a cabinet overnight. It's in
  the standard library: no apt, no pip, no venv. JSON files would need all of that hand-made.
- **The logger never reads it.** After each library change, gatbox-web writes
  `library-specs.json` (confirmed limits by `model_key`, written atomically), and
  `profiles.machine_specs()` reads that as its "confirmed" layer. The logger's inputs stay plain
  JSON files (hard rule 3). Until the library is seeded, every reader keeps using today's files
  through today's code.

### 5.2 The merge, per world, per record, each run

**Scope.** A world's copy gets the records for its models: games its machines name with
`model_key` (from its roster), and every child of those games. Records created in that world
are also in scope. A model that leaves the scope (no machine uses it any more) is no longer
updated there, but it isn't deleted from that copy.

For each record in scope that changed on the deck or in that world since the last run:

- **The world changed it** if its revision is past the one sync last saw there *and* its content
  hash differs.
- **The deck changed it** if the archive's revision is past the one sync last pushed to that
  world.
- **Only the world changed it:** take it into the archive, with origin = that world.
- **Only the deck changed it:** push it to the world with `if_rev` = the revision last seen
  there. **Unless decision K is "automatic", a record whose current origin is the *other* world
  isn't pushed.** It waits on the 7" under ADOPT (below). Once adopted, its origin becomes the
  deck and it goes out.
- **Both changed it, the same way:** nothing to do.
- **Both changed it, differently:** a **conflict**. Nothing is written on either side. Both
  versions are kept, with the base, for the 7".
- A push refused as `conflict` (someone edited between the pull and the push) is treated as a
  newly pulled change and judged again. It's never forced.
- **An unreachable world** is simply skipped. The other world is unaffected, since they never
  depend on each other.
- **First contact:** records only one side has are copied (subject to scope and origin). The
  same id with different content on both sides is a conflict.
- **Deletes** are versions too, so an edit against a delete is a conflict.
- **A second device** syncing the same hub is safe: its stale push is refused, it pulls the newer
  version, finds the same hash, and moves on.
- Conflicts are per entry (one game header, one document, one limit, one fact, one image), as
  the owner asked.

**ADOPT** (decision K, recommended): LIBRARY → FROM THE APPS lists the archive entries whose
latest change came from a world, grouped by world, with what changed. ADOPT makes an entry the
deck's own (origin = deck), so it goes to every world whose scope includes it. Until then,
each world keeps its own version, and the deck shows both.

### 5.3 Conflicts on the 7"

- **SYSTEM gets a Library tile:** per hub label, edits waiting to go up, files moving, open
  conflicts, "identical" or not, and entries waiting for ADOPT. A conflicted entry carries a red
  CONFLICT badge wherever it shows: MACHINE, MANUALS, LIBRARY.
- **LIBRARY → CONFLICTS** shows one card per entry, with columns side by side (BEFORE, THIS DECK,
  and the hub's `HUB_LABEL`): who changed it and when, the fields that differ highlighted, and
  for a limit, the manual page one tap away. **KEEP THIS** under either column; **EDIT** starts
  from either column.
- **Her choice is a local write marked `resolves`**, naming the revision of the side she saw.
  Sync writes it with exactly that `if_rev`. A side that changed again meanwhile makes a new
  conflict and is never overwritten.

### 5.4 Rail limits: what the logger trusts

- The logger uses only a limit with `confirmed` set: CONFIRM, then the window, as today (hard
  rule 12).
- **A change to `lo`, `hi`, `doc_id` or `page` clears `confirmed`.** Every copy enforces this,
  not only the UI: a write that changes one of them and keeps `confirmed` is rejected.
- **Who confirms:** recommended, the 7" only, where she sees the page beside the numbers, as
  today. The Tech Tracker shows a limit as unconfirmed and can add or fix one.
- For a machine, precedence stays Phase 8's: its own actual window > the confirmed library limit
  for its model > user > profile.

### 5.5 Who edits where

- **The 7":** everything, including CONFIRM, ADOPT and resolving conflicts. A phone on the deck
  gets read access plus PDF upload, as today.
- **Tech Tracker:** owner and tech edit; viewer reads. Pages: games, a game (documents, MAME,
  limits, facts, known images, ROM list), the PDF viewer, upload a document. The asset page
  shows its game, and the asset form picks `model_key` from the library its copy holds, or
  proposes a new game.
- **cabcrawl: read-only for every role, recommended:** barcade staff shouldn't change the
  owner's archive, and the deck is then its only writer. The protocol allows edits there later
  without change.

---

## 6. PDF (and ROM image) storage

- **One file per sha256 in each copy;** records point at the hash. ROM images use the same file
  store and endpoints (plan §5).
- **Tech Tracker:** its existing file store, `SHOP_FILES_DIR/<aa>/<sha256>.<ext>`. It's already
  content-addressed and atomic, never served without a login, and backed up. A file goes only
  to a logged-in user (`Cache-Control: private`, `X-Robots-Tag: noindex`), never through a `/d/`
  customer link.
- **cabcrawl:** a new `LIBRARY_FILES_DIR` **outside `static/`**: `instance/library/` in
  development (`instance/` is git-ignored) and `/var/lib/arcade-tracker/library` on the server
  (the old name stays, per its rules). It's served only by a `login_required` route, never by
  `/report/<token>` or any page without a login, and it's added to its backup.
- **The deck:** `/srv/gatbox/manuals/<model_key>/<doc_id>.pdf` plus the sidecar, the layout it
  has now. The seed keys equal today's slugs, so the folders are already in the right place.
  gatbox-web writes them (it already can), and sync streams the bytes through gatbox-web's
  loopback file endpoints. A PDF dropped into the folder by hand still works: gatbox-web finds
  a PDF with no record, as it finds one with no sidecar today, and offers ADD TO LIBRARY.
- **Archives:** `gatbox-manuals fetch` keeps working. It fills documents that have a
  `source_url` and no bytes (the "not fetched" list). The record then gets its sha256, and the
  file goes up to the worlds whose scope includes it.
- **Git: never.** Gatbox already ignores `*.pdf` and `*.bin`. Each app adds its file folder to
  its ignore file and its privacy check.
- **Transfers:** resumable 8 MB parts. Sessions and orders go first in every run; file parts get
  what's left of the 240 s budget, so a big first upload spans many runs without holding up a
  trace.
- **Space:** unknown today. `gatbox-manuals list` prints the total, and the owner can run it.
  Each app stores only its own models' files.

---

## 7. Seeding: the deck's current library, once

A new CLI, **`gatbox-library`**, run as the owner (group `gatbox-manuals`, like
`gatbox-manuals`).

1. **`gatbox-library seed --plan`** (read-only) reads every source in the table below and writes
   a plan to the owner's home directory, never into git. The plan lists each proposed
   `model_key` (= the slug), flags slugs that look like the same game, and prints counts.
2. The owner edits the plan where needed: two cabinets to one key, or a slug with a
   cabinet-specific part.
3. **`gatbox-library seed --apply PLAN`** writes the records into the archive as the deck's own
   changes (origin = deck), in one transaction. Re-running it with the same plan changes
   nothing.
4. **Linking machines:** cabcrawl gets `Game.model_key` (new, nullable), set by a one-off script
   from the plan (slug → key) in the style of its `apply_roster_map.py` (dry run, review,
   apply), plus a field managers can edit. Tech Tracker assets already carry `model_key`.
5. **The next sync** with each hub that lists `library` sends the files, then the records
   (`if_rev: 0`), **for that world's models only.** An empty hub gets everything `created`. A hub
   that already has entries goes through the normal merge.

| today | becomes |
|---|---|
| roster name, maker, kind (`video_games` / `pinball`) | `game` title, mfr, kind |
| `why` / `note` in the manuals list | `game` notes |
| manuals list `docs` + sidecars in `/srv/gatbox/manuals` (hand-dropped PDFs: sha256 and page count computed, kind `yours`) | `document` (+ the file) |
| `specs.rails` | `rail_limit`, `confirmed` where `machine-specs-confirmed.json` matches its doc, page, lo and hi |
| `gatbox-machine-specs.json` (in git: one machine, cited "per the owner") | `rail_limit`, confirmed, its source line as the quote |
| `specs.sheet` | `fact` |
| `gatbox-mame-sets.json` / `mame-roms.json` | `mame` / `rom_list` |
| dumps in `/srv/gatbox/roms` | stay bench records (plan §5); she can mark any of them known-good → `rom_image` |
| `machine-actuals.json` | **not library:** stays per machine |

After the seed, **the archive is the source.** The git-ignored list files are read only by
`gatbox-library import FILE`, which can be re-run for updates prepared off the deck. Every
record an import changes becomes a local edit, so it goes through the same conflict rules.
`gatbox-mame-roms` builds from the archive's `mame` records and writes `rom_list` records back.

---

## 8. What changes in each repo

**Gatbox**
- `backend/gatboxweb/library.py`: the archive store and the loopback protocol. `manuals.py`,
  `mame.py` and the spec sheet read through it. Every existing URL stays
  (`/api/manuals/<slug>`, `/api/specs/<slug>`, `/api/mame/<slug>`, confirm, upload: rule 14).
- `roster.library(slug)`: the `model_key` from the machine's hub roster if the archive has it,
  else the slug if the archive has a game by that key, else nothing.
- The dashboard's **LIBRARY** view (games, a game's documents, MAME, known images, limits, facts;
  ADD GAME; CONFLICTS; FROM THE APPS), built under the existing `html.box` rules (one keyboard,
  pickers as buttons, the bezel margin).
- gatbox-sync: the library step per hub, its status, `GATBOX_SYNC_DRYRUN` printing the merge
  plan.
- `gatbox-library` (seed, import). `gatbox-manuals` and `gatbox-mame-roms` read the archive.
- **System config, shown before install (rule 5):** rebuilding a ROM list when a set changes
  needs `mame -listxml` (a minute or two of CPU), which today runs only at install. Proposed:
  a `gatbox-mame-roms.path` / `.service` pair that watches a request file gatbox-web writes,
  like `gatbox-dump.path`.
- Tests:
  - `test-library.py`: store, compare-and-set, feed, a retried uid comes back `duplicate`,
    origin, the `confirmed` rule.
  - `test-sync.sh`, against two stub hubs: scope, one side changed, both the same way, a
    conflict, an unreachable hub, a lost reply, edit against delete, a resumed file, digests.
    **A test that nothing from one stub hub is ever sent to the other without ADOPT.**
  - `test-dash.py`: LIBRARY, CONFLICTS, FROM THE APPS.
  - `test-contract.sh`: the vectors.
  - The raillog tests: the window from `library-specs.json`, and today's behaviour without it.

**cabcrawl:** library tables + feed + Alembic migration (registered in
`scripts/check_schema.py`), the API, `Device.library`, `Game.model_key` and roster
`model_key`. Read-only pages: the game list, a game, the PDF viewer behind login, and
"Game: …" on the machine page. The link script, the characterization snapshot, and
screenshots at 390 and 1280.

**Tech Tracker:** the same tables in PostgreSQL (`grant_app()`, `EXPECTED_GRANTS`), the API,
`Device.library`. Mobile-first library pages with editing for owner and tech, the asset page's
game, and the asset form's `model_key` picker. Files go in the file store. Tests against a
throwaway Postgres, plus the privacy check.

---

## 9. Order of work

See plan §9: the archive on the deck first (phase B, Gatbox only, offline), then ROM images to
their worlds (C), then this sync (D).

---

## 10. Decisions specific to the library

| # | question | recommended |
|---|---|---|
| L1 | Can people edit the library in cabcrawl? | **No, read-only for every role;** the deck is its only writer. |
| L2 | Who can CONFIRM a rail limit (the step that feeds the logger)? | **The 7" only.** The Tech Tracker can add or fix limits, which arrive unconfirmed. |
| L3 | `model_key` naming | **Title slug** (`pin-` for pinball, maker/year suffix on a clash), permanent, seeded from the barcade slugs. |
| L4 | Which devices may sync the library? | **A per-device switch at each hub, off by default.** |
| L5 | Library edits from a phone on the deck? | **No:** read access and PDF upload as today; edits on the 7". |

Decision K, whether knowledge crosses between the worlds, is in the plan (§10).

---

## 11. Risks

1. **The deck is the archive's home.** An SD card that dies takes whatever hasn't been backed up
   or synced. Mitigations: each world's copy holds its own models, plus the off-deck backup
   (plan §4.4). A fresh deck rebuilds from the backup, then from the worlds.
2. **A merge bug could cost data.** Mitigations: every write is compare-and-set, nothing writes
   blind, and a refusal is never forced. The merge rules get their own tests, and the first real
   sync runs in dry-run mode.
3. **Leaking between worlds:** scope by each world's own models, origin plus ADOPT, and
   attribution to the deck. A test asserts nothing crosses.
4. **Big transfers over home Wi-Fi and tunnels:** resumable parts inside the run budget, after
   sessions and orders.
5. **Clocks:** the deck's `at` may be unverified. Ordering never depends on time, only on
   revisions; times are shown for information.
6. **Copyright:** files only behind a login in each app, on no public route, and never in git.
7. **Phase 8 stays as it is:** a deck whose hubs don't list `library` behaves as today, and an
   unseeded deck reads today's files through today's code.
