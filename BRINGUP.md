# GATBOX (GDD-GAT/01) bring-up log

Pi 5 Model B Rev 1.1, 4 GB · Raspberry Pi OS Trixie 64-bit Desktop · hostname `gatbox` · user `<user>`

"Bring-up 1/2" below are steps on this Pi, not the project's phases. In the build journal, Phase 1 is the rail
flight recorder (done) and Phase 2 is the software on owned hardware (minipro, display, gpiozero, dashboard;
not started). These headings were called "Phase 1/2" until 2026-09-24.

## Phase 2 — progress (spec: `handoff/PROMPT.md`, later `docs/phase2-handoff.md`)

Resume from the first unchecked box. ⏸ = waiting on the owner (hands on hardware).

- [x] **M0 orient** (2026-09-28): git repo + baseline `83ba066`; installed files == bootstrap payload (nothing to
      reconcile); CLAUDE.md merged with the project CLAUDE.md + the handoff's hard rules.
- [ ] **M1 RTC**: [x] read-only checks (2026-09-28: charging OFF, BATT_V 3.13 V) [x] bootstrap guard
      [x] gatbox-rtc-sync timer [x] logger `clock=rtc` [x] status line [x] web badge [x] tests (36 pass)
      [ ] install (waiting on the owner's OK: new units) [ ] ⏸ unplug + offline boot test
- [ ] **M2 2A foundation**: repo layout; bootstrap shape; Forgejo remote; minipro; MAME CLI; ⏸ T48 re-verify
- [ ] **M3 2B face**: ⏸ panel connected; mode; touch; power; kiosk; blanking; ⏸ kiosk on/off reboots
- [ ] **M4 2C backend**: 4a replay harness; 4b data model + logger header/marks/splits; 4c report; 4d JSON API
- [ ] **M5 2D dashboard** at `/dash/`; screenshot tests; ⏸ on the real panel
- [ ] **M6 2E scanner**: gatbox-scand; labels; ⏸ scan slug / MARK / NEW
- [ ] **M7 2F T48 dump**: gatbox-dump; dashboard flow; ⏸ real board dump
- [ ] **M8 2G stretch**: ask first

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
