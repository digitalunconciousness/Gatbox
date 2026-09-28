# GATBOX Phase 2: Plan

**GDD-GAT/01 · drafted 2026-09-24 · revised 2026-09-26 · owned hardware only**

Phase 1 left a Pi that records a rail by itself. Phase 2 gives it a **screen, a real
dashboard, and workflows**. The Pi becomes the bench brain for the gear already on hand:

| Owned | Role in Phase 2 |
|---|---|
| Raspberry Pi 5 4GB + cooler (Trixie Desktop) | runs everything |
| Waveshare 7inch HDMI LCD (C) | the face: dashboard in kiosk mode |
| UT61E + UT-D02 + PL-2303 | live rail panel, sessions, reports |
| XGecu T48 | dump → identify → archive workflow |
| Eyoyo EY-H2 USB scanner | ties logs and dumps to roster machines; event markers |
| RTC coin-cell holder on J5 (**non-rechargeable** cell, 09-26) | real timestamps on offline boots |
| Ender 3 | bench stand for panel + Pi |

**Who builds it:** Claude Code on the Pi, from `gatbox-phase2-handoff` (`PROMPT.md` + `ref/`).
Order: RTC follow-up → 2A → 2B → 2C → 2D → 2E → 2F → 2G. It stops for every step that needs
hands on hardware.

**Phase 3 (needs purchases):** ADALM2000 (M2K) + Scopy/libm2k probing, UUGear MEGA4 +
uhubctl per-port arming. Anything that *writes* to hardware from the dashboard (T48 burn,
board power) also waits for Phase 3's arming discipline.

**Parallel, not Phase 2:** Mule Mk III coupons → tub → base hull; Gauntlet Legends follow-ups
(switch, self-test, CF card); more cabinet logs (any night, the logger must keep working).

---

## Ground rules for all of Phase 2

- **One repo, one install path.** Everything lives in a git repo; the Pi is rebuilt by
  `git pull && sudo bash bootstrap/gatbox-bootstrap.sh`. Nothing is configured by hand.
- **The logger owns the serial port.** The backend and dashboard read its CSVs and never
  open `/dev/gatbox-dmm`.
- **No hardware writes from the dashboard.** Phase 2 writes only Pi-side state (profile,
  current machine, marks, new session, capture points) and T48 *reads* to disk. Never a chip.
- **Plain HTML/CSS/JS dashboard**, house style, no framework, no CDN, no localStorage,
  big-tap UI at 1024×600.
- **apt before pip.** evdev, qrcode, MAME all come from Debian packages.
- **The desktop stays.** Kiosk mode sits on top of it and can be toggled.
- **RTC charging stays off.** The cell is a primary cell: no `dtparam=rtc_bbat_vchg`, ever.

---

## 2·0 · RTC follow-up  *(first; small)*

- [ ] Verify charging is off: `/sys/class/rtc/rtc0/charging_voltage` = 0, no
      `rtc_bbat_vchg` in config.txt. Bootstrap: `GATBOX_RTC_BATTERY` removed; any
      `rtc_bbat_vchg` line gets commented out; only `GATBOX_RTC_CHARGE=ML2020` could ever set it.
- [ ] `gatbox-rtc-sync` timer (hourly): `hwclock --systohc` only while NTP-synced, and stamp
      the write.
- [ ] Logger labels sessions `ntp` / `rtc` / `unverified`. `rtc` requires: the RTC was stamped
      from NTP at least once, it hasn't gone backwards since, and the system clock agrees
      with it to within 5 s (this catches fake-hwclock). No 90 s NTP wait when the RTC is trusted.
- [ ] gatbox-status, report and web show the clock source; RTC health on the status line.

**Exit:** unplug overnight, boot with no network, and the new session says `clock=rtc` with the right time.

---

## 2A · Foundation  *(~1 session)*

- [ ] `gatbox` repo on Forgejo: `bootstrap/`, `backend/`, `dashboard/`, `tools/`, `data/`,
      `docs/`. Existing sources move in unchanged (history kept).
