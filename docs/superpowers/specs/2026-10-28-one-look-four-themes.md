# One look, four themes — design

Status: **decided by the owner, 2026-10-28** (§6); no code yet. It hangs off the integration
plan (`2026-10-28-gatbox-integration-plan.md`, phase A2). **The deck keeps Synthwave only:** the
four themes are for cabcrawl and the Tech Tracker, per user. The deck rows below stay for
reference, unused.

The owner's ask: one design philosophy across the deck, cabcrawl and the Tech Tracker, with
four themes: **cyberpunk, light professional, dark professional, and follow the system.**

A preview of all four on the 7" deck screen and on a phone is on a private canvas (the link is
in the session summary). It uses the token values below.

---

## 1. What exists today

| | look | how it's built | themes |
|---|---|---|---|
| **Gatbox** (the 7" kiosk and the phone view) | synthwave only | raw palette variables in `web/dash/dash.css` (`--bg`, `--mag`, `--ok`, `--bad`…). The dashboard uses them through `var()` almost everywhere. The phone view's CSS is inline in `phone.py`. The 7" layout lives under `html.box`. | none. Hard rule 11 names the synthwave tokens. The rail-report plot already has a dark palette and a light ("print") palette. |
| **cabcrawl** | synthwave only | the best base of the three: a raw palette mapped to **semantic roles** (`--surface`, `--text-dim`, `--accent`, `--ok`…). Components use only the roles, and `tests/test_design_tokens.py` checks AA contrast from the CSS itself. | none |
| **Tech Tracker** | four themes per user: **Ledger** (light, the default), **Graphite** (dark), follow the system, synthwave | `app_user.desk_theme`, the `data-desk` attribute, theme blocks in `desk.css`. IBM Plex Sans/Mono are vendored for the two business themes. Token names are the synthwave ones, reused, so `--magenta` is teal in Ledger. | **desktop (1024 px and up) only.** Phones are always synthwave (the owner's decision, 2026-10-13). |

So the four themes she asked for already exist, as Tech Tracker's desktop looks. The work is
to make them one shared design that all three use, on every screen size.

---

## 2. The philosophy (the same in all three apps)

1. **The reading comes first.** One big number, its state as a word beside it, and the action
   next to that. Whether it's a voltage on the 7" or a job on a phone, what matters most is
   the largest thing on the screen.
2. **Colour means status, and only status:** ok, warn, fault and info. The accent means
   "you are here" or "the main action", never a state. A state is never shown by colour
   alone: there's always a word (IN WINDOW, OPEN, NO MATCH).
3. **Built for the bench:** touch targets of 44 px or more on phones and 56 px on the 7";
   no text under 14 px on the 7", and 16 px body text on phones. It has to read in a dark
   cabinet and under shop lights.
4. **Quiet by default.** Glow and scanlines belong to Synthwave alone, and even there only on
   the brand, the active tab and status edges. The professional themes have none.
5. **Monospace for anything that's copied or compared:** readings, tags, slugs, `model_key`,
   hashes, chip labels. That keeps 0/O and 1/l apart, and keeps columns lined up.
6. **Self-hosted, no frameworks:** no CDN, no build step, and fonts served by each app, as
   today.
7. **Paper is paper.** Printed labels and customer documents (Tech Tracker's estimates,
   invoices and service reports) stay print-first, white with black text, whatever the screen
   theme.

---

## 3. The four themes

| theme | key | for | type | effects |
|---|---|---|---|---|
| **Synthwave** (the cyberpunk one) | `synthwave` | the deck's own look, and the brand | Chakra Petch (display), Share Tech Mono (body and numbers) | glow on brand, active and status edges; scanlines on the 7" |
| **Ledger** (light professional) | `ledger` | daylight, bright shops, desk work | IBM Plex Sans, with IBM Plex Mono for numbers | none |
| **Graphite** (dark professional) | `graphite` | a dark cabinet or the night shift, without the neon | IBM Plex Sans / Mono | none |
| **Follow system** | `system` | whatever the device says: Ledger when light, Graphite when dark | as above | as above |

The keys are Tech Tracker's existing ones (its database CHECK already allows exactly these
four), so nothing there has to migrate.

### 3.1 One set of roles

Every component uses **roles**, never raw colours. These are cabcrawl's names, the most
complete of the three:

`--surface-page`, `--surface-page-top`, `--surface`, `--surface-raised`, `--surface-inset` ·
`--text`, `--text-dim`, `--text-on-accent`, `--link` · `--border` (decorative),
`--border-control` (field and button edges), `--focus` · `--accent`, `--accent-tint` ·
`--ok`, `--warn`, `--fault`, `--info`, `--muted`, each with a `-tint` · `--font-display`,
`--font-body`, `--font-mono` · `--r` (radius) · `--glow`, `--glow-text`, `--scanline-alpha`.

### 3.2 The values

| role | Synthwave | Ledger | Graphite |
|---|---|---|---|
| surface-page / page-top | `#150a28` / `#1f0f3d` | `#f6f7f9` / `#f6f7f9` | `#0f1216` / `#0f1216` |
| surface / raised / inset | `#1f1240` / `#28184e` / `#150a28` | `#ffffff` / `#eef1f4` / `#f6f7f9` | `#171b21` / `#1f252d` / `#0f1216` |
| text / text-dim | `#ece3ff` / `#a394c4` | `#1f2328` / `#5f6b7a` | `#e5e7eb` / `#8b93a1` |
| link, info, focus | `#7dfaff` | `#0b5f8a` | `#6ea8ff` |
| accent / text-on-accent | `#ff2e9f` / `#150a28` | `#0f6e6e` / `#ffffff` | `#4f8cff` / `#0f1216` |
| ok / warn / fault | `#5ef2b0` / `#ffb74d` / `#ff4d6d` | `#18733a` / `#8f6000` / `#b42318` | `#34c38f` / `#f0b429` / `#ef5d5d` |
| border / border-control | `#3d1e6b` / `#8466bd` | `#dfe3e8` / `#7d8590` | `#2a313b` / `#6b7685` |
| radius | 8 px | 4 px | 4 px |

- Synthwave is the GATBOX palette, with cabcrawl's accessibility lifts (dim text, a control
  edge and a raised surface that pass AA).
