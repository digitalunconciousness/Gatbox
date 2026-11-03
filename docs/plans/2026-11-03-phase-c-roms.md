# Phase C: dumps and burns go to their world — short spec

Status: **approved by the owner, 2026-11-03** ("OK as written"; her OK covers the code and the push in all three repos,
integration plan §10; merging, deploying and installing stay hers). Built the same day: see "As built" at the end.

This is the build plan for phase C of `docs/superpowers/specs/2026-10-28-gatbox-integration-plan.md` (§5). Where
this file and that one differ, this file says why (last section).

## What phase C is

Today a T48 dump or burn lives only on the deck: `/srv/gatbox/roms/<machine>/<label>_<sha1:8>.bin` with a `.json`
sidecar, and a line per burn in `burns.jsonl`. After phase C a machine's dumps and burns are also in **the app of
the world it belongs to**: cabcrawl for a barcade machine, the Tech Tracker for a customer's asset. The image
goes first (a resumable upload by hash), then the record. A machine or asset page lists its dumps and burns, an
order or job page lists the ones made for it, and an image downloads only behind the login.

It's the deck's sync job doing the sending, so it behaves like sessions already do: finished things only,
routed by roster, idempotent, retried by itself, never sent to the wrong hub.

## What she gets

- On a cabcrawl **machine page** and a Tech Tracker **asset page**: a "ROM dumps and burns" list (label, part,
  size, the SHA-1, MAME's verdict, read twice and identical, when; for a burn: part, verified or stopped, which
  image). Each image downloads behind the login (cabcrawl: operator and up; Tech Tracker: any signed-in role).
- On an **order** (cabcrawl) or **job** (Tech Tracker): the dumps and burns made for it.
- On the 7": each dump and burn in the DUMP list says where it is: **SENT to WORKSHOP**, **WAITING** (no hub
  reachable), **HELD** (no hub's roster has the machine, or it's `unassigned`), **REFUSED: <reason>**.
- A **FOR ORDER** chip on DUMP and BURN when the machine has open orders (the cached ones): preselected when
  there's exactly one, a tap to clear it. That's the only link from the deck to an order; the hubs never guess.

## What stays as it is

- Contract v1's existing items and shapes (`rail_session`, `order`, `session_tag`, roster, orders): nothing
  changes. Everything here is **additive and capability-gated** (integration plan §8): a hub that doesn't list
  `roms` in its health is sent nothing, so an older deck and an older hub keep working.
- The dump and burn tools, the archive's layout and the sidecars (hard rules 6 and 14). `gatbox-dump` isn't
  touched. Sending only reads.
- **No new system config on the deck.** `gatbox-web.service` and `gatbox-sync.service` are unchanged: the sync
  reads from gatbox-web on loopback, as it does for sessions, so its sandbox stays as tight as it is. Installing
  phase C is the bootstrap's usual copy of changed files.
- The hubs get **images and records, never the library**: ROM hashes and verdicts reach the apps in phase D, from
  the archive (decision L8). Nothing customer-side comes to the deck (decision P).

## The contract (one change, in all three repos together)

Gatbox's `docs/contract/v1/` gets the following and is vendored byte-identical into both apps (`CHECKSUMS`, the
drift tests). The examples remain the schema: each app's test posts them and compares the replies byte for byte.
Example images are a few invented bytes written as hex in JSON (no `.bin` in git).

**Capability `roms`:** the hub serves `/api/v1/files`, accepts `rom_dump` and `burn`, and says so in `health`.

**Files** (the image, before the item that names it; device token, rate limited):

| call | answer |
|---|---|
| `POST /api/v1/files` `{"sha256","size"}` | `{"state":"stored"}` (nothing to send) or `{"state":"open","part_size":8388608,"parts":N,"have":[0,…]}` (resume from what it holds) |
| `PUT /api/v1/files/<sha256>/parts/<n>` raw bytes | `{"state":"open","have":[…]}`; every part is exactly 8 MiB, the last is the rest; a part already held is replaced by identical bytes |
| `POST /api/v1/files/<sha256>/finish` | `{"state":"stored"}` once the parts join to exactly that size and SHA-256; anything else is a 422 and the parts are dropped |

