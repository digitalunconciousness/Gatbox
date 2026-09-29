# GATBOX (GDD-GAT/01) bring-up log

Pi 5 Model B Rev 1.1, 4 GB · Raspberry Pi OS Trixie 64-bit Desktop · hostname `gatbox` · user `<user>`

"Bring-up 1/2" below are steps on this Pi, not the project's phases. In the build journal, Phase 1 is the rail
flight recorder (done) and Phase 2 is the software on owned hardware (minipro, display, gpiozero, dashboard;
not started). These headings were called "Phase 1/2" until 2026-09-24.

## Phase 2 — progress (spec: `docs/phase2-handoff.md`)

Resume from the first unchecked box. ⏸ = waiting on the owner (hands on hardware).

- [x] **M0 orient** (2026-09-28): git repo + baseline `83ba066`; installed files == bootstrap payload (nothing to
      reconcile); CLAUDE.md merged with the project CLAUDE.md + the handoff's hard rules.
- [x] **M1 RTC** (2026-09-28): read-only checks, bootstrap guard, gatbox-rtc-sync timer, logger `clock=rtc`, status
      line, web badge, tests (36 pass), installed 09:16, ⏸ offline warehouse test **passed** (notes below)
- [x] **M2 2A foundation** (2026-09-28): repo layout, bootstrap installs from the checkout, logger SD sync,
      minipro 0.7.4 + mame 0.276 installed, GitHub (public, scrubbed), MAME smoke test, ⏸ T48 re-verify **passed**
- [x] **M3 2B face** (2026-09-28): either HDMI port (EDID + unmapped touch), autotouch off, boot-to-kiosk +
      gatbox-kiosk + EXIT KIOSK, never blanks in kiosk, power OK under load, ⏸ reboot tests **passed**
- [x] **M4 2C backend** (2026-09-28): [x] 4a replay harness [x] 4b data model + logger (mode/profile/window/alarm/machine
      header, one file per dial mode + settling, marks spool, /run/gatbox/mode) [x] 4c report (header window/limit, power cycles, suspect
      glitches, marks, --json) [x] 4d JSON API + SSE (gatbox-web → package + routes table; 80 API tests)
      4a–c + kiosk SHUT DOWN installed 2026-09-28 (reboot 17:44, `--check` clean). 4d installed 19:32 (owner OK'd
      the unit: `video` + a private /dev with only /dev/vcio_gencmd); vcgencmd works inside it
