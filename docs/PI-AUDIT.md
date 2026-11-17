# GATBOX hardware audit: a prompt for the agent on the Pi

The owner asked for this on 2026-11-17, as the capstone of phase 2: one pass on the real hardware over every function the repo
has coded, recorded as it is, with nothing changed that she hasn't said yes to. Give the agent on the Pi this whole file.

---

You are auditing the GATBOX Raspberry Pi 5 on the owner's bench, on the real hardware. Your job is to find out what works, write
it down, and change nothing unless the owner says yes to that one thing. Read `CLAUDE.md` and `docs/agents/RULES.md` in
`~/gatbox` first; their hard rules win over anything here.

## Ground rules

1. **Read-only first.** For each area, run the read-only checks before anything that could change state.
2. **No hardware writes** (a WRITE, RESTORE or FORMAT of a drive, a T48 burn, HOLES on an image, any `dd`, `mkfs`, `minipro -w`)
   unless the owner is at the bench and says yes to that test, by name. Use the dashboard's armed flow or the CLI's typed
   arm, never a shortcut. She picks the drive, the chip and the image.
3. **No installs, config edits, reboots or service restarts** without her yes. If a fix is needed, write it down and stop.
4. **Never open `/dev/gatbox-dmm`.** The logger owns it. Read its CSVs and `/run/gatbox/*` instead.
5. **Never enable RTC trickle charging.** Don't touch GPIO3 or GPIO26.
6. **No secrets in what you write:** no Wi-Fi passwords, tokens, SSH keys. Write any LAN address as `<lan>`, any login name as
   `<user>`. The results file stays on the Pi; it is never committed.
7. If something looks wrong or dangerous, stop that test, write down what you saw, and go on to the next area.

## The results file

`~/gatbox-audit-YYYY-MM-DD.md` on the Pi. One line per item:

`AREA | ITEM | PASS / FAIL / NOT RUN / BLOCKED | evidence (trimmed output) | notes`

NOT RUN means you chose not to, or the owner said no. BLOCKED means something else stopped it (a missing part, a failed
precondition). Say which.

## 0. What is installed

```bash
cd ~/gatbox && git log -1 --oneline && git status --short | head
sudo bash bootstrap/gatbox-bootstrap.sh --check
```

`--check` needs no root to run, but under `sudo` it reads root-only files too. It lists what a bootstrap run would change; an
empty list means the Pi matches the checkout. Record every line it prints. Don't run the bootstrap itself.

## 1. Services and timers

```bash
systemctl list-units 'gatbox*' --all --no-pager
systemctl list-timers 'gatbox*' --all --no-pager
systemctl --failed --no-pager
journalctl -p warning -b --no-pager | tail -n 200
systemctl show gatbox-disk.service -p TimeoutStartSec
```

For every `gatbox-*` unit: its state, and whether that state is right (a path or socket unit waits; a oneshot is inactive
between jobs). The disk job's time limit should be 12h (the repo's value since 2026-11-16); record what the Pi has.

## 2. The rail logger

- `gatbox-status`: the reading, the dial mode, the port. It re-runs itself under `sudo`, so the owner runs it if the agent
  has no password.
- The newest CSV in `/var/log/gatbox/`: `tail -n 20` it, then `gatbox-rail-report <that csv> --no-plot`.
- The ACT LED blinks while it logs (the owner looks).
- Dial modes: the owner turns the UT61E's dial through DC V, AC V, Ω, continuity and diode. For each, record what `gatbox-status`
  shows and the CSV's `# mode=` line in the new file. **Diode has never been seen on real data** (continuity has: three CSVs from 2026-09-27). A mode that
  logs nothing, or logs a flag in the unit column, is a finding.
- A CSV that ends in NUL bytes (a past power cut), if one exists: `gatbox-rail-report` reads it without an error. Don't cut power
  to make one.

## 3. gatbox-web on port 80

