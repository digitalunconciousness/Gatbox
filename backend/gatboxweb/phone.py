"""The phone view: server-rendered HTML, the same pages and URLs as before the API.

    /                      sessions, newest first, plus what's logging right now
    /s/<csv>?lo=&hi=       gatbox-rail-report text + plot for one session
    /png/<csv>?lo=&hi=     the plot on its own
    /pdf/<csv>?lo=&hi=&from=&to=   header + plot + report as a PDF to save/share
                           (from/to are Pi wall-clock times, YYYY-MM-DDTHH:MM[:SS]; on /s and /png too)
    /csv/<csv>             the raw CSV
    /font/<file>           the two UI fonts (the hotspot has no internet)
    POST /control          action=start|stop
    GET /kiosk/state, POST /kiosk/exit, POST /kiosk/shutdown   the 7" kiosk's EXIT KIOSK / SHUT DOWN

The window: each session's own, from its header (older files: 4.75-5.25 V), unless lo/hi are given.
"""
import html
import os
import re
import time

from gatboxlib import profiles
from gatboxlib.modes import WARN, fmt

from . import config, meter, report, sessions
from .live import display

TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?$")


def kiosk_at(name):
    """Epoch of the last kiosk-exit / kiosk-shutdown request (0 if none)."""
    try:
        with open(os.path.join(config.CTRL, name)) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


# EXIT KIOSK: shown only to the Pi's own screen. A long-press (1.5 s), so a stray tap in a cabinet can't close it.
KIOSK_EXIT = """<div class="card kx">
<button type="button" data-act="exit" data-ms="1500" data-idle="Hold to exit kiosk">Hold to exit kiosk</button>
<button type="button" class="sd" data-act="shutdown" data-ms="3000" data-idle="Hold 3 s to shut down">Hold 3 s to shut down</button>
<span class="mut">This screen only. Exit closes the kiosk until the next login. Shut down powers the Pi off safely.</span></div>
<div id="kx-off" class="kx-off"><div><div class="big">Shutting down…</div>
<p>Unplug the Pi only when this screen goes dark and its LED turns red.</p></div></div>
<script>
(function () {
  document.querySelectorAll('.kx button').forEach(b => {
    let t = null;
    const stop = () => { clearTimeout(t); b.classList.remove('arm'); if (!b.dataset.done) b.textContent = b.dataset.idle; };
    b.addEventListener('pointerdown', e => {
      e.preventDefault(); b.classList.add('arm'); b.textContent = 'Keep holding…';
      t = setTimeout(() => {
        b.dataset.done = 1;
        b.textContent = b.dataset.act === 'exit' ? 'Closing…' : 'Shutting down…';
        fetch('/kiosk/' + b.dataset.act, {method: 'POST'});
        if (b.dataset.act === 'shutdown') document.getElementById('kx-off').style.display = 'flex';
      }, +b.dataset.ms);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(k => b.addEventListener(k, stop));
  });
})();
</script>"""

