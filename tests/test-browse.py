#!/usr/bin/env python3
"""The drive browser (docs/plans/2026-11-15-drive-browser.md), F1: gatboxlib/browse.py, the gatbox-browse service and
gatbox-web's door to it, against invented images (tests/fakeimage.py): an ext4 volume and swap, a FAT32 volume with a label,
random bytes, and images with a LUKS and a BitLocker header. Nothing real is opened and nothing is mounted.

What is checked is what the owner relies on: the right volumes and folders come out, paged and sorted; photos, PDF pages and
text are shown; every limit holds (a photo with too many pixels, a file over 64 MB, a damaged photo, a hung tool); a name or
an inode that is not an image in the store is refused; the image is never changed; the service answers over its socket,
starts from systemd's socket and leaves when idle; and gatbox-web answers only the Pi's own screen.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools and Pillow; without them it skips, saying which.
    python3 tests/test-browse.py
"""
import re
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-browse-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(MiB), b""):
            h.update(b)
    return h.hexdigest()


def unix_get(sock, path, timeout=60):
    """GET over the daemon's unix socket: (status, content type, body)."""
    class C(http.client.HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(sock)
            self.sock = s
    c = C("localhost")
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        c.close()


def main():
    gone = F.missing()
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the drive browser's tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
        return 0
    store = f"{T}/store"
    os.makedirs(store)
    open(f"{store}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mountinfo = f"{T}/mountinfo"
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")
    os.makedirs(f"{T}/spool")
    os.environ.update(GATBOX_DISKS=store, GATBOX_MOUNTINFO=mountinfo, GATBOX_BROWSE_DIR=f"{store}/_browse",
                      GATBOX_DISK_SPOOL=f"{T}/spool", GATBOX_UDEV=f"{T}/udev", GATBOX_SYSFS=f"{T}/sys", GATBOX_DEV=f"{T}/dev")
    from gatboxlib import browse as B

    tree = f"{T}/tree"
    os.makedirs(tree)
    F.sample_tree(tree, many=450)
    ext = f"{T}/e.img"
    F.ext4_image(ext, tree)
    fat = f"{T}/f.img"
    F.fat_image(fat, [f"{tree}/Photos", f"{tree}/Docs"], label="WIDGETFAT")
    rnd = f"{T}/r.img"
    with open(rnd, "wb") as f:
        f.write(os.urandom(4 * MiB))
    enc = f"{T}/x.img"
    with open(enc, "wb") as f:
        f.truncate(8 * MiB)
    F.mbr(enc, [(2048, 4000, 0x83, False), (6048, 4000, 0x07, False)])
    with open(enc, "r+b") as f:
        f.seek(2048 * 512)
        f.write(b"LUKS\xba\xbe" + os.urandom(100))
        f.seek(6048 * 512)
        f.write(b"\xeb\x52\x90-FVE-FS-" + os.urandom(100))
    E = F.put_in_store(store, "unassigned", "ext", ext)
    FA = F.put_in_store(store, "widget-wars", "fat", fat)
    R = F.put_in_store(store, "unassigned", "rnd", rnd)
    X = F.put_in_store(store, "unassigned", "enc", enc)
    open(f"{T}/outside.img", "wb").write(b"x")
    json.dump({"label": "OUTSIDE", "sha1": "f" * 40}, open(f"{T}/outside.json", "w"))
    before = {r: sha(f"{store}/{r}") for r in (E, FA, R, X)}

    print("volumes:")
    v = B.volumes(E)
    vv = v["volumes"]
    check("ext4 behind a DOS table: the Linux volume with its type, block size, size, used and free, and the swap partition",
          v["table"] == "DOS Partition Table" and len(vv) == 2 and vv[0]["fs"]["type"] == "Ext4" and vv[0]["fs"]["block_size"] == 4096
          and vv[0]["fs"]["size_bytes"] > 90 * MiB and vv[0]["fs"]["used_bytes"] + vv[0]["fs"]["free_bytes"] == vv[0]["fs"]["size_bytes"])
    check("... the swap partition says what it is, and shows the first bytes of its first sector, not a guess",
          vv[1]["fs"] is None and vv[1]["note"] == "swap space" and vv[1]["encrypted"] is None and vv[1]["peek"].startswith("00 00"))
    f = B.volumes(FA)["volumes"][0]
    check("FAT32: type, size, free space and its volume label", f["fs"]["type"] == "FAT32" and f["fs"]["label"] == "WIDGETFAT"
          and f["fs"]["size_bytes"] == 180000 * 512 and f["fs"]["free_bytes"] > 0)
    rv = B.volumes(R)["volumes"]
    check("random bytes: no partition table, one volume, 'no filesystem recognized'", len(rv) == 1 and rv[0]["slot"] == "whole image"
          and rv[0]["fs"] is None and rv[0]["note"] == "no filesystem recognized")
    xv = B.volumes(X)["volumes"]
    check("a LUKS header and a BitLocker header are named, and said not to open",
          xv[0]["encrypted"] == "LUKS" and "cannot be opened" in xv[0]["note"] and xv[1]["encrypted"] == "BitLocker")
    for bad, code in (("../outside.img", 404), (f"{store}/{E}", 404), ("unassigned/nope.img", 404), ("", 404), (None, 404),
                      (E[:-4] + ".json", 404), ("../../etc/passwd", 404)):
        try:
            B.volumes(bad)
            got = None
        except B.Err as e:
            got = e.code
        if got != code:
            check(f"image {bad!r} is refused", False)
            break
    else:
        check("an image name that is not one in the store (outside it, absolute, missing, empty, a sidecar, a path trick) -> 404", True)
    try:
        B.ls(E, 999)
        got = None
    except B.Err as e:
        got = e.code
    check("a volume start that mmls did not report -> 404 (no offset is invented)", got == 404)
    try:
        B.ls(E, 202048)
        got = None
    except B.Err as e:
        got = e.code
    check("the swap partition has no folders: 415, with its reason", got == 415)

    print("folders:")
    root = B.ls(E, 2048)
    names = {e["name"]: e for e in root["entries"]}
    check("the root: its folders first, then files, a symlink as a link with its target, a pipe in a name intact, nothing deleted or virtual",
          [e["kind"] for e in root["entries"]][:6] == ["folder"] * 6 and "odd|name dir" in names and names["link.txt"]["kind"] == "link"
          and names["link.txt"]["target"] == "Docs/notes.txt" and "$OrphanFiles" not in names
          and names["big.bin"]["size"] == 2 * MiB and names["Photos"]["kind"] == "folder")
    check("each entry carries its own inode, so a client walks down by inode", all(str(e["inode"]).isdigit() for e in root["entries"]))
    notes = B.ls(E, 2048, names["Notes"]["inode"])
    check("a folder of 452 entries: 3 pages (200, 200, 52), the total, and a page past the end clamps to the last",
          notes["total"] == 452 and notes["pages"] == 3 and len(notes["entries"]) == 200 and len(B.ls(E, 2048, names["Notes"]["inode"], page=2)["entries"]) == 52
          and B.ls(E, 2048, names["Notes"]["inode"], page=99)["page"] == 2)
    by_size = B.ls(E, 2048, None, sort="size", order="desc")
    files = [e for e in by_size["entries"] if e["kind"] != "folder"]
    check("sorted by size, largest first: files in order, folders still first",
          [e["size"] for e in files] == sorted((e["size"] for e in files), reverse=True) and by_size["entries"][0]["kind"] == "folder")
    by_date = B.ls(E, 2048, None, sort="date", order="asc")
    check("sorted by date: accepted, in order", [e["mtime"] or 0 for e in by_date["entries"] if e["kind"] != "folder"]
          == sorted(e["mtime"] or 0 for e in by_date["entries"] if e["kind"] != "folder"))
    ph = B.ls(E, 2048, None, kind="photos")
    check("the PHOTOS filter keeps the folders and the photos, nothing else", all(e["kind"] in ("folder", "photo") for e in ph["entries"])
          and "bomb.png" in {e["name"] for e in ph["entries"]} and "big.bin" not in {e["name"] for e in ph["entries"]})
    dc = B.ls(E, 2048, None, kind="documents")
    check("the DOCUMENTS filter keeps the folders and the documents", all(e["kind"] in ("folder", "document") for e in dc["entries"])
          and "huge.pdf" in {e["name"] for e in dc["entries"]})
    for kw in ({"sort": "colour"}, {"order": "up"}, {"kind": "videos"}, {"dir_inode": "12;rm -rf /"}, {"dir_inode": "../x"}, {"dir_inode": "1 2"}):
        try:
            B.ls(E, 2048, **kw)
            got = None
        except B.Err as e:
            got = e.code
        if got != 400:
            check(f"{kw} -> 400", False)
            break
    else:
        check("a bad sort, order, kind, or an inode that is not digits and dashes -> 400 (nothing reaches a command line)", True)
    ntfs_like = B.ls(E, 2048, "5-144-1") if False else None
    fr = B.ls(FA, 2048)
    check("FAT: the folders, and the volume label entry is not listed as a file", [e["name"] for e in fr["entries"]] == ["Docs", "Photos"])
    docs = B.ls(E, 2048, names["Docs"]["inode"])
    dn = {e["name"]: e for e in docs["entries"]}

    print("what is on a volume:")
    check("before it is counted: 'none', and asking without starting does not start it", B.stats(E, 2048, begin=False)["state"] == "none")
    st = B.stats(E, 2048)
    for _ in range(100):
        if st["state"] not in ("computing", "waiting"):
            break
        time.sleep(0.1)
        st = B.stats(E, 2048)
    check("counted in the background: files, folders, links, and photos, documents and other by count and bytes",
          st["state"] == "done" and st["files"] == 462 and st["folders"] == 7 and st["links"] == 1 and st["photos"]["files"] == 4
          and st["documents"]["files"] == 457 and st["other"]["files"] == 1 and st["volume"]["type"] == "Ext4")
    check("... and kept on the store, so the next look is instant, even after the service restarts",
          any(n.endswith(".stats") for n in os.listdir(f"{store}/_browse/stats")) and B.stats(E, 2048, begin=False)["state"] == "done")
    open(f"{T}/spool/running.json", "w").write("{}")
    try:
        B.stats(FA, 2048)
        got = None
    except B.Err as e:
        got = e.code
    os.remove(f"{T}/spool/running.json")
    check("a disk job running: counting waits (409), imaging goes first", got == 409)
    for _ in range(50):
        s2 = B.stats(FA, 2048)
        if s2["state"] == "done":
            break
        time.sleep(0.1)
    check("FAT is counted too", s2["state"] == "done" and s2["photos"]["files"] == 2 and s2["documents"]["files"] == 3)

    print("one file:")
    ph = [e for e in B.ls(E, 2048, B.ls(E, 2048, names["Photos"]["inode"])["entries"][0]["inode"])["entries"]]
    pn = {e["name"]: e for e in ph}
    jpg = pn["IMG_0001.jpg"]
    i = B.info(E, 2048, jpg["inode"], jpg["size"])
    check("a photo: its kind, from its bytes, and its size in pixels", i["kind"] == "photo" and (i["width"], i["height"]) == (1600, 1200))
    data, ctype = B.preview(E, 2048, jpg["inode"], width=800, size=jpg["size"])
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(data))
    check("... drawn as a JPEG no wider than asked, in proportion", ctype == "image/jpeg" and im.size == (800, 600))
    data2, _ = B.preview(E, 2048, jpg["inode"], width=99999, size=jpg["size"])
    check("... and a huge width is held to the limit (the original is never drawn bigger than it is)", Image.open(io.BytesIO(data2)).size[0] <= 1600)
    png = pn["red.png"]
    check("a PNG is shown too", B.preview(E, 2048, png["inode"], width=400)[1] == "image/jpeg")
    pdf = dn["manual.pdf"]
    ip = B.info(E, 2048, pdf["inode"], pdf["size"])
    check("a PDF: its kind and page count", ip["kind"] == "pdf" and ip["pages"] == 3)
    pg, ctype = B.preview(E, 2048, pdf["inode"], page=2, width=500)
    check("... one page drawn as a JPEG at the width asked", ctype == "image/jpeg" and Image.open(io.BytesIO(pg)).size[0] == 500)
    try:
        B.preview(E, 2048, pdf["inode"], page=4)
        got = None
    except B.Err as e:
        got = (e.code, e.msg)
    check("... a page past the end -> 404 saying how many pages it has", got == (404, "this PDF has 3 pages"))
    calls = []
    real_run = B.run

    def counting(argv, *a, **k):
        calls.append(argv[0])
        return real_run(argv, *a, **k)
    B.run = counting
    B.preview(E, 2048, pdf["inode"], page=1, width=500)
    B.preview(E, 2048, pdf["inode"], page=3, width=500)
    B.run = real_run
    check("turning pages does not read the image again: the file is in the cache and its kind is remembered", "icat" not in calls)
    txt = B.text(E, 2048, dn["notes.txt"]["inode"])
    check("a text file: its text, UTF-8, not cut", txt == {"text": "Widget Wars cabinet notes\nline two\n", "cut": False, "encoding": "UTF-8"})
    nf = {e["name"]: e for e in B.ls(E, 2048, names["Notes"]["inode"], per_page=500)["entries"]}
    lt = B.text(E, 2048, nf["latin1.txt"]["inode"])
    lg = B.text(E, 2048, nf["long.txt"]["inode"])
    check("a Latin-1 file is decoded as Latin-1 ('café au lait'); a 70,000-character file is cut at 64 KB and says so",
          lt["encoding"] == "Latin-1" and lt["text"] == "caf\xe9 au lait\n" and lg["cut"] and len(lg["text"]) == 65536)
    check("a file that is not text is refused as text (415)", _code(lambda: B.text(E, 2048, names["big.bin"]["inode"])) == 415)
    ib = B.info(E, 2048, names["big.bin"]["inode"], names["big.bin"]["size"])
    check("a binary file: 'no preview for this kind of file', not an error", ib["kind"] == "other" and "no preview" in ib["message"])
    check("a photo with too many pixels (a 30000x30000 PNG a few KB long) -> 413, not drawn",
          _code(lambda: B.info(E, 2048, names["bomb.png"]["inode"], names["bomb.png"]["size"])) == 413
          and _code(lambda: B.preview(E, 2048, names["bomb.png"]["inode"])) == 413)
    check("a damaged photo -> 415 with a sentence, no crash",
          _code(lambda: B.info(E, 2048, names["broken.jpg"]["inode"], names["broken.jpg"]["size"])) == 415)
    check("a file over 64 MB -> 413 at once when the listing's size says so", _code(lambda: B.info(E, 2048, names["huge.pdf"]["inode"], names["huge.pdf"]["size"])) == 413)
    check("... and when the size is not given, after the limit's worth has been read, never more",
          _code(lambda: B.preview(E, 2048, names["huge.pdf"]["inode"])) == 413 and
          max((os.path.getsize(os.path.join(B._cache.dir, n)) for n in os.listdir(B._cache.dir)), default=0) <= B.PREVIEW_CAP)
    check("HEIC and video are told apart from other files, and said not to be shown yet",
          B.sniff(b"\x00\x00\x00\x18ftypheic" + b"\0" * 20) == ("other", "image/heic") and B.sniff(b"\x00\x00\x00\x18ftypisom" + b"\0" * 20)[1] == "video/mp4")
    check("the kind comes from the bytes: a PDF, a JPEG, a GIF, text, and binary",
          B.sniff(b"%PDF-1.7\n")[0] == "pdf" and B.sniff(b"\xff\xd8\xff\xe0")[0] == "photo" and B.sniff(b"GIF89a")[0] == "photo"
          and B.sniff(b"hello world\n")[0] == "text" and B.sniff(b"\x00\x01\x02\x03" * 100)[0] == "other")
    for bad in ("12;rm", "../x", "", None):
        if _code(lambda: B.info(E, 2048, bad)) not in (400,):
            check(f"inode {bad!r} -> 400", False)
            break
    else:
        check("an inode that is empty or not digits and dashes -> 400", True)
    for n in os.listdir(B._cache.dir):
        os.remove(os.path.join(B._cache.dir, n))             # start from an empty cache
    old_cap = B.CACHE_CAP
    B.CACHE_CAP = 300 * 1024
    B.preview(E, 2048, jpg["inode"], width=300)
    one = [n for n in os.listdir(B._cache.dir) if n.endswith(".bin")]
    B.preview(E, 2048, png["inode"], width=300)
    B.preview(E, 2048, pdf["inode"], width=300)
    left = [n for n in os.listdir(B._cache.dir) if n.endswith(".bin")]
    B.CACHE_CAP = old_cap
    check("the cache of extracted files: the one just read is never dropped (a 1.2 MB photo over a 300 KB cap still draws), the oldest go "
          "when the cap is passed", len(one) == 1 and len(left) == 2
          and sum(os.path.getsize(os.path.join(B._cache.dir, n)) for n in left) <= 300 * 1024)

    print("thumbnails:")
    import io as _io
    from PIL import Image as _Img
    runs = []
    real_run = B.run

    def counting_run(argv, *a, **k):
        runs.append(argv[0])
        return real_run(argv, *a, **k)
    B.run = counting_run
    real_sniff = B._sniff_file
    data, ctype = B.thumb(E, 2048, jpg["inode"], jpg["size"])
    tim = _Img.open(_io.BytesIO(data))
    check("a photo's thumbnail: a JPEG, its longest side 192 px, the proportions kept (1600x1200 -> 192x144)", ctype == "image/jpeg" and tim.size == (192, 144) and tim.format == "JPEG"
          and len(data) < 60_000)
    n_made = len(runs)
    data2, _ = B.thumb(E, 2048, jpg["inode"], jpg["size"])
    check("made once: the second look reads nothing out of the image, and is the same bytes", len(runs) == n_made and data2 == data)
    jp = [os.path.join(d, n) for d, _, ns in os.walk(os.path.join(B.BROWSE_DIR, "thumbs")) for n in ns if n.endswith(".jpg")]
    check("kept on the store in its own folder, named by hash (two characters, then the rest), nothing the client typed", len(jp) == 1
          and re.fullmatch(r"[0-9a-f]{2}", os.path.basename(os.path.dirname(jp[0]))) and re.fullmatch(r"[0-9a-f]{38}\.jpg", os.path.basename(jp[0])))
    red = pn["red.png"]
    td, _ = B.thumb(E, 2048, red["inode"], red["size"])
    check("a PNG too (300x200 -> 192x128)", _Img.open(_io.BytesIO(td)).size == (192, 128))
    exif = f"{T}/rot.jpg"
    im = _Img.new("RGB", (400, 200), (10, 120, 200))
    ex = im.getexif()
    ex[0x0112] = 6                                                   # EXIF: rotate 90 degrees to display; stored 400x200, shown 200x400
    im.save(exif, "JPEG", exif=ex)
    real_extract = B.extract
    B.extract, B._sniff_file = (lambda *a, **k: exif), (lambda *a, **k: ("photo", "image/jpeg"))
    try:
        rd, _ = B.thumb(E, 2048, "99999", None)
    finally:
        B.extract, B._sniff_file = real_extract, real_sniff
    check("the photo's own orientation is applied: stored 400x200 and marked 'rotate', its thumbnail is 96x192 (tall)", _Img.open(_io.BytesIO(rd)).size == (96, 192))
    for name in ("bomb.png", "broken.jpg"):
        ent = names[name]
        code = _code(lambda: B.thumb(E, 2048, ent["inode"], ent["size"]))
        for n in os.listdir(B._cache.dir):                           # the copy read out of the image is gone: only the note can answer
            if n.endswith(".bin"):
                os.remove(os.path.join(B._cache.dir, n))
        n1 = len(runs)
        code2 = _code(lambda: B.thumb(E, 2048, ent["inode"], ent["size"]))
        check(f"{name}: refused in words ({code}), and remembered, so the second ask reads nothing", code in (413, 415) and code2 == code and len(runs) == n1)
    bads = [n for d, _, ns in os.walk(os.path.join(B.BROWSE_DIR, "thumbs")) for n in ns if n.endswith(".bad")]
    check("what could not be drawn is written down on the store (one note each for the two), with its code", len(bads) == 2)
    txt = [e for e in B.ls(E, 2048, names["Docs"]["inode"])["entries"] if e["name"] == "notes.txt"][0]
    check("a file that is not a photo: 415, and no thumbnail", _code(lambda: B.thumb(E, 2048, txt["inode"], txt["size"])) == 415)
    huge = names["huge.pdf"]
    check("a PDF is not a photo: 415", _code(lambda: B.thumb(E, 2048, huge["inode"], huge["size"])) == 415)
    B._sniff_file = lambda *a, **k: ("photo", "image/jpeg")
    n_before = len(runs)
    try:
        code = _code(lambda: B.thumb(E, 2048, "88888", 100 * MiB))
    finally:
        B._sniff_file = real_sniff
    check("a photo whose listed size is over the limit: 413, the file is never read out of the image", code == 413 and len(runs) == n_before)
    check("an inode that is not one, and an image that is not in the store: 400 and 404, nothing made",
          _code(lambda: B.thumb(E, 2048, "12;rm", None)) == 400 and _code(lambda: B.thumb("../outside.img", 2048, jpg["inode"])) == 404)
    spool_dir = f"{T}/spool"
    open(os.path.join(spool_dir, "request.json"), "w").write("{}")
    other = pn["red.png"]
    os.remove(next(p for p in jp))                                    # forget the JPEG's thumbnail, so that the next ask must be made
    n2 = len(runs)
    check("a disk job running: a thumbnail that has to be made is refused (409) and nothing is read; one already made is still given",
          _code(lambda: B.thumb(E, 2048, jpg["inode"], jpg["size"])) == 409 and len(runs) == n2 and B.thumb(E, 2048, other["inode"], other["size"])[0])
    os.remove(os.path.join(spool_dir, "request.json"))
    old_cap = B.THUMB_CAP
    side_e = B.image(E)[1]
    jpg_path, red_path = (B._thumb_paths(side_e, 2048, e["inode"])[1] for e in (jpg, red))
    B.thumb(E, 2048, jpg["inode"], jpg["size"])
    time.sleep(0.05)
    B.thumb(E, 2048, red["inode"], red["size"])                       # looked at last
    both = os.path.isfile(jpg_path) and os.path.isfile(red_path)
    B.THUMB_CAP = os.path.getsize(red_path) + 500                     # room for the newer one only
    B._trim_thumbs()
    B.THUMB_CAP = old_cap
    check("the thumbnails are kept under their cap, the oldest-looked-at going first: the photo's goes, the PNG's (newer, and small) stays",
          both and not os.path.isfile(jpg_path) and os.path.isfile(red_path))
    B.run = real_run
    import stat as _st
    tdir = os.path.join(B.BROWSE_DIR, "thumbs")
    shutil.rmtree(tdir)
    open(tdir, "w").write("a file where the folder should be")
    check("nowhere to keep thumbnails (the folder cannot be made): the thumbnail is still returned, not an error", B.thumb(E, 2048, jpg["inode"], jpg["size"])[0][:3] == b"\xff\xd8\xff")
    os.remove(tdir)

    print("the limits on running things:")
    check("the runner refuses any program that is not sleuthkit's or poppler's (500)", _code(lambda: B.run(["ls", "/"], 5)) == 500)
    slow = f"{T}/slowbin"
    os.makedirs(slow)
    open(f"{slow}/fls", "w").write("#!/bin/sh\nsleep 30\n")
    os.chmod(f"{slow}/fls", 0o755)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = slow + ":" + old_path
    t0 = time.time()
    got = _code(lambda: B.run(["fls", "-r", ext], 1))
    os.environ["PATH"] = old_path
    check("a tool that hangs is killed at its limit (504) and the call returns", got == 504 and time.time() - t0 < 10)
    check("the images are byte-for-byte unchanged after all of that", {r: sha(f"{store}/{r}") for r in before} == before)
    os.remove(f"{T}/outside.img")
    try:
        os.rmdir(f"{store}/_browse/cache")
    except OSError:
        pass

    print("the service (gatbox-browse):")
    daemon = os.path.join(REPO, "backend/gatbox-browse")
    sock = f"{T}/browse.sock"
    denv = dict(os.environ, GATBOX_LIB=os.path.join(REPO, "backend"))
    p = subprocess.Popen([sys.executable, daemon, "--socket", sock, "--idle", "3"], env=denv, stderr=subprocess.PIPE)
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    code, ctype, body = unix_get(sock, "/ping")
    check("it answers /ping over its socket, and the socket is for its group only (mode 660)",
          code == 200 and json.loads(body) == {"ok": True} and oct(os.stat(sock).st_mode & 0o777) == "0o660")
    code, ctype, body = unix_get(sock, f"/volumes?image={E}")
    check("/volumes answers the same as the library", code == 200 and json.loads(body)["volumes"][0]["fs"]["type"] == "Ext4")
    code, ctype, body = unix_get(sock, f"/ls?image={E}&vol=2048&dir={names['Notes']['inode']}&page=1&sort=size&order=desc&kind=documents")
    check("/ls with its paging, sort and filter in the query", code == 200 and json.loads(body)["page"] == 1 and json.loads(body)["pages"] == 3)
    code, ctype, body = unix_get(sock, f"/preview?image={E}&vol=2048&inode={jpg['inode']}&w=600&size={jpg['size']}")
    check("/preview returns the JPEG itself, with its content type", code == 200 and ctype == "image/jpeg" and body[:3] == b"\xff\xd8\xff")
    code, ctype, body = unix_get(sock, f"/preview?image=../outside.img&vol=2048&inode=12")
    check("a bad image comes back as JSON with its code and a sentence", code == 404 and "not an image in the store" in json.loads(body)["error"])
    code, ctype, body = unix_get(sock, "/nothing")
    check("an unknown route -> 404", code == 404)

    class Post(http.client.HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock)
            self.sock = s
    c = Post("localhost")
    c.request("POST", "/volumes", body=b"{}")
    r = c.getresponse()
    check("anything but a GET -> 405, 'read-only'", r.status == 405 and "read-only" in r.read().decode())
    c.close()
    t0 = time.time()
    try:
        p.wait(15)
    except subprocess.TimeoutExpired:
        p.kill()
    check("it leaves when nothing has asked for 3 seconds", p.poll() == 0 and time.time() - t0 < 12)
    # started by systemd: the socket arrives as descriptor 3 and LISTEN_PID names the service
    lsock = f"{T}/activated.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(lsock)
    srv.listen(4)
    wrapper = ("import os,sys; os.dup2(int(sys.argv[1]), 3); os.environ['LISTEN_PID']=str(os.getpid()); os.environ['LISTEN_FDS']='1'; "
               "os.execv(sys.executable, [sys.executable, sys.argv[2], '--idle', '3'])")
    p = subprocess.Popen([sys.executable, "-c", wrapper, str(srv.fileno()), daemon], env=denv, pass_fds=(srv.fileno(),))
    code, ctype, body = unix_get(lsock, "/ping")
    check("started the way systemd starts it (the socket on descriptor 3): it answers on that socket", code == 200)
    p.terminate()
    p.wait(10)
    srv.close()
    open(mountinfo, "w").write("22 1 179:2 / / rw - ext4 /dev/mmcblk0p2 rw\n")
    p = subprocess.Popen([sys.executable, daemon, "--socket", f"{T}/stale.sock", "--idle", "60", "--exit-when-store-missing"], env=denv)
    for _ in range(100):
        if os.path.exists(f"{T}/stale.sock"):
            break
        time.sleep(0.05)
    code, ctype, body = unix_get(f"{T}/stale.sock", f"/volumes?image={E}")
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
    check("with the store not connected it says so (409) and exits, so systemd's next start sees the current mounts",
          code == 409 and "NOT CONNECTED" in json.loads(body)["error"] and p.poll() == 0)
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")

    print("gatbox-web's door:")
    web_side(E, jpg, daemon, denv)
    print(f"\ndrive browser: {passed} passed, {failed} failed")
    return 1 if failed else 0


def _code(fn):
    from gatboxlib import browse as B
    try:
        fn()
    except B.Err as e:
        return e.code
    return None


def _lan_addrs():
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=False).stdout.split()
    except (FileNotFoundError, OSError):
        return []


