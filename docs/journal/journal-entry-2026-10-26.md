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
