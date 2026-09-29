"""The live session: one thread follows gatbox-raillog's file, every client reads from it.

gatbox-raillog writes the live file's path to /run/gatbox/current once the header is complete, and removes it when
the session ends; a dial turn, NEW or a profile/machine change ends one file and starts the next. The follower polls
that name every POLL_S, reads the file's new lines, and keeps: the header, the last reading, the current file's
recent samples (for the chart), its marks, and its over-voltage events. Every change goes into one event log that
the SSE clients (GET /api/rail/live) read from, so many clients cost no more file reads than one.

The live alarm (DECIDED 2026-09-28, owner): a reading over the file's alarm_hi (as a magnitude; only in the dial
modes its profile measures, and never OL) is a SPIKE; two or more in a row are an ALARM. The report's "suspect"
rule is the same idea after the fact: a lone over-limit reading is usually an autorange glitch. Events are facts
about the data and are always tracked; the ALARM ON/OFF switch (meter.py) only decides whether the dashboard
takes over the screen.
"""
import collections
import json
import math
import os
import threading
import time

from gatboxlib import csvlog, profiles
from gatboxlib.modes import mode

from . import config

POLL_S = 0.25
RING = 7200            # samples of the current file kept for the chart (1 h at the UT61E's ~2/s)
EVENT_LOG = 4000       # events kept for SSE clients catching up (Last-Event-ID)
MAX_ALARMS = 50        # over-voltage events kept per file


def uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def display(raw, unit, m):
    """A reading the way the meter's display would put it."""
    if raw.lstrip("+-").lower() == "inf":
        return "OL"
    if m.key == "CONT":
        try:
            return "closed" if float(raw) > 0 else "open"
        except ValueError:
            pass
    return f"{raw} {m.unit}".rstrip()


def header_dict(h):
    return {"clock": h.clock, "clock_sync": h.clock_sync, "mode": h.mode, "profile": h.profile,
            "window": h.window, "window_source": h.window_source,
            "alarm_hi": None if h.alarm_none else h.alarm_hi, "machine": h.machine, "mode_change": h.mode_change}


