## 2026-11-04: Phase D, D1 and D2 built: the archive (repo only, nothing installed)
- Owner: "OK as written". Spec `docs/plans/2026-11-04-phase-d-archive.md`; branch `phase-d-archive`, stacked on
  `phase-b-library`. Built D1 (the server) and D2 (its pages). D3 (the deck's sync) is next; D1 to D3 go to her as a unit,
  with a dry run on her real library, before D4 on.
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
- Not done: D3 to D7. Not installed anywhere. Her steps are in `archive/RUNBOOK.md`: the Proxmox container, the install
  script, the Access application and its two policies, the tunnel, the tokens.
- Owner, next: nothing yet; D3 comes to her with the dry run.
