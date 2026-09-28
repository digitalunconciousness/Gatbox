# GATBOX: RTC follow-up + Phase 2 software (Claude Code handoff, 2026-09-26)

You're Claude Code on GATBOX's Raspberry Pi 5 (unit GDD-GAT/01), in the folder where
Phase 1 was built (it has `BRINGUP.md` and `CLAUDE.md`). The owner owns the deck. She's
technical (Arch, homelab, arcade repair): be direct, skip preamble, flag tradeoffs.

There are two jobs:

1. **RTC follow-up.** Small, and it comes first.
2. **Phase 2 software**, packages 2A–2G of `handoff/ref/gatbox-phase2-plan.md`.

This file is the spec. Read all of it, and everything in `handoff/ref/`, before you change
anything. **Resuming in a new session?** Re-read this file and the progress checklist in
`BRINGUP.md`, then carry on from the first unchecked milestone.

---

## What's in `handoff/ref/`

| File | What it is |
|---|---|
| `gatbox-phase2-plan.md` | The Phase 2 plan, revised 2026-09-26. Where it and this file differ, this file wins. |
| `gatbox-barcade-roster.json` | The roster: 83 video games and 12 pinball machines. Every entry has a permanent `slug` (QR codes use it). Data of record, shared with the owner's maintenance app. **Don't change its schema.** |
| `gatbox-machine-specs.json` | **New.** Per-machine rail limits, keyed by slug. Starts with `gauntlet-legends`. |
| `gatbox-terminal.html` | The dashboard mockup. Borrow its **layout vocabulary**, not its colours: its palette is the old flat-black one. Colours come from the CLAUDE.md tokens. Drop its Google Fonts link; the fonts are served from the Pi. |
| `CLAUDE.project.md` | The claude.ai project's CLAUDE.md. Merge in anything yours lacks (M0). |
| `journal-entry-2026-09-24/25/26.md` | Recent history: mode-aware logger/web, the first cabinet log (Gauntlet Legends), the RTC holder, and today's decisions. Also the journal format the owner uses. |

**Which copy wins.** For anything already on this Pi (the logger, report, gatbox-web,
gatbox-status, the bootstrap, BRINGUP.md), your copies are newer than anything in ref/ or the
project. On what the code does today, the Pi wins. On what to build, this file wins,
then the plan.

`handoff/` is input, not source. Add it to `.gitignore`. M2 copies what belongs in the repo
into `data/` and `docs/`, including this file as `docs/phase2-handoff.md`.

---

## Where things stand (2026-09-26)

- Pi 5 4GB + Active Cooler, Raspberry Pi OS **Trixie 64-bit Desktop** (labwc). **The desktop stays.**
- **Phase 1 is done and in use.**
  - `gatbox-raillog.service` (root) runs sigrok `uni-t-ut61e-ser` on `/dev/gatbox-dmm`.
    The chain is UT61E → UT-D02 → PL-2303, 19200 7O1. The link is **one-way**: the dial picks
    the function and software can only detect it.
  - It writes `/var/log/gatbox/rail_*.csv`, one file per session and per dial mode, plus
    `/run/gatbox/current` and `/run/gatbox/mode`. The ACT LED is the heartbeat. It waits up to
    90 s for NTP. Start/Stop flag files live in `/var/lib/gatbox-web`.
  - `gatbox-web.service` (stdlib http.server, DynamicUser, :80) serves the session list,
    range/window views, PNG, PDF, CSV, Start/Stop and local fonts.
  - Also installed: `gatbox-rail-report` (mode-aware), `gatbox-status`, and the fallback
    hotspot `GATBOX` at 10.42.0.1.
  - `gatbox-bootstrap.sh` installs all of the above.
- **First cabinet log:** `/var/log/gatbox/rail_20260925_021402.csv`.
  - Gauntlet Legends, 1 h 25 m. The clock was *unverified* (no NTP at the bar).
  - About 62 s at 0 V. About 25 s at ~20.28 V from 03:22:48, in two bursts, during a botched
    power-off.
  - The game crashed mid-run with a flat rail. The owner is still chasing it.
  - This file is the **regression fixture** below. Never modify it.
- **Owned:**
  - Pi 5
  - UT61E + UT-D02 + PL-2303
  - XGecu T48
  - **Eyoyo EY-H2** USB scanner (HID keyboard wedge)
  - **Waveshare 7inch HDMI LCD (C)** (1024×600 IPS, capacitive USB-HID touch)
  - As of 09-26, a coin-cell holder on the RTC connector (J5) holding a
    **non-rechargeable cell**