- [ ] Bootstrap either inlines sources (the `build.py` pattern) or installs straight from the
      checkout. Either way: fresh card + clone + one command. Re-runs change nothing, and
      a re-run offline succeeds.
- [ ] Bootstrap module: **minipro**. Use the apt package if it handles the T48, else build
      the pinned upstream tag. Includes udev rules.
- [ ] Bootstrap module: **MAME CLI** (`apt install mame`; trixie ships 0.276 for arm64), used
      only for `mame -romident`. No gameplay on the Pi.
- [ ] Re-verify the T48 on the fresh card: dump a known EPROM, SHA1 matches.

**Exit:** a blank card plus `git clone` plus one command gives a fully working Pi,
T48 included.

---

## 2B · The face: Waveshare 7" (C)  *(~1 session)*

- [ ] HDMI + USB touch straight into the Pi. Confirm EDID gives 1024×600 (starter §8;
      `video=` in cmdline.txt only if needed). Confirm touch lands where you tap under labwc.
- [ ] Power check with the panel on the Pi's USB: `gatbox-status` shows no undervoltage
      under load.
- [ ] **Kiosk at boot:** Chromium fullscreen from `~/.config/labwc/autostart`, started only
      once `gatbox-web` answers. No keyring prompt, no "restore pages" bubble after a power
      cut, and restarted if it dies. `gatbox-kiosk on|off|status`. EXIT KIOSK is a long-press
      on the SYSTEM panel, local only.
- [ ] Blanking: default is never blank in kiosk (the (C) has a backlight switch). If blanking
      stays, it's inhibited while a session is live.
- [ ] Print a **bench stand** that carries the panel with the Pi on its back (the (C) board
      has Pi mounting holes). 4.0 mm walls, PETG, the 08-24 print profile.

**Exit:** power on → dashboard on the 7", touch works, desktop is one command away.

---

## 2C · Backend: gatbox-web, grown  *(~1–2 sessions)*

**DECIDED 2026-09-26:** no separate `gatbox-api`. The existing `gatbox-web` (stdlib,
DynamicUser, :80) becomes the backend: one server, and every current URL (`/`, `/s/`, `/png/`,
`/pdf/`, `/csv/`, `/font/`, `POST /control`) keeps working. The dashboard is served at `/dash/`.
It gets restructured into a small package. FastAPI + uvicorn (apt) only if a concrete need turns
up.

- [ ] **Replay harness first:** `tools/gatbox-replay` feeds a CSV to the logger as sigrok
      output, so everything below is tested without the meter.
- [ ] `data/profiles.json`: one source of profiles for the logger, report, web and dashboard.
- [ ] `data/gatbox-machine-specs.json`: per-machine rail limits keyed by slug (*not* a roster
      field, so the maintenance-app import can't break). Starts with gauntlet-legends.
- [ ] Writable state goes in gatbox-web's existing state dir (the Start/Stop flag pattern).
      gatbox-web is the only writer and the logger reads it.
- [ ] Logger header: `# profile=`, `# window=… source=profile|machine:<slug>|user`,
      `# alarm_hi=`, `# machine=`; `# mark=` lines mid-file. **One file = one machine + one
      profile + one dial mode.**
- [ ] Report: window from the header; **powered vs off** (< 0.5 V on rail profiles; stats
      on powered stretches only, power cycles listed); **OVER-VOLTAGE** section; marks listed
      and drawn. The 09-25 GL file is the regression fixture.

| Endpoint | Returns / does |
|---|---|
| `GET /api/system` | temp, throttling, EXT5V, disk, network/hotspot, clock source + RTC health, kiosk |
| `GET /api/meter` | live mode, value, flags, profile, window + source, machine, dial check, alarm |
| `GET /api/rail/live` | **SSE** tailing `/run/gatbox/current` across session rollovers |
| `GET /api/rail/sessions` | start, duration, mode, profile, machine, window, clock, powered stats, OV + marks counts |
| `GET /api/rail/report/{file}` | report text + parsed sections + PNG (existing cache) |
| `GET/PUT /api/meter/profile` | selected profile (+ user values: ripple ceiling, current window) |
| `GET/PUT/DELETE /api/machine` | current machine slug (validated against the roster) |
| `POST /api/mark`, `POST /api/session/new` | marker; new session (= Start) |
| `GET/POST /api/captures` | per-machine capture points |
| `GET /api/devices` | DMM chain, T48, scanner, touch panel |
| `GET /api/roster[/{slug}]` | roster entry + platform + critical actions + machine specs |

**Exit:** every number the dashboard needs is one GET away, and the old phone view still works.

---

## 2D · Dashboard v1  *(~2 sessions)*

Built at `/dash/` for 1024×600 in kiosk, phone-usable too. Layout vocabulary from
`gatbox-terminal.html`, colours from CLAUDE.md's synthwave tokens (the mockup's palette is the
older flat-black one). No simulated data.

