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