# Project design tokens (synthwave). The fonts come from this Pi, since the hotspot has no internet. If they're
# missing, /font/ 404s and each stack falls back to the system fonts after it.
CSS = """
@font-face{font-family:"Chakra Petch";font-weight:600;font-display:swap;src:url(/font/ChakraPetch-SemiBold.ttf)}
@font-face{font-family:"Share Tech Mono";font-display:swap;src:url(/font/ShareTechMono-Regular.ttf)}
:root{--bg:#150a28;--bg2:#1f0f3d;--panel:#1f1240;--line:#3d1e6b;--mag:#ff2e9f;--cyan:#7dfaff;--ok:#5ef2b0;
--bad:#ff4d6d;--warn:#ffb74d;--fg:#ece3ff;--mut:#9080b0;--disp:"Chakra Petch",system-ui,sans-serif;
--mono:"Share Tech Mono",ui-monospace,monospace}
*{box-sizing:border-box}html{background:var(--bg) linear-gradient(180deg,var(--bg2),var(--bg) 100vh) no-repeat}
body{margin:0 auto;padding:16px;max-width:900px;color:var(--fg);font:15px/1.45 var(--mono)}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px)}
a{color:var(--cyan)}.mut{color:var(--mut);font-size:14px}
h1{font:600 22px/1.2 var(--disp);letter-spacing:.03em;margin:0 0 10px;text-shadow:0 0 12px rgba(255,46,159,.5)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.card a{text-decoration:none;display:block;color:var(--fg)}
.big{font:600 32px/1.2 var(--disp);color:var(--cyan);text-shadow:0 0 10px rgba(125,250,255,.35)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
pre{white-space:pre-wrap;font:13px/1.4 var(--mono);overflow-x:auto;margin:0}
img{width:100%;height:auto;border-radius:6px;border:1px solid var(--line)}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input{width:5.5em;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);
font:inherit}input:focus{outline:1px solid var(--cyan)}input::placeholder{color:var(--mut)}
button,.btn{padding:7px 14px;border-radius:6px;border:1px solid var(--mag);background:var(--mag);color:var(--bg);
font:600 15px var(--disp);text-decoration:none;display:inline-block;cursor:pointer}
button.stop{background:transparent;color:var(--bad);border-color:var(--bad);margin-left:8px}
.rng{display:flex;gap:8px;flex-wrap:wrap;width:100%}.rng label{color:var(--mut);font-size:14px}.rng input{width:auto}
.mode{display:inline-block;padding:0 8px;border:1px solid var(--cyan);border-radius:999px;color:var(--cyan);
background:rgba(125,250,255,.08);box-shadow:0 0 6px rgba(125,250,255,.25);font:13px/1.5 var(--mono);
vertical-align:middle;text-shadow:none}.modes div{margin-top:5px}.modes .warn{font-size:14px}
.kx button{min-height:56px;background:transparent;color:var(--mut);border-color:var(--line);margin-right:10px}
.kx button.arm{color:var(--bad);border-color:var(--bad);box-shadow:0 0 10px rgba(255,77,109,.45)}
.kx button.sd{color:var(--bad);border-color:rgba(255,77,109,.5)}
.kx-off{display:none;position:fixed;inset:0;z-index:9;background:rgba(21,10,40,.96);align-items:center;justify-content:center;text-align:center;padding:24px}
.cyan{color:var(--cyan)}.clk{display:inline-block;padding:0 6px;border:1px solid currentColor;border-radius:4px;
font:12px/1.5 var(--mono);vertical-align:middle;font-weight:normal}
"""


def page(title, body, refresh=None):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="theme-color" content="#150a28">{meta}'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>')


def warnings(flags, when):
    w = dict(WARN[f] for f in sorted(flags))
    return "".join(f'<div class="warn">{html.escape(k)} {when}: {v}</div>' for k, v in w.items())


CLOCK_BADGE = {"ntp": ("NTP", "ok"), "rtc": ("RTC", "cyan"), "unverified": ("UNVERIFIED", "warn")}


def clock_badge(s):
    """NTP / RTC / UNVERIFIED from the session's # clock= line, plus NTP-from-<time> if it synced later."""
    if not s.get("clock"):
        return ""
    txt, cls = CLOCK_BADGE.get(s["clock"], (s["clock"].upper(), "mut"))
    b = f'<span class="clk {cls}" title="{html.escape(s["clock_note"] or "")}">{html.escape(txt)}</span>'
    if s.get("clock_sync"):
        b += f' <span class="clk ok" title="NTP synced mid-session">NTP from {html.escape(s["clock_sync"][11:19])}</span>'
    return b


def session_window(s, lo, hi):
    """(window [lo, hi] or None, the dial modes it applies to) for a session: lo/hi from the page if given, else
    the file's header window; files from before profiles have none, so they get the default +5V rail's."""
    _, _, P = profiles.profiles()
    if s["profile"]:
        keys = set((P.get(s["profile"]) or {}).get("modes") or [])
        win = s["window"]
    else:
        res = profiles.resolve(None)
        keys, win = set(res["modes"]), res["window"]
    if lo is not None or hi is not None:
        base = win or [None, None]
        win = [lo if lo is not None else base[0], hi if hi is not None else base[1]]
        keys = keys or {"VDC"}
        if None in win:
            win = None
    return win, keys


