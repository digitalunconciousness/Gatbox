# Phase B: the library on the deck — short spec

Status: **approved by the owner, 2026-11-03,** with P1 as proposed (LIBRARY replaces MANUALS). Her OK covers the
code and the push (integration plan §10); merging and installing stay hers.

This is the build plan for phase B of `docs/superpowers/specs/2026-10-28-gatbox-integration-plan.md`.
The design behind it is `2026-10-28-shared-game-library-design.md` (its §3 keys, §4 records, §5.4 limits,
§6 files, §7 seeding). Where this file and that one differ, this file says why.

## What phase B is

The deck gets its own copy of what she knows about each game: **one SQLite store, `library.sqlite`**,
holding games, documents, the MAME set and ROM list, ROM images by hash, rail limits and facts. Today that
knowledge is spread over the roster, the manuals list, the sidecars, the specs files and the MAME lists.
After the seed, the store is the source and those files are only read by `gatbox-library import`.

It's offline and Gatbox-only. No archive, no sync, no conflicts (phase D), and nothing sent to the apps
(phase C). The store has the revisions, hashes and `uid`s the archive's sync will need, so D adds a client
and doesn't change the format.

## What she gets

- **A LIBRARY view on the 7":** games, a game's documents, MAME set, images, limits and facts; ADD GAME;
  MISSING (every game she has met with no manual, each with its reason).
- **A title search** over MAME's own list (description, maker, year), built once per MAME version, offline.
  It suggests the game and its key when she picks a machine that has none.
- **A CHECK screen per dump** and per chip in a game's ROM list: against MAME, against the game's expected
  chips, against her own images (known good / known bad / seen before / never seen) and against the
  machine's earlier dumps. A comparison never sends anything anywhere.
- **Known-good / known-bad marks** on her images, including chips MAME doesn't have.
- **The library's size and the free space on `/srv`** on SYSTEM, with a warning when it's low.
- **Manuals on request:** an archive.org search offered as candidates and fetched by `gatbox-manuals`
  through a request file and a path unit, like dumps.

## What stays as it is

- Every existing gatbox-web URL and API, every CSV, the roster file and its schema (hard rule 14).
- **The logger never opens SQLite.** `gatbox-raillog` runs as root and must always work (hard rule 3). It
  keeps reading flat JSON; gatbox-web writes a `library-specs.json` export of the confirmed limits, and
  with no export the logger behaves exactly as today.
- A rail limit reaches the logger only when `confirmed` (hard rule 12); CONFIRM is on the 7" only.
- An unseeded deck reads today's files through today's code. Nothing changes until `seed --apply`.

## The slices

Each is small, ends with tests that fail without it, and goes on one branch, `phase-b-library`. One PR at
the end. Nothing here is installed until she says.

