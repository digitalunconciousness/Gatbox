"""gatbox-rail-report runs, cached, so the numbers everywhere match the CLI; and the PDF.

One run with --json gives the text report and the parsed sections; the PNG comes from the same run. Results are
cached in $CACHE_DIRECTORY keyed by the file's size + mtime, the report script's mtime, the window (or "hdr" for
the file's own header window), the from/to slice and light/dark. A newer version of a session drops the old ones.
"""
import io
import json
import os
import subprocess
import textwrap
import threading
import time

from gatboxlib.modes import WARN

from . import config, sessions

PDF_LOCK = threading.Lock()   # matplotlib isn't thread-safe
_live_lock = threading.Lock()
_live_summary = {}            # name -> (monotonic time, J): the live file's summary, reused for LIVE_REUSE_S
_run_locks, _run_locks_lock = {}, threading.Lock()   # one report run per cache key at a time
LIVE_REUSE_S = 15


def _num(x):
    return f"{x:g}" if x is not None else ""


def _key(name, st, lo, hi, t0, t1, light, plot):
    ver = f"{st.st_size}_{int(st.st_mtime)}_{int(os.stat(config.REPORT).st_mtime)}"   # new report -> new cache
    win = "hdr" if lo is None and hi is None else f"{_num(lo)}_{_num(hi)}"
    rng = f"_{t0 or ''}_{t1 or ''}".replace(":", "") if (t0 or t1) else ""
    return ver, f"{name[:-4]}_{ver}_{win}{rng}{'_light' if light else ''}{'' if plot else '_np'}"


def run(name, lo=None, hi=None, t0=None, t1=None, light=False, plot=True):
    """(J, png path or None) for a session (or its from/to slice). J is the report's --json object; on failure
    it's {"error": …, "text": …}. lo/hi None = the file's header window (older files: 4.75-5.25 V)."""
    src = config.log_path(name)
    if not src:
        raise FileNotFoundError(name)
    st = os.stat(src)
    os.makedirs(config.CACHE, exist_ok=True)
    ver, key = _key(name, st, lo, hi, t0, t1, light, plot)
    with _run_locks_lock:
        lock = _run_locks.setdefault(key, threading.Lock())
        if len(_run_locks) > 256:                 # forget idle locks now and then
            for k in [k for k, v in _run_locks.items() if k != key and not v.locked()]:
                del _run_locks[k]
    with lock:
        return _run(name, src, ver, key, lo, hi, t0, t1, light, plot)


def _run(name, src, ver, key, lo, hi, t0, t1, light, plot):
    png, jf = os.path.join(config.CACHE, key + ".png"), os.path.join(config.CACHE, key + ".json")
    try:
        with open(jf, encoding="utf-8") as f:
            return json.load(f), (png if os.path.exists(png) else None)
    except (OSError, ValueError):
        pass
    for old in os.listdir(config.CACHE):          # drop stale versions of this session (the file grew)
        if old.startswith(name[:-4] + "_") and f"_{ver}_" not in old:
            try:
                os.remove(os.path.join(config.CACHE, old))
            except OSError:
                pass
    run_on = src
    if t0 or t1:                                  # slice the CSV, keep the header + "# ..." notes
        run_on, kept = os.path.join(config.CACHE, key + ".csv"), 0
        with open(src, errors="replace") as fi, open(run_on, "w") as fo:
            for i, line in enumerate(fi):
                if i == 0 or line.startswith("#"):
                    fo.write(line)
                    continue
                iso = line[:19]
                if (not t0 or iso >= t0) and (not t1 or iso <= t1):
                    fo.write(line)
                    kept += 1
        if not kept:
            os.remove(run_on)
            J = {"error": "no samples in this range", "text": f"No samples between {t0 or 'start'} and {t1 or 'end'}.\n"}
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(J, f)
            return J, None
    cmd = [config.REPORT, run_on, "--json"] + (["--plot", png] if plot else ["--no-plot"])
    cmd += (["--lo", str(lo)] if lo is not None else []) + (["--hi", str(hi)] if hi is not None else [])
    cmd += ["--light"] if light else []
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        J = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        J = None
    if J is None:
        msg = (r.stderr or r.stdout).strip() or f"gatbox-rail-report exited {r.returncode}"
        J = {"error": msg, "text": msg + "\n"}
    J.pop("plot", None)                           # a cache path means nothing to a client
    J["file"] = name
    if run_on != src:
        os.remove(run_on)
    tmp = jf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(J, f, ensure_ascii=False)
    os.replace(tmp, jf)
    return J, (png if plot and os.path.exists(png) else None)


