"""Text out of a document, for the drive browser's content search (F3 of docs/plans/2026-11-15-drive-browser.md).

Each reader takes the path of a COPY of the file (the browser reads it out of an image with icat into its own temporary file;
nothing here ever touches an image) and returns its text, or says in a word why it cannot. The files are other people's, on
drives of unknown history, so every reader is built to be refused rather than trusted:

- Office and ePub files are zips, read by hand: a limit on the number of parts, a limit on how much any part and the whole
  file may expand to, a refusal when the expansion ratio is a zip bomb, and a refusal of a password-protected part. The XML in
  them is not parsed (no XML parser, so no entity tricks): the text is lifted out with patterns, and the five predefined and
  numeric entities are decoded.
- PDFs and the old Word, Excel and PowerPoint formats go to poppler's `pdftotext` and catdoc's `catdoc`, `xls2csv` and `catppt`,
  through the browse core's own runner: a time limit, the whole process group killed, output cut at a limit.
- Plain text and HTML are read up to a limit, decoded as UTF-8 or UTF-16 (with a byte-order mark), else as Windows-1252.
- At most TEXT_CAP characters are kept, with the white space folded, and the answer says when it cut.

A reader raises Unreadable(code, sentence). The codes are the ones the screen counts, so an empty search result can say why a
file was not searched: `encrypted`, `damaged`, `no text` (a scan: OCR is a later feature), `too big`, `too slow`, `binary`,
`no reader`.
"""
import html
import os
import re
import shutil
import zipfile
from html.parser import HTMLParser

from . import browse as B

