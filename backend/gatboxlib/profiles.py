"""Measurement profiles, per-machine rail specs, the roster, and the writable state that picks among them.

Data (read-only): profiles.json, gatbox-machine-specs.json, gatbox-barcade-roster.json (the roster is optional:
it's workplace data, git-ignored, and may be missing on a fresh clone). Found in $GATBOX_DATA, else the repo's
data/ when running from a checkout, else /usr/local/share/gatbox.

State (written only by gatbox-web, read by gatbox-raillog via gatbox-meta): profile.json ({"id", "window"}) and
machine (a roster slug) in $GATBOX_STATE, else /var/lib/gatbox-web.
"""
import json, math, os

STATE_DEFAULT = "/var/lib/gatbox-web"


def data_dir():
    env = os.environ.get("GATBOX_DATA")
    if env:
        return env
    repo = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "data"))
    return repo if os.path.isfile(os.path.join(repo, "profiles.json")) else "/usr/local/share/gatbox"


def state_dir():
    return os.environ.get("GATBOX_STATE", STATE_DEFAULT)


_cache = {}


def _load(path):
    """JSON file, re-read only when it changes on disk. None if it's missing."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_size, st.st_mtime_ns)
    hit = _cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        val = json.load(f)
    _cache[path] = (key, val)
    return val


def profiles(dd=None):
    """(meta, modes, {id: profile}) from profiles.json."""
    d = _load(os.path.join(dd or data_dir(), "profiles.json")) or {"meta": {}, "modes": {}, "profiles": []}
    return d.get("meta", {}), d.get("modes", {}), {p["id"]: p for p in d.get("profiles", [])}


def machine_specs(dd=None):
    return (_load(os.path.join(dd or data_dir(), "gatbox-machine-specs.json")) or {}).get("machines", {})


def roster(dd=None):
    """The roster dict, or None when it isn't installed (fresh clone: it's git-ignored workplace data)."""
    return _load(os.path.join(dd or data_dir(), "gatbox-barcade-roster.json"))


def roster_slugs(dd=None):
    r = roster(dd)
    if r is None:
        return None
    return {e["slug"] for k in ("video_games", "pinball") for e in r.get(k, []) if e.get("slug")}


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def read_state(sd=None):
    """(profile id or None, user window [lo, hi] or None, machine slug or None) from the writable state."""
    sd = sd or state_dir()
    pid = win = machine = None
    try:
        with open(os.path.join(sd, "profile.json"), encoding="utf-8") as f:
            st = json.load(f)
        pid = st.get("id")
        w = st.get("window")
        if isinstance(w, list) and len(w) == 2 and all(finite(x) for x in w) and w[0] < w[1]:
            win = [float(w[0]), float(w[1])]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(os.path.join(sd, "machine"), encoding="utf-8") as f:
            machine = f.read().strip() or None
    except OSError:
        pass
    return pid, win, machine


def resolve(profile_id=None, user_window=None, machine=None, dd=None):
    """The effective profile: window + where it came from (machine spec > user > profile) and alarm_hi."""
    meta, modes, P = profiles(dd)
    pid = profile_id if profile_id in P else meta.get("default", "rail-5v")
    p = P.get(pid) or {"id": pid, "label": pid, "kind": "rail", "rail": "+5V", "modes": ["VDC"],
                       "window": [4.75, 5.25], "user_window": False, "alarm_hi": None, "notes": ""}
    window, source = p.get("window"), "profile"
    if user_window and p.get("user_window"):
        window, source = user_window, "user"
    spec = machine_specs(dd).get(machine or "", {}).get("rails", {}).get(p.get("rail") or "")
    if spec:
        window, source = [float(spec[0]), float(spec[1])], f"machine:{machine}"
    alarm = p.get("alarm_hi")
    if alarm is None and p.get("kind") == "rail" and window:
        alarm = round(max(abs(window[0]), abs(window[1])) * 1.10, 3)
    return {"id": pid, "label": p.get("label", pid), "kind": p.get("kind", "bench"), "rail": p.get("rail"),
            "modes": p.get("modes", []), "window": window, "source": source, "alarm_hi": alarm,
            "user_window": p.get("user_window", False), "jack": bool(p.get("jack")), "notes": p.get("notes", ""),
            "machine": machine}


def header_lines(res):
    """The session header lines gatbox-raillog writes after '# clock=' and '# mode='."""
    w = res["window"]
    out = [f"# profile={res['id']}",
           f"# window={w[0]:g}..{w[1]:g} source={res['source']}" if w else f"# window=none source={res['source']}",
           f"# alarm_hi={res['alarm_hi']:g}" if res["alarm_hi"] is not None else "# alarm_hi=none"]
    if res.get("machine"):
        out.append(f"# machine={res['machine']}")
    return out