- **Not owned.** Don't write code that assumes these parts, and don't ask the owner to test with them:
  - ADALM2000
  - UUGear MEGA4 / uhubctl
  - INA226, relays
  - MAK Strike, GBS-Control
  - RP2350B bus driver
  - InfiRay P2 Pro

---

## Hard rules

1. **Never enable RTC trickle charging.** The cell is non-rechargeable, and charging a primary
   cell can make it leak or vent. That means no `dtparam=rtc_bbat_vchg` in config.txt, and
   never run the bootstrap with `GATBOX_RTC_BATTERY=1`.
2. **The logger owns the serial port.** Nothing else opens `/dev/gatbox-dmm`. Everything else
   reads the logger's CSVs and `/run/gatbox/*`.
3. **The logger always works.** the owner can take the Pi to a cabinet on any night. Never end a
   turn with `gatbox-raillog` broken or half-installed. If a change isn't finished, the
   installed version stays the old one.
4. **One install path.** Every system change goes into the repo/bootstrap and is applied by
   re-running it: packages, units, udev, config.txt, cmdline.txt, labwc autostart.
   Nothing is hand-configured. The bootstrap stays idempotent, and **a re-run with no
   internet** (the bar, the hotspot) succeeds when everything is already installed.
5. **Show before you change system config.** Before touching `/boot/firmware/*`, udev rules,
   systemd units, labwc config or anything in `/etc`, show the owner the diff and why, and wait
   for her OK. Repo-only changes don't need approval.
6. **No hardware writes from the dashboard.** Phase 2 does two kinds of action:
   - Pi-side state: profile, current machine, marks, new session, capture points, kiosk exit.
   - T48 **reads** (dumps) to disk.

   Chip programming is never reachable from gatbox-web; Phase 3 brings arming. There is no
   part auto-detect: the part is always explicit.
7. **GPIO is 3.3 V only, Pi 5 included.**
   - Use gpiozero (with lgpio). Never RPi.GPIO.
   - GPIO3 and GPIO26 are reserved (key shutdown / `gpio-poweroff` in the master guide).
   - Keep I²C (2/3), UART (14/15) and SPI free.
8. **The board under test is always external.** Nothing here energizes a board.
9. **apt before pip.** Trixie blocks system pip (PEP 668). If something truly isn't packaged,
   ask before adding a venv.
10. **Dashboard stack:** plain HTML/CSS/JS.
    - No framework, no build step.
    - No CDN: the hotspot has no internet, so the fonts come from the Pi.
    - No localStorage, sessionStorage, IndexedDB or cookies for state. State lives in JS
      memory or on the server.
11. **Design tokens** (CLAUDE.md; not the mockup's palette): bg `#150a28`, bg2 `#1f0f3d`,
    panel `#1f1240`, border `#3d1e6b`, magenta `#ff2e9f`, cyan `#7dfaff`, lime `#5ef2b0`,
    red `#ff4d6d`, amber `#ffb74d`, text `#ece3ff`, dim `#9080b0`.
    - The body is a `linear-gradient(180deg, bg2, bg)`, with scanlines.
    - Fonts: Chakra Petch (display) and Share Tech Mono (body).
    - Glow sparingly.
12. **Don't guess hardware facts.** Read USB IDs (`lsusb`), minipro part names
    (`minipro -L`), the logger's exact mode strings and free GPIOs off this Pi, or ask.
    Machine-spec numbers come only from a manual the owner cites.
13. **Secrets never go in git:** AP PSK, Wi-Fi credentials, SSH keys, API tokens.
14. **Backward-compatible, always.**
    - Every existing CSV keeps loading in the report and gatbox-web, including the 09-25
      fixture.
    - Every existing gatbox-web URL keeps working: `/`, `/s/…`, `/png/…`, `/pdf/…`, `/csv/…`,
      `/font/…`, `POST /control`. They're bookmarked on phones.

---

## How to work

- **Go milestone by milestone, in order.** For each one:
  1. Short plan.
  2. Code in the repo.
  3. Install via the bootstrap.
  4. Run the listed tests.
  5. Update BRINGUP.md (progress checklist + notes).
  6. Draft the journal entry.
  7. Commit.
  8. Send the owner a short summary.
- **⏸ = STOP.** the owner has to do something physical: plug in, print, scan, insert a chip,
  pull power. Tell her exactly what to do and what she should see, then wait.
- **Journal:** write `docs/journal/journal-entry-YYYY-MM-DD.md` in her format (see
  `ref/journal-entry-2026-09-25.md`). One `## YYYY-MM-DD — title` per day, bullets,
  `DECIDED:` for load-bearing decisions, `NEXT:` for what's unblocked. If today's file
  exists, append to it. She uploads these to the project.
- **Test before hardware.** The replay harness (M4a) is how you test the logger, report, web
  and dashboard without the meter.
- **Reality beats the spec.** If something here contradicts what's actually on the Pi, stop
  and say so. Don't bend the code to fit.
- **Keep an eye on the 4 GB of RAM.** The kiosk Chromium, the backend and `mame -romident`
  all share it.

---

## M0: Orient (no changes)

- Read `CLAUDE.md` and `BRINGUP.md`. Read the installed units (`systemctl cat gatbox-raillog
  gatbox-web`), `/usr/local/bin/gatbox-*`, the bootstrap source you maintain, and all of
  `handoff/ref/`.
- If this folder isn't a git repo, run `git init`, add a `.gitignore` (including `handoff/`
  and secrets), and commit the current state as `baseline: phase 1 as running 2026-09-26`.
  Do this before anything else.