def mode_lines(s, lo, hi, when="was on"):
    """One line per meter mode seen: chip, min/max, and for the profile's own mode the window tag.
    Then a warning for HOLD/REL/MAX/MIN."""
    win, keys = session_window(s, lo, hi)
    out = []
    for label, (n, vmin, vmax, base, key) in s["modes"].items():
        line = f'<span class="mode">{html.escape(label)}</span>'
        if vmin <= vmax and key != "CONT":             # all-OL has no finite values
            line += f' <span class="mut">min {html.escape(fmt(vmin, base))} · max {html.escape(fmt(vmax, base))}</span>'
        if win and key in keys and vmin <= vmax:
            line += (' · <span class="ok">in window</span>' if win[0] <= vmin and vmax <= win[1]
                     else ' · <span class="bad">left the window</span>')
        out.append(f"<div>{line}</div>")
    return f'<div class="modes">{"".join(out)}{warnings(s["warn"], when)}</div>'


def window(q):
    """lo/hi from the query: floats, or None (= each file's own window)."""
    def f(k):
        try:
            v = float(q.get(k, [""])[0])
            return v if abs(v) < 1e9 else None
        except ValueError:
            return None
    return f("lo"), f("hi")


def win_qs(lo, hi):
    parts = ([f"lo={lo:g}"] if lo is not None else []) + ([f"hi={hi:g}"] if hi is not None else [])
    return ("?" + "&".join(parts)) if parts else ""


def span(q):
    """from/to query values -> (t0, t1) as 'YYYY-MM-DDTHH:MM:SS', or None if absent/invalid.
    Compared as strings against the CSV's iso_time column (same local wall-clock format)."""
    def f(k, pad):
        v = q.get(k, [""])[0]
        return (v if len(v) == 19 else v + pad) if TS.match(v) else None
    return f("from", ":00"), f("to", ":59")


def span_qs(t0, t1):
    return (f"&from={t0}" if t0 else "") + (f"&to={t1}" if t1 else "")


def win_inputs(lo, hi, ph):
    ph = ph or [None, None]
    def one(k, v, p):
        return (f'<input name="{k}" value="{"" if v is None else f"{v:g}"}" inputmode="decimal" '
                f'placeholder="{"" if p is None else f"{p:g}"}">')
    return one("lo", lo, ph[0]) + one("hi", hi, ph[1])


def controls(is_live):
    stopped = meter.stopped()
    if stopped:
        state = '<span class="bad">Stopped</span> from this page. Nothing is being recorded.'
    elif is_live:
        state = '<span class="ok">Running</span>'
    else:
        state = '<span class="ok">Running</span>, waiting for readings from the meter'
    start_lbl = "Start new session" if (is_live and not stopped) else "Start"
    return (f'<div class="card"><div>Logger: {state}</div>'
            f'<form method="post" action="/control" style="margin-top:8px">'
            f'<button name="action" value="start">{start_lbl}</button>'
            + ('' if stopped else
               '<button name="action" value="stop" class="stop" '
               'onclick="return confirm(\'Stop logging? Nothing will be recorded until you press Start.\')">Stop</button>')
            + '</form></div>')


