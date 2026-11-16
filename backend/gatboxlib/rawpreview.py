"""The picture inside a camera raw file (a Nikon NEF, and the other raws that are TIFF files underneath).

The raw data cannot be drawn, but the file carries pictures that can: usually a JPEG preview, sometimes several of different
sizes, and in the oldest cameras only a small uncompressed thumbnail. They are found by walking the file's TIFF directories:
IFD0 and the directories chained after it, its SubIFDs, the EXIF directory, and Nikon's own preview directory inside the
MakerNote. The largest JPEG that decodes wins; a small uncompressed RGB thumbnail is the fallback.

Standard library and Pillow, and bounded, because the file is whatever a drive holds: a directory seen twice is not walked
again, only a few dozen directories are visited, every count and offset is checked against the file's length, and a file this
cannot make sense of gives None, never an exception.

Written from the TIFF and EXIF layouts in general; it has been tried on invented files (tests/test-browse-raw.py) and, as of
2026-11-16, on no real camera's NEF. The browser says so in BRINGUP until one has been looked at on the Pi."""
import io
import struct

MAX_IFDS = 40                       # directories visited in one file
MAX_ENTRIES = 512                   # entries in one directory
MAX_VALUES = 4096                   # values read from one entry
RGB_MAX_PIXELS = 4_000_000          # an uncompressed thumbnail is small; anything bigger is not one
MIN_JPEG = 512                      # shorter than this is not a picture
READ_CAP = 96 * 1024 * 1024         # the browser's own preview limit is 64 MB; a bit of room

_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1}
_FMT = {1: "B", 2: "B", 3: "H", 4: "I", 7: "B"}
_ORIENT = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE", 6: "ROTATE_270", 7: "TRANSVERSE",
           8: "ROTATE_90"}


class _Tiff:
    """One TIFF header's worth of the file. `base` is where that header starts: offsets inside are counted from it, which is not
    the start of the file for Nikon's MakerNote, which carries a small TIFF of its own."""

    def __init__(self, data, base=0):
        self.d, self.base = data, base
        order = data[base:base + 2]
        if order == b"II":
            self.e = "<"
        elif order == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF header")
        magic, self.first = struct.unpack(self.e + "HI", self._at(base + 2, 6))
        if magic != 42:
            raise ValueError("not a TIFF header")

    def _at(self, pos, n):
        if pos < 0 or n < 0 or pos + n > len(self.d):
            raise ValueError("outside the file")
        return self.d[pos:pos + n]

    def ifd(self, off):
        """({tag: (type, count, position of the 4-byte value field)}, offset of the next directory)."""
        p = self.base + off
        (n,) = struct.unpack(self.e + "H", self._at(p, 2))
        if n > MAX_ENTRIES:
            raise ValueError("too many entries")
        raw = self._at(p + 2, 12 * n + 4)
        ents = {}
        for i in range(n):
            tag, typ, cnt = struct.unpack(self.e + "HHI", raw[12 * i:12 * i + 8])
            ents[tag] = (typ, cnt, p + 2 + 12 * i + 8)
        (nxt,) = struct.unpack(self.e + "I", raw[12 * n:12 * n + 4])
        return ents, nxt

    def values(self, ent):
        """The integers of a BYTE, SHORT or LONG entry (a few thousand at most); [] for an entry this does not read."""
        if ent is None:
            return []
        typ, cnt, pos = ent
        size = _SIZE.get(typ)
        if size is None:
            return []
        cnt = min(cnt, MAX_VALUES)
        total = size * cnt
        start = pos if total <= 4 else self.base + struct.unpack(self.e + "I", self._at(pos, 4))[0]
        return list(struct.unpack(self.e + _FMT[typ] * cnt, self._at(start, total)))

    def raw(self, ent, limit=1 << 20):
        """(absolute position, bytes) of an entry's data: the MakerNote is one."""
        typ, cnt, pos = ent
        cnt = min(cnt, limit)
        start = pos if cnt <= 4 else self.base + struct.unpack(self.e + "I", self._at(pos, 4))[0]
        return start, self._at(start, cnt)


def _first(vals):
    return vals[0] if vals else None


