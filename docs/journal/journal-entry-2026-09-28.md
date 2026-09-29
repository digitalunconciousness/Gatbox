## 2026-09-28 — Phase 2 started: M0 orient + RTC read-out
- Claude Code on the Pi took the Phase 2 handoff (`gatbox-phase2-handoff.tar.gz`). `~/gatbox` is now a git repo:
  baseline commit `83ba066` = Phase 1 as running (every installed file matches the bootstrap payload).
- RTC checked read-only: charging **OFF** (`charging_voltage` 0, no `rtc_bbat_vchg`), cell BATT_V **3.13 V**.
  First boot after fitting the cell (09-27) the RTC read 1970, so the clock started at the saved 09-25 time until
  NTP. The kernel's 11-minute sync then set the RTC; on 09-28 RTC − system = 0 s.
- FOUND: the logger does **not** yet write `# mode=`, `/run/gatbox/mode`, or split files per dial mode, although
  the handoff/project CLAUDE.md say it does. Built in M4b instead.
- FOUND: two 09-25 NFL Blitz files end in NUL bytes (power pulled mid-write). The report and web view skip them.
- NEXT: M1 RTC (bootstrap guard, gatbox-rtc-sync timer, `clock=rtc` label), then the unplug + offline boot test.
- M1 RTC code done (not installed yet, waiting on the units OK): bootstrap charging guard
  (`GATBOX_RTC_BATTERY` refused; `GATBOX_RTC_CHARGE=ML2020` only), `gatbox-rtc-sync` timer (RTC written from NTP
  time 2 min after boot + hourly, stamped in `/var/lib/gatbox/rtc-synced`), logger labels sessions `clock=rtc`
  when the RTC is trusted and notes a mid-session NTP sync, `gatbox-status` RTC line, NTP/RTC/UNVERIFIED badges in
  the web view. 36 tests pass (label logic, fake-sigrok end-to-end, guard).
- DECIDED: `gatbox-status` only asks a logger that knows `--clock-label`: an older logger would ignore the flag
  and start a second logger on the port.
- NEXT: install via the bootstrap, then the unplug + offline boot test (`clock=rtc`).
- M1 installed (09:16) and TESTED at the warehouse with no network: the RTC set the clock at boot, the logger
  skipped the NTP wait, and the session header says `clock=rtc (RTC-held time; last set from NTP 09:16:43)`.
  Back home NTP corrected the clock by only +0.65 s. Cell 3.12 V, charging OFF. **M1 done.**
