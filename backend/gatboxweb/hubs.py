"""What gatbox-sync knows about the hubs (Phase 8), read-only from its state directory.

gatbox-sync is the only process that talks to a hub and the only writer of its state; this
process cannot read /etc/gatbox and never tries. Everything here comes from what the last run
left behind, so it is as fresh as the last 2-minute run and works when the Pi is offline:

    /var/lib/gatbox-sync/              the default hub (hub.conf), where one hub always kept it
        status.json                    the whole run; "hubs" lists those configured now
        hub.json roster.json orders.json sent.json
    /var/lib/gatbox-sync/hubs/<name>/  each hubs.d hub, the same files

**One hub, or none, is today's box**: no grouping, no conflicts, and the one hub's roster
owns everything it lists. A state directory left behind by a hub since removed from hubs.d is
not a hub: status.json's list says which are configured.

Missing files are normal (a Pi with no hub never runs the job at all), so every read fails
soft to "nothing".
"""
import json
import os
import re

SYNC_STATE = "/var/lib/gatbox-sync"
DEFAULT = "default"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
# A hub's slug becomes a state file here and a "# machine=" line in a CSV header: only the roster's own slug rule
# gets through (roster.SLUG), whole-string, so nothing a hub sends can carry a newline or a path into either.
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def good_slug(x):
    return isinstance(x, str) and len(x) <= 64 and SLUG.fullmatch(x) is not None
PHASES = ("as_found", "as_left")      # what the 7" offers; the hub also takes "during"


def _root():
    return os.environ.get("GATBOX_SYNC_STATE", SYNC_STATE)


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return default
    return d if isinstance(d, dict) else default


def status():
    return _read(os.path.join(_root(), "status.json"), {}) or {}


def _dir(name):
    return _root() if name == DEFAULT else os.path.join(_root(), "hubs", name)


def hubs():
    """[{name, label, capabilities, app, url, reached_at, jobs, machines, roster_at, dir}].

    Configured hubs only: those in status.json's "hubs" list. A sync from before Phase 8 wrote
    no such list and had only the default hub, so that is what an old state reads as."""
    st = status()
    listed = st.get("hubs")
    if isinstance(listed, list):
        names = [h.get("name") for h in listed if isinstance(h, dict)]
        labels = {h.get("name"): h.get("label") for h in listed if isinstance(h, dict)}
    else:
        names, labels = [DEFAULT], {}
    out = []
    for name in names:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            continue
        d = _dir(name)
        info = _read(os.path.join(d, "hub.json"), {}) or {}
        roster = _read(os.path.join(d, "roster.json"), {}) or {}
        if not info and not roster and name == DEFAULT and not isinstance(listed, list):
            continue                       # no hub has ever been reached: nothing at all
        caps = [c for c in info.get("capabilities") or [] if isinstance(c, str)]
        out.append({"name": name, "label": info.get("label") or labels.get(name) or name.upper(),
                    "capabilities": caps, "jobs": "jobs" in caps, "app": info.get("app"),
                    "url": info.get("url"), "reached_at": info.get("reached_at"),
                    "machines": [m for m in roster.get("machines") or []
                                 if isinstance(m, dict) and good_slug(m.get("slug"))],
                    "roster_at": roster.get("at"), "dir": d})
    return out


def owners(hs=None):
    """{slug: [hub, …]}: which configured hubs list each machine."""
    out = {}
    for h in hs if hs is not None else hubs():
        for m in h["machines"]:
            out.setdefault(m["slug"], []).append(h)
    return out


def slugs():
    """Every machine any hub lists. A conflicting one included: it is still a real machine to
    meter, it just goes nowhere until the rosters agree."""
    return set(owners())


def owner(slug, hs=None):
    """(hub or None, conflict labels or None). With one hub, it owns what it lists."""
    who = owners(hs).get(slug) or []
    if len(who) == 1:
        return who[0], None
    if len(who) > 1:
        return None, [h["label"] for h in who]
    return None, None


def machine(slug):
    """The cached roster entry for a slug, with the hub that lists it ({…, "hub": {…}}), or None."""
    hs = hubs()
    who, conflict = owner(slug, hs)
    for h in ([who] if who else (owners(hs).get(slug) or [])):
        for m in h["machines"]:
            if m["slug"] == slug:
                out = {k: m.get(k) for k in ("slug", "name", "status", "location", "model_key")}
                out["hub"] = brief(who) if who else None
                out["conflict"] = conflict
                return out
    return None


def brief(h):
    return {"name": h["name"], "label": h["label"], "jobs": h["jobs"]}


def multi(hs=None):
    return len(hs if hs is not None else hubs()) > 1


def open_orders(slug):
    """What the owning hub has open for this machine, as of the last sync (its orders cache).

    Minutes stale by nature: which is exactly why a hub accepts a tag for an order that has
    since been closed rather than rejecting it."""
    if not slug:
        return []
    h, _ = owner(slug)
    if h is None:
        # Before Phase 8's sync wrote a hub list, the one cache was at the top level.
        if hubs():
            return []
        cached = (_read(os.path.join(_root(), "orders.json"), {}) or {}).get("open") or {}
    else:
        cached = (_read(os.path.join(h["dir"], "orders.json"), {}) or {}).get("open") or {}
    rows = (cached.get(slug) or []) if isinstance(cached, dict) else []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def sent_uids():
    """Every outbox uid any hub has acknowledged: the queued/sent badge is a join."""
    dirs = [h["dir"] for h in hubs()] or [_root()]
    out = set()
    for d in dirs:
        outbox = (_read(os.path.join(d, "sent.json"), {}) or {}).get("outbox")
        if isinstance(outbox, dict):
            out |= set(outbox)
    return out
