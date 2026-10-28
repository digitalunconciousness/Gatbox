# The deck, the barcade, the side work and the archive — the integration plan

Status: **draft for the owner's approval, 2026-10-28. No code yet.** Revised the same day with
her decisions (§11). This is the top-level plan. The detailed specs hang off it:
- the library and the archive (`2026-10-28-shared-game-library-design.md`);
- one set of agent rules (`2026-10-28-agent-rules-draft.md`);
- one look with four themes (`2026-10-28-one-look-four-themes.md`).

Each later phase gets its own short spec when it starts.

---

## 1. What the owner wants (from her description, 2026-10-28)

- **cabcrawl** is for the barcade where she works, and is barcade-only. It started as play
  counts, to decide which floor games to swap with a big warehouse library, then grew into
  maintenance and inventory. Not in service yet. **She wants that functionality in the box,**
  including adding coin totals from the deck.
- **ArcadeTech-Tracker** (the Tech Tracker, deployed as shop-hub) is for side jobs she and the
  other tech take: customers, jobs, the customers' machines, parts. **She wants that
  functionality in the box,** without customer data on it.
- **The deck (GATBOX)** is a Pi-powered cyberdeck becoming a complete arcade diagnostic machine:
  multimeter, T48 ROM programmer, screen, scanner. Meter readings and ROM rips are tied to the
  machines she works on.
- **One detailed record of everything:** for the bench (as little searching on the spot as
  possible) and for the archive (arcade knowledge is being lost).
- **cabcrawl and the Tech Tracker never talk.**
- **A PDF for every machine she sees,** ROM hashes compared on the spot, and ROM images sent back
  to the source of truth.
- **Her knowledge in a container of its own** on the Proxmox node where both apps already run.
- **One set of agent rules** for the repos, with a single OK to push.

---

## 2. The shape: two worlds, one deck, one archive

```
        THE BARCADE                                         THE SIDE WORK
   cabcrawl (floor app)                              Tech Tracker (shop-hub)
   machines · plays · rotation                       customers · assets · jobs
   work orders · parts · stock                       parts · money
          ▲   (a machine page links to the archive)          ▲  (an asset page links to it)
          │ its own token, outbox, cache, contract part      │ its own token, outbox, cache, contract part
          └─────────────────────── THE DECK ─────────────────┘
                  meter · T48 · scanner · 7" · works offline
                  a full copy of the library · every reading and dump it made
                                      ▲  │ library sync
                                      │  ▼
                  THE ARCHIVE: an LXC on her Proxmox node, hers alone
                  every manual, every ROM image, every limit and fact
                  ── nothing passes from one world to the other ──
```

There are three kinds of data, and each has one rule:

| kind | examples | source of truth | on the deck | crosses between worlds? |
|---|---|---|---|---|
| **business** | machines, work orders / jobs, customers, parts and stock, plays, rotation, money | the world's app | a working cache of that world, plus an outbox of **actions** | **never** |
| **bench records** | meter sessions, dump and burn records, readings, coin counts | the app of the machine's world (the deck makes them, sends them, keeps its own copy) | everything it ever made | **never**: a record belongs to one machine |
| **knowledge** | manuals and PDFs, MAME sets and hashes, ROM images (by hash), rail limits, facts | **the archive** | a full copy | **nothing to cross:** the apps hold none, only a link |

- **Business data flows as actions, not as copies of state.** The deck sends "log 30 min on
  order 41", "used 2 × fuse from shelf A3 on order 41", "coin meter 48,213 on Widget Wars".
  Each action has a uid minted once, and the app checks it, applies it once and answers per
  item, as ingest does today. The deck keeps a read-only cache so it can show and scan offline.
  The app's rules stay the app's.
- **Knowledge** lives in the archive; the deck keeps a full copy in step with it (§4).
- **Bench records flow one way, to their machine's world,** and come back down only to restore
  a deck (§5).

---

## 3. On the deck: two realms, kept apart