def _collect(t, ents, st, depth):
    """Note every picture this directory describes: a JPEG by offset and length (JPEGInterchangeFormat, or one JPEG strip), an
    uncompressed RGB thumbnail by its strips."""
    comp = _first(t.values(ents.get(0x0103)))
    w, h = _first(t.values(ents.get(0x0100))), _first(t.values(ents.get(0x0101)))
    if depth == 0 and st["ori"] is None:
        st["ori"] = _first(t.values(ents.get(0x0112)))
    if 0x0201 in ents and 0x0202 in ents:
        a, n = _first(t.values(ents[0x0201])), _first(t.values(ents[0x0202]))
        if a is not None and n:
            st["jpeg"].add((n, t.base + a))
    offs, cnts = t.values(ents.get(0x0111)), t.values(ents.get(0x0117))
    if comp in (6, 7) and len(offs) == 1 and len(cnts) == 1 and cnts[0]:
        st["jpeg"].add((cnts[0], t.base + offs[0]))
    if (comp == 1 and w and h and w * h <= RGB_MAX_PIXELS and _first(t.values(ents.get(0x0106))) == 2
            and t.values(ents.get(0x0115)) == [3] and t.values(ents.get(0x0102)) == [8, 8, 8]
            and offs and len(offs) == len(cnts) and sum(cnts) == w * h * 3):
        st["rgb"].add((w * h, w, h, tuple(t.base + o for o in offs), tuple(cnts)))


def _walk(t, off, st, depth):
    """A directory, the ones chained after it, and the ones it points to."""
    while off:
        key = (t.base, off)
        if key in st["seen"] or len(st["seen"]) >= MAX_IFDS or depth > 4:
            return
        st["seen"].add(key)
        try:
            ents, nxt = t.ifd(off)
        except (ValueError, struct.error):
            return
        try:
            _collect(t, ents, st, depth)
            for sub in t.values(ents.get(0x014A)):                        # SubIFDs: the larger previews usually live here
                _walk(t, sub, st, depth + 1)
            exif = _first(t.values(ents.get(0x8769)))                     # the EXIF directory, and its MakerNote
            if exif:
                _walk(t, exif, st, depth + 1)
            if 0x927C in ents:
                _maker(t, ents[0x927C], st, depth)
        except (ValueError, struct.error):
            pass
        off = nxt


def _maker(t, ent, st, depth):
    """Nikon's MakerNote: "Nikon\\0", a version, two spare bytes, then a TIFF header of its own. Its PreviewIFD (tag 0x0011) holds
    a JPEG whose offsets are counted from that inner header."""
    start, note = t.raw(ent)
    if note[:6] == b"Nikon\x00" and note[6:7] == b"\x02":
        inner = _Tiff(t.d, start + 10)
        ents, _ = inner.ifd(inner.first)
        pv = _first(inner.values(ents.get(0x0011)))
        if pv:
            _walk(inner, pv, st, depth + 1)


def _orient(im, ori):
    """The file's own orientation (IFD0), unless the preview already carries one, which the caller applies."""
    from PIL import Image
    if ori in _ORIENT and not im.getexif().get(0x0112):
        return im.transpose(getattr(Image.Transpose, _ORIENT[ori]))
    return im


def preview_image(path, max_pixels=50_000_000, edge=2400):
    """A PIL image of the largest picture inside a raw file, or None. The image is decoded (a big JPEG at a fraction of its size,
    still at least `edge` pixels on its long side), so the caller can resize it without reading the file again."""
    try:
        with open(path, "rb") as f:
            data = f.read(READ_CAP + 1)
        if len(data) > READ_CAP:
            return None
        t = _Tiff(data)
    except (OSError, ValueError, struct.error):
        return None
    st = {"seen": set(), "jpeg": set(), "rgb": set(), "ori": None}
    _walk(t, t.first, st, 0)
    try:
        from PIL import Image
    except ImportError:
        return None
    for length, off in sorted(st["jpeg"], reverse=True):                   # the longest first: the biggest picture
        blob = data[off:off + length] if off >= 0 else b""
        if len(blob) != length or length < MIN_JPEG or blob[:3] != b"\xff\xd8\xff":
            continue
        try:
            im = Image.open(io.BytesIO(blob))
            if im.size[0] * im.size[1] > max_pixels:
                continue
            im.draft("RGB", (edge, edge))
            im.load()
        except Exception:                                                  # a damaged preview: the next one, not a failure
            continue
        return _orient(im, st["ori"])
    for _, w, h, offs, cnts in sorted(st["rgb"], reverse=True):
        try:
            pix = b"".join(t._at(o, c) for o, c in zip(offs, cnts))
            if len(pix) == w * h * 3:
                from PIL import Image as _I
                return _orient(_I.frombytes("RGB", (w, h), pix), st["ori"])
        except (ValueError, struct.error):
            continue
    return None
