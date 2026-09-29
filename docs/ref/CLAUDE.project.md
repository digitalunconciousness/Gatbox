# GATBOX — Project Memory

Arcade-repair + homelab cyberdeck (unit **GDD-GAT/01**, Greybard Diagnostics and
Design). A Raspberry Pi 5 drives a touch dashboard, an EPROM programmer, a scope,
and a JAMMA supergun to diagnose arcade boards — and doubles as a Proxmox crash
cart. This file is context for the **software**; full hardware/build detail lives
in `docs/gatbox-master-guide.html`. **Hardware acquisition status lives in
`gatbox-journal.md` — check it before assuming a device is on the bench.**

## Where the build is (2026-09-29)
- **Phase 0 (bench bring-up) and Phase 1 (rail flight recorder) are done** and in use
  since 2026-09-25. The enclosure (the Mule, `gatbox-mule-2.scad` rev d) is not
  finished yet.
- **Phase 2 (current): owned hardware only.** Spec `docs/phase2-handoff.md`, plan
  `docs/gatbox-phase2-plan.md`, progress checklist in the repo's `BRINGUP.md`. Done
  and in use on the Pi:
  - M1 RTC (`clock=rtc` sessions, no charging);
  - M2 repo + bootstrap;
  - M3 the 7" kiosk at boot;
  - M4 data model and JSON API;
  - M5 the dashboard;
  - M6 the scanner;
  - M7 T48 dumps and burns (dashboard dump passed 2026-09-29; burn built the same day, armed by a hold on the 7″).
- **Owner requests (2026-09-29), also done:**
  - add machines on the dashboard (+ export for the maintenance app);
  - SAVE READING (was CAPTURE);
  - each machine's ROM chip checklist from MAME;
  - the MANUALS tab: the floor's manuals on the Pi, spec sheets with CONFIRM and actual values from the field.
- **Next:** M8 (stretch: Sync, the war-room panel) is on hold until the owner is ready to sort out her server setup. **Phase 3:** M2K + MEGA4/uhubctl and any other
  dashboard action that writes to hardware (the T48 burn is the one there is).
- Code: GitHub `digitalunconciousness/Gatbox` (public: no personal info, no roster,
  no ROMs, no manuals). The Pi's own `CLAUDE.md` in the repo is the detailed contract
  for Pi-side work.

## Components
- `bootstrap/gatbox-bootstrap.sh` in the repo: **the way a Pi gets set up.** It
  installs straight from the checkout (a MANIFEST of repo file → installed path),
  writes only what differs, restarts only what changed, `--check` shows what a run
  would change. Re-runnable and offline-safe. Change the repo, then re-run it; never
  hand-configure a Pi. (The old self-contained script with `payload()` heredocs is in
  `docs/history/`.)
- **Rail flight recorder (shipped, Phase 1)**, all installed by the bootstrap:
  - `gatbox-raillog` (systemd `gatbox-raillog.service`, root): sigrok → one CSV per
    contiguous session in `/var/log/gatbox/rail_YYYYmmdd_HHMMSS.csv`, current session
    path in `/run/gatbox/current`, ACT LED blinks ~1 Hz per sample.
    CSV: `iso_time,epoch,value,unit,flags,uptime_s` plus a `# clock=ntp|unverified`
    line. `uptime_s` is monotonic: use it for durations.
  - `gatbox-status` (`-f` = live follow) · `gatbox-rail-report` (Python; excursions,
    open-input, gaps, clock steps, house-style PNG).
- `gatbox-web` (Python stdlib, one server on port 80, sandboxed): the phone view,
  the JSON API + live stream under `/api/`, and the dashboard at `/dash/`
  (`web/dash/`: vanilla HTML/CSS/JS, no framework, synthwave tokens). Tabs:
  - METER: live reading, profiles, the over-voltage alarm, marks, saved readings;
  - SESSIONS: reports and zoomable charts;
  - MACHINE: the roster card, add machine, ROM chips from MAME;
  - MANUALS: the viewer, the spec sheet;
  - SYSTEM, DEVICES;
  - DUMP: T48 reads, and BURN (a blank chip, armed by a 3 s hold on the Pi's own screen, verified).
- `gatbox-scand` (the EY-H2 scanner: roster slug → machine, `GATBOX:MARK`,
  `GATBOX:NEW`), `gatbox-dump` (T48 read twice → MAME romident → archive in
  `/srv/gatbox/roms`; `--burn`: blank check → write + verify → 2 read-backs → `burns.jsonl`), `gatbox-mame-roms` (each machine's chips from `mame -listxml`),
  `gatbox-manuals` (the floor's documents into `/srv/gatbox/manuals`).
- `firmware/` — two targets:
  - **Stick injector** — Pico (RP2040/RP2350) USB-host → JAMMA control injector.
    Arduino-Pico core + Adafruit TinyUSB (host mode). Ref: `gatbox_stick_injector.cpp`.
  - **Bus driver mainboard** (planned) — RP2350B module (Olimex PICO2-XL or
    Waveshare Core2350B; **A4-stepping silicon only** — earlier steppings have the
    E9 GPIO pull-down erratum). Drives 74LVC4245A transceivers for C.A.T.-style
    active bus tests. Per-CPU pods are **passive** pin-remap cards; all logic lives
    here. V1 scope: DIP-40 **6502 and Z80** only. 68000 = v2 (FPGA).
## Tech stack & conventions
- Dashboard: plain HTML/CSS/JS. NO build step, NO framework, NO localStorage /
  sessionStorage — state lives in JS memory.
- Firmware: Arduino-Pico (earlephilhower) + Adafruit TinyUSB host. C++.
- Backend: the existing `gatbox-web`, grown (Python stdlib, systemd, DynamicUser).
  FastAPI only if a concrete wall appears, and ask the owner first. Call the
  CLIs/libraries below — don't reimplement.
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
**Owned today:** Pi 5, T48 (firmware 01.1.32, minipro 0.7.4), UT61E + UT-D02 + PL-2303,
Eyoyo EY-H2 barcode scanner, Waveshare 7inch HDMI LCD (C), RTC coin cell
(non-rechargeable: never enable charging). Everything else below is **planned, not
purchased**. Don't write code that assumes it's attached, and don't tell the owner to
test against it.
- EPROM: XGecu T48 via `minipro`, exact part names only (never auto-detected). The
  dashboard reads, and burns a blank chip: the only hardware write it has, armed at the Pi
  (a 3 s hold on the 7″ for now, a physical ARM button later), blank-checked, verified.
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
  - One CSV per session and dial mode; header lines `# clock=`, `# mode=`,
    `# profile=`, `# window=` (with its source: profile, user, the machine's spec, or
    the machine's actual), `# alarm_hi=`, `# machine=`, and `# mark=` lines. The
    profile, machine and alarm switch are gatbox-web's state in `/var/lib/gatbox-web`.
  - A machine's window: its confirmed manual limit (the dashboard's spec sheet, page
    cited) or its own actual window from the field; machine-spec numbers only ever
    come from a manual, and nothing applies until the owner confirms it.
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