MiB = 1 << 20
TEXT_CAP = 1 << 20                      # characters kept per file for the index
RAW_CAP = 8 * MiB                       # bytes read from a plain text or HTML file
TOOL_OUT_CAP = 4 * TEXT_CAP             # bytes taken from pdftotext, catdoc, xls2csv or catppt
TOOL_TIMEOUT = 60                       # seconds for any one of those
ZIP_MEMBERS = 4000                      # parts in a zip
ZIP_PART_CAP = 32 * MiB                 # bytes any one part may expand to
ZIP_TOTAL_CAP = 128 * MiB               # and all the parts read from one file
ZIP_RATIO = 200                         # expanded : stored beyond this is a zip bomb
READABLE = ("txt", "md", "csv", "html", "htm", "pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "doc", "xls", "ppt")


class Unreadable(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _fold(text):
    return re.sub(r"\s+", " ", text).strip()


def _decode(data):
    """Bytes to text: a byte-order mark says UTF-8 or UTF-16, else UTF-8 if it is, else Windows-1252. Mostly NUL or control
    bytes is not text."""
    if data[:3] == b"\xef\xbb\xbf":
        text = data[3:].decode("utf-8", "replace")
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", "replace")
    else:
        if b"\x00" in data[:4096]:                       # text has no NUL bytes (UTF-16 without a mark is read as binary, and said so)
            raise Unreadable("binary", "this is not a text file")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", "replace")
    if data and sum(1 for c in text[:4096] if c == "\x00" or (c < " " and c not in "\t\r\n\f")) > len(text[:4096]) * 0.1:
        raise Unreadable("binary", "this is not a text file")
    return text


def _read_raw(path, cap=RAW_CAP):
    try:
        with open(path, "rb") as f:
            return f.read(cap)
    except OSError:
        raise Unreadable("damaged", "the copy of the file could not be read") from None


# --- plain text, HTML ----------------------------------------------------------------------------------------------------------

class _Strip(HTMLParser):
    """The text of an HTML or XHTML page: no scripts, no styles, a break at each block."""
    BLOCKS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def _html_text(text):
    p = _Strip()
    try:
        p.feed(text)
        p.close()
    except Exception:                                   # the parser is tolerant; what it cannot take is not worth reading
        raise Unreadable("damaged", "this page could not be read") from None
    return "".join(p.out)


def _plain(path, ext):
    text = _decode(_read_raw(path))
    return _html_text(text) if ext in ("html", "htm") else text


# --- zips: Office and ePub -----------------------------------------------------------------------------------------------------

def _zip_parts(path, want):
    """Yield (name, text) of the parts the predicate wants, read as UTF-8 with every zip limit applied."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise Unreadable("damaged", "this file is not a readable zip") from None
    with z:
        infos = z.infolist()
        if len(infos) > ZIP_MEMBERS:
            raise Unreadable("damaged", f"more than {ZIP_MEMBERS} parts: not read")
        total = 0
        for info in infos:
            if not want(info.filename):
                continue
            if info.flag_bits & 0x1:
                raise Unreadable("encrypted", "this file is password-protected")
            if info.file_size > ZIP_PART_CAP or (info.compress_size and info.file_size / info.compress_size > ZIP_RATIO and info.file_size > MiB):
                raise Unreadable("too big", "a part of this file expands to far more than it should")
            total += info.file_size
            if total > ZIP_TOTAL_CAP:
                raise Unreadable("too big", "this file expands to far more than it should")
            try:
                with z.open(info) as f:
                    data = f.read(ZIP_PART_CAP + 1)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError):
                raise Unreadable("damaged", "a part of this file could not be unpacked") from None
            if len(data) > ZIP_PART_CAP:
                raise Unreadable("too big", "a part of this file expands to far more than it should")
            yield info.filename, data.decode("utf-8", "replace")


def _entities(s):
    return html.unescape(s)


def _runs(xml, tag, brk):
    """The text of every <tag ...>..</tag> in an XML string, with a newline at each <brk>."""
    out = []
    for m in re.finditer(rf"<{tag}(?:\s[^>]*)?>([^<]*)</{tag}>|<({brk})[\s/>]|</({brk})>", xml):
        if m.group(1) is not None:
            out.append(_entities(m.group(1)))
        else:
            out.append("\n")
    return "".join(out)


def _docx(path):
    parts = sorted(_zip_parts(path, lambda n: n == "word/document.xml" or re.fullmatch(r"word/(footnotes|endnotes|header\d*|footer\d*)\.xml", n)))
    if not parts:
        raise Unreadable("damaged", "this is not a Word document")
    return "\n".join(_runs(x, r"w:t", r"w:p") for _, x in parts)


def _pptx(path):
    def num(n):
        m = re.search(r"(\d+)\.xml$", n)
        return int(m.group(1)) if m else 0
    parts = sorted(_zip_parts(path, lambda n: re.fullmatch(r"ppt/(slides|notesSlides)/[^/]+\.xml", n) is not None),
                   key=lambda p: (p[0].startswith("ppt/notesSlides"), num(p[0])))
    if not parts:
        raise Unreadable("damaged", "this is not a PowerPoint file")
    return "\n".join(_runs(x, r"a:t", r"a:p") for _, x in parts)


def _xlsx(path):
    parts = dict(_zip_parts(path, lambda n: n in ("xl/sharedStrings.xml", "xl/workbook.xml")))
    if not parts:
        raise Unreadable("damaged", "this is not an Excel file")
    sheets = re.findall(r'<sheet\s[^>]*name="([^"]*)"', parts.get("xl/workbook.xml", ""))
    strings = _runs(parts.get("xl/sharedStrings.xml", ""), r"t", r"si")
    return "\n".join([_entities(s) for s in sheets] + [strings])


def _odf(path):
    parts = list(_zip_parts(path, lambda n: n == "content.xml"))
    if not parts:
        raise Unreadable("damaged", "this is not an OpenDocument file")
    xml = parts[0][1]
    xml = re.sub(r"</(text:p|text:h|table:table-row|text:list-item)>|<text:(tab|line-break)\s*/>", "\n", xml)
    xml = re.sub(r"<text:s\s*/>", " ", xml)
    return _entities(re.sub(r"<[^>]*>", "", xml))


def _epub(path):
    parts = sorted(_zip_parts(path, lambda n: n.lower().endswith((".xhtml", ".html", ".htm"))))
    if not parts:
        raise Unreadable("damaged", "this is not an ePub book")
    return "\n".join(_html_text(x) for _, x in parts)


# --- tools: PDF and the old Office formats -------------------------------------------------------------------------------------

def _tool(argv, what):
    if shutil.which(argv[0]) is None:
        raise Unreadable("no reader", f"{argv[0]} is not installed here, so {what} cannot be read")
    try:
        rc, out, err = B.run(argv, TOOL_TIMEOUT, cap=TOOL_OUT_CAP)
    except B.Err:
        raise Unreadable("too slow", "reading this took too long, so it was stopped") from None
    return rc, out, err


def _pdf(path):
    rc, out, err = _tool(["pdftotext", "-q", "-enc", "UTF-8", "-nopgbrk", path, "-"], "a PDF")
    low = err.lower()
    if "password" in low or "encrypt" in low:
        raise Unreadable("encrypted", "this PDF is password-protected")
    if rc != 0 and not out:
        raise Unreadable("damaged", "this PDF could not be read")
    return out.decode("utf-8", "replace")


def _legacy(prog, what):
    def read(path):
        argv = ["catdoc", "-w", "-d", "utf-8", path] if prog == "catdoc" else [prog, "-d", "utf-8", path]
        rc, out, err = _tool(argv, what)
        if rc != 0 and not out.strip():
            if "password" in err.lower() or "encrypt" in err.lower():
                raise Unreadable("encrypted", "this file is password-protected")
            raise Unreadable("damaged", "this file could not be read")
        return out.decode("utf-8", "replace")
    return read


READERS = {
    "txt": _plain, "md": _plain, "csv": _plain, "html": _plain, "htm": _plain,
    "pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "odt": _odf, "ods": _odf, "odp": _odf, "epub": _epub,
    "doc": _legacy("catdoc", "an old Word file"), "xls": _legacy("xls2csv", "an old Excel file"), "ppt": _legacy("catppt", "an old PowerPoint file"),
}


def extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def readable(name):
    """True when there is a reader for this file name's extension."""
    return extension(name) in READERS


def extract(path, ext):
    """(text, cut) of the file at `path`: the text with its white space folded and at most TEXT_CAP characters, and whether it
    was cut. Raises Unreadable with a code and a sentence."""
    reader = READERS.get(ext)
    if reader is None:
        raise Unreadable("no reader", f"there is no reader for .{ext} files")
    if reader is _plain:
        text = reader(path, ext)
    else:
        text = reader(path)
    text = _fold(text)
    if not text:
        raise Unreadable("no text", "there is no text in this file (a scan, or empty)")
    cut = len(text) > TEXT_CAP
    return text[:TEXT_CAP], cut
