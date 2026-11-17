# GATBOX: features for the next phase

The owner's picks on 2026-11-17, from a list written at the end of phase 2. Nothing here is built. Each one gets its own plan
note and the owner's OK before any code, as every milestone has.

## Picked for the next phase

**Power-loss indicator.** In a cabinet the Pi runs from a 12 V buck converter, and nothing tells it when the converter's
input sags or drops. A voltage reading on the converter's input, logged beside the rail readings, would show whether a
glitch in a session was the cabinet's supply or the Pi's. *Needs hardware that isn't owned yet:* a divider (or an isolated
sense) from the 12 V side onto a 3.3 V input (hard rule 7), and the converter's make and rating read off it first
(CLAUDE.md). The plan starts with that part list; no code assumes it before then.

**An audit log of every hardware write.** One append-only record of every burn, WRITE, RESTORE, FORMAT and HOLES pass:
when, from which screen, which chip or drive (its model and serial), which image, and the result. `burns.jsonl` and the
disk job records hold most of this already, in different places; this is one index over them, readable on the 7".

**A test-fixture library.** Known-good images and CHDs kept as references, each with the result of its MAME CHECK and its
SHA-256, so a drive or a card can be checked against the right reference in one step instead of being matched by hand.

**Session templates.** A preset per machine or platform (the rail limits, the dial modes to expect, the usual marks), so a
new session starts with the right window and the 7" says which mode to set next.

**An image integrity sweep.** A scheduled re-hash of every stored image against the SHA-256 in its sidecar, on the deck's
store and on the archive, with the last result on the DISK tab. It catches a bad sector on the store before the image is
needed. It reads only.

**A printable record for every procedure.** The DISK tab's job record exists. The same printable sheet for a rail-logger
session (machine, dates, window, marks, what was found) and for a T48 dump or burn.

**Help on each tab.** One short line per tab on the 7", in the owner's words, saying what the tab is for and what to press
first, so the screen explains itself without the manual.

## Punted, on hold beside M8

Recorded in BRINGUP next to M8. Not started, not asked about, until the owner brings one up.

- **A physical ARM button.** Hard rule 6 names it as the later replacement for the 7" hold.
- **A GPIO MARK button.** Deferred since 2026-09-28: the momentary button is owned and its pin is chosen (BRINGUP), and it waits for an enclosure to mount it in.
- **SMART reads on a path that isn't the dock's bridge.** The XinTop dock resets on SMART commands (2026-11-14).
- **A search page on the archive** across games, manuals and images.
- **A phone status page with sign-in**, behind the archive's Cloudflare Access.
