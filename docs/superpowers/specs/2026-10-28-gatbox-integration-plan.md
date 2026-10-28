# The deck, the barcade, the side work and the archive — the integration plan

Status: **approved by the owner, 2026-10-28.** Phase A is under way. Her decisions are in §11.
This is the top-level plan. The detailed specs hang off it:
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
| **knowledge** | manuals and PDFs, MAME sets and hashes, ROM images (by hash), rail limits, facts | **the archive** | a full copy | **never:** each app holds a read-only copy of its own machines' games, pulled from the archive; nothing flows back |

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
- **Each app holds a read-only copy of its own machines' games** (decided: cabcrawl will have an
  unknown number of users, who use its own logins).
  - A pull job in each app fetches them from the archive by key, PDFs included, with a
    read-only reader token over the node's private network.
  - Its users read manuals, limits, facts and ROM lists on the machine or asset page.
  - The Tech Tracker adds "edit in the library" for owner and tech.
  - The archive takes nothing from an app.
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
by hash:** game code is knowledge, the owner has permission to keep copies, and it's what a
known-good reburn, a MAME comparison or a desuicide needs at the bench.

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
| **A3. Report a problem** | a "Report a problem" link on every page of both apps; reports kept privately in each app's own database with a page for her to triage; a reference on every error page; a problem report and automatic error reports on the coin-door form; "report to the developers" for other installs; GitHub issue forms and a SECURITY.md for the repos. Spec: §17 | the two apps (+ issue forms in all three) | nothing more: F1–F5 decided (§17) |
| **A2. One look, four themes** | the shared philosophy and `design/themes.css` (Synthwave, Ledger, Graphite, Follow system) with the contrast test; per-user choice in both apps (one setting for every width); the four colour fixes. **The deck stays Synthwave.** | the two apps (+ the shared file from Gatbox) | nothing more: T1–T4 decided |
| **B. The library on the deck** | `library.sqlite`, seed, readers switched over; LIBRARY on the 7"; the title search; manual fetch on request; MISSING; the CHECK screen; known-good marks; space on SYSTEM | Gatbox | the seed review; new units shown before install |
| **C. Dumps and burns to their world** | `rom_dump`, `burn`, the file endpoints; the apps store and show them | all three | deploys |
| **D. The archive** | `archive/` (server, installer, RUNBOOK), the deck ↔ archive sync and conflicts, every image to the archive; **each app's read-only copy** (tables, pull job, reader token, the game section on machine / asset pages), `Game.model_key` | Gatbox + both apps | creating the LXC and the Cloudflare tunnel and Access (written out for her, ⏸) |
| **E. Barcade work on the deck** | action ledger, "acts as", order actions, coin counts, parts and stock with the scanner | Gatbox + cabcrawl | deploy; the device switches |
| **F. Side work on the deck** | the same for jobs, time, readings, parts | Gatbox + Tech Tracker | deploy |
| **G. More floor data** | §16 ideas 8 and 9: cost to keep, downtime and lost plays, repeat faults, rail health beside each swap | cabcrawl | deploy |
| **H. Shop extras** | §16 ideas 10 and 11: real repair times while estimating; a customer status link per job | Tech Tracker | deploy |
| **I. Health and alerts** | §16 ideas 2 and 12: overnight alerts from the deck, health at a glance in all three, ntfy when something's wrong | all three | her ntfy server's address (site config, never in git) |

- **Why this order:** A fixes what's broken and unblocks the rest. A2 is independent. B and C
  are what she asked for by name: a PDF for every machine and hashes on the spot (B, with no
  network or server needed), and images back to the source of truth (C). D gives the knowledge
  its home and her partner his phone access. E and F are the biggest and build on A's rules
  and "acts as". E and F can swap if the side work is busier. G and H touch one app each and
  can run any time after A, alongside the deck's phases. G is worth doing early, since the
  barcade is about to put cabcrawl into service. I comes once there's something worth
  watching (after C).
- **PRs:** one per repo per phase, never merged by an agent. Merge the apps first in C–F (each
  advertises a capability only once deployed, and an older deck ignores it); Gatbox goes last.
- **Every phase:** a short spec → **her one OK** (it covers the code and the push) → code and
  tests → PRs → she merges, deploys and installs.

---

## 11. Decisions

