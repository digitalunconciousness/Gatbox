# One game library for both apps — design

Status: **draft for the owner's approval, 2026-10-28. No code yet.**
Repos: Gatbox (this one), cabcrawl (the floor app, formerly arcade-tracker), ArcadeTech-Tracker
(the tech hub, deployed as shop-hub). Each repo's CLAUDE.md still governs its own code.

The goal: for a game (a model, not a cabinet), both apps hold the same entry: manuals (title,
kind, source URL, sha256, pages, and the PDF), MAME set and ROM list, rail limits with the manual
page and quote, and facts (fuses, monitor, PSU). The owner enters and fixes entries on the 7";
her partner reads and fixes the same entries on the tech tracker from his phone. The gatbox keeps
a full offline copy, PDFs included. Its offline edits wait in an outbox. When two places change
the same entry, she sees both side by side and chooses; nothing is overwritten silently.
Customer machines link to a game by `model_key`.

---

## 0. The design in one paragraph

Each app gets a full copy of the library in its own database, with the PDFs in its own private
file store. **The apps never talk to each other.** The Pi already holds a token for each, so it
relays: every sync run pulls each app's changes since the last run, merges them with the 7"'s
own edits, and pushes the result to whichever copy lacks it. Every write to a copy is
compare-and-set: it names the revision it replaces, and a stale write is refused with the
current version. So nothing anyone typed can be replaced unseen. When two places changed the
same entry, nothing moves and the 7" shows both. The Pi keeps its own full copy (SQLite, plus
the PDF folder it already has), so it works with no network. Its edits that haven't gone up yet
are the outbox. The contract change is additive to v1, behind a `library` capability, exactly
as Phase 8 added `jobs`.

