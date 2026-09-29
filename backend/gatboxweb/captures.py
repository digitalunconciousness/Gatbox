"""Capture points (bench profiles): the live reading plus a label, saved to the current machine.

One CSV per machine in $STATE_DIRECTORY/captures/<slug>.csv (unassigned.csv when no machine is set):
    iso,epoch,value,unit,mode,profile,label,flags
value/unit exactly as the meter sent them (like the rail logs); flags so a HOLD or REL capture reads as one.
A capture is always the live reading: there's no way to post a value.
"""
import csv
import io
import os
import threading
import time

from . import config, roster
from .live import LIVE
from .meter import Bad, label, resolved

FIELDS = ["iso", "epoch", "value", "unit", "mode", "profile", "label", "flags"]
UNASSIGNED = "unassigned"
FRESH_S = 3.0          # the reading must be this recent: the meter sends ~2/s
_lock = threading.Lock()


def _file(slug):
    """captures/<slug>.csv for a roster slug or 'unassigned' only (never a path from a request as is)."""
    if slug != UNASSIGNED:
        known = roster.slugs() or set()
        if slug not in known:
            raise Bad(404, f"not in the roster: {slug!r}")
    return os.path.join(config.CTRL, "captures", slug + ".csv")


def read(slug):
    p = _file(slug)
    try:
        with open(p, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def add(body):
    """POST /api/captures {"label"}: the live reading, to the current machine (or unassigned)."""
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    lab = label(body.get("label"), required=True)
    last, age = LIVE.last, LIVE.age()
    if not LIVE.logging() or last is None or age is None or age > FRESH_S:
        raise Bad(409, "no live reading to capture (meter off, D02 head off, or logging stopped)")
    res = resolved()
    slug = res["machine"] or UNASSIGNED
    row = {"iso": last["iso"], "epoch": f"{last['epoch']:.3f}", "value": last["raw"], "unit": last["unit"],
           "mode": last["mode"], "profile": res["id"], "label": lab, "flags": " ".join(last["flags"])}
    p = _file(slug)
    with _lock:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        new = not os.path.exists(p)
        buf = io.StringIO()
        w = csv.DictWriter(buf, FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
    return dict(row, machine=slug, display=last["display"], captured_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
