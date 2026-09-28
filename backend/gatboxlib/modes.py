"""Meter modes from sigrok's unit + flags columns, and SI formatting.

Shared by gatbox-web, gatbox-rail-report and gatbox-meta. gatbox-raillog has the same key rules in bash
(mode_key); tests/test-modes.sh checks the two agree on every case.

gatbox-raillog splits sigrok's "P1: 119.68 V AC AUTO" into value, unit (SI prefix + unit) and flags.
Continuity has no unit, so its first flag (if any) lands in the unit column: "1,AUTO,".
"""
import functools
from collections import namedtuple

FLAGS = {"AC", "DC", "RMS", "DIODE", "HOLD", "MAX", "MIN", "AUTO", "REL", "AVG", "REF", "UNSTABLE"}
PREFIX = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0,
          "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}   # sigrok can tag OL as e.g. "inf TV"
KIND = {"V": "voltage", "A": "current", "Ω": "resistance", "F": "capacitance", "Hz": "frequency",
        "%": "duty cycle", "": "continuity"}
BASE_KEY = {"Ω": "OHM", "F": "CAP", "Hz": "HZ", "%": "DUTY", "": "CONT"}
WARN = {"HOLD": ("HOLD", "the meter repeats one frozen reading"),     # these make the log misleading
        "REL": ("REL (Δ)", "readings are offsets, not the real value"),
        "MAX": ("MAX/MIN", "the meter sends its max or min, not the live value"),
        "MIN": ("MAX/MIN", "the meter sends its max or min, not the live value")}
RAIL = "DC voltage"   # the only mode a rail window applies to
Mode = namedtuple("Mode", "label base scale unit warn key")


@functools.lru_cache(maxsize=256)
def mode(unit, flags):
    """CSV unit + flags -> Mode, e.g. ('kΩ', 'AUTO') -> label 'Resistance', base 'Ω', scale 1e3, key 'OHM'."""
    fl = flags.split()
    unit = unit.replace("Ω", "Ω")          # sigrok writes OHM SIGN; "Ω" here is Greek omega
    if unit in FLAGS:
        fl, unit = [unit] + fl, ""
    base, scale = unit, 1.0
    for b in ("V", "A", "Ω", "F", "Hz", "%"):
        if unit.endswith(b) and unit[:-len(b)] in PREFIX:
            base, scale = b, PREFIX[unit[:-len(b)]]
            break
    ac, dc = "AC" in fl, "DC" in fl
    if "DIODE" in fl:
        label, key = "diode", "DIODE"
    elif base in ("V", "A"):
        label = ("AC+DC " if ac and dc else "AC " if ac else "DC " if dc else "") + KIND[base]
        if base == "V":
            key = "VACDC" if ac and dc else "VAC" if ac else "VDC"
        else:
            key = "AAC" if ac else "ADC"
    elif base in KIND:
        label, key = KIND[base], BASE_KEY[base]
    else:
        label, key = unit, "UNKNOWN"                   # not a UT61E unit: show it as sigrok wrote it
    if base in KIND:
        label = label[:1].upper() + label[1:]
    return Mode(label, base, scale, unit, frozenset(f for f in fl if f in WARN), key)


def fmt(v, base):
    """Value in base units -> text: volts to 3 decimals, other units with an SI prefix."""
    if base == "V":
        return f"{v:.3f} V"
    if base not in KIND or base in ("%", ""):
        return f"{v:g} {base}".rstrip()
    for p, k in (("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if abs(v) >= k:
            return f"{v / k:.4g} {p}{base}"
    return f"{v:g} {base}"
