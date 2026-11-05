# Phase D: the archive — short spec

Status: **approved by the owner, 2026-11-04 ("OK as written")**. It covers the code and the push in all three repos
(integration plan §10); merging, deploying, installing and everything on Proxmox and Cloudflare stay hers. D1 to D4 are
built on `phase-d-archive`; D5 and D6 (the apps) on `phase-d-library` in cabcrawl and `phase/13-library` in the Tech Tracker,
D7 (the read side's contract and its drift tests) in all three. Nothing is installed or deployed.

This is the build plan for phase D of `docs/superpowers/specs/2026-10-28-gatbox-integration-plan.md` (§4). The design
behind it is `2026-10-28-shared-game-library-design.md` (§2 where the library lives, §4 the protocol, §5 sync and
conflicts, §6 files, §8 each repo). Where this file and that one differ, the last section says why.

## What phase D is

Today the library is one SQLite file on the deck (phase B) and the dump images are on the deck and in the apps (phase C).
Phase D gives the knowledge a **home that isn't a Pi in a cabinet**: a small server in its own LXC on her Proxmox node,
reached through a Cloudflare tunnel, holding every manual, every ROM image, every limit and fact. The deck keeps a full
copy and syncs both ways; each app keeps a read-only copy of its own machines' games.

It is the biggest phase, so it's built in slices that are each useful, and she can stop after D3 with the knowledge safe.

## What she gets

- **The archive**: `archive/` in this repo, a stdlib server over the same `gatboxlib/library.py` the deck runs (so the two
  can't disagree about the format), an idempotent `archive/install.sh`, a systemd unit, and a RUNBOOK with the Proxmox and
  Cloudflare steps as exact commands (⏸).
- **Her partner's phone pages** at the archive (synthwave, phone-first): games, a game (documents and the PDF viewer, MAME
  and the ROM list, images and their verdicts, limits, facts), search, upload a PDF, add a game, edit. Limits he adds
  arrive **unconfirmed**; CONFIRM and conflict choices stay on the 7".
- **The deck's two-way sync** (a library step in gatbox-sync): edits go up, the partner's edits come down, **every dump
  image and manual goes up** (resumable, after sessions and orders), and a same-record disagreement waits **side by side on
  the 7"** (LIBRARY → CONFLICTS) instead of overwriting anything. The first sync is a dry run that prints the plan.
- **A read-only copy in each app** of the games its machines name: cabcrawl gets `Game.model_key`; both apps get a pull
  job, the library tables, a file folder, and a game section on the machine or asset page (manuals from the app's own
  store behind its own login, limits with page and quote, facts, MAME set and ROM list with verdicts). The Tech Tracker's
  page adds "edit in the library" for owner and tech.
- **The `NO CHIP LIST` gap from phase B closes:** a game added after the seed gets its ROM list built from MAME.

## What stays as it is

- **The deck works offline and unseeded exactly as today** (hard rule 3, 14): with no `/etc/gatbox/archive.conf` the sync
  has no library step, and phase 8's routing never sees the archive (it's a different file, not a `hubs.d` hub).
- **The logger never opens SQLite.** A limit reaches it only when `confirmed`, and only the 7" confirms.
- **Nothing crosses between worlds.** The archive accepts writes only from the deck and from Access users, never from an
  app; an app only reads, for its own games, and the archive keeps no record of what an app asked for. No library record
  names a machine, a customer, a site or a world; a ROM image is stored by its hash.
- Contract v1 and phase C's `roms` calls, unchanged. The archive **speaks phase C's file calls** (begin, parts, finish,
  read with Range), so the deck has one uploader.
- No framework, no CDN, no build step, no browser storage (the dashboard's rules, for the archive's pages too).

## The protocol, and what's vendored

The library protocol is Gatbox's own (`docs/contract/library-v1/`, written in phase B). Two additions land here:

- **HTTP endpoints** (design §4.1): `GET /api/v1/library/changes?since=&limit=&models=`, `PUT /api/v1/library/records`
  (up to 100 compare-and-set writes), `GET /api/v1/library/digest`, and the file calls of contract v1's `roms` part.
  Examples in `docs/contract/library-v1/examples/`, reproduced by the tests of both ends.
