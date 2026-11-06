## 2026-11-05: The archive goes live, the deck syncs with it, the apps read it; the disk imaging branch merges
- First real run of `archive/RUNBOOK.md`: the container and its data volume, the Access application and tunnel, the tokens,
  the deck's `archive.conf`, a dry run, then the real sync. The dry run listed a ROM list for every game and two ROM
  images; the archive's log then showed the file uploads answered 200. cabcrawl and the Tech Tracker were deployed with their library settings and
  timers. cabcrawl's machines were linked from the seed plan; the tracker has one asset keyed so far, with its game added
  at the archive by hand.
- Mistakes of mine that the run found, each now fixed in the repository or the RUNBOOK:
  - The bootstrap never made `/srv/gatbox/library`, so `gatbox-library seed --apply` stopped with "Permission denied".
    The tmpfiles line and gatbox-web's `ReadWritePaths` are in the bootstrap now.
  - The container's create wizard has no data volume, so the mount point is added after the container exists. The RUNBOOK
    says how, from the web UI as well as `pct`, and has the Cloudflare steps for a terminal.
  - `gatbox-archive token add` run as root left a token file the service could not read, so every token failed with no
    message. The command now carries on as the data directory's owner.
  - The deck's archive client followed a redirect and then fell over on an answer that was not JSON (`'bytes' object has no
    attribute 'get'`). It never follows a redirect now, and says in words that Access sent it elsewhere (and that this
    usually means the service token was not accepted), or that the answer was not JSON.
  - `install.sh` restarted the service only when its own files changed, so editing the private listener's address in
    `archive.conf` did nothing. It keeps a checksum of the conf the service was last started with and restarts when it
    differs; `--check` lists the restart.
- Cloudflare, three ways it went wrong. A service-token secret was cut short in the copy: Access answered every request
  with a 302 to its login page and the token's "Last seen" stayed empty; rotating it and checking the length before
  saving it fixed it. The Access application, when recreated, needed the deck's Service Auth policy attached again or
  the deck got 302s.
  And for a while a request for `/` from someone not signed in got the archive's own 401, not Access's 302, which means
  Access did not cover that path. That mattered: the archive reads the identity from `Cf-Access-Jwt-Assertion` without
  verifying a signature, so a header made up by anyone would have been believed. It was noticed because the pages said
  "Not signed in" to a browser that had worked earlier. Recreating the application closed it, and a forged header now
  gets the 302. The RUNBOOK has the two checks and the forged-header test. The owner chose not to add signature
  verification to the archive.
- What the apps do, which the apps' own pages did not make obvious: a pull asks the archive only for the games its
  machines or assets name. With none named it makes no request, so `changed=0 … ok=True` proved nothing about the
  connection. And a game has to exist in the archive: the deck sends the barcade's roster, and a machine that is not on it
  (here, a customer's machine in the shop) needs its game added by hand on the archive first. I had not said so, and
  the tracker's page said "the library sends that game on the next pull" with nothing to tell the key was unknown.
  Also, `deploy.sh` reads the env file as a shell script: cabcrawl stops in its preflight with `<token>: command not
  found`, and the tracker says "can't reach the database" with an `export: not a valid identifier` line above it, when the
  token landed on a line of its own. Notes are in each app's RUNBOOK.
- Two wrong diagnostics of mine, for the record. I read an empty first page of the feed as "the archive has no such game"
  from a request with `limit=3`; the feed filters by game after it takes a page, so a small limit can return nothing with
  `more` true. The RUNBOOK's walker follows every page. And I gave a `read -rs` prompt without saying it waits silently
  for the paste, which looked like a hang.
- Disk imaging, branch `disk-imaging`, merged into main. Main had moved by phases B, C and D; five files conflicted and
  each was resolved by keeping both sides. Two things git could not see: both branches had added a function called
  `imagesCard` to `dash.js`, so the library page called the DISK tab's version and its ROM images card never drew (the
  DISK one is `diskImagesCard`, and the dashboard test now fails on a repeated function name); and the dashboard test
  stalled at exactly six connections to one host, the browser's limit, because every tab it opens keeps a live stream
  (the 11-04 entry hit the same thing). The harness gained `close_others()`, called once after the DISK section.
  `test-dash` passes 195, `test-disk` 93. Hard rule 6 now names the disk write next to the T48 burn (the owner's decision
  of 2026-11-02). Merging installs nothing on the Pi beyond two Python modules; the CLI and the units wait for Task 4.
- Not done: Task 0 (the read-only script for the Pi, in stages: the dock empty, the 4 TB drive in, a donor drive on the
  dock), Task 3 (the MAME check on an image, which waits for Task 0's `chdman` output) and Task 4 (the install diff:
  units, udev rule, sysusers, tmpfiles, the store's mount unit). Whatever the bootstrap would still change on the deck
  is not recorded here yet; `bash bootstrap/gatbox-bootstrap.sh --check` says.
- Owner, next: Task 0 stage 1, and the output of `--check`, pasted back.