- **Each hub is a realm.** Everything the deck stores that came from a hub or is going to one is
  stamped with exactly one realm when it's made: a cached record, an outbox entry, a session,
  a dump record, a burn. **The sender refuses an item whose realm isn't the destination's**, and
  a test with two stub hubs asserts nothing ever crosses.
- **A machine is in exactly one realm,** by roster. A slug on both rosters is already a conflict
  that goes nowhere.
- **A scanned code is resolved per realm.** cabcrawl's labels (`<base>/g/<slug>`, `/i/<code>`,
  `/p/<code>`, `/u/<code>`) are looked up in that realm's cache. The Tech Tracker has no printed
  labels: its machines are picked from its group.
- **The deck is often on the barcade's Wi-Fi with an open web server** (it lives in cabinets
  overnight). So:
  - **no customer data on the deck** (decided). A job shows the asset, its tag, the complaint
    and the work; her phone has the rest;
  - **side-work screens and APIs are 7"-only** (`is_local()`), as job details are today;
  - **money stays off the deck,** apart from the NTE string the JOB card shows today.
- **The deck acts as her** in each app, and each app's history shows "via GATBOX" (decided).
- A deck PIN for the side-work realm: not now; revisit with the enclosure.

---

## 4. Knowledge: the archive and the deck's copy (phases B and D)

The full spec is `2026-10-28-shared-game-library-design.md`. In short:

### 4.1 The archive

- **A small always-on server in its own LXC** on her Proxmox node, belonging to neither
  business. **It runs the deck's own library code** (`archive/` in this repo: stdlib Python,
  SQLite and files, a systemd unit), so the deck and the archive can't disagree about the
  format. She installs it with an idempotent script; the Proxmox and Cloudflare steps are
  written out for her as exact commands.
- **Reached through a Cloudflare tunnel**, like the apps:
  - Cloudflare Access in front for people: her and her partner, by email login (free under 50
    users);
  - an Access service token plus a device token for the deck;
  - the archive listens only on localhost.
- **Her partner reads and fixes entries on his phone** in the archive's own pages (synthwave).
  CONFIRM of rail limits and conflict choices stay on the 7".
- **The apps hold no knowledge.** A machine or asset page shows the game's key and a link to the
  archive, and Access decides who gets in. Nothing passes between an app and the archive.
- **Backups:** the archive is in her Proxmox backups, and the deck holds a second full copy. A
  USB copy (`gatbox-backup`) is optional now.

### 4.2 The deck's copy

- **A full copy, offline:** `library.sqlite` plus `/srv/gatbox/manuals`. The 7" is where she
  edits, and the outbox holds edits the archive hasn't taken yet. Two-way sync with
  compare-and-set; same-entry conflicts side by side on the 7"; nothing overwritten silently.
- **Entries:** game (keyed by `model_key`, a permanent title slug), documents (+ PDF), MAME set,
  ROM list (built on the deck from `mame -listxml`), ROM images by hash (every dump, plus her
  known-good marks), rail limits (page, quote, confirmed or not) and facts.
- **First fill:** `gatbox-library seed --plan` builds it from today's files, she reviews the
  keys, and then `--apply`. The first sync fills the archive.

### 4.3 A PDF for every machine she sees

When she picks or scans a machine with no game, or a game with no manuals:

1. **Which game?** An offline title search over MAME's own list (description, maker, year for
   every set: a small index built once per MAME version) suggests a game and its `model_key`.
   Pinball and games MAME doesn't have are typed.
2. **Link it:** on the deck for a THIS PI machine; in its app for a world's machine (linking from
   the deck is later).
3. **Manuals:**
   - **online:** an archive.org search, offered as candidates and fetched by `gatbox-manuals`
     through a request file and a path unit, like dumps (a new unit, shown first);
   - **otherwise:** a phone upload (exists), or her partner uploads it to the archive and it
     comes down on sync;
   - **LIBRARY → MISSING** lists every game she has met without a manual. Today's list already
     has a handful, each with the reason (not in the public archives, only on sites that don't
     serve plain PDFs, which mix it is, …).