- Touch on the Waveshare 7" checked before the trip: taps within 6–19 px of target (no skew, no mirroring), real
  touch events, 3 fingers tracked. Panel is on HDMI-A-2 (the Pi's HDMI1 port); M3 expects HDMI0.
- NOTE: pressing Stop, then pulling power within ~30 s, can lose the last few samples to a NUL tail (ext4 delayed
  writes). The warehouse file lost one sample; 09-25 lost ~7–15 s. Fix candidate: sync at session end.
- NEXT: M2 (repo layout, bootstrap shape, minipro, MAME, T48 re-verify). Needs the Forgejo URL.
- M2 started: repo moved into `backend/ tools/ bootstrap/ data/ docs/ tests/` (git mv, history kept). The bootstrap now
  installs straight from the checkout: a file table, writes only what differs, restarts only services whose files
  changed, skips apt when everything's present (offline re-runs are safe), `--check` shows what a run would change.
- DECIDED: bootstrap installs from the git checkout instead of inlining sources into heredocs (one copy of each file;
  no hand-splicing). The claude.ai project gets the GitHub repo instead of one self-contained script.
- DECIDED: repo on GitHub, `digitalunconciousness/Gatbox` (public), not Forgejo.
- Logger SD fix: session file synced to the card every ~10 s and at session end (09-25 lost 7–15 s to NUL tails).
- minipro isn't packaged in Trixie: bootstrap builds pinned tag 0.7.4 (test-built here; T48 supported) and installs
  its udev rules (T48 → plugdev). mame 0.276 from apt for `-romident`.
- Repo pushed to GitHub (`digitalunconciousness/Gatbox`, public) with a repo-only deploy key. History scrubbed before
  the first push: noreply identity, UTC timestamps, no names, home network or workplace roster.
- DECIDED: no personal info on GitHub, ever. Site values stay in a git-ignored `SITE.local.md`, the roster stays
  git-ignored on the Pi, and a local pre-push hook enforces it.
- M2 installed: new logger (SD sync) + minipro 0.7.4 (built, T48 supported, udev → plugdev) + mame 0.276. Only the
  logger restarted; a re-check says the Pi matches the checkout.
- MAME `-romident`: ~8 s and ~485 MB RAM per lookup. FOUND: an all-FF blank "matches" real sets (ColecoVision blank
  halves), so the dump tool must flag blank/all-FF reads before trusting a MATCH.
- NEXT: ⏸ T48 re-verify (known EPROM, dump twice, SHA1s match), which closes M2.
- ⏸ T48 re-verify PASSED on the fresh card: SegaSonic EPR-15781C (marked 27C020-15). minipro's chip-ID check said
  it's a TI TMS27C020 (ID 0x9732), not the generic 27C020, so it was read as `TMS27C020@DIP32`: two identical reads,
  SHA1 9f524012… = MAME `sonic` epr-15781c.ic18, and `mame -romident` names it. **M2 done.**
- DECIDED: ROM dumps never go in the public repo (`*.bin` ignored, pre-push refuses them). Dumps live outside it
  (`~/t48-dumps/` now, `/srv/gatbox/roms/` from M7).
- NEXT: M3, the 7" panel as a boot-to-kiosk dashboard. Panel is on HDMI-A-2 (the Pi's HDMI1): move it to HDMI0 or
  target HDMI-A-2.
- M3 code (not installed yet): the 7" works on **either HDMI port** with nothing to configure (EDID mode on both,
  touch unmapped). Pi OS's autotouch had pinned touch to one USB + one HDMI port with mouse emulation; it's turned
  off for the user. Boot-to-kiosk Chromium via XDG autostart, `gatbox-kiosk on|off|start|status`, and a local-only
  "Hold to exit kiosk". Never blanks while the kiosk runs. 27 kiosk tests pass.
- DECIDED: touch stays unmapped with real touch events (no mouse emulation). The M5 graph needs multi-finger gestures.
- FOUND: the first reading after a dial change can be junk (726.4 V AC for one sample). M4b drops/flags it.
- M3 installed and ⏸ TESTED: reboot → kiosk on the 7"; EXIT KIOSK (long-press) closed it; `gatbox-kiosk off` →
  desktop after reboot; `on` → kiosk again. Touch stayed unmapped (autotouch off). Power under full CPU load with
  the kiosk and panel on the Pi's USB: 5.11 V minimum, no throttling, 61.5 °C. **M3 done.**
- NEXT: M4 (backend: replay harness, profiles/machine data model, logger header + marks + per-mode files, report
  powered/OV/marks, JSON API + SSE). The first reading after a dial change gets dropped/flagged there.
- FOUND (09-25 GL log, re-read for the M4 regression fixture): the "~25 s at ~20.28 V in two bursts" isn't in the
  data. There are **three single readings** over 5.775 V: 18.667 V (03:22:52), 20.277 V (03:22:53), 20.284 V
  (03:23:17), each **one sample at the instant the rail came back on**, between mV readings. They fit the rising
  edge if the decimal point is off: 186.67 mV between 92.3 → 246.7 mV, 2.0277 V between 676.3 mV → 5.018 V,
  2.0284 V between 2.9 mV → 5.021 V. So they're almost certainly **UT61E autorange glitches** (right digits, wrong
  range during a fast change), not a real 20 V and not a backfeed.