From the Pi:
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
curl -s http://127.0.0.1/api/system | head -c 400; echo
curl -sI http://127.0.0.1/ | grep -iE 'x-frame|nosniff|referrer'
```

From the owner's phone on the same network (she does this): the phone view's live reading, sessions, a report and its plot, the
PDF, the CSV, and Start/Stop.

**The write gate (from 2026-11-17's security PR, if it is installed):** from the phone, every change except the cabinet's own
must be refused. Ask the owner to run, from a laptop on the same network:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE http://<lan>/api/machine
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{}' http://<lan>/api/roster
```
Both should print 403. Before that PR is installed they don't; record what they print either way, and that the machine on the 7"
is still the one it was (the DELETE clears it when the gate isn't there: put it back on the 7" if so).

## 4. The 7" dashboard

- Every tab opens: METER, SESSIONS, MACHINE, DUMP, LIBRARY, DISK, SYSTEM, DEVICES.
- Every button you can reach without changing anything responds to a touch. Write down any control that looks like plain text,
  any text that looks like a button, and any screen where you couldn't tell what to press.
- **SYSTEM → TOUCH TEST** has never been ticked in BRINGUP. The owner runs it; record the result.
- The holds (HOLD 3 S TO SHUT DOWN, HOLD TO EXIT KIOSK): start a hold and let go before it finishes. Record that letting go
  cancels it. **Don't let either one finish.**

## 5. The scanner (EY-H2)

- `systemctl is-active gatbox-scand`.
- The owner scans one roster label: that machine opens on the 7".
- A `GATBOX:MARK` label, if she has one: a mark is added. A scan changes state, so she says which labels to scan.

## 6. USB devices

```bash
lsusb
lsusb -t
dmesg | grep -iE 'usb|error -71' | tail -n 60
```

- DEVICES tab: the DMM, the T48, the scanner and the touch panel, each with the right state.
- **The Hauppauge device at 3-1.2 is still unexplained** (BRINGUP's open items: `can't set config #1, error -71`). Find its
  bus and device in `lsusb`, then `lsusb -v -s BUS:DEV 2>/dev/null | head -n 40`. Record what it is. Don't unplug anything.

## 7. The T48: dumps

- DUMP tab with the T48 plugged in (the owner plugs it in). The chip and the part name are hers: `minipro -q T48 -l | grep -i <part>`
  confirms the name, never a guess.
- A dump: read twice, MATCH or the difference, `mame -romident`'s answer, and the file in `/srv/gatbox/roms`.
- **BURN passed once** (AM27C020, 2026-09-29). Don't burn in this audit unless the owner asks for one on a scratch chip; then
  BLANK CHECK, the hold, the verify and the two read-backs, and the line in `burns.jsonl`.

## 8. Disks: read first, write only with the owner

Read-only:
```bash
gatbox-disk list
gatbox-disk chd
ls -la /srv/gatbox/disks/ /srv/gatbox/disks/_chd/ /srv/gatbox/disks/_incoming/ 2>&1 | head -n 60
```

- DISK tab: the drive list, DRIVE INFO, SWAP, RECORDS, the store's free space.
- TARGET CHECK of a drive against an image (it reads only).
- **MAME CHECK has never run with a real CHD.** The owner's CarnEvil CHD is in `_chd/`. With a CarnEvil image in the store, run
  MAME CHECK on it (DISK tab, or `gatbox-disk mame -i <image>`). Record MATCH, the differences, or the error.
- **The store's lock file:** `ls -l /srv/gatbox/disks/.gatbox-disk.lock`. On 2026-11-17 it was the owner's, and the job's user
  couldn't open it. Record its owner and mode.
- `_chd/` was 770 with no setgid bit on 2026-11-17. Record its mode.

Writes, with the owner at the bench, one at a time:
- READ: one small card or drive she picks (READ has passed on the 80 GB donor drive and a 65 MB CF card). Record pass or fail and
  the time it took.
- IMPORT: only if a file is waiting in `_incoming`. Record what it filed and where.
- HOLES (`gatbox-disk holes -i <image>`, run as the job's user: `sudo -u gatbox-disk /usr/local/bin/gatbox-disk holes -i <image>`):
  one image first. Take `sha256sum` of the image before and after; they must be the same. Record the room freed.
- **FORMAT has never run on a real drive.** Only on a scratch drive the owner names: FORMAT CHECK first (it reads), then FORMAT
  with her yes and the hold. Record the filesystem made and the result of reading it back.
- RESTORE and WRITE have each passed once. Don't repeat them unless she asks.

## 9. The drive browser (DISK → FILES)

- A stored image: volumes, a folder, a photo's preview, a PDF page, a document's text.
- `systemd-analyze security gatbox-browse.service | tail -n 1` (the score: 0.8 SAFE when it was installed).
- A Nikon NEF, if a stored image has one: does its picture draw? (Never tried on a real camera's file.)
- A `.doc`, `.xls` or `.ppt`, if one is stored: its text, or the error.

## 10. Wi-Fi and the hotspot (system config: the owner decides)

```bash
nmcli -t -f NAME,TYPE,DEVICE con show
systemctl status gatbox-ap-fallback.timer gatbox-wifi.path --no-pager
```

Don't print a password (`nmcli` shows none with these fields). **The hotspot coming up on its own has never been exercised,** and
neither has adding a network from the 7". Only if the owner wants to: she takes the home network away, and you record whether
SSID GATBOX appears and how long it took. Change no profile yourself.

## 11. Sync, the hubs and the archive

```bash
systemctl list-timers 'gatbox-sync*' 'gatbox-disk-send*' --no-pager
journalctl -u gatbox-sync -n 40 --no-pager
journalctl -u gatbox-disk-send -n 40 --no-pager
```

Record whether each hub answered and whether the archive took the last disk image (state only: no token, no address).

## 12. The library and manuals

- LIBRARY: the games load; FROM THE HUBS, if it shows a number.
- FIND A MANUAL on a game the owner picks: the job runs and ends.
- READ LIMITS on one manual: what it proposed, the page, and whether a page needed OCR. Confirm nothing: CONFIRM is hers.

## 13. Kiosk and boot

The 7" opens `127.0.0.1` at boot. Don't reboot unless the owner says so; if she does, record how long until the dashboard
was up.

## 14. A quick look at what listens

```bash
sudo ss -tlnp
ls -l /etc/gatbox/ /var/lib/gatbox-web/ 2>&1 | head -n 40
```

Expected listeners: 80 (gatbox-web), 22 (ssh), and the loopback-only ones (the drive browser's socket). Anything else listening
on the network is a finding. Any file under `/etc/gatbox` readable by everyone that holds a token is a finding (record the file
name, never its contents).

## 15. The end

At the top of the results file: the counts of PASS, FAIL, NOT RUN and BLOCKED; the five findings that matter most, worst first;
and a list of everything that wrote to a drive, a chip, a config file or the network during the audit, with the time of each.
Then tell the owner where the file is.