- Ledger and Graphite are the Tech Tracker's shipped values with **four changes, each to pass
  AA on a raised surface**:
  - Ledger ok `#1a7f37` → `#18733a` (it was 4.48:1);
  - Ledger warn `#9a6700` → `#8f6000` (4.29:1);
  - a control edge for each, since a 1.3:1 field border can't be seen: Ledger `#7d8590`,
    Graphite `#6b7685`.
- **Checked on 2026-10-28:** every text role is at least 4.5:1 on every surface in all three
  palettes; control edges and focus are at least 3:1; text on each filled status and accent
  colour is at least 5:1.

### 3.3 One shared file

- **`design/themes.css`**: the roles, with one block per theme (`:root[data-theme=…]`), and
  `system` as a `prefers-color-scheme` pair of Ledger and Graphite.
- **`design/check_contrast.py`**: a stdlib test that reads the CSS and checks every rule
  above. It's cabcrawl's test, generalized to all three palettes.
- **`design/CHECKSUMS`.**

These are owned by Gatbox and vendored byte-identical into each app with the same drift test as
the contract and the rules. Each app keeps its own components. Where its old variable names
differ (Gatbox's `--mag` and `--bad`, the Tech Tracker's `--magenta`), it first maps them onto
the roles, so nothing breaks while the components move over.

---

## 4. Where each app keeps the choice

| app | who chooses | stored | default |
|---|---|---|---|
| **Tech Tracker** | each user, Account → Look | `app_user.desk_theme` (exists) | Ledger (as now) |
| **cabcrawl** | each user, Account → Look | `user.theme` (new column, Alembic) | Synthwave |
| **the deck** | nobody: **Synthwave only** (the owner, 2026-10-28) | — | Synthwave |

- **The Tech Tracker on phones (decision T1).** In October the owner chose "phones always
  synthwave". A single philosophy suggests a user's look should apply at every width.
  Recommended: the one setting applies everywhere, and it's still synthwave on a phone for
  anyone who picks synthwave.
- **Pages with no login** (sign-in pages, cabcrawl's coin-door form, the Tech Tracker's `/d/`
  customer links) use the app's default. The `/d/` documents stay print-first.
- **Charts:** each app's on-screen charts read their colours from the roles. The deck's charts
  and rail plots stay as they are (Synthwave).

---

## 5. Work, per repo (phase A2: independent of the data work, any time after A)

- **All three:** `design/` vendored, the contrast test in each suite, and the rules' "shared
  design" section pointing at it.
- **Gatbox: no change to the deck's look** (Synthwave only, hard rule 11 as it is). It owns
  `design/` and its test, and the archive's pages (plan phase D) use the deck's Synthwave.
- **cabcrawl:**
  - the theme blocks over its existing roles, and `user.theme` with a migration (registered in
    `check_schema.py`);
  - IBM Plex vendored with licences (its font test already demands them);
  - screenshots at 390 and 1280 in each theme; the characterization snapshot unchanged.
- **Tech Tracker:**
  - `desk.css`'s themes move onto the shared roles, and out of the desktop media query if T1
    says so;
  - the four corrected colours;
  - screenshots of desktop and phone in each theme.

---

## 6. Decisions for the owner

All decided by the owner, 2026-10-28:

| # | question | decided |
|---|---|---|
| T1 | Should the Tech Tracker's look apply on phones too (today phones are always synthwave)? | **Yes:** one setting per user, at every width. |
| T2 | Theme names | **The shipped ones:** Synthwave, Ledger, Graphite, Follow system. |
| T3 | Defaults | **Tech Tracker Ledger, cabcrawl Synthwave.** The deck is Synthwave only. |
| T4 | The four colour corrections to Ledger and Graphite (§3.2) | **Fix them.** |
