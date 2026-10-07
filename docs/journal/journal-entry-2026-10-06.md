## 2026-10-06 — Wi-Fi without a keyboard; coin-door reporting; orders from the bench
- Phase 5 merged (GATBOX PR #7). Three ways onto a network nobody coded into the Pi: scan the
  Wi-Fi QR a phone makes from "share this network", the 7" SYSTEM → Wi-Fi card, or the desktop.
  The desktop is deliberately the one nothing in this phase can break, which is why the runbook
  names it as the way back in.
- A key goes to nmcli and nowhere else — not the journal, not the status file, not any endpoint,
  not the recent-scans list, which says `WIFI:<redacted>`. Asserted by value, because an earlier
  version of that test grepped for field *names* at a point where the field was null and would
  have passed against a response carrying the key under any other name.
- The hotspot now comes back on a two-minute timer instead of needing a reboot, and backs off
  10/30/60 minutes for a saved network that keeps refusing it. Without the backoff a changed
  password at a venue makes the box drop its hotspot every two minutes for ever, cutting anyone
  connected to GATBOX each time.
- ONE GAP, WRITTEN DOWN NOT FIXED: nmcli takes the key as a command-line argument, so for the
  few seconds a join lasts it is visible in `ps`. Local-only on a single-owner bench box.
  Closing it rewrites the one path that has to work and needs a real join on the Pi to verify,
  so it is in `docs/network.md` as a known gap rather than patched blind.
- The pre-push hook had been refusing this branch since the Wi-Fi code landed, and I had
  reported it clean: its pattern matched `psk = body.get("psk")`, which is code, not a secret,
  and the dry run that said "clean" was fed a ref line git would not produce, so the hook
  inspected nothing. Fixed both ways — it now wants a literal, and it catches a quoted JSON
  `psk`, which it never did. Four cases added to tests/test-hooks.sh so the suite owns it.
- Phase 2.5 on the hub (tracker PR #15): a maintenance request filed with no login by whoever
  can open the coin door. A per-machine token, 32 hex from a CSPRNG, on its own label sheet that
  goes *inside* the door. The plan said one label could serve both purposes; it could not — the
  cabinet labels are public, so that would have printed a credential where a customer could
  photograph it.
- Phase 6, today's build: MACHINE → Work orders → NEW ORDER. Type the fault on the keypad, pick
  how urgent, say whether the last finished trace goes with it. If the hub already has an order
  open for that machine the card offers ATTACH LAST TRACE instead, which was the owner's ask —
  the second measurement belongs on the order that exists. Filing a separate one stays
  available: two faults on one machine are two orders.
- DECIDED (owner): free typing for the fault, not a list of canned faults. Slower on a kiosk
  keypad, and hers to choose.
- The Pi only queues an order; `gatbox-sync` reaches the hub. An order reads queued until the
  next run, then sent, and CANCEL works only while queued — once the hub has it there is no API
  to withdraw it, so that is the only undo there will ever be.
- `gatbox-sync` used to retry a refused item for ever. Survivable before today, because almost
  everything refusable cleared on its own; not survivable now, because an order for a machine
  the hub's roster lacks is refused identically every two minutes. Five refusals and it parks,
  with the hub's reason on the Hub tile and `GATBOX_SYNC_UNPARK=1` as the way back.
- Nothing here needed a new systemd unit, user or spool: gatbox-web owns the outbox and
  gatbox-sync fetches it over HTTP, the same way it already fetches the session list, because
  it cannot read that process's files at all.

### The afternoon: what broke, and what the tests did not cover

- **The kiosk would not start after the update, and that was my doing.** Phase 6 added
  `backend/gatboxweb/orders.py`; `server.py` imports it; the bootstrap's MANIFEST names every
  module in that package by hand and I never added the line. So the Pi got the new server
  without the module it needs and `gatbox-web` died on import. Hard rule 4 is "one install
  path" and I put the file in the repo only. The logger was never affected — it loads
  `gatboxlib`, not `gatboxweb`.
- The real fix was the test. `test-bootstrap.sh` had a per-file check naming `wifi.py` alone:
  a hand-maintained list beside another hand-maintained list, which is how this got through 29
  green checks. It now reads both package directories and asserts every `.py` in them is in
  the MANIFEST. `bootstrap: 29 → 49`.
- **The on-screen keyboard: three complaints, one cause.** The symbols were extra rows
  *below* the letters, so on a 600 px panel with no scrollbar they pushed the confirm button
  off the bottom — there was no visible way to save. And they were only offered to two of the
  twenty-one places on the dashboard that type text, so no two keypads were alike.
- Now one keyboard everywhere: six rows always, with `?#+` / `ABC` swapping the symbol layer
  the way a phone does, so the height never changes and nothing moves under a finger. CANCEL
  and SAVE live outside the key grid and cannot be pushed off it; a test measures SAVE's
  bounding box against the viewport rather than trusting that it fits. OK is now SAVE, because
  that is what the owner went looking for. The `symbols` option is gone rather than defaulted
  — an option is how two of them diverged in the first place.
- DECIDED: the case key reads `aA`, not `abc`. The layer key has to read `ABC` to mean "back
  to letters", and two keys reading abc and ABC side by side is a coin toss.
- **The scanner will not read a Wi-Fi QR off a phone screen.** Proved rather than assumed:
  nothing at all reached `gatbox-scand` while the code was held up on the phone, and a printed
  cabinet label scanned on the first go a minute later. Density, not a fault — a label carries
  a short URL, a Wi-Fi code carries an SSID *and* a passphrase. Written into `docs/network.md`
  with the one-step way to tell a scanner problem from a screen problem.
- Looking into it turned up two real bugs on that path anyway, neither of which was the cause:
  `gatbox-scand` buffered 512 characters while the web refused over 200, and a Wi-Fi QR can
  reach 208 once both fields are escaped; and a second scan while one was still queued raised
  `Busy` uncaught, so it answered **500** — exactly the state after a join that has not been
  taken yet, which is exactly when someone scans again.
- Three times today a test caught me rather than the code: a long-payload test that lost a
  backslash level in the shell and asserted nothing, worst-case arithmetic that forgot the
  SSID is escaped too, and a parked-item check that ended in a shell assignment whose exit
  status is always 0. Each passed while proving nothing until it was rewritten.
