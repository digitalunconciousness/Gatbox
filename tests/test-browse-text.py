#!/usr/bin/env python3
"""The drive browser's text readers (docs/plans/2026-11-15-drive-browser.md), F3: gatboxlib/browsetext.py, against files built
here: text in three encodings, HTML, a PDF with a text layer and one without, Word, Excel, PowerPoint, OpenDocument and ePub
files as zips, a zip bomb, a password-protected part, a damaged file, and stand-ins for poppler's and catdoc's programs. Nothing
real is read.

What is checked is what the content search relies on: the right words come out; a file that cannot be read says why in one of
the codes the screen counts; every limit holds (a zip that expands too far, too many parts, output over the cap, a tool that
hangs); and the module starts nothing of its own and parses no XML.

Needs poppler's pdftotext and Pillow; without them it skips, saying which.
    python3 tests/test-browse-text.py
"""
import os
import shutil
import sys
import tempfile
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))

T = tempfile.mkdtemp(prefix="gatbox-text-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def put(name, data):
    path = f"{T}/{name}"
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def zipped(name, parts, locked=False):
    path = f"{T}/{name}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)
    if locked:                                  # the encryption bit, in the local headers and in the central directory
        raw = bytearray(open(path, "rb").read())
        for sig, at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = raw.find(sig)
            while i >= 0:
                raw[i + at] |= 0x1
                i = raw.find(sig, i + 4)
        open(path, "wb").write(bytes(raw))
    return path


def pdf_with_text(text):
    """A one-page PDF with a text layer, written by hand (the offsets are computed)."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n"
    return out.encode("latin-1")


def reason(fn):
    from gatboxlib import browsetext as X
    try:
        fn()
    except X.Unreadable as e:
        return e.code
    return None


def main():
    gone = []
    if not shutil.which("pdftotext"):
        gone.append("poppler-utils (pdftotext)")
    try:
        from PIL import Image
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the text reader tests need {', '.join(gone)}")
        return 0
    from gatboxlib import browse as B
    from gatboxlib import browsetext as X
    real_path = os.environ["PATH"]

    print("plain text and HTML:")
    t, cut = X.extract(put("a.txt", "Widget Wars service manual\r\n\r\n  line   two  \n"), "txt")
    check("UTF-8: the words, with the white space folded to single spaces", t == "Widget Wars service manual line two" and cut is False)
    check("Windows-1252 (no byte-order mark, not UTF-8): café", X.extract(put("l.txt", "caf\xe9 au lait".encode("cp1252")), "txt")[0] == "café au lait")
    check("UTF-16 with a byte-order mark", X.extract(put("u.txt", "Widget über".encode("utf-16")), "txt")[0] == "Widget über")
    check("UTF-8 with a byte-order mark: the mark is not text", X.extract(put("b.txt", b"\xef\xbb\xbfhello"), "txt")[0] == "hello")
    check("a binary file named .txt is refused as binary (a single NUL byte is enough, whatever else it holds), an empty one and a blank one as no text",
          reason(lambda: X.extract(put("bin.txt", os.urandom(4000)), "txt")) == "binary"
          and reason(lambda: X.extract(put("nul.txt", b"mostly letters " * 40 + b"\x00" + b"and more letters"), "txt")) == "binary"
          and reason(lambda: X.extract(put("e.txt", b""), "txt")) == "no text" and reason(lambda: X.extract(put("w.txt", "  \n\t "), "txt")) == "no text")
    t, _ = X.extract(put("p.html", "<html><head><title>Cab notes</title><style>p{color:red}</style><script>var secret=1;</script></head>"
                         "<body><h1>Rail test</h1><p>5 V &amp; 12 V &lt;ok&gt;</p></body></html>"), "html")
    check("HTML: the text with entities decoded, the title kept, no script and no style", t == "Cab notes Rail test 5 V & 12 V <ok>" and "secret" not in t)
    check("markdown and CSV are plain text", X.extract(put("r.md", "# Notes\n* one"), "md")[0] == "# Notes * one"
          and X.extract(put("t.csv", "a,b\n1,2"), "csv")[0] == "a,b 1,2")

    print("PDF:")
    check("a PDF with a text layer", X.extract(put("t.pdf", pdf_with_text("Widget Wars manual")), "pdf")[0] == "Widget Wars manual")
    img = f"{T}/scan.pdf"
    Image.new("RGB", (200, 200), (250, 250, 250)).save(img, "PDF")
    check("a PDF that is only a picture (a scan): 'no text', which OCR would be for", reason(lambda: X.extract(img, "pdf")) == "no text")
    check("a damaged PDF: damaged", reason(lambda: X.extract(put("bad.pdf", b"%PDF-1.4\nnot really"), "pdf")) == "damaged")

    print("Office, OpenDocument and ePub files (zips read by hand):")
    docx = zipped("a.docx", {"[Content_Types].xml": "<x/>", "word/document.xml":
                             '<w:document><w:body><w:p><w:r><w:t>Widget</w:t></w:r><w:r><w:t xml:space="preserve"> Wars &amp; Co</w:t></w:r></w:p>'
                             '<w:p><w:pPr/><w:r><w:t>caf&#233; menu</w:t></w:r></w:p></w:body></w:document>',
                             "word/footnotes.xml": "<w:footnotes><w:p><w:r><w:t>a footnote</w:t></w:r></w:p></w:footnotes>"})
    check("Word (.docx): the runs of each paragraph, entities decoded, footnotes included", X.extract(docx, "docx")[0] == "Widget Wars & Co café menu a footnote")
    pptx = zipped("a.pptx", {"ppt/slides/slide2.xml": "<p:sld><a:p><a:t>second</a:t></a:p></p:sld>", "ppt/slides/slide10.xml": "<p:sld><a:p><a:t>tenth</a:t></a:p></p:sld>",
                             "ppt/slides/slide1.xml": "<p:sld><a:p><a:t>first</a:t></a:p></p:sld>", "ppt/notesSlides/notesSlide1.xml": "<p:notes><a:p><a:t>speaker note</a:t></a:p></p:notes>"})
    check("PowerPoint (.pptx): the slides in number order (10 after 2), the notes after them", X.extract(pptx, "pptx")[0] == "first second tenth speaker note")
    xlsx = zipped("a.xlsx", {"xl/workbook.xml": '<workbook><sheets><sheet name="Rail log" sheetId="1"/></sheets></workbook>',
                             "xl/sharedStrings.xml": "<sst><si><t>5V</t></si><si><t>12V &amp; ground</t></si><si><r><t>a</t></r><r><t>b</t></r></si></sst>"})
    check("Excel (.xlsx): the sheet names and the shared strings", X.extract(xlsx, "xlsx")[0] == "Rail log 5V 12V & ground ab")
    odt = zipped("a.odt", {"mimetype": "application/vnd.oasis.opendocument.text", "content.xml":
                           '<office:document-content><office:body><office:text><text:h>Title</text:h><text:p>one<text:s/>two<text:tab/>three</text:p>'
                           '<text:p>caf&#233;</text:p></office:text></office:body></office:document-content>'})
    check("OpenDocument (.odt, and .ods, .odp the same way): paragraphs, spaces and tabs", X.extract(odt, "odt")[0] == "Title one two three café"
          and X.extract(odt, "ods")[0] == X.extract(odt, "odp")[0])
    epub = zipped("a.epub", {"mimetype": "application/epub+zip", "OEBPS/ch2.xhtml": "<html><body><p>chapter two</p></body></html>", "OEBPS/ch1.xhtml": "<html><body><p>chapter one</p></body></html>",
                             "OEBPS/style.css": "p{}"})
    check("ePub: the chapters in file-name order, not the style sheet", X.extract(epub, "epub")[0] == "chapter one chapter two")
    check("a zip with the wrong parts is damaged, a file that is not a zip is damaged, a truncated one is damaged",
          reason(lambda: X.extract(zipped("w.docx", {"x.txt": "hi"}), "docx")) == "damaged"
          and reason(lambda: X.extract(put("n.docx", b"this is not a zip"), "docx")) == "damaged"
          and reason(lambda: X.extract(put("t.docx", open(docx, "rb").read()[:120]), "docx")) == "damaged")
    check("a password-protected part: encrypted, and nothing of it is read",
          reason(lambda: X.extract(zipped("p.docx", {"word/document.xml": "<w:t>secret</w:t>"}, locked=True), "docx")) == "encrypted")

    print("limits:")
    bomb = zipped("bomb.docx", {"word/document.xml": "<w:t>" + "a" * (6 * MiB) + "</w:t>"})
    check("a part that expands more than 200 times (a zip bomb, 6 MB from a few KB): too big, and it is not unpacked",
          os.path.getsize(bomb) * X.ZIP_RATIO < 6 * MiB and reason(lambda: X.extract(bomb, "docx")) == "too big")
    import random
    rng = random.Random(1)
    words = " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 9))) for _ in range(500_000))
    big = zipped("big.docx", {"word/document.xml": "<w:p><w:t>" + words + "</w:t></w:p>"})
    t, cut = X.extract(big, "docx")
    check("a real, large document (about 3 MB of words, expanding about 3 times) is read, and cut at 1 MB of text, and says so",
          len(t) == X.TEXT_CAP and cut is True and t.split(" ")[0] == words.split(" ")[0])
    old = X.ZIP_PART_CAP
    X.ZIP_PART_CAP = MiB
    check("a part over the per-part cap: too big", reason(lambda: X.extract(zipped("cap.docx", {"word/document.xml": "<w:t>" + "x y " * 600_000 + "</w:t>"}), "docx")) == "too big")
    X.ZIP_PART_CAP = old
    old = X.ZIP_MEMBERS
    X.ZIP_MEMBERS = 10
    check("more parts than the limit: damaged", reason(lambda: X.extract(zipped("many.docx", {"word/document.xml": "<w:t>x</w:t>", **{f"word/media/{i}.png": "x" for i in range(20)}}), "docx")) == "damaged"
          and X.extract(zipped("few.docx", {"word/document.xml": "<w:t>x</w:t>", **{f"word/media/{i}.png": "x" for i in range(5)}}), "docx")[0] == "x")
    X.ZIP_MEMBERS = old
    old = X.ZIP_TOTAL_CAP
    X.ZIP_TOTAL_CAP = 3000
    check("more than the total cap across the parts read: too big", reason(lambda: X.extract(zipped("tot.pptx", {f"ppt/slides/slide{i}.xml": "<a:t>" + "word " * 200 + "</a:t>" for i in range(5)}), "pptx")) == "too big")
    X.ZIP_TOTAL_CAP = old

    print("the tools (poppler and catdoc), with stand-ins for the ones a test cannot make files for:")
    stub = f"{T}/bin"
    os.makedirs(stub)

    def tool(name, body):
        with open(f"{stub}/{name}", "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(f"{stub}/{name}", 0o755)
    tool("catdoc", 'case "$*" in *good*) echo "Old   Word  text";; *locked*) echo "Password required" >&2; exit 1;; *) echo "not an OLE file" >&2; exit 1;; esac')
    tool("xls2csv", 'echo "a,b"; echo "1,2"')
    tool("catppt", 'echo "slide one"')
    os.environ["PATH"] = f"{stub}:{real_path}"
    check("catdoc, xls2csv and catppt: the text comes out, folded", X.extract(put("good.doc", b"x"), "doc")[0] == "Old Word text"
          and X.extract(put("a.xls", b"x"), "xls")[0] == "a,b 1,2" and X.extract(put("a.ppt", b"x"), "ppt")[0] == "slide one")
    check("an old file that asks for a password: encrypted; one the tool cannot read: damaged",
          reason(lambda: X.extract(put("locked.doc", b"x"), "doc")) == "encrypted" and reason(lambda: X.extract(put("bad.doc", b"x"), "doc")) == "damaged")
    tool("pdftotext", "yes 'lorem ipsum dolor sit amet' | head -c 10000000")
    t0 = time.time()
    t, cut = X.extract(put("loud.pdf", b"x"), "pdf")
    check("a tool that prints 10 MB: the output is cut at the cap, the tool is stopped, and the text is cut at 1 MB", cut is True and len(t) == X.TEXT_CAP and time.time() - t0 < 20)
    tool("pdftotext", "sleep 30")
    X.TOOL_TIMEOUT = 1
    t0 = time.time()
    check("a tool that hangs is killed at its limit: too slow", reason(lambda: X.extract(put("hang.pdf", b"x"), "pdf")) == "too slow" and time.time() - t0 < 10)
    X.TOOL_TIMEOUT = 60
    tool("pdftotext", 'echo "Command Line Error: Incorrect password" >&2; exit 1')
    check("a PDF that asks for a password: encrypted", reason(lambda: X.extract(put("lock.pdf", b"x"), "pdf")) == "encrypted")
    empty = f"{T}/empty-path"
    os.makedirs(empty)
    os.environ["PATH"] = empty
    check("a reader that is not installed: no reader", reason(lambda: X.extract(put("good.doc", b"x"), "doc")) == "no reader")
    try:
        X.extract(put("good.doc", b"x"), "doc")
    except X.Unreadable as e:
        check("... in words: 'catdoc is not installed here, so an old Word file cannot be read'", e.msg == "catdoc is not installed here, so an old Word file cannot be read")
    os.environ["PATH"] = real_path

    print("what it does and does not do:")
    check("an extension with no reader: no reader; readable() agrees", reason(lambda: X.extract(put("a.xyz", b"x"), "xyz")) == "no reader"
          and X.readable("Manual.PDF") and not X.readable("photo.jpg") and not X.readable("noextension"))
    src = open(os.path.join(REPO, "backend/gatboxlib/browsetext.py")).read()
    check("it starts nothing itself (no subprocess, no os.system): every program goes through the browse core's runner", "subprocess" not in src
          and "os.system" not in src and "os.popen" not in src)
    check("... and it parses no XML: no ElementTree, minidom, expat or sax", not any(w in src for w in ("ElementTree", "minidom", "expat", "xml.sax", "xml.dom", "lxml")))
    code = 0
    try:
        B.run(["sh", "-c", "echo hi"], 5)
    except B.Err as e:
        code = e.code
    check("the runner still refuses any program that is not a sleuthkit, poppler or catdoc one", code == 500)
    print(f"\ndrive browser F3 readers: {passed} passed, {failed} failed")
    return 1 if failed else 0


try:
    rc = main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(rc)
