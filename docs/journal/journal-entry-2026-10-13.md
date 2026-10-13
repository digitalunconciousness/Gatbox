## 2026-10-13 — Several hubs (Phase 8), built; not installed yet
- Housekeeping first: CLAUDE.md still said Phase 6 wasn't installed and the two bench exits
  hadn't run. Both happened 2026-10-07 (owner's date). Fixed in PR #13, and repeated at the
  base of this branch so the two merge in either order.
- A second hub now exists, and it serves contract v1 exactly, plus optional fields: `app`
  and `capabilities: ["jobs"]` in health, `model_key` on roster entries, `work_order` and
  `nte` on orders, `phase` accepted on sessions and tags. No request shape changed. Its
  vendored contract is byte-identical to ours; checked, not assumed.
- `/etc/gatbox/hubs.d/<name>.conf` adds a hub with HUB_URLS, HUB_TOKEN and HUB_LABEL. The
  label is the only place a hub's name lives, and that's config, never git.
- **One hub is the box as it was:** no routing, the same state files in the same places, the
  same exit codes. Every one of the 72 existing sync checks passed unchanged, which is the
  proof of that. With two or more hubs, each item goes to the one hub whose roster has the
  machine. A machine on no roster is held and goes out by itself once a roster lists it. A
  machine on two is a conflict: it goes to neither and shows in red on SYSTEM.
- CHANGED FROM THE NOTE WHILE BUILDING: the note moved the original hub's state into
  `hubs/default/`. Leaving it at the top level instead means nothing moves, the old tests hold
  as written, and a downgrade still finds its ledger. Only the new hubs get `hubs/<name>/`.
- Credentials stay as held as before: `hubs.d` is a directory credential. Found in the man
  page, not by trial: a `LoadCredential=` path that's missing fails the unit outright. So
  tmpfiles makes sure both exist, with an *empty* `hub.conf` when there's none (empty means no
  hub), and the unit runs when either has something in it. To re-check on the Pi's own
  systemd before install.
- `model_key`: a customer's machine reads the library this Pi already has (manuals, CONFIRMed
  limits, the MAME list), but **never the model's actuals**. Those were measured on one
  particular board, so a barcade cabinet's measured window must not become a customer
  board's. A customer board's own actuals stay on its own tag.
- DECIDED (owner): AS-FOUND / AS-LEFT on SAVE READING sets the session's phase, which is a
  `# phase=` header line and a new file when it changes. A trace is then one phase from end
  to end, and the job receives it marked. The captures CSV keeps its columns. `during` isn't
  offered on the 7″. THIS PI heads the machines no hub has.
- The background security review caught a real one: a slug from a hub's roster went
  unchecked into the machine state file and from there into the CSV header. A slug with a
  newline could have written header lines. Now only the roster's slug rule gets through,
  whole-string, at the reader and again at `set_machine`. A hub name with a trailing newline
  slipped past `re.match(...$)` the same way; all of these are `fullmatch` now.
- A screenshot caught what no check did: the JOB card drawn twice. A button reloads the
  MACHINE card and so does the state event it causes; both appended after their awaits. Only
  the newest reload draws now, and a check counts the cards. The race was there before, when
  setting a machine; the JOB card just made it visible.
- Tests: sync 110, web api 177, dash 91, bootstrap 54, report 17, all passing. The laptop
  had no matplotlib, so the dashboard suite ran against a copy in the session's scratch
  directory, not the system Python and not the Pi.
- Still to do: install on the Pi (the bootstrap shows the unit and tmpfiles diff first), then
  the owner's exit on hardware.