- Diff the installed files against the repo copies. Where they differ, the installed files are
  the truth: reconcile them into the baseline.
- Merge into your `CLAUDE.md` what it's missing from `ref/CLAUDE.project.md`: tokens, hard rules
  and owned hardware. Keep your Pi-specific facts. Add this file's hard rules.
- Report to the owner: what you found, anything surprising, and your plan for M1. Keep it short.

## M1: RTC (charging off, keep it set, label sessions `clock=rtc`)

**Background.** The Pi 5's PMIC has an RTC, and J5 takes a backup cell. Charging stays off
unless `dtparam=rtc_bbat_vchg=…` is set. Raspberry Pi's docs don't recommend primary lithium
cells: the Pi 5's backup draw gives them a short life. Treat the cell as a consumable.

1. **Read-only checks.** Report what you find:
   - `ls /sys/class/rtc/`, `cat /sys/class/rtc/rtc0/{name,hctosys,since_epoch}`
   - `cat /sys/class/rtc/rtc0/charging_voltage{,_min,_max}`. These can be missing on older
     firmware; say so if they are.
   - `grep -n rtc_bbat /boot/firmware/config.txt`
   - `timedatectl`
   - `sudo hwclock -r --verbose`. Install `util-linux-extra` if `hwclock` is missing.
   - The cell voltage, if `vcgencmd pmic_read_adc` reports one.
   - Whether `fake-hwclock` is installed and enabled.
   - Whether the kernel's 11-minute RTC sync is built in (`CONFIG_RTC_SYSTOHC`).

   **If `charging_voltage` isn't 0, or an `rtc_bbat_vchg` line is present: STOP and tell
   The owner at once.** That's the one urgent outcome.
2. **Bootstrap charging guard.**
   - Remove `GATBOX_RTC_BATTERY`.
   - By default, comment out any active `rtc_bbat_vchg` line, with a dated note and a loud
     warning that a reboot is needed.
   - The only way to set it is `GATBOX_RTC_CHARGE=ML2020`, exactly that string, kept for a real
     ML2020 fitted later. Refuse any other value.
   - Update the usage text and BRINGUP.md.
3. **`gatbox-rtc-sync`:** a root oneshot plus a timer, about 2 min after boot and then hourly.
   - If `timedatectl show -p NTPSynchronized --value` is `yes`, run
     `hwclock --systohc --utc`, then record the write (epoch + ISO) in
     `/var/lib/gatbox/rtc-synced`.
   - Never write the RTC while unsynced. Log to the journal.
   - Keep this even if the kernel's 11-minute sync is on: the stamp is the proof that the RTC
     was set from real time.
