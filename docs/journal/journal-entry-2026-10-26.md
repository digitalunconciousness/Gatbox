## 2026-10-26 — The keypad on a short screen; Phase 8 installed
- Owner: "The on screen keyboard is wildly crazy for the screen size. Sometimes I can't see
  more than one row of buttons."
- FOUND: every key row was a fixed 54 px (48 on a phone), and only the key grid was allowed to
  give, so that SAVE stays on the screen (the 10-07 fix). On anything shorter than the 7" the
  grid scrolled its rows out of sight, with no scrollbar. A phone held sideways with its address
  bar showing (844x320): 35 of 45 keys cut off, one row showing. The 7" itself (1024x600) showed
  all five rows.
- Ruled out, on the Pi: squeekboard, Pi OS's own on-screen keyboard, is running (it starts on
  any touchscreen), but under the full-screen kiosk it never takes height from the page. The
  kiosk is 1024x600 at scale 1.
- FIXED (#15): the rows share the height the panel has, up to 54 px each. The 7" looks exactly
  as before; a short screen gets shorter keys instead of fewer, and below 480 px of height the
  title, the typed text and CANCEL / SAVE shrink too. CSS only. The tests check every key and
  SAVE at three sideways-phone sizes, letters and the number pad.
- FOUND along the way: on the Pi, test-roster failed 4 checks, because six suites started their
  test server reading the Pi's live hub cache (the real roster). Phase 8 was built on a machine
  with no cache, so it never showed there. Each suite now has a cache of its own. All 22 pass.
- Phase 8 installed with it (owner OK'd the gatbox-sync unit + tmpfiles diff). systemd 257 on
  the Pi, checked in its man page: a directory credential loads one per file, and a missing path
  fails the unit, which is why tmpfiles makes both. The new unit's first run passed: one hub,
  nothing queued, no conflicts.
- gh is on the Pi now (owner). Its browser login opened lynx in the terminal, where GitHub's
  Authorize button can't work; authorizing from a phone at github.com/login/device does.
  `BROWSER=echo gh auth login --web` prints the address instead.
- NEXT: the Phase 8 exit on hardware: the second hub's file in `/etc/gatbox/hubs.d/`, then one
  session each, each trace landing only on its own hub.

## 2026-10-26 (later) — The 7" box: readability and touch
- Owner: "The keypad should always be the same size, shown in totality. The other menus should
  form around the keypad. The machine's page has a crushed an unusable keyboard. [...] The heavy
  data should probably be behind a hide button. [...] some times the buttons seem to click in an
  area upward from the rendered button. Keep important things a little bit away from the bezzel,
  since the scrren is going in a 3d printed box and the bezzle is large."
- FOUND: the crushed keyboard was PICK MACHINE's own second keyboard, squeezed under the machine
  list, and this morning's fix (#15) had let key rows shrink, which made it worse.
- FOUND, the touch: the panel is fine. It reports exactly the screen (0–1024 × 0–600, no
  calibration). What the code did wrong: toasts lay over the bottom of the page and swallowed
  taps (one sat on NEW JOB), and MACHINE emptied itself on every redraw and jumped back to the
  top while a finger was on its way to a button. Both fixed. What's left, if anything, is the
  fingertip and the glass: SYSTEM → TOUCH TEST measures it.
- DECIDED: only the 7" changes. The kiosk's page knows it's the kiosk by its address
  (127.0.0.1); a phone gets the old layout, untouched.
- The 7" now: nothing within 20 px of the sides or 16 px of the top and bottom; no text smaller
  than 14 px, and the dim text brighter; toasts at the top, never in the way of a tap.
- One keyboard everywhere, always whole, always the same size and in the same place; the title,
  the field and anything else fit above it. Typing past the field's width, it follows the cursor;
  a long complaint gets two lines.
- Pickers are buttons: a handful of choices (how urgent, with or without the trace) are big
  buttons with no keyboard; a long list (the machines) is big buttons, and SEARCH brings up the
  keyboard with the matches above it.
- MACHINE leads with the job / work orders card (AS-FOUND, AS-LEFT, NEW JOB), then critical
  actions and rail specs; DETAILS, SAVED READINGS, ROM CHIPS and DUMPS sit behind SHOW.
- FOUND along the way: two links on the 7" ("phone page" on SESSIONS, "Phone view" on SYSTEM)
  led to pages with no way back (the kiosk has no back button). Gone from the 7". LABEL LIST's
  filter was a text box the 7" had no keyboard for: SEARCH now. The manuals viewer's buttons were
  on the edges: moved in.
- TOUCH TEST: nine crosses, tap each centre; it says whether there's a steady offset and by how
  much, and keeps the run so a calibration can be made from the numbers.
- NEXT: install (program files only); use the 7" a while; run TOUCH TEST. A steady offset → a
  calibration (a system setting, shown first); scatter only → done.

