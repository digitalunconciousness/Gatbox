## 2026-09-25 — First cabinet log: Gauntlet Legends
- LOGGED: `rail_20260925_021402.csv`, V DC, 1h25m25s, 10,251 samples at 2 S/s. First real
  cabinet run of the Phase 1 logger. Clock **unverified** (no NTP at the bar): durations are
  right, wall-clock times are not.
- CRASH: the game crashed during the run. From power-up (~02:15) to the power-off at 03:22:48
  (~67 min) the +5V rail never left 4.75–5.25 V. The crash moment itself isn't in the log
  (no marker, clock unverified).
- CORRECTION: the window was too wide for this game. The Gauntlet Legends manual's DC limits
  are **+5V 4.90–5.10 V** and **+12V 11.5–12.5 V**. The session mean (4.936 V) is dragged
  around by the 0 V and 20 V stretches; steady-state is somewhere around 4.93–4.98, which
  is the bottom of spec. Re-run the gameplay slice (02:16 → 03:22) at 4.90–5.10 in gatbox-web.
- LIMIT: a DMM at 2 S/s can't see millisecond dropouts. A flat log rules out slow sag at this
  point, not fast glitches (M2K, Phase 3).
- EVENT 03:22:48: tried to power off; the mains switch didn't go fully off. The meter read
  **~20.28 V** for about 25 s in two bursts (11 + 39 samples, back inside the window in
  between), then ~5 V to the end at 03:39:27. **Not yet explained.** A loaded +5V rail can't
  sit at 20 V: that's ~16× the power into the same load, beyond an arcade switcher, and 5V
  logic wouldn't survive it. So it was either not the loaded rail (lead moved, or measuring
  a different node) or a backfeed into the unpowered 5V net from something that stayed live
  through the half-thrown switch.
  PENDING: CSV slice 03:22:40–03:23:30, where the clips were, GL boot + self-test result.
- SAFETY: the mains switch can rest half-off, so its contacts are worn/arcing. Replace it
  before the cab goes back to customers.
- SYMPTOM: GL on-screen text switched itself to **Spanish** (Game Options → "Texts In",
  on-screen text only). Settings memory got scrambled. Suspects: the dirty power-off, and/or
  the **timekeeper SNAPHAT battery** (MP1037 / M4T28-BR12SH1, ~$14; same part on NFL Blitz
  2000 and the other Midway Vegas-era boards). Test mode reports battery failure.
- Logger moved to NFL Blitz for the day as a comparison (same Atari/Midway 3D + HDD family).
- NEXT (GL): replace the switch → boot + self-test (battery status) → check ALL adjustments,
  restore factory settings, Texts In = English → re-run the report slice at 4.90–5.10 →
  trim +5V to 5.00–5.05 at the board if it's sitting low (GL max is 5.10, overriding our
  usual 5.0–5.2) → image the HDD → next session: log **+12V at the drive connector**
  (11.5–12.5) with a MARK the moment it crashes.
- LATER SAME DAY: GL runs from a CF card, not the original HDD. The owner suspects the card.
  Worked separately. Quick check: image it twice and compare hashes; a card that returns
  different data on two reads is dying.
- MULE Mk III, screen carrier rev b coupon printed + fitted: glass drops in the ridge,
  picture fills the window, all four ears land on the bosses (G 157.16 / H 114.43 confirmed).
  **Carrier geometry validated.**
  - Micro-USB plug wouldn't pass: the 3.7 mm web between the HDMI/USB notch and the backlight
    notch sat on the plug overmould (usb_W 11 was a guess). Web pulled off by hand.
    → rev c: one connector opening, HDMI → backlight switch, 52.2 mm long. The tub's
    right-wall window must be one opening too.
  - Coupon tap holes (2.2 modeled, ~2.0 printed = M2.5 minor diameter): stiff first start,
    expected. Coupon-only; the real carrier takes heat-sets.
  - Connector side: resolved. The jacks are on the right (+X) as designed; "left" was a mix-up.
    Right side also keeps the HDMI jumper short (Pi is back-right). NEXT: draw the tub.
- MULE Mk III rev d: **the lid tub is drawn** (`gatbox-mule-3.scad`, carrier unchanged).
  - 178.36 × 196.83 × 23.33, 4.0 walls + skin, prints skin-down (209.6 deep with the hinge:
    ~5 mm spare on the 220 bed, so no brim). 4 heat-set bosses take the carrier (M2.5 CSK from
    the bezel face). The single connector opening on the right wall is open at the rim, so it
    needs no bridge. Plug pocket in front, with the lead slot over the jacks, a thumb notch, and a divider rib.
  - Hinge = rev d's dowel, same numbers, so the pins already printed fit. Towers are NOT
    teardropped (the lid's back wall sweeps over them at 0.85). NEW open stop: a lug on each lid
    knuckle lands flat on the base back wall at 105°.
  - Snaps: rev d's 1.4 bite, on a 14 mm tongue in each side wall (~1% strain) over a ramped nub
    in a base pocket. Closing ramp 36° to the travel, holding ramp 43°. The lower ramp prints
    unsupported.
  - CHECKED: lid swept 0–110° against a base proxy: clear to 104.9°, stop faces touch past
    105°, snaps clear at shut. The sweep caught the knuckle arm dipping 1.5 mm into the base wall
    below the rim, fixed by raising the pad's inner corner. Tub clears the carrier, the panel and
    the HDMI plug. Print-pose overhang scan: only the tongue-window bridges and the pin-bore crowns.
  - DECIDED: coupons before the tub: hinge_lid + hinge_base (existing rev d pin, check the
    stop), snap_lid + snap_base (click and pull).
  - BASE must match the echoed BASE lines: towers ±10.25 per cluster, bore 5.45 at y 202.33
    z rim−2, root rim−16..rim−4; flat back wall rim..rim−9 between towers; snap pockets
    y 24.5–39.5, 2.4 deep, rim..rim−11.2, nub 1.8 off the floor at rim−5.5.
  - Hardware: 8 heat-sets, 4× M2.5×8 (ears → carrier), **4× M2.5×8 countersunk**
    (carrier → tub, bezel face), 3 pins.
- NEXT (tooling, into the Phase 2 plan): per-machine rail specs from the roster, over-voltage
  alarm on the live view, steady-state stats that skip power-off/on stretches, MARK
  scans; RTC battery moves up to "buy now".
