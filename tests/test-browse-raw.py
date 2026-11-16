#!/usr/bin/env python3
"""Pictures inside camera raw files (gatboxlib/rawpreview.py, and gatboxlib/browse.py's use of it): the drive browser draws a
Nikon NEF by its embedded preview instead of showing only the file's name.

Nothing here is a real camera's file. The raws are invented to the TIFF layout: a header, directories with entries, SubIFDs, an
EXIF directory, and Nikon's MakerNote with a TIFF of its own, built by a small writer below, in both byte orders. What is checked:
the largest embedded JPEG is the one drawn, in whichever directory it hides; an uncompressed thumbnail is the fallback; the
file's own orientation is applied; a file that is damaged, looped, truncated or absurd gives nothing and never an exception;
an ordinary TIFF is left alone; and a raw drawn through the browser's own thumbnail, info and preview calls, on a FAT image.

Needs Pillow, and for the last part the sleuthkit tools, mkfs.fat and mtools; without them it skips, saying which.
    python3 tests/test-browse-raw.py
"""
import io
import json
import os
import struct
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))

T = tempfile.mkdtemp(prefix="gatbox-raw-test-")
passed = failed = 0


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


class Tiff:
    """A TIFF writer: blobs and directories appended in order, offsets counted from the header."""

    def __init__(self, order="MM"):
        self.o = order
        self.e = ">" if order == "MM" else "<"
        self.b = bytearray(order.encode() + struct.pack(self.e + "HI", 42, 0))

    def blob(self, data):
        if len(self.b) % 2:
            self.b += b"\0"
        off = len(self.b)
        self.b += data
        return off

    def ifd(self, entries, nxt=0):
        rows = []
        for tag, typ, vals in entries:
            if isinstance(vals, (bytes, bytearray)):
                data, cnt = bytes(vals), len(vals)
            else:
                data, cnt = struct.pack(self.e + {1: "B", 3: "H", 4: "I"}[typ] * len(vals), *vals), len(vals)
            field = data.ljust(4, b"\0") if len(data) <= 4 else struct.pack(self.e + "I", self.blob(data))
            rows.append(struct.pack(self.e + "HHI", tag, typ, cnt) + field)
        if len(self.b) % 2:
            self.b += b"\0"
        off = len(self.b)
        self.b += struct.pack(self.e + "H", len(rows)) + b"".join(rows) + struct.pack(self.e + "I", nxt)
        return off

    def finish(self, first):
        struct.pack_into(self.e + "I", self.b, 4, first)
        return bytes(self.b)


def jpeg(w, h, seed=1):
    """A JPEG of noise, so that its length grows with its area: the largest picture is the longest."""
    from PIL import Image
    im = Image.effect_noise((w, h), 60 + seed).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def nef(order="MM", big=(640, 480), small=(160, 120), ori=None, raw_comp=34713, where="subifd"):
    """A NEF-shaped file: IFD0 (a thumbnail's worth of tags), its chain, a SubIFD with the big JPEG, the EXIF directory with
    Nikon's MakerNote carrying a PreviewIFD with the small one. `where` moves the big JPEG: subifd, ifd1, or maker."""
    t = Tiff(order)
    big_j, small_j = jpeg(*big, seed=2), jpeg(*small, seed=3)
    # Nikon's MakerNote: "Nikon\0", version 2 1 0, two spare bytes, then a TIFF of its own (offsets counted from its header)
    inner = Tiff(order)
    pj = small_j if where != "maker" else big_j
    pj_off = inner.blob(pj)
    pv = inner.ifd([(0x0201, 4, [pj_off]), (0x0202, 4, [len(pj)])])
    inner_first = inner.ifd([(0x0011, 4, [pv])])
    note = b"Nikon\x00\x02\x10\x00\x00" + inner.finish(inner_first)
    exif = t.ifd([(0x927C, 7, note)])
    sub_entries = [(0x0100, 4, [3008]), (0x0101, 4, [2000]), (0x0103, 3, [raw_comp]), (0x0106, 3, [1])]
    if where == "subifd":
        sub_entries = [(0x0100, 4, [big[0]]), (0x0101, 4, [big[1]]), (0x0103, 3, [7]), (0x0111, 4, [t.blob(big_j)]),
                       (0x0117, 4, [len(big_j)])]
    sub = t.ifd(sub_entries)
    ifd1_entries = [(0x0103, 3, [6])]
    if where == "ifd1":
        ifd1_entries += [(0x0201, 4, [t.blob(big_j)]), (0x0202, 4, [len(big_j)])]
    ifd1 = t.ifd(ifd1_entries)
    ifd0 = [(0x0100, 4, [160]), (0x0101, 4, [120]), (0x0103, 3, [raw_comp]), (0x010F, 1, b"NIKON\x00"),
            (0x014A, 4, [sub]), (0x8769, 4, [exif])]
    if ori:
        ifd0.append((0x0112, 3, [ori]))
    return t.finish(t.ifd(sorted(ifd0), nxt=ifd1))


