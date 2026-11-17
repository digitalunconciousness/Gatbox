"""READ LIMITS: supply-rail limits read off a manual's pages, proposed for the owner to confirm (the owner, 2026-11-17).

Hard rule 12: machine-spec numbers come only from a manual the owner cites. So nothing here sets a limit. It finds lines like
"+5V 4.75V to 5.25V" or "+12 VDC ±5%", and each one goes on the spec sheet as a proposal with its page and the words it was read
from, until the owner taps CONFIRM after looking at the page (or NOT THIS).

The text of a page is pdftotext's. A page with no text layer (a scan) is read by OCR (tesseract) when it is installed, and what
OCR read is marked so: it can mistake "5.25" for "5.2S", so its proposals say "read by OCR: check the page".

A rail is named by its nominal voltage with a sign (+5V, +12V, -5V, -12V, +3.3V, +24V...). A limit is a low and a high in volts,
from an explicit range ("4.75 to 5.25", "4.9-5.1V", "between 4.75 and 5.25 volts"), a tolerance ("5V ±5%", "+5.0 V +/- 0.25 V"),
or a nominal with a range that is only digits. A nominal alone ("adjust to 5.0V") is no limit and is not proposed.
"""
import os
import re
import shutil
import subprocess
import time

PDFTOTEXT, PDFINFO, PDFTOPPM = "pdftotext", "pdfinfo", "pdftoppm"
TESSERACT = os.environ.get("GATBOX_TESSERACT", "tesseract")
MIN_TEXT = 40                      # fewer non-blank characters than this and a page is taken as a scan
OCR_BUDGET = float(os.environ.get("GATBOX_OCR_BUDGET", 15 * 60))     # seconds of OCR per manual (the job's limit is 20 minutes)
OCR_DPI = 300
QUOTE_MAX = 200
NOMINALS = (3.3, 5.0, 6.0, 9.0, 12.0, 13.0, 15.0, 24.0, 25.0, 48.0)     # what an arcade supply's rails are called

# a number that doesn't run on into a letter other than V ("5.2S" is OCR's misread of "5.25": no number at all, not 5.2)
_NUM = r"(\d{1,2}(?:\.\d{1,3})?)(?![\d.]|(?!v)[a-z])"
_V = r"\s*(?:v(?:olts?)?|vdc|vac|v\s*dc)?"
# a rail written out: a sign and a nominal, with V (or VDC) after it; "5V" without a sign counts as +5V
RAIL_RE = re.compile(r"(?<![\w.])([-+−–]?)\s*" + _NUM + r"\s*(?:v(?:olts?)?|vdc)\b", re.I)
RANGE_RES = [
    re.compile(r"between\s+([-+−–]?" + _NUM + r")" + _V + r"\s+and\s+([-+−–]?" + _NUM + r")" + _V, re.I),
    re.compile(r"([-+−–]?" + _NUM + r")" + _V + r"\s*(?:to|-|–|~|thru|through)\s*([-+−–]?" + _NUM + r")" + _V + r"(?![\d.])", re.I),
]
TOL_RE = re.compile(r"([-+−–]?)\s*" + _NUM + _V + r"\s*(?:±|\+/-|\+-|plus or minus)\s*(\d{1,2}(?:\.\d{1,3})?)\s*(%|v\b|volts?)?", re.I)


def _sign(s):
    return -1.0 if s and s in "-−–" else 1.0


def _num(s):
    s = s.strip()
    neg = s[:1] in ("-", "−", "–")
    v = float(s.lstrip("+-−–").strip())
    return -v if neg else v


def rail_name(nominal):
    """+5V, -12V, +3.3V."""
    mag = abs(nominal)
    txt = f"{mag:g}"
    return ("-" if nominal < 0 else "+") + txt + "V"


def _nominal_for(mid):
    """The rail a range belongs to: the nominal its middle is within 12 % of, with the range's sign."""
    best = min(NOMINALS, key=lambda n: abs(abs(mid) - n))
    if abs(abs(mid) - best) > best * 0.12:
        return None
    return best if mid >= 0 else -best


def _quote(line):
    q = " ".join(line.split())
    return q if len(q) <= QUOTE_MAX else q[:QUOTE_MAX - 1] + "…"


def _plausible(lo, hi, nominal):
    """A real tolerance band: lo < hi, both on the rail's side of zero, the nominal inside it, and not wider than ±15 %."""
    if not lo < hi:
        return False
    n = abs(nominal)
    a, b = sorted((abs(lo), abs(hi)))
    return a <= n <= b and a >= n * 0.85 and b <= n * 1.15 and (lo < 0) == (nominal < 0) == (hi < 0)