| # | question | answer |
|---|---|---|
| S | Where does knowledge live? | **Decided:** a container on her Proxmox node. Design: the archive (§4.1). |
| K | Knowledge crossing between the apps | **Gone:** the apps' copies are read-only and come only from the archive. |
| L6 | Do the apps hold knowledge? | **Decided: yes, a read-only copy of their own machines' games.** |
| P | Customer data on the deck | **Decided: none.** Plus coin totals to cabcrawl from the deck (phase E). |
| A | Who the deck acts as | **Decided: her, "via GATBOX".** |
| R | Rules and the push gate | **Decided: one shared set of rules; a single OK to push.** Her OK on a change's spec covers its code and its push; merging and deploying stay hers. |
| T1–T4 | Themes | **Decided:** per user in the apps, one setting at every width; names kept; defaults as proposed; the four colour fixes in. **The deck: Synthwave only.** |
| L1 | Where the archive's code lives | **Recommended: in the Gatbox repo (`archive/`).** It also fits going public (§14). |
| L2 | Who can open the archive | **Decided: her and her partner.** Everyone else reads their app's copy. |
| L3–L5, L7, L8 | CONFIRM on the 7" only; the key naming; no library edits from a phone on the deck's Wi-Fi; apps pull over the node's private network; apps get ROM hashes and verdicts, not the images | **Recommended** (library spec §10). |
| N | The privacy-patterns location (`~/.config/gatbox-family/`) | **Decided.** |
| ROMs | Keep ROM images | **Decided: yes, every image, in the archive and on the deck** (she has permission). |

---

## 12. What needs the owner before work can start

1. **Approve this plan,** and the open decisions above (or "as recommended").
2. **The privacy patterns:** done. `PRIVACY_PATTERNS` is in the cloud environment, and each
   session writes it where the hooks read it. Everything pushed so far was re-checked against
   it, clean.
3. **GitHub:** done. cabcrawl and the Tech Tracker are attached with push access.
4. **The fix list (§9):** being built on branches (cabcrawl `claude/peaceful-cerf-9ukxgf`, Tech
   Tracker `phase/10-gatbox-fixes`); the PRs carry the ⏸ server steps.
5. **Sizes:** done (§4.5).

---

## 13. Risks

1. **Leaking between worlds:** realm stamps, the sender's check and a test with two stub hubs.
   The apps' library copies are read-only and come only from the archive, there's no customer
   data on the deck, and money stays off it.
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

---

## 14. Going public: other techs and arcades

The owner asked what the best setup would be if the apps became public apps that other techs
or arcades could use, or if others could see the GATBOX code.

**Where things stand:**

| repo | visible | licence | what that allows |
|---|---|---|---|
| Gatbox | public | **none** (so all rights reserved by default) | anyone can read it; nobody may legally reuse it |
| cabcrawl | public | **AGPL-3.0** | anyone may use, change and host it; changes must be shared, including when it runs as a service |
| Tech Tracker | public | **proprietary, view only** | read only |

**Recommended:**

1. **One product per repo, as they're split now.**
   - **GATBOX = the deck plus its archive**, one repo (L1). A tech who builds a deck also runs
     their own archive. Its public interfaces are the hub contract and the library protocol,
     both owned here.
   - **cabcrawl** is an arcade's floor app, and **the Tech Tracker** is a repair shop's app.
   - Each is **self-hosted, one install per arcade or shop.** Not one shared server for many
     businesses: that would mean holding other people's customers and money, with their
     support and security on her.
   - The contract is what lets anyone's shop app work with a deck.
2. **Choose the licences on purpose; that's the real decision.**
   - To let people use and improve the code while stopping a closed fork from being sold as a
     service: **AGPL-3.0** (cabcrawl already is). It fits the deck and archive, and the Tech
     Tracker if it's ever opened.
   - For the most uptake with no duty to share back: MIT or Apache-2.0.
   - The build guide and docs: Creative Commons (e.g. CC BY-SA 4.0). The enclosure's CAD
     files: an open hardware licence (CERN-OHL-S).
   - **To keep the option of selling later:** as the sole author, she can license her own code
     twice (AGPL for everyone, plus a paid licence for anyone wanting closed use), provided
     outside contributions come with permission to do that (a short contributor agreement, or
     a sign-off on each commit).
   - The Tech Tracker can stay view-only until she decides.
