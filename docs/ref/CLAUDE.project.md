# GATBOX — Project Memory

Arcade-repair + homelab cyberdeck (unit **GDD-GAT/01**, Greybard Diagnostics and
Design). A Raspberry Pi 5 drives a touch dashboard, an EPROM programmer, a scope,
and a JAMMA supergun to diagnose arcade boards — and doubles as a Proxmox crash
cart. This file is context for the **software**; full hardware/build detail lives
in `docs/gatbox-master-guide.html`. **Hardware acquisition status lives in
`gatbox-journal.md` — check it before assuming a device is on the bench.**

## Where the build is (2026-09-23)
- **Phase 0 (bench bring-up) and Phase 1 (rail flight recorder) are done.** The Pi
  (Trixie 64-bit **Desktop**) is set up by `gatbox-bootstrap.sh` and logs the UT61E
  headless at boot. The enclosure (the Mule, `gatbox-mule-2.scad` rev d) is not
  finished yet.
- **Phase 2 (current): owned hardware only.** Plan: `gatbox-phase2-plan.md`: repo +
  bootstrap modules, Waveshare 7" kiosk at boot, read-only FastAPI backend, dashboard v1
  with the DMM measurement picker,
  scanner → roster, T48 dump → identify → archive. **Phase 3:** M2K + MEGA4/uhubctl
  and any dashboard action that writes to hardware.

## Components
- `pi/gatbox-bootstrap.sh` (new dir; project copy at `claude/gatbox-bootstrap.sh`) —
  **the way a Pi gets set up.** Fresh Trixie → rail recorder:
  packages, ModemManager/brltty removal, groups, power fix, `/dev/gatbox-dmm` udev
  symlink, logger service, tools, desktop launcher, optional fallback hotspot.
  Re-runnable; `--extract DIR` writes the payload for review. Change the script
  (its `payload()` heredocs) rather than hand-configuring a Pi.
- **Rail flight recorder (shipped, Phase 1)**, all installed by the bootstrap:
  - `gatbox-raillog` (systemd `gatbox-raillog.service`, root): sigrok → one CSV per
    contiguous session in `/var/log/gatbox/rail_YYYYmmdd_HHMMSS.csv`, current session
    path in `/run/gatbox/current`, ACT LED blinks ~1 Hz per sample.
    CSV: `iso_time,epoch,value,unit,flags,uptime_s` plus a `# clock=ntp|unverified`
    line. `uptime_s` is monotonic: use it for durations.
  - `gatbox-status` (`-f` = live follow) · `gatbox-rail-report` (Python; excursions,
    open-input, gaps, clock steps, house-style PNG).
- `dashboard/` — cyberpunk-terminal web dashboard, served by the Pi. Vanilla
  HTML/CSS/JS, no framework. Reference mockup: `gatbox-terminal.html`.
- `firmware/` — two targets:
  - **Stick injector** — Pico (RP2040/RP2350) USB-host → JAMMA control injector.
    Arduino-Pico core + Adafruit TinyUSB (host mode). Ref: `gatbox_stick_injector.cpp`.
  - **Bus driver mainboard** (planned) — RP2350B module (Olimex PICO2-XL or
    Waveshare Core2350B; **A4-stepping silicon only** — earlier steppings have the
    E9 GPIO pull-down erratum). Drives 74LVC4245A transceivers for C.A.T.-style
    active bus tests. Per-CPU pods are **passive** pin-remap cards; all logic lives
    here. V1 scope: DIP-40 **6502 and Z80** only. 68000 = v2 (FPGA).
- `backend/` — (to build) Pi service the dashboard talks to; wraps the hardware
  tools below. Prefer Python (FastAPI) under systemd unless told otherwise.

## Tech stack & conventions
- Dashboard: plain HTML/CSS/JS. NO build step, NO framework, NO localStorage /
  sessionStorage — state lives in JS memory. Keep single-file where practical.
- Firmware: Arduino-Pico (earlephilhower) + Adafruit TinyUSB host. C++.
- Backend: Python 3 + FastAPI. Call the CLIs/libraries below — don't reimplement.
- Pi shell tooling: bash, `set -u`, no per-sample forks in hot loops; shellcheck-clean.
- Python on the Pi: apt packages or a venv. Trixie blocks system-wide pip (PEP 668).
- Indent 2-space JS/HTML, 4-space Python. Comment the hardware-facing code.

## Design tokens (match these in any UI)
- Aesthetic: synthwave cyberpunk — neon on a deep violet gradient base (not
  flat near-black), scanlines, mono type. Glow via `text-shadow`/`box-shadow`
  in the accent colors below, used with restraint (badges, active states,
  headline edges) rather than blanket-applied.
- Palette: bg `#150a28`, bg2 (gradient top / recessed surfaces) `#1f0f3d`,
  panel `#1f1240`, panel border `#3d1e6b`, magenta/hot-pink (primary accent)
  `#ff2e9f`, cyan (secondary accent) `#7dfaff`, lime/mint (OK/pass) `#5ef2b0`,
  red/coral (trip/danger) `#ff4d6d`, amber (warn) `#ffb74d`, text `#ece3ff`,
  dim `#9080b0`.
- Body background is a gradient (`linear-gradient(180deg, bg2, bg)`), not a
  flat fill — that's the synthwave tell vs. the older flat-black direction.
