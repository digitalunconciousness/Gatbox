## 2026-11-03 — Phase B built: the library on the deck (repo only, nothing installed)
- Owner: "OK: LIBRARY replaces MANUALS". Spec `docs/plans/2026-11-03-phase-b-library.md`; branch `phase-b-library`,
  slices B1–B8, each with tests that fail without it and guards broken on purpose.
- The library is one SQLite file (WAL) of records with revisions, hashes, compare-and-set and tombstones: games,
  documents, MAME sets, ROM lists, ROM images, rail limits, facts. Format and vectors in `docs/contract/library-v1/`.
  The rail logger never opens it: gatbox-web keeps a flat export of the confirmed limits (`library-specs.json`) and
  the logger reads that, as it read the files before. A damaged export, or no library, falls back to the files.
- `gatbox-library seed --plan` is read-only and writes its plan to the owner's home, never git; it flags slugs
  that look like one game, ids it had to change, an http source, and a limit confirmed at other numbers than the
  list (the logger would stop using it). `--apply` is one transaction, create-only, and a second run changes nothing.
  An unseeded deck behaves exactly as today.
- LIBRARY on the 7" replaces MANUALS: ALL GAMES, MISSING (no manual), + ADD GAME, EDIT GAME (the key is permanent
  and shown before saving), FIND GAME (MAME's own title list, offline), USE IT FOR <machine>, a ROM images card per
  game, and FIND A MANUAL. Edits are the Pi's own screen only; a phone reads and can tick the swap checklist.
- CHECK on a dump: MAME's answer, the game's chips, her marked images, the machine's earlier dumps. KNOWN GOOD /
  KNOWN BAD marks live in the library on the image's SHA-256. SYSTEM has a Library tile with LOW at under 2 GB
  free or 90% used on /srv.
- FIND A MANUAL: gatbox-web never goes online. It writes a request to a spool and a separate job (own user,
  archive.org only, a real PDF only) answers; a finished download becomes the game's document. The real archive.org
  search and item listing were read once to confirm the shapes; no download was made from the build machine.
- Not done: a game added after the seed has its MAME set noted but no chip list until MAME's list is built from
  the library's games (CHECK says NO CHIP LIST until then). Not installed: the units `gatbox-manuals.path` and
  `.service`, the `gatbox-fetch` user, two spool/library tmpfiles lines and three lines in `gatbox-web.service`
  (the install diff is in BRINGUP.md, phase B). Hard rule 5: she sees it first.
- Owner, next: read the seed plan (`gatbox-library seed --plan`) before anything is applied.