4. **Logger clock label at session start.** First match wins:
   - `ntp`: NTPSynchronized=yes.
   - `rtc`: all of these hold:
     - `/dev/rtc0` is readable.
     - `rtc-synced` exists.
     - The RTC time (`since_epoch`) is ≥ the last stamp. A reset cell reads 1970 or 2000 and
       fails here.
     - |system clock − RTC| ≤ 5 s. This proves the system clock came from the RTC and not
       from fake-hwclock's saved guess. `hctosys`=1 is supporting evidence.

     Header line: `# clock=rtc (RTC-held time; last set from NTP <iso>)`.
   - Otherwise `unverified …` (keep today's wording).

   Also:
   - Skip the 90 s NTP wait when `rtc` qualifies.
   - If NTP syncs mid-session, append one line: `# clock-sync=ntp at <iso>`. The report's
     CLOCK STEPS section already catches any step.
   - This check runs once per session start. The per-sample loop stays fork-free.
5. **gatbox-status RTC line:**
   - RTC present or absent.
   - Charging **OFF** (green), or **CHARGING** (red, with the fix).
   - RTC−system delta.
   - Last NTP→RTC write.
   - Cell voltage, if it can be read.
6. **Report and gatbox-web:**
   - `clock=rtc` means a trustworthy wall clock, so no warning.
   - A clock badge on each session: NTP / RTC / UNVERIFIED.
   - Handle `# clock-sync=` lines.
7. **Tests.** Unit-test the label logic, with the sysfs and stamp paths overridable by env
   (tests only). Cover:
   - NTP-synced
   - RTC good
   - RTC reset (1970)
   - RTC never stamped
   - system ≠ RTC (the fake-hwclock case)

   Run the report and web on old `ntp`/`unverified` files and on a synthetic `clock=rtc` file.
8. **Install via the bootstrap.** Check `systemctl list-timers 'gatbox-rtc-sync*'` and that
   `hwclock -r` matches `date`.

⏸ **The owner:**
1. Shut down.
2. Pull power for 10+ minutes.
3. Boot with no internet (hotspot only).
4. Check `date` and `gatbox-status`, and start a session: the header must say `clock=rtc`.

If the time is wrong, the cell or holder isn't holding. Report the cell voltage and stop.

## M2: 2A Foundation (repo, minipro, MAME)

1. **Repo layout:** `bootstrap/ backend/ dashboard/ tools/ data/ docs/ docs/journal/`.
   - Move the existing sources in with `git mv`, without changing behaviour.
   - `data/` gets the roster (ref copy), `gatbox-machine-specs.json`, and `profiles.json`
     (M4). `docs/` gets the plan and this file.
2. **Bootstrap shape.** Keep the single-file build (build.py inlines sources into heredocs)
   *or* install straight from the checkout. Pick one and say why in BRINGUP.md. Hard
   requirements:
   - A fresh card + clone + `sudo bash bootstrap/gatbox-bootstrap.sh` gives a working Pi.
   - A re-run on this Pi changes nothing.
   - An offline re-run succeeds.
3. **Forgejo.** Ask the owner for the remote URL (her homelab); the Pi may only reach it on home
   Wi-Fi. Add the remote and push when it's reachable. Otherwise commit locally and push
   later. Never block on it.
4. **minipro module.**
   - First try `apt-cache policy minipro`. Use the Debian package only if it handles the T48
     (the T48 shows in `minipro -k`).
   - Otherwise build `https://gitlab.com/DavidGriffith/minipro` at a **pinned release tag**.
     Record the tag and bump it deliberately.
     - Deps: `build-essential pkg-config libusb-1.0-0-dev git`, plus `zlib1g-dev` if needed.
     - `make && make install`, the udev rules, then `udevadm control --reload-rules &&
       udevadm trigger`.
   - The owner's user goes in whatever group the rules use.
   - Starter §2 has the manual version.
5. **MAME CLI module,** for `mame -romident` only: `apt install mame` (trixie ships 0.276
   for arm64).
   - Smoke test on a small file, with a writable HOME/cfg dir.
   - Note the runtime and peak RAM.
   - No gameplay on the Pi.

⏸ **The owner, T48 re-verify:**
1. She puts a known EPROM in the ZIF and gives you the exact part.
2. You dump it twice with `minipro -p '<name exactly as minipro -L lists it>'`.
3. The SHA1s must match each other and her known-good hash.

Show the warning while you do it: **27C1000 / 27C301 are non-JEDEC (A5/A7). Pick the wrong part
and the read is silently garbage.**

## M3: 2B The face (Waveshare 7" (C) + kiosk at boot)

⏸ **First, the owner** connects the panel:
- HDMI → the Pi's HDMI0 (HDMI-A-1)
- touch USB straight into the Pi
- panel power from the Pi's USB

1. **Mode.** Check that KMS picked 1024×600 from EDID (`wlr-randr` or `kmsprint`).
   - Only if it didn't: append `video=HDMI-A-1:1024x600@60D` to the *single* line in
     `/boot/firmware/cmdline.txt`, via the bootstrap, idempotently, diff shown first.
   - config.txt `hdmi_*` settings do nothing on the Pi 5 (KMS).
2. **Touch lands where tapped** under labwc. If it doesn't (for example, mapped to the wrong
   output), map the touch device to HDMI-A-1 in labwc's `rc.xml`.