- **The read side is vendored into both apps** (`contract/library-v1/`: the README's record kinds and the `changes` and
  `files` reads, with `CHECKSUMS`, byte-identical, and the same sibling drift test as contract v1). The design left the
  library protocol Gatbox-only; with a pull job in each app it isn't any more, and a drift test is cheaper than a bug.

## The slices

One branch per repo: `phase-d-archive` (Gatbox, stacked on `phase-b-library`, which it needs), and the apps' own. One PR
per repo at the end, never merged by an agent.

| # | repo | slice | tests that fail without it |
|---|---|---|---|
| D1 | Gatbox | **The archive server.** `archive/server.py` (stdlib, localhost listener) around `Library` and a content-addressed file store: the changes / records / digest / files calls; device tokens and **per-app reader tokens** (hashed, `gbx_` format, a small CLI `archive/gatbox-archive token add\|list\|revoke`); the `models=` filter; reader tokens read only; the Access identity read from the tunnel listener only. `archive/install.sh`, the unit, `RUNBOOK.md` with the ⏸ steps | `test-archive.py` against the real server on a temp port: auth and role per endpoint, compare-and-set, a retried uid, the feed, the digest, a resumed file, a reader's filter, a write refused to a reader |
| D2 | Gatbox | **The archive's pages**: games list and search, a game, PDF viewer, upload, add game, edit game / fact / limit (unconfirmed), phone-first | `test-archive.py` page checks; screenshots at phone width and desktop |
| D3 | Gatbox | **The deck's sync.** gatbox-sync's library step (own state in `/var/lib/gatbox-sync/library/`: cursor, last revision and hash per side, the agreed base, `conflicts.json`), `/etc/gatbox/archive.conf`, the per-record merge (archive only / deck only / both the same / **both differently = a conflict, nothing written**), files in 8 MiB parts through gatbox-web's new loopback file calls (so the sync never writes `/srv/gatbox/manuals`), digest compare, `GATBOX_SYNC_DRYRUN` printing the plan, a `library` block in `status.json` | `test-sync-library.py` against the real archive server: each merge case, out of reach, a lost reply, an edit against a delete, a resumed file, first contact, dry run writes nothing, a refused push is judged again and never forced |
| D4 | Gatbox | **On the 7":** LIBRARY → CONFLICTS (BEFORE / THIS DECK / THE ARCHIVE, differing fields lit, KEEP THIS or EDIT, her choice is a local write marked `resolves`), SYSTEM's Library tile gains edits waiting, files moving, conflicts and "identical", and a CONFLICT badge wherever a conflicted entry shows. **Every dump image becomes a `rom_image` record and a file to send.** ROM lists built for games added after the seed (the `gatbox-mame-roms` path unit: ⏸ shown first) | `test-dash.py` CONFLICTS, KEEP, EDIT, the badge; `test-library-web.py`; the mame-roms rebuild |
| D5 | cabcrawl | **Read-only copy.** `Game.model_key` (Alembic, `check_schema.py`, a link script from the seed plan, managers edit it), library tables, a file folder inside `uploads/` (no URL, as phase C's), the pull job (CLI + timer unit, reader token and archive address in settings), the machine page's game section and a library list for any signed-in role; screenshots at 390 and 1280 | a stub archive speaking the vendored examples: pull, a changed record replaced, a tombstone, a PDF fetched and resumed, own keys only, no archive configured = no section; characterization snapshots regenerated and checked |
| D6 | Tech Tracker | **The same, on PostgreSQL**: tables with `grant_app()` and `EXPECTED_GRANTS`, PDFs in its file store, the pull job, the asset page's game section, "edit in the library" for owner and tech (`Asset.model_key` exists) | the same, on a real PostgreSQL |
| D7 | all three | The vendored read-side contract with its drift tests; BRINGUP, journals, CLAUDE.md status | each repo's contract test |

D1 to D3 are the risk: the merge is the one place data could be lost. They go to her as a unit (with the dry-run plan from
her real library) before D4 on, as B1 to B3 did.

## What she does (⏸), and what is system config

Nothing below is done by an agent. The RUNBOOK writes each step out as exact commands.

1. **Proxmox:** create the LXC (Debian 13, unprivileged, 1-2 cores, 1 GB RAM) and a data mount point (about 50 GB, grown
   later), and put it in her backups. I won't guess her node's network: the RUNBOOK asks for its private address and gives
   the lines with a placeholder.
2. **Cloudflare:** a tunnel from that container (`cloudflared`), an Access application with a policy for her and her
   partner by email, and one **service token** for the deck. The tunnel listener is bound to localhost.
3. **On the container:** run `archive/install.sh`; mint the deck's device token and one reader token per app.
4. **On the deck:** `/etc/gatbox/archive.conf` (the address, the device token, the service-token pair), mode 600 root.
   **System config, shown before install (rule 5):** the sync unit loads it as a credential (a change to
   `gatbox-sync.service`), and a `gatbox-mame-roms.path` / `.service` pair (D4).
5. **On each app's server:** the pull job's timer unit and its settings (the archive address and the reader token). The
   unit is shown before it's installed there too.
6. **The first deck sync is `GATBOX_SYNC_DRYRUN=1`:** she reads the plan, then runs it for real.

## Where this differs from the design

- **The files calls are phase C's** (begin, 8 MiB parts, finish, read with Range), not the design's `status` and
  `?of=&size=&type=` form: one uploader on the deck for the hubs and the archive. A file is 400 MB at most here (the
  deck's fetch limit); a hub's is 32 MiB (phase C), because a hub keeps an image only.
- **Two listeners, so identity can't be forged.** The archive has no crypto library (stdlib only, hard rule 9: apt before
  pip), so it can't verify Cloudflare's signed token itself. The tunnel listener binds to localhost, so everything on it
  has passed Access, and only there is the Access identity header trusted for "who changed this". A second, optional
  listener on the node's private address answers **reader tokens only, read-only, with no identity**. The deck's writes
  name themselves by device token; a service-token request carries no user email, which the archive doesn't need.
  Cloudflare's settings were **checked against its documentation when D1 was built (2026-11-04)**. A service token is sent
  as `CF-Access-Client-Id` and `CF-Access-Client-Secret`, expires on the duration chosen and has to be refreshed
  ([docs](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)); a request
  body is capped at 100 MB on the Free and Pro plans, so the 8 MiB parts stay well under it. **A correction to the
  design:** Access sends its token in `Cf-Access-Jwt-Assertion`
  ([docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)),
  and the current documentation names no `Cf-Access-Authenticated-User-Email` header. So the archive reads the `email` claim
  of that JWT. It does not verify the signature (no crypto library, and it never calls out for Cloudflare's keys); Cloudflare's
  application-token page says an origin reached through a tunnel need not validate the token, which holds only while every
  route to that port is behind the Access application. The RUNBOOK makes that a rule and says to create the Access
  application before the tunnel route. A service token's JWT has no email, which is fine: the deck is its device token.
- **The read-side contract is vendored into the apps** (see above), where the design kept it Gatbox-only.
- **ROM lists for later games** are built by a path unit, as the design lists, and close phase B's one open gap.
- **Not in phase D:** disk images (the plan's separate disk phase: they're gigabytes and get their own dataset, the deck
  keeps their records and hashes), `gatbox-backup`, and other people's installs (plan §14). A rebuilt deck fills from the
  archive on first contact, so it never needs to pull its dumps back from a hub: phase C's read-back stays unused by the
  deck.

## Decisions (as recommended; say if you want any different)

| # | question | recommended |
|---|---|---|
| D-1 | What does her partner change on his phone? | Game fields and notes, documents (upload a PDF), facts, limits (arriving unconfirmed). Not known-good marks, not CONFIRM, not conflicts: those are the 7" |
| D-2 | How do the apps reach the archive? | The node's private network with a reader token per app, if she gives me the address; else through the tunnel with an Access service token per app. The server supports both |
| D-3 | Build order | D1 to D3 first and shown to her (with a dry run on her real library) before D4 to D6 |
| D-4 | Where D lives while B is unmerged | A branch stacked on `phase-b-library`. She merges B first, then I rebase D onto `main`, so D's PR is only D |
