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
