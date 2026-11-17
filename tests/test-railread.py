#!/usr/bin/env python3
"""READ LIMITS (gatboxlib/railread.py and the manuals job's read_limits): supply-rail limits read off a manual's pages, OCR for a
page with no text. Every manual line here is invented; the PDFs are built by hand, and tesseract is tests/fake-tesseract.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
os.environ["GATBOX_TESSERACT"] = os.path.join(REPO, "tests", "fake-tesseract")
from gatboxlib import railread as RR  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def pdf(path, pages):
    """A PDF with one page per entry: a list of text lines, or None for a page with no text at all (a scan, to pdftotext)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        body = b""
        if lines:
            body = b"BT /F1 11 Tf 40 740 Td 14 TL " + b" ".join(b"(%s) Tj T*" % ln.replace("(", r"\(").replace(")", r"\)").encode("latin-1")
                                                             for ln in lines) + b" ET"
        objs.append(b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream")
        cont = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>" % cont)
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    open(path, "wb").write(out)


def lims(text, how=None):
    return [(c["rail"], c["lo"], c["hi"]) for c in RR.find_limits(text if isinstance(text, list) else [text], how)]


def main():
    print("the parser: what a manual says, and what it doesn't")
    cases = [
        ("+5V DC ...... 4.75V to 5.25V", [("+5V", 4.75, 5.25)]),
        ("+12V DC: 11.4 - 12.6 V", [("+12V", 11.4, 12.6)]),
        ("-5V  4.75 to 5.25 volts", [("-5V", -5.25, -4.75)]),
        ("-5V: -4.9 to -5.1V", [("-5V", -5.1, -4.9)]),
        ("Voltage must be between 4.9V and 5.1V at the PCB.", [("+5V", 4.9, 5.1)]),
        ("+12 VDC ±5%", [("+12V", 11.4, 12.6)]),
        ("-12V +/- 0.6 V", [("-12V", -12.6, -11.4)]),
        ("+3.3V 3.2-3.4V", [("+3.3V", 3.2, 3.4)]),
        ("+5 V  4.75~5.25 V", [("+5V", 4.75, 5.25)]),
        ("Logic supply +5V: 5.00 V +/- 0.10 V", [("+5V", 4.9, 5.1)]),
    ]
    for text, want in cases:
        check(f"{text!r} -> {want}", lims(text) == want)
    for text in ("Adjust +5V to 5.0V at the edge connector.", "Part no. 12-345-6789, page 4 to 5", "Rev 2.0 to 2.1",
                 "Fuse F1 5A 250V", "Use 22 to 24 AWG wire", "+5V supply: see page 12", "Pins 4 to 5 are +5V"):
        check(f"no limit in {text!r}", lims(text) == [])
    check("a limit on two pages: one proposal, from the first page", [c["page"] for c in RR.find_limits(["+5V 4.75 to 5.25V", "x", "+5V 4.75 to 5.25V"])] == [1])
    two = RR.find_limits(["+5V 4.75 to 5.25 V and +12 V 11.5 V to 12.5 V"])
    check("two rails on one line: both", {(c["rail"], c["lo"], c["hi"]) for c in two} == {("+5V", 4.75, 5.25), ("+12V", 11.5, 12.5)})
    q = RR.find_limits(["   +5V      4.75V   to   5.25V   " + "x" * 300])[0]["quote"]
    check("the quote is the line, spaces squeezed, at most 200 characters", q.startswith("+5V 4.75V to 5.25V") and len(q) <= 200)
    check("the page a limit came from, and how it was read", RR.find_limits(["nothing", "+5V 4.9-5.1V"], ["text", "ocr"])[0] == {
        "rail": "+5V", "lo": 4.9, "hi": 5.1, "page": 2, "quote": "+5V 4.9-5.1V", "how": "ocr"})

    tmp = tempfile.mkdtemp(prefix="gatbox-railread-")
    try:
        print("a manual's pages: text where there is some, OCR for a page with none")
        p = os.path.join(tmp, "manual.pdf")
        pdf(p, [["WIDGET WARS OPERATORS MANUAL", "POWER SUPPLY", "+5V DC 4.75V to 5.25V"], None, ["SECTION 3 - NEGATIVE SUPPLY CHECK", "-5V 4.75 to 5.25 volts"]])
        r = RR.read_limits(p, tmp)
        got = [(c["rail"], c["lo"], c["hi"], c["page"], c["how"]) for c in r["candidates"]]
        check("3 pages, 1 read by OCR", r["pages"] == 3 and r["ocr_pages"] == 1)
        check("the text pages' limits, and the scanned page's clean line (+12V) marked OCR", got == [
            ("+5V", 4.75, 5.25, 1, "text"), ("+12V", 11.4, 12.6, 2, "ocr"), ("-5V", -5.25, -4.75, 3, "text")])
        check("the OCR misread (5.2S) proposes nothing rather than a wrong number", not any(c["page"] == 2 and c["rail"] == "+5V" for c in r["candidates"]))
        check("nothing left in the scratch folder (the page image is removed)", os.listdir(tmp) == ["manual.pdf"])
        seen = []
        RR.read_limits(p, tmp, on_page=lambda n, total, how: seen.append((n, total, how)))
        check("it says which page it is on", seen == [(1, 3, "text"), (2, 3, "ocr"), (3, 3, "text")])
        r = RR.read_limits(p, tmp, budget=0)
        check("the OCR time limit spent: the scanned page is skipped, and the notes say so", r["ocr_pages"] == 0
              and any("time limit" in n for n in r["notes"]) and len(r["candidates"]) == 2)
        old = RR.TESSERACT
        RR.TESSERACT = "/nonexistent/tesseract"
        r = RR.read_limits(p, tmp)
        RR.TESSERACT = old
        check("no tesseract on the Pi: the text pages still read, and the notes say OCR isn't installed",
              len(r["candidates"]) == 2 and any("isn't installed" in n for n in r["notes"]))
        os.environ["FAKE_TESSERACT_FAIL"] = "1"
        r = RR.read_limits(p, tmp)
        del os.environ["FAKE_TESSERACT_FAIL"]
        check("tesseract failing on a page: that page gives nothing, the rest carry on", len(r["candidates"]) == 2 and r["ocr_pages"] == 1)

        print("the manuals job: op read_limits, and after a fetch")
        dest, sp = os.path.join(tmp, "manuals"), os.path.join(tmp, "spool")
        os.makedirs(os.path.join(dest, "widget-wars"))
        os.makedirs(sp)
        shutil.copy(p, os.path.join(dest, "widget-wars", "operators-manual.pdf"))
        env = dict(os.environ, GATBOX_MANUALS=dest, GATBOX_LIB=os.path.join(REPO, "backend"), PYTHONDONTWRITEBYTECODE="1")

        def job(req):
            json.dump(req, open(os.path.join(sp, "request.json"), "w"))
            subprocess.run([sys.executable, os.path.join(REPO, "tools", "gatbox-manuals"), "--job", os.path.join(sp, "request.json"),
                            "--status", os.path.join(sp, "status.json")], env=env, capture_output=True, timeout=120)
            return json.load(open(os.path.join(sp, "status.json")))
        st = job({"op": "read_limits", "id": "j1", "key": "widget-wars", "folder": "widget-wars", "doc_id": "operators-manual"})
        lj = os.path.join(dest, "widget-wars", "operators-manual.limits.json")
        check("read_limits: done, and the status counts them", st["state"] == "done" and st["limits"]["found"] == 3
              and st["limits"]["pages"] == 3 and st["limits"]["ocr_pages"] == 1)
        out = json.load(open(lj)) if os.path.exists(lj) else {}
        check("... <id>.limits.json beside the PDF, with every proposal's page, words and how it was read",
              out.get("doc_id") == "operators-manual" and len(out.get("candidates", [])) == 3 and out["candidates"][1]["how"] == "ocr")
        check("... and nothing else was written to the folder", sorted(os.listdir(os.path.join(dest, "widget-wars"))) ==
              ["operators-manual.limits.json", "operators-manual.pdf"])
        for bad, why in (({"folder": "../etc", "doc_id": "x"}, "malformed"), ({"folder": "widget-wars", "doc_id": "nope"}, "no nope.pdf")):
            st = job(dict({"op": "read_limits", "id": "j2", "key": "widget-wars"}, **bad))
            check(f"read_limits refuses {bad}: stopped, saying {why!r}", st["state"] == "stopped" and why in st["error"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"railread: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
