"""The meter right now, and the state that says what it's measuring.

gatbox-web is the only writer of this state (in $STATE_DIRECTORY, /var/lib/gatbox-web); gatbox-raillog (root)
reads it through gatbox-meta when it starts a file, and polls the flags:

    profile.json    {"id": profile, "window": [lo, hi] (the selected profile's user values, or null),
                     "windows": {profile: [lo, hi]}: user values remembered per profile}
    machine         a roster slug
    alarm.json      {"on": false} = ALARM OFF (probing odd voltages); missing = on. Picking another profile turns
                    it back on, so an overnight log never starts with the alarm silenced by yesterday's probing.
    marks.spool     epoch<TAB>uptime_s<TAB>source<TAB>label, appended; the logger moves them into the live file
    stopped         exists = don't log (Stop)
    start-request   newer than the live session = end it, start a new file (Start / NEW; also a profile change or
                    a different machine while a session is live: one file = one machine + one profile + one mode)
"""
import json
import math
import os
import re
import threading
import time

from gatboxlib import profiles

from . import config, roster, wifi
from .live import LIVE, uptime

LABEL_MAX = 40
MARK_SOURCES = {"dashboard", "phone", "scan"}
_lock = threading.Lock()


class Bad(Exception):
    """A request the API refuses: (HTTP status, message)."""
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _path(name):
    return os.path.join(config.CTRL, name)


def _write(name, text):
    """Atomic write into the state dir (the logger may read it at any moment)."""
    os.makedirs(config.CTRL, exist_ok=True)
    tmp = _path(f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, _path(name))


def label(x, required=False):
    """≤ 40 printable characters (no tabs or newlines: the spool and CSVs are line/tab based)."""
    if x is None or x == "":
        if required:
            raise Bad(400, "label is required")
        return ""
    if not isinstance(x, str):
        raise Bad(400, "label must be text")
    x = x.strip()
    if len(x) > LABEL_MAX or not x.isprintable():
        raise Bad(400, f"label: at most {LABEL_MAX} printable characters")
    if required and not x:
        raise Bad(400, "label is required")
    return x


