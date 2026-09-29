## 2026-09-29 — M7 dump from the 7" passed; adding machines; SAVE READING
- ⏸ M7 dashboard dump PASSED ("It works"): the SegaSonic EPR-15781C, dumped from the DUMP tab, MATCH
  `sonic/epr-15781c.ic18`, archived under SegaSonic with its identification. Burning is still to decide.
- Owner's requests: a way to add a new machine; what capture does; the manuals with their specs, viewable from the
  dashboard; MAME data for her games for easy hashing. Taken one at a time, each design OK'd first.
- DECIDED: new machines are typed in on the dashboard (not a roster upload). The roster file on the Pi is never
  changed; the Pi keeps its additions separately and EXPORT ROSTER hands back the full roster, in the same format,
  for the maintenance app to import.
- MACHINE → + ADD MACHINE: name, maker, video game or pinball, platform (or NOT SURE), notes. The ID shows before
  saving, since it's permanent (it's what the QR code holds); a machine already on the roster is refused. The new
  machine works at once in PICK MACHINE, the scanner, the label list and the DUMP tab. EDIT fixes a typo (the ID
  stays).
- CAPTURE is now SAVE READING: it saves the meter's reading with a label (e.g. U12 PIN 3) to the machine's notes.
  The button says which machine, and the last three show under the reading.
- DECIDED: no ROM sets get downloaded (copyrighted). Hashing doesn't need them: the MAME on the Pi already holds
  the size, CRC and SHA-1 of every chip of every set. Next: each machine's ROM checklist from that.
- Add machine + SAVE READING installed; the export matched the roster file exactly (nothing added yet).
- ROM checklist: each machine's MAME set, every chip with its size, CRC and SHA-1, in every version MAME knows
  (revisions, regions). A chip is ticked once a dump in the archive matches it. The SegaSonic board's EPR-15781C
  already ticks. On the DUMP tab, WHICH CHIP? lists what's left to dump, fills in the label and lights the EPROM
  sizes that fit (the exact part is still picked off the chip).
- DECIDED: the machine → MAME set list is made by hand, not by name matching: matching by name alone put the
  Joust and Defender pinballs on the video games and a Tiger handheld on Batman. Unsure matches are marked CHECK;
  a dump from the board settles them. Machines MAME doesn't have (the PC-based gun games, Pac-Man Battle Royale,
  Retro Raccoons, most of the Sterns) say so.
- Manuals: a new MANUALS tab. Each machine's documents (manuals, schematics, parts catalogs, kit sheets, bulletins)
  come from the public manual archives (the Internet Archive, the Arcade Manual Archive, Stern's own site), onto the
  Pi only: they're copyrighted, so never in the public repo. The Pi draws each page as a picture, so they read on the
  7" and on a phone: swipe or PREV/NEXT, pinch to zoom, SEARCH ("fuse", "U12", "4.75"). Her own PDFs go in the
  machine's folder, or up from her phone with ADD PDF.
- The spec sheet: each rail limit the manual gives, with its page and the manual's own words. CONFIRM shows the page
  first; only a confirmed limit becomes the logger's window for that machine.
- DECIDED (owner's addition): actual values from the field sit beside the manual's, which are never changed. E.g.
  the manual says +5 V and the cabinet was boosted to 5.20 V: record 5.20 V with a note, taken from the meter or typed,
  and optionally give that machine its own window so a boosted rail doesn't read HIGH all night. The same for the
  rest of the spec page ("fuse is 8 A slow-blow", "LCD swap").
