# One set of agent rules for the three repos — draft

Status: **draft for the owner's approval, 2026-10-28.** Nothing in any CLAUDE.md has changed yet.

The owner wants one set of agent rules for Gatbox, cabcrawl and ArcadeTech-Tracker. Today each
CLAUDE.md carries its own, and they disagree in ways that block cross-repo work. For example,
the tech tracker's rules call the other two repos "READ-ONLY: never edit, commit, branch or
push in them", and cabcrawl has no privacy check at all.

**Proposal:** the text below becomes `docs/agents/RULES.md`, owned by Gatbox like the contract,
and vendored byte-identical into each repo with a CHECKSUMS file and the same drift test. Each
CLAUDE.md shrinks to one line ("Read `docs/agents/RULES.md` first; it wins where this file
disagrees, except for the facts below") plus that repo's own facts: stack, commands, layout and
repo-specific hard rules.

---

## The proposed RULES.md

### 1. The system

Three repositories, all **public**:

| repo | what it is | its world |
|---|---|---|
| Gatbox | the deck: a Pi 5 bench and diagnostic box (meter, T48, 7" touch screen, scanner) and the owner's archive of game knowledge. Offline-first. | neither: it's the owner's tool, and it talks to each world separately |
| cabcrawl | the floor app for the barcade where the owner works: machines, plays, rotation, maintenance, inventory | the **barcade** |
| ArcadeTech-Tracker (deployed as shop-hub) | the side business: customers, assets, jobs, parts, money | the **side work** |

**The two worlds never talk.** No code, request, file or record goes from cabcrawl to the tech
tracker or back, whether directly or through the deck. The deck keeps each world's data, outbox,
token and state apart, and never sends one world's data to the other. A world's name and data
never appear in the other world's repo.

### 2. Hard rules (every repo)

1. **Nothing personal or site-specific in git:** no names, usernames, emails (only the noreply
   address and reserved domains), home paths, hostnames, LAN addresses, Wi-Fi names, timezones,
   workplace or customer data, real rosters, databases, dumps or logs.
2. **Nothing copyrighted or secret in git:** no manuals (PDF), ROM images or dumps, tokens,
   keys, `.env` files or uploaded files.
3. **Test data is invented:** Widget Wars, `555-01xx` phone numbers, `example.com` / `.test`.
4. **Commit** as `digitalunconciousness <240414701+digitalunconciousness@users.noreply.github.com>`
   with `TZ=UTC git commit …`.
5. **The privacy check runs before every push and blocks it on a finding** (§6). Never push
   without the owner's pattern file present.
6. **Agents never change a host:** not the Pi, an LXC, the Postgres host, Cloudflare or GitHub
   settings. Read-only checks on the machine the session runs on are fine (journalctl, lsusb,
   `minipro -L`). A change is written as exact commands marked ⏸, and then the agent stops.
7. **Never merge, never deploy, never push to `main`,** never force-push someone else's branch.
8. **Don't guess:** hardware facts are read off the device or asked for, spec numbers come
   only from a manual the owner cites, and an owner's decision is asked for, not assumed.
9. **Backward compatible:** printed labels, existing URLs, stored files and data formats keep
   working. Each repo's CLAUDE.md lists its own frozen items.

### 3. How a change flows

1. **Design note first** for anything beyond a small fix: what changes, data, routes, risks,
   tests. Wait for the owner's OK. One note may cover a change that spans several repos.
2. **Code and tests in small commits,** each with a test that fails without it. Use the repo's
   own test commands (in its CLAUDE.md).
3. **Before pushing, show** the diff stat, the test results and the privacy-check output, clean
   or not. UI changes also get screenshots (cabcrawl: 390 and 1280 wide; the deck: the 7"
   layout and a phone).
4. **Push to a branch and open (or update) one PR per repo per change.** The owner reviews,
   merges and deploys.
5. **Say plainly** what was and wasn't checked.

### 4. Working across repos

- A session may change any of the three repos when the task needs it, each through its own
  branch and PR. (This replaces "the reference repos are read-only".)
- **Port patterns; never import code** from another repo.
- Shared files are vendored byte-identical, with a CHECKSUMS file and a test in every repo:
  the contract (`contract/v1/` in the apps, `docs/contract/v1/` in Gatbox) and these rules.
  **Gatbox owns both.** A change to either lands in all three PRs together, and each repo's test
  also diffs its copy against sibling checkouts when they're present.
- A world's contract extension is vendored only into Gatbox and that world's app.

### 5. Shared design

- **One philosophy, four themes:** Synthwave, Ledger, Graphite and Follow system, in
  `design/themes.css`. It's vendored like the contract, owned by Gatbox, and checked by
  `design/check_contrast.py` (`docs/superpowers/specs/2026-10-28-one-look-four-themes.md` until
  it lands).
- **Components use the roles, never a raw colour:** surface, text, accent, ok / warn / fault /
  info, and so on.
- **Colour means status, and every status has a word.** Glow and scanlines are Synthwave only.
  Numbers, tags and hashes are set in monospace.
- **Touch targets:** at least 44 px on phones and 56 px on the 7".
- **Printed labels and customer documents stay print-first.**
- **Fonts are served by each app.** No CDN; plain HTML/CSS/JS, no framework, no build step.

### 6. The privacy check

- **One script, `scripts/privacy_check.sh`, identical in all three repos** (vendored with these
  rules), run by `.githooks/pre-push` (`git config core.hooksPath .githooks`). It checks each
  commit in the range: author and committer email, UTC timestamps, forbidden paths (PDF, ROM,
  databases, `.env`, keys), secret shapes, the current login name and home path, and the
  owner's own patterns.
- **The owner's patterns live outside git** in `~/.config/gatbox-family/patterns.local`, one
  extended regex per line. A cloud session's setup script writes that file from the environment
  variable `PRIVACY_PATTERNS`. Missing file: the check says so and the push is refused.

### 7. Talking to the owner

She's a professional arcade tech, fluent in Linux, systemd, Postgres and git. Be
direct, flag trade-offs, skip preamble, keep narration short, and show command output when it
matters.

---

## What changes from today's rules

| repo | today | under the shared rules |
|---|---|---|
| all | three CLAUDE.md files with overlapping, slightly different rules | one vendored RULES.md, plus each repo's facts |
| ArcadeTech-Tracker | the other repos are read-only references; "one phase per session"; "the owner says when to push" | cross-repo sessions allowed; one change per PR; push to a branch once tests and the privacy check pass, with the owner's gates at the design and at merge |
| ArcadeTech-Tracker | patterns in `~/.config/shop-hub/patterns.local`, written from `SHOP_PATTERNS` | one location and variable for all three |
| cabcrawl | no pre-push privacy check; "wait for the owner's go after each step" | the shared hook and check; the go comes at the design and the merge, with screenshots still shown before every push |
| cabcrawl | owns contract v1 | Gatbox owns it; cabcrawl vendors it |
| Gatbox | its hook reads `.githooks/patterns.local` | the shared location (the old path still works) |

**For the owner to decide:**
1. Is the push gate "the owner says when" (today's tech-tracker rule), or "push to a branch once
   checks pass; the owner gates design and merge"? Recommended: the second, so a cross-repo
   change can reach three PRs in one session.
2. Is the patterns directory name above acceptable? Any neutral name that isn't personal works.
