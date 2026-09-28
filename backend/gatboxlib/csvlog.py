"""Read a gatbox-raillog CSV: header lines, marks and samples.

    iso_time,epoch,value,unit,flags,uptime_s
    # clock=ntp | rtc (...) | unverified (...)
    # mode=VDC                                   (from 2026-09-28 on; older files: none)
    # profile=rail-5v
    # window=4.75..5.25 source=profile|machine:<slug>|user     (or: window=none source=…)
    # alarm_hi=5.775                                           (or: alarm_hi=none)
    # machine=<slug>                                           (only when one is set)
    # settling=<iso>,<epoch>,<value>,<unit>,<flags>            (the first reading after a dial turn, set aside)
    samples…
    # mark=<iso>,<epoch>,<uptime_s>,<source>,<label>           (and mark-before-start=… for marks made between files)
    # clock-sync=ntp at <iso>
    # mode-change=<iso>,<new mode>                             (why the file ended)

Old files carry only '# clock=' (or nothing): Header.profile is None and the callers use today's defaults.
A power cut can leave NUL bytes at the end: they're dropped.
"""
import math
from collections import namedtuple

from .modes import mode

Row = namedtuple("Row", "epoch iso v unit flags up mode")
Mark = namedtuple("Mark", "iso epoch up source label before_start")


class Header:
    def __init__(self):
        self.clock = self.clock_note = self.clock_sync = None
        self.mode = self.profile = self.machine = None
        self.window = None                 # [lo, hi] or None
        self.window_source = None
        self.alarm_hi = None
        self.alarm_none = False            # '# alarm_hi=none' written explicitly
        self.settling = []                 # raw "<iso>,<epoch>,<value>,<unit>,<flags>"
        self.mode_change = None
        self.notes = []                    # every comment line, '#' stripped, in order

    def as_dict(self):
        return {k: v for k, v in vars(self).items() if k != "notes"}


def _num(s):
    try:
        x = float(s)
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def _comment(h, marks, c, t0, t1):
    key, _, val = c.partition("=")
    if key == "clock":
        h.clock_note, h.clock = c, val.split("(")[0].strip() or None
    elif key == "clock-sync":
        h.clock_sync = val.rsplit(" at ", 1)[-1].strip()
    elif key == "mode":
        h.mode = val.strip() or None
    elif key == "profile":
        h.profile = val.strip() or None
    elif key == "machine":
        h.machine = val.strip() or None
    elif key == "window":
        rng, _, src = val.partition(" source=")
        h.window_source = src.strip() or None
        lo, sep, hi = rng.partition("..")
        if sep and _num(lo) is not None and _num(hi) is not None:
            h.window = [_num(lo), _num(hi)]
    elif key == "alarm_hi":
        h.alarm_hi = _num(val)
        h.alarm_none = val.strip() == "none"
    elif key == "settling":
        h.settling.append(val)
    elif key == "mode-change":
        h.mode_change = val
    elif key in ("mark", "mark-before-start"):
        p = val.split(",", 4)
        if len(p) == 5 and _num(p[1]) is not None:
            iso = p[0]
            if key == "mark-before-start" and t0:
                return
            if (t0 and iso < t0) or (t1 and iso > t1):
                return
            marks.append(Mark(iso, float(p[1]), _num(p[2]), p[3], p[4], key == "mark-before-start"))


def read(path, t0=None, t1=None):
    """(Header, [Row], [Mark]); rows (and marks) limited to iso_time in [t0, t1] when given."""
    h, rows, marks = Header(), [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "\0" in line:
                line = line.replace("\0", "")
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("iso_time"):
                continue
            if line.startswith("#"):
                c = line[1:].strip()
                h.notes.append(c)
                _comment(h, marks, c, t0, t1)
                continue
            p = line.split(",")
            if len(p) < 5:
                continue
            iso, epoch, val, unit, flags = p[:5]
            if (t0 and iso < t0) or (t1 and iso > t1):
                continue
            try:
                e = float(epoch)
            except ValueError:
                continue
            m = mode(unit, flags)
            try:
                v = float(val) * m.scale                 # 'inf' / '-inf' parse as infinities
            except ValueError:
                v = math.nan
            up = None
            if len(p) >= 6 and p[5]:
                try:
                    up = float(p[5])
                except ValueError:
                    up = None
            rows.append(Row(e, iso, v, unit, flags, up, m))
    return h, rows, marks