- [ ] **METER** (default view): big live reading in whatever mode the dial is on, sparkline,
      session age, window band, logging indicator, **measurement picker** (below).
- [ ] **SESSIONS:** list, tap for the report (PNG + excursions, OV events, power cycles, marks, gaps).
- [ ] **MACHINE:** current roster card, rail specs, captures, dumps.
- [ ] **SYSTEM:** temp, throttling, EXT5V, clock + RTC, network, kiosk exit.
- [ ] **DEVICES:** DMM chain / T48 / scanner / touch.
- [ ] Phase 3 panels (M2K, hub arming, board power) greyed out as **NOT FITTED**.

**Exit:** at the bench you can watch a rail live and read last night's report without SSH.

### The measurement picker (DMM modes)

The UT-D02 link is **one-way**: the UT61E's dial picks the function and the Pi can only
read it. So the dashboard works the other way round: **you pick what you're measuring,
the dashboard checks the dial and tells you when it's wrong.**

| Profile | Dial | Window | Use |
|---|---|---|---|
| +5V rail | V⎓ | 4.75–5.25 | overnight log |
| +5V rail · Neo Geo | V⎓ | 5.00–5.20 (never over 5.2) | overnight log |
| +12V rail | V⎓ | 11.4–12.6 | overnight log |
| −5V rail | V⎓ | −5.25…−4.75 | overnight log |
| Ripple on a rail | V~ (on the DC rail) | you set the ceiling | overnight / bench |
| Resistance / continuity | Ω | none, or you set one | bench, capture points |
| Diode | diode | none | bench, capture points |
| Capacitance | F | none | bench, capture points |
| Current | A / mA / µA | you set | bench; jack reminder |
| Frequency / duty | Hz / % | none | bench |
| Free | anything | none | just show what the dial says |

- **Dial check:** the live mode doesn't match the profile → full-width amber **SET DIAL TO Ω**.
  It clears itself.
- **LEADS REVERSED?** when a rail profile reads the opposite sign.
- **HOLD / REL / MAX-MIN** in the stream → warning banner. A HOLD left on freezes an overnight log.
- **Current profiles:** a jack reminder while one is selected, and **MOVE THE RED LEAD BACK TO VΩ**
  when you leave one. A lead left in the A jack shorts the next rail you measure.
- **Capture points** (bench profiles): CAPTURE → label on an in-page keypad (not the OS
  keyboard) → saved to the current machine.
- **The machine's own spec wins:** scanning a cabinet with a spec sets the window
  (Gauntlet Legends: +5V 4.90–5.10, +12V 11.5–12.5).
- **Over-voltage alarm:** above `alarm_hi` (default hi × 1.10) → full-screen red alarm with the
  value and time, latched until ACK. The 20 V event of 09-25 is the test case.
- **Powered vs off** stats in reports (the 09-25 mean was polluted by 62 s at 0 V and ~25 s at 20 V).

