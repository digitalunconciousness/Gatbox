"""Measurement profiles, per-machine rail specs, the roster, and the writable state that picks among them.

Data (read-only): profiles.json, gatbox-machine-specs.json, gatbox-barcade-roster.json (the roster is optional:
it's workplace data, git-ignored, and may be missing on a fresh clone). Found in $GATBOX_DATA, else the repo's
data/ when running from a checkout, else /usr/local/share/gatbox.

State (written only by gatbox-web, read by gatbox-raillog via gatbox-meta): profile.json ({"id", "window"}) and
machine (a roster slug) in $GATBOX_STATE, else /var/lib/gatbox-web. Also roster-added.json: the machines added on
the dashboard, merged into roster() (the installed roster file itself is never written).
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


SPECS_CONFIRMED = "machine-specs-confirmed.json"   # state: rail limits CONFIRMed on the dashboard against a manual page
ACTUALS = "machine-actuals.json"                   # state: the owner's own values for a machine, measured in the field


def _state_json(name, sd=None):
    """A gatbox-web state file as a dict ({} when missing, damaged, or not readable here)."""
    try:
        d = _load(os.path.join(sd or state_dir(), name))
    except (OSError, ValueError):
        d = None
    return d if isinstance(d, dict) else {}


def _window(w):
    return (isinstance(w, list) and len(w) == 2 and all(finite(x) for x in w) and w[0] < w[1]) and [float(w[0]), float(w[1])]


def machine_specs(dd=None, sd=None):
    """{slug: {"rails": {rail: [lo, hi]}, "source": ..., "sources": {rail: where that window came from}}}.

    Three layers, each later one winning the rails it has: the specs file (gatbox-machine-specs.json), limits the owner
    CONFIRMed on the dashboard against a manual page, and a window of her own for a rail on that machine (set with an
    actual value from the field). The manual's numbers are never overwritten: each layer keeps its own file, and the
    dashboard shows them side by side."""
    base = (_load(os.path.join(dd or data_dir(), "gatbox-machine-specs.json")) or {}).get("machines", {})
    conf, act = _state_json(SPECS_CONFIRMED, sd), _state_json(ACTUALS, sd)
    if not conf and not act:
        return base
    out = {}
    for slug in set(base) | set(conf) | set(act):
        b = base.get(slug) if isinstance(base.get(slug), dict) else {}
        rails = {r: w for r, w in (b.get("rails") or {}).items() if _window(w)}
        src = {r: "specs file" for r in rails}
        for r, c in (conf.get(slug) or {}).items():
            w = isinstance(c, dict) and _window([c.get("lo"), c.get("hi")])
            if w:
                rails[r], src[r] = w, f"manual: {c.get('source', '')}".strip()
        for r, a in ((act.get(slug) or {}).get("rails") or {}).items():
            w = isinstance(a, dict) and _window(a.get("window"))
            if w:
                rails[r], src[r] = w, "actual"
        if rails:
            out[slug] = dict(b, rails=rails, sources=src)
    return out


ROSTER_FILE = "gatbox-barcade-roster.json"
ADDED_FILE = "roster-added.json"      # in the state dir: machines added on the dashboard (gatbox-web writes it)
FLOOR = ("video_games", "pinball")


def roster_file(dd=None):
    """The installed roster file as it is, or None when it isn't installed (it's git-ignored workplace data)."""
    return _load(os.path.join(dd or data_dir(), ROSTER_FILE))


def roster_added(sd=None):
    """Machines added on the Pi: {"video_games": [...], "pinball": [...]} in the roster's own entry format. Empty when
    there are none or the file can't be read here (a login shell can't see gatbox-web's private state; a damaged file
    is left for gatbox-web to report, never guessed at)."""
    try:
        d = _load(os.path.join(sd or state_dir(), ADDED_FILE))
    except (OSError, ValueError):
        d = None
    d = d if isinstance(d, dict) else {}
    return {k: [e for e in d.get(k, []) if isinstance(e, dict) and e.get("slug")] for k in FLOOR}


def roster(dd=None, sd=None):
    """The roster: the installed file plus the machines added on the Pi, appended to video_games / pinball. The file
    wins a slug both have, and its retired slugs stay retired. None when neither exists. The cached file dict is never
    changed: a merge builds new lists."""
    base, extra = roster_file(dd), roster_added(sd)
    if not any(extra.values()):
        return base
    r = dict(base) if isinstance(base, dict) else {"meta": {}, "video_games": [], "pinball": [], "retired": []}
    taken = {e.get("slug") for k in FLOOR + ("retired",) for e in r.get(k, []) if isinstance(e, dict)}
    for k in FLOOR:
        new = [e for e in extra[k] if e["slug"] not in taken]
        if new:
            r[k] = list(r.get(k, [])) + new
    return r


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
    """The effective profile: window + where it came from and alarm_hi. Window: the machine's own actual window
    (source actual:<slug>) > its spec, from the specs file or a confirmed manual page (machine:<slug>) > the user's
    value > the profile's."""
    meta, modes, P = profiles(dd)
    pid = profile_id if profile_id in P else meta.get("default", "rail-5v")
    p = P.get(pid) or {"id": pid, "label": pid, "kind": "rail", "rail": "+5V", "modes": ["VDC"],
                       "window": [4.75, 5.25], "user_window": False, "alarm_hi": None, "notes": ""}
    window, source = p.get("window"), "profile"
    if user_window and p.get("user_window"):
        window, source = user_window, "user"
    ms = machine_specs(dd).get(machine or "", {})
    spec = ms.get("rails", {}).get(p.get("rail") or "")
    if spec:
        kind = "actual" if (ms.get("sources") or {}).get(p.get("rail")) == "actual" else "machine"
        window, source = [float(spec[0]), float(spec[1])], f"{kind}:{machine}"
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