- [x] **M5 2D dashboard** (2026-09-28) at `/dash/`: code (web/dash: METER/SESSIONS/MACHINE/SYSTEM/DEVICES/DUMP, live
      chart with tap/zoom, alarm takeover, keypad), screenshot tests (tests/test-dash.sh, looked at), installed 20:22
      and 20:40 (reboot), ⏸ on the real panel **passed** (owner: "everything looks rad"; she picked a profile and
      started logging from the 7" at 20:49)
- [x] **M6 2E scanner** (2026-09-28): EY-H2 USB ID read (af99:8002); gatbox-scand + POST /api/scan + dashboard
      (toasts, DEVICES, LABEL LIST) + gatbox-labels (tests/test-scan.sh); installed 21:17 (owner OK'd the unit +
      python3-qrcode); ⏸ Katasymbol labels **passed**: slug `DDR` (same machine, already picked), GATBOX:NEW → new
      file still `machine=ddr`, GATBOX:MARK → `# mark=…,scan,` in the live file and on the report plot. GPIO MARK
      button deferred (no enclosure yet). The overnight exit test (scan a cabinet, MARK at a crash) is real-world use.
- [ ] **M7 2F T48 dump**: [x] T48 firmware 01.1.32 (owner flashed it, self-test passed) [x] gatbox-dump CLI (real
      chip: MATCH sonic/epr-15781c.ic18) [x] dashboard DUMP flow + gatbox-dump.path/.service + archive + part list
      (tests/test-dump.sh, test-dash) [x] install (owner OK'd the user, dirs, units, group) [x] ⏸ dump a known
      EPROM from the dashboard **passed** 2026-09-29 01:50 (owner: "It works"; SegaSonic EPR-15781C, MATCH
      sonic/epr-15781c.ic18 in the archive's sidecar) [ ] burn (owner wants it: CLI per the spec; dashboard burn is
      her call on hard rule 6)
- [ ] **M8 2G stretch**: ask first
- [ ] **Owner requests (2026-09-29)**, off-spec, one at a time with her OK on each design:
      [x] + ADD MACHINE / EDIT / EXPORT ROSTER, and CAPTURE → SAVE READING (code + tests/test-roster.sh, test-dash)
      [x] installed (d578961) [x] each machine's ROM checklist from MAME's own hash data, no ROM sets downloaded
      (code + tests/test-mame.sh, test-dash) [x] manuals: MANUALS tab (viewer, search, phone upload), spec sheet
      (the manual's limits with page + words, CONFIRM, actual values from the field) (code + tests/test-manuals.sh,
      test-dash) [x] install both (owner OK'd the group, the folder, the unit) [x] the manuals list for the floor +
      fetch (396 documents for 88 of 95 machines, 389 on the Pi, 1.2 GB) [x] spec sheets read off the manuals (18
      rail limits on 10 machines, 52 facts on 42) [ ] the list installed [ ] project update + journal

### M0 notes (2026-09-28)
- **Spec vs reality:** the handoff and the project CLAUDE.md say the logger already writes `# mode=`, a
  `/run/gatbox/mode` file, and one file per dial mode. It doesn't: the installed logger is the 09-23 one + web
  Start/Stop (the 09-24 dial-through landed in one file). Those behaviours are built in M4b instead.
- **RTC (M1 step 1, read-only):** `rpi-rtc` rtc0, hctosys=1, `charging_voltage` 0 (min 1.3 V, max 4.4 V), no
  `rtc_bbat` line, BATT_V 3.126 V. At the 09-27 boot the RTC read 1970-01-01T00:00:13 (cell just fitted), so the
  system clock started at timesyncd's saved 09-25 16:33 until NTP at 19:31. The kernel's 11-min sync
  (`CONFIG_RTC_SYSTOHC=y`) set the RTC after that: RTC − system = 0 s on 09-28. No fake-hwclock on this image
  (timesyncd's `/var/lib/systemd/timesync/clock` plays that role); `hwclock` missing (util-linux-extra).
- `/dev/rtc0` is root-only (0600); sysfs `since_epoch` is world-readable.
- Two 09-25 files end in a run of NUL bytes (power pulled while logging); report and web both skip it.
- A 137 MB `Warp-ARM64.AppImage` sits in `~/gatbox`: not source, git-ignored, left alone.

### M1 notes (2026-09-28)
- **Charging guard (bootstrap):** `GATBOX_RTC_BATTERY` is gone and is refused if set. `GATBOX_RTC_CHARGE` accepts only
  `ML2020`, exactly, and is checked before the root check. Otherwise any active `rtc_bbat_vchg` in config.txt is
  commented out with a dated note (a combined `dtparam=` line keeps its other params). The backup goes to
  `config.txt.pre-rtc-guard`, and the bootstrap then asks for a reboot. On this Pi it's a no-op (no such line).
- **gatbox-rtc-sync** (`/usr/local/sbin`, root oneshot + timer: 2 min after boot, then hourly): only when
  `NTPSynchronized=yes`, runs `hwclock --systohc --utc` and writes `/var/lib/gatbox/rtc-synced` = `<epoch> <iso>`.
  `hwclock` comes from `util-linux-extra` (added to the packages) and keeps `/etc/adjtime` (UTC).
- **Logger clock label** (once per session start): `ntp` if NTP-synced; else `rtc (RTC-held time; last set from NTP
  <iso>)` if the stamp exists, RTC ≥ stamp, and |system − RTC| ≤ 5 s; else `unverified (…)` (wording unchanged).
  A trusted RTC skips the 90 s NTP wait. When timesyncd's `/run/systemd/timesync/synchronized` appears mid-session,
  one `# clock-sync=ntp at <iso>` line is added. That's a builtin file test, so the sample loop still doesn't fork.
  `gatbox-raillog --clock-label` prints what a session started now would get.
- **gatbox-status:** a `clock` line (what new sessions will say) and an `rtc` line (charging OFF/CHARGING,
  RTC−system, last NTP→RTC write, cell voltage). It only calls a logger that supports `--clock-label`.
- **gatbox-web:** NTP / RTC / UNVERIFIED badge per session (+ "NTP from hh:mm:ss" after a mid-session sync) on the
  list and the session page, and in the PDF header. The report needed no change: `# clock=` and `# clock-sync=`
  lines show up as `note:` lines.
- **bootstrap:** an offline `apt-get update` failure is now a warning, not a stop (rule 4).
- **Tests** (`tests/`, no root, never touch the real service/port/logs): `test-clock-label.sh` (13),
  `test-raillog-clock.sh` (10, fake sigrok-cli end-to-end), `test-rtc-guard.sh` (13).
- **Physical test (⏸):** shut down, pull power ≥10 min, boot with no internet, `date` + `gatbox-status`, start a
  session: its line 2 must be `# clock=rtc (…)`.
- **Result (2026-09-28):** unplugged 09:26 → warehouse boot 11:00 with no network: kernel set the clock from the
  RTC (11:00:34), logger said "clock from the RTC … not waiting for NTP", and `rail_20260928_110134.csv` line 2 is
  `# clock=rtc (RTC-held time; last set from NTP 2026-09-28T09:16:43)`. gatbox-rtc-sync at 11:02 correctly left the
  RTC alone (not NTP-synced). Unplugged again 12:03 → home boot 14:06: NTP corrected the clock by **+0.65 s** (RTC
  has 1 s resolution). Cell 3.12 V afterwards, charging_voltage 0.
- The warehouse file lost only its last sample to a NUL tail (Stop at 12:02:56, power pulled ~14 s later). The
  09-25 files lost ~7–15 s the same way. Possible fix: `sync` the file at session end (one fork per session).

### M2 notes (2026-09-28)
- **Layout:** `backend/` (raillog, web, rtc-sync, ap-fallback + units), `tools/` (status, rail-report), `bootstrap/`
  (installer + `files/`), `data/` (roster, machine specs: ref copies), `docs/` (spec, plan, `ref/` mockup + project
  CLAUDE.md, `journal/`, `history/` = the original Phase 1 script, its payload, the 09-23 log, the 09-24 diffs),
  `tests/`. Moved with `git mv`, so each file keeps its history.
- **Bootstrap shape: install straight from the checkout** (not the single-file heredoc build). Why:
  - one copy of each file: no generated file, and no hand-splicing into heredocs (which went wrong once on 09-24);
  - `git log` of a script is that script's real history;
  - "re-run changes nothing" is simple: `put_file` writes only when content or mode differs, and services restart
    only when their own files changed (a logger restart ends the live session);
  - offline re-runs are trivially safe: apt is skipped when every package is present, minipro when the pinned
    version is installed, fonts when their sha256 matches;
  - the spec's install line is `git clone` + `sudo bash bootstrap/gatbox-bootstrap.sh` anyway.
  The cost: the bootstrap is no longer one self-contained file for the claude.ai project. The project gets the
  GitHub repo, or the handful of files, instead. `--extract DIR` still writes everything under a scratch root for
  review, and `--check` (no root) lists what a run would change.
- **Re-run hygiene:** groups are added only if missing, I2C/SPI are toggled only if off, journald restarts only
  when its drop-in changed, `daemon-reload` runs only when a unit changed, udev reloads only when a rule changed.
- **Logger SD fix:** `sync -d` on the session file every 20 samples (~10 s) and at session end. Bounds a power-cut
  loss to ~10 s (09-25 lost 7–15 s; a Stop then a quick pull loses nothing). One fork per 20 samples.
- **minipro:** not packaged in Trixie, so the bootstrap builds the pinned upstream tag **0.7.4** (2025-08-02, commit
  3808aec) into `/usr/local` from `/usr/local/src/minipro`. It was test-built here (scratch, local libusb headers):
  it compiles clean and lists T48 among its supported programmers. Its `make install` skips the udev rules on Pi OS (no
  `udev.pc`), so `bootstrap/files/minipro-0.7.4/` carries upstream's three rules byte for byte: T48 = `a466:0a53`,
  group `plugdev` (the owner is in it) + `uaccess`. Bump the tag and the rules together.
- **MAME:** `mame` 0.276 from apt (+ mame-data, libportaudio2, libutf8proc3; ~515 MB), for `mame -romident` only.
- **GitHub (public):** `digitalunconciousness/Gatbox`, pushed over SSH with a repo-only deploy key
  (`~/.ssh/gatbox_github`, `Host github-gatbox` in `~/.ssh/config`; GitHub's ed25519 host key checked against its
  published fingerprint). Before the first push the whole history was rewritten: noreply author/committer, UTC
  timestamps, names/username/home network/timezone replaced by placeholders, the workplace roster dropped (now
  git-ignored in `data/`). Real values: `SITE.local.md` (git-ignored). A local `.git/hooks/pre-push` refuses
  personal details, the roster, `*.local.md` and non-UTC timestamps. Commit with `TZ=UTC git commit`.
- **Installed 2026-09-28 16:17** by the owner. It wrote the new logger + 3 minipro udev rules, installed
  libusb-1.0-0-dev + mame (+ mame-data, libportaudio2, libutf8proc3), and built minipro 0.7.4
  (`/usr/local/bin/minipro`, `/usr/local/share/minipro/{infoic,logicic}.xml`). Only gatbox-raillog restarted; the
  web view was untouched. Disk +0.6 GB. Afterwards `--check` said "nothing to change". Offline re-runs need no
  network: apt, minipro and fonts are all skipped when present.
- **MAME smoke test:** `mame -romident` with HOME in scratch writes no files. It takes ~8 s and peaks at **485 MB RSS**
  (don't run it in parallel with other heavy jobs). Exit 0 = match, 9 = no match. **All-FF data "matches"**:
  an 8 KB all-FF file matched a run of ColecoVision sets' blank ROM halves. M7 must flag all-FF (and all-00) *before*
  reporting a MATCH.
- **minipro part names** include the package: `minipro -q t48 -L 27C1024` → `AM27C1024@DIP40`, `AT27C1024@DIP40`, …
  (works without the T48 plugged in).
- **⏸ T48 re-verify (2026-09-28): PASSED.** Chip: SegaSonic The Hedgehog EPR-15781C, marked 27C020-15. T48 direct on a
  Pi port (not the hub), 5.0 V supply, owner has plugdev access. Generic `27C020@DIP32` refused: **Invalid Chip ID:
  expected 0x8934, got 0x9732 (TMS27C020@DIP32)**. 0x97 = Texas Instruments. Read as `TMS27C020@DIP32` (ID OK, never
  `-y`), twice, 1.3 s each: both reads identical, 262144 bytes, SHA1 `9f524012a7adbc71737f90fc556f0ce9adc2bcf8`
  CRC32 `65b06c25` = MAME 0.276 `sonic` `epr-15781c.ic18`, and `mame -romident` names it. Copy kept outside the
  repo (`~/t48-dumps/`); `*.bin` is git-ignored and the pre-push hook refuses `.bin` files.
  - For M7: minipro's chip-ID check caught a real mismatch and named the right part, so surface that message and
    re-select explicitly (never `-y`). `-z` pin check → "Pin test is not supported." for this part: handle it. Read
    progress uses `\r`/`ESC[K`: strip it from logs.
### M3 notes (2026-09-28)
- **Either HDMI port, by design:** nothing names an output. EDID gives 1024×600 @ 59.85 on HDMI-A-1 and HDMI-A-2
  (the panel identifies as "Addi-Data GmbH 0x0004"), so no `video=` in cmdline.txt. Touch is left **unmapped**, which
  with one display covers the panel on either port and any USB port. Chromium in kiosk goes full screen on the only
  output. With a second display (the future GBS path), touch and the kiosk would need pinning to the panel's output:
  find it by EDID make, not by port.
- **autotouch (Pi OS):** runs at every login from `/etc/xdg/autostart`. The first time it sees one touchscreen + one
  display with no mapping, it writes `<touch deviceName="WaveShare WS170120 (USB 1-1)" mapToOutput="HDMI-A-1"
  mouseEmulation="yes"/>` into `~/.config/labwc/rc.xml` (done 09-28 11:00, `rc.bak` = before), then never updates it.
  libinput names carry the USB port, so after replugging (now `(USB 3-1)`) it no longer even matched. It also turns
  on mouse emulation, and the dashboard needs real touch for multi-finger gestures. The bootstrap now installs a
  `Hidden=true` override (`~/.config/autostart/autotouch.desktop`) and removes that line (backup
  `rc.xml.pre-gatbox-touch`).
- **Kiosk:** `gatbox-kiosk-launch` starts from XDG autostart (`~/.config/autostart/gatbox-kiosk.desktop`). That's a
  deliberate deviation from the spec's `~/.config/labwc/autostart`: XDG autostart is additive, so the desktop's own
  startup (panel, desktop icons) always runs. It waits for gatbox-web, clears Chromium's crash state (no "Restore
  pages?" after a power cut), runs Chromium 153 with commented flags in its own profile
  (`~/.local/share/gatbox-kiosk`), restarts it if it dies, and one launcher at a time (flock). `gatbox-kiosk
  on|off|start|status` (state in `~/.config/gatbox/kiosk`, missing = on). URL: gatbox-web's `/` until M5 → `/dash/`.
- **EXIT KIOSK:** `POST /kiosk/exit` works from 127.0.0.1 only (403 otherwise). `GET /kiosk/state` gives `exit_at`,
  and the launcher polls it and acts when the value *changes* (not on a time comparison: NTP can step the clock
  after boot). A 1.5 s long-press "Hold to exit kiosk" sits at the top of `/`, shown only to the Pi's own screen.
  M5 moves it to the SYSTEM panel.
- **Blanking: never while the kiosk runs.** Today nothing blanks (no swayidle; raspi-config get_blanking = 1).
  raspi-config's blanking is a swayidle line in `~/.config/labwc/autostart`, so the launcher stops the user's
  swayidle for its login only, and the plain desktop keeps whatever is set. The (C) has a backlight switch.
- **Installed 2026-09-28 ~16:50**; `--check` afterwards: nothing to change.
- **⏸ Reboot tests (owner, 16:53–17:00): PASSED.** Reboot → kiosk on the 7"; long-press EXIT KIOSK closed it
  (logged 16:54:54 from 127.0.0.1); `gatbox-kiosk off` + reboot → plain desktop; `gatbox-kiosk on` + start → kiosk;
  reboot → kiosk. autotouch stayed off across three reboots (no touch line came back).
- **Ports:** EDID 1024×600 was seen on HDMI-A-2 (morning, with the unmapped-touch tap test: 6–19 px) and on HDMI-A-1
  (kiosk now). A kiosk boot on the other port wasn't tried, but nothing in it names a port.
- **Power under load:** kiosk Chromium up, panel powered from the Pi's USB, 60 s of 4-core busy loops: EXT5V min
  5.11 V (5.16 idle), `get_throttled` 0x0 before and after, peak 61.5 °C, arm at 2.4 GHz. No need for the 27 W PSU.
- The launcher's messages now also go to the journal (`journalctl -t gatbox-kiosk`).
- **SHUT DOWN (added 2026-09-28, owner's request):** "Hold 3 s to shut down" next to EXIT KIOSK, on the Pi's own
  screen only (`POST /kiosk/shutdown` = 403 from the network). The kiosk launcher sees `shutdown_at` change and runs
  `systemctl poweroff` from the desktop session: polkit's `org.freedesktop.login1.power-off` is `implicit active:
  yes` for the active seat0 session, so no new permissions or root service. If poweroff fails, the kiosk comes
  back. The page says to unplug only when the screen is dark and the LED is red.
- **Found in the warehouse log:** the first sample after a dial change can be junk (11:16:27: `726.4 V AC` right
  after mV DC, then 2.77 V, then 7.4 V). M4b's per-mode files should drop or flag the first sample after a change.
### M4 notes
- **The 09-25 fixture vs the spec:** the spec expected ~25 s at ~20.28 V in two bursts and a powered mean of 4.93–4.98 V.
  The file has three single-sample readings over 5.775 V (18.667 / 20.277 / 20.284 V), each at a rail power-on edge
  between mV readings, and consistent with UT61E autorange glitches (see the 09-28 journal). Gameplay 02:16–03:22:47
  is 5.020–5.024 V (mean 5.022). Off: 62 s at the start, 5 s at 02:15:09, and the 03:22:48–03:23:17 switch
  bounce.
- **DECIDED (owner, 2026-09-28):** a lone over-voltage reading next to a range change is "suspect" (listed,
  left out of stats/excursions); the live alarm needs 2+ consecutive readings (a lone one = amber SPIKE), and
  there's an **ALARM ON/OFF** switch (server-side state, default on for rail profiles) for probing around.
- **Report (M4c):** a power cycle runs from the last in-window reading before the rail drops under 0.5 V to the
  first one back in the window (≤3 s of slope each side; bounces merge). The fixture reads: 4 power cycles
  (1m32s), powered 5.009–5.024 V mean 5.020 (100% in the window, also at the GL spec 4.90–5.10), 3 suspect
  over-voltage readings, no excursions. The old report called the two switch-bounce power-downs "excursions of
  11 and 39 samples, worst +20.28 V", which is where the journal's "25 s at 20 V" came from.
- **4d JSON API (2026-09-28):** gatbox-web is now `backend/gatboxweb/` (routes table in `server.py`) behind the same
  `/usr/local/bin/gatbox-web`; every old URL answers as before, except that the phone view and PDF now use each file's
  header window unless `lo`/`hi` are given. Endpoints per the spec, plus `PUT /api/meter/alarm {"on"}` (the ALARM
  ON/OFF switch). Writes need `Content-Type: application/json` (no cross-site form posts) and bodies ≤ 4 KB.
  - **Live:** one thread follows `/run/gatbox/current` across rollovers. It keeps the file's last hour of samples
    (for the chart) and feeds every SSE client from one event log: hello, backlog, sample, session, alarm, mark,
    capture, state, heartbeat; `Last-Event-ID` resumes.
  - **The alarm rule** as decided: over `alarm_hi` (magnitude, only in the profile's own dial modes, never OL) = SPIKE;
    2+ in a row = ALARM. Events are always tracked; ALARM OFF only stops the takeover. Picking another profile turns
    the alarm back on, so an overnight log never inherits a silenced alarm from probing.
  - **New files:** a profile change, or a different machine, while a session is live writes `start-request` (the
    logger starts a new file); re-setting the same machine does nothing; nothing is written while stopped.
  - **Captures:** the live reading only (≤ 3 s old), to `captures/<slug>.csv` (or `unassigned.csv`).
  - **Roster:** critical actions name games by display name, so matching is by name (the longest wins: "Gauntlet"
    doesn't also land on Gauntlet Legends).
  - **Session list:** from `gatbox-rail-report --json --no-plot` (0.15 s a file, cached; the live one reused for 15 s).
- **Spec vs reality (4d):** `vcgencmd` on this Pi 5 opens `/dev/vcio_gencmd` (root:video 0660), not `/dev/vcio`
  (root-only). So the unit adds `SupplementaryGroups=video`, `PrivateDevices=yes`, and binds in only
  `/dev/vcio_gencmd` (tried under `systemd-run --user`: vcgencmd works, and /dev has no ttyUSB, gatbox-dmm or vcio).
  So hard rule 2 is also enforced by the unit: the web service can't see the meter's port.
- **Logger fix found by the API tests:** a mark made < 1 s before a file started was written as `# mark=`, because
  the logger compared whole seconds. It now compares milliseconds (builtins, per mark only; the sample loop is unchanged).
- **Scanner:** no USB ID yet (the EY-H2 hasn't been plugged into this Pi); `/api/devices` says so until M6.
- **After the 4d install (19:32):** `vcgencmd` works in the sandbox (throttle 0x0, EXT5V 5.14 V, cell 3.13 V), but
  `timedatectl` and `nmcli` don't: D-Bus is unreachable from the DynamicUser service (the journal shows no request
  from its uid at all). /api/system now reads NTP from the kernel (adjtimex, timedatectl's own "max error < 16 s"
  rule), Wi-Fi/hotspot from `iw dev`, and the logger/kiosk from /proc, and it names any source that fails in
  `errors`. Also fixed: a log crash on Chromium's idle pre-connects (no request line).
- **Site config, not in the bootstrap:** the home Wi-Fi's static address (09-24, `nmcli connection modify … ipv4.method
  manual …`). It belongs to the network the Pi is on, not to the Pi, and the repo is public.

### M5 notes
- **Dashboard** (`web/dash/`, installed to /usr/local/share/gatbox-web/dash, served at `/dash/`; the kiosk opens it):
  plain HTML/CSS/JS, no framework, no CDN, no browser storage. 1024x600: the METER view never scrolls; tap targets
  ≥ 56 px; a phone gets a stacked layout.
  - **METER:** the reading at arm's length, mode chip, profile chip (the picker), window and where it came from,
    session age.
  - **Banners:** SET DIAL TO, LEADS REVERSED?, HOLD/REL/MAX-MIN, the jack reminder, MOVE THE RED LEAD BACK TO VΩ,
    NO READINGS / SESSION ENDED.
  - **Buttons:** MARK (tap = now; hold = with a label), NEW FILE / START LOGGING, ALARM ON/OFF, CAPTURE (bench
    profiles).
  - **Live chart:** window band, limit line, marks, SPIKE/ALARM dots. Tap = the reading nearest in time; drag =
    pan; pinch or wheel = zoom; LIVE = follow again.
  - **SESSIONS:** the report with the same chart over a past file (`/api/rail/samples`: min/max per bucket, every
    over-limit reading kept) and the report text. PDF/CSV only off the Pi's own screen: the kiosk has no way back
    from a PDF.
  - **MACHINE:** pick from the roster (keypad filter), the merged card, CLEAR.
  - **SYSTEM:** health tiles, logger START/STOP, EXIT KIOSK / SHUT DOWN (Pi screen only).
  - **DEVICES:** DMM chain, T48, scanner (M6), touch; NOT FITTED greyed.
  - **DUMP:** greyed until the T48 is plugged in; the flow itself is M7.
- **Alarm on screen:** a run of 2+ over-limit readings takes the screen (red, latched until ACK, peak kept up to
  date); a lone reading gets an amber SPIKE toast once it's over ("likely an autorange glitch"). ALARM OFF keeps the
  events and only skips the takeover. The 09-25 burst plays back as three SPIKEs and no takeover.
- **Headless Chromium on this Pi:** `--screenshot` of any http page hangs. The net log shows the request stopping
  right after its privacy-mode step, where it needs cookies, whose key Chromium fetches from the desktop keyring over
  D-Bus. `--password-store=basic` fixes it (the kiosk already uses it). `tests/cdp.py` drives Chromium over
  `--remote-debugging-pipe` (stdlib only: there's no websocket module) so the tests can tap, wait and screenshot.
- **Report layout (owner, 2026-09-28: the report's wall of text is "ugly and distracting", but she likes the info):**
  - The phone page (`/s/…`) and SESSIONS now show a verdict (HELD THE WINDOW / LEFT THE WINDOW / OVER-VOLTAGE), stat
    tiles and short tables: over-voltage with suspect/real tags, excursions, power cycles, marks, OL, dial turns,
    gaps. A table shows at most 8 rows, then "+N more".
  - The full text sits collapsed underneath; the PDF keeps it.
  - The session list's old "left the window" tag compared raw min/max, so every power cycle looked like a failure. It
    is now the report's verdict.
- **Found in the screenshots and fixed:**
  - the empty-value dash drew as a triple bar;
  - session rows squashed in the scrolling list;
  - every recent file tagged LIVE;
  - the band stayed after a dial turn;
  - "-5.25--4.75";
  - the first reading of an alarm run stayed amber;
  - mark labels clipped at the right edge;
  - "NO READINGS" flashed while a new file started;
  - disk usage keyed by long paths.

### M6 notes
- **The EY-H2 on this Pi (2026-09-28, read with lsusb and a read-only listener on its event node):**
  - USB `af99:8002` "Totinfo TOT2D PRODUCT HID KBW", a plain USB keyboard (EV_KEY; `/dev/input/event5` at the
    time), sending Enter after each code.
  - Scans decoded right on a US layout: a product barcode (digits), and `GATBOX:MARK` from a small Katasymbol
    label, capitals and colon (Shift) included. It read that label from 4–5 inches.
- **gatbox-scand is stdlib, not python3-evdev as the spec suggests.** The grab is one ioctl (EVIOCGRAB) and an
  event is a 24-byte struct. So there's no extra package, and the daemon is testable before an install; key events
  from a file stand in for the device (GATBOX_SCAN_FAKE).
  - It finds the scanner by USB ID in sysfs (it never opens the other input devices) and re-finds it after
    unplug/replug.
  - A code ends at Enter / keypad Enter / Tab / LF, or 0.5 s of no keys (a scanner set to send no suffix).
  - It posts every code to gatbox-web: `POST /api/scan`, loopback only.
- **gatbox-web decides what a code means,** and stays the only writer:
  - a roster slug (video_games + pinball, not retired; case doesn't matter) sets the machine;
  - `GATBOX:MARK` makes a mark with source `scan`;
  - `GATBOX:NEW` = NEW;
  - anything else shows as UNKNOWN CODE on the dashboard.
- **The unit:** DynamicUser + `input` group, `DevicePolicy=closed` + `DeviceAllow=char-input` (input devices only),
  `IPAddressDeny=any` except localhost. Its state (attached, grabbed, last scan) goes in
  `/run/gatbox-scand/state.json`, which feeds DEVICES.
- **Labels (owner, 2026-09-28):** she has a Katasymbol label maker (phone app: long batches, no CSV import). The
  dashboard's MACHINE → LABEL LIST shows the exact QR text for the two commands and every machine, with COPY (it
  works on plain http). `gatbox-labels` / `/labels.pdf` is the printable fallback: command card + 20 machines a
  page, 33 mm QRs.
- **Not driven from the Pi:** the label maker's Bluetooth protocol isn't documented, and its app does the job.
- **GPIO MARK button: DEFERRED (owner, 2026-09-28: she has a momentary button, but it waits until there's an
  enclosure to mount it in).** Ready for then, read off this Pi with pinctrl:
  - **The pin:** GPIO17 = physical pin 11, with GND on physical pin 9 (the pin next to it, same column: a 2-pin
    Dupont plug fits).
  - **Why it's free:** GPIO17 is unused (input). I²C (2/3) and SPI0 (7–11) are on, so those stay free, as do UART
    (14/15), SPI1 (16–21) and the reserved GPIO3/26.
  - **Software:** gpiozero 2.0.1 + python3-lgpio are installed. gpiozero opens /dev/gpiochip4, a symlink to
    gpiochip0 (the 40-pin header, root:gpio).
  - **Plan:** a small separate service (gpiozero Button, internal pull-up, 50 ms debounce, 2 s hold-off) that posts
    a mark with source `button`. Only the button's two leads touch the header; no external voltage.

### M7 notes
- **The T48 on this Pi (2026-09-28):**
  - It reports firmware 00.1.03 (0x103). minipro 0.7.4 expects 01.1.32 (0x120, `T48_FIRMWARE_STRING` in
    src/t48.h) and warns "Firmware is out of date".
  - The T48 keeps its FPGA algorithms on the programmer, and they're updated with the firmware (minipro man page,
    ALGORITHMS).
  - The owner asked for the update. minipro's own `dump-alg-minipro.bash` pins where the matching file comes from:
    `xgproV1278_Setup.rar` from the Kreeblah/XGecu_Software mirror (official XGecu software), SHA-256
    `cf5dd277…b8eec9`, whose `UpdateT48.dat` is 01.1.32. The download matched that checksum.
  - Pi OS's 7zip can't open RAR (it wrote an empty file), so the bootstrap adds `libarchive-tools` (bsdtar, the
    script's own prerequisite).
  - The firmware file is XGecu's and never goes in the repo.
  - `minipro -F` checks the file's version, size and CRC and asks y/n before switching the T48 to its bootloader.
  - **Updated 2026-09-28 21:38 (owner ran it, socket empty):** `echo y | minipro -F ~/t48-firmware/UpdateT48-01.1.32.dat`
    (the file's SHA-256 `af7394b8…3b4a5c` is next to it, with a provenance note). Before that, minipro checked the file
    with the answer "n": "contains firmware version 00.1.32 (newer)", the same 0x120 it expects (its own print
    format). After: `Found T48 00.1.32 (0x120)`, no out-of-date warning. `minipro -t` with the socket empty: every
    VPP / VCC / GND pin driver and logic pin Good, VPP and VCC overcurrent protection OK, supply 5.03 V.
- **gatbox-dump on the real T48 (2026-09-28 21:44, the SegaSonic EPR-15781C chip, TI 27C020):**
  - `-p 27C020@DIP32` stopped at read 1 with minipro's own words, "Invalid Chip ID: expected 0x8934, got 0x9732
    (TMS27C020@DIP32)", and the suggestion to use `-p TMS27C020@DIP32`.
  - `-p TMS27C020@DIP32`: pin check not supported for this part (noted), two reads identical (262,144 bytes), SHA-1
    `9f524012…` (the same as the M2 dump, so the firmware update changed nothing), MATCH `sonic/epr-15781c.ic18`.
    Archived with its sidecar. 14 s in all.
- **The dashboard flow (why a path unit):**
  - gatbox-web can't see the T48 (private /dev), and it shouldn't: it's the always-on web server. So it only
    validates a request and drops `request.json` in `/var/spool/gatbox-dump` (group gatbox-dump, setgid 2770;
    gatbox-web joins that group).
  - `gatbox-dump.path` (PathExists) starts `gatbox-dump.service`, a oneshot running as the system user
    `gatbox-dump`:
    - it can write only `/srv/gatbox/roms` and the spool;
    - of /dev, only USB device nodes (DevicePolicy=closed + char-usb_device; only the T48's is group plugdev);
    - it talks only to localhost.
  - The job claims the request (rename → running.json) and writes progress and the outcome to `status.json`, which
    gatbox-web relays over SSE ("dump" events).
  - This mirrors the logger's flag-file pattern. systemd gives the job its own identity, sandbox, journal and
    20-minute timeout. A oneshot can't run twice, so it's one dump at a time, and gatbox-web also refuses (409)
    while one is queued or running. A crash in a dump can't take the web server down.
  - Picker: 14 family tiles (data/eproms.json: families only) → the exact names from minipro's own list, which the
    bootstrap writes at install (`minipro -q T48 -l`, regenerated when minipro changes). A name without `@` is the
    DIP package (e.g. `M27C801`).
  - A stop offers the one-tap fix: USE <the part minipro names>, READ ANYWAY (ignore the ID), ARCHIVE ANYWAY
    (blank), I CHECKED (non-JEDEC), TRY AGAIN (reseat).
- **First dashboard dumps (2026-09-29 01:2x–01:4x, the SegaSonic chip), three findings:**
  - **The kiosk kept the old page after the install** (the DUMP tab still said "arrives in M7"). The server now
    reports a fingerprint of the installed dashboard files (`dash_version` in /api/system), and the page reloads
    itself within 10 s when it changes.
  - **A one-tap mis-pick of `TMS27C020@TSOP32`** for the DIP chip: minipro answered "Invalid Chip ID: expected
    0x9732, got 0xFEFF (unknown)" and stopped before reading. The screen offered only READ ANYWAY. Now:
    - the picker lists DIP parts first and the adapter-only packages (TSOP/PLCC/SOP) apart, under an amber
      warning, and a non-DIP pick says so;
    - `gatbox-dump` tells the causes apart: (a) minipro names another part → USE it; (b) a non-DIP pick with an
      unknown ID → the adapter note + USE <the DIP version>; (c) a DIP part with an unknown ID → check the
      seating, and --ignore-id only for a part with no ID.
  - **"MAME couldn't run": `FileNotFoundError`.** Debian's MAME is `/usr/games/mame`, and a systemd service's PATH
    has no /usr/games. gatbox-dump now finds it by its full path. A dump archived without an identification gets
    it (the sidecar only; the .bin is never rewritten) when the same chip is dumped again. A MAME that runs but
    doesn't answer is reported as an error, not a NO MATCH.
  - The dump itself was right: SHA-1 `9f524012…`, the same as the two earlier dumps (the TSOP mis-pick did the chip
    no harm). It was archived under segasonic-the-hedgehog through the DUMP tab's new **FOR <machine>** choice (per
    dump; the logger's machine, DDR at the time, stays as it is).
- **Part names (minipro -q T48 -l, 32,361 entries):** generic names repeat (two `27C010@DIP32` entries), next to
  manufacturer-prefixed ones (AM27C010, M27C1001, TMS27C010…). The M2 dump showed why the exact part matters: generic
  `27C020@DIP32` refused a TI chip, "Invalid Chip ID: expected 0x8934, got 0x9732 (TMS27C020@DIP32)", and it read as
  `TMS27C020@DIP32`. The dump flow surfaces that message and re-selects; it never passes `-y`.

### Owner requests notes (2026-09-29)
- **"There is no way to import a new game/machine."** MACHINE → **+ ADD MACHINE**: name, maker, VIDEO GAME /
  PINBALL, platform (the roster's 44, or NOT SURE = platform + risk `confirm`, the roster's own convention), notes.
  - gatbox-web makes the ID from the name (lower-case, hyphens; apostrophes dropped, & → and, accents folded, `pin-`
    for pinball) and shows it before saving (a dry run), because it's permanent: it's what the QR code holds. A
    name or ID already on the roster (retired ones too) is refused before saving. `unassigned` is reserved.
  - Saved to `/var/lib/gatbox-web/roster-added.json` in the roster's entry format (all 8 fields, the file's order).
    The installed roster file is never written: the bootstrap owns it and the maintenance app is its source.
  - `gatboxlib.profiles.roster()` merges the additions, so the picker, scanner, captures, the DUMP tab's FOR
    machine, and the label sheet (`/labels.pdf` rebuilds when the additions change) all take a new machine at once.
  - EDIT (machines added here only) changes the name, maker, platform or notes; the ID and kind stay.
  - **EXPORT ROSTER**: `GET /roster.json`, the file plus the additions in the file's own layout (2-space indent,
    ASCII escapes), for the maintenance app to import. On the kiosk it shows the address to open on a phone.
  - When a newer roster file (installed by the bootstrap) has the same slug, the file's entry wins and the Pi's copy
    is ignored; a slug the file retires stays off the floor. A damaged additions file is never overwritten (503).
- **"What does capture even do?"** It saved the live reading with a label into the machine's notes, but the only
  place to see them was MACHINE → Captures. Now: **SAVE READING**, the subtitle says which machine it goes to, the
  last three show on one line under the reading, and MACHINE lists "Saved readings".
- The on-screen keypad got a shift key (abc/ABC) and ' & : ! for names. The meter's action buttons now cut long
  subtitles with "…" instead of widening the grid past the screen edge.
- **"All of the MAME files for the games that I have for easy hashing."** No ROM sets are downloaded (copyrighted;
  only piracy sites carry them). Hashing doesn't need them: MAME's driver list carries every chip's name, size,
  CRC32 and SHA-1.
  - `data/gatbox-mame-sets.json` (git-ignored: it's the floor list; the local pre-push hook refuses it by name):
    slug → the MAME **parent** set, `sure` or `check` (+ why), `prefer` (the version shown first), or `set: null` +
    why for machines MAME doesn't have. Built by hand from MAME's own list (name, maker, year, driver): matching by
    name alone put the Joust and Defender *pinballs* on the video games, a Tiger handheld on Batman, and a mahjong
    game on TMNT.
  - `gatbox-mame-roms --build` streams `mame -listxml` (~290 MB of XML) and keeps the mapped parents and all their
    clones (revisions, regions), devices and BIOS machines aside. The bootstrap runs it at install, only when the
    MAME version or the list changed (`--check`), with HOME in a throwaway dir. The real SegaSonic set built this way
    lists epr-15781c.ic18 with the SHA-1 of the owner's dump (tests/test-mame.py checks it).
  - `/api/mame/<slug>` ticks a chip when any archived dump has its SHA-1 (under any machine, `unassigned` too) and
    shows first the version the dumps match best, else `prefer`, else the parent.
  - MACHINE → "ROM chips (MAME)": the version switch, ✓ + the dump's label, size + the EPROM families of that size,
    CRC, SHA-1, board region; hard-disk / CD images listed as "not a T48 job". DUMP → WHICH CHIP?: the machine's
    chips, undumped first; a pick fills the label from the chip's printed name (MAME's name before the board spot)
    and lights the family tiles of its size. The exact part is still picked off the chip (hard rule 6).
  - `data/eproms.json` families carry `bytes` (a whole-chip dump's size) for that.
- **Manuals, "pulled in with their specs", viewable on the dashboard.** Owner's choices: every document the archives
  have (manuals, schematics, parts catalogs, kit sheets, bulletins); rails + the spec sheet; her own PDFs by folder
  and by phone upload. Plus (her addition) actual values from the field next to the manual's.
  - Sources, checked from the Pi: arcade-museum.com (TAMA) direct PDFs; archive.org arcademanual_* items, whose
    `…_text.pdf` is the scan with an OCR text layer (so scans are searchable with no OCR here); Stern's own PDFs;
    arcade.segakore.fr (Sega). `gatbox-manuals fetch` takes https from those hosts only (redirects too), a real PDF
    only (%PDF- + pdfinfo), pins sha256 (`--pin`) and refuses a changed file, writes `<id>.pdf` + `.json` sidecar +
    `.txt` (one page per form feed), one download at a time.
  - `/srv/gatbox/manuals/<slug>/` (tmpfiles, 2775 root:gatbox-manuals; the owner joins the group; gatbox-web joins it
    with ReadWritePaths for uploads only). Never in git: `*.pdf` is ignored and the pre-push guard refuses PDFs and
    `gatbox-manuals.json` (the list: git-ignored like the roster).
  - gatbox-web (`manuals.py`): documents per machine (sidecar titles/kinds, PDFs dropped in by hand as "yours"), the
    PDF itself, a page as a PNG (`pdftoppm`, widths 800/1200/1600/2400, cached in its CacheDirectory, ~1 GB cap),
    search (`pdftotext` text, made on the spot for dropped PDFs), uploads (body = the PDF, `Content-Type:
    application/pdf`, ≤200 MB, never over a file). A file is served only if it's in that machine's folder listing.
  - The spec sheet: rail limits read off a manual page (`{rail, lo, hi, doc, page, quote}`), the rest of the spec
    page (`{what, value, doc, page, quote}`). CONFIRM shows the page and the manual's words; the API confirms only a
    limit on the list, never numbers sent to it. Actual values from the field (typed or the live reading, a note,
    optionally a window of its own) sit beside the manual's, which are never changed. State in gatbox-web's state
    dir: `machine-specs-confirmed.json`, `machine-actuals.json`.
  - `gatboxlib.profiles.machine_specs()` layers them: specs file < confirmed manual limit < the machine's own actual
    window; `resolve()` names the source (`machine:<slug>` or `actual:<slug>`), so the logger's header, the meter and
    the MACHINE card show whose window it is. A change to the current machine's window while logging starts a new
    file (like a profile change). Confirmed specs stay on the Pi: the public repo keeps only the one it had.
  - Dashboard: MANUALS tab (FOR <machine>, like DUMP), the spec sheet table, the documents, ADD PDF on a phone; the
    viewer (page images, PREV/NEXT, swipe, pinch/drag/+−, double tap, page jump, SEARCH, OPEN PDF on a phone).
  - **The list (2026-09-29):** 396 documents for 88 of the 95 machines, picked by hand. Sources:
    - the Internet Archive's arcademanuals collection (4,753 items; the OCR'd `_text.pdf` when the original is an
      image-only scan, 346 of 373);
    - the Arcade Manual Archive's index (2,822 PDFs);
    - Stern's own PDFs (wp.sternpinball.com);
    - the SEGA documents database (SegaSonic 420-6095);
    - pinrepair.com (Bally D&D);
    - PrimeTime Amusements (Pac-Man Battle Royale; Bandai Namco's link is gone).

    Left out: other games, cocktail/conversion variants, older printings and Japanese-only editions.
    - Stand-ins: UMK3 gets the MK3 manual (same Wolf-unit board, MAME midwunit.cpp); NBA Jam TE gets NBA Jam's
      (T-unit); NFL Blitz 2000 gets the 1997 Blitz kit (Seattle). The Sportstation manuals were dropped: those are the
      Vegas board.
    - Two candidates side by side, with a note: Batman (Atari video / Data East pinball), Batman pinball (TDK / The
      Batman 2023), D&D pinball (Bally 1987 / Stern).
    - None found: Darkstalkers, DDR (which mix?), House of the Dead 2 (only on Manualzz/ManualsLib: ADD PDF), The
      Swarm, Retro Raccoons (glitchbit.com has it), Snow Bros 2, Super Ghouls (no arcade board). IPDB refuses
      automated downloads (403), so the regular Addams Family manual is a note pointing there.
  - **Fetch:** 389 of the 396 documents, 1.2 GB, sha256 pinned in the list. Seven are waiting on four Internet
    Archive storage nodes that answer 500; a later `gatbox-manuals fetch` gets them.
  - **Spec sheets:** found with scans of every document's text, then each read on its page.
    - Rail limits (18, on 10 machines):
      - Batman (Atari) +5 ±0.25;
      - California Speed +5 4.90–5.10, +12 11.5–12.5, −5 −4.75..−5.25;
      - Captain America +5 ±5%;
      - Defender +5 4.75–5.25 (power supply recap);
      - Gauntlet Legends +5 4.90–5.10, +12 11.5–12.5, −5, −12 (p.51: the same as the owner's specs file);
      - Hyper Sports +5.0 ±0.1;
      - RoboCop +5 4.90–5.10;
      - Super Pac-Man +5 ±0.2;
      - Tekken 3 +5 ±5% / kit +5% −1%, +12 ±5%;
      - Tetris +5 ±0.25, +12 ±0.5.
    - 52 facts on 42 machines: power, line voltage, fuses, monitor.
    - A setpoint without limits ("adjust the +5V output to 5.4V", Aliens) is a fact, never a window.
    - Each entry was checked by script against its page's text before it went in.

## Bring-up 1: bootstrap run — 2026-09-23

### What ran
1. Read all of `~/Downloads/gatbox-bootstrap.sh` (700 lines, sha256 `25d47335…6bff860`).
   Unmodified copy: `~/gatbox/gatbox-bootstrap.orig.sh`. Payload extracted to `~/gatbox/payload/`.
2. Wi-Fi: I briefly saved a `barcade` client profile with SSID WalkinAround, then deleted it after
   the user said WalkinAround was the name for the **hotspot**, not the barcade Wi-Fi. **No barcade profile exists yet**
   (the user doesn't know the barcade credentials yet).
3. `sudo GATBOX_AP_PSK=… bash ~/Downloads/gatbox-bootstrap.sh` exited 0 (full output in `bootstrap.log`).
   `GATBOX_RTC_BATTERY` was not set.
4. After the script: `nmcli connection modify gatbox-ap 802-11-wireless.ssid WalkinAround` (**renamed back to
   GATBOX on 2026-09-24**, to match the script and the project docs),
   because the script hard-codes the SSID `GATBOX`. Re-running the script with GATBOX_AP_PSK set recreates
   the profile as `GATBOX`, so the rename has to be done again after any re-run.

### Checks
| Item | Result |
|---|---|
| `sigrok-cli -L` | `uni-t-ut61e-ser  UNI-T UT61E (UT-D02 cable)` ✅ (sigrok-cli 0.7.2) |
| config.txt | `[all]` / `usb_max_current_enable=1` appended ✅. Backup is `config.txt.pre-gatbox`. raspi-config also set `dtparam=i2c_arm=on` and `dtparam=spi=on` |
| EEPROM | `PSU_MAX_CURRENT=5000` staged ✅ (`/boot/firmware/pieeprom.upd` + `recovery.bin`). Existing BOOT_UART / BOOT_ORDER=0xf461 / NET_INSTALL_AT_POWER_ON kept |
| gatbox-raillog | enabled + active ✅, "waiting for /dev/gatbox-dmm" |
| udev rule | `/etc/udev/rules.d/99-gatbox-dmm.rules` ✅ (any VID 067b → `/dev/gatbox-dmm`, dialout 0660) |
| groups | dialout plugdev gpio i2c spi video ✅ (these were already present on this fresh image) |
| Hotspot | `gatbox-ap`, SSID WalkinAround (GATBOX since 09-24), AP mode, autoconnect no; `gatbox-ap-fallback.service` enabled ✅ |
| Installed files | all 9 are byte-identical to the extracted payload ✅ |
| ModemManager / brltty | neither was installed, so nothing was removed |

### Things that were odd or need watching
- **Bootloader version upgrade.** `rpi-eeprom-config --apply` stages the config onto the *newest* image, so the
  reboot also upgrades the bootloader from 2026-01-21 to 2026-05-26 (default channel). The user OK'd this.
  To cancel before the reboot: `sudo rpi-eeprom-update -r`.
- `WARNING: SPI device /dev/spidev10.0 not found` during EEPROM staging is expected: `[pi5] dtoverlay=nospi10`
  disables the direct flash path, so the update applies through recovery.bin at the next boot.
- **5 A setting.** `PSU_MAX_CURRENT=5000` + `usb_max_current_enable=1` tell the Pi its supply is 5 A.
  This is only safe with a real 5 A supply (e.g. the official 27 W). A weaker brick can brown out overnight.
  Check `vcgencmd get_throttled` after long runs.
- **Logger gap: confirmed and fixed in bring-up 2** (see below).
- `gatbox-rail-report` defaults to a 5 V ±5% window. For the AA bench test, use `--lo 1.2 --hi 1.7`.
- In a cabinet with no known Wi-Fi (no barcade profile yet), the hotspot comes up ~60 s after boot:
  join **GATBOX**, Pi at `10.42.0.1` / `gatbox.local`.

## Bring-up 2: bench test — 2026-09-23

### Checklist
- [x] EEPROM `PSU_MAX_CURRENT=5000`, bootloader 2026/05/26 (read with `vcgencmd bootloader_config` / `bootloader_version`: no sudo needed)
- [x] `vcgencmd get_throttled` = `0x0` (still `0x0` after the bench test)
- [x] `id` (dialout ✅), `hostname` gatbox
- [x] `gatbox-raillog` active; NTP synced 30 s after boot
- [x] (a) PL-2303 → `067b:23a3` (PL2303 HXN), `/dev/gatbox-dmm → ttyUSB0`
- [x] (b) AA reads **1.611 V DC**, CSV growing at 2.0 S/s, `gatbox-status` all green, ACT LED blinking. *Needed fix 1*
- [x] (c) head off → "no reading for 5 s; ending session" → head on → new file within ~6 s. *Needed fix 2*
- [x] (d) `gatbox-rail-report --lo 1.2 --hi 1.7`: plateau + both excursions correct, plot OK. *Needed fix 3*
- [x] "GATBOX Rail Monitor" under Accessories opens the live status (user checked)

### Fixes (copies in `~/gatbox`, installed to `/usr/local/bin` by the user with sudo)
1. **`gatbox-raillog`: `--continuous` → `--samples 1000000000`.** sigrok-cli in `--continuous` mode prints
   "Press any key to stop acquisition" and watches stdin. Under systemd stdin is /dev/null, so it stopped
   immediately after SR_DF_HEADER. No readings were ever logged, and the only error was the harmless
   `g_atomic_ref_count_dec` assertion on exit. 1e9 samples at ~2/s is ~15 years.
2. **`gatbox-raillog`: `read -t 5` in `session()`.** With the D02 head off, sigrok keeps the port open and goes
   quiet, so the old loop blocked and later resumed in the same file (a 20.5 s gap hidden inside one session).
   Now 5 s of silence ends the session and kills that sigrok; the main loop retries and starts a new file.
   (The exit status is caught from `read` itself, because a `while` loop's status comes from its body.)
3. **`gatbox-rail-report`: honour the unit column.** The meter autoranges to its mV range near 0 V, and the
   report treated `-1,mV` as −1 V. It now scales mV/µV to V. A dead rail logging e.g. 150 mV would
   previously have been reported as 150 V.

`~/gatbox/gatbox-bootstrap.sh` = the original script with fixes 1–3 spliced into its heredocs (bash -n OK),
plus the mode-aware gatbox-rail-report (2026-09-24, see "Remote viewing"), plus gatbox-web + its unit + font fetch
(2026-09-24, `curl` added to the packages). Use it instead of the `.orig`/Downloads copy for any re-run; the heredocs
for the other 7 files are unchanged. **Upload it to the project** in place of `claude/gatbox-bootstrap.sh`.
The installed raillog and rail-report now differ from `payload/` on purpose.

### Meter notes (original UT61E)
- There is **no RS232 button** on this model: the serial output is always on (the "hold REL 2 s" instruction in
  the UT61 manual applies to other models in the series). A quick REL/Δ press zeroes the display, so leave it off.
- The UT-D02 head slides into the slot on the **top edge** of the meter.
- Phone selfie camera at the top slot shows the IR LED flickering if the meter is sending.
- In the first tries, raw reads got 0 bytes until the D02's 9-pin end / head was reseated. If it's ever silent
  again, reseat both ends before suspecting anything else.

### Open items
- A **Hauppauge** USB device (3-1.2) appeared/re-enumerated during the bench test (`can't set config #1, error -71`
  once). The user didn't say whether it belongs there. Not needed by GATBOX; check what it is before cabinet use.
- No barcade Wi-Fi profile yet → the GATBOX hotspot is how to reach the box in the cabinet.
- No RTC battery → timestamps depend on NTP. Offline, the file header says `clock=` accordingly.
- The 5 A settings assume a real 5 A supply (see above).

### Remote viewing (added 2026-09-23, after the bench test)
- `gatbox-web` (`/usr/local/bin`, source `~/gatbox/gatbox-web`) + `gatbox-web.service` (enabled). Stdlib Python on
  port 80, DynamicUser, read-only access to `/var/log/gatbox`, plot/PDF cache in `/var/cache/gatbox-web`.
- Pages: sessions list with live reading + in/left-window tag (refresh 30 s); per session the report + plot,
  a From/To range (Pi wall-clock) and "PDF of this view" (letter size, header + plot + report, multi-page).
  CSV download. The analysis is `gatbox-rail-report`, so the numbers match the CLI.
- Tested: phone on home Wi-Fi (http://<home-lan-ip>) ✅, **hotspot** (then named WalkinAround) brought up by hand with
  `nmcli con up gatbox-ap`, phone got 10.42.0.198 and loaded http://10.42.0.1 ✅, PDF open + share on phone ✅,
  fake 8 h / 144-dip log → 3-page PDF in 3 s ✅.
- In `gatbox-bootstrap.sh` since 2026-09-24 (payload heredocs + `systemctl enable`); before that it was hand-installed.
- Limits: a range stays inside one session file; the plot is dark-themed in the PDF too.
- **Meter mode shown (2026-09-24).** Taken from the CSV unit + flags columns: DC/AC voltage, DC/AC current,
  resistance, continuity (closed/open), diode, capacitance, frequency, duty cycle. It appears on the live card, per
  session (min/max per mode, in that mode's unit) and in the PDF header. HOLD / REL / MAX-MIN are shown in red. The
  window tag applies only to DC voltage.
- **gatbox-rail-report is mode-aware too (2026-09-24).** It uses the same mode rules as gatbox-web (keep the two
  `mode()` functions in step). It prints a `mode:` line, stats per mode in that mode's unit (SI prefixes are scaled
  now, not just mV/µV), MODE CHANGES and a WARNING for HOLD / REL / MAX-MIN. The window and EXCURSIONS use DC
  voltage readings only. The plot has one panel per mode, with its own axis label (V DC, V AC, kΩ, mA DC, continuity
  open/closed, ...). For a DC-only session the only difference is the added `mode:` line. The same file is in the
  bootstrap heredoc. gatbox-web's plot/report cache key includes the report's mtime, so a new report takes effect
  without clearing the cache by hand.
- **Dial-through on the real meter (2026-09-24 11:02–11:05, tail of `rail_20260924_000412.csv`).** The meter sent:
  V/mV DC, V AC, Hz and % (with an AC or DC flag in the voltage positions, and none in the Hz position),
  Ω, pF/nF/µF, µA/mA DC. OL is `inf` with a `T` prefix (`inf,TΩ`, `inf,TV`, `inf,TF`). No diode or continuity rows
  appeared, so those two are still untested on real data. **sigrok writes Ω as U+2126 OHM SIGN**, not
  Greek Ω (U+03A9): both `mode()` functions map it, since without that resistance showed up as the modes "Ω" and "TΩ".
  Turning the dial doesn't end a session (the stream never stops for 5 s), so the dial-through is in the same file
  as the overnight AC log.
- **Restyled + folded into the bootstrap (2026-09-24).** gatbox-web now uses the project design tokens: violet
  gradient + scanlines, Chakra Petch headings, Share Tech Mono text, magenta buttons, cyan mode badges, amber for
  HOLD/REL/MAX-MIN, red for out-of-window. The two fonts (SIL OFL, licence files alongside) live in
  `/usr/local/share/gatbox-web/fonts` and the Pi serves them itself at `/font/…`, cached for a year, because the hotspot
  has no internet. Without them the page falls back to system fonts. The bootstrap fetches them from google/fonts
  commit `23e54b5`, checks each sha256, and only warns if offline. gatbox-web + its unit are now bootstrap payload.
- **Home Wi-Fi fixed at <home-lan-ip> (2026-09-24).** `nmcli connection modify netplan-wlan0-<home-wifi>
  ipv4.method manual ipv4.addresses <home-lan-ip> ipv4.gateway <home-lan-ip> ipv4.dns "<isp-dns> <isp-dns>"`
  (the DNS servers the router's DHCP gave out). It's saved to that profile's `/etc/netplan/90-NM-06fe…yaml`. It
  applies only on that SSID; eth0 (`netplan-eth0`, any wired network) stays DHCP (.92 at home). Why: the Arch PC
  has no `.local` lookup (no nss-mdns), so it uses the IP. **.79 is inside the router's DHCP pool:** reserve it in
  the router for wlan0 MAC `<wlan0-mac>`, or another device can get it once the Pi's 2-day lease lapses.
  Undo: `nmcli connection modify netplan-wlan0-<home-wifi> ipv4.method auto ipv4.addresses "" ipv4.gateway ""
  ipv4.dns ""`, then `nmcli device reapply wlan0`.
- **Hotspot renamed back to GATBOX (2026-09-24)** with `nmcli connection modify gatbox-ap 802-11-wireless.ssid
  GATBOX`, while the profile was inactive. The password is unchanged. It now matches the script, so a re-run no
  longer undoes a rename.

### Ready for the cabinet
The bench test passed end-to-end. To deploy: meter on **V DC**, Δ off, D02 head in the top slot, 9-pin end
seated, adapter plugged into the Pi, Pi powered. The ACT LED blinks when it's logging. One CSV per unbroken
stretch lands in `/var/log/gatbox/`. Next morning: join **GATBOX** on a phone (the hotspot is up ~60 s after
boot when no known Wi-Fi is around) and open **http://10.42.0.1**. Pick a session, narrow From/To if needed,
and use "PDF of this view" to save or share. Default window 4.75–5.25 V.
