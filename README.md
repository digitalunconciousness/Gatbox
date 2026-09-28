# GATBOX (GDD-GAT/01)

Greybard Diagnostics and Design's arcade-repair deck. A Raspberry Pi 5 that logs a board's supply rail overnight
(UNI-T UT61E → UT-D02 optical cable → PL-2303 → sigrok), serves a phone-friendly web view of the logs, and is
growing into the bench brain: a 7" touch dashboard, T48 EPROM dumps checked against MAME, and a barcode scanner that
ties sessions to machines.

## Install (Raspberry Pi OS Trixie 64-bit Desktop, Pi 5)

```
git clone https://github.com/digitalunconciousness/Gatbox.git ~/gatbox && cd ~/gatbox
sudo bash bootstrap/gatbox-bootstrap.sh
```

Re-run it after every `git pull`. It changes only what differs from the checkout and restarts only the services
whose files changed. `bash bootstrap/gatbox-bootstrap.sh --check` shows what a run would change without root.
`GATBOX_AP_PSK='…'` arms the fallback hotspot (the passphrase is never stored in this repo).

## Layout

| Path | What |
|---|---|
| `backend/` | services: `gatbox-raillog` (the logger, owns the serial port), `gatbox-web` (port 80), `gatbox-rtc-sync`, `gatbox-ap-fallback`, and their units |
| `tools/` | CLIs: `gatbox-status`, `gatbox-rail-report` |
| `bootstrap/` | the installer + `files/` (udev, journald, launcher, pinned minipro udev rules) |
| `data/` | the machine roster and per-machine rail specs |
| `docs/` | Phase 2 spec and plan, build journal entries, history (the original Phase 1 script) |
| `tests/` | run as a normal user; never touch the live logger, port or logs |

`BRINGUP.md` is the Pi's bring-up log and Phase 2 progress checklist; `CLAUDE.md` holds the hard rules.

## Safety rules that shape the code

- The board under test is always external; nothing here powers a board. The optical D02 link is the only
  connection between the cabinet and the Pi.
- The RTC backup cell is **not** rechargeable: charging stays off.
- Pi GPIO is 3.3 V only.
