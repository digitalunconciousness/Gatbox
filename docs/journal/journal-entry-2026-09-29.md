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
- Manuals installed (the owner OK'd the new group, the folder and the web server's write access for uploads).
- The floor's manuals list: 396 documents for 88 of the 95 machines, picked by hand from the public archives (other
  games, cocktail and conversion versions, older printings and Japanese-only editions left out). 389 are on the Pi
  (1.2 GB); 7 wait on Internet Archive servers that are erroring, and a later fetch gets them.
- Where a machine has no manual of its own, the manual for the same board stands in, per MAME: UMK3 gets MK3's,
  NBA Jam TE gets NBA Jam's, NFL Blitz 2000 gets the 1997 Blitz kit's.
- Nothing found for Darkstalkers, DDR, House of the Dead 2, The Swarm, Retro Raccoons, Snow Bros 2 and Super Ghouls
  (no arcade board: it was a Super Nintendo game). House of the Dead 2 and Retro Raccoons can be downloaded by hand
  and added with ADD PDF; IPDB won't allow automated downloads, so the regular Addams Family manual is the same.
- Spec sheets read off the manuals, every value checked against its page: 18 rail limits on 10 machines (Batman,
  California Speed, Captain America, Defender, Gauntlet Legends, Hyper Sports, RoboCop, Super Pac-Man, Tekken 3,
  Tetris) and 52 facts on 42 machines (power, line voltage, fuses, monitor). Gauntlet Legends' manual (p.51) gives
  the same +5 V 4.90–5.10 and +12 V 11.5–12.5 as the owner's spec from 09-25.
- FOUND: most older manuals give only current ratings and line voltage, no rail limits. Those machines get the
  facts; their window stays the profile's until a limit is confirmed or an actual value is set.
- NEXT: install the list; CONFIRM the rail limits on the dashboard as each machine comes up; settle the CHECK
  machines (Batman, D&D, SF2 Grandmaster, After Burner, Snow Bros 2, DDR's mix); burning (CLI per the spec, or the
  dashboard, which would mean changing hard rule 6); M8 when she's ready.