3. **Ownership.** The owner, 2026-10-28: all of it is outside work done on her own time, which
   her boss wants to use, and so far its only authors are her and AI agents. So the code is
   hers to license.
   - Worth one short note in writing with the barcade: the code is hers; the barcade uses
     cabcrawl under its licence (AGPL-3.0 already allows that); the barcade's data stays the
     barcade's. That settles it before anyone else relies on it. (This plan isn't legal
     advice.)
   - Keeping sole authorship is what keeps every licensing option open. If anyone else ever
     contributes, ask for a sign-off that lets her relicense.
4. **What makes the projects usable by others** (mostly true already):
   - no site data in code (a rule in all three);
   - everything site-specific in configuration (hub labels, names, branding);
   - a first-run path (create the owner account, mint a device token);
   - install paths: the Pi bootstrap, cabcrawl's Docker files, an LXC script for the Tech
     Tracker and the archive;
   - tagged releases with a changelog, the wikis, and a versioned contract.
5. **Sharing knowledge with other techs (later).** Manuals and ROM images can't be published,
   but facts can: rail limits with page citations, fuse values, pinouts she wrote, ROM hashes
   with verdicts, fix notes. The archive could one day export a public subset (facts, hashes
   and links to public archive pages, never files) that other decks import. Nothing now.
6. **Before publicizing:** the build guide names parts of her home network. It isn't in any repo
   today; scrub it first if it's ever published.

---

## 15. What the build guide adds, for later

The full guide (the CATBOX design, now GATBOX) is the deck's long-term plan. It fits this
design without changing it:

- **Knowledge it produces** (platforms and pinouts, logic-analyzer signatures, MAME notes, disk
  images, photos, fix notes) becomes new record kinds in the archive (library spec §2.4). The
  store and protocol are built so each is added without touching the others.
- **New instruments** (ADALM2000, INA226 trip, adjustable rail, thermal camera, microscope,
  media bay, UV drawer) are deck features. They produce bench records for the machine at hand,
  sent to its world like a meter session today. **None of them is owned yet, so no code assumes
  them** (Gatbox CLAUDE.md).
- **Burning from known-good images** comes free with C and D: the archive holds every image,
  and DUMP → BURN already burns from the archive folder.
- **The homelab "crash cart" and Proxmox panel** stay out of both worlds and out of the
  library. If built, they're a realm of their own on the deck.

---

## 16. More ideas (approved by the owner, 2026-10-28: "I like all of those")

The owner asked for more improvements to the functionality and took all twelve. Each gets its
own short spec in the phase that builds it:

