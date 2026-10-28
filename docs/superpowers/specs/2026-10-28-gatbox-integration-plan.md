# The deck, the barcade and the side work — the integration plan

Status: **draft for the owner's approval, 2026-10-28. No code yet.** This is the top-level plan.
Detailed specs hang off it: the knowledge archive (`2026-10-28-shared-game-library-design.md`),
one set of agent rules (`2026-10-28-agent-rules-draft.md`) and one look with four themes
(`2026-10-28-one-look-four-themes.md`). Each later phase gets its own
short spec when it starts, as each repo's rules ask.

---

## 1. What the owner wants (from her description, 2026-10-28)

- **cabcrawl** is for the barcade where she works, and is barcade-only. It started as play
  counts, to decide which floor games to swap with a big warehouse library, then grew into
  maintenance and inventory. Not in service yet. **She wants that functionality in the box.**
- **ArcadeTech-Tracker** (the Tech Tracker, deployed as shop-hub) is for side jobs she and the
  other tech now take: customers, jobs, the customers' machines, parts. **She wants that
  functionality in the box.**
- **The deck (GATBOX)** is a Pi-powered cyberdeck becoming a complete arcade diagnostic machine.
  Today it has a multimeter, a T48 ROM programmer, a screen and a scanner. Meter readings and
  ROM rips are tied to the machines she works on.
- **One detailed record of everything:** for the bench (as little searching on the spot as
  possible) and for the archive (arcade knowledge is being lost).
- **cabcrawl and the Tech Tracker never talk.**
- **A PDF for every machine she sees,** ROM hashes compared on the spot, and ROM images sent back
  to the source of truth.
- **One set of agent rules** for the three repos.

---

## 2. The shape: two worlds, one deck

```
        THE BARCADE                                         THE SIDE WORK
   cabcrawl (floor app)                              Tech Tracker (shop-hub)
   machines · plays · rotation                       customers · assets · jobs
   work orders · parts · stock                       parts · money
          ▲                                                       ▲
          │ its own token, its own outbox,          its own token, its own outbox,
          │ its own cache, its own contract part    its own cache, its own contract part
          │                                                       │
          └────────────────────── THE DECK ───────────────────────┘
                 meter · T48 · scanner · 7" · works offline
                 THE ARCHIVE: every manual, every ROM hash, every reading, every dump
                 ── nothing passes from one side to the other ──
```

There are three kinds of data, and each has one rule:

| kind | examples | source of truth | on the deck | crosses between worlds? |
|---|---|---|---|---|
| **business** | machines, work orders / jobs, customers, parts and stock, plays, rotation, money | the world's app | a working cache of that world, plus an outbox of **actions** | **never** |
| **bench records** | meter sessions, ROM dumps and images, burns, readings | the app of the machine's world (the deck makes them, sends them there, and keeps its own copy) | everything it ever made | **never**: a record belongs to one machine |
| **knowledge** | manuals and PDFs, MAME sets and hashes, known-good ROM images, rail limits, facts | **the deck** (her archive), backed up off the deck; each app holds the entries for its own machines' models | everything | **only when she adopts it on the 7"** (decision K) |

- **Business data flows as actions, not as copies of state.** The deck sends "log 30 min on
  order 41", "used 2 × fuse from shelf A3 on order 41", "order 41 fixed: replaced C12". Each
  action has a uid minted once, and the app checks it, applies it once and answers per item,
  exactly as ingest works today. The deck keeps a read-only cache (open orders, parts, stock) so
  it can show and scan offline. The app's rules stay the app's: the deck can't make an order
  skip a step the app wouldn't allow.
- **Knowledge flows as a shared, merged record:** the archive spec, summarized in §4.
- **Bench records flow one way, to their machine's world,** with the files, and come back down
  only to restore a deck (§5).

---

## 3. On the deck: two realms, kept apart

Phase 8 already keeps a hub's config, token, state directory and routing separate. This plan
makes that a rule the code proves.

