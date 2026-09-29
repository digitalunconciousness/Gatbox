"""The session files in LOGDIR: names, a quick per-file summary (the phone list), and /api/rail/sessions."""
import math
import os
import threading
import time

from gatboxlib import csvlog
from gatboxlib.modes import mode

from . import config

_cache, _cache_lock = {}, threading.Lock()   # name -> ((size, mtime_ns), summary): finished files never change


def names():
    """Session file names, newest first."""
    try:
        return sorted((n for n in os.listdir(config.LOGDIR) if config.NAME.match(n)), reverse=True)
    except OSError:
        return []


def summary(name, t0=None, t1=None):
    """First/last sample, the header's clock/profile/window, and per meter mode the sample count and cheap min/max
    in base units (V, Ω, ...), without running the full report. Optionally only the samples from t0 to t1."""
    path = config.log_path(name)
    st = os.stat(path)
    key = (st.st_size, st.st_mtime_ns)
    if not (t0 or t1):
        with _cache_lock:
            hit = _cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
    first = last = None
    modes, warn, n = {}, set(), 0
    hdr = {"clock": None, "clock_note": None, "clock_sync": None, "profile": None, "window": None,
           "window_source": None, "machine": None, "mode": None}
    with open(path, errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                c = line[1:].strip()
                k, _, v = c.partition("=")
                if k == "clock":                           # ntp | rtc (...) | unverified (...)
                    hdr["clock_note"], hdr["clock"] = c, v.split("(")[0].strip()
                elif k == "clock-sync":                    # NTP arrived mid-session
                    hdr["clock_sync"] = v.rsplit(" at ", 1)[-1]
                elif k in ("profile", "machine", "mode"):
                    hdr[k] = v.strip() or None
                elif k == "window":
                    rng, _, src = v.partition(" source=")
                    lo, sep, hi = rng.partition("..")
                    try:
                        hdr["window"] = [float(lo), float(hi)] if sep else None
                    except ValueError:
                        hdr["window"] = None
                    hdr["window_source"] = src.strip() or None
                continue
            p = line.rstrip("\n").replace("\0", "").split(",")
            if len(p) < 4 or p[0] == "iso_time":
                continue
            if (t0 and p[0] < t0) or (t1 and p[0] > t1):
                continue
            m = mode(p[3], p[4] if len(p) > 4 else "")
            try:
                e, v = float(p[1]), float(p[2]) * m.scale
            except ValueError:
                continue
            n += 1
            if first is None:
                first = (p[0], e)
            last = (p[0], e, m, p[2])
            s = modes.setdefault(m.label, [0, float("inf"), float("-inf"), m.base, m.key])
            s[0] += 1
            if math.isfinite(v):
                s[1], s[2] = min(s[1], v), max(s[2], v)
            warn |= m.warn
    out = dict(first=first, last=last, n=n, modes=modes, warn=warn, **hdr)
    if not (t0 or t1):
        with _cache_lock:
            _cache[name] = (key, out)
    return out


def is_live(s, now=None):
    return bool(s["last"]) and (now or time.time()) - s["last"][1] < config.LIVE_S


def api_list(limit=100):
    """GET /api/rail/sessions: newest first, the report's own numbers (no plot, the file's header window)."""
    from . import report
    out, now = [], time.time()
    for name in names()[:limit]:
        s = summary(name)
        if not s["first"]:
            continue
        from .live import LIVE
        live = name == LIVE.name and LIVE.logging()             # the file being written now, not just a recent one
        J = report.summary(name, live=live)
        ov = J.get("over_voltage") or []
        pw = J.get("powered")
        sess = J.get("session") or {}
        prof = J.get("profile") or {}
        keys = [m["key"] for m in J.get("modes", [])] or [v[4] for v in s["modes"].values()]
        out.append({
            "file": name, "live": live,
            "start": sess.get("start", s["first"][0]), "end": sess.get("end", s["last"][0]),
            "duration_s": sess.get("duration_s", round(s["last"][1] - s["first"][1], 2)),
            "samples": sess.get("samples", s["n"]),
            "mode": J.get("mode_key") or (keys[0] if len(keys) == 1 else None),
            "modes": [{"key": m["key"], "label": m["label"], "samples": m["samples"]} for m in J.get("modes", [])],
            "profile": {"id": prof.get("id"), "label": prof.get("label"), "kind": prof.get("kind"),
                        "from_header": prof.get("from_header")} if prof else None,
            "machine": J.get("machine"),
            "window": J.get("window"), "alarm_hi": J.get("alarm_hi"),
            "clock": {"source": s["clock"], "ntp_from": s["clock_sync"]},
            "powered": ({"min": pw.get("min"), "max": pw.get("max"), "mean": pw.get("mean"),
                         "in_window_pct": pw.get("in_window_pct"), "readings": pw.get("readings")}
                        if isinstance(pw, dict) else None),
            "ov": len(ov), "ov_suspect": sum(1 for o in ov if o.get("suspect")),
            "power_cycles": len(J.get("power_cycles") or []),
            "marks": len(J.get("marks") or []),
            "warnings": [w["flag"] for w in J.get("warnings") or []],
            "error": J.get("error"),
        })
    return out


def samples(name, t0=None, t1=None, max_points=2000):
    """GET /api/rail/samples/<file>: the readings for a chart, downsampled to about max_points. Each bucket keeps its
    lowest and highest reading (in time order), so a one-sample spike or dropout survives; OL stays as v null."""
    from .live import alarm_states, header_dict, limits
    h, rows, marks = csvlog.read(config.log_path(name), t0, t1)
    lim, modes = limits(h)
    states = alarm_states(rows, lim, modes)
    idx = list(range(len(rows)))
    if len(rows) > max_points:
        per = len(rows) / (max_points // 2)
        keep = []
        for b in range(max_points // 2):
            chunk = idx[int(b * per):int((b + 1) * per)]
            if not chunk:
                continue
            fin = [i for i in chunk if math.isfinite(rows[i].v)]
            pick = {min(fin, key=lambda i: rows[i].v), max(fin, key=lambda i: rows[i].v)} if fin else {chunk[0]}
            pick |= {i for i in chunk if states[i] in ("spike", "alarm")}          # never drop an over-limit reading
            if len(fin) < len(chunk):
                pick.add(next(i for i in chunk if not math.isfinite(rows[i].v)))  # and show that OL happened
            keep.extend(sorted(pick))
        idx = keep
    return {"file": name, "header": header_dict(h), "alarm_hi": lim, "alarm_modes": sorted(modes),
            "n": len(rows), "downsampled": len(idx) < len(rows),
            "fields": ["epoch", "up", "raw", "unit", "v", "mode", "alarm"],
            "rows": [[rows[i].epoch, rows[i].up, raw_value(rows[i]), rows[i].unit,
                      rows[i].v if math.isfinite(rows[i].v) else None, rows[i].mode.key, states[i]] for i in idx],
            "marks": [m._asdict() for m in marks]}


def raw_value(r):
    """The value as the meter sent it (the CSV's text is gone after parsing: rebuild it from v and the unit)."""
    if not math.isfinite(r.v):
        return "inf" if r.v > 0 else ("-inf" if r.v < 0 else "nan")
    return f"{r.v / r.mode.scale:.6g}"
