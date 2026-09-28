# GATBOX (GDD-GAT/01) — context for Claude sessions

Portable arcade-board diagnostic box. This Raspberry Pi 5 (4 GB, Raspberry Pi OS Trixie 64-bit **Desktop**,
hostname `gatbox`, user `<user>`) is a self-starting **rail logger** that gets left inside an arcade cabinet
overnight. There's no enclosure yet, so the Pi sits bare in the cabinet.

## Signal chain
UNI-T UT61E (original ES51922) → UT-D02 optical cable → CableCreation PL-2303 USB-RS232 → `/dev/gatbox-dmm`
→ sigrok driver `uni-t-ut61e-ser`. Protocol: **19200 7O1**. Serial group: **dialout**.
If readings come back as garbage, suspect the adapter chip or parity before the meter.

## Installed by gatbox-bootstrap.sh (patched copy: ~/gatbox/gatbox-bootstrap.sh; see BRINGUP.md)
- `gatbox-raillog.service` → `/usr/local/bin/gatbox-raillog`. It writes one CSV per contiguous session:
  `/var/log/gatbox/rail_YYYYmmdd_HHMMSS.csv`. The ACT LED blinks while it's logging.
- `gatbox-status [-f]`, `gatbox-rail-report [csv] [--lo --hi --plot]` (default window 4.75–5.25 V)
- udev `/etc/udev/rules.d/99-gatbox-dmm.rules`, persistent journal, menu entry "GATBOX Rail Monitor"
- EEPROM `PSU_MAX_CURRENT=5000`, config.txt `usb_max_current_enable=1`
- `gatbox-web` + `gatbox-web.service`: phone web view on port 80 (live reading + meter mode, sessions, report/plot,
  PDF, CSV, Start/Stop). Fonts in `/usr/local/share/gatbox-web/fonts`. Bootstrap payload since 2026-09-24.
- Fallback hotspot NM profile `gatbox-ap`, **SSID GATBOX** (it was WalkinAround 09-23..09-24, renamed back to match
  the script and the docs). It comes up only if no known network appears
  ~60 s after boot. Pi = 10.42.0.1. There's no barcade client Wi-Fi profile yet (the user doesn't know those credentials).
- Pristine script copy: `~/gatbox/gatbox-bootstrap.orig.sh`; extracted payload: `~/gatbox/payload/`

## Rules
- Keep the desktop. Don't change boot mode, display/HDMI config, or bootloader settings beyond what the script does.
- On failure: find the cause first (journalctl, dmesg, lsusb) and explain it before changing system config.
  Script fixes go in a copy in ~/gatbox, with the diff shown and the reason given.
- No system-wide pip (Trixie blocks it): use apt or a venv.
- Do **not** set GATBOX_RTC_BATTERY=1. There's no ML2020 RTC battery yet.
- **Never suggest wiring anything from the cabinet to the Pi.** The D02 optical link is the only connection, on purpose.
- Keep narration short; show command output when it matters.

## Project docs (not on the Pi)
The build journal (`gatbox-journal.md`, the record of which hardware is owned vs only planned), `gatbox-pi5-starter.md`
and the project `CLAUDE.md` (design tokens, hard rules) live in the claude.ai project. Ask the owner to upload them when
needed. Pi-side script changes get folded back there by uploading `~/gatbox/gatbox-bootstrap.sh`.

## Status
Bring-up done 2026-09-23: bootstrap run + bench test (BRINGUP.md "Bring-up 1/2"; the bench test needed three script
fixes). In the project's terms Phase 0 and Phase 1 (rail flight recorder) are done, and **project Phase 2 (software on
owned hardware: minipro rebuild, display, gpiozero, dashboard) hasn't started**. 2026-09-24: mode-aware report + web
view, web view restyled and added to the bootstrap. For any re-run, use `~/gatbox/gatbox-bootstrap.sh` (the patched
copy), not the Downloads/orig copy.
Next: the first overnight cabinet run.