def rgb_thumb(w=16, h=12, order="II", ori=None):
    """A camera whose only picture is a small uncompressed one, in a single strip."""
    t = Tiff(order)
    pix = b"".join(bytes((x * 15 % 256, y * 20 % 256, 200)) for y in range(h) for x in range(w))
    entries = [(0x0100, 4, [w]), (0x0101, 4, [h]), (0x0102, 3, [8, 8, 8]), (0x0103, 3, [1]), (0x0106, 3, [2]),
               (0x0111, 4, [t.blob(pix)]), (0x0115, 3, [3]), (0x0117, 4, [len(pix)])]
    if ori:
        entries.append((0x0112, 3, [ori]))
    return t.finish(t.ifd(sorted(entries))), pix


def nef_drawable_thumb(order="MM", big=(640, 480)):
    """IFD0 a 160x120 uncompressed RGB thumbnail that Pillow draws by itself, the big JPEG in a SubIFD: the usual NEF."""
    t = Tiff(order)
    big_j = jpeg(*big, seed=5)
    sub = t.ifd(sorted([(0x0100, 4, [big[0]]), (0x0101, 4, [big[1]]), (0x0103, 3, [7]), (0x0111, 4, [t.blob(big_j)]),
                        (0x0117, 4, [len(big_j)])]))
    pix = b"".join(bytes((x % 256, y % 256, 90)) for y in range(120) for x in range(160))
    entries = [(0x0100, 4, [160]), (0x0101, 4, [120]), (0x0102, 3, [8, 8, 8]), (0x0103, 3, [1]), (0x0106, 3, [2]),
               (0x0111, 4, [t.blob(pix)]), (0x0115, 3, [3]), (0x0116, 4, [120]), (0x0117, 4, [len(pix)]), (0x014A, 4, [sub])]
    return t.finish(t.ifd(sorted(entries)))


def write(name, data):
    p = f"{T}/{name}"
    with open(p, "wb") as f:
        f.write(data)
    return p