3. **Power.** Run `gatbox-status` under load: panel on, Chromium up, a minute of CPU stress.
   There should be no undervoltage or throttling. If there is, say so: the fix is the 27 W
   PSU (optional buy), not software.
4. **Kiosk at boot, desktop kept.**
   - Check desktop autologin; don't assume it.
   - `gatbox-kiosk on|off|status`:
     - `on` = boot-to-kiosk from `~/.config/labwc/autostart` of the owner's desktop user.
     - `off` = plain desktop from the next login, and it kills a running kiosk now.
   - The launcher waits until gatbox-web answers (`curl -fs http://127.0.0.1/…`) before
     starting Chromium. Chromium started too early from labwc autostart can come up white.
   - Chromium (check the binary name on this image):
     - `--kiosk --noerrdialogs --disable-infobars --no-first-run`
     - a dedicated `--user-data-dir`
     - `--password-store=basic` (no keyring prompt)
     - no swipe-back navigation on touch
     - **no "restore pages?" bubble after a power cut:** cabinet power gets yanked, so clear
       the crash state before launch
     - restart it if it dies
     - comment every flag
   - URL: gatbox-web's `/` until M5, then `/dash/`.
   - **EXIT KIOSK:** a long-press control on the dashboard's SYSTEM panel, offered only to
     requests from 127.0.0.1. It closes the kiosk for this boot and doesn't change on/off.
5. **Blanking.** Find out how this image blanks (raspi-config, swayidle or wlopm).
   - Default: kiosk on = never blank. The (C) has a backlight switch.
   - If you keep blanking, it must be inhibited while a rail session is live.
   - Say which you chose, and why, in BRINGUP.md.

⏸ **The owner:**
1. Reboot: the dashboard comes up on the 7", taps land, no keyboard needed.
2. `gatbox-kiosk off` + reboot: the desktop comes up.
3. `gatbox-kiosk on` + reboot: back to the kiosk.

The bench stand is CAD/print work, not yours.

## M4: 2C Backend (grow gatbox-web, one server)

**DECIDED 2026-09-26, overriding the plan's original "new FastAPI gatbox-api":** the backend is
the existing gatbox-web, extended.
- One server on :80, stdlib, with the same DynamicUser hardening.
- SSE is fine on ThreadingHTTPServer for the two or three clients it will ever see.
- Restructure it into a small package: a routes table, plus modules for sessions / report /
  meter / roster / system / devices / dump. It's about to triple in size.
- Move to FastAPI + uvicorn (both apt) only if you hit a concrete wall, and ask the owner first.

### 4a. Replay harness (build this first)
- `tools/gatbox-replay` turns a rail CSV back into sigrok `-O analog` lines
  (`P1: 4.95 V DC AUTO`), at 2/s or faster. It must reproduce mode switches, HOLD, OL
  (`inf` with a T prefix), the Ω sign (U+2126) and continuity's unit-less rows.
- Add a **test-only** env override so gatbox-raillog runs the replay instead of sigrok-cli.
  It writes into a temp log dir and run dir, and never touches the real service, the port or
  `/var/log/gatbox`.
- Every logger, report, web and dashboard test from here on uses it.

### 4b. Data model
- **`data/profiles.json`** is the single source for the logger, report, web and dashboard.
  - Per profile:
    - id, label
    - the expected dial mode (use the exact strings the logger writes to `/run/gatbox/mode`)
    - kind (`rail` | `bench`)
    - rail id for the spec lookup (`+5V`, `+12V`, `-5V`: ASCII minus in data, "−" only on
      screen)
    - window lo/hi or none
    - `alarm_hi`
    - notes (jack reminder etc.)
  - The profiles come from the plan's table:
    - +5V 4.75–5.25
    - +5V Neo Geo 5.00–5.20 (never over 5.2)
    - +12V 11.4–12.6
    - −5V −5.25…−4.75
    - ripple (V~ on a DC rail, ceiling set by the user)
    - Ω/continuity
    - diode
    - capacitance
    - current (A/mA/µA, with the jack reminder)
    - Hz/%
    - Free
  - Rail `alarm_hi` defaults to hi × 1.10: +5V → 5.78 V, GL spec → 5.61 V. For −5V, compare
    magnitudes.
  - With no profile set (fresh install, or the state file missing), use the +5V rail
    profile. That's today's behaviour, and overnight logs must keep working without anyone
    touching the dashboard.