def number(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 1e9:
        raise Bad(400, f"{what}: must be a finite number")
    return float(x)


def stopped():
    return os.path.exists(_path("stopped"))


def request_start():
    """Start / NEW: end the live session (if any) and start a new file; also undoes Stop."""
    _write("start-request", time.strftime("%Y-%m-%dT%H:%M:%S\n"))
    try:
        os.remove(_path("stopped"))
    except FileNotFoundError:
        pass


def stop():
    os.makedirs(config.CTRL, exist_ok=True)
    open(_path("stopped"), "w").close()


def _new_file_if_live():
    """A change to what the live session is about: the logger starts a new file (not while stopped)."""
    if LIVE.logging() and not stopped():
        _write("start-request", time.strftime("%Y-%m-%dT%H:%M:%S\n"))
        return True
    return False


def spec_change(fn):
    """Run fn (a confirmed manual limit, an actual value) and, when that changes what the live file's header says
    (its window), start a new file, like a profile change. Returns (fn's result, new file started)."""
    with _lock:
        before = profiles.header_lines(resolved())
        out = fn()
        return out, profiles.header_lines(resolved()) != before and _new_file_if_live()


def _profile_state():
    try:
        with open(_path("profile.json"), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def alarm_on():
    try:
        with open(_path("alarm.json"), encoding="utf-8") as f:
            return json.load(f).get("on", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def resolved():
    pid, win, machine = profiles.read_state()
    return profiles.resolve(pid, win, machine)


def state(full=True):
    """What's selected: GET /api/meter/profile (full: plus the profiles and modes tables), and the 'state' SSE
    event (not full). selected = null means nothing was ever picked: the default profile is in use."""
    _, modes, P = profiles.profiles()
    res = resolved()
    st = _profile_state()
    out = {"profile": res, "selected": st.get("id") if st.get("id") in P else None,
           "user_windows": st.get("windows") or {},
           "alarm": {"on": alarm_on(), "applies": res["kind"] == "rail" and res["alarm_hi"] is not None,
                     "limit": res["alarm_hi"]},
           "machine": res["machine"], "machine_name": roster.name(res["machine"]) if res["machine"] else None,
           "stopped": stopped()}
    if full:
        out.update(profiles=[dict(p) for p in P.values()], modes=modes)
    return out


def set_profile(body):
    """PUT /api/meter/profile {"id", "window": [lo, hi] | null} or {"id", "ceiling": x} (base units)."""
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    _, _, P = profiles.profiles()
    pid = body.get("id")
    if pid not in P:
        raise Bad(400, f"unknown profile: {pid!r}" if isinstance(pid, str) else "id: a profile id from profiles.json")
    kind = P[pid].get("user_window")
    with _lock:
        before = profiles.header_lines(resolved())
        st = _profile_state()
        windows = st.get("windows") if isinstance(st.get("windows"), dict) else {}
        if "ceiling" in body:
            if kind != "ceiling":
                raise Bad(400, f"{pid} has no ceiling to set")
            c = number(body["ceiling"], "ceiling")
            if c <= 0:
                raise Bad(400, "ceiling: must be above 0")
            windows[pid] = [0.0, c]
        elif "window" in body:
            w = body["window"]
            if w is None:
                windows.pop(pid, None)
            elif kind != "range":
                raise Bad(400, f"{pid} has no window to set" + (" (it takes a ceiling)" if kind == "ceiling" else ""))
            elif not isinstance(w, list) or len(w) != 2:
                raise Bad(400, "window: [lo, hi]")
            else:
                lo, hi = number(w[0], "window lo"), number(w[1], "window hi")
                if lo >= hi:
                    raise Bad(400, "window: lo must be below hi")
                windows[pid] = [lo, hi]
        changed_id = st.get("id") != pid
        _write("profile.json", json.dumps({"id": pid, "window": windows.get(pid), "windows": windows}) + "\n")
        if changed_id:
            try:
                os.remove(_path("alarm.json"))             # a new profile starts with the alarm on
            except FileNotFoundError:
                pass
        new_file = profiles.header_lines(resolved()) != before and _new_file_if_live()
    return new_file


def set_alarm(body):
    """PUT /api/meter/alarm {"on": true|false}: the ALARM ON/OFF switch."""
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        raise Bad(400, 'expected {"on": true|false}')
    with _lock:
        if body["on"]:
            try:
                os.remove(_path("alarm.json"))
            except FileNotFoundError:
                pass
        else:
            _write("alarm.json", '{"on": false}\n')


def set_machine(slug):
    """PUT /api/machine {"slug"}; None = DELETE. Returns (changed, new file started)."""
    if slug is not None:
        known = roster.slugs()
        if known is None:
            raise Bad(503, "no roster installed (data/gatbox-barcade-roster.json)")
        if not isinstance(slug, str) or slug not in known:
            raise Bad(404, f"not in the roster: {slug!r}")
    with _lock:
        _, _, cur = profiles.read_state()
        if cur == slug:
            return False, False                             # re-scanning the same machine does nothing
        if slug is None:
            os.remove(_path("machine"))
        else:
            _write("machine", slug + "\n")
        return True, _new_file_if_live()


def add_mark(body):
    """POST /api/mark {"source", "label"?}: into the spool now, into the live file at its next sample."""
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    src = body.get("source")
    if src not in MARK_SOURCES:
        raise Bad(400, f"source: one of {', '.join(sorted(MARK_SOURCES))}")
    lab = label(body.get("label"))
    e, up = time.time(), uptime()
    line = f"{e:.3f}\t{up:.2f}\t{src}\t{lab}\n"
    with _lock:
        os.makedirs(config.CTRL, exist_ok=True)
        with open(_path("marks.spool"), "a", encoding="utf-8") as f:
            f.write(line)                                   # one write: the logger never sees half a line
    live = LIVE.logging()
    return {"iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(e)), "epoch": round(e, 3), "up": round(up, 2),
            "source": src, "label": lab, "file": LIVE.name if live else None, "pending": not live}


# Matches gatbox-scand's MAX_LEN. They disagreed -- the daemon buffered 512 and this refused
# over 200 -- so a code in between was read off the scanner and then thrown away with a 400.
# A Wi-Fi QR reaches it: the format escapes \ ; , : and " in *both* fields, so a 32-byte SSID
# and a 63-character passphrase of those become 64 and 126, and the payload is 208.
SCAN_MAX = 512
SCAN_MARK, SCAN_NEW = "GATBOX:MARK", "GATBOX:NEW"

# The hub prints cabinet labels whose QR encodes a URL, not a bare slug: <base>/g/<slug>. The host is deliberately
# not checked -- the label may carry a LAN address, a tunnel hostname or neither, and which one is site data that
# does not belong in this repository. The slug is what means something here, so that is all we read.
LABEL_URL = re.compile(r"^(?:https?://[^/?#\s]+)?/g/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/?(?:[?#].*)?$", re.I)


def scanned_slug(code):
    """The roster slug a scanned code is asking for: the code itself, or the one inside a printed label's URL.

    Returns (slug, from_label). Case is folded because a scanner on caps lock still has to work."""
    m = LABEL_URL.match(code)
    return (m.group(1).lower(), True) if m else (code.lower(), False)


def scan(code):
    """POST /api/scan (from gatbox-scand): what a scanned code means. Returns {"action", "code", …}:
    machine / same-machine (a roster slug, or a hub label's /g/<slug> URL), mark (GATBOX:MARK),
    new (GATBOX:NEW), unknown (anything else)."""
    if not isinstance(code, str):
        raise Bad(400, 'expected {"code": "<text>"}')
    code = code.strip()
    if not code or len(code) > SCAN_MAX or not code.isprintable():
        raise Bad(400, f"code: 1 to {SCAN_MAX} printable characters")
    up = code.upper()
    # A Wi-Fi QR. The code *is* the key, so neither it nor any part of it goes into the
    # result: this reaches the DEVICES panel's recent-scans list.
    if up.startswith(wifi.PREFIX):
        parsed = wifi.parse(code)
        if parsed is None:
            return {"action": "unknown", "code": wifi.REDACTED,
                    "detail": "not a usable Wi-Fi code"}
        try:
            wifi.request("join", parsed["ssid"], parsed["psk"])
        except wifi.Busy as exc:
            # The spool holds one request. Scanning a second code while the first is still
            # waiting used to escape as a 500 -- which is what someone sees after a join
            # that has not been taken yet, exactly when they are trying again. Say so
            # instead, and never echo the code.
            return {"action": "unknown", "code": wifi.REDACTED, "detail": str(exc)}
        return {"action": "wifi", "code": wifi.REDACTED, "ssid": parsed["ssid"]}
    if up == SCAN_MARK:
        return {"action": "mark", "code": code, "mark": add_mark({"source": "scan"})}
    if up == SCAN_NEW:
        request_start()
        return {"action": "new", "code": code, "logging": LIVE.logging()}
    slug, from_label = scanned_slug(code)
    known = roster.slugs()
    if known and slug in known:
        changed, new_file = set_machine(slug)
        return {"action": "machine" if changed else "same-machine", "code": code, "slug": slug,
                "name": roster.name(slug), "new_file": new_file, "via_label": from_label}
    if known is None:
        detail = "no roster installed"
    elif from_label:
        # Worth saying apart: the label scanned cleanly and the machine is simply not in this roster yet,
        # which is a roster to export again, not a bad scan.
        detail = f"a hub label for {slug!r}, which is not in this roster"
    else:
        detail = "not a roster slug or a GATBOX: command"
    return {"action": "unknown", "code": code, "detail": detail}


def pending_marks():
    """Spool lines the logger hasn't taken yet (no live session): they go into the next file as mark-before-start."""
    try:
        with open(os.path.join(config.RUNDIR, "marks-seen")) as f:
            seen = int(f.read().strip() or 0)
        with open(_path("marks.spool"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for ln in lines[seen:]:
        p = ln.split("\t", 3)
        if len(p) == 4:
            try:
                e = float(p[0])
            except ValueError:
                continue
            out.append({"iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(e)), "epoch": e,
                        "source": p[2], "label": p[3], "pending": True})
    return out


def _read_run(name):
    try:
        with open(os.path.join(config.RUNDIR, name)) as f:
            return f.read().strip() or None
    except OSError:
        return None


def snapshot():
    """GET /api/meter."""
    _, modes, _ = profiles.profiles()
    res = resolved()
    L = LIVE.snapshot()
    age = LIVE.age()
    logging = bool(L["file"]) and age is not None and age < config.LIVE_S
    last = L["last"] if logging else None
    key = _read_run("mode") if logging else None      # the dial right now (also between a turn and its new file)
    key = key if key in modes else (last["mode"] if last else None)
    dial_ok = None
    if key:
        dial_ok = not res["modes"] or key in res["modes"]
    reversed_ = False
    if last and dial_ok and res["kind"] == "rail" and res["rail"] and last["v"] is not None and abs(last["v"]) >= 0.5:
        reversed_ = (last["v"] < 0) != res["rail"].startswith("-")   # a +5V profile reading -5 V, or the reverse
    slug = res["machine"]
    return {
        "logging": logging, "stopped": stopped(), "file": L["file"], "age_s": age,
        "mode": key, "mode_label": modes.get(key, {}).get("label") if key else None,
        "dial": modes.get(key, {}).get("dial") if key else None,
        "value": last["v"] if last else None, "ol": last["ol"] if last else False,
        "display": last["display"] if last else None, "raw": last["raw"] if last else None,
        "unit": last["unit"] if last else None, "base": last["base"] if last else None,
        "flags": last["flags"] if last else [], "warn": last["warn"] if last else [],
        "iso": last["iso"] if last else None, "epoch": last["epoch"] if last else None,
        "profile": res, "window": res["window"], "window_source": res["source"],
        "expected": [{"key": k, **modes.get(k, {})} for k in res["modes"]],
        "dial_ok": dial_ok, "leads_reversed": reversed_,
        "machine": slug, "machine_name": roster.name(slug),
        "alarm": {"on": alarm_on(), "applies": res["kind"] == "rail" and res["alarm_hi"] is not None,
                  "limit": res["alarm_hi"], "state": last["alarm"] if last else None,
                  "open": L["open_alarm"], "events": L["alarms"]},
        "session": {"file": L["file"], "header": L["header"], "marks": L["marks"], "ended": L["ended"]},
        "marks_pending": pending_marks(),
        "last_error": _read_run("last-error") if not logging else None,
    }