def summary(name, live=False):
    """The no-plot report JSON with the header window, for the session list. The live file changes every sample,
    so its summary is reused for LIVE_REUSE_S instead of re-running the report on every request."""
    if live:
        with _live_lock:
            hit = _live_summary.get(name)
            if hit and time.monotonic() - hit[0] < LIVE_REUSE_S:
                return hit[1]
    J, _ = run(name, plot=False)
    if live:
        with _live_lock:
            _live_summary.clear()
            _live_summary[name] = (time.monotonic(), J)
    return J


def text(J, name, t0=None, t1=None):
    """The report text for a page: no cache path, the real session name, and the range if sliced."""
    out = []
    for line in J.get("text", "").splitlines(keepends=True):
        if line.startswith("plot:"):
            continue
        if line.startswith("file:"):
            line = f"file:      {name}\n"
            if t0 or t1:
                line += f"range:     {t0 or 'start'}  ->  {t1 or 'end'}\n"
        out.append(line)
    s = "".join(out)
    return s if s.endswith("\n") or not s else s + "\n"


def window_text(J):
    w = J.get("window")
    return f"window {w['lo']:g}–{w['hi']:g} {w.get('unit') or 'V'}" if w else "no window"


def make_pdf(name, lo, hi, t0, t1):
    """Letter-size PDF: header, plot, then the report text (continued on more pages if long)."""
    J, png = run(name, lo, hi, t0, t1, light=True)
    body = text(J, name, t0, t1)
    s = sessions.summary(name, t0, t1)
    clk = (s["clock"] or "?").upper() + (f" (NTP from {s['clock_sync'][11:19]})" if s["clock_sync"] else "")
    mode_txt = ("clock: " + clk + "   ·   meter mode: " + (", ".join(s["modes"]) or "no samples")
                + "".join(f"   ·   {k} was on" for k in dict(WARN[f] for f in sorted(s["warn"]))))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.image as mpimg
    lines = body.rstrip("\n").split("\n")
    buf = io.BytesIO()
    with PDF_LOCK, PdfPages(buf, metadata={"Title": f"GATBOX rail report {name}", "Creator": "gatbox-web"}) as pdf:
        first, per_page, page_no = True, 72, 1
        while first or lines:
            fig = Figure(figsize=(8.5, 11))
            fig.text(0.06, 0.965, "GATBOX rail report (GDD-GAT/01)", fontsize=14, weight="bold")
            fig.text(0.94, 0.965, f"page {page_no}", fontsize=8, ha="right", color="0.4")
            if first:
                sub = (f"{name}   ·   {window_text(J)}   ·   "
                       f"range {(t0 or 'start').replace('T', ' ')} → {(t1 or 'end').replace('T', ' ')}")
                fig.text(0.06, 0.945, sub, fontsize=8.5, color="0.25")
                rows = textwrap.wrap(mode_txt, 110)
                for i, row in enumerate(rows):
                    fig.text(0.06, 0.93 - 0.0135 * i, row, fontsize=8.5,
                             color="#c0304a" if s["warn"] else "0.25")
                y = 0.93 - 0.0135 * len(rows)
                fig.text(0.06, y, f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} (Pi clock)",
                         fontsize=8, color="0.45")
                top = y - 0.015
                if png:
                    img = mpimg.imread(png)
                    h = 0.88 * 8.5 * img.shape[0] / img.shape[1] / 11   # keep aspect, 88% width
                    ax = fig.add_axes([0.06, top - h, 0.88, h])
                    ax.imshow(img)
                    ax.axis("off")
                    top -= h + 0.02
                n = max(1, int((top - 0.04) / 0.0125))
            else:
                top, n = 0.935, per_page
            chunk, lines = lines[:n], lines[n:]
            fig.text(0.06, top, "\n".join(chunk), fontsize=7.5, family="monospace", va="top",
                     linespacing=1.25)
            pdf.savefig(fig)
            first, page_no = False, page_no + 1
    return buf.getvalue()