A file over **32 MiB** is a 413 (the deck keeps it, says so, and it goes to the archive in D). Half-sent parts
older than 7 days are cleaned up. Re-sending anything is harmless.

**`rom_dump`:** `machine`, `label`, `part`, `size`, `crc32`, `sha1`, `sha256` (the file), `romident`
(`{"match": true, "matches": [{"set","rom"}]}`, `{"match": false}`, or `null` when MAME didn't run), `reads: 2`,
`identical: true`, `blank`, `created` (epoch), `clock: {"source"}`, optionally `order`. The uid is
`sha256(f"{public_id}|rom_dump|{machine}|{label}|{sha1}")[:32]` (integration plan §5), so the same SHA-1 on the
same machine is one dump, as the deck's archive already treats it. **Rejected** for an unknown machine, a machine
off the device's list, a file the hub doesn't hold ("send the file first", as a session before its order), a
size, SHA-1 or CRC32 that isn't what the stored file computes to, or an `order` that isn't that machine's.

**`burn`:** `machine`, `image` (the SHA-256 of the file burned), `image_sha1`, `part`, `size`, `state`
(`verified` or `stopped`), `vpp`, `blank_ok`, `error` (the reason a stopped burn stopped, or `null`), `created`,
optionally `order`. The uid comes from its line in `burns.jsonl`:
`sha256(f"{public_id}|burn|{machine}|{image_sha1}|{epoch:.3f}")[:32]` (`.3f` is exact, like a reading's). The
burned image goes to the hub first like a dump's, so a burn from `_images/` carries its file too. A stopped burn
is sent as well: a burn that went wrong is the record that matters.

**Read back** (hubs only; the deck's pull-back is built in D): `GET /api/v1/machines/<slug>/dumps` and
`GET /api/v1/files/<sha256>` (Range supported).

## The slices

Branches: `phase-c-roms` (Gatbox, cabcrawl) and `phase/12-roms` (Tech Tracker). One PR per repo at the end.
Apps first, Gatbox last (the merge order of integration plan §10).

| # | repo | slice | tests that fail without it |
|---|---|---|---|
| C1 | Gatbox, copied to both | **The contract:** README, examples, `CHECKSUMS`, uid vectors, the hex test image | each repo's contract test (checksums, sibling diff, the examples' bytes) |
| C2 | Tech Tracker | **Hub side.** `rom_file` / `rom_dump` / `burn` tables and one migration (`grant_app()`, `EXPECTED_GRANTS`); the file endpoints over the existing content-addressed store (`SHOP_FILES_DIR`, so the existing backup carries it); ingest of both items with every rejection above; `roms` in health; the asset page list, the job page list, the login-only download | the examples posted, a duplicate, each rejection, a part replayed, an interrupted upload resumed, a wrong hash dropped, no download without login |
| C3 | cabcrawl | **Hub side.** The same, with its own tables and migration (registered in `scripts/check_schema.py`), files in `ROM_FILES_DIR` (`uploads/rom_files/`: see "As built"), already in the uploads backup; `app` and `capabilities` in health; the machine page list, the order page list, the operator-and-up download | the same, on SQLite and a throwaway PostgreSQL; the characterization snapshot regenerated and checked |
| C4 | Gatbox | **Sending.** `gatbox-web` loopback-only reads (`/api/sync/dumps`, `/api/sync/burns`, the image bytes by part); `gatbox-sync` uploads the file then posts the item to a hub whose health lists `roms`, routed and `guard()`ed by roster exactly like sessions; its own ledger namespaces (so an older `sent.json` loads unchanged); the 32 MiB rule; `unassigned` and unrostered machines held with the reason | two stub hubs: nothing crosses; an upload cut off mid-part resumes; a hub without `roms` is sent nothing; a refused item parks after five tries with its reason |
| C5 | Gatbox | **The 7" says where each one is** (the words above), and SYSTEM's hub block counts them | `test-dash.sh`, screenshots |
| C6 | Gatbox | **FOR ORDER** on DUMP and BURN: the request carries `order`, the sidecar and burn line keep it, the item sends it | `test-dump.sh` with the fakes; the dash test |
| C7 | both apps | **Read back** (the two routes above). Last, and the cut line: dropping it changes nothing before it | each app's tests |

C1 is first and is the risk: the contract is the one thing that can't be changed lightly afterwards. She sees it
(the README diff and the examples) before C2 and C3 are written on it.

## Not in phase C

- The archive, and every image going there (D). A dump with no machine goes nowhere until then.
- A decision from the deck to attach a dump to a job by itself: the hubs never guess; FOR ORDER is explicit.
- Disk images (D2): they're gigabytes and go to the archive, never to a hub.
- The deck pulling its dumps back from a hub on a rebuild: the hubs can serve them (C7); the deck's side comes
  with D's "every image back from the archive", where it belongs.

## What she does (⏸), after she merges

- Tech Tracker: deploy as usual (the migration adds three tables; `SHOP_FILES_DIR` already exists).
- cabcrawl: deploy (the migration adds three tables). The images go in `uploads/rom_files/`, which the service may
  already write and the uploads backup already archives: nothing to make or edit.
- Gatbox: the bootstrap's usual run. Until a hub lists `roms`, the deck sends nothing new: **apps first, then
  the deck, in that order**.

## Where this differs from integration plan §5

- **One capability, `roms`,** for the files, `rom_dump` and `burn` together, because a hub that takes one without
  the others is useless. The plan named no capability.
- **A burn names its image by SHA-256 and the file goes first.** `burns.jsonl` has the SHA-1 and a path; the
  sync asks gatbox-web for the file's SHA-256. A burn whose image has since been deleted is held with that reason.
- **The images come out of a loopback-only route.** gatbox-web has no way to download a `.bin` today and
  gains none for a phone: only the sync, on 127.0.0.1, can read an image off the deck.
- **Realm stamping at creation isn't added.** The roster decides at send time, as it does for sessions, and
  `guard()` re-checks; once a hub has acknowledged an item the ledger remembers which one, and the sender never
  offers it to another. A machine moved between worlds is the one case this doesn't cover, and it's a roster
  conflict already.
- **The pull-back moves to D** (above); the hubs' read routes are built now.

## As built (2026-11-03)

What differs from the plan above, and why:

- **cabcrawl's images are in `uploads/rom_files/`, not a new folder.** Its systemd unit is `ProtectSystem=strict`
  with a list of writable folders, so a new top-level folder would have needed a unit edit (and the backup list
  another entry). `uploads/` is writable and archived already, and the one route that serves from it takes a bare file
  name, so a folder inside it has no URL (a test says so). `ROM_FILES_DIR` is the config key; the Tech Tracker keeps
  its images in its existing file store (`SHOP_FILES_DIR/<aa>/<sha256>.rom`).
- **The read-back routes (C7) were built with each app's hub side (C2, C3),** not last: they read the same rows. The
  deck's own side of a rebuild is still D's.
- **The contract's example uids hash the machine slug,** and the Tech Tracker makes its own asset tags. Its tests
  substitute their tag and recompute the two uids by the rule; the README says so (a hub with a machine called
  `widget-wars`, like cabcrawl's tests, posts the example as it is).
- **ROM items have state files of their own.** The sync keeps, per hub, `roms.json` (what that hub acknowledged,
  refusals counted, parked items) and, once, `roms-status.json` for the 7"; `sent.json` and the session ledger are
  untouched, so an older sync state loads as it always did. `status.json` gains `roms_waiting` and `roms_parked` per hub.
- **A chip read again can't be linked to a new order.** The same bytes are the same dump in the archive and at the hub
  (one record per dump, evidence that is never changed), so the second read is "already archived". The job says
  "not linked to order #N: this chip was archived before, and the same bytes are the same dump" in the result's steps
  rather than drop the link quietly. A burn is its own record each time, so FOR ORDER always works for a burn.
- **FOR ORDER offers only the cached open orders** (`GET /api/orders/open?machine=`), and gatbox-web refuses an order
  that isn't open for the machine as of the last sync (400), so a made-up or stale number never reaches a hub.
- **The deck's sandbox is unchanged.** gatbox-sync still reads from gatbox-web on loopback only; the images come out
  of three loopback-only routes (`/api/sync/dumps`, `/api/sync/burns`, `/api/sync/image`), which refuse any other
  address (a test connects from 127.0.0.2).