- The rest of 03:22:48–03:23:17 is the half-off switch bouncing the supply: collapse at 03:22:48 (capacitor decay
  1.17 V → 92 mV), back at 03:22:53 for ~4 s at 5.02 V, collapse again at 03:22:58 (decay to 2.9 mV), back at 03:23:17.
- Gameplay (02:16 → 03:22:47, 8015 samples): **5.020–5.024 V, mean 5.022 V**. That's 100% inside the GL spec (4.90–5.10) and
  mid-window, not "at the bottom". The 4.936 V session mean came from the 62 s at 0 V before the cab was switched on.
  So the +5V rail doesn't need a trim, and slow sag is ruled out for this crash (a 2 S/s DMM still can't see ms dropouts).
- DECIDED: over-voltage handling. The report tags a lone reading beside a range change as "suspect" (likely an
  autorange glitch). The live alarm needs 2+ consecutive readings over the limit; a lone reading is an amber SPIKE.
  An ALARM ON/OFF switch covers probing around on games with odd voltages.
- M4c report: header window/limit/profile/machine, powered vs off with POWER CYCLES (the slopes of a power-off are
  part of the cycle, not excursions), OVER-VOLTAGE with suspect tagging, MARKS, `--json`. The 09-25 file now reads:
  4 power cycles, rail 5.009–5.024 V (100% in window, also at the GL spec), 3 suspect glitches, no excursions.
- Kiosk SHUT DOWN button (owner's request): a 3 s hold on the 7" powers the Pi off cleanly (the logger stops normally,
  files synced). Pi screen only, never from a phone. No new permissions needed: the desktop session may power off.
- M4a–c + SHUT DOWN installed and rebooted (17:44). The bootstrap check came back clean, the clock came up from the
  RTC, and the kiosk started on its own.
- M4d code (not installed yet): gatbox-web grew into a package with a JSON API under `/api/`. It has live readings
  as a stream (SSE: every sample as it lands, file changes, over-voltage events, heartbeats); the meter state
  (mode, value, profile, window, machine, dial check, leads-reversed, alarm); profile / machine / ALARM ON/OFF
  switches; marks; NEW; captures; the session list with the report's own numbers; the Pi's health; USB devices;
  and roster entries with their platform, critical actions and rail spec. Every old phone URL still works. The
  phone view and PDF now use each file's own window.
- DECIDED (applied): the live alarm fires on 2+ readings in a row over the limit; a lone one is an amber SPIKE; OL
  never counts. ALARM OFF comes back on when another profile is picked, so an overnight log can't start with the
  alarm silenced from probing.
- FOUND: on this Pi 5, `vcgencmd` talks through `/dev/vcio_gencmd` (video group), not `/dev/vcio`. The web
  service gets the video group and a private /dev that holds only that one node, so it can't even see the
  meter's serial port.
- FOUND by the new tests: a mark pressed in the same second a file started was labelled as made during the file.
  The logger now compares to the millisecond.
- NEXT: install M4d (the owner OKs the web unit change first), then M5, the dashboard at `/dash/` with the live
  graph (real time, tap for a reading, zoom).
