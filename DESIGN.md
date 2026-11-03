# GATBOX: design direction

The owner's direction for the deck's own screens: the 7" kiosk dashboard, the phone view and the
archive's pages. It is read by agents, with antislop's UI, copy and accessibility skills, as the
direction to build toward. It was written from the owner's approved design notes (the tokens in
`CLAUDE.md`, and `docs/superpowers/specs/2026-10-28-one-look-four-themes.md`), not invented.

## Identity

GATBOX is a bench and diagnostic deck: a Raspberry Pi 5 with a 7" touch screen, a meter, an EPROM
programmer and a scanner, used at a cabinet or a bench. It is a tool the owner reads a number off,
not a product page.

## Personality

- The reading comes first: one big number, its state as a word beside it, and the action next to it.
- Colour means status and only status (ok, warn, fault, info). The accent means "you are here" or
  "the main action". A state always has a word: IN WINDOW, OPEN, NO MATCH.
- Quiet by default. Glow and scanlines are for the brand, the active tab and status edges, nowhere else.
- Anything copied or compared is monospace: readings, tags, slugs, `model_key`, hashes, chip labels.

## The look: Synthwave, and only Synthwave

The deck has one look, chosen by the owner on 2026-10-28 (the four themes are for the two apps).
It is deliberate, and these are the parts antislop would otherwise count as defaults:

- a deep violet gradient ground, and a neon palette on it;
- scanlines on the 7";
- monospace type for the body, in Share Tech Mono;
- glow, only on badges, active states and headline edges.

Palette: bg `#150a28`, bg2 `#1f0f3d`, panel `#1f1240`, border `#3d1e6b`, magenta (primary, the one
thing to do) `#ff2e9f`, cyan (secondary, links) `#7dfaff`, lime (ok) `#5ef2b0`, red (trip, danger)
`#ff4d6d`, amber (warn) `#ffb74d`, text `#ece3ff`, dim `#9080b0`. The page is
`linear-gradient(180deg, bg2, bg)`.

Typography: Chakra Petch for display, Share Tech Mono for the body and every number, both served by
the Pi (no CDN).

## Mood and use

Readable in a dark cabinet and under bench lights. At least 14 px on the 7" and 16 px on a phone;
touch targets of 56 px on the 7" and 44 px on a phone. Plain HTML, CSS and JS: no framework, no build
step, no CDN. Printed labels stay print-first.
