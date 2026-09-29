"""The HTTP server: one ThreadingHTTPServer on :80, a routes table, and the SSE stream.

API rules: everything under /api/ speaks JSON and fails as {"error": …}. Writes (PUT/POST/DELETE) need
Content-Type: application/json, so a page on some other site can't press buttons here with a plain form post
(a cross-site JSON request needs a CORS preflight, which this server never grants). Bodies are at most 4 KB.
Nothing from a request becomes a path unless it's first matched against a whitelist (session names, roster slugs,
font names).
"""
import html
import json
import math
import os
import re
import sys
import subprocess
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import captures, config, devices, dump, meter, phone, report, roster, sessions, system
from .live import LIVE
from .meter import Bad

BODY_MAX = 4096
SSE_MAX = 8            # live clients at once; each holds a thread
BACKLOG_MAX = 7200     # samples a client can ask for on connect (the live ring's size)


def clean(x):
    """JSON-safe: NaN/inf -> null (the report can produce them; JSON can't carry them)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def q1(q, k, default=None):
    return q.get(k, [default])[0]


# --- phone view (the URLs from before the API: every one keeps working) ---------------------
def get_index(h, m, q):
    h.send(200, phone.index(q, h.is_local()))


def get_detail(h, m, q):
    name = m["name"]
    if not config.log_path(name):
        return h.not_found()
    h.send(200, phone.detail(name, q))


def get_png(h, m, q):
    name = m["name"]
    if not config.log_path(name):
        return h.not_found()
    _, png = report.run(name, *phone.window(q), *phone.span(q))
    if not png:
        return h.not_found()
    with open(png, "rb") as f:
        h.send(200, f.read(), "image/png")


def get_pdf(h, m, q):
    name = m["name"]
    if not config.log_path(name):
        return h.not_found()
    t0, t1 = phone.span(q)
    fn = f"gatbox_{name[:-4]}" + (f"_{(t0 or 'start')}_{(t1 or 'end')}".replace(":", "") if (t0 or t1) else "") + ".pdf"
    h.send(200, report.make_pdf(name, *phone.window(q), t0, t1), "application/pdf",
           [("Content-Disposition", f'inline; filename="{fn}"')])


def get_csv(h, m, q):
    p = config.log_path(m["name"])
    if not p:
        return h.not_found()
    with open(p, "rb") as f:
        h.send(200, f.read(), "text/csv", [("Content-Disposition", f'attachment; filename="{m["name"]}"')])


_labels_lock = threading.Lock()


def get_labels(h, m, q):
    """The QR label sheet (gatbox-labels) as a PDF: the command card + every machine on the floor. Rebuilt when the
    roster, the machines added on the dashboard, or the tool change."""
    from gatboxlib import profiles

    def mt(p):
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return 0
    rp, ap = os.path.join(profiles.data_dir(), profiles.ROSTER_FILE), os.path.join(config.CTRL, profiles.ADDED_FILE)
    if not (mt(rp) or mt(ap)) or not mt(config.LABELS):
        return h.send(503, phone.page("No labels", "<h1>No roster or no gatbox-labels on this Pi</h1>"))
    key = f"{mt(rp)}_{mt(ap)}_{mt(config.LABELS)}"                  # machines added on the dashboard count too
    out = os.path.join(config.CACHE, f"labels_{key}.pdf")
    with _labels_lock:
        if not os.path.exists(out):
            os.makedirs(config.CACHE, exist_ok=True)
            for old in os.listdir(config.CACHE):
                if old.startswith("labels_"):
                    os.remove(os.path.join(config.CACHE, old))
            r = subprocess.run([config.LABELS, "-o", out], capture_output=True, text=True, timeout=300)
            if r.returncode or not os.path.exists(out):
                return h.send(500, phone.page("Labels failed", f"<pre>{html.escape(r.stderr or r.stdout)}</pre>"))
    with open(out, "rb") as f:
        h.send(200, f.read(), "application/pdf", [("Content-Disposition", 'inline; filename="gatbox-labels.pdf"')])


def get_roster_json(h, m, q):
    """The roster for the maintenance app to import: the installed file plus the machines added on the dashboard."""
    body = roster.export()
    if body is None:
        return h.send(503, phone.page("No roster", "<h1>No roster on this Pi</h1>"))
    h.send(200, body, "application/json; charset=utf-8",
           [("Content-Disposition", 'attachment; filename="gatbox-barcade-roster.json"')])


def get_font(h, m, q):
    if m["file"] not in config.FONT_FILES:
        return h.not_found()
    try:
        with open(os.path.join(config.FONTS, m["file"]), "rb") as f:
            h.send(200, f.read(), "font/ttf", cache="public, max-age=31536000, immutable")
    except OSError:
        h.not_found()                                   # not installed: the page falls back to system fonts


def post_control(h, m, q):
    """The phone view's Start/Stop (a plain form post; POST-redirect-GET so a refresh doesn't press it again)."""
    n = int(h.headers.get("Content-Length") or 0)
    form = parse_qs(h.rfile.read(min(n, 1024)).decode(errors="replace"))
    action = q1(form, "action", "")
    if action not in ("start", "stop"):
        return h.send(400, phone.page("Bad request", "<h1>Bad request</h1><a href='/'>Back</a>"))
    meter.stop() if action == "stop" else meter.request_start()
    h.log_line(f"control: {action}")
    LIVE.emit("state", meter.state(full=False))
    h.send(303, b"", extra=[("Location", "/?" + h.url.query if h.url.query else "/")])


def get_kiosk_state(h, m, q):
    h.send(200, json.dumps({"exit_at": phone.kiosk_at("kiosk-exit"), "shutdown_at": phone.kiosk_at("kiosk-shutdown")}),
           "application/json")


def post_kiosk(h, m, q):
    """EXIT KIOSK / SHUT DOWN: the Pi's own screen only, never a phone. gatbox-kiosk-launch acts on the change."""
    what = m["what"]
    if not h.is_local():
        return h.send(403, phone.page("Forbidden", f"<h1>{what.upper()} works on the Pi's own screen only</h1>"))
    os.makedirs(config.CTRL, exist_ok=True)
    with open(os.path.join(config.CTRL, f"kiosk-{what}"), "w") as f:
        f.write(f"{int(time.time())}\n")
    h.log_line(f"kiosk: {what}")
    h.send(204, b"")