- **`data/gatbox-machine-specs.json`** (DECIDED: separate from the roster so the maintenance
  app's import never sees a new field). A machine's spec for a rail overrides the profile
  window. Manual-sourced numbers only.
- **Writable state** lives in gatbox-web's existing state dir (`/var/lib/gatbox-web`, the same
  pattern as `stopped`/`start-request`): profile, current machine, marks spool, captures,
  user window values.
  - gatbox-web is the **only writer**; the logger (root) reads.
  - Root-only system state (the RTC stamp) stays in `/var/lib/gatbox`.
- **Logger header at session start,** after `# clock=`:
  - `# mode=` (existing)
  - `# profile=<id>`
  - `# window=<lo>..<hi> source=profile|machine:<slug>|user`
  - `# alarm_hi=`
  - `# machine=<slug>` (when one is set)
- **One file = one machine + one profile + one dial mode.** If the profile changes, or a
  *different* machine is set, while a session is live, the logger ends it and starts a new
  file (like NEW). Re-scanning the same machine does nothing.
- **Marks.**
  - The API appends to the spool, recording `epoch, uptime_s, source, label` at request time.
  - At its next sample, the logger moves each mark into the live CSV as
    `# mark=<iso>,<epoch>,<uptime_s>,<source>,<label>`. Use builtins only: the sample loop
    stays fork-free.
  - A mark with no live session goes into the next file as `# mark-before-start=…` and still
    shows on the dashboard.
- **NEW** = today's `start-request` flag.

### 4c. gatbox-rail-report
- **Window:** from the header's `# window=` unless `--lo/--hi` is given. Units and labels
  come from the profile.
- **Powered vs off** (rail profiles): |v| < 0.5 V counts as off. Stats, excursions and
  time-in-window use powered stretches only. List the power cycles (off at, on at, duration).
- **OVER-VOLTAGE section:** samples above `alarm_hi` (magnitude on negative rails), with
  times, peak and duration.
- **MARKS section.** On the plot: marks as labelled vertical lines, OV stretches in red, off
  stretches shaded.
- **Header:** machine, profile and clock source. Accept `clock=rtc`.
- **Old files:** no new header lines → today's defaults. Output is the same as now apart from
  the new sections.
- **The phone view** (`/`, `/s/…`, PDF) uses the header window too.
- **Regression fixture:** `rail_20260925_021402.csv`. Expect:
  - a powered mean around 4.93–4.98 V
  - an OV event from 03:22:48 peaking ~20.28 V, two bursts, ~25 s in all
  - ~62 s off

  Also run the gameplay slice (02:16 → 03:22) at the GL spec, `--lo 4.90 --hi 5.10`, to see
  how close to the floor it ran. **Put the numbers you get in the journal entry:** they're
  live evidence for the GL crash hunt.

### 4d. JSON API
Everything lives under `/api/`, speaks JSON, and returns errors as `{"error": …}`.

| Method + path | Does |
|---|---|
| `GET /api/system` | temp, throttling decoded (now / since boot), EXT5V, disk (`/`, `/var/log/gatbox`, `/srv/gatbox`), IPs/SSID/hotspot, clock source + RTC health (M1), kiosk state, versions |
| `GET /api/meter` | live mode, value (base units + as displayed), flags (HOLD/REL/MAX/MIN), last-sample age, session file, profile, window + source, machine, `dial_ok`, alarm state |
| `GET /api/rail/live` | **SSE**: one event per sample plus a heartbeat every 15 s. Tails `/run/gatbox/current` and follows session rollovers |
| `GET /api/rail/sessions` | start, duration, samples, mode, profile, machine, window, clock, powered min/max/mean, OV count, marks count |
| `GET /api/rail/report/{file}?from&to&lo&hi` | report text + parsed sections + PNG URL, using the existing cache |
| `GET/PUT /api/meter/profile` | select a profile, plus user values (ripple ceiling, current window) |
| `GET/PUT/DELETE /api/machine` | current machine slug, validated against the roster |
| `POST /api/mark` | `{label?, source}` |
| `POST /api/session/new` | same as Start |
| `GET/POST /api/captures[?machine=]` | capture points, CSV per machine: iso, epoch, value, unit, mode, profile, label |
| `GET /api/devices` | DMM chain (adapter / readings flowing / mode), T48, scanner (present + grabbed), touch panel. USB IDs confirmed on this Pi, not guessed |
| `GET /api/roster`, `/api/roster/{slug}` | the entry merged with its platform, any `critical_actions` that name it, and its machine spec |

