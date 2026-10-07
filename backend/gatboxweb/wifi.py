"""Wi-Fi requests: parse a scanned code, and ask gatbox-wifi to act.

This process cannot change the network. gatbox-web runs DynamicUser with
ProtectSystem=strict and has no D-Bus, so nmcli fails here -- see system.py, which has said
so since 2026-09-28. Everything below writes a request to /var/spool/gatbox-wifi and lets
gatbox-wifi (root, via gatbox-wifi.path) do the work.

**A key written into that spool is the only copy this process keeps.** It is never logged,
never returned by an endpoint, and never put in a scan result -- a scanned Wi-Fi QR *is* the
key, and scan results reach the DEVICES panel.
"""
import json
import os
import time

SPOOL = os.environ.get("GATBOX_WIFI_SPOOL", "/var/spool/gatbox-wifi")
REQUEST = "request.json"
SSID_MAX = 32          # 802.11: an SSID is at most 32 bytes
PREFIX = "WIFI:"

# How long a request may sit unread before it is treated as abandoned rather than waiting.
# Generously longer than a join takes: gatbox-wifi.path fires the moment the file appears, so
# anything still here after this means nothing is going to take it.
REQUEST_TTL = 120

# What a scan result says instead of the code, because the code is the key.
REDACTED = "WIFI:<redacted>"


def _split_unescaped(text, sep):
    """Split on an unescaped separator, leaving the escapes in place for _unescape."""
    parts, cur, i = [], "", 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            cur += text[i:i + 2]
            i += 2
        elif c == sep:
            parts.append(cur)
            cur = ""
            i += 1
        else:
            cur += c
            i += 1
    parts.append(cur)
    return parts


def _unescape(text):
    out, i = "", 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out += text[i + 1]
            i += 2
        else:
            out += text[i]
            i += 1
    return out


def parse(code):
    """``WIFI:T:WPA;S:<ssid>;P:<key>;;`` -> {"ssid", "psk"}, or None if unusable.

    The format both Android and iOS produce from "share this network". A literal ``;``,
    ``:`` or backslash inside a field is backslash-escaped, which is why the fields are
    split before they are unescaped rather than after -- unescaping first would turn an
    escaped separator into a real one and split in the wrong place.
    """
    if not isinstance(code, str) or code[:len(PREFIX)].upper() != PREFIX:
        return None
    fields = {}
    for field in _split_unescaped(code[len(PREFIX):], ";"):
        if not field:
            continue
        # The key is a single letter, so the first colon is always the separator: a key
        # never contains an escape.
        key, sep, value = field.partition(":")
        if sep:
            fields[key.strip().upper()] = value

    ssid = _unescape(fields.get("S", ""))
    if not ssid or len(ssid.encode("utf-8")) > SSID_MAX:
        return None
    # T is optional; its absence means the same as nopass only when there is no key either.
    if fields.get("T", "").lower() == "nopass":
        psk = ""
    else:
        psk = _unescape(fields.get("P", ""))
    return {"ssid": ssid, "psk": psk}


class Busy(Exception):
    """A request is already waiting for gatbox-wifi to take it."""


# What may be read back out of a waiting request. A fixed list, like OUTCOME_FIELDS below,
# and for the same reason: `psk` is in that file and must not leave this function.
PENDING_FIELDS = ("id", "action", "ssid", "at")


def pending():
    """The request waiting for gatbox-wifi, if any -- never its key. Also what clears an
    abandoned one.

    The request file holds a plaintext key until the helper takes it, and the helper only
    runs because gatbox-wifi.path saw the file appear. On a box where that unit is not
    enabled nothing would ever take it, so the key would sit there until someone happened to
    ask for another join. This is polled from the Pi's own screen -- exactly where a stuck
    request is noticed -- so a request older than REQUEST_TTL is swept here, and the sweep
    says so once rather than silently.
    """
    path = os.path.join(SPOOL, REQUEST)
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return None
    d = read_json(REQUEST, {})
    if not isinstance(d, dict):
        d = {}
    out = {k: d.get(k) for k in PENDING_FIELDS if k in d}
    # mtime, not the body's `at`: the body is what a writer claimed, and a request that
    # cannot be parsed has no `at` at all but still holds a key.
    out["stale"] = age > REQUEST_TTL
    if out["stale"]:
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


def request(action, ssid="", psk=""):
    """Leave a request for gatbox-wifi and return its id. 0600: it may hold a key until the
    helper takes it.

    One slot, so a second request while one is waiting used to overwrite the first -- lost
    with nothing said, while the card showed an outcome that answered neither. Raises Busy
    instead. An abandoned request is not a waiting one, and pending() has already swept it.
    """
    os.makedirs(SPOOL, exist_ok=True)
    waiting = pending()
    if waiting and not waiting["stale"]:
        raise Busy(f"a {waiting.get('action') or 'wi-fi'} request is already waiting; "
                   "give it a moment")
    # Identifies the outcome this request produces, so the dashboard can tell the answer to
    # the button just pressed from the one before it.
    rid = os.urandom(8).hex()
    body = {"id": rid, "action": action, "at": time.time()}
    if ssid:
        body["ssid"] = ssid
    if action == "join":
        body["psk"] = psk
    path = os.path.join(SPOOL, REQUEST)
    tmp = path + ".tmp"
    # Opened 0600 from the start, not chmod-ed after: between creation and chmod the key
    # would be readable by the group.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    os.replace(tmp, path)
    return rid


def read_json(name, default=None):
    """One of the helper's output files, or *default*. Never raises."""
    try:
        with open(os.path.join(SPOOL, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def saved():
    """SSIDs we have a profile for. **Names only — there is no endpoint that reads a key.**

    The helper writes this alongside its scan; it cannot be read here, because this process
    has no D-Bus and nmcli fails in it.
    """
    return read_json("saved.json", {}).get("ssids", [])


def in_range():
    """What the helper last saw in range, and when."""
    d = read_json("scan.json", {})
    return {"ssids": d.get("ssids", []), "at": d.get("at")}


# What the API will pass through from the helper's status file. A fixed list, not the file:
# `detail` is nmcli's own free text, and the one field that could ever carry a key. It stays
# in the journal, which is where docs/network.md sends you to diagnose a join, and out of
# every HTTP response. gatbox-wifi already scrubs before writing; this is the second wall,
# and it does not depend on every future writer of that file remembering to.
# `where` joins this list and `detail` still does not: `where` is a network name this code
# put there, while `detail` is nmcli's own text and the one field that could ever carry a key.
OUTCOME_FIELDS = ("id", "at", "action", "ssid", "ok", "state", "where")


def last_outcome():
    """What the helper last did, as a known set of fields rather than whatever is on disk."""
    d = read_json("status.json")
    if not isinstance(d, dict):
        return None
    return {k: d.get(k) for k in OUTCOME_FIELDS if k in d}