### 4.4 Hashes on the spot

`mame -romident` already runs on every dump and covers all of MAME, offline. The deck adds one
CHECK screen per dump (and per chip in a game's ROM list):

| against | source | answers |
|---|---|---|
| MAME | `mame -romident` (exists) | which set and ROM this is, or NO MATCH |
| the game's expected chips | the library's `rom_list` for the machine's model | right chip / another revision / not this game |
| her images | the library's `rom_image` entries (every dump, with her known-good / bad marks, including chips MAME lacks) | known good / known bad / seen before / never seen |
| this machine's earlier dumps | the deck's own dump records | same as last time / changed |

**A comparison never sends anything anywhere.**

### 4.5 Room to grow

- **Today:** 1.13 GB of manuals (about 12 MB per game) and under 1 MB of dumps.
- **Plan for about 20 GB over the next years:** manuals for a few hundred games is several GB,
  and a board's ROMs are 0.5–8 MB.
- The archive's data mount starts at about 50 GB and grows in Proxmox.
- **The deck:** SYSTEM shows the library's size and the free space on `/srv`, and warns when
  it's low. If the SD card ever gets tight, the option is to keep every record but only some
  PDFs on the deck, fetching the rest from the archive on demand.

---

## 5. ROM images and burns, back to the source of truth (phase C)

**A machine's dump and burn records belong to its world's app:** cabcrawl for a barcade
machine, the Tech Tracker for a customer's asset. **The image itself also goes to the archive,
by hash:** game code is knowledge, and the barcade's copy isn't hers to keep.

- **New core contract items** (both apps implement them):
  - `rom_dump`: machine, chip label, part, size, CRC32, SHA-1, SHA-256, the `mame -romident`
    result, "read twice, identical", when, optionally the open order or job, plus the image;
  - `burn`: machine, image hash, part, verified or stopped, when, optionally the order or job.
- **Uids:**
  - `rom_dump`: `sha256(f"{public_id}|rom_dump|{machine}|{label}|{sha1}")[:32]`. The deck's dump
    archive already treats the same SHA-1 in the same machine's folder as the same dump;
  - `burn`: from its line in `burns.jsonl`.
- **Files first, then the item,** through resumable file endpoints (`/api/v1/files/…`, 8 MB
  parts), like a session before its order.
- **In the apps:** a machine or asset page lists its dumps and burns, an order or job page lists
  the ones made for it, and images download only behind the login. Files are kept outside any
  public path.
- **Back down:** a rebuilt deck pulls its worlds' dump records back (and every image from the
  archive). A dump with no machine (`unassigned`) goes to the archive only.

---

## 6. Barcade work on the deck (phase E: Gatbox + cabcrawl)

What exists in cabcrawl (recon, 2026-10-28) and what the deck would do with it:

| area | cabcrawl today | on the deck |
|---|---|---|
| work orders | `MaintenanceRecord`: Open → In_Progress → Fixed / Deferred; `WorkLog` (text, hours); `machine_down` drives machine status; parts used via `use_parts` | **all open orders**, with detail and logs; **START**, **LOG WORK** (text + minutes), **FIXED** / **DEFERRED** with what was done; NEW ORDER, ATTACH TRACE (exist), ATTACH DUMP (phase C) |
| **coin totals** | `PlayRecord` meter readings: no backwards readings, one round reading a day, "far more than usual" needs confirming, a baseline first | **COIN COUNT** on a barcade machine: scan its label, type the meter, send it. cabcrawl applies its own rules and the deck shows the answer (a flag, "confirm high", or refused, with why). The reading's date comes from the deck's clock and is refused when the clock is unverified. |
| parts and stock | `InventoryItem` (`/i/` label), `StockPlace` tree (`/p/`), `StockLevel`; every change through `stock_places`; usage costed on the order | **scan a part or shelf label**, see stock per place; **USED ON THIS ORDER**; **COUNT** at a shelf; **REQUEST** a part that's short |
| serial units | `SerialUnit` (`/u/` label) | scan to see where a unit is; INSTALL / REMOVE (later) |
| rotation | floor ↔ warehouse swaps (manager) | view only, if at all |