- Fonts: display **Chakra Petch**, mono/body **Share Tech Mono** (unchanged).

## Hardware the software drives
**Owned today:** Pi 5, T48, UT61E + UT-D02 + PL-2303, barcode scanner, Waveshare
7inch HDMI LCD (C). Everything else below is **planned, not purchased**. Don't
write code that assumes it's attached, and don't tell the owner to test against it.
- EPROM: XGecu T48 via `minipro` (e.g. `minipro -p <chip> -r out.bin`).
- Scope / logic / AWG: ADALM2000 (M2K) via libm2k / Scopy. *(planned)*
- DMM: UNI-T UT61E over the **UT-D02 serial optical cable + PL-2303 adapter** —
  sigrok driver **`uni-t-ut61e-ser:conn=/dev/gatbox-dmm`** (udev symlink to the
  ttyUSB node; 19200 7O1). The plain `uni-t-ut61e` HID driver is for the UT-D04
  cable — wrong hardware. **`gatbox-raillog` owns this port**: the dashboard/backend
  reads its CSV (`/run/gatbox/current`, mode in `/run/gatbox/mode`) and never opens the
  serial port concurrently.
  - The link is **one-way**. The meter's dial sets the function; software can only
    detect it (mode = base unit + AC/DC/DIODE) and prompt "SET DIAL TO …".
  - CSV `value,unit` are exactly as sigrok printed them, **SI prefix included**
    (`812.0,mV`, `4.70,kΩ`). Normalize to base units before any math.
  - One CSV per mode; `# mode=` header line. Profiles (planned, 2C) live in
    `/var/lib/gatbox/profile.json` and will add `# profile=` + window to the header.
- Instrument power: **UUGear MEGA4** hub, per-port via `uhubctl`. Dashboard
  arm/off toggles map to `uhubctl -a on|off -l <hub> -p <port>`. *(planned)*
- Board current + protection: INA226 over I2C; trip a relay (GPIO) on overcurrent. *(planned)*
- Thermal: **InfiRay P2 Pro** (256×192, USB **UVC camera** → V4L2/OpenCV video
  pipeline). *Eventual choice — not yet purchased.* Do NOT build against the old
  MLX90640/I2C plan; that part is superseded.
- Video: GBS-Control (board→LCD upscale; downscale→CRT for 15kHz test patterns). *(planned)*
- Supergun: ArcadeForge MAK Strike V3 (JAMMA). Board under test is ALWAYS external. *(planned)*

## Hard rules / gotchas
- Board under test is external, always. BOARD POWER is a guarded rail — never
  auto-energize it.
- **All Raspberry Pi GPIO are 3.3V-only. No Pi model — Pi 5 included — is
  5V-tolerant.** Anything at 5V (ignition-key aux, JAMMA-side signals) goes
  through a divider / optocoupler / level shifter first.
- Injector safety: JAMMA control lines idle at ~5V; the Pico is 3.3V and NOT
  5V-tolerant. Drive JAMMA contacts through a ULN2803 buffer
  (GPIO HIGH = press = sink line to GND). NEVER wire a GPIO straight to JAMMA.
- Injector reads HID gamepads in **DInput** mode. XInput (Xbox / 8BitDo X-mode)
  needs a separate host driver — prefer DInput.
- Main display is the **Waveshare 7inch HDMI LCD (C)**: IPS **capacitive** touch,
  1024×600, USB HID touch (owned). HDMI, not DSI. DSI breaks the GBS-Control video path. Still design
  big-tap UI for greasy fingers; capacitive won't register most gloves.
- Pi 5 display modes are **KMS**: config.txt `hdmi_*` settings are ignored. Use
  EDID or `video=HDMI-A-1:...` in `/boot/firmware/cmdline.txt`.
- The Pi **keeps the desktop**. Don't switch it to console boot.
- The Pi's ACT LED belongs to `gatbox-raillog` while it runs.
- Signature workflow: dump ROM → verify vs MAME hash → probe the delta with the M2K.
- QR codes encode the roster **`slug`** (stable, rename-proof) and hand board IDs
  to the owner's existing arcade maintenance app — don't build a competing database.
  Data of record: `gatbox-barcade-roster.json`.
- Multi-platform: Mode A = JAMMA bay (JAMMA / CPS+kick / Neo Geo MVS);
  Mode B = universal patch panel (Atari / Sega Sys32 / pre-JAMMA). Vector games
  (Asteroids, Tempest) need an external vector monitor — out of scope for the LCD.

## Homelab / crash-cart mode
The deck doubles as a Proxmox field-service rig: PiKVM (HDMI+USB capture),
e-ink cluster status, a "war-room" LCD mode (nodes/CTs/ZFS via the Proxmox API),
alert annunciator (ntfy/Alertmanager), and a portable network/Kali diagnostic.

## Deep reference
Full spec, schematics, BOM, phases, fabrication: `docs/gatbox-master-guide.html`
(Rev C pending — see the punch list in `gatbox-journal.md`). Pi bring-up and the
rail logger: `gatbox-pi5-starter.md`. Consumer-sale constraints:
`gatbox-manufacturing-plan.md`. Keep settled decisions there; this file is the
behavioral contract for the code.
