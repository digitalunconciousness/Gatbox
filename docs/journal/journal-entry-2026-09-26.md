## 2026-09-26 — RTC battery: primary-cell holder bought
- BOUGHT: coin-cell holder for the Pi 5's J5 RTC battery connector (Amazon), **non-rechargeable**
  cell. Not the official rechargeable ML2020.
- RULE: **trickle charging stays OFF for good.** Never set `dtparam=rtc_bbat_vchg` and never run
  the bootstrap with `GATBOX_RTC_BATTERY=1`: charging a primary cell can make it leak or vent.
  Charging is off by default; check `/sys/class/rtc/rtc0/charging_voltage` reads 0.
- CAVEAT (Raspberry Pi docs): primary cells aren't recommended on the Pi 5 because its RTC backup
  draw is higher than a dedicated RTC chip's, so the cell's life is short. It only drains while
  the Pi is unplugged. Treat the cell as a consumable: when an offline boot shows a stale clock,
  replace it.
- Setting it: after an NTP-synced boot, `sudo hwclock --systohc`, then `sudo hwclock -r` to read
  it back. Test: power off, unplug, boot with no network, and check `date`.
- NEXT (small, Pi-side): the logger still labels offline sessions `clock=unverified` even when the
  RTC held good time. Label them `clock=rtc` when the RTC reads a sane date, and write the RTC from
  the system clock after each NTP sync.
- HANDOFF: wrote `gatbox-phase2-handoff.tar.gz` for Claude Code on the Pi: `PROMPT.md` plus the
  reference files the Pi doesn't have (`ref/`). Milestones: M0 orient + baseline commit, M1 RTC,
  M2 = 2A repo/minipro/MAME, M3 = 2B panel + kiosk, M4 = 2C backend, M5 = 2D dashboard,
  M6 = 2E scanner, M7 = 2F T48 dump, M8 = 2G (ask first). It stops for every hands-on step.
- DECIDED: the RTC counts as trusted only if (a) it was set from NTP at least once (a stamp file
  written by an hourly `gatbox-rtc-sync` timer), (b) it hasn't gone backwards since, and (c) the
  system clock agrees with it to 5 s, which catches fake-hwclock's saved guess. Otherwise the
  session is `unverified`. With a trusted RTC the logger skips the 90 s NTP wait.
- DECIDED: `GATBOX_RTC_BATTERY` is removed from the bootstrap. By default the bootstrap now comments
  out any `rtc_bbat_vchg` line it finds. The only way to enable charging is
  `GATBOX_RTC_CHARGE=ML2020`, reserved for a real ML2020.
- DECIDED: the Phase 2 backend is the existing **gatbox-web, grown**, not a separate FastAPI
  `gatbox-api`: one server on :80, and every current URL keeps working. The dashboard goes at
  `/dash/`. Switch to FastAPI (apt) only if a concrete need turns up.
- DECIDED: per-machine rail limits go in `gatbox-machine-specs.json`, keyed by roster slug,
  instead of a `rails` field in the roster, so the maintenance app's import can't break.
  It starts with gauntlet-legends (+5V 4.90–5.10, +12V 11.5–12.5).
- DECIDED: one CSV = one machine + one profile + one dial mode. Scanning a different cabinet or
  changing the profile mid-session starts a new file.
- STALE: the master guide (BOM row and Phase 1 list) and the journal open items still say
  "bootstrap with `GATBOX_RTC_BATTERY=1`". That flag is going away. Fix them at the next guide rev.
