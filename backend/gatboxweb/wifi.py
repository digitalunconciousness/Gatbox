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
SSID_MAX = 32          # 802.11: an SSID is at most 32 bytes
PREFIX = "WIFI:"

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


def request(action, ssid="", psk=""):
    """Leave a request for gatbox-wifi. 0600: it may hold a key until the helper takes it."""
    os.makedirs(SPOOL, exist_ok=True)
    body = {"action": action, "at": time.time()}
    if ssid:
        body["ssid"] = ssid
    if action == "join":
        body["psk"] = psk
    path = os.path.join(SPOOL, "request.json")
    tmp = path + ".tmp"
    # Opened 0600 from the start, not chmod-ed after: between creation and chmod the key
    # would be readable by the group.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    os.replace(tmp, path)
    return True


def read_json(name, default=None):
    """One of the helper's output files, or *default*. Never raises."""
    try:
        with open(os.path.join(SPOOL, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default