- M4d installed (19:32, owner OK'd the web unit change). `vcgencmd` works in the sandbox. FOUND: nothing in the
  sandboxed web service can reach D-Bus, so `timedatectl` and `nmcli` fail there. The SYSTEM page now reads the clock
  status from the kernel, and Wi-Fi and hotspot from `iw`. It also names anything it couldn't read.
- M5 dashboard built at `/dash/`, and the kiosk opens it:
  - METER: a big live reading, profile picker tiles, banners (SET DIAL TO, LEADS REVERSED?, HOLD, the A-jack
    reminders), MARK / NEW FILE / ALARM ON-OFF / CAPTURE, and the live graph from the 09-28 wish list (real time,
    tap for the reading at that moment, pinch/drag to zoom, LIVE to follow again).
  - SESSIONS: the report plus the same zoomable graph over past logs.
  - MACHINE, SYSTEM (with EXIT KIOSK / SHUT DOWN), DEVICES (NOT FITTED greyed), and DUMP, greyed until the T48 is
    plugged in.
- Over-voltage on screen: 2+ readings in a row take over the screen in red until ACK; one reading is an amber SPIKE
  note. The 09-25 20 V glitches play back as three SPIKEs and no alarm.
- FOUND: headless Chromium hung on every web page because it waits on the desktop keyring for its cookie key;
  `--password-store=basic` fixes it (the kiosk already had it). The dashboard tests drive Chromium directly, step
  through every state from the spec with the replay harness, and screenshot each one at 1024x600. I looked at them
  all and fixed what they showed.
- The owner found the report's wall of text ugly and distracting, though she likes the info. Reports now open with
  a verdict (HELD THE WINDOW / LEFT THE WINDOW / OVER-VOLTAGE), number tiles and short tables. The full text is one
  tap away, and the PDF still has all of it. The session list no longer says "left the window" just because the
  board was switched off; it shows the report's verdict.
- NEXT: install M5, the owner tries it on the 7" (⏸), then M6 (scanner).
- ⏸ M5 PASSED on the real panel after a reboot: the 7" opened the dashboard, the owner picked a profile and started
  logging from it ("everything looks rad"). **M5 done.**
- M6 (scanner) started. The EY-H2 reads as USB af99:8002 ("Totinfo TOT2D PRODUCT HID KBW"), a plain keyboard. Test
  scans from a product barcode and from a small Katasymbol label (`GATBOX:MARK`, 4–5 inches away) decoded exactly.
- M6 code (not installed yet):
  - `gatbox-scand` takes the scanner over so scans never type into the kiosk, and hands each code to the web server.
  - Scanning a cabinet sets the machine, GATBOX:MARK drops a mark, GATBOX:NEW starts a new file, anything else pops
    up as UNKNOWN CODE.
  - DEVICES shows whether the scanner is held.
  - MACHINE → LABEL LIST shows the text for every label with a COPY button, for making them in the Katasymbol app.
    A printable PDF sheet is the fallback.
- DECIDED: labels come from the owner's Katasymbol label maker, typed or pasted into its app (no CSV import).
  GATBOX doesn't drive the printer.
- ⏸ M6 PASSED with labels from the Katasymbol app:
  - The DDR cabinet label read (DDR was already the machine, so nothing changed, as designed).
  - GATBOX:NEW started a new file, still tagged DDR.
  - GATBOX:MARK put a mark in the live log, and it shows on the morning report's plot as "MARK (scan)".
  - Scans no longer type into the kiosk: the scanner service holds the scanner. **M6 done.**
- DECIDED: the wired MARK push button waits for an enclosure. The pin plan is kept: GPIO17 on pin 11, ground on
  pin 9.
- NEXT: M7, the T48: pick the chip, label it, read it twice and compare, identify it with MAME, archive it under
  the machine. The DUMP tab lights up when the T48 is plugged in.
- T48 firmware updated from 1.03 to 1.32, the version minipro expects. The file came from XGecu's official software
  release that minipro's own script names, and was checked against its checksum before flashing. The T48's
  self-test passed on every pin afterwards. This also brings the T48's own chip-programming routines up to date for
  M7.
- M7 code (not installed yet): the dashboard's DUMP tab.
  - Pick the EPROM family, then the exact name printed on the chip, type its label, and tap DUMP.
  - A separate locked-down service does the reading. The result card says MATCH (with the MAME set and ROM) or NO
    MATCH, and the dump is archived under the machine.
  - If the chip's maker doesn't match the name picked, it stops and offers a one-tap retry with the name minipro
    suggests.
  - The command line `gatbox-dump` already works on the real T48: the SegaSonic chip came back MATCH
    `sonic/epr-15781c.ic18`.