---

## 2E · Scanner → roster  *(~1–2 sessions)*

`gatbox-scand`: python3-evdev daemon that **grabs the EY-H2 exclusively**, so scans work
headless in a cabinet and never type into a window. It posts to gatbox-web over loopback, so
there's only one writer.

- [ ] Scan a **cabinet QR** (roster `slug`) → current machine → the next rail session gets
      `# machine=<slug>` and that machine's spec window; dashboard shows the roster card.
- [ ] Scan **`GATBOX:MARK`** → marker in the live session ("it just crashed"). The report
      lists markers and draws them on the plot.
- [ ] Scan **`GATBOX:NEW`** → close the session and start a fresh file.
- [ ] Optional physical MARK button on a free GPIO (not 2/3, 14/15, 26; SPI kept free),
      gpiozero, internal pull-up, 3.3 V-safe.
- [ ] `tools/gatbox-labels`: QR sheet for every roster slug + a MARK / NEW command card.
      Print, laminate, one inside each cabinet door.

**Exit:** scan the cabinet, leave it overnight, scan MARK when staff see a crash, and the
morning report shows the rail trace with the crash marked on it.

---

## 2F · T48: dump → identify → archive  *(~1–2 sessions)*

`gatbox-dump` (CLI first, dashboard flow second):

- [ ] Explicit part selection (names exactly as `minipro -L` lists them). Never auto-guess.
      **27C1000/27C301 non-JEDEC A5/A7 warning** plus confirmation when either is picked.
- [ ] Pin check if supported. Read twice and compare: a mismatch means bad seating, so stop.
      Flag all-FF/all-00.
- [ ] SHA1 + CRC32 → `mame -romident` → MATCH `<set>/<rom>` or NO MATCH.
- [ ] Archive: `/srv/gatbox/roms/<machine-slug>/<label>_<sha1:8>.bin` + JSON sidecar.
      Never overwrite.
- [ ] Dashboard DUMP flow runs as a job under a narrow identity (T48 access + archive write
      only). gatbox-web itself never touches the programmer.
- [ ] Burning stays **CLI-only** in Phase 2. No dashboard write button until Phase 3 arming.

**Exit:** dump a known EPROM from a real board and the dashboard names the ROM; a NO MATCH
becomes Phase 3's "probe the delta with the M2K".

---

## 2G · Stretch: homelab tie-in

- [ ] Log, ROM-archive and captures sync: a systemd timer rsyncs over SSH to a homelab CT/NAS when
      that host is reachable.
- [ ] War-room panel: read-only Proxmox API token (PVEAuditor) via systemd `LoadCredential=`,
      cert pinned → node / CT / ZFS status on the 7".
- (PiKVM crash-cart needs HDMI capture hardware. Not owned, not Phase 2.)

---

## Phase 2 done when

1. A fresh card rebuilds from the repo with one command (T48 included).
2. The Pi boots to the dashboard on the Waveshare 7" with touch working.
3. The dashboard shows the live meter (any mode), sessions, reports and system health.
   Picking a measurement profile checks the dial and sets the report window.
4. Scanned cabinets tag rail sessions; MARK scans show up in reports.
5. A dumped EPROM is identified against MAME and archived under its machine.
6. Offline sessions say `clock=rtc`; RTC charging reads OFF.

## Phase 3 (purchases): what's waiting

- ADALM2000 (M2K): Scopy on the Pi, libm2k backend, "probe the delta" after a NO MATCH
- UUGear MEGA4: uhubctl port map, dashboard ARM/OFF, arming discipline
- Dashboard write actions (T48 burn) gated behind arming

*RTC: done 2026-09-26 with a primary-cell holder; charging off for good. If an official ML2020
ever replaces it, that's the only case for `GATBOX_RTC_CHARGE=ML2020`.*

*Unassigned for later:* MAK Strike V3, GBS-Control + HDMI switch, INA226/board power,
RP2350B bus driver.
