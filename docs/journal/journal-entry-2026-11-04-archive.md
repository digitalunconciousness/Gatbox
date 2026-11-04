## 2026-11-04: Phase D, D1 to D4 built: the archive, the deck's sync and the 7" (repo only, nothing installed)
- Owner: "OK as written". Spec `docs/plans/2026-11-04-phase-d-archive.md`; branch `phase-d-archive`, stacked on
  `phase-b-library`. Built D1 (the server), D2 (its pages), D3 (the deck's sync) and D4 (the 7"). D1 to D3 go to her as a unit,
  with a dry run on her real library; D4 followed on her go.
- The archive is a stdlib server in its own container, running the deck's own `library.py` over a content-addressed file
  store. It speaks the library protocol (changes, records, digest) and phase C's file calls. Tokens are hashed, one for
  the deck and one read-only token per app, which sees only its games, reads manuals and is never given a ROM image.
  Two listeners: the tunnel one (localhost) takes people through Cloudflare Access, the private one (optional) takes
  reader tokens and nothing else. It never calls out, and its log holds no query and no token.
- Her partner's pages, phone-first: games and search, a game with its manuals, limits, facts, MAME set, ROM list and
  dumped images; a PDF viewer; upload; add a game (the key shown before saving); edit a game, a document, a fact, a
  limit. A limit he adds arrives unconfirmed and stays so until she confirms it on the 7". A save made against an old
  revision shows both versions and keeps what he typed.
- A fact I got wrong in the design and corrected: Cloudflare's current documentation names `Cf-Access-Jwt-Assertion` as
  the header Access sends and names no email header. The archive reads the JWT's `email` claim and does not verify the
  signature, which is safe only while every route to its port is behind the Access application. The RUNBOOK says so,
  and says to create the Access application before the tunnel route. A Cloudflare service token expires on the
  duration chosen; when it does the deck's sync stops with an authorization error until it is refreshed.
- Tests, each with guards broken on purpose that a test then caught: server 96, pages 84, installer 29 (the pinned fonts
  fetched for real once and checked against their checksums), layout 304 in headless Chromium. Three guards first went
  uncaught (a token with the right id and the wrong secret, a raw `../` out of the fonts folder, a 500 page); each got a
  test.
- D3, the deck's sync. Per record, the deck's version, the archive's and the last version both held decide: push, pull,
  agreed, or a conflict. A conflict (both changed it, an edit against a removal, two versions with no history) writes
  nothing on either side and is kept with both versions and what they started from. Every write is a compare-and-set
  on the revision the run saw, so a record that moved meanwhile is judged again, never forced. Thrown-away state, a
  rebuilt deck, or an archive restored from an older backup means "compare everything from the start". Manuals go
  both ways by hash in 8 MiB parts and resume. gatbox-sync reaches the deck's library only through gatbox-web's new
  loopback-only routes, so it never opens the library or writes the manuals folder. The first run is a dry run that
  prints the plan.
- Mistakes the tests caught on the way: the base was settled from a record a side hadn't changed (so a quiet record
  forgot what it had agreed), and a restored archive wasn't noticed because the saved cursor lagged the deck's own
  writes (the fix keeps each library's newest cursor at the end of a run and compares it at the start).
- D4. LIBRARY → CONFLICTS on the 7": each kept conflict as BEFORE / THIS DECK / THE ARCHIVE with the differing fields lit,
  KEEP either, or EDIT the deck's version first (games). A choice is a note the sync applies on its next run only if both
  sides are still what she saw, judged on that run's own plan (so an EDITed game is judged as edited). A CONFLICT badge on
  the game and in the list, and SYSTEM's Library tile says how the sync went, what is waiting, the conflicts and whether
  the two libraries are identical. Every dump image goes up as a file. Games added after the seed get their ROM list from a
  small job over MAME's own driver list (hashes only, no network), asked for through a spool like FIND A MANUAL.
- Things worth knowing: the dashboard test needs matplotlib, absent here, so it ran in a venv; its first runs hung because
  every `open` leaves a tab with a live feed (Chromium allows six connections a host), so the CONFLICTS cases run in a fresh
  browser; a stale sheet was clicked by one wait until each wait named its sheet by title.
- Not done: D5 to D7 (the apps' read-only copies, the vendored read-side contract, docs). Not installed anywhere. Her steps are `archive/RUNBOOK.md` (Proxmox, Cloudflare, tokens) and, on the deck, the install
  diff in BRINGUP.md (D3: the manifest files, one tmpfiles line, two lines in `gatbox-sync.service`; D4: a group, a spool, two
  lines in `gatbox-web.service` and two new units).
- Owner, next: read the install diffs and, when she wants it, the dry run on her real library.