# --- the dashboard (M5): static files from config.DASH ---------------------------------------
def get_dash_redirect(h, m, q):
    h.send(301, b"", extra=[("Location", "/dash/")])


def get_dash(h, m, q):
    name = m.get("file") or "index.html"
    if name != "index.html" and not config.DASH_FILE.match(name):
        return h.not_found()
    try:
        with open(os.path.join(config.DASH, name), "rb") as f:
            body = f.read()
    except OSError:
        return h.not_found()
    ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
             "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
    h.send(200, body, ctype, cache="no-cache")        # small files on a LAN: always check, so installs show at once


# --- JSON API ---------------------------------------------------------------------------------
def dash_version():
    """A fingerprint of the installed dashboard files: an open page reloads itself when it changes (an install), so the
    7" kiosk never keeps running old code (2026-09-29: the DUMP tab still said "arrives in M7" after the M7 install)."""
    parts = []
    for n in sorted(os.listdir(config.DASH)) if os.path.isdir(config.DASH) else []:
        try:
            st = os.stat(os.path.join(config.DASH, n))
            parts.append(f"{n}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            pass
    return format(zlib.crc32("|".join(parts).encode()), "08x")      # stable across restarts (hash() isn't)


def api_system(h, m, q):
    h.json(200, dict(system.snapshot(), client={"local": h.is_local()},      # local = the Pi's own screen
                     dash_version=dash_version()))


def api_meter(h, m, q):
    h.json(200, meter.snapshot())


def api_profile_get(h, m, q):
    h.json(200, meter.state())


def api_profile_put(h, m, q):
    new_file = meter.set_profile(h.body())
    st = meter.state()
    LIVE.emit("state", meter.state(full=False))
    h.json(200, dict(st, new_file=new_file))


def api_alarm_put(h, m, q):
    meter.set_alarm(h.body())
    st = meter.state(full=False)
    LIVE.emit("state", st)
    h.json(200, st)


def api_machine_get(h, m, q):
    slug = meter.resolved()["machine"]
    h.json(200, {"slug": slug, "entry": roster.entry(slug) if slug else None, "roster": roster.slugs() is not None})


def api_machine_put(h, m, q):
    b = h.body()
    if not isinstance(b, dict) or not isinstance(b.get("slug"), str):
        raise Bad(400, 'expected {"slug": "<roster slug>"}')
    changed, new_file = meter.set_machine(b["slug"])
    if changed:
        LIVE.emit("state", meter.state(full=False))
    h.json(200, {"slug": b["slug"], "changed": changed, "new_file": new_file, "entry": roster.entry(b["slug"])})


def api_machine_delete(h, m, q):
    changed, new_file = meter.set_machine(None)
    if changed:
        LIVE.emit("state", meter.state(full=False))
    h.json(200, {"slug": None, "changed": changed, "new_file": new_file})


def api_mark(h, m, q):
    mark = meter.add_mark(h.body())
    LIVE.emit("mark", mark)
    h.log_line(f"mark: {mark['source']} {mark['label']!r}")
    h.json(201, mark)


_scans, _scans_lock = [], threading.Lock()      # the last few scans, for the DEVICES panel


def api_scan(h, m, q):
    """POST /api/scan {"code"}: gatbox-scand's codes. Loopback only: a scan means someone is at the box."""
    if not h.is_local():
        raise Bad(403, "scans come from gatbox-scand on the Pi itself")
    b = h.body()
    r = meter.scan(b.get("code") if isinstance(b, dict) else None)
    if r["action"] == "mark":
        LIVE.emit("mark", r["mark"])
    elif r["action"] in ("machine", "new"):
        LIVE.emit("state", meter.state(full=False))
    ev = {k: v for k, v in r.items() if k != "mark"}
    ev["at"] = round(time.time(), 3)
    with _scans_lock:
        _scans.append(ev)
        del _scans[:-10]
    LIVE.emit("scan", ev)
    h.log_line(f"scan: {r['code'][:80]!r} -> {r['action']}")
    h.json(200, r)


def api_session_new(h, m, q):
    h.body()                                            # checks the content type; the body itself is ignored
    meter.request_start()
    h.log_line("control: start (api)")
    LIVE.emit("state", meter.state(full=False))
    h.json(202, {"ok": True, "logging": LIVE.logging()})


def api_session_stop(h, m, q):
    h.body()
    meter.stop()
    h.log_line("control: stop (api)")
    LIVE.emit("state", meter.state(full=False))
    h.json(202, {"ok": True})


def api_captures_get(h, m, q):
    slug = q1(q, "machine") or meter.resolved()["machine"] or captures.UNASSIGNED
    h.json(200, {"machine": slug, "captures": captures.read(slug)})


def api_captures_post(h, m, q):
    c = captures.add(h.body())
    LIVE.emit("capture", c)
    h.json(201, c)


def api_dump_get(h, m, q):
    h.json(200, dump.status())


def api_dump_post(h, m, q):
    req = dump.request(h.body())
    h.log_line(f"dump: {req['part']} {req['label']} -> {req['machine']}")
    LIVE.emit("dump", dump.status())
    h.json(202, req)


def api_dump_parts(h, m, q):
    h.json(200, dump.search(q1(q, "q"), q1(q, "family")))


def api_dumps(h, m, q):
    slug = q1(q, "machine") or meter.resolved()["machine"] or "unassigned"
    h.json(200, {"machine": slug, "dumps": dump.dumps(slug)})


def api_devices(h, m, q):
    d = devices.snapshot()
    with _scans_lock:
        d["scanner"]["recent"] = list(reversed(_scans))
    h.json(200, d)


def api_roster(h, m, q):
    L = roster.listing()
    if L is None:
        raise Bad(503, "no roster installed (data/gatbox-barcade-roster.json)")
    h.json(200, {"machines": L, "platforms": roster.platforms(), "added": sum(1 for x in L if x["added"])})


def api_roster_add(h, m, q):
    """POST /api/roster: add a machine (dry_run: just the ID it would get). See roster.add."""
    entry, kind, saved = roster.add(h.body())
    if saved:
        h.log_line(f"roster: added {entry['slug']} ({entry['name']})")
        LIVE.emit("roster", {"action": "added", "slug": entry["slug"], "name": entry["name"]})
    h.json(201 if saved else 200, {"entry": entry, "kind": kind, "saved": saved})


def api_roster_edit(h, m, q):
    entry, kind = roster.edit(m["slug"], h.body())
    h.log_line(f"roster: edited {entry['slug']}")
    LIVE.emit("roster", {"action": "edited", "slug": entry["slug"], "name": entry["name"]})
    if meter.resolved()["machine"] == entry["slug"]:
        LIVE.emit("state", meter.state(full=False))          # its name shows in every client's header
    h.json(200, {"entry": entry, "kind": kind})


def api_roster_entry(h, m, q):
    e = roster.entry(m["slug"])
    if e is None:
        raise Bad(404 if roster.slugs() is not None else 503, f"not in the roster: {m['slug']!r}")
    h.json(200, e)


def api_sessions(h, m, q):
    try:
        limit = max(1, min(int(q1(q, "limit", "100")), 1000))
    except ValueError:
        raise Bad(400, "limit: a whole number")
    h.json(200, {"sessions": sessions.api_list(limit)})


def api_report(h, m, q):
    name = m["name"]
    if not config.log_path(name):
        raise Bad(404, f"no such session: {name!r}")
    lo, hi = phone.window(q)
    t0, t1 = phone.span(q)
    J, png = report.run(name, lo, hi, t0, t1)
    qs = phone.win_qs(lo, hi)
    full = (qs or "?") + phone.span_qs(t0, t1)
    out = dict(J, text=report.text(J, name, t0, t1), png=f"/png/{name}{full}" if png else None,
               pdf=f"/pdf/{name}{full}", csv=f"/csv/{name}", range={"from": t0, "to": t1})
    h.json(200, out)


def api_samples(h, m, q):
    name = m["name"]
    if not config.log_path(name):
        raise Bad(404, f"no such session: {name!r}")
    try:
        mx = max(100, min(int(q1(q, "max", "2000")), 20000))
    except ValueError:
        raise Bad(400, "max: a whole number")
    h.json(200, sessions.samples(name, *phone.span(q), max_points=mx))


def api_live(h, m, q):
    """GET /api/rail/live: Server-Sent Events.
        hello      the meter snapshot (GET /api/meter), first thing on every connect
        backlog    ?backlog=N: the current file's last N samples, compact rows (the chart's history)
        sample     one per reading, as it lands in the file
        session    a new file (dial turn, NEW, profile/machine change) or {"file": null} when logging stops
        alarm      an over-voltage event opened (spike), escalated (alarm) or closed
        mark / capture / state   something done through the API (by any client)
        roster     a machine added or edited on the dashboard
        heartbeat  every 15 s, even while samples flow: {"t", "logging", "age_s"}
    Event ids are sequence numbers: a client reconnecting with Last-Event-ID gets what it missed, if still kept."""
    with LIVE.cv:
        if LIVE.clients >= SSE_MAX:
            raise Bad(503, "too many live clients")
        LIVE.clients += 1
    try:
        try:
            n = max(0, min(int(q1(q, "backlog", "0")), BACKLOG_MAX))
        except ValueError:
            n = 0
        try:
            resume = int(h.headers.get("Last-Event-ID") or q1(q, "since", "0"))
        except ValueError:
            resume = 0
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Accel-Buffering", "no")
        h.end_headers()
        h.quiet = True

        def out(seq, typ, data):                        # data: one line of JSON
            h.wfile.write(((f"id: {seq}\n" if seq else "") + f"event: {typ}\ndata: {data}\n\n").encode())

        h.wfile.write(b"retry: 3000\n\n")
        missed = LIVE.since(resume) if resume else None
        cursor = LIVE.seq
        out(cursor, "hello", json.dumps(clean(meter.snapshot()), ensure_ascii=False))
        if missed:
            for seq, typ, data in missed:
                out(seq, typ, data)
            cursor = max(cursor, missed[-1][0])
        if n:
            out(0, "backlog", json.dumps(clean(LIVE.backlog(n)), ensure_ascii=False))
        h.wfile.flush()
        next_hb = time.monotonic() + config.HEARTBEAT_S
        while True:
            LIVE.wait(cursor, max(0.1, next_hb - time.monotonic()))
            evs = LIVE.since(cursor)
            if evs is None:                              # fell behind the log: start this client over
                out(0, "resync", "{}")
                h.wfile.flush()
                return
            for seq, typ, data in evs:
                out(seq, typ, data)
                cursor = seq
            if time.monotonic() >= next_hb:
                hb = {"t": round(time.time(), 3), "logging": LIVE.logging(), "age_s": LIVE.age(), "file": LIVE.name}
                out(0, "heartbeat", json.dumps(clean(hb)))
                next_hb = time.monotonic() + config.HEARTBEAT_S
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        pass                                             # the client went away
    finally:
        with LIVE.cv:
            LIVE.clients -= 1


ROUTES = [(method, re.compile(pattern), fn) for method, pattern, fn in [
    # phone view
    ("GET", r"/?", get_index),
    ("GET", r"/s/(?P<name>[^/]+)", get_detail),
    ("GET", r"/png/(?P<name>[^/]+)", get_png),
    ("GET", r"/pdf/(?P<name>[^/]+)", get_pdf),
    ("GET", r"/csv/(?P<name>[^/]+)", get_csv),
    ("GET", r"/font/(?P<file>[^/]+)", get_font),
    ("GET", r"/labels\.pdf", get_labels),
    ("GET", r"/roster\.json", get_roster_json),
    ("POST", r"/control", post_control),
    ("GET", r"/kiosk/state", get_kiosk_state),
    ("POST", r"/kiosk/(?P<what>exit|shutdown)", post_kiosk),
    # dashboard (M5)
    ("GET", r"/dash", get_dash_redirect),
    ("GET", r"/dash/(?P<file>[^/]*)", get_dash),
    # JSON API (M4d)
    ("GET", r"/api/system", api_system),
    ("GET", r"/api/meter", api_meter),
    ("GET", r"/api/meter/profile", api_profile_get),
    ("PUT", r"/api/meter/profile", api_profile_put),
    ("PUT", r"/api/meter/alarm", api_alarm_put),
    ("GET", r"/api/rail/live", api_live),
    ("GET", r"/api/rail/sessions", api_sessions),
    ("GET", r"/api/rail/report/(?P<name>[^/]+)", api_report),
    ("GET", r"/api/rail/samples/(?P<name>[^/]+)", api_samples),
    ("GET", r"/api/machine", api_machine_get),
    ("PUT", r"/api/machine", api_machine_put),
    ("DELETE", r"/api/machine", api_machine_delete),
    ("POST", r"/api/mark", api_mark),
    ("POST", r"/api/session/new", api_session_new),
    ("POST", r"/api/session/stop", api_session_stop),
    ("POST", r"/api/scan", api_scan),
    ("GET", r"/api/captures", api_captures_get),
    ("POST", r"/api/captures", api_captures_post),
    ("GET", r"/api/devices", api_devices),
    ("GET", r"/api/dump", api_dump_get),
    ("POST", r"/api/dump", api_dump_post),
    ("GET", r"/api/dump/parts", api_dump_parts),
    ("GET", r"/api/dumps", api_dumps),
    ("GET", r"/api/roster", api_roster),
    ("POST", r"/api/roster", api_roster_add),
    ("GET", r"/api/roster/(?P<slug>[^/]+)", api_roster_entry),
    ("PUT", r"/api/roster/(?P<slug>[^/]+)", api_roster_edit),
]]


class H(BaseHTTPRequestHandler):
    server_version = "gatbox-web"
    timeout = 30                  # per socket operation: a phone that went to sleep mid-stream is dropped

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=(), cache="no-store"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, code, obj, extra=()):
        self.send(code, json.dumps(clean(obj), ensure_ascii=False, allow_nan=False), "application/json; charset=utf-8",
                  extra)

    def not_found(self):
        if self.url.path.startswith("/api/"):
            return self.json(404, {"error": "not found"})
        self.send(404, phone.page("Not found", "<h1>Not found</h1><a href='/'>Back</a>"))

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def body(self):
        """The request's JSON body ({} if empty). JSON content type required (see the module docstring)."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise Bad(415, "Content-Type must be application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Bad(400, "bad Content-Length")
        if n > BODY_MAX:
            raise Bad(413, f"body over {BODY_MAX} bytes")
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise Bad(400, "body is not valid JSON")

    def log_line(self, msg):
        sys.stderr.write(f"{self.address_string()} {msg}\n")

    def log_message(self, fmt, *a):
        # a dashboard polls the API every second or two: log only what changes something, and failures.
        # No request line at all (Chromium opens spare connections and lets them time out): nothing to log.
        command, url = getattr(self, "command", None), getattr(self, "url", None)
        if command is None:
            return
        if command == "GET" and not getattr(self, "failed", False) and url and \
                url.path.startswith(("/api/", "/font/", "/kiosk/state", "/dash/")):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def send_response(self, code, message=None):
        self.failed = code >= 400
        super().send_response(code, message)

    def route(self, method):
        self.url = urlsplit(self.path)
        q = parse_qs(self.url.query)
        allowed = []
        for m, rx, fn in ROUTES:
            mt = rx.fullmatch(self.url.path)
            if not mt:
                continue
            if m != method:
                allowed.append(m)
                continue
            try:
                return fn(self, mt.groupdict(), q)
            except Bad as e:
                return self.json(e.code, {"error": e.msg})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:      # keep serving; show the error instead of a dropped connection
                self.log_line(f"error on {method} {self.url.path}: {e!r}")
                if self.url.path.startswith("/api/"):
                    return self.json(500, {"error": repr(e)})
                return self.send(500, phone.page("Error", f"<h1>Error</h1><pre>{html.escape(repr(e))}</pre>"))
        if self.url.path.startswith("/api/"):
            if allowed:
                return self.json(405, {"error": f"{method} not allowed here"}, [("Allow", ", ".join(allowed))])
            return self.not_found()
        if method != "GET":             # as before the API: any other POST to the phone view is a bad request
            return self.send(400, phone.page("Bad request", "<h1>Bad request</h1><a href='/'>Back</a>"))
        self.not_found()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")


def _warm():
    """Fill the session-list cache in the background, so the first SESSIONS view doesn't wait on every report."""
    try:
        sessions.api_list(100)
    except Exception as e:
        print(f"warm-up: {e!r}", flush=True)


def main():
    try:                                                 # the text-report cache from before the API
        for f in os.listdir(config.CACHE):
            if f.endswith(".txt"):
                os.remove(os.path.join(config.CACHE, f))
    except OSError:
        pass
    LIVE.start()
    dump.watch()
    threading.Thread(target=_warm, name="warm", daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("", config.PORT), H)
    print(f"gatbox-web on :{config.PORT}, logs {config.LOGDIR}", flush=True)
    srv.serve_forever()
