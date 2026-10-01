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

## Developing on a PC

Everything except the hardware-facing tests runs off the Pi. After cloning, **enable the hooks and the UTC commit
alias** — the repo is public, and the pre-push hook is the only thing standing between a careless commit and a
permanent leak:

```
git config core.hooksPath .githooks        # enable .githooks/pre-push
git config alias.c '!TZ=UTC git commit'    # git c -m "…"  (a local timestamp leaks the timezone)
cp .githooks/patterns.local.example .githooks/patterns.local
$EDITOR .githooks/patterns.local           # your own values; git-ignored, never committed
```

`.githooks/pre-push` refuses the roster and list files by name, `*.local` / `*.local.md`, PDFs, `.bin` dumps,
key-shaped lines, and anything matching `patterns.local`. Without that file the generic checks still run and the
hook says so. It never prints a match, only which pattern tripped.

The tests want **Python 3.13**, to match Trixie:

```
uv venv --python 3.13 ~/.venvs/gatbox && export PATH="$HOME/.venvs/gatbox/bin:$PATH"
for t in tests/test-*.sh; do echo "== $t"; bash "$t"; done
```

Eleven of the seventeen test files need nothing but Python. The rest need, and skip without:

| Needs | Files |
|---|---|
| `matplotlib` (Pi: `python3-matplotlib`) | `test-dash.sh`, `test-manuals.sh`, `test-web-api.sh` (plots, PDFs) |
| `chromium` | `test-dash.sh` (via `tests/cdp.py`, over `--remote-debugging-pipe`). Set `GATBOX_CHROME=/path/to/binary` if yours is not called `chromium` |
| `qrcode` (Pi: `python3-qrcode`) | the label sheet in `test-roster.sh` |
| the `hostname` binary (Arch: `inetutils`) | `test-scan.sh`, `test-kiosk.sh`, `test-dump.sh` |
| a T48 plugged in | `test-dump.sh`'s blank-check and burn paths |

Nothing in `tests/` touches the live logger, its serial port, port 80 or `/var/log/gatbox`.

## Safety rules that shape the code

- The board under test is always external; nothing here powers a board. The optical D02 link is the only
  connection between the cabinet and the Pi.
- The RTC backup cell is **not** rechargeable: charging stays off.
- Pi GPIO is 3.3 V only.