- **Each hub is a realm.** Everything the deck stores that came from a hub or is going to one is
  stamped with exactly one realm when it's made: a cached record, an outbox entry, a session,
  a dump, a burn. **The sender refuses an item whose realm isn't the destination's**, and a test
  with two stub hubs asserts nothing ever crosses.
- **A machine is in exactly one realm,** by roster. A slug on both rosters is already a conflict
  that goes nowhere.
- **A scanned code is resolved per realm.** cabcrawl's part, place and unit labels
  (`<base>/i/<code>`, `/p/<code>`, `/u/<code>`) and the Tech Tracker's codes are looked up in
  each realm's cache. Found in one realm, it's that realm's; found in both, the deck asks.
- **The deck is often on the barcade's Wi-Fi with an open web server** (it lives in cabinets
  overnight). So:
  - **side-work business data is served to the 7" only** (`is_local()`), never to a phone or
    the network. Job details already work this way; new side-work screens and APIs follow;
  - **customers' personal details stay off the deck** (decision P, recommended). A job on the 7"
    shows the asset, its tag, the complaint and the work, not who owns it or how to reach them.
    Her phone has the Tech Tracker for that;
  - **money stays off the deck**, apart from the NTE string the JOB card shows today. No
    estimates, invoices or payments.
- **Optional: a deck PIN for the side-work realm** on the 7" (decision Q). The 7" sits behind a
  closed cabinet door overnight, so recommended: not now.

---

## 4. Knowledge: the archive (phases B and D)

The full spec is `2026-10-28-shared-game-library-design.md`. In short:

### 4.1 The archive

- **The deck holds every entry for every model she has seen,** PDFs included, in
  `library.sqlite` plus `/srv/gatbox/manuals`. It's offline, and the 7" is where she edits.
- **Entries:** game (keyed by `model_key`, a permanent title slug), documents (+ PDF), MAME set,
  ROM list (built on the deck from `mame -listxml`), **known-good ROM images**, rail limits (page,
  quote, confirmed or not) and facts (fuses, monitor, PSU).
- **Each app holds the entries its own machines use** (`Game.model_key` / `Asset.model_key`), so
  the barcade's copy never shows what models turned up on side jobs, and the other way round.
- **Sync, per world:** compare-and-set writes, same-entry conflicts side by side on the 7",
  nothing overwritten silently. **An edit made in one app reaches the other app only when she
  ADOPTs it on the 7"** (decision K).
- **First fill:** `gatbox-library seed --plan` builds it from today's files (the manuals list,
  the PDFs and their sidecars, MAME sets, confirmed limits). She reviews the keys, then
  `--apply`.

### 4.2 A PDF for every machine she sees

When she picks or scans a machine with no game, or a game with no manuals, the deck helps on
the spot:

1. **Which game?** An offline title search over MAME's own list (description, maker, year for
   every set: a small index built once per MAME version) suggests a game and its `model_key`.
   Pinball and games MAME doesn't have are typed.
2. **Link it:** on the deck for a machine in no world (THIS PI); in its app for a world's machine
   (a link from the deck is decision M, recommended later).
3. **Manuals:**
   - **online:** a search of archive.org, an allowed archive, offered as candidates, fetched by
     `gatbox-manuals` through a request file and a path unit, like dumps (a new unit, shown
     first);
   - **offline or not found:** a phone upload (it exists), or the partner uploads it in the
     Tech Tracker and it comes down on sync;
   - **LIBRARY → MISSING** lists every game she has met that has no manual, for the next time
     the deck is online.

### 4.3 Hashes on the spot