```
   tech tracker (shop-hub)                     floor app (cabcrawl)
   library tables + PDFs                       library tables + PDFs
   partner edits (phone)                       read-only for everyone
          ▲   │ changes feed                          ▲   │ changes feed
   CAS PUT│   ▼ GET /api/v1/library/…          CAS PUT│   ▼ GET /api/v1/library/…
          │                                            │
          └──────────── gatbox-sync (the Pi) ──────────┘   ← the only merger; calls out only
                              ▲   │ the same protocol on loopback
                              │   ▼
                     gatbox-web: library.sqlite + /srv/gatbox/manuals
                     the 7" edits here (offline OK); unsent edits = the outbox
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
  are minted once, so a re-send comes back `duplicate`. New behaviour is gated by a hub's
  capabilities. Writes are the Pi's own screen only (`is_local()`).

### 1.2 cabcrawl (the floor app)

- `Game` is **one cabinet**: name, `barcode` (= the roster slug, printed on labels,
  permanent), manufacturer, year, genre, location, status, counters. There's no title/model
  entity, so two cabinets of one game are two unrelated rows.
- Manuals: none stored. The machine page links out to KLOV, with a URL built from the name
  (`services/machines.klov_url`).
- Rail data: `RailSession` / `Reading` exactly as GATBOX computed them (floats). No per-machine
  limits.
- Files: work-order photos live under `static/maintenance_photos`, a public static path. That's
  the wrong model for copyrighted PDFs.
- Devices: `gbx_` tokens, each allowed every machine or an allow-list (2026-10-26). Roles go
  readonly < operator < manager < admin. Tests and dev run on SQLite, production on PostgreSQL.
- v1 roster entries: `slug, name, status, location`, with no `model_key`. Its contract test
  asserts the **exact key set** of `roster-response.json`.

### 1.3 shop-hub (the tech tracker)

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
  `phase`. The brief says `model_key` is "already in contract v1". In practice it's in the code
  and in `docs/PLAN.md`, not in `contract/v1/`.

### 1.5 What follows from this

- Neither app has a "game". The Pi's library is the only one, and it's keyed by cabinet slugs.
- `model_key` is the right hook, but today it means "a barcade slug this Pi has".
- Everything the library needs has a pattern already: pull/push with idempotent uids, an
  outbox drained over HTTP, capability gating, a content-addressed private file store, and
  device tokens.

---

## 2. Where the library lives, and how the copies stay identical

**Every copy holds the whole library:** cabcrawl's database and file store, shop-hub's database
and file store, and the Pi (SQLite plus `/srv/gatbox/manuals`).

Ways to keep the two apps identical:

| | how | verdict |
|---|---|---|
| A | one app is primary; the other copies it over a server-to-server link | **No.** The apps are separate on purpose (shop-hub's PLAN: "Why not inside the tracker"): different hosts, different tunnels. It would need app-to-app credentials, and a release of one could break the other. |
| B | one app holds the library and the other reads it live | **No.** Same coupling, and barcade staff would depend on the side business's server, or the other way round. |
| C | **the Pi relays** | **Recommended.** "The gatbox is the tool connected to both": it already holds a token for each, and it already calls out to both. No new network path: neither hub ever calls the other, or the Pi. |

**The cost of C:** an edit on the tech tracker reaches cabcrawl only through the Pi's next sync
while it's online (every two minutes when the Pi has a network). If the Pi stays off for a
week, cabcrawl's copy is a week behind. For a library that changes a few times a week, that's
acceptable, and nothing is lost meanwhile: the change waits in the tech tracker's change feed.
The protocol also tolerates a second device relaying (§5.2), so a later Pi doesn't need a
redesign.

**How "identical" is kept and checked.** Each copy is a plain store with two properties:

1. **A change feed.** Every write gets the next revision number, so "what changed since N" is
   one query.
2. **Compare-and-set writes.** A write names the revision it replaces (`if_rev`). If the record
   has moved on since, the write is refused with the current version.

gatbox-sync remembers, per copy and per record, the last revision it saw. That's the version
vector, held in one place. §5 describes the merge. To *prove* the copies match, each copy also
serves a **digest**: a sha256 over every record's kind, id and content hash. The SYSTEM tile
shows "library identical" when all digests agree, and lists the records that differ when they
don't.

### What is in the library, and what is not

| in the library (model-level) | stays where it is (machine-level) |
|---|---|
| game: title, maker, year, kind, notes | the cabinet: location, status, counters, the roster's faults/parts/risk (the roster file's schema is frozen) |
| documents and their PDFs | customers, sites, assets, jobs (shop-hub only) |
| MAME set; the ROM list it gives | per-machine **actuals** (the Pi, keyed by machine slug, as today) |
| rail limits read off a manual page, with page, quote and whether confirmed | the machine → game link: `Game.model_key` (cabcrawl, new) and `Asset.model_key` (shop-hub, exists), each owned by its app |
| facts read off a manual (fuses, monitor, PSU) | |

**No library record names a machine, a customer or a site.** That's what lets the same library
sit in both apps.

---

## 3. Game identity: `model_key`

Rules (one function, the same in all three repos, with shared test vectors):

- `^[a-z0-9]+(-[a-z0-9]+)*$`, at most **60** characters (shop-hub's column; the Pi's slug check
  allows 64).
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
  data in the `mame` record.
- **UUIDs:** the Phase 8 roster and the 7" already use readable slugs, and the partner types the
  key into shop-hub.

**Seeding keeps every link that exists today:** wherever a barcade slug is a game title (almost
all of them, e.g. `gauntlet-legends`), that slug *becomes* the key. Any `model_key` already set
to a barcade slug keeps resolving. Two cabinets of one game, or a slug with a cabinet-specific
part, are mapped to one key in the seed review (§7).

Whoever adds a game (the 7" or the tech tracker) mints the key from the title with that rule,
and sees it before saving (a dry run, like ADD MACHINE). If two places mint the same key
offline with different content, that's a conflict like any other (§5).

---

## 4. The contract change: additive in v1

**Recommendation: additive to v1, behind a `library` capability.**

- Nothing existing changes: no request shape, uid rule or endpoint. An old Pi never asks, and a
  hub that doesn't list `library` is never asked. This is the mechanism Phase 8 used for `jobs`.
- A v2 would mean running two contracts side by side through a transition, with nothing to
  migrate. Save v2 for the day an existing shape has to change.
- "GATBOX pushes, the hub never connects to GATBOX" stays true: the Pi pulls library changes,
  exactly as it pulls rosters and orders today.

The contract work, landed byte-identical in all three PRs:

1. **Fix the drift:** re-vendor cabcrawl's 2026-10-26 README into Gatbox and shop-hub.
2. **Write down what's already built:** health `app` and `capabilities`, roster `model_key`,
   orders `work_order` / `nte`, and `phase` on `rail_session` / `session_tag`. Add `model_key`
   to `roster-response.json` (one entry with a key, one with `null`), and add it to cabcrawl's
   roster, which then sends `Game.model_key`.
3. **New `library.md`**, with examples: `library-changes-response.json`,
   `library-put-request.json`, `library-put-response.json`, `library-file-status.json`, and
   `library-vectors.json` (canonical-JSON hashes and `model_key` slugs that every repo's test
   reproduces, like the uid rules).
4. Regenerate CHECKSUMS.

**Ownership.** The README says the hub owns v1. shop-hub's PLAN already recommends moving
ownership to Gatbox at the next real change, "because GATBOX is the one client that every hub
serves". The library protocol is defined by the relay, so: **Gatbox owns `contract/v1` from
here, and the apps vendor it** (decision 4). To catch drift, each repo's contract test also
diffs its copy against sibling checkouts when they're present (`../cabcrawl`, `../Gatbox`, …)
and skips with a note when they aren't. Every contract PR states that the three copies match.

### 4.1 The library protocol (a sketch; the final wording goes in `library.md`)

These endpoints take the device token. They're offered only when health lists `library` **and**
the device has library access switched on at the hub (a new per-device switch, off by default).

| method | path | |
|---|---|---|
| GET | `/api/v1/library/changes?since=<cursor>&limit=500` | changed records since the cursor, oldest first; `cursor`, `more` |
| PUT | `/api/v1/library/records` | up to 100 writes, each compare-and-set |
| GET | `/api/v1/library/digest` | `{count, digest}` over the whole library |
| GET | `/api/v1/library/files/<sha256>` | the PDF, `Range` honoured for resume |
| GET | `/api/v1/library/files/<sha256>/status` | `{have: true}` or `{have: false, parts: [...], size}` |
| PUT | `/api/v1/library/files/<sha256>/parts/<n>?of=<N>&size=<bytes>` | one part, at most 8 MB |

A record in the change feed:

```json
{"kind": "rail_limit", "id": "3f2a…", "rev": 118, "hash": "<sha256 of canonical data>",
 "deleted": false, "at": 1790000000.0, "by": "<who, as the hub shows them>",
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
- **Parents first:** a game before its children, and a document that names a sha256 only once
  the hub has that file. Otherwise the record is `rejected` and goes again next run, the same
  rule as "send the session before the order".

Record kinds (`data`):

| kind | id | data |
|---|---|---|
| `game` | the `model_key` | `title, mfr, year, kind (video\|pinball\|other), notes, replaced_by` |
| `document` | `<model_key>/<doc_id>` | `model_key, doc_id, title, kind (manual\|schematics\|parts\|kit\|bulletin\|other\|yours), source_url, source, sha256, pages, bytes`; `sha256: null` = listed but not fetched yet |
| `mame` | the `model_key` | `set, prefer, status (sure\|check\|null), why` |
| `rom_list` | the `model_key` | `mame` (version), `set`, `versions[{name, parent, description, roms[{name, size, crc, sha1}]}]`. **Written only by a device:** the Pi builds it from `mame -listxml`, and people can't edit it in the apps. |
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
  `source_url` 500 (https), `rom_list` 2 MB. Files: 400 MB (the Pi's fetch limit); parts 8 MB,
  under both the ingest body limit and Cloudflare's request-body cap on the tunnels (100 MB on
  the Free and Pro plans; a larger body is a 413 at the edge). Responses aren't capped, so a
  download is one ranged GET.
- **Files** are content-addressed by sha256. When the last part lands, the hub checks the hash,
  the `%PDF-` header and the size. Parts already received survive across runs.

---

## 5. Sync and conflict handling

### 5.1 On the Pi

- **gatbox-web owns the Pi's copy:** `/var/lib/gatbox-web/library.sqlite`, written with stdlib
  `sqlite3` in WAL mode. It holds records, the change feed and the uid behind each revision,
  with exactly a hub's semantics. It serves **the same protocol on loopback**
  (`/api/library/changes`, `/records`, `/digest`, `/files/…`), with writes from the Pi's own
  screen and loopback only, as for orders and Wi-Fi.
- **The 7"'s edits are local writes, and the outbox is "local changes after sync's last
  cursor".** Nothing extra to keep or prune: it empties as sync pushes.
- **gatbox-sync relays.** After sessions and orders, a new step replicates across the local copy
  and every hub that lists `library`, using one code path for all of them. It moves PDF bytes
  between copies over HTTP in 8 MB parts, so it never needs write access to
  `/srv/gatbox/manuals`. **No change to the sync or web unit files is expected.**
- **Sync's state** lives in `/var/lib/gatbox-sync/library/`: a cursor per copy; per copy and
  record, the last revision and hash seen; the last agreed content (the *base*, shown in
  conflicts); `conflicts.json`; and a `library` block in `status.json` (waiting to go up, per
  hub; files moving; conflicts; digests matching). gatbox-web reads all of it read-only, as it
  reads the rest of sync's state today.
- **Why SQLite:** a numbered feed, compare-and-set, atomic writes across many records (a
  resolution or the seed touches hundreds), and power-cut safety in a cabinet overnight. It's in
  the standard library: no apt, no pip, no venv. JSON files would need all of that hand-made.
- **The logger never reads it.** After each library change, gatbox-web writes
  `library-specs.json` (confirmed limits by `model_key`, written atomically), and
  `profiles.machine_specs()` reads that as its "confirmed" layer. The logger's inputs stay plain
  JSON files (hard rule 3). Until the library is seeded, every reader keeps using today's files
  through today's code.

### 5.2 The merge, per record, each run

For each record that changed anywhere since the last run:

- A copy **changed it** if its revision is past the one sync last saw there *and* its content
  hash differs from what sync saw.
- **Nobody changed it:** nothing to do.
- **One change**, or several identical ones (same hash): write it to every other copy, with
  `if_rev` = the revision last seen there.
- **Two or more different changes:** a **conflict**. Nothing is written for that record anywhere.
  Each side's version and revision is kept, with the base, for the 7".
- A push refused as `conflict` (someone edited between the pull and the push) is treated as a
  newly pulled change and judged again. It's never forced.
- **An unreachable copy** is skipped this run, and its "last seen" stays put. When it comes
  back, its own edits made in the meantime are judged against what everyone else did: an edit
  of a version that has since moved on is a conflict, as it should be.
- **First contact** with a copy: records only it has are copied everywhere. The same id with
  different content on two sides (one key created twice, say) is a conflict.
- **Deletes** are versions too, so an edit against a delete is a conflict.
- **A second device** relaying the same hubs is safe: its stale push is refused, it pulls the
  newer version, finds the same hash, and moves on.
- Conflicts are per entry (one game header, one document, one limit, one fact), as the owner
  asked. Merging disjoint field changes inside an entry could come later; it isn't needed for
  entries this small.

### 5.3 Conflicts on the 7"

- **SYSTEM gets a Library tile:** edits waiting to go up (per hub label), files moving, open
  conflicts, and "identical" or not. A conflicted entry carries a red CONFLICT badge wherever it
  shows: MACHINE, MANUALS, LIBRARY.
- **LIBRARY → CONFLICTS** shows one card per entry, with columns side by side (BEFORE, THIS PI,
  and each hub's `HUB_LABEL`): who changed it and when, the fields that differ highlighted, and
  for a limit, the manual page one tap away. **KEEP THIS** under any column; **EDIT** starts
  from any column.
- **Her choice is a local write marked `resolves`**, naming the revision of each side she saw.
  Sync writes it to those copies with exactly those `if_rev`s. A side that changed again
  meanwhile makes a new conflict and is never overwritten.
- The partner sees the outcome on his next page load after the Pi's sync. The apps don't hold
  conflicts. Each one just keeps showing its own version until the resolution lands.

### 5.4 Rail limits: what the logger trusts

- The logger uses only a limit with `confirmed` set: CONFIRM, then the window, as today (hard
  rule 12).
- **A change to `lo`, `hi`, `doc_id` or `page` clears `confirmed`.** Every copy enforces this,
  not only the UI: a write that changes one of them and keeps `confirmed` is rejected.
- **Who confirms:** recommended, the 7" only, where she sees the page beside the numbers, as
  today. The tech tracker shows a limit as unconfirmed and can add or fix one (decision 3).
- For a machine, precedence stays Phase 8's: its own actual window > the confirmed library limit
  for its model > user > profile.

### 5.5 Who edits where

- **The 7":** everything, including CONFIRM and resolving conflicts. A phone on the Pi gets
  read access plus PDF upload, as today (decision 8).
- **shop-hub:** owner and tech edit; viewer reads. Pages: games, a game (documents, MAME,
  limits, facts, ROM list), the PDF viewer, upload a document. The asset page shows its game,
  and the asset form picks `model_key` from the library.
- **cabcrawl: read-only for every role, recommended** (decision 2): barcade staff shouldn't
  change a library the side business depends on, and the Pi is then its only writer. The
  protocol allows edits there later without change.

---

## 6. PDF storage

- **One file per sha256 in each copy;** records point at the hash.
- **shop-hub:** its existing file store, `SHOP_FILES_DIR/<aa>/<sha256>.pdf`. It's already
  content-addressed and atomic, never served without a login, and backed up. A PDF goes only to
  a logged-in user (inline, `Cache-Control: private`, `X-Robots-Tag: noindex`), never through a
  `/d/` customer link.
- **cabcrawl:** a new `LIBRARY_FILES_DIR` **outside `static/`**: `instance/library/` in
  development (`instance/` is git-ignored) and `/var/lib/arcade-tracker/library` on the server
  (the old name stays, per its rules). It's served only by a `login_required` route, never by
  `/report/<token>` or any page without a login, and it's added to its backup.
- **The Pi:** `/srv/gatbox/manuals/<model_key>/<doc_id>.pdf` plus the sidecar, the layout it
  has now. The seed keys equal today's slugs, so the folders are already in the right place.
  gatbox-web writes them (it already can), and sync streams the bytes through gatbox-web's
  loopback file endpoints. A PDF dropped into the folder by hand still works: gatbox-web finds
  a PDF with no record, as it finds one with no sidecar today, and offers ADD TO LIBRARY.
- **Archives:** `gatbox-manuals fetch` keeps working. It fills documents that have a
  `source_url` and no bytes (the "not fetched" list). The record then gets its sha256, and the
  file goes up.
- **Git: never.** Gatbox already ignores `*.pdf`. Each app adds the library folder to its
  ignore file and its privacy check.
- **Transfers:** resumable 8 MB parts. Sessions and orders go first in every run; file parts get
  what's left of the 240 s budget, so a big first upload spans many runs without holding up a
  trace.
- **Space:** unknown today. `gatbox-manuals list` prints the total, and the owner can run it.
  Each app stores the whole set, and shop-hub's backups grow by that much.

---

## 7. Seeding: the Pi's current library into the apps, once

A new CLI, **`gatbox-library`**, run as the owner (group `gatbox-manuals`, like
`gatbox-manuals`).

1. **`gatbox-library seed --plan`** (read-only) reads every source in the table below and writes
   a plan to the owner's home directory, never into git. The plan lists each proposed
   `model_key` (= the slug), flags slugs that look like the same game, and prints counts.
2. The owner edits the plan where needed: two cabinets to one key, or a slug with a
   cabinet-specific part.
3. **`gatbox-library seed --apply PLAN`** writes the records into the Pi's copy as local changes,
   in one transaction. Re-running it with the same plan changes nothing.
4. **The next sync** with a hub that lists `library` sends the files first, then the records
   (`if_rev: 0`). An empty hub gets everything `created`. A hub that already has entries (the
   partner started early) goes through the normal merge: the same key with different content
   is a conflict on the 7".
5. **Linking machines:** shop-hub assets already carry `model_key`. cabcrawl gets
   `Game.model_key` (new, nullable), set by a one-off script from the plan (slug → key) in the
   style of its `apply_roster_map.py` (dry run, review, apply), plus a field managers can edit.

| today | becomes |
|---|---|
| roster name, maker, kind (`video_games` / `pinball`) | `game` title, mfr, kind |
| `why` / `note` in the manuals list | `game` notes |
| manuals list `docs` + sidecars in `/srv/gatbox/manuals` (hand-dropped PDFs: sha256 and page count computed, kind `yours`) | `document` (+ the file) |
| `specs.rails` | `rail_limit`, `confirmed` where `machine-specs-confirmed.json` matches its doc, page, lo and hi |
| `gatbox-machine-specs.json` (in git: one machine, cited "per the owner") | `rail_limit`, confirmed, its source line as the quote |
| `specs.sheet` | `fact` |
| `gatbox-mame-sets.json` / `mame-roms.json` | `mame` / `rom_list` |
| `machine-actuals.json` | **not library:** stays per machine |

After the seed, **the library is the source.** The git-ignored list files are read only by
`gatbox-library import FILE`, which can be re-run for updates prepared off the Pi. Every record
an import changes becomes a local edit, so it goes through the same conflict rules.
`gatbox-mame-roms` builds from the library's `mame` records and writes `rom_list` records back.

---

## 8. What changes in each repo

**Gatbox**
- The contract (§4).
- `backend/gatboxweb/library.py`: the store and the loopback protocol.
  `manuals.py`, `mame.py` and the spec sheet read through it. Every existing URL stays
  (`/api/manuals/<slug>`, `/api/specs/<slug>`, `/api/mame/<slug>`, confirm, upload: rule 14).
- `roster.library(slug)`: the `model_key` from any hub roster if the library has it, else the
  slug if the library has a game by that key, else nothing.
- The dashboard's **LIBRARY** view (games, a game's documents, MAME, limits, facts; ADD GAME;
  CONFLICTS), built under the existing `html.box` rules (one keyboard, pickers as buttons, the
  bezel margin).
- gatbox-sync: the library step, its status, `GATBOX_SYNC_DRYRUN` printing the merge plan.
- `gatbox-library` (seed, import). `gatbox-manuals` and `gatbox-mame-roms` read the library.
- **System config, shown before install (rule 5):** rebuilding a ROM list when a set changes
  needs `mame -listxml` (a minute or two of CPU), which today runs only at install. Proposed:
  a `gatbox-mame-roms.path` / `.service` pair that watches a request file gatbox-web writes,
  like `gatbox-dump.path`. That's the only new unit.
- Tests:
  - `test-library.py`: store, compare-and-set, feed, a retried uid comes back `duplicate`,
    the `confirmed` rule.
  - `test-sync.sh`, against stub hubs: one side changed, both changed the same way, a
    conflict, an unreachable copy, a lost reply, edit against delete, a resumed file, digests.
  - `test-dash.py`: LIBRARY and CONFLICTS.
  - `test-contract.sh`: the vectors.
  - The raillog tests: the window from `library-specs.json`, and today's behaviour without it.

**cabcrawl**
- Library tables + feed + Alembic migration (registered in `scripts/check_schema.py`), the API,
  `Device.library`, `Game.model_key` and roster `model_key`.
- Read-only pages: the game list, a game, the PDF viewer behind login, and "Game: …" on the
  machine page.
- The link script, the characterization snapshot, and screenshots at 390 and 1280.

**shop-hub**
- The same tables in PostgreSQL (`grant_app()`, `EXPECTED_GRANTS`), the API, `Device.library`.
- Mobile-first library pages with editing for owner and tech, the asset page's game, and the
  asset form's `model_key` picker. PDFs go in the file store.
- Tests against a throwaway Postgres, plus the privacy check.

---

## 9. Order of work

1. **The owner approves this design** and the decisions in §10. (Here now.)
2. **Contract, all three repos together** (§4). It's the first commit of each repo's PR.
3. **Gatbox, offline first:** the local copy, the loopback protocol, `gatbox-library
   seed/import`, and the readers switched over. Useful and testable with no hub at all.
4. **shop-hub:** the store, the API and the pages. The partner can use it as soon as it's
   deployed, with or without the Pi.
5. **cabcrawl:** the store, the API, read-only pages, `Game.model_key`, roster `model_key`.
6. **Gatbox, the relay:** the sync step, CONFLICTS, the SYSTEM tile, all tested against stub
   hubs that implement the contract.
7. **The owner, on hardware:** deploys shop-hub and cabcrawl (migrations; library access
   switched on for the Pi's device), installs Gatbox through the bootstrap (the new unit shown
   first), runs `seed --plan`, reviews, runs `--apply`, and watches the first sync in dry-run
   mode, then for real.

**One PR per repo:** Gatbox (this design + steps 2, 3, 6), shop-hub (2, 4), cabcrawl (2, 5).
Merge either app first; each advertises `library` only once deployed, and an old Pi ignores
it. Gatbox merges last. Each repo's own gate still applies:
- **shop-hub:** a PROGRESS.md phase with its design note and the owner's OK, then the diff,
  the tests and the privacy-check output before every push.
- **cabcrawl:** after each step, the diff stat, the tests, and screenshots at 390 and 1280.

**Blocker in this cloud session:** shop-hub's privacy check needs `patterns.local`, which its
setup script writes and which isn't in this environment. Its CLAUDE.md says never to push
without it, so the shop-hub PR waits until the owner adds it to this environment (or pushes
from her own checkout).

---

## 10. Decisions for the owner

| # | question | recommended |
|---|---|---|
| 1 | How do the two apps stay identical? | **The Pi relays** (§2 C). No app-to-app link; cabcrawl may lag while the Pi is offline. |
| 2 | Can people edit the library in cabcrawl? | **No, read-only for every role;** the Pi is its only writer. |
| 3 | Who can CONFIRM a rail limit (the step that feeds the logger)? | **The 7" only.** The tech tracker can add or fix limits, which arrive unconfirmed. |
| 4 | Contract: additive v1 or v2, and who owns it? | **Additive v1** behind `library`; **ownership moves to Gatbox**; fix the drift first. |
| 5 | `model_key` naming | **Title slug** (`pin-` for pinball, maker/year suffix on a clash), permanent, seeded from the barcade slugs. |
| 6 | Full PDF copies in both apps? | Yes, per the brief. Please run `gatbox-manuals list` so the space is known. |
| 7 | Which devices may sync the library? | **A per-device switch at each hub, off by default.** |
| 8 | Library edits from a phone on the Pi? | **No:** read access and PDF upload as today; edits on the 7". |
| 9 | Link a machine to its game from the 7" (a new ingest item that sets the hub's `model_key`)? | **Later, not this round:** links are set in each app. |

---

## 11. Risks

1. **One relay.** cabcrawl lags while the Pi is offline (§2). An SD card that dies takes
   unsynced 7" edits with it, as it would unsent orders today. The Pi syncs every two minutes
   when it has a network, and a fresh Pi rebuilds its copy with a full pull.
2. **A merge bug could cost data.** Mitigations: every write is compare-and-set, nothing writes
   blind, and a refusal is never forced. The merge rules get their own tests, and the first real
   sync runs in dry-run mode.
3. **Big transfers over home Wi-Fi and tunnels:** resumable parts inside the run budget, after
   sessions and orders.
4. **Clocks:** the Pi's `at` may be unverified. Ordering never depends on time, only on
   revisions; times are shown for information.
5. **Copyright:** PDFs only behind a login in each app, on no public route, and never in git.
6. **Free text** (notes, `by`) may name people. It lives only in the databases and on the Pi,
   never in git. Tests and examples use invented names (Widget Wars, `example.com`).
7. **Phase 8 stays as it is:** a Pi whose hubs don't list `library` behaves as today, and an
   unseeded Pi reads today's files through today's code.
