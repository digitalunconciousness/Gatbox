# The 7" box: a readability and touch pass (2026-10-26)

The owner, after #15: "We need to do a heavy readability pass on this. The keypad should always be the same size,
shown in totality. The other menus should form around the keypad. The machine's page has a crushed an unusable
keyboard. Run through each page and look at them with an eye on readability and usability. The heavy data should
probably be behind a hide button. Keep an eye on where putting a button to touch works better than picking from a
list. This will just be for the GATBOX. the web ui works fine, but the dashboard on the box is difficult to use with
the touch screen.. I dunno if it's a screen or code issue, but some times the buttons seem to click in an area upward
from the rendered button. Keep important things a little bit away from the bezzel, since the scrren is going in a 3d
printed box and the bezzle is large."

## What was found (before any change)

- **The crushed keyboard is PICK MACHINE.** `pickFrom()` has its own second keyboard (QWERTY + digits, no space, no
  case) under the list; list and keys share the sheet. #15 let key rows shrink (`minmax(0,54px)`), so with the real
  roster the picker's keys are squeezed. Two keyboards that look different, one of them crushed.
- **Typing past one line is blind.** `.kp-field` is one `nowrap` line with `overflow:hidden` and never scrolls; NEW
  ORDER takes up to 2000 characters.
- **Lists where buttons belong.** "How urgent?" (5), "Attach the last trace?" (2), "Which rail?", "What kind of
  document?" are `pickFrom` lists with a keyboard under a handful of choices.
- **MACHINE puts its actions last.** Name, then risks, faults, parts, PM, critical actions, rail specs, saved
  readings, the ROM chip table (CRC / SHA-1 columns), dumps, and only then the JOB / orders card with AS-FOUND /
  AS-LEFT and NEW JOB.
- **Small, dim text** everywhere: 11–13 px labels, `--mut` #9080b0 on #1f1240.
- **Nothing keeps off the edges**: nav at x=6, header at y=0, CLOSE at the sheet's corner.

### Touch "lands above the button": screen or code?

Screen, checked on the Pi: the WS170120 (USB 0eef:0005) reports ABS 0–1024 × 0–600, the display is 1024×600 at
scale 1, no calibration matrix, touch unmapped (one output), real touch (no mouse emulation). Nothing is skewed. That
leaves a *constant* offset (the finger's contact centre sits below where you aim, plus parallax through the panel's
glass when it's looked at from above) — measurable, and a libinput calibration matrix fixes it — and code. Code,
found:
1. **Toasts eat taps.** They sit bottom-centre over content, with pointer events, for seconds. On MACHINE the toast
   covered NEW JOB in the test's own screenshot.
2. **Views jump under the finger.** `loadMachine()` clears the scrolling view *before* its awaits: on every state
   event the page empties, the scroll resets to the top, and redraws. DUMP redraws on the 15 s device poll, SYSTEM
   every 5 s. A redraw that moves content as a finger comes down sends the tap to whatever slid under it.

## Design

**Box mode.** `html.box` when the page is the 7" kiosk: hostname 127.0.0.1 / localhost / ::1 (the kiosk opens
`http://127.0.0.1/dash/`; a phone comes by the Pi's name or address). `?box=1` / `?box=0` force it. Set by an
inline script in `<head>` before first paint. Every change below is under `html.box` (CSS) or `BOX` (JS): the phone
layout is unchanged.

1. **Bezel margin.** The whole app inset 16 px top/bottom, 20 px left/right; sheets the same; toasts too.
2. **Readable type.** Body 17 px; nothing under 14 px; `--mut` lighter on the box.
3. **One keyboard, one size, the sheet forms around it.** A shared keyboard builder for `keypad()` and the
   picker's search. On the box the key block is fixed (5 rows × 50 px, number pad stretched to the same height,
   CANCEL/SAVE below), docked at the bottom of the sheet; title, field and any list sit above and give way. The
   field follows the cursor; long-text fields show two lines.
4. **Pickers are buttons first.** ≤ 8 choices: big buttons, no keyboard. More: a grid of big buttons, and SEARCH
   brings up the keyboard (the same one, same size) with the matches above it.
5. **MACHINE: actions first, reference data folded.** Head, JOB / orders card, critical actions, rail specs; then
   folds (closed by default, remembered while the page lives): DETAILS (risks, faults, parts, PM), SAVED READINGS,
   ROM CHIPS, DUMPS. Built off-screen and swapped in, so a redraw doesn't move the page.
6. **The other pages**, reviewed one at a time on screenshots: METER, SESSIONS, MANUALS, SYSTEM, DEVICES, DUMP, the
   sheets. Explanations behind a fold, heavy rows behind SHOW.
7. **Touch.** Toasts at the top over the header, `pointer-events:none`. Redraws keep the scroll position. SYSTEM →
   TOUCH TEST: crosses across the screen, tap each centre; it records where the touch landed, shows the mean offset
   and spread, and keeps the result (`GET /api/touchtest`) so a calibration can be made from numbers, not guesses.
   The calibration itself is a udev / libinput change: shown to the owner first (rule 5).

## Tasks
- [x] T1 box mode, bezel margin, readable type, toasts (tests: box class only on the box; every view's frame inside
      the margin; no text under 14 px; a toast never takes a tap)
- [x] T2 one keyboard, fixed size (tests: the same 274 px block in the same place for letters and the number pad;
      every key on screen; the field follows the cursor, one line and two; #15's phone check runs with box off)
- [x] T3 pickers as buttons (tests: "How urgent?" five big buttons, the trace question two, no keyboard; PICK MACHINE
      with a 12-machine roster: big buttons, no keyboard; SEARCH brings the same 274 px keyboard in the same place,
      matches above it)
- [x] T4 MACHINE reordered and folded; redraws keep the scroll (tests: the orders card above the first fold; DETAILS,
      SAVED READINGS, ROM CHIPS, DUMPS closed behind SHOW; a state event while scrolled leaves the page where it was
      and an open fold open)
- [ ] T5 the other pages, from screenshots
- [ ] T6 TOUCH TEST + `/api/touchtest`
- [ ] T7 full suite, screenshots of every view, BRINGUP, journal, PR, install, ask the owner to run TOUCH TEST
