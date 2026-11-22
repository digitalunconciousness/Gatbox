# MEGA4: POWER CYCLE for the T48 (design note, 2026-11-22)

## The owner's decisions (2026-11-22)

- The UUGear MEGA4's per-port power switching (PPPS) is used, **for the T48 only.** This replaces "uhubctl / any USB
  port power switching: not owned" and "MEGA4 as a plain hub" in CLAUDE.md.
- Switching is a **button on the 7", interlocked.**
- The MEGA4 runs **from the Pi** for now (no new power brick). A brick on its USB-C input while it is also on the Pi
  waits until UUGear's documentation says whether the two can feed each other.
- The Waveshare USB hub HAT (USB 2.0 hub, 1a40:0101 read off the Pi) is connected **by USB cable**, carrying the
  meter's PL-2303 and the EY-H2. If it is later seated on the 40-pin header, its pins are checked first (hard rule 7:
  GPIO 14/15 stay free, GPIO 3 and 26 are reserved).

## What changes

1. **Package:** `uhubctl` (apt, 2.6.0-1 on Trixie) joins the bootstrap's package list.
2. **Permission:** a udev rule gives group `gatbox-dump` read-write on the MEGA4's hub device nodes (its USB 2 and
   USB 3 halves), matched by the vendor and product IDs read off the Pi at bench step 1, never guessed (hard rule 12).
   Shown to the owner as a diff before it is installed (hard rule 5).
3. **The job:** `gatbox-dump` gets a third operation beside dump and burn, `cycle`:
   - find the T48 (a466:0a53) in the USB tree, and the MEGA4 port it hangs off;
   - refuse when the T48 isn't behind the MEGA4 ("the T48 is on a Pi port, which can't be switched: move it to the
     MEGA4"), and when it isn't plugged in at all;
   - switch that one port off, wait 2 s, switch it on (`uhubctl -l <hub> -p <port> -a cycle -d 2`);
   - wait up to 10 s for the T48 to enumerate again, and say whether it did, in `status.json`.
4. **The interlock:** a cycle is a job in the same oneshot service as dump and burn, so systemd never runs two at
   once. On top of that, the request is refused while a dump or burn is queued or running, and while a burn is
   armed (the 30 s window after the hold).
5. **The API:** `POST /api/t48/cycle`, the Pi's own screen only (`is_local()`, 403 from the LAN), refused with the
   reason when the interlock says no.
6. **The 7":** POWER CYCLE T48 on the DUMP tab and on DEVICES' T48 row, shown only when the T48 is behind the MEGA4,
   disabled while a T48 job is busy; a toast with the result ("T48 back in 3 s" / "T48 didn't come back: check its
   cable"). No hold: a cycle with no job running writes nothing.
7. **CLAUDE.md:** the owned list, the MEGA4 line, the not-owned line, and hard rule 6's list of decisions: a T48
   port power cycle is not a hardware write (nothing is written to a chip), and it never runs during a burn.

## Risks

- **A cycle during a burn ruins the chip.** Covered by the shared oneshot service, the busy and armed refusals, and
  a test for each.
- **Does the MEGA4 really cut VBUS on a Pi 5?** Not assumed: bench step 2 shows the T48 dropping off the bus and
  coming back in the kernel log before any code relies on it.
- **The sandbox.** `gatbox-dump.service` has `ProtectKernelTunables=yes` (sysfs read-only) and `DeviceAllow=char-usb_device rw`.
  If uhubctl on this kernel needs to write a port's sysfs `disable` file rather than talk to the hub over USB, the
  unit needs that one path writable. Settled at bench step 2, shown as a diff.
- **The broken Pi USB port.** The MEGA4 goes in a port that works; which one is the owner's answer.

## Tests

- `tests/fake-uhubctl` beside `fake-minipro`. In `tests/test-dump.py`: a cycle succeeds; the T48 on a Pi port is
  refused; no T48 is refused; a queued or running dump or burn is refused; an armed burn is refused; the LAN gets 403.
- In `tests/test-dash.py`: the button shows only with the T48 behind the MEGA4, and is disabled while a job is busy.
- On the bench, with the owner: a cycle with the T48 idle; it comes back; a dump right after reads twice, identical.

## Bench steps before the code

1. The owner plugs the MEGA4 (from the Pi, no brick) into a working USB 3 port and the T48 into one of its ports.
   The Pi's `lsusb`, `lsusb -t` and `lsusb -v` give the MEGA4's IDs and the T48's port.
2. The bootstrap adds `uhubctl` only; the owner runs it. Then `sudo uhubctl` lists the MEGA4 and whether it reports
   per-port power switching, and one `sudo uhubctl -l <hub> -p <port> -a cycle` with the T48 idle shows in the
   kernel log whether the port's power really goes off.
