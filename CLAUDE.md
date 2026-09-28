# GATBOX (GDD-GAT/01) — context for Claude sessions

Portable arcade-board diagnostic box (Greybard Diagnostics and Design). This Raspberry Pi 5 (4 GB + Active Cooler,
Raspberry Pi OS Trixie 64-bit **Desktop**, labwc, hostname `gatbox`, user `<user>`) is a self-starting **rail
logger** that gets left inside an arcade cabinet overnight, and is becoming the bench brain: 7" touch dashboard,
T48 EPROM dumps, scanner → roster. No enclosure yet, so the Pi sits bare in the cabinet.

**Phase 2 is in progress.** The spec is `docs/phase2-handoff.md` (the handoff's PROMPT.md), with the plan in
`docs/gatbox-phase2-plan.md`. Where they differ, the spec wins. **Resuming? Re-read the spec and
the progress checklist in BRINGUP.md, then carry on from the first unchecked milestone.**

## Signal chain
UNI-T UT61E (original ES51922) → UT-D02 optical cable → CableCreation PL-2303 USB-RS232 → `/dev/gatbox-dmm`
→ sigrok driver `uni-t-ut61e-ser`. Protocol: **19200 7O1**. Serial group: **dialout**.
If readings come back as garbage, suspect the adapter chip or parity before the meter.
- The link is **one-way**: the meter's dial sets the function; software can only detect it and prompt "SET DIAL TO …".
- CSV `value,unit` are exactly as sigrok printed them, **SI prefix included** (`812.0,mV`, `4.70,kΩ`). Normalize to
  base units before any math. sigrok writes Ω as **U+2126 OHM SIGN**; OL arrives as `inf` with a `T` prefix;
  continuity rows have no unit (a flag can land in the unit column).
- A power cut mid-session can leave a run of NUL bytes at the end of a CSV (ext4). Both readers skip it; keep it that way.

## Installed by `bootstrap/gatbox-bootstrap.sh`, straight from this checkout (see BRINGUP.md)
Layout: `backend/` services + units, `tools/` CLIs, `bootstrap/` installer + `files/`, `data/`, `docs/`, `tests/`.
The bootstrap's MANIFEST maps repo files to installed paths. `--check` (no root) shows what a run would change.
- `gatbox-raillog.service` (root) → `/usr/local/bin/gatbox-raillog`. It writes one CSV per contiguous session:
  `/var/log/gatbox/rail_YYYYmmdd_HHMMSS.csv` (`iso_time,epoch,value,unit,flags,uptime_s` + `# clock=` line;
  `uptime_s` is monotonic, use it for durations), and `/run/gatbox/current`. The ACT LED blinks while it's logging.
  Start/Stop flag files in `/var/lib/gatbox-web`.
  **Not yet (as of 2026-09-28, despite the handoff/project docs):** no `# mode=` header, no `/run/gatbox/mode`, and
  no new file on a dial change. Those arrive with M4b.
- `gatbox-status [-f]`, `gatbox-rail-report [csv] [--lo --hi --plot]` (mode-aware; default window 4.75–5.25 V)
- `gatbox-web` + `gatbox-web.service`: phone web view on port 80 (live reading + meter mode, sessions, report/plot,
  PDF, CSV, Start/Stop). Fonts in `/usr/local/share/gatbox-web/fonts`. Stdlib, DynamicUser.
- udev `/etc/udev/rules.d/99-gatbox-dmm.rules`, persistent journal, menu entry "GATBOX Rail Monitor"
- EEPROM `PSU_MAX_CURRENT=5000`, config.txt `usb_max_current_enable=1`
- Fallback hotspot NM profile `gatbox-ap`, **SSID GATBOX**, up only if no known network appears ~60 s after boot.
  Pi = 10.42.0.1. No barcade client Wi-Fi profile yet. Home Wi-Fi (<home-wifi>) is static <home-lan-ip>
  (set by hand 09-24; M2 folds it into the bootstrap or documents it as site config); eth0 is DHCP.
- minipro 0.7.4 (built from the pinned upstream tag into /usr/local; T48 via udev → plugdev) and mame 0.276 (apt,
  for `mame -romident` only).
- The original Phase 1 script and its payload are in `docs/history/`.
- Installs need `sudo`, which asks for a password: the owner runs the install command herself.

## Hardware
**Owned:** Pi 5 4GB + Active Cooler · UT61E + UT-D02 + PL-2303 · XGecu T48 · Eyoyo EY-H2 USB scanner (HID keyboard
wedge) · Waveshare 7inch HDMI LCD (C) (1024×600 IPS, capacitive USB-HID touch) · coin-cell holder on the RTC
connector J5 with a **non-rechargeable** cell (fitted 2026-09-26/27, BATT_V ~3.13 V).
**Not owned: don't write code that assumes these, don't ask the owner to test with them:** ADALM2000, UUGear MEGA4 /
uhubctl, INA226, relays, MAK Strike, GBS-Control, RP2350B bus driver, InfiRay P2 Pro. The build journal
(`gatbox-journal.md`) is the record of what's owned.

## Hard rules
1. **Never enable RTC trickle charging.** No `dtparam=rtc_bbat_vchg`, never `GATBOX_RTC_BATTERY=1`. Only a real
   ML2020 could ever justify `GATBOX_RTC_CHARGE=ML2020`.
2. **The logger owns the serial port.** Nothing else opens `/dev/gatbox-dmm`; everything reads its CSVs and `/run/gatbox/*`.
3. **The logger always works.** Never end a turn with `gatbox-raillog` broken or half-installed. Unfinished changes
   stay uninstalled.
4. **One install path.** Every system change goes into the repo/bootstrap and is applied by re-running it. Nothing is
   hand-configured. The bootstrap stays idempotent, and an offline re-run succeeds when everything is installed.
5. **Show before you change system config** (`/boot/firmware/*`, udev, systemd units, labwc config, `/etc`): show
   The owner the diff and why, and wait for her OK. Repo-only changes don't need approval.
6. **No hardware writes from the dashboard.** Phase 2 does Pi-side state and T48 **reads** only. No part auto-detect.
7. **GPIO is 3.3 V only, Pi 5 included.** gpiozero (+lgpio), never RPi.GPIO. GPIO3 and GPIO26 reserved; keep I²C
   (2/3), UART (14/15) and SPI free. Anything at 5 V goes through a divider / opto / level shifter.
8. **The board under test is always external.** Nothing here energizes a board.
9. **apt before pip.** Trixie blocks system pip. Ask before adding a venv.
10. **Dashboard stack:** plain HTML/CSS/JS, no framework, no build step, no CDN (fonts come from the Pi), no
    localStorage/sessionStorage/IndexedDB/cookies for state.
11. **Design tokens** (below), not the mockup's flat-black palette.
12. **Don't guess hardware facts.** Read USB IDs, minipro part names (`minipro -L`), mode strings and free GPIOs off
    this Pi, or ask. Machine-spec numbers come only from a manual the owner cites.
13. **Secrets never go in git:** AP PSK, Wi-Fi credentials, SSH keys, API tokens.
14. **Backward-compatible, always.** Every existing CSV keeps loading (the 09-25 fixture
    `rail_20260925_021402.csv` is never modified), and every existing gatbox-web URL keeps working: `/`, `/s/…`,
    `/png/…`, `/pdf/…`, `/csv/…`, `/font/…`, `POST /control`.
- Keep the desktop. Don't change boot mode or bootloader settings beyond what the bootstrap does.
- On failure: find the cause first (journalctl, dmesg, lsusb) and explain it before changing system config.
- **Never suggest wiring anything from the cabinet to the Pi.** The D02 optical link is the only connection.
- QR codes encode the roster `slug`; the roster (`gatbox-barcade-roster.json`) is data of record shared with the owner's
  maintenance app: never change its schema. Per-machine rail limits live in `gatbox-machine-specs.json`.
- Keep narration short; show command output when it matters.

## Design tokens (any UI)
Synthwave: neon on a deep violet gradient, scanlines, mono type, glow only on badges / active states / headline edges.
bg `#150a28`, bg2 `#1f0f3d`, panel `#1f1240`, border `#3d1e6b`, magenta (primary) `#ff2e9f`, cyan (secondary)
`#7dfaff`, lime (OK) `#5ef2b0`, red (trip/danger) `#ff4d6d`, amber (warn) `#ffb74d`, text `#ece3ff`, dim `#9080b0`.
Body `linear-gradient(180deg, bg2, bg)`. Fonts: Chakra Petch (display), Share Tech Mono (body), served from the Pi.

## Conventions
- Backend = the existing **gatbox-web, grown** (stdlib, one server on :80, DynamicUser). FastAPI only if a concrete
  wall appears, and ask the owner first.
- Pi shell tooling: bash, `set -u`, no per-sample forks in hot loops, shellcheck-clean. Python: 4-space indent;
  JS/HTML: 2-space. Comment the hardware-facing code.
- Git: repo in `~/gatbox`, branch `main`, remote GitHub `digitalunconciousness/Gatbox` (**public**: no secrets, no
  personal details); `handoff/` is input and ignored.
- Per milestone: plan → code → install via bootstrap → tests → BRINGUP.md → journal
  (`docs/journal/journal-entry-YYYY-MM-DD.md`, the owner's format) → commit → short summary.

## Project docs (not on the Pi)
The build journal, `gatbox-pi5-starter.md`, the master guide and the project `CLAUDE.md` live in the claude.ai
project (a copy of the project CLAUDE.md is in `handoff/ref/`). Pi-side changes get folded back there by uploading
the bootstrap, CLAUDE.md, the plan/progress and journal entries.

## Status
Bring-up done 2026-09-23 (BRINGUP.md "Bring-up 1/2"). Phase 0 and Phase 1 (rail flight recorder) done and in use:
first cabinet log 2026-09-25 (Gauntlet Legends). **Phase 2 started 2026-09-28**: progress checklist in BRINGUP.md.
