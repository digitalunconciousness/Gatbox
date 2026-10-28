## 2026-10-27 — Phase 8 passed on hardware; STOP LOGGING on the METER
- The second hub joined (late on 10-26): a device token minted on it (its own `create_device.py`, run with the
  app's virtualenv and its systemd environment file, since the script needs the app's settings), one file in
  `/etc/gatbox/hubs.d/` on the Pi. The first sync reached both; the second hub lists `jobs`, so its machines get
  the JOB card. Rosters 98 and 4, no machine on both, nothing held.
- ⏸ Phase 8 exit PASSED (owner: "The traces go through from gb to the other sites"): one session on a customer
  machine and one on a barcade machine, a minute apart. Each went to its own hub only (the Pi's two sent
  ledgers say so) and showed up on its own site.
- TOUCH TEST, first two runs: mean offsets (−7, 0) and (+6, +11) px with ±6–9 px of spread. They disagree, so
  it's how the screen is looked at and tapped, not the panel: no calibration. Keys are 50 px and buttons 56+,
  so ±11 px lands inside them. Re-run once the panel sits in its box.
- Owner: "there should be a way to stop logging from the meter screen, so I don't have to go searching through
  the settings to stop." STOP LOGGING now sits by the logging line on METER while it logs (asks first, like
  SYSTEM's); once stopped, START LOGGING is where NEW FILE was.
