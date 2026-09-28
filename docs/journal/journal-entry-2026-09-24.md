## 2026-09-24 — meter mode in the web view + report; web view into the bootstrap
- gatbox-web shows the UT61E mode (DC/AC V, DC/AC A, Ω, continuity, diode, capacitance, Hz, %) on the live
  card, per session (min/max in that mode's unit) and in the PDF. HOLD / REL / MAX-MIN flagged. The
  in/out-of-window tag applies to DC voltage only.
- gatbox-rail-report is mode-aware: mode line, per-mode stats (all SI prefixes, was mV/µV only), MODE CHANGES,
  WARNING for HOLD/REL/MAX-MIN, window + EXCURSIONS on DC voltage only, one plot panel per mode.
  DC-only sessions: unchanged apart from the new mode line; plots pixel-identical.
- Real-meter dial-through verified V DC/mV, V AC, Hz, %, Ω, capacitance, µA/mA. FOUND: sigrok writes Ω as
  U+2126 OHM SIGN (fixed). OL arrives as `inf` with a T prefix. Turning the dial does not end a session.
  Diode + continuity not yet seen on real data.
- gatbox-web restyled to the design tokens (gradient, scanlines, Chakra Petch + Share Tech Mono served from the
  Pi, since the hotspot is offline) and now bootstrap payload (+ unit, + pinned/sha256-checked font fetch, + curl).
- Hotspot SSID back to GATBOX (was hand-renamed WalkinAround on 09-23).
- Pi's BRINGUP.md "Phase 1/2" renamed "Bring-up 1/2" so they don't clash with project Phase 2 (software).
- DECIDED: fold-back done by uploading the Pi's gatbox-bootstrap.sh + logger/report/web copies to the project.
- NEXT: meter back to V⎓ + Start new session, first overnight cabinet log; confirm diode/continuity on real data.