| # | slice | tests |
|---|---|---|
| B1 | **The store.** `backend/gatboxlib/library.py` (stdlib `sqlite3`, WAL): records with `rev`, canonical-JSON `hash`, `uid` per edit, tombstones, compare-and-set writes (`if_rev`), the changes feed and the digest. `model_key` as one function. The confirmed rule: a change to `lo`, `hi`, `doc_id` or `page` clears `confirmed`. `docs/contract/library-v1/` with the vectors (canonical hashes, key slugs) the archive's tests will reproduce. | `test-library.sh`: create, update, duplicate retry, conflict, delete, the feed's cursor, the digest, key vectors, the confirmed rule |
| B2 | **The seed.** `tools/gatbox-library seed --plan` (read-only, writes the plan to the owner's home, never git), then `--apply PLAN` (one transaction; re-running changes nothing), and `import FILE`. Sources: the roster, the manuals list and sidecars, the specs files, the confirmed and actual specs, the MAME lists, the dumps. Barcade slugs that are game titles become the keys. | `test-library.sh` seed section on invented data (Widget Wars): plan, apply, re-apply, an import |
| B3 | **The readers switch over.** `manuals.py`, `mame.py` and `profiles.machine_specs()` read through the library once it's seeded. `library-specs.json` for the logger. A machine's game comes from `roster.library(slug)`: the hub roster's `model_key` if the library has it, else the slug if the library has that game, else nothing. Precedence stays Phase 8's: the machine's own actual window, then the confirmed library limit for its model, then user, then profile. | the existing `test-web-api`, `test-manuals`, `test-mame`, `test-modes` and raillog tests pass unchanged on an unseeded deck, and again seeded; new ones for the export and for a logger with no export |
| B4 | **LIBRARY.** The view and its API (`/api/library/...`, the same shapes the archive will serve on loopback), under the existing `html.box` rules; read-only on a phone. ADD GAME, MISSING, a game's documents (the page viewer and search that MANUALS has today), its limits with CONFIRM, facts. | `test-dash.sh` LIBRARY section and screenshots (7" and phone); `test-web-api` |
| B5 | **The title search** and linking. An index built once per MAME version from `mame -listxml` (at install, like `gatbox-mame-roms`); suggested keys for a machine with no game. | a fake `mame -listxml`, both a hit and a miss; pinball and games MAME lacks are typed |
| B6 | **CHECK and the marks.** `rom_image` records made from dumps; the four comparisons; known good / bad. | the dump tests' fakes (`fake-minipro`, `fake-mame`) through CHECK, all four answers |
| B7 | **SYSTEM:** the library tile (size, free space, a warning when low). | `test-dash.sh` |
| B8 | **Manuals on request** ⏸: the request file, `gatbox-manuals` reading it, the path unit. The unit is system config, so it's shown to her first. | a fake archive.org (no network in tests) |

B1 to B3 come first and are the risk. They go to her as a unit before any of B4 on, so the seed review can
happen on a real plan while the view is built.

## What changes on the Pi, and what she sees first (hard rule 5)

Installed by the bootstrap, as a diff she sees before it runs:
- `backend/gatboxlib/library.py`, `tools/gatbox-library` and the changed modules, in the MANIFEST;
- `/srv/gatbox/library/` (`2775`, group `gatbox-manuals`, setgid, like the manuals folder) so both her
  login (`gatbox-library`) and gatbox-web can write the one database; a tmpfiles line;
- B8's path unit and service;
- the MAME title index, built at install.

## What she does

1. **OK this spec** (it covers the code and the push).
2. **Review the seed plan** after B2: which slugs are one game, which carry a cabinet-specific part. The
   plan lands in her home directory as a file she edits.
3. **See the install diff,** then run the install herself.

## Decisions

| # | question | proposed |
|---|---|---|
| P1 | **Decided (owner, 2026-11-03): LIBRARY replaces MANUALS.** The question was: where does LIBRARY go on the 7"? Eight tabs at 56 px fill the nav; a ninth makes it scroll and cuts tabs off. DISK (its own branch) is the eighth, so LIBRARY would be the ninth. | **LIBRARY replaces MANUALS.** MANUALS' documents, viewer and spec sheet are library content, so they move inside a game: DOCUMENTS and LIMITS & FACTS. Its API and URLs stay. The alternative is a ninth tab with the nav scrolling. |
| P2 | Where the store module lives. | `gatboxlib/library.py`, not `gatboxweb/library.py` as the design says: the CLIs (`gatbox-library`, `gatbox-manuals`, `gatbox-mame-roms`) and the archive's server all import it, and `gatboxlib` is where shared code lives. The HTTP side stays in `gatboxweb`. |
| P3 | Where the database lives. | `/srv/gatbox/library/library.sqlite`, next to the manuals. |
| L3-L5 | Who confirms a limit, key naming, phone editing. | As recommended in the library design §10: the 7" only; title slugs; read-only on a phone. |

## Risks

1. **A bad seed or a wrong key is permanent** (keys never change). The plan is read-only first and she edits
   it; `--apply` is one transaction and idempotent.
2. **Two writers on one SQLite file** (her CLI and gatbox-web): WAL and one shared group; a test runs both.
3. **Switching readers could change what the logger trusts.** The flat export and the unchanged logger tests
   are the guard; an unseeded deck is byte-for-byte today's behaviour.
4. **Growth on the SD card** (about 12 MB a game): SYSTEM shows it (B7).
