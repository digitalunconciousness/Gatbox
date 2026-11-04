## 2026-11-03 — Phase C built: dumps and burns go to their world (repo only, nothing installed)
- Owner: "OK as written" on the phase C spec (`docs/plans/2026-11-03-phase-c-roms.md`). Branches `phase-c-roms`
  (here and in cabcrawl) and `phase/12-roms` (the Tech Tracker); the contract is GATBOX's and was vendored first.
- Contract v1 gained, additively, a `roms` capability: a resumable file upload by hash (8 MiB parts, 32 MiB a file),
  `rom_dump` and `burn` items with uid rules, and a read-back of a machine's dumps and burns. Examples are the schema
  (32 invented bytes as hex); each repo's contract test reproduces the uids and hashes. I broke each new check on
  purpose, six ways here and every hub guard in both apps: each one fails a test (the survivors were reasoned through:
  two are covered by the app's global login gate and default-deny test, one is a header the app sets everywhere, and
  one was dead code, which I removed).
- Both apps store the images by hash outside any URL, verify size, SHA-1 and CRC32 against the stored bytes, take
  `order` only for that machine's own order, and list dumps and burns on the machine or asset page and on the order or
  job page; downloads are behind the login (cabcrawl: operator and up). Real PostgreSQL for the Tech Tracker, SQLite
  and the migration round trip for cabcrawl.
- The Tech Tracker's tests found a contract wrinkle: the example uids hash the machine slug and its asset tags are its
  own, so a hub whose test machine isn't called `widget-wars` substitutes the slug and recomputes the two uids. The
  README says so now.
- On the deck: gatbox-web reads the archive for the sync only on loopback (`/api/sync/dumps`, `/api/sync/burns`,
  `/api/sync/image`; a request from another address is 403, tested from 127.0.0.2). gatbox-sync uploads the image,
  then posts the item, routed and guarded by roster like a session, with its own state per hub; a cut-off upload
  resumes from the parts the hub has, and a refusal counts and parks. The 7″ says SENT TO <hub>, WAITING, HELD or
  REFUSED beside each dump and burn, SYSTEM counts them, and DUMP and BURN have a FOR ORDER chip (the machine's one
  open order preselected, a tap clears it).
- Found while building: a chip read again is "already archived", and the hub keeps one record per dump, so it can't
  be linked to a new order. The result now says so ("not linked to order #N") instead of dropping the link. A real
  bug caught by the tests: images in `_images/` (a burn's usual source) were refused by the sync's name pattern.
- Not done: the deck pulling its dumps back from a hub on a rebuild (the hubs serve them now; D does the deck's side).
- Owner, next: merge the two apps first (each runs its migration at deploy), then this repo and run the bootstrap. Until
  a hub lists `roms`, the deck sends nothing new.