def web_side(E, jpg, daemon, denv):
    import urllib.error
    import urllib.request
    port = 8097
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is in use")
    w = f"{T}/web"
    for d in ("log", "run", "ctrl", "cache", "data", "spool", "sync"):
        os.makedirs(f"{w}/{d}")
    for f in ("profiles.json", "gatbox-machine-specs.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{w}/data/")
    json.dump({"meta": {}, "video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x"}], "pinball": []},
              open(f"{w}/data/gatbox-barcade-roster.json", "w"))
    sock = f"{T}/web-browse.sock"
    wenv = dict(denv, GATBOX_WEB_PORT=str(port), STATE_DIRECTORY=f"{w}/ctrl", CACHE_DIRECTORY=f"{w}/cache", GATBOX_LOGDIR=f"{w}/log",
                GATBOX_RUNDIR=f"{w}/run", GATBOX_DATA=f"{w}/data", GATBOX_SYNC_STATE=f"{w}/sync", GATBOX_DISK_SPOOL=f"{w}/spool",
                GATBOX_DUMP_SPOOL=f"{w}/nodump", MPLCONFIGDIR=f"{w}/cache/mpl", GATBOX_BROWSE_SOCK=sock)
    d = subprocess.Popen([sys.executable, daemon, "--socket", sock, "--idle", "120"], env=wenv)
    web = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-web")], env=wenv, stdout=open(f"{w}/web.log", "w"),
                           stderr=subprocess.STDOUT)
    B = f"http://127.0.0.1:{port}"

    def get(path, base=B):
        try:
            with urllib.request.urlopen(base + path, timeout=60) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()
    def post(path, obj, base=B):
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()
    try:
        for _ in range(100):
            try:
                get("/kiosk/state")
                break
            except OSError:
                time.sleep(0.1)
        code, ctype, body = get(f"/api/browse/volumes?image={E}")
        check("from the Pi's own screen (127.0.0.1): /api/browse/volumes is answered by the service", code == 200 and json.loads(body)["volumes"][0]["fs"]["type"] == "Ext4")
        code, ctype, body = get(f"/api/browse/preview?image={E}&vol=2048&inode={jpg['inode']}&w=500&size={jpg['size']}")
        check("... and a preview comes back as the JPEG, cacheable", code == 200 and ctype == "image/jpeg" and body[:3] == b"\xff\xd8\xff")
        code, ctype, body = get(f"/api/browse/thumb?image={E}&vol=2048&inode={jpg['inode']}&size={jpg['size']}")
        check("... and a thumbnail is a small JPEG", code == 200 and ctype == "image/jpeg" and body[:3] == b"\xff\xd8\xff" and len(body) < 60_000)
        code, ctype, body = get(f"/api/browse/thumb?image={E}&vol=2048&inode=12;rm")
        check("... a bad inode is refused with its words (400)", code == 400 and "inode" in json.loads(body)["error"])
        code, ctype, body = get(f"/api/browse/ls?image={E}&vol=2048&evil=1&dir=")
        check("... a parameter the service does not read is dropped, an empty one is too", code == 200 and json.loads(body)["total"] > 0)
        code, ctype, body = get(f"/api/browse/ls?image=../outside.img&vol=2048")
        check("... the service's refusals come through with their codes and sentences", code == 404 and "not an image in the store" in json.loads(body)["error"])
        code, ctype, body = get("/api/browse/export")
        check("COPY TO THE STORE: from the Pi's own screen, what the copy is doing: idle, and no copies yet", code == 200 and json.loads(body)["state"] == "idle"
              and json.loads(body)["recent"] == [])
        code, ctype, body = post("/api/browse/export", {"action": "start", "image": E, "vol": 2048, "kind": "evil", "inode": jpg["inode"]})
        check("... a kind that is not one is refused with its words (400)", code == 400 and "kind" in json.loads(body)["error"])
        code, ctype, body = post("/api/browse/export", {"action": "start", "image": "../outside.img", "vol": 2048, "kind": "file", "inode": jpg["inode"]})
        check("... an image that is not in the store is refused (404)", code == 404)
        code, ctype, body = post("/api/browse/export", {"action": "start", "image": E, "vol": 2048, "kind": "file", "inode": jpg["inode"], "name": jpg["name"],
                                                         "size": jpg["size"], "evil": "x"})
        check("... a copy is started through gatbox-web and answered at once (the extra key is dropped)", code == 200 and json.loads(body)["state"] in ("queued", "planning", "copying", "done"))
        end = time.time() + 60
        while time.time() < end and json.loads(get("/api/browse/export")[2])["state"] != "done":
            time.sleep(0.2)
        got = json.loads(get("/api/browse/export")[2])
        copies = [d_ for d_ in os.listdir(f"{T}/store/_exports") if not d_.endswith(".partial")] if os.path.isdir(f"{T}/store/_exports") else []
        check("... it finishes, the copy is on the store under _exports, and it is the photo, byte for byte",
              got["state"] == "done" and len(copies) == 1 and sha(f"{T}/store/_exports/{copies[0]}/{jpg['name']}") == sha(f"{T}/tree/Photos/2019/IMG_0001.jpg")
              and got["recent"][0]["dir"] == copies[0])
        code, ctype, body = get("/api/browse/hash")
        check("COMPARE: from the Pi's own screen, what the comparison is doing: idle, and no files", code == 200 and json.loads(body)["state"] == "idle" and json.loads(body)["files"] == [])
        mem = [{"id": 1, "image": E, "vol": 2048, "inode": jpg["inode"], "size": jpg["size"]}, {"id": 2, "image": E, "vol": 2048, "inode": jpg["inode"], "size": jpg["size"]}]
        code, ctype, body = post("/api/browse/hash", {"action": "start", "name": jpg["name"], "members": [mem[0]]})
        check("... one member is refused with its words (400)", code == 400 and "2 to" in json.loads(body)["error"])
        code, ctype, body = post("/api/browse/hash", {"action": "start", "name": jpg["name"], "members": [dict(mem[0], image="../outside.img"), mem[1]]})
        check("... an image that is not in the store is refused (404)", code == 404)
        code, ctype, body = post("/api/browse/hash", {"action": "start", "name": jpg["name"], "members": mem, "evil": "x"})
        check("... a comparison is started through gatbox-web, with its members passed on (the extra key is dropped), and answered at once",
              code == 200 and json.loads(body)["state"] in ("queued", "hashing", "done") and len(json.loads(body)["files"]) == 2)
        end = time.time() + 60
        while time.time() < end and json.loads(get("/api/browse/hash")[2])["state"] != "done":
            time.sleep(0.2)
        got = json.loads(get("/api/browse/hash")[2])
        check("... it finishes, says the two are the same file, and each SHA-1 is the photo's",
              got["state"] == "done" and got["verdict"] == "same" and [f["sha1"] for f in got["files"]] == [hashlib.sha1(open(f"{T}/tree/Photos/2019/IMG_0001.jpg", "rb").read()).hexdigest()] * 2)
        code, ctype, body = post("/api/browse/hash", {"action": "dismiss"})
        check("... DISMISS puts it back to idle", code == 200 and json.loads(body)["state"] == "idle")
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            check("from a phone: what the comparison is doing is 403, and one cannot be started (403), whatever is asked",
                  get("/api/browse/hash", base=f"http://{lan}:{port}")[0] == 403 and post("/api/browse/hash", {"action": "start", "members": mem}, base=f"http://{lan}:{port}")[0] == 403)
            code, ctype, body = get("/api/browse/export", base=f"http://{lan}:{port}")
            check("from a phone: what the copy is doing is 403 too, and a copy cannot be started (403), whatever is asked",
                  code == 403 and post("/api/browse/export", {"action": "start", "image": E, "vol": 2048, "kind": "file", "inode": jpg["inode"]}, base=f"http://{lan}:{port}")[0] == 403)
            code, ctype, body = get(f"/api/browse/volumes?image={E}", base=f"http://{lan}:{port}")
            check("from a phone (the LAN address): 403, 'FILES is on the Pi's own screen', and nothing was read", code == 403 and "Pi's own screen" in json.loads(body)["error"])
            code, ctype, body = get(f"/api/browse/preview?image={E}&vol=2048&inode={jpg['inode']}", base=f"http://{lan}:{port}")
            check("... for a preview too", code == 403)
            code, ctype, body = get(f"/api/browse/thumb?image={E}&vol=2048&inode={jpg['inode']}", base=f"http://{lan}:{port}")
            check("... and for a thumbnail", code == 403)
        else:
            print("  skip  the phone's 403 (no LAN address here)")
        d.terminate()
        d.wait(10)
        os.remove(sock) if os.path.exists(sock) else None
        code, ctype, body = get(f"/api/browse/volumes?image={E}")
        check("with the service gone: 503, 'isn't installed yet', not a crash", code == 503 and "isn't installed" in json.loads(body)["error"])
        check("server log: no errors", "Traceback" not in open(f"{w}/web.log").read())
    finally:
        for pr in (d, web):
            if pr.poll() is None:
                pr.terminate()
                pr.wait(10)


try:
    rc = main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(rc)
