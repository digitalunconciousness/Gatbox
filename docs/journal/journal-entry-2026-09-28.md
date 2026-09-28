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