def find_limits(pages, how=None):
    """pages: [text of page 1, page 2, ...]. how: the same length, "text" or "ocr" per page (default all "text").
    -> [{rail, lo, hi, page, quote, how}], one per (rail, lo, hi), first page first."""
    how = how or ["text"] * len(pages)
    out, seen = [], set()
    for pno, (text, h) in enumerate(zip(pages, how), start=1):
        lines = (text or "").splitlines()
        for i, line in enumerate(lines):
            # a line about volts: a rail named on it ("+5V", "12 VDC") or the word itself; "page 4 to 5" is not one
            if not re.search(r"\d", line) or not (RAIL_RE.search(line) or re.search(r"\bvolt", line, re.I)):
                continue
            found = []
            for rx in RANGE_RES:
                for m in rx.finditer(line):
                    a, b = _num(m.group(1)), _num(m.group(3))
                    lo, hi = min(a, b), max(a, b)
                    # "-5V ... 4.75 to 5.25": a range written without its sign takes the rail's sign from the line
                    # the rails named on the line, outside this range itself (its own dash is no minus sign)
                    rails = [(_sign(r.group(1)) * float(r.group(2))) for r in RAIL_RE.finditer(line)
                             if r.end() <= m.start() or r.start() >= m.end()]
                    neg = lo > 0 and any(r < 0 and abs(abs(r) - (lo + hi) / 2) < abs(r) * 0.12 for r in rails)
                    if neg:
                        lo, hi = -hi, -lo
                    nominal = _nominal_for((lo + hi) / 2)
                    if nominal is not None and _plausible(lo, hi, nominal):
                        found.append((nominal, lo, hi))
            for m in TOL_RE.finditer(line):
                nominal = _sign(m.group(1)) * float(m.group(2))
                t = float(m.group(3))
                unit = (m.group(4) or "").lower()
                if unit == "%" or (not unit and t < 20 and t > abs(nominal) * 0.3):
                    d = abs(nominal) * t / 100
                elif unit:
                    d = t
                else:
                    continue
                lo, hi = round(nominal - d, 3), round(nominal + d, 3)
                n2 = _nominal_for(nominal)
                if n2 is not None and abs(n2 - nominal) < 0.01 * abs(n2) + 0.05 and _plausible(lo, hi, n2):
                    found.append((n2, lo, hi))
            for nominal, lo, hi in found:
                k = (rail_name(nominal), round(lo, 3), round(hi, 3))
                if k in seen:
                    continue
                seen.add(k)
                out.append({"rail": k[0], "lo": k[1], "hi": k[2], "page": pno, "quote": _quote(line), "how": h})
    return out


# --- the pages' text ---------------------------------------------------------------------------------------------------

def page_count(pdf):
    r = subprocess.run([PDFINFO, pdf], capture_output=True, text=True, timeout=60)
    m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    if not m:
        raise ValueError(f"pdfinfo could not read {os.path.basename(pdf)}")
    return int(m.group(1))


def page_text(pdf, n):
    r = subprocess.run([PDFTOTEXT, "-layout", "-f", str(n), "-l", str(n), pdf, "-"], capture_output=True, text=True,
                       timeout=60, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def have_ocr():
    return shutil.which(TESSERACT) is not None


def ocr_page(pdf, n, scratch):
    """One page as an image (pdftoppm, grey, 300 dpi) read by tesseract. "" when either fails."""
    base = os.path.join(scratch, f"p{n}")
    r = subprocess.run([PDFTOPPM, "-f", str(n), "-l", str(n), "-r", str(OCR_DPI), "-gray", "-png", "-singlefile", pdf, base],
                       capture_output=True, timeout=120)
    img = base + ".png"
    try:
        if r.returncode != 0 or not os.path.exists(img):
            return ""
        t = subprocess.run([TESSERACT, img, "-", "--psm", "6"], capture_output=True, text=True, timeout=180, errors="replace")
        return t.stdout if t.returncode == 0 else ""
    finally:
        try:
            os.remove(img)
        except OSError:
            pass


def read_pages(pdf, scratch, ocr=True, on_page=None, budget=None):
    """-> (texts, how, notes). A page with too little text is read by OCR when tesseract is here, within the time budget."""
    n = page_count(pdf)
    texts, how, notes = [], [], []
    ocr_ok, end, skipped = ocr and have_ocr(), time.monotonic() + (OCR_BUDGET if budget is None else budget), 0
    for p in range(1, n + 1):
        t = page_text(pdf, p)
        h = "text"
        if len(re.sub(r"\s", "", t)) < MIN_TEXT:
            if ocr_ok and time.monotonic() < end:
                t, h = ocr_page(pdf, p, scratch), "ocr"
            else:
                skipped += 1
        texts.append(t)
        how.append(h)
        if on_page:
            on_page(p, n, h)
    if skipped:
        notes.append(f"{skipped} page(s) with no text were not read: " +
                     ("OCR (tesseract) isn't installed" if not ocr_ok else "the OCR time limit ran out"))
    return texts, how, notes


def read_limits(pdf, scratch, ocr=True, on_page=None, budget=None):
    """The whole thing for one PDF: {pages, ocr_pages, candidates, notes, at}."""
    texts, how, notes = read_pages(pdf, scratch, ocr=ocr, on_page=on_page, budget=budget)
    return {"pages": len(texts), "ocr_pages": how.count("ocr"), "candidates": find_limits(texts, how), "notes": notes,
            "at": round(time.time(), 3)}