def index(q, local):
    lo, hi = window(q)
    qs = win_qs(lo, hi)
    now = time.time()
    cards, live = [], ""
    for name in sessions.names():
        s = sessions.summary(name)
        if not s["first"]:
            continue
        last_iso, last_e, m, raw = s["last"]
        dur = last_e - s["first"][1]
        is_live = sessions.is_live(s, now)
        if is_live:
            win, _ = session_window(s, lo, hi)
            about = (f'{html.escape(s["profile"] or "")}'
                     + (f' · window {win[0]:g}–{win[1]:g}' if win else "")
                     + (f' · {html.escape(s["machine"])}' if s["machine"] else ""))
            live = (f'<div class="card"><div class="mut">Logging now · {html.escape(name)}</div>'
                    f'<div class="big">{html.escape(display(raw, m.unit, m))} '
                    f'<span class="mode">{html.escape(m.label)}</span></div>'
                    f'{warnings(m.warn, "is on")}'
                    + (f'<div class="mut">{about}</div>' if about else "")
                    + f'<div class="mut">{s["n"]} samples · {dur/3600:.2f} h so far</div></div>')
        mach = f' · <span class="cyan">{html.escape(s["machine"])}</span>' if s["machine"] else ""
        cards.append(
            f'<div class="card"><a href="/s/{name}{qs}">'
            f'<b>{html.escape(s["first"][0].replace("T", " "))}</b> {clock_badge(s)}'
            f'{" · <span class=ok>live</span>" if is_live else ""}{mach}<br>'
            f'<span class="mut">{dur/3600:.2f} h · {s["n"]} samples</span>'
            f'{mode_lines(s, lo, hi)}</a></div>')
    is_live = bool(live)
    if not live and not meter.stopped():
        live = ('<div class="card"><div class="mut">Not logging right now</div>'
                '<div class="mut">Meter off, D02 head out of the top slot, or adapter unplugged.</div></div>')
    live = controls(is_live) + live
    body = (f'<h1>GATBOX rail log</h1><div class="mut">Pi time {time.strftime("%Y-%m-%d %H:%M:%S")}'
            f' · refreshes every 30 s</div>{KIOSK_EXIT if local else ""}{live}'
            f'<form method="get" action="/"><span class="mut">Window (V)</span>'
            f'{win_inputs(lo, hi, None)}<button>Apply</button>'
            f'<span class="mut">empty = each session\'s own</span></form>'
            + ("".join(cards) or '<div class="card mut">No sessions yet.</div>'))
    return page("GATBOX", body, refresh=30)


def detail(name, q):
    lo, hi = window(q)
    t0, t1 = span(q)
    qs = win_qs(lo, hi)
    s = sessions.summary(name)
    start = s["first"][0] if s["first"] else ""
    end = s["last"][0] if s["last"] else ""
    J, png = report.run(name, lo, hi, t0, t1)
    text = report.text(J, name, t0, t1)
    view = sessions.summary(name, t0, t1) if (t0 or t1) else s
    full = (qs or "?") + span_qs(t0, t1)                  # "?&from=…" is fine: parse_qs skips the empty part
    src = f"/png/{name}{full}&t={int(time.time())}"
    img = f'<a href="{src}"><img src="{src}" alt="plot (tap for full size)"></a>' if png else \
          '<div class="mut">No plot (python3-matplotlib missing?)</div>'
    own, _ = session_window(s, None, None)
    own_note = "empty = the file's own" if own else "this file has no window"
    body = (f'<a href="/{qs}">← all sessions</a><h1>{html.escape(name)}</h1>'
            f'<div class="card"><div>{clock_badge(s)} <span class="mut">{html.escape(s["clock_note"] or "no clock line")}</span></div>'
            f'<span class="mut">Meter mode{" in this range" if (t0 or t1) else ""}</span>'
            f'{mode_lines(view, lo, hi) if view["n"] else "<div class=mut>no samples</div>"}</div>'
            f'<form method="get"><span class="mut">Window</span>{win_inputs(lo, hi, own)}'
            f'<span class="mut">{own_note}</span>'
            f'<div class="rng"><label>From <input type="datetime-local" step="1" name="from" '
            f'value="{t0 or start}" min="{start}" max="{end}"></label>'
            f'<label>To <input type="datetime-local" step="1" name="to" '
            f'value="{t1 or end}" min="{start}" max="{end}"></label></div>'
            f'<button>Apply</button>'
            f'{f"<a href=/s/{name}{qs}>whole session</a>" if (t0 or t1) else ""}</form>'
            f'<p><a class="btn" href="/pdf/{name}{full}">PDF of this view</a></p>'
            f'<div class="card">{img}</div><div class="card"><pre>{html.escape(text)}</pre></div>'
            f'<a href="/csv/{name}">Download CSV</a>')
    return page(name, body)