| idea | phase |
|---|---|
| 1 guided board dump; 3 machine timeline (the deck's own records); 4 manual search and chip-label search (on the deck) | **B** |
| 4 search in the archive's pages; 7 fix notes as a library kind | **D** |
| 6 blank chips as stock; 7 SAVE AS FIX on closing an order; 3 the timeline gains the order history | **E** |
| 5 the job timer; 6 and 7 for jobs; 3 the timeline gains the job history | **F** |
| 8 more data for cabcrawl's existing swap planner; 9 downtime cost and repeat faults | **G** (cabcrawl only) |
| 10 real repair times for estimates; 11 the customer status link | **H** (Tech Tracker only) |
| 2 overnight alerts; 12 health at a glance | **I** (all three; needs her ntfy server's address) |

**At the bench (the deck):**

1. **Guided board dump.** Pick the game, and the deck walks its MAME ROM list chip by chip:
   - "IC18, a 256 KB chip: insert it", with the label and size filled in;
   - read twice, identify, tick it off;
   - finish with a board report: every chip good, another revision, bad, or missing.

   It's built from what exists: DUMP, the ROM checklist and CHECK. She still picks the exact
   part; the deck only suggests families that match the size (no auto-detect, hard rule 6).
2. **Overnight alerts to her phone.** The logger already knows when a rail leaves its window, a
   session ends unexpectedly, or the meter goes quiet in a cabinet overnight. Push that to an
   ntfy topic on her own server (the guide already plans ntfy). It needs one setting in
   `/etc/gatbox`, quiet hours, and is sent by gatbox-sync, never by the open web server.
3. **One timeline per machine.** On MACHINE, everything about that machine, newest first:
   sessions, saved readings, dumps, burns, orders or jobs, notes, coin counts. It's "what did I
   do last time?" in one scroll, from the deck's own records and its world's cache.
4. **Search every manual.** Full-text search across the whole archive (the deck already extracts
   each page's text), on the 7" and in the archive's pages, e.g. "every manual that mentions
   'watchdog'", opening at the page. Plus **chip-label search**: type `EPR-12345` and see which
   games and sockets use it, to identify a board before it's dumped.
5. **A job timer that offers itself (side work).** Picking a customer machine with an open job
   offers START TIMER; changing machine offers STOP. The time lands on the job as a deck action,
   with the bench's times.
6. **Blank chips as stock.** A burn uses a blank EPROM, so the deck records it as a part used on
   the open order or job, picked from that world's inventory. Stock and costs stay right with
   nothing to type.
7. **Fix notes from closed work.** Closing an order or job on the deck offers SAVE AS FIX:
   symptom → cause → fix for the game or platform, with the machine and customer left out, into
   the archive (library spec §2.4). The next time that game comes in, the deck shows "seen
   before".

**In cabcrawl (the barcade):**

8. **More data for the swap planner.** cabcrawl already has one (`app/services/rotation.py`):
   plays per *working* day (broken days don't count against a game), a settling period,
   like-for-like pairing, fade detection, and a swap suggested only for a game doing 20% better.
   The owner: "more data is always welcome". So each candidate gains, over the same window:
   - **cost to keep:** repair hours and parts spent on its orders;
   - **downtime:** days down and the plays lost to them (idea 9);
   - **repeat faults;**
   - **rail health** from the deck's traces (a +5V that sags or drifts).

   The planner's choice stays the planner's: these sit beside each swap so a manager can see
   why to keep or pull a game.
9. **Downtime cost and repeat faults:** plays lost while a machine is down (its usual daily plays
   times the days down), to rank repairs; and a flag when the same machine gets a similar order
   three times in 60 days.

**In the Tech Tracker (side work):**

10. **Real repair times for estimates:** time actually spent per kind of repair (from time entries
    and fix notes), shown while writing an estimate.
11. **A status link for customers:** a `/d/` link per job that shows only its status (received,
    waiting on parts, ready), so customers can check instead of calling. It uses the existing
    `/d/` machinery: expiring, revocable, not indexed.

**Across all three:**

12. **Health at a glance:** one page in each app and on the deck with the last sync, last
    backup, disk space, queued and parked items. The same facts go to ntfy when something's
    wrong: a backup that didn't run, an archive disk 90% full, a parked item.

---

## 17. Report a problem (phase A3, both apps)

The owner, 2026-10-28: cabcrawl and the Tech Tracker "need an easy place to report bugs and such
for when they begin to get used." Small, and independent of the deck work, so it runs right after
phase A, before cabcrawl goes into service.

**For the people using the apps:**
- **"Report a problem"** in every page's menu and footer, for any signed-in user, every role.
- A short form:
  - what happened (required);
  - what you expected (optional);
  - how much it matters: *it's in my way* / *it's annoying* / *an idea*;
  - an optional photo (stored behind the login like other photos).
- The page it was pressed on is filled in automatically, so nobody has to describe where they
  were.
- **Error pages say "Something went wrong · reference 7F3A2C · report it"**, with the reference
  already in the form. The same reference is in the server's log beside the error, so a report
  leads straight to the cause.

**For the owner:**
- **Reports stay private, in the app's own database:** a `feedback` table holding when, who,
  their role, the page, the app's version (its git commit), the browser, how much it matters,
  the text, a status (new / seen / fixed / won't fix) and her note.
- **A Feedback page** lists them, filters by status and marks them done.
  - cabcrawl: managers and admins.
  - The Tech Tracker: owner.
- **Being told:**
  - **cabcrawl:** a new `feedback` event in its existing alerts (ntfy and Discord channels,
    through the alert outbox), so a channel can send reports to her phone.
  - **The Tech Tracker:** it has no notifications yet, so a "new reports" count on the
    dashboard and in the menu. ntfy arrives with phase I.
- **Never sent anywhere public automatically.** The repos are public, and a report can name a
  customer, a machine or the barcade.
  - The Feedback page has a **copy as issue** button. It produces the report's text with the
    reporter, the ids in the page address and the photo left out, for her to check and paste
    into GitHub.
  - An agent turning a report into an issue strips names and business data the same way and
    shows her the text first.

**For the repos (all three, the deck included):**
- **GitHub issue forms** (`.github/ISSUE_TEMPLATE/`) for a bug and for an idea, each warning not
  to paste customer, business or personal data. They're for her, and for anyone else once the
  projects go public (§14).
- **`SECURITY.md`:** how to report a security problem privately, through GitHub's private
  vulnerability reporting, not an issue.

**The public coin-door form** (the owner, 2026-10-28: "or if there's a bug in the coin door
form"). Whoever scans a coin door has no login, and a broken form loses the fault they came to
report, so this form gets its own way back:
- **"Problem with this form?"** under the form opens one text box (what went wrong, 500
  characters at most). It posts to a new `/report/<token>/problem` in the same blueprint, so the
  printed `/report/<token>` labels don't change and the route table still shows the blueprint
  reaching nothing else.
  - Same rules as the form itself: the token is the authorisation, CSRF-exempt for the same
    reason, a per-token limit (3 an hour), a honeypot field, text only (no photo, no name
    asked for). It's stored in `feedback` with the source `coin_door` and the machine, and
    raises the `feedback` alert like any other report.
  - An unknown token is a 404, exactly as the form's is, so it can't be used to guess tokens.
- **A server error on the form is reported without anyone typing.** The error page says
  "Something went wrong and your report wasn't saved · reference 7F3A2C · please tell the staff",
  and the error itself is written to `feedback` (source `error`, the page, the reference, no
  form text) with a new `public_error` alert, so she hears about it even if nobody says a word.
  A failure while writing that row is only logged; it never hides the error page.
- **The Tech Tracker's `/d/` customer links** get the second half only: an error there is
  written to `feedback` as source `error` and counted on the dashboard. No form: a customer
  with a broken invoice link texts the shop.

**People using the apps at other places** (the owner, 2026-10-28: "for future people who use
the app"). New staff at her installs need nothing new: every signed-in user can report (F1).
Once other arcades or techs run their own copy (§14), their reports stay in **their** app,
read by **their** admins, and never reach her or anyone else on their own. What reaches her is
what their admins choose to send:
- **"Report to the developers"** on the Feedback page beside "copy as issue". It opens the
  repo's bug form on GitHub in a new tab, already filled in with the report's text (stripped as
  for "copy as issue"), the app's version, the route pattern (`/maintenance/<id>`, never the
  real address) and the browser. Nothing is sent until that admin reads it and presses Submit
  on GitHub. GitHub fills a form field from the address when the field has an `id`
  (`/issues/new?template=bug.yml&version=…&page=…`); the forms below are written with those ids.
- **Where it goes is a setting:** `UPSTREAM_ISSUES_URL`, the public repo's issues by default.
  An install that forks the app points it at its own repo; an empty value hides the button.
- **A reporter without a GitHub account** tells their own admin through "Report a problem",
  as now. Security problems go through `SECURITY.md` (private vulnerability reporting), never
  an issue.
- **The issue forms** ask for the version and the page and warn, at the top and beside the
  text box, not to paste customer, business or personal data.

**Tests (each app):**
- any role can file a report and can't see others' unless allowed;
- the page and the app's version are recorded;
- the error page's reference matches the logged one;
- the Feedback page is limited to its roles;
- "copy as issue" and "report to the developers" leave out the reporter, ids, real addresses and
  the photo, and the link is the configured URL (none when it's empty);
- cabcrawl's `feedback` and `public_error` alerts reach the outbox.
- cabcrawl's coin-door problem report: stored with its machine, limited per token, 404 for an
  unknown token, the honeypot drops a bot's post, and a forced error on the form writes the
  `error` row and still shows the reference.
- the Tech Tracker's `/d/` error is recorded and counted.

| # | question | decided |
|---|---|---|
| F1 | Who can report? | **Any signed-in user, every role** (the owner: "as recommended works"). **Plus the coin-door form** (her ask): "Problem with this form?" and automatic error reports, above. The Tech Tracker's `/d/` links get automatic error reports only. |
| F2 | Who reads and triages? | **cabcrawl: managers and admins** (so the boss sees them too). **The Tech Tracker: owner.** |
| F3 | How she hears about a new one | **cabcrawl: its alert channels** (a `feedback` event and a `public_error` event, chosen per channel). **The Tech Tracker: a count on the dashboard now, ntfy with phase I.** |
| F4 | GitHub issue forms and a SECURITY.md | **Yes, in all three repos,** with the privacy warning. Nothing is ever filed automatically. |
| F5 | Reports from other places that run the apps | **Their admins send them on**, with "report to the developers": a prefilled GitHub form they read and submit themselves. `UPSTREAM_ISSUES_URL` chooses where; empty turns it off. |