`mame -romident` already runs on every dump and covers all of MAME, offline. The deck adds one
CHECK screen per dump (and per chip in a game's ROM list), comparing four things:

| against | source | answers |
|---|---|---|
| MAME | `mame -romident` (exists) | which set and ROM this is, or NO MATCH |
| the game's expected chips | the archive's `rom_list` for the machine's model | right chip / another revision / not this game |
| known-good images | the archive's `rom_image` entries (hers, including chips MAME lacks) | known good / known bad / never seen |
| this machine's earlier dumps | the deck's own dumps, and its world's copy | same as last time / changed |

Hash matches span her whole archive on her screen. **A comparison never sends anything anywhere.**

### 4.4 Off-deck backup (decision B)

An SD card in a cyberdeck is the most fragile place to keep lost knowledge. Proposed: a
`gatbox-backup` command. It copies the archive, the manuals, the dumps and the rail CSVs to a
labelled USB drive (no network or host needed), and optionally rsyncs to a host she names
(her own server). Each world's copy covers its own models too.

---

## 5. ROM images and burns, back to the source of truth (phase C)

**The source of truth for a machine's dumps and burns is its world's app:** cabcrawl for a
barcade machine, the Tech Tracker for a customer's asset. The deck makes them and keeps
everything; the app holds the record of what was in that machine.

- **New core contract items** (both apps implement them):
  - `rom_dump`: machine, chip label, part, size, CRC32, SHA-1, SHA-256, the `mame -romident`
    result, "read twice, identical", when, and optionally the open order or job it was for,
    plus the image itself;
  - `burn`: machine, image hash, part, verified or stopped, when, and optionally the order or job.
- **Uids:**
  - `rom_dump`: `sha256(f"{public_id}|rom_dump|{machine}|{label}|{sha1}")[:32]`. The archive
    already treats the same SHA-1 in the same machine's folder as the same dump;
  - `burn`: from its line in `burns.jsonl`.
- **Files go first, then the item that names them,** through the shared resumable file
  endpoints (`/api/v1/files/…`, 8 MB parts), like a session before its order.
- **In the apps:** a machine or asset page lists its dumps and burns, and an order or job page
  lists the ones made for it. Images download only behind the login. Files are stored outside
  any public path (cabcrawl's photos sit in `static/`, which is public: the wrong model).
- **Back down:** a deck rebuilt after an SD failure pulls its worlds' dumps back. A dump with no
  machine (`unassigned`) never leaves the deck.
- **Known good:** she can mark any dump "known good for <game>, chip <location>", which makes
  it knowledge (a `rom_image` entry in the archive), with or without the image.

---

## 6. Barcade work on the deck (phase E: Gatbox + cabcrawl)

What exists in cabcrawl (recon, 2026-10-28) and what the deck would do with it:

| area | cabcrawl today | on the deck |
|---|---|---|
| work orders | `MaintenanceRecord`: Open → In_Progress → Fixed / Deferred; `WorkLog` (text, hours); `machine_down` drives machine status; parts used via `use_parts` | **all open orders** (not just machines metered), detail with logs; **START**, **LOG WORK** (text + minutes), **FIXED** / **DEFERRED** with what was done; NEW ORDER, ATTACH TRACE (exist), ATTACH DUMP (phase C) |
| parts and stock | `InventoryItem` (code = the `/i/` label), `StockPlace` tree (code = `/p/`), `StockLevel`; every change through `stock_places`; usage costed on the order | **scan a part or shelf label** with the EY-H2, see stock per place; **USED ON THIS ORDER** (part, place, quantity); **COUNT** at a shelf; **REQUEST** a part that's short |
| serial units | `SerialUnit` (`/u/` label): in stock, in machine, at repair, retired | scan to see where a unit is; INSTALL / REMOVE on the open order (later) |
| plays | `PlayRecord` meter readings; the collection round (one a day, flags) | not first: rounds are done walking the floor with a phone. Possible later |
| rotation | swap floor ↔ warehouse (manager) | view only (where a machine is), if at all |

What the recon says the app needs before a device can do any of this safely:

- **A device action ledger** (a uid unique per action, with its result). Today only orders have
  `external_id`. Work logs, stock history, part usage and meter readings have no idempotency
  key, so a resent action would be applied twice.
- **"Acts as" a person.** `WorkLog.user_id` and `StockHistory.user_id` are required, and the
  history hook records the logged-in user. A device has none. Proposed: each device is linked on
  the Devices page to the person it acts for (the owner), so every action is hers in the
  history.
- **Strict device semantics:** `take` quietly takes less than asked, and `use_parts` skips short
  rows with a warning. A device action is all-or-nothing: short stock is `rejected` with what's
  there, and never partly applied. Status changes follow explicit transitions: no closing a
  closed order or blanking its fields, unlike the web form today.
- **When it happened:** actions carry `at`, the deck's clock, with its clock source. The app
  stores it where it has a field and keeps server time for the rest.
- **Inventory scope:** the device allow-list covers machines only. Recommended: a "parts and
  stock" switch per device, off by default.

The contract part: `contract/floor-v1/`, vendored only into Gatbox and cabcrawl (§8).

---

## 7. Side work on the deck (phase F: Gatbox + Tech Tracker)

What exists in the Tech Tracker (recon, 2026-10-28) and what the deck would do with it:

| area | Tech Tracker today | on the deck (7" only) |
|---|---|---|
| jobs | `WorkOrder` (`WO-YYYY-NNNN`, gapless; new → scheduled → in_progress → waiting_parts / waiting_approval → ready → completed / cancelled) holding `WoJob`s (one per asset: complaint, cause, correction, intake; open / done / cancelled). The deck can already raise a job (an `order`) and attach traces with a phase. | **open jobs for assets in the shop**, with complaint, WO status and NTE (as today); **NOTE** → the job's cause / correction (appended, never overwriting the form); **DONE** on a job; WO status only through the app's own moves |
| time | `TimeEntry` (user, started / ended, minutes, note; one running timer per user, feeding the actual-time line until billed) | **TIMER** on the job from the bench (start / stop as actions with the deck's times), or **ADD TIME** (minutes + note) |
| readings | `ManualReading` (test point, value, unit, spec lo/hi, phase as_found / during / as_left; `passed` computed by Postgres) | **SAVE READING** on a jobs-hub machine also becomes a `ManualReading` on the job: the meter's value, the rail as the test point, the spec window, and AS-FOUND / AS-LEFT. That's the deck's meter writing straight into the customer record. |
| parts | `Part` (sku), `StockLot` (FIFO cost), `StockMove` (the sum is on hand; a trigger refuses negatives); issuing a part makes a billable `WoLine` priced by the app | **search a part** (sku or name; parts have no labels to scan yet), see on hand; **USED ON THIS JOB**, which the app issues as if a tech did it. The deck never shows a cost or a price |
| photos | `Attachment` (job photos, re-encoded, EXIF stripped, in the file store) | later: the deck has no camera |
| assets | tags `s-NNNN-name`, permanent; no printed labels (the owner removed them) | picked from the shop group (exists); no intake from the deck (tags are minted by the app) |

What the recon says the app needs first:

- **A device action ledger.** `time_entry`, `manual_reading`, `wo_line`, `stock_move` and
  `attachment` have no idempotency key; only `wo_job.external_id` and the job-session pair do.
- **"Acts as" a person, and the audit log names the device.** `TimeEntry.user_id` is required,
  and a device request today runs with no actor, so `created_by` and `audit_log.user_id` come
  out NULL ("system"). Proposed: the device acts for the person it's linked to on Settings →
  Devices (the owner). Its requests set `shop.user_id` to her and a new `shop.device_id`, so the
  audit log reads "the owner, via GATBOX".
- **Device permissions = a tech's, minus anything financial,** and per-device switches (library,
  dumps, job work), all off by default. Today any enabled device can read every asset's jobs,
  and there's no allow-list.
- **Closed is closed:** nothing in the app stops time, lines or readings landing on a done job
  or a completed WO. The device API refuses them, except trace tags, which the contract
  already accepts on a closed job.
- **Bench time, not sync time:** actions carry `at`, set into `taken_at`, `started_at` and the
  stock move's time (`reported_at` is the precedent). An `unverified` clock is marked as such.
- **Strict stock:** a shortfall is `rejected` with what's on hand, never partly issued. Issuing
  ignores other jobs' reservations today, so the device checks "available", not just "on hand".
- **Note:** the Tech Tracker's PLAN parks `rom_dump` evidence as "needs v2". As an additive,
  capability-gated item kind in core v1 (§5, §8), it doesn't.

The contract part: `contract/shop-v1/`, vendored only into Gatbox and the Tech Tracker (§8).

---

## 8. The contract

- **Fix the drift first.** cabcrawl's v1 README has the 2026-10-26 additions; the Gatbox and
  Tech Tracker copies don't. Bring all three back to byte-identical.
- **Write down what's built:** health `app` and `capabilities`, roster `model_key` (in the
  example too, since cabcrawl's test checks the exact keys), orders `work_order` / `nte`, and
  `phase`.
- **Gatbox owns the contract from here.** The deck is the one client every hub serves, and the
  Tech Tracker's PLAN already recommends the move. Each repo's test also diffs its copy against
  sibling checkouts when they're present.
- **Three parts, each vendored only where it's implemented:**

  | part | contents | vendored into |
  |---|---|---|
  | `contract/v1/` (core) | health and capabilities, the ingest framework and uid rules, `rail_session` / `order` / `session_tag`, roster, orders, **files**, **`rom_dump`**, **`burn`**, **`library`** | all three |
  | `contract/floor-v1/` | work-order actions, parts and stock reads and actions, (later) meter readings | Gatbox + cabcrawl |
  | `contract/shop-v1/` | job actions, time, readings, parts | Gatbox + Tech Tracker |

  So neither app's repo describes the other's API.
- **A shared action framework in core.** Every bench action is an ingest item with a uid minted
  once on the deck, `at` and the clock source, and optionally the order or job it's for. The
  hub records it in a device action ledger (uid unique, plus the result), applies it
  all-or-nothing, and answers `created`, `duplicate` or `rejected` per item, as ingest does
  today. The kinds differ per world (cabcrawl's orders and stock aren't the Tech Tracker's jobs
  and lots), so they live in the world parts. The deck's WORK screen maps one set of buttons
  onto each world's kinds.
- **Everything is additive and capability-gated.** No existing request shape changes, and a hub
  is sent only what its health lists: `library`, `rom_dumps`, `floor_work`, `floor_stock`,
  `shop_work`… A v2 isn't needed.

---

## 9. Phases and order of work

| phase | what | repos | needs the owner for |
|---|---|---|---|
| **A. Groundwork** | the shared agent rules (vendored, checksummed) and the same privacy hook in all three (cabcrawl has none today); the contract drift fixed, ownership moved to Gatbox, Phase 8 extras written down, the three-part split; the realm rule and its test on the deck | all three | approving the rules; the pattern file in the cloud environment (§11) |
| **A2. One look, four themes** | one design philosophy and one shared `design/themes.css` (Synthwave, Ledger, Graphite, Follow system) with a contrast test, vendored like the contract; each app on the shared roles, with a per-user (deck: per-screen) choice. Spec: `2026-10-28-one-look-four-themes.md` | all three | T1–T4 in that spec; the hard rule 11 change |
| **B. The archive on the deck** | `library.sqlite`, seed, readers switched over; LIBRARY on the 7"; the offline title search and model suggestions; manual fetch on request; MISSING; the CHECK screen for hashes; known-good marking; `gatbox-backup` | Gatbox | the seed review; new units shown before install |
| **C. Dumps and burns to their world** | `rom_dump`, `burn`, the file endpoints; the apps store and show them; a rebuilt deck pulls them back | all three | deploys |
| **D. Knowledge in the apps** | library tables, sync per world, ADOPT, conflicts; read-only in cabcrawl, editable in the Tech Tracker | all three | deploys; switching library on per device |
| **E. Barcade work on the deck** | the action ledger, "acts as", work-order actions, parts and stock with the scanner | Gatbox + cabcrawl | deploy; the device switches |
| **F. Side work on the deck** | the same for jobs (after the Tech Tracker recon) | Gatbox + Tech Tracker | deploy |

- **Why this order:** A unblocks everything and is small. B and C are what she asked for by name
  (a PDF for every machine, hashes on the spot, images back to the source of truth), and B works
  with no network or app change at all. D gives the partner the manuals on his phone. E and F
  are the biggest and build on A's ledger and realm rules. E and F can swap if the side work is
  busier.
- **PRs:** one per repo per phase, never merged by an agent. Merge the apps first in C–F: each
  advertises a capability only once deployed, and an older deck ignores it. Gatbox goes last.
- **Every phase** is: short spec → the owner's OK → code and tests → PRs → the owner deploys and
  installs (Gatbox through the bootstrap, system config shown first).

---

## 10. Decisions for the owner

| # | question | recommended |
|---|---|---|
| S | Where does knowledge (manuals, ROM hashes, limits, facts) live? Earlier you said "the apps are the source of truth"; with "never talk", neither app can hold all of it. | **The deck is the archive's home,** backed up off the deck; each app holds its own machines' models. Business data and bench records stay with the apps as before. |
| K | Can knowledge typed in one app (the partner fixing a fuse value, say) reach the other app's copy? | **Only when you ADOPT it on the 7".** Business data never crosses, whatever the answer. |
| A | Who the deck acts as in each app | **You:** each app's Devices page links the deck to your account, and the history shows "via GATBOX". |
| P | Customers' personal details on the deck? | **None.** Jobs show the asset, tag, complaint and work; your phone has the rest. |
| Q | A PIN for the side-work realm on the 7"? | **Not now** (it sits behind a cabinet door); revisit when the enclosure exists. |
| B | Off-deck backup target | **A labelled USB drive first,** plus an rsync target you name if you want one. |
| M | Linking a machine to its game from the deck (sets the app's `model_key`)? | **Later:** link in each app for now; the deck links THIS PI machines. |
| R | The shared agent rules, and the push gate in them | **Yes; push to a branch once checks pass, with your gates at design and merge** (the rules draft). |
| O | Contract ownership moves to Gatbox | **Yes.** |
| T1–T4 | the themes (Tech Tracker's look on phones too; names; defaults; four colour corrections) | see the themes spec §6 |
| L1–L5 | the library specifics (cabcrawl read-only; CONFIRM on the 7" only; key naming; per-device switch; no phone edits) | see the library spec §10 |

---

## 11. What needs the owner before work can start

1. **Approve this plan,** the decisions above, and the rules draft.
2. **Privacy patterns in the cloud environment.** The Tech Tracker's rules forbid pushing
   without its pattern file, and this environment doesn't have one. Add an environment variable
   **`PRIVACY_PATTERNS`** holding the lines of your `patterns.local`, in the environment's
   settings (never in chat or git). Phase A's setup script then writes it where all three hooks
   read it.
3. **Push access for cabcrawl and the Tech Tracker** in this session. It's read-only today; it's
   attached when phase A starts and may ask you to confirm.
4. **Sizes:** `gatbox-manuals list` (manuals total) and `du -sh /srv/gatbox/roms` on the Pi, so
   the apps' storage and backups can be planned.

---

## 12. Risks

1. **Leaking between worlds.** Realm stamps, the sender's check, per-world scoping of knowledge,
   ADOPT, and a test with two stub hubs. Customers' details and money stay off the deck.
2. **The deck as the archive's home.** It's fragile hardware. Mitigated by the off-deck backup,
   and by each world holding its own models and its machines' dumps.
3. **Actions replayed or half-applied.** The ledger with uids, all-or-nothing device actions,
   and explicit transitions. The recon found that cabcrawl's own forms are looser than this
   (partial takes, reopen by save), so the device API is deliberately stricter than the web.
4. **An open web server on someone else's network.** Side-work data is 7"-only; anything new on
   the phone view is reviewed against that rule.
5. **Size of the program.** Six phases over three repos. Each phase is useful alone, and A–C
   deliver what was asked for by name first.
6. **Findings outside this plan, for the owner** (from the recon; not fixed here):
   - cabcrawl's work-order photos are served from `static/` with no login;
   - nothing backs up cabcrawl's photos or `uploads/`;
   - cabcrawl's device API doesn't validate an order's priority;
   - in the Tech Tracker, any enabled device can read every asset's jobs, and device requests
     reach the audit log as "system";
   - the Tech Tracker's restore drill re-hashes PDFs and receipts but not photos.
