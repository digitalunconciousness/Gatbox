"""The order outbox: maintenance orders raised at the bench, waiting to go to the hub.

Phase 6. `gatbox-sync` cannot read this process's files -- it runs as its own user under
ProtectSystem=strict -- and it already derives its session queue from `GET /api/rail/sessions`
rather than from the filesystem. So orders follow the same grain: this process owns them, and
`gatbox-sync` fetches them over HTTP. **No new unit, user, spool or group.**

Two kinds live in the one outbox, because one sender drains it:

    order        a new work order for the machine currently set
    session_tag  a trace attached to an order the hub already has

**An order records the session's *file name*, never a session uid.** The uid is
`sha256(f"{device}|rail_session|{file}")[:32]` and the device id comes from the hub token,
which `gatbox-sync` receives through LoadCredential and this process deliberately cannot read.
So `gatbox-sync` does the translation at send time.

The `uid` here is minted once, when the entry is written, and never again: it is what makes
*sending* idempotent. Pressing the button twice is two orders, which is correct -- two faults
on one machine are two orders.
"""
import json
import os
import time
import uuid

from . import config, meter, system

OUTBOX = "orders.json"
# What the tracker accepts; its own forms offer the same five.
PRIORITIES = ("Low", "Medium", "High", "Critical", "Urgent")
ISSUE_MAX = 2000
NOTE_MAX = 200


def _path():
    return os.path.join(config.CTRL, OUTBOX)


def _load():
    try:
        with open(_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = d.get("entries") if isinstance(d, dict) else None
    return entries if isinstance(entries, list) else []


def _save(entries):
    os.makedirs(config.CTRL, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"entries": entries}, fh, indent=1, sort_keys=True)
    os.replace(tmp, _path())


def _sent_uids():
    """What `gatbox-sync` has already handed to the hub.

    Read-only, from its state directory: it is mode 0755 and gatbox-web.service already has
    ReadOnlyPaths for it because the SYSTEM panel's Hub tile reads the same place. So the
    queued/sent badge is a join, and no write crosses the boundary in either direction.
    """
    outbox = _sync_state("sent.json", {}).get("outbox")
    return set(outbox) if isinstance(outbox, dict) else set()


def _sync_state(name, default):
    """One of gatbox-sync's cache files. Read-only, and missing is normal: a Pi with no
    /etc/gatbox/hub.conf never runs that job at all."""
    state = os.environ.get("GATBOX_SYNC_STATE", system.SYNC_STATE)
    try:
        with open(os.path.join(state, name), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return default
    return d if isinstance(d, dict) else default


def hub_open(slug):
    """What the hub already has open for this machine, as of the last sync.

    So the card can offer to attach a trace to an order that exists instead of filing a
    second one for the same fault. Minutes stale by nature -- which is exactly why the hub
    accepts a tag for an order that has since been closed rather than rejecting it.
    """
    if not slug:
        return []
    cached = _sync_state("orders.json", {}).get("open") or {}
    rows = cached.get(slug) or []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def listing():
    """Every entry, each with `queued` or `sent`, and what the hub has open."""
    sent = _sent_uids()
    out = []
    for e in _load():
        out.append({**e, "state": "sent" if e.get("uid") in sent else "queued"})
    slug = meter.resolved()["machine"]
    return {"orders": out, "machine": slug, "hub_open": hub_open(slug)}


class Bad(Exception):
    """Refused, with the reason the caller should be shown."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def _text(body, field, limit, required=True):
    value = body.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Bad(400, f"{field}: required")
    value = value.strip()
    if len(value) > limit:
        raise Bad(400, f"{field}: at most {limit} characters")
    return value


def add(body):
    """Raise an order for the machine currently set. Returns the entry."""
    if not isinstance(body, dict):
        raise Bad(400, 'expected {"issue": "...", "priority": "...", "attach": true}')
    slug = meter.resolved()["machine"]
    if not slug:
        # Without a machine the hub has nothing to file against, and guessing would file it
        # against whatever was metered last.
        raise Bad(400, "no machine set: PICK MACHINE or scan its code first")
    issue = _text(body, "issue", ISSUE_MAX)
    priority = body.get("priority") or "Medium"
    if priority not in PRIORITIES:
        raise Bad(400, f"priority: one of {', '.join(PRIORITIES)}")

    session_file = None
    if body.get("attach"):
        session_file = _last_complete_session()
        if session_file is None:
            raise Bad(400, "nothing to attach: no finished session for this machine yet")

    entry = {"uid": uuid.uuid4().hex, "kind": "order", "created": round(time.time(), 3),
             "machine": slug, "issue": issue, "priority": priority}
    if session_file:
        entry["session_file"] = session_file
    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry


def tag(body):
    """Attach a finished session to an order the hub already has."""
    if not isinstance(body, dict):
        raise Bad(400, 'expected {"order": <id>, "session_file": "...", "note": "..."}')
    order = body.get("order")
    if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
        raise Bad(400, "order: the hub's id for the record")
    # Named, or "the last finished one" -- which is what the 7" means by ATTACH LAST TRACE,
    # and it cannot name the file itself without listing every session to find it.
    session_file = _text(body, "session_file", 120, required=False)
    if session_file is None:
        session_file = _last_complete_session()
        if session_file is None:
            raise Bad(400, "nothing to attach: no finished session on this Pi yet")
    elif session_file not in _complete_sessions():
        raise Bad(400, f"session_file: no finished session named {session_file!r}")
    note = _text(body, "note", NOTE_MAX, required=False)

    for e in _load():
        if (e.get("kind") == "session_tag" and e.get("order") == order
                and e.get("session_file") == session_file):
            # The hub would answer "duplicate" anyway -- the pair is its natural key -- but
            # saying so here is what lets the card tell someone they already did this.
            raise Bad(409, "that trace is already queued for that order")

    entry = {"uid": uuid.uuid4().hex, "kind": "session_tag",
             "created": round(time.time(), 3), "order": order,
             "session_file": session_file}
    if note:
        entry["note"] = note
    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry


def cancel(uid):
    """Drop a queued entry. The only undo there will ever be: once the hub has an order
    there is no API to withdraw it."""
    sent = _sent_uids()
    entries = _load()
    keep = [e for e in entries if e.get("uid") != uid]
    if len(keep) == len(entries):
        raise Bad(404, "no queued order with that id")
    if uid in sent:
        raise Bad(409, "that one has already gone to the hub; close it there instead")
    _save(keep)
    return {"uid": uid, "cancelled": True}


def _complete_sessions():
    """Finished session files, newest first.

    Never the live one. Contract v1 forbids sending a session that is still being written: a
    partial trace downsamples into different buckets from the finished one, so the same
    reading would hash to a different uid in each and the duplicate check would miss.

    `sessions.names()` rather than `api_list()` -- the latter runs the report for every
    session on the Pi, which is a lot of work to answer "what files are there".
    """
    from . import sessions
    from .live import LIVE

    live = LIVE.name if LIVE.logging() else None
    return [n for n in sessions.names() if n != live]


def _last_complete_session():
    files = _complete_sessions()
    return files[0] if files else None
