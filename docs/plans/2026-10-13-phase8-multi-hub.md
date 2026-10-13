# Phase 8: several hubs — design note

Status: **agreed 2026-10-13**; being built.

The second hub serves contract v1 exactly as the tracker does (`docs/contract/v1` is byte-identical
in all three repos), plus optional extras: `app` and `capabilities: ["jobs"]` in health,
`model_key` on roster entries, `work_order` and `nte` on orders, and `phase` accepted on
`rail_session` and `session_tag`. The contract's request shapes don't change. Hub names, URLs and
labels live only in `/etc/gatbox`, never in git.

**The rule that bounds this note: a Pi with only `/etc/gatbox/hub.conf` behaves exactly as
today.** Same uids, same ledger contents, same exit codes, same tile, same picker, no `phase` ever
sent. Every new behaviour switches on when a second hub, or a hub that lists `jobs`, appears.

---

## 1. Config

```
/etc/gatbox/hub.conf            existing, unchanged: the implicit entry, named "default"
/etc/gatbox/hubs.d/<name>.conf  new, one per hub: HUB_URLS, HUB_TOKEN, HUB_LABEL
```

- **`<name>`** is the file's stem and must match `[a-z0-9][a-z0-9-]{0,31}`. It names the state
  directory and appears in logs. Files that don't fit (`x.conf~`, `.x.swp`, `README`) are ignored
  and logged. `default` is reserved for `hub.conf`, so `hubs.d/default.conf` is refused.
- **`HUB_LABEL`** is what the 7" shows: picker group headings, the SYSTEM tile, the JOB card. It's
  optional, falling back to the name upper-cased (`DEFAULT` for `hub.conf`). It goes into the sync
  state (below) so gatbox-web can show it. A label isn't secret. The token is.
- **Two entries with the same device public id** are the same token twice. That's a mistake: both
  would hold the same roster, and every slug would conflict. The second entry, in sort order with
  `default` first, is refused, and the tile says so.
- `hub.conf` may also carry `HUB_LABEL`. Without one, nothing changes.

### Credentials: as tightly held as today