What cabcrawl needs before a device can do any of this safely:

- **A device action ledger** (a uid unique per action, with its result). Today only orders have
  `external_id`; work logs, stock history, part usage and meter readings have no idempotency
  key.
- **The device acts as her:** each device is linked to a person on the Devices page, and the
  history shows "via GATBOX". `WorkLog.user_id` and `StockHistory.user_id` are required.
- **Strict device semantics:**
  - stock: `take` quietly takes less than asked, so a device action is all-or-nothing, with a
    shortfall `rejected` and the amount on hand given;
  - status changes follow explicit transitions.
- **When it happened:** actions carry `at` and the clock source.
- **Device switches** (orders, parts and stock, coin counts), off by default.

The contract part: `contract/floor-v1/`, vendored only into Gatbox and cabcrawl (§8).

---

## 7. Side work on the deck (phase F: Gatbox + Tech Tracker)

What exists in the Tech Tracker (recon, 2026-10-28) and what the deck would do with it:

| area | Tech Tracker today | on the deck (7" only) |
|---|---|---|
| jobs | `WorkOrder` (`WO-YYYY-NNNN`, gapless; new → … → completed / cancelled) holding `WoJob`s (one per asset: complaint, cause, correction; open / done / cancelled). The deck can already raise a job and attach traces with a phase. | **open jobs for assets in the shop**, with complaint, WO status and NTE (as today); **NOTE** → the job's cause / correction (appended, never overwriting); **DONE** on a job |
| time | `TimeEntry` (user, started / ended, minutes, note; one running timer per user) | **TIMER** (start / stop as actions with the deck's times), or **ADD TIME** |
| readings | `ManualReading` (test point, value, unit, spec lo/hi, phase; `passed` computed by Postgres) | **SAVE READING** on a jobs-hub machine also becomes a `ManualReading` on the job: the meter's value, the rail, the window and AS-FOUND / AS-LEFT |
| parts | `Part` (sku), `StockLot` (FIFO), `StockMove` (a trigger refuses negatives); issuing makes a billable line priced by the app | **search a part**, see on hand; **USED ON THIS JOB**, issued as a tech would. No cost or price on the deck |
| assets | tags `s-NNNN-name`, permanent; no printed labels | picked from the shop group (exists); no intake from the deck |

What the Tech Tracker needs first: a device action ledger; "acts as" with `shop.device_id` in
the audit; device switches; "closed is closed" (no time, lines or readings on a done job,
except trace tags); bench time (`taken_at`, `started_at`); strict stock against "available".

The contract part: `contract/shop-v1/`, vendored only into Gatbox and the Tech Tracker (§8).

---

## 8. The contract

- **Fix the drift first.** cabcrawl's v1 README has the 2026-10-26 additions; the Gatbox and
  Tech Tracker copies don't. Bring all three back to byte-identical.
- **Write down what's built:** health `app` and `capabilities`, roster `model_key` (in the example
  too, since cabcrawl's test checks the exact keys), orders `work_order` / `nte`, `phase`, and
  **the order priorities** (§9 item 3).
- **Gatbox owns the contract from here.** Each repo's test also diffs its copy against sibling
  checkouts when they're present.
- **What goes where:**

  | part | contents | vendored into |
  |---|---|---|
  | `contract/v1/` (core) | health and capabilities, the ingest framework and uid rules, the action framework, `rail_session` / `order` / `session_tag`, roster, orders, **files**, **`rom_dump`**, **`burn`** | all three |
  | `contract/floor-v1/` | order actions, parts and stock, coin counts | Gatbox + cabcrawl |
  | `contract/shop-v1/` | job actions, time, readings, parts | Gatbox + Tech Tracker |
  | `docs/contract/library-v1/` | the library protocol (deck ↔ archive) | Gatbox only: both ends live there |

- **The action framework (core):** every bench action is an ingest item with a uid minted once
  on the deck, `at` and the clock source, and optionally the order or job it's for. The hub
  records it in a device action ledger and applies it all-or-nothing, answering `created`,
  `duplicate` or `rejected` per item.
- **Everything is additive and capability-gated.** No existing request shape changes; a hub is
  sent only what its health lists. No v2 needed.

---

## 9. Fix now (in phase A)

Found in the recon. **The owner: "these need fixed for sure."** Each fix gets a test that fails
without it, in its repo's suite.

1. **cabcrawl: work-order photos are public.**
   - Today they're served from `static/maintenance_photos/` with no login.
   - Fix: move them out of `static/` (beside its other uploads) and serve them only through a
     `login_required` route.
   - Old `/static/maintenance_photos/<file>` addresses redirect there, so nothing printed or
     bookmarked breaks (its URL rule).
   - Existing files move with a one-off script that the owner runs on the server (⏸).
2. **cabcrawl: photos and `uploads/` aren't backed up.** The backup next to the `pg_dump` in
   `deploy/` gains an archive of both, with a restore step in the RUNBOOK.
3. **cabcrawl: the device API doesn't check an order's priority, and the two sides disagree.**
   The deck offers Low, Medium, High, Critical and **Urgent**; cabcrawl's orders know only the
   first four.
   - Fix: cabcrawl accepts the contract's five and stores Urgent as Critical, rejects anything
     else, and the contract says so.
   - The owner checks whether any order already in the database carries "Urgent" (one query,
     ⏸).
4. **Tech Tracker: any enabled device can read every asset's jobs, and device writes show in the
   audit log as "system".**
   - Fix: per-device switches (jobs on for the deck, off by default for any new device).
   - The device is linked to the person it acts as (decided: her), and device requests set
     `shop.user_id` and a new `shop.device_id`, so the audit reads "the owner via GATBOX".
   - This is the "acts as" groundwork phase F needs anyway.
5. **Tech Tracker: the restore drill re-hashes PDFs and receipts but not photos.** It re-hashes
   photos too.
6. **cabcrawl has no pre-push privacy check at all,** in a public repo carrying the barcade's
   work. It gets the shared hook (phase A's rules).

---

## 10. Phases and order of work

| phase | what | repos | needs the owner for |
|---|---|---|---|
| **A. Groundwork and fixes** | the shared agent rules and the same privacy hook in all three; the contract drift fixed, ownership moved to Gatbox, Phase 8 extras and priorities written down; the realm rule and its test on the deck; **the fix list (§9)** | all three | plan approval; the privacy patterns variable (§12); the two ⏸ server steps in §9 |
| **A2. One look, four themes** | the shared philosophy and `design/themes.css` (Synthwave, Ledger, Graphite, Follow system) with the contrast test; per-user choice in both apps (one setting for every width); the four colour fixes. **The deck stays Synthwave.** | the two apps (+ the shared file from Gatbox) | nothing more: T1–T4 decided |
| **B. The library on the deck** | `library.sqlite`, seed, readers switched over; LIBRARY on the 7"; the title search; manual fetch on request; MISSING; the CHECK screen; known-good marks; space on SYSTEM | Gatbox | the seed review; new units shown before install |
| **C. Dumps and burns to their world** | `rom_dump`, `burn`, the file endpoints; the apps store and show them | all three | deploys |
| **D. The archive** | `archive/` (server, installer, RUNBOOK), the deck ↔ archive sync and conflicts, every image to the archive, the "open in the library" links in both apps, `Game.model_key` | Gatbox + small app changes | creating the LXC and the Cloudflare tunnel and Access (written out for her, ⏸) |
| **E. Barcade work on the deck** | action ledger, "acts as", order actions, coin counts, parts and stock with the scanner | Gatbox + cabcrawl | deploy; the device switches |
| **F. Side work on the deck** | the same for jobs, time, readings, parts | Gatbox + Tech Tracker | deploy |

- **Why this order:** A fixes what's broken and unblocks the rest. A2 is independent. B and C
  are what she asked for by name: a PDF for every machine and hashes on the spot (B, with no
  network or server needed), and images back to the source of truth (C). D gives the knowledge
  its home and her partner his phone access. E and F are the biggest and build on A's rules
  and "acts as". E and F can swap if the side work is busier.
- **PRs:** one per repo per phase, never merged by an agent. Merge the apps first in C–F (each
  advertises a capability only once deployed, and an older deck ignores it); Gatbox goes last.
- **Every phase:** a short spec → **her one OK** (it covers the code and the push) → code and
  tests → PRs → she merges, deploys and installs.

---

## 11. Decisions

| # | question | answer |
|---|---|---|
| S | Where does knowledge live? | **Decided:** a container on her Proxmox node. Design: the archive (§4.1). |
| K | Knowledge crossing between the apps | **Gone:** the apps hold no knowledge, only a link. |
| P | Customer data on the deck | **Decided: none.** Plus coin totals to cabcrawl from the deck (phase E). |
| A | Who the deck acts as | **Decided: her, "via GATBOX".** |
| R | Rules and the push gate | **Decided: one shared set of rules; a single OK to push.** Her OK on a change's spec covers its code and its push; merging and deploying stay hers. |
| T1–T4 | Themes | **Decided:** per user in the apps, one setting at every width; names kept; defaults as proposed; the four colour fixes in. **The deck: Synthwave only.** |
| L1 | Where the archive's code lives | **Open, recommended: in the Gatbox repo (`archive/`).** |
| L2 | Who can open the archive | **Open, recommended: her and her partner** (Cloudflare Access); barcade coworkers only if she adds them. |
| L3–L5 | CONFIRM on the 7" only; the key naming; no library edits from a phone on the deck's Wi-Fi | **Recommended** (library spec §10). |
| N | The privacy-patterns location in the rules draft (`~/.config/gatbox-family/`) | **Open:** any neutral name works. |

---

## 12. What needs the owner before work can start

1. **Approve this plan,** and the open decisions above (or "as recommended").
2. **The privacy patterns, in the cloud environment** (never in chat or git):
   - in the Claude app, open this session's environment (its name in the session's title bar)
     → **Edit** → add an environment variable **`PRIVACY_PATTERNS`**;
   - its value is her pattern lines joined into one line with `|`, so the hooks read it as one
     extended regex. On the Pi, in her checkout:
     `grep -v '^#' .githooks/patterns.local | grep -v '^$' | paste -sd'|' -`
     prints that line;
   - **a new session picks it up, this one doesn't.** Until then, pushes to the Tech Tracker
     (whose rules require the check) wait for a new session.
3. **GitHub:** done. cabcrawl and the Tech Tracker are attached to this session with push access
   (2026-10-28).
4. **Sizes:** done (§4.5).

---

## 13. Risks

1. **Leaking between worlds:** realm stamps, the sender's check and a test with two stub hubs.
   The apps hold no knowledge, there's no customer data on the deck, and money stays off it.
2. **The archive on the internet** behind a tunnel: Cloudflare Access for people, a service
   token plus a device token for the deck, and localhost only. Copyrighted files are never
   public.
3. **Actions replayed or half-applied:** the ledger with uids, all-or-nothing device actions,
   and explicit transitions. Both apps' own forms are looser than this, so the device API is
   deliberately stricter than the web.
4. **An open web server on someone else's network:** side-work data is 7"-only, and anything new
   on the phone view is reviewed against that rule.
5. **Growth:** watched on the deck; the archive's mount grows in Proxmox.
6. **Size of the program:** seven phases over three repos and one new container. Each phase is
   useful alone, and A–C deliver what was asked for by name first.
