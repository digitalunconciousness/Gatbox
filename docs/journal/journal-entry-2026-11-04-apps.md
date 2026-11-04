## 2026-11-04: Phase D, D5 to D7 built: the apps' read-only copies and the contract they read (repo only, nothing installed or deployed)
- Owner: "Go" (D4 was shown, then D5 to D7). cabcrawl on `phase-d-library`, the Tech Tracker on `phase/13-library`, each stacked on
  its phase C branch; the read side's contract lives here in `docs/contract/library-v1/read/` and is vendored byte-identical into
  both (`contract/library-v1/`) with drift tests, like contract v1.
- What an app does: keeps a read-only copy of the games its machines name. It asks the archive for `models=<its own keys>` with a
  reader token, replaces each record with the one it is sent, removes tombstones, follows a retired game to its survivor, and fetches
  each document's PDF in 8 MiB parts that resume. A key a machine gains is read from cursor 0 once; the others from the saved
  cursor. A cursor above the archive's `head` means the archive was restored from an older copy: every key is read again from 0 and the
  copy is trimmed to what that read found, but only once the read has finished, so one that dies half way changes nothing. An archive
  out of reach keeps the copy and says so; a refused token is the one answer a person has to act on (the timer's unit fails, the pages
  say it). Without an archive configured there is no library section anywhere and the job does nothing.
- cabcrawl: `Game.model_key` (managers set it on the machine page, or once from the deck's seed plan with `scripts/link_model_keys.py`,
  which shows before it writes and leaves a key set by hand alone), one generic `library_record` table (the archive's data as sent, so a
  new field needs no migration), the PDFs in `uploads/library_files/` behind the login, the machine page's "Manual and limits", a Library
  page for any signed-in role, and GATBOX's roster now carries each machine's key. Tech Tracker: the same on PostgreSQL (migration
  0027, `grant_app()` and the expected grants, a CHECK that holds `library_state` to one row), PDFs in its file store, the asset page's
  section with "Edit it in the library" for owners and techs, pointing at the archive's own page.
- Things I decided that she may want otherwise: who wrote or confirmed a record (`by`) is stored and never shown, because the archive
  fills it from the signed-in person's address; a limit with no confirmation shows as "Unconfirmed" with a note not to judge a board by
  it; the library list is for every signed-in role in cabcrawl, and the Tech Tracker has no list page, only the section on an asset.
- A browser's own PDF viewer can be stopped by the pages' `object-src 'none'`; the PDF route sends its own `frame-ancestors` only.
  That is reasoned, not tried in a real browser here (headless Chromium has no PDF viewer to check).
- Tests: cabcrawl 69 library tests, whole suite 2027 passed, 1 skipped, 1 failed (the sibling-checkout contract diff); Tech Tracker 57 library tests, whole suite 606 passed, 6 skipped, 1 failed (the sibling-checkout contract diff) on PostgreSQL; this repo's `test-archive-read.py` 19 (the real archive reproduces every
  example). Each app's stub archive starts from the contract's own examples, and one test per app asks it the contract's own requests
  and compares the answers, so a stub can't drift from the real archive. Guards broken on purpose: 33 in cabcrawl, 36 in the Tech Tracker; every one fails a test, except two in the Tech Tracker that cannot change behaviour (the manual route's own login check is redundant with the app's global login gate, and its limit data has no "confirmed by" to leak). Eleven first survived and each got a test.
- Two mistakes of mine the checks found in what I had already pushed or committed: the read contract's example manual was a `.pdf` that
  the archive's test generated and `.gitignore` swallowed, so it never reached this repository while CHECKSUMS listed it, and the apps'
  shared privacy check refuses a `.pdf` by rule; it is `examples/manual.pdf.b64` now, written by the test and checked against the PDF
  the archive serves. And the pull job would have followed a redirect with the reader token (an Access policy answers an unauthenticated
  request with a 302 to its login page); it now stops and says where it was sent.
- Mistakes the tests caught on the way: a cut-off manual download threw away the bytes that had arrived (it now keeps the front of the
  part and resumes from it), an archive restored to an older copy left records the old copy never had (the trim above), a float column for
  the archive's timestamp failed the Tech Tracker's no-floats rule (a timestamptz now), and a test client reused the first user's login
  because one app context stayed open (the tests sign in once per test).
- Screenshots of the new pages at 390 and 1280 (`take.py --library`, `csp_check.py --library`); the machine page at 390 showed "1 pages" and
  "0.0 MB" for the tiny example file, now "1 page" and "1 KB".
- Not done: nothing is merged, deployed or installed. Her steps are in each app's RUNBOOK (cabcrawl: the library section; Tech Tracker: §12):
  mint a reader token at the archive, put the address and token in the app's environment, read the two unit files, install the timer,
  and link the machines. The apps' own dashboards do not show the pull's state beyond the page text and `pull_library.py --status`.
- Owner, next: the D3 and D4 install diffs and `archive/RUNBOOK.md` are still the gate; the apps' steps follow the archive being up.