Today: `LoadCredential=hub:/etc/gatbox/hub.conf`, with the file at 600 root and
`ConditionPathExists=` on it. Two systemd facts shape what changes (checked in `systemd.exec(5)`;
to re-check on the Pi's systemd before installing, see Risks):

1. A directory given to `LoadCredential=` loads each regular file in it as its own credential,
   `<id>_<filename>`, and skips symlinks.
2. **A missing absolute path is fatal to the unit.** So adding a second `LoadCredential=` line for
   a file that might not exist would break any Pi that lacks it.

What I propose for the unit:

```ini
# Run when either source has something in it. "|" makes these OR, not AND.
ConditionFileNotEmpty=|/etc/gatbox/hub.conf
ConditionDirectoryNotEmpty=|/etc/gatbox/hubs.d
LoadCredential=hub:/etc/gatbox/hub.conf
LoadCredential=hubs:/etc/gatbox/hubs.d
```

The tmpfiles entry gains two lines, so both paths always exist and neither credential can be the
missing one:

```
d /etc/gatbox/hubs.d   0700 root root -
f /etc/gatbox/hub.conf 0600 root root -     # created empty only if absent; never touches an existing one
```

An empty `hub.conf` means no implicit entry. `ConditionFileNotEmpty` keeps the unit from running
at all, as today on a Pi with no token, and the tile still says "not configured". This is the
only change to the "show before you change" files: `/etc/tmpfiles.d/gatbox.conf` and the
`gatbox-sync.service` unit. I'll show both diffs at install time as usual.

`gatbox-sync` reads `$CREDENTIALS_DIRECTORY/hub` and `hubs_<name>.conf`. `GATBOX_SYNC_CONF` keeps
working for tests, and a new `GATBOX_SYNC_CONF_DIR` stands in for `hubs.d`.

---

## 2. gatbox-sync

Still stdlib-only, still one oneshot on the 2-minute timer, still reading gatbox-web over
loopback.

### State: the default hub stays where it is

```
/var/lib/gatbox-sync/
  status.json                 the whole run; with one hub, exactly today's keys (+ "hubs",
                              "held", "conflicts", "refused")
  sent.json roster.json orders.json hub.json     the default hub (hub.conf), as today
  hubs/<name>/
    sent.json roster.json orders.json hub.json   each hubs.d hub
```

*Changed while building:* the note first moved the default hub's files into `hubs/default/`.
Leaving them at the top level instead means nothing moves at all, every existing test holds
as written, and a downgrade still finds its ledger. `hub.json` is new: label, app,
capabilities, the URL that answered. It keeps the last good capabilities so the JOB card works
from cache when the hub can't be reached.

### The run

1. Read every entry (hub.conf → `default`, then `hubs.d` in name order).
2. For each hub, as today: health on each URL in turn → if one answers, refresh `roster.json` and
   `hub.json`. A hub that can't be reached keeps its last cached roster for routing.
3. **Route.** Build `slug → [hubs]` from the cached rosters.
   - **Exactly one hub configured (today's case): no routing.** Everything goes to it, unknown
     slugs are refused by the hub and parked after five tries, exactly as now.
   - **Two or more:** a slug in one roster goes to that hub. A slug in no roster is **held**:
     not sent, not counted toward the five tries, and listed on the tile as "no hub has
     `<slug>`". It goes out by itself on the first run after a roster gains it. A slug in two
     rosters is a **conflict**: held, flagged on the tile with both labels, sent nowhere until
     one roster drops it.
4. Per hub, the existing send loop: sessions first, then orders and tags in the same POST, with
   the same caps, 240 s budget shared across hubs, same ledger, tries and parking. **Every uid is
   salted with that hub's device public id.** An order naming a session uses the destination
   hub's id, which is always the same hub because they share a machine.
5. Open-orders cache per hub: as today (machines with sessions, max 40), plus **the machine
   currently set**, read from `GET /api/machine`, so a customer machine the Pi has never metered
   still shows its job.

### What routes where

| item | routed by |
|---|---|
| `rail_session` | the session's `machine` (header `# machine=`) |
| `order` | the outbox entry's `machine` |
| `session_tag` | new entries carry `machine` (the machine the order is on); old entries without one: the attached session's machine |

### `phase`

Sent **only** to a hub whose cached capabilities list `jobs`, and only as one of `as_found`,
`during`, `as_left`. Anything else is left out, never sent as null. The tracker never advertises
`jobs`, so it never sees the field, even if a stray value got onto a barcade session.

### Exit codes

Unchanged per hub and combined as the worst: 1 only for a malformed token or a 401/403/400 from
some hub. A hub being out of reach is still 0. A refused duplicate-token entry logs and is
otherwise 0, because it's on the tile.

---

## 3. gatbox-web

Read-only on `/var/lib/gatbox-sync` as today (the unit already has `ReadOnlyPaths=`). It never
reads `/etc/gatbox`.

- **New `gatboxweb/hubs.py`:** the one reader of the sync state. `hubs()` returns
  `[{name, label, capabilities, roster_at, …}]`, `owner(slug)` returns hub, None or conflict,
  `machine(slug)` returns the cached roster entry, and `open_orders(slug)` reads the owning hub's
  cache. `orders.py` (`_sent_uids`, `hub_open`) and `system.hub()` switch to it.
  - Today's single top-level reads still work while there's no `hubs/` yet, so a gatbox-web
    installed before the first sync run under the new code is fine.
- **Known machines** = the local roster (the barcade file + added-here) ∪ every cached hub
  roster. `roster.slugs()` grows into this, so PICK MACHINE, the scanner (bare slug and
  `/g/<slug>` label), captures and dumps all accept a customer machine's asset tag. Asset tags
  already pass `roster.SLUG`.
  - The local roster file and its schema are untouched. Nothing is written into it or
    `roster-added.json`. A hub machine is never "added here".
- **`/api/machine`** for a hub-only slug returns a synthesized entry (`slug`, `name`, `status`,
  `location`, `model_key`, `hub: {name, label, jobs}`). For a local machine it adds the same
  `hub` block when a hub owns it. Existing keys are unchanged.
- **`/api/roster`** gains `groups: [{label, hub, slugs}]` for the picker. `machines` stays as it
  is, plus hub-only entries. `GET /roster.json` (the maintenance-app export) stays **local only**:
  customer machines never leak into the barcade's roster file.
- **`/api/system`** `hub` keeps its keys (aggregate) and gains `hubs: [...]`, `conflicts`,
  `unrouted`.
- **`model_key`:** when the set machine has one, gatbox-web saves it next to the machine:
  `machine` keeps its one-line format, and a new `machine-model` file holds the key. That way
  `gatbox-meta` (root, no sync access) and every reader of `profiles.read_state()` see it without
  learning about hubs. Then:
  - **Rail window** (`profiles.resolve`): the slug's own *actual* window > the model's spec
    (specs file < confirmed) > user > profile. A manual's limits belong to the model, and a
    measured actual belongs to this one board.
  - **MANUALS / spec sheet:** documents, search, phone uploads and CONFIRM go to the model_key's
    library. The actual-window field goes to the machine's own slug.
  - **MAME ROM checklist / WHICH CHIP?:** the model_key's set. Dumps are still archived under the
    customer machine's slug.
- **`phase`** (decision 1 below): the current phase is gatbox-web state like the
  machine, and it's cleared whenever the machine changes. `profiles.header_lines()` adds an
  optional `# phase=as_found` line, so changing phase mid-session **starts a new file** through the
  path a profile or machine change already takes. Old CSVs have no such line, and the report gives
  `phase: null`. The 09-25 fixture is untouched.
- **Outbox:** `POST /api/orders/tag` takes an optional `phase`, and new tag entries record
  `machine`. Order entries are unchanged.

---

## 4. Dashboard

Plain JS and the design tokens, as everywhere else.

- **Picker:** with two or more hubs cached, the list is grouped under each hub's label. Machines
  no hub owns go under **THIS PI**, and a conflicting slug shows under both groups with a red
  CONFLICT badge. The filter keys work across all groups. With one hub or none, it stays today's
  flat list.
- **MACHINE card** for a hub machine: name, location/status, `HUB: <label>`, and the model's
  spec, manuals and ROM chips when there's a `model_key`. Without one: "No model on this hub's
  record, so no manuals or ROM list".
- **JOB card,** only when the owning hub's capabilities list `jobs`: it replaces the "Work orders"
  section for that machine.
  - Each open job shows issue, status, work order, NTE (the string as the hub sent it, or
    "no NTE") and priority.
  - **ATTACH LAST TRACE** offers AS-FOUND / AS-LEFT / no phase.
  - **NEW JOB** is the existing NEW ORDER flow under a hub-appropriate name.
  - Barcade machines get today's Work orders section, byte-for-byte the same flow.
- **SAVE READING** on a jobs-hub machine shows AS-FOUND / AS-LEFT (decision 1 below).
- **SYSTEM:** one Hub tile per hub (label, reachable, queued, last push, sent, parked), plus a
  red **Conflicts** row listing each slug and the two labels, and a **Held** row for slugs no
  roster has. With one hub, today's tile is unchanged.

---

## 5. Tests

- `tests/test-sync.sh`: every existing check passes unchanged (they're the "exactly as today"
  proof). New: two stub hubs on two ports, one advertising `jobs`. The cases are routing by slug,
  per-hub uid salt (the same CSV name hashes differently per hub), a held slug that goes out once
  a roster gains it, a conflict that's sent nowhere and listed, `phase` sent only to the jobs hub
  and never as null or an unknown value, a duplicate token refused, bad file names ignored,
  ledger migration with no re-send, and per-hub `hub.json` keeping capabilities while a hub is
  unreachable.
- `tests/test-contract.sh`: the examples still match byte-for-byte with no phase, and a phase
  item has exactly the extra key.
- `tests/test-web-api.sh` / `test-roster.py` / `test-scan.py`: a hub slug can be set and scanned,
  an unknown slug still gets 404, `/roster.json` has no hub machines, `model_key` reaches
  manuals, spec and MAME, the actual window stays on the slug, `/api/system` keeps its old keys,
  and old phase-less CSVs still load.
- `tests/test-dash.py` (headless Chromium): grouped picker, JOB card shown for a jobs hub and
  absent for the tracker, phase buttons, conflict and held rows.
- `tests/test-raillog-*.sh` + `test-replay.sh`: the logger with and without `# phase=`.
- `tests/test-bootstrap.sh`: `hubs.d` 0700, empty `hub.conf` created only if absent and never
  overwritten, the OR conditions, `--check` showing the unit/tmpfiles diff.

---

## 6. Risks

1. **Credential directory behaviour on the Pi's systemd.** I read it from the man page on a
   newer systemd. Before install I'll check on the Pi (read-only):
   `systemd-analyze verify` on the new unit, plus `man systemd.exec`. On the first run, the
   journal should show it loading both an empty `hubs.d` and a filled one. If the empty-directory
   case misbehaves there, the fallback is to drop a `README` into `hubs.d` from tmpfiles; it's
   not a `.conf`, so sync ignores it.
2. **The empty `hub.conf` placeholder** is a new file in `/etc`. Its only content is nothing, and
   tmpfiles `f` never overwrites. The other option, a per-machine drop-in written by the
   bootstrap, would go wrong when a file is added after install, so I prefer the placeholder.
3. **Routing from a stale cache.** A machine moved between hubs routes by the last roster seen.
   Worst case it's held or conflicting until both rosters refresh, never sent to the wrong hub,
   because a conflict is sent nowhere.
4. **The logger touches new code** (`header_lines` + `phase`). Hard rule 3: the raillog/replay
   tests gate the install, and `gatbox-meta` must still never fail loudly. A bad `phase` value in
   state is dropped, not written.
5. **Customer data on the Pi.** The second hub's roster carries no customer names, only asset name,
   status and location. gatbox-web has no login, and the phone view shows
   only that much. Nothing customer-side is written into git or into `/roster.json`.
6. **Order ids are per hub.** A tag names an order id. Routing by machine keeps tag and order on
   the same hub, and the hub rejects a different-machine pair anyway.

---

## Decided by the owner (2026-10-13)

1. **Phase on SAVE READING:** AS-FOUND / AS-LEFT sets the session's phase (a new file if it
   changes), then saves the reading. The captures CSV keeps its 8 columns.
2. **`during`:** not offered in the UI. The hub accepts it, and sync still passes it through
   if it is ever set.
3. **THIS PI** is the picker heading for machines no hub owns.

---

## The exit (on hardware, the owner's steps; final versions with the install)

Marked **[yours]**: they need sudo or the hub's Settings, and the URL and label are site
data.

1. **[yours]** The second hub → Settings → Devices: mint a token for this Pi. It's shown once.
2. **[yours]** On the Pi:
   ```
   sudo install -m 600 -o root -g root /dev/null /etc/gatbox/hubs.d/<name>.conf
   sudoedit /etc/gatbox/hubs.d/<name>.conf
   ```
   with
   ```
   HUB_URLS=https://<the hub's hostname>
   HUB_TOKEN=gbx_<public_id>.<secret>
   HUB_LABEL=<what the 7" should call it>
   ```
3. **[yours]** `sudo systemctl start gatbox-sync && journalctl -u gatbox-sync -n 40 --no-pager`.
   Expect both hubs `ok`, two rosters cached, no conflicts.
4. **[yours]** One session on a barcade machine, one on a customer machine (picked from its
   group, AS-FOUND), each stopped so the file is finished. Two minutes later: the barcade trace
   is in the tracker and not on the second hub, and the customer trace is on the job (it
   auto-attaches when the asset has exactly one open job) and not in the tracker.