- **Validate everything:**
  - slugs must be in the roster
  - labels ≤ 40 printable chars
  - profile ids must come from profiles.json
  - numbers must be finite
  - never build a path from request input without whitelisting
- `vcgencmd` needs `/dev/vcio` (group `video`). Add only the SupplementaryGroups the unit
  really needs.
- **Tests:**
  - curl every endpoint.
  - SSE shows readings as the replay harness produces them.
  - Every old URL still works; test each one.

## M5: 2D Dashboard v1 at `/dash/`

**Targets.** The 7" at 1024×600 in kiosk, and usable on a phone.
- The main view doesn't scroll.
- Tap targets are ≥ 56 px: greasy fingers, and capacitive won't read gloves.
- The hero reading is readable at arm's length.
- **No simulated data anywhere.**

**Header:** `GATBOX//`, GDD-GAT/01, the clock with its source badge (NTP / RTC / UNVERIFIED),
network, and the logging heartbeat.

**METER** (the default view):
- A huge live reading in whatever mode the dial is on, plus a mode chip.
- A canvas sparkline, session age and the window band.
- The window source: profile, machine spec or user.
- The **profile picker:** big tiles from `profiles.json`.
- **Banners:**
  - **SET DIAL TO <mode>** (full-width amber) when the live mode ≠ the profile's mode. It
    clears itself.
  - **LEADS REVERSED?** when a rail profile reads the opposite sign.
  - **HOLD / REL / MAX-MIN** warning: a HOLD left on freezes an overnight log.
  - **Current profiles:** a jack reminder while one is selected, and **MOVE THE RED LEAD BACK
    TO VΩ** when you switch away. A lead left in the A jack shorts the next rail through the
    meter.
- **OVER-VOLTAGE:** a full-screen red alarm when a sample goes over `alarm_hi`, showing the
  value and time. It stays latched until someone taps ACK. JS memory is fine for the ACK, so
  a reload may show the alarm again.
- **Bench profiles:** a CAPTURE button. The label is typed on an **in-page big-key keypad**;
  don't rely on the OS on-screen keyboard in kiosk. The point is saved to the current machine.

**SESSIONS:**
- The list: machine, profile, mode, duration, clock badge, OV and mark counts. Sessions
  with OV events get a red tag.
- Tap one for the report (PNG + parsed sections) and the existing PDF link.

**MACHINE:**
- The current machine: roster name, platform description, risk chips, faults/parts/pm, the
  critical actions that name it, rail specs, captures, dumps.
- CLEAR.

**SYSTEM:** temp, throttling/undervoltage, EXT5V, disk, network/hotspot, clock + RTC health,
and EXIT KIOSK (local only, long-press).

**DEVICES:** DMM chain, T48, scanner, touch.

**DUMP:** comes in M7.

**NOT FITTED** (greyed, labelled, no fake data): M2K SCOPE, HUB ARM (MEGA4), BOARD POWER.

**Wiring up:** switch the kiosk URL to `/dash/`, and link `/dash/` and the phone view both ways.

**Tests.** Drive the dashboard with the replay harness through each of these states:
- a normal rail
- a dial change
- HOLD
- OL
- the fixture's 20 V burst
- power off and on
- marks
- a profile change
- setting and clearing the machine

Screenshot each state at 1024×600 with headless Chromium and **look at the screenshots**.
Then ⏸ the owner tries it on the real panel.

## M6: 2E Scanner → roster

**`gatbox-scand`** (python3-evdev, systemd):
- Finds the EY-H2 by the USB IDs you read with `lsusb`.
- **Grabs it exclusively (EVIOCGRAB),** so scans never type into a window.
- Decodes keycodes to text (US layout, shift).
- A code ends at Enter; tolerate CR, LF and Tab suffixes.
- Re-attaches on hot-plug.
- Minimal privileges (the `input` group). Talks to gatbox-web over loopback, so gatbox-web
  stays the single writer.

**Codes:**
- a roster slug → `PUT /api/machine`
- `GATBOX:MARK` → mark (source=scan)
- `GATBOX:NEW` → new session
- anything else → logged, and shown on the dashboard as "unknown code"