class Live:
    def __init__(self):
        self.cv = threading.Condition()
        self.seq = 0
        self.events = collections.deque(maxlen=EVENT_LOG)   # (seq, type, json text)
        self.ring = collections.deque(maxlen=RING)          # the current file's samples, oldest first
        self.name = self.path = self.fh = None
        self.buf = b""
        self.h = csvlog.Header()
        self.marks, self.alarms = [], []
        self.run = None               # the over-voltage event in progress
        self.limit, self.modes = None, set()
        self.last = None              # the last sample (kept after the session ends)
        self.ended = None             # the file the last session ended with
        self.announce = False         # a new file whose 'session' event hasn't gone out yet
        self.quiet = False            # catching up on a file that was already live at startup: no sample events
        self.started = False
        self.clients = 0

    # --- the event log ---------------------------------------------------------------
    def emit(self, typ, data):
        with self.cv:
            self.seq += 1
            self.events.append((self.seq, typ, json.dumps(data, ensure_ascii=False)))
            self.cv.notify_all()
            return self.seq

    def since(self, seq):
        """Events after seq, oldest first; None if seq is older than the log (the client missed some)."""
        with self.cv:
            if self.events and seq < self.events[0][0] - 1:
                return None
            out = []
            for ev in reversed(self.events):
                if ev[0] <= seq:
                    break
                out.append(ev)
            return out[::-1]

    def wait(self, seq, timeout):
        with self.cv:
            self.cv.wait_for(lambda: self.seq > seq, timeout)
            return self.seq

    # --- following the logger ----------------------------------------------------------
    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._loop, name="live", daemon=True).start()

    def _loop(self):
        first = True
        while True:
            try:
                self.poll(first)
                first = False
            except Exception as e:   # keep following: a bad line or a vanished file mustn't stop the live view
                print(f"live: {e!r}", flush=True)
            time.sleep(POLL_S)

    def _current(self):
        """The live file from /run/gatbox/current: only a session name inside LOGDIR, else None."""
        try:
            with open(os.path.join(config.RUNDIR, "current")) as f:
                p = f.read().strip()
        except OSError:
            return None
        name = os.path.basename(p)
        if os.path.dirname(p) != os.path.normpath(config.LOGDIR) or not config.NAME.match(name):
            return None
        return p

    def poll(self, first=False):
        path = self._current()
        if path != self.path:
            if self.fh:
                self._read()                 # the old file's last lines (# mode-change=, # settling-unused=)
                self.fh.close()
            prev, self.fh = self.name, None
            if path:
                try:
                    self.fh = open(path, "rb")
                except OSError:
                    return                   # not there yet: try again next poll
            with self.cv:
                self.path, self.name, self.buf = path, (os.path.basename(path) if path else None), b""
                if path:
                    self.h, self.marks, self.alarms, self.run = csvlog.Header(), [], [], None
                    self.limit, self.modes = None, set()
                    self.ring.clear()
                    self.announce, self.quiet = True, first
                else:
                    self.ended = prev
                    if self.run:
                        self.run["open"] = False
                        self.run = None
            if not path:
                self.emit("session", {"file": None, "ended": prev})
        if self.fh:
            self._read()
            if self.announce:
                self._announce()
            self.quiet = False

    def _announce(self):
        self.announce = False
        self.emit("session", {"file": self.name, "header": header_dict(self.h), "marks": self.marks})

    def _read(self):
        data = self.fh.read()
        if not data:
            return
        lines = (self.buf + data).split(b"\n")
        self.buf = lines.pop()               # a line still being written
        for raw in lines:
            line = raw.replace(b"\0", b"").decode("utf-8", "replace").rstrip("\r")
            if not line.strip() or line.startswith("iso_time"):
                continue
            if line.startswith("#"):
                with self.cv:
                    n = len(self.marks)
                    csvlog.comment(self.h, self.marks, line)
                    if len(self.marks) > n:
                        self.marks[-1] = self.marks[-1]._asdict()
                continue
            if self.announce:
                self._announce()
            self._sample(line.split(","))

    def _limits(self):
        """alarm_hi and the dial modes it applies to, from the file's header (older files: the default profile)."""
        h = self.h
        if h.profile is None and not h.alarm_none and h.alarm_hi is None:
            res = profiles.resolve(None)
            return res["alarm_hi"], set(res["modes"])
        _, _, P = profiles.profiles()
        p = P.get(h.profile) or {}
        return (None if h.alarm_none else h.alarm_hi), set(p.get("modes") or ["VDC"])

    def _sample(self, p):
        if len(p) < 5:
            return
        iso, epoch, raw, unit, flags = p[:5]
        try:
            e = float(epoch)
        except ValueError:
            return
        try:
            up = float(p[5]) if len(p) > 5 and p[5] else None
        except ValueError:
            up = None
        m = mode(unit, flags)
        try:
            v = float(raw) * m.scale
        except ValueError:
            v = math.nan
        fin = math.isfinite(v)
        with self.cv:
            if not self.ring and not self.run:
                self.limit, self.modes = self._limits()
            state, change = None, None
            if self.limit is not None and m.key in self.modes:
                if fin and abs(v) > self.limit:
                    if self.run is None:
                        self.run = {"file": self.name, "start": iso, "epoch": e, "end": iso, "peak": v, "n": 1,
                                    "kind": "spike", "open": True, "limit": self.limit}
                        self.alarms.append(self.run)
                        del self.alarms[:-MAX_ALARMS]
                        state = change = "spike"
                    else:
                        r = self.run
                        r["n"], r["end"] = r["n"] + 1, iso
                        if abs(v) > abs(r["peak"]):
                            r["peak"] = v
                        if r["kind"] == "spike":
                            r["kind"] = change = "alarm"
                        state = "alarm"
                else:
                    state = "ok"
            if state in (None, "ok") and self.run:     # back under (or the dial moved off the rail mode)
                self.run["open"], change = False, "closed"
                closed, self.run = self.run, None
            s = {"file": self.name, "iso": iso, "epoch": e, "up": up, "raw": raw, "unit": unit,
                 "flags": flags.split(), "mode": m.key, "label": m.label, "base": m.base,
                 "v": v if fin else None, "ol": raw.lstrip("+-").lower() == "inf",
                 "display": display(raw, unit, m), "warn": sorted(m.warn), "alarm": state}
            self.last = s
            self.ring.append(s)
            ev = dict(self.run) if change in ("spike", "alarm") else (dict(closed) if change == "closed" else None)
        if not self.quiet:
            self.emit("sample", s)
            if ev:
                self.emit("alarm", ev)

    # --- what clients read -------------------------------------------------------------
    def snapshot(self):
        with self.cv:
            return {"seq": self.seq, "file": self.name, "ended": self.ended,
                    "header": header_dict(self.h) if self.name else None, "last": self.last,
                    "alarms": [dict(a) for a in self.alarms[-20:]], "open_alarm": dict(self.run) if self.run else None,
                    "marks": list(self.marks) if self.name else []}

    def backlog(self, n):
        """The current file's last n samples, compact: one row per sample."""
        with self.cv:
            rows = list(self.ring)[-n:] if n > 0 else []
            return {"file": self.name,
                    "fields": ["epoch", "up", "raw", "unit", "v", "mode", "alarm"],
                    "rows": [[s["epoch"], s["up"], s["raw"], s["unit"], s["v"], s["mode"], s["alarm"]] for s in rows]}

    def age(self):
        """Seconds since the last reading (monotonic uptime when the file has it), or None."""
        s = self.last
        if not s:
            return None
        if s["up"] is not None:
            try:
                return round(uptime() - s["up"], 2)
            except OSError:
                pass
        return round(time.time() - s["epoch"], 2)

    def logging(self):
        a = self.age()
        return bool(self.name) and a is not None and a < config.LIVE_S


LIVE = Live()