def main():
    try:
        from PIL import Image
    except ImportError:
        print("  skip  the raw preview tests need python3-pil")
        return 0
    from gatboxlib import rawpreview as R

    print("the pictures inside a raw file:")
    for order in ("MM", "II"):
        big = R.preview_image(write(f"a{order}.nef", nef(order, where="subifd")))
        check(f"{order}: the big JPEG in a SubIFD is the one drawn (640x480, not the 160x120 in the MakerNote)", big is not None and big.size == (640, 480))
    check("the big JPEG in IFD1's JPEGInterchangeFormat is found", R.preview_image(write("ifd1.nef", nef("MM", where="ifd1"))).size == (640, 480))
    check("the big JPEG in Nikon's PreviewIFD, inside the MakerNote's own TIFF (offsets from its header), is found",
          R.preview_image(write("maker.nef", nef("II", where="maker"))).size == (640, 480))
    only_small = R.preview_image(write("small.nef", nef("MM", where="none")))
    check("when the small one in the MakerNote is all there is, that is drawn", only_small is not None and only_small.size == (160, 120))
    rgb, pix = rgb_thumb()
    im = R.preview_image(write("rgb.nef", rgb))
    check("a camera with only a small uncompressed thumbnail: that, with its pixels exactly", im is not None and im.size == (16, 12)
          and im.mode == "RGB" and im.tobytes() == pix)
    tall = R.preview_image(write("o6.nef", nef("MM", ori=6, where="subifd")))
    check("the file's own orientation (6, rotate) is applied to a preview that has none: 640x480 is drawn 480x640", tall.size == (480, 640))
    r3, _ = rgb_thumb(ori=3)
    check("orientation 3 turns an uncompressed thumbnail upside down: its first pixel is the original's last",
          R.preview_image(write("o3.nef", r3)).getpixel((0, 0)) == (15 * 15 % 256, 11 * 20 % 256, 200))
    big_edge = R.preview_image(write("huge.nef", nef("MM", big=(1600, 1200), where="subifd")), edge=300)
    check("a big preview is decoded at a fraction of its size, still at least as large as asked for (1600x1200 at edge 300 -> 400x300 or more)",
          big_edge is not None and max(big_edge.size) >= 300 and max(big_edge.size) < 1600)

    print("a file that is not what it should be gives nothing, never an exception:")
    good = nef("MM", where="subifd")
    cases = {
        "not TIFF at all": b"hello world" * 50,
        "a TIFF header and nothing more": b"MM\x00*\x00\x00\x00\x08",
        "a first directory far outside the file": b"MM\x00*\x7f\xff\xff\xf0" + b"\0" * 20,
        "cut off in the middle of the big JPEG": good[:len(good) // 2],
        "an absurd entry count (65535)": b"II*\x00\x08\x00\x00\x00\xff\xff" + b"\0" * 64,
    }
    for name, data in cases.items():
        try:
            got = R.preview_image(write("bad.nef", data))
        except Exception as e:                                                 # noqa: BLE001
            got = f"RAISED {e!r}"
        check(f"{name}: None", got is None)
    loop = Tiff("MM")
    self_off = len(loop.b)
    loop.ifd([(0x0100, 4, [10])], nxt=self_off)                                 # the next directory is itself
    check("a directory chained to itself ends the walk: None, not a hang", R.preview_image(write("loop.nef", loop.finish(self_off))) is None)
    sub_loop = Tiff("MM")
    so = len(sub_loop.b)
    sub_loop.ifd([(0x014A, 4, [so])])                                           # a SubIFD that points back at its own directory
    check("a SubIFD that points at its own directory ends the walk: None", R.preview_image(write("subloop.nef", sub_loop.finish(so))) is None)
    liar = Tiff("MM")
    lo = liar.blob(b"\xff\xd8\xff" + b"\0" * 100)
    first = liar.ifd([(0x0201, 4, [lo]), (0x0202, 4, [99999999])])
    check("a JPEG length that runs past the end of the file is not used: None", R.preview_image(write("liar.nef", liar.finish(first))) is None)
    junk = Tiff("MM")
    jo = junk.blob(b"\xff\xd8\xff" + os.urandom(2000))
    first = junk.ifd([(0x0201, 4, [jo]), (0x0202, 4, [2003])])
    check("bytes that start like a JPEG but do not decode are skipped, not drawn and not raised: None", R.preview_image(write("junk.nef", junk.finish(first))) is None)
    check("a missing file is None", R.preview_image(f"{T}/nope.nef") is None)

    print("through the browser's own calls, on a FAT image:")
    sys.path.insert(0, os.path.join(REPO, "tests"))
    import fakeimage as F
    gone = F.missing()
    if gone:
        print(f"  skip  the rest needs {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools)")
        return report()
    store = f"{T}/store"
    os.makedirs(store)
    open(f"{store}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mountinfo = f"{T}/mountinfo"
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")
    spool = f"{T}/spool"
    os.makedirs(spool)
    os.environ.update(GATBOX_DISKS=store, GATBOX_MOUNTINFO=mountinfo, GATBOX_BROWSE_DIR=f"{store}/_browse", GATBOX_DISK_SPOOL=spool,
                      GATBOX_UDEV=f"{T}/udev", GATBOX_SYSFS=f"{T}/sys", GATBOX_DEV=f"{T}/dev")
    from gatboxlib import browse as B
    tree = f"{T}/tree/DCIM"
    os.makedirs(tree)
    open(f"{tree}/DSC_0001.NEF", "wb").write(nef("MM", where="subifd"))             # IFD0 is a 160x120 thumbnail, the big one is inside
    open(f"{tree}/DSC_0002.NEF", "wb").write(nef("II", where="maker", raw_comp=1))
    open(f"{tree}/DSC_0003.NEF", "wb").write(rgb_thumb()[0])
    open(f"{tree}/DSC_0004.NEF", "wb").write(b"MM\x00*\x00\x00\x00\x08" + os.urandom(200))
    open(f"{tree}/DSC_0005.NEF", "wb").write(nef_drawable_thumb())                  # IFD0 is a 160x120 thumbnail Pillow can draw
    Image.effect_noise((900, 700), 60).convert("RGB").save(f"{tree}/scan.tif", "TIFF")
    img = f"{T}/card.img"
    F.fat_image(img, [tree], label="NIKON D100")
    rel = F.put_in_store(store, "unassigned", "card", img)
    root = B.ls(rel, 2048, None)
    dcim = next(e for e in root["entries"] if e["name"] == "DCIM")
    ents = {e["name"]: e for e in B.ls(rel, 2048, dcim["inode"])["entries"]}

    def code_of(fn):
        try:
            fn()
        except B.Err as e:
            return e.code
        return None
    data, ctype = B.thumb(rel, 2048, ents["DSC_0001.NEF"]["inode"], ents["DSC_0001.NEF"]["size"])
    th = Image.open(io.BytesIO(data))
    check("a NEF's thumbnail is a JPEG made from the big picture inside it (640x480 -> 192x144)", ctype == "image/jpeg" and th.size == (192, 144))
    data, _ = B.thumb(rel, 2048, ents["DSC_0002.NEF"]["inode"], ents["DSC_0002.NEF"]["size"])
    check("... one whose raw data Pillow will not open, and whose picture is in the MakerNote, has a thumbnail too",
          Image.open(io.BytesIO(data)).size == (192, 144))
    data, _ = B.thumb(rel, 2048, ents["DSC_0003.NEF"]["inode"], ents["DSC_0003.NEF"]["size"])
    check("... and a camera with only a small uncompressed thumbnail", Image.open(io.BytesIO(data)).size[0] > 0)
    bad = code_of(lambda: B.thumb(rel, 2048, ents["DSC_0004.NEF"]["inode"], ents["DSC_0004.NEF"]["size"]))
    check("a TIFF-shaped file with no picture in it: 415, a sentence, no crash", bad == 415)
    inf = B.info(rel, 2048, ents["DSC_0001.NEF"]["inode"], ents["DSC_0001.NEF"]["size"])
    check("info says photo, and the size of the big picture (640x480), not the 160x120 thumbnail", inf["kind"] == "photo"
          and (inf["width"], inf["height"]) == (640, 480))
    inf5 = B.info(rel, 2048, ents["DSC_0005.NEF"]["inode"], ents["DSC_0005.NEF"]["size"])
    check("a NEF whose first image is a 160x120 thumbnail Pillow can draw: the big picture in its SubIFD is shown instead (640x480)",
          (inf5["width"], inf5["height"]) == (640, 480))
    pdata, pctype = B.preview(rel, 2048, ents["DSC_0001.NEF"]["inode"], width=500)
    check("the viewer's picture of a NEF is the big one, drawn about 500 px across", pctype == "image/jpeg"
          and 400 <= Image.open(io.BytesIO(pdata)).size[0] <= 640)
    sdata, _ = B.thumb(rel, 2048, ents["scan.tif"]["inode"], ents["scan.tif"]["size"])
    sinf = B.info(rel, 2048, ents["scan.tif"]["inode"], ents["scan.tif"]["size"])
    check("an ordinary TIFF (a 900x700 picture) is left as it was: its own size, its own thumbnail (192x149)", (sinf["width"], sinf["height"]) == (900, 700)
          and Image.open(io.BytesIO(sdata)).size == (192, 149))
    # an ordinary TIFF whose first image is a real picture keeps it, even when the file also carries a bigger JPEG (a scan's preview)
    t = Tiff("II")
    pic = os.urandom(700 * 650 * 3)
    big_j = jpeg(1200, 900, seed=4)
    ifd1 = t.ifd([(0x0103, 3, [6]), (0x0201, 4, [t.blob(big_j)]), (0x0202, 4, [len(big_j)])])
    off = t.blob(pic)
    first = t.ifd(sorted([(0x0100, 4, [700]), (0x0101, 4, [650]), (0x0102, 3, [8, 8, 8]), (0x0103, 3, [1]), (0x0106, 3, [2]),
                          (0x0111, 4, [off]), (0x0115, 3, [3]), (0x0116, 4, [650]), (0x0117, 4, [len(pic)])]), nxt=ifd1)
    keep = B._open_photo(write("scan2.tif", t.finish(first)))
    check("an ordinary TIFF (700x650) that also carries a bigger JPEG keeps its own picture: a first image of 640 px or more is not replaced",
          keep.size == (700, 650))
    return report()


def report():
    print(f"raw: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