**Optional GPIO MARK button,** only if the owner has a switch:
- A momentary between a free GPIO and GND: gpiozero `Button`, internal pull-up, debounce.
- Propose the pin (per the rules in Hard rules) and name the physical pins.
- Off unless configured.

**`tools/gatbox-labels`:** a printable sheet, built with python3-qrcode or qrencode (apt).
- One QR per roster slug (video_games + pinball, not retired), with the machine name under it.
- A command card with `GATBOX:MARK` and `GATBOX:NEW`.
- Sized to scan from about 20 cm with the EY-H2.

**Exit test:** scan a cabinet → run overnight → scan MARK at a crash → the morning report shows
the mark on the trace.

⏸ **The owner** scans a printed slug, then MARK, then NEW. Check that each one lands, on the
dashboard and in the CSV.

## M7: 2F T48 dump → identify → archive

**`gatbox-dump` CLI first:**
- `-p PART`: the exact minipro name. `--search` wraps `minipro -L` so she can find it.
- `-l LABEL`: the chip label, e.g. `LG-U12`.
- `-m SLUG`: defaults to the current machine, else `unassigned`.
- **Never auto-detect the part.** If she picks 27C1000 or 27C301, print the non-JEDEC A5/A7
  warning and require confirmation.

**The read:**
- Run minipro's pin-contact check first, if it supports it for the T48.
- **Read twice and compare.** A mismatch means "reseat the chip": stop.
- Flag all-FF (blank or no contact) and all-00.
- Never pass minipro's ignore-ID option silently. Show any ID complaint and require an
  explicit flag to continue.

**Identify:** SHA1 + CRC32 → `mame -romident` → **MATCH** `<set>/<rom>` (list every match) or
**NO MATCH**.

**Archive:**
- `/srv/gatbox/roms/<slug>/<label>_<sha1:8>.bin`, plus a `.json` sidecar: part, size, sha1,
  crc32, romident result, reads compared, date, clock source, machine, minipro version.
- Never overwrite. Same hash = already archived; say so.

**Dashboard DUMP flow:** machine (scanned or picked) → part → label (keypad) → DUMP → result
card.
- Parts: a curated list of common arcade EPROMs, with names taken from `minipro -L` (don't
  type names from memory), plus search.
- It runs as a job, one at a time, with progress.
- gatbox-web never touches the programmer. The job runs under a narrow identity with T48
  access and write access to `/srv/gatbox/roms` only. A spool file plus a systemd path/service
  unit would mirror the logger's flag pattern. Explain your choice.

**No burn anywhere in gatbox-web.** A CLI burn helper is optional and low priority: blank
check → type the part name to confirm → write → verify.

⏸ **The owner** dumps a known EPROM from a real board. The dashboard should name the ROM. A
NO MATCH is Phase 3's "probe the delta".

## M8: 2G Stretch (ask before starting)

- **Sync:** a systemd timer rsyncs `/var/log/gatbox`, `/srv/gatbox`, and the capture/state
  files to a homelab target over SSH, whenever that host is reachable. Ask the owner for the
  target. Key-based, with a dedicated key.
- **War-room panel:** a read-only Proxmox API token (PVEAuditor) → nodes / CTs / ZFS on the
  dashboard.
  - The token lives in a root-only file and reaches gatbox-web through systemd
    `LoadCredential=`. Never in the repo.
  - Pin the Proxmox cert rather than turning off TLS verification.

---

## Done = Phase 2 exit (report against each)

1. **A fresh card rebuilds from the repo with one command, T48 included.** If the owner has a
   spare card, test it for real. If not, prove idempotence on this Pi and dry-run an install
   into a scratch root.
2. **Boots to the dashboard** on the 7", with touch working.
3. **Dashboard coverage:** live meter in any mode, sessions, reports and system health. Picking
   a profile checks the dial and sets the report window.
4. **Scanning:** scanned cabinets tag sessions, and MARK scans show in reports.
5. **Dumps:** a dumped EPROM is identified against MAME and archived under its machine.
6. **RTC:** offline sessions say `clock=rtc`, and RTC charging reads OFF.

**Then:**
- a final journal entry
- BRINGUP.md up to date
- everything pushed to Forgejo
- a list of files for the owner to upload to the claude.ai project: bootstrap, CLAUDE.md,
  plan/progress, journal entries, and data files if they changed
