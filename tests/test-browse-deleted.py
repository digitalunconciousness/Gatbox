#!/usr/bin/env python3
"""The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 2), F4b: deleted files, on images where files really were
deleted (mdel and mdeltree on a FAT32 volume, debugfs on ext4), so the markers, the bytes that come back and the cases where
nothing does are sleuthkit's own answers and not guesses.

What is checked: a listing hides deleted entries unless asked, and says how many it hid; shown, they are marked, named without
sleuthkit's marker and sorted in; a deleted folder can be listed and what is in it is deleted too; the content of a deleted FAT
file comes back byte for byte (text, a photo's thumbnail) and, once a new file has taken its space, comes back as something else
(which is why the screen warns); an ext4 file whose inode the drive cleared lists as empty and says nothing could be read;
an entry whose inode now holds another file ("deleted-realloc") is marked and never read; deleted entries are never indexed.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools, debugfs and Pillow; without them it skips, saying which.
    python3 tests/test-browse-deleted.py
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-deleted-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def code_of(fn):
    from gatboxlib import browse as B
    try:
        fn()
    except B.Err as e:
        return e.code
    return None


def main():
    gone = F.missing() + [t for t in F.DELETE_TOOLS if not any(os.path.exists(f"{d}/{t}") for d in os.environ["PATH"].split(":") + ["/usr/sbin", "/sbin"])]
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the deleted-files tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
        return 0
    store = f"{T}/store"
    os.makedirs(store)
    open(f"{store}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mountinfo = f"{T}/mountinfo"
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")
    spool = f"{T}/spool"
    os.makedirs(spool)
    os.environ.update(GATBOX_DISKS=store, GATBOX_MOUNTINFO=mountinfo, GATBOX_BROWSE_DIR=f"{store}/_browse", GATBOX_DISK_SPOOL=spool,
                      GATBOX_UDEV=f"{T}/udev", GATBOX_SYSFS=f"{T}/sys", GATBOX_DEV=f"{T}/dev")
    from PIL import Image
    from gatboxlib import browse as B
    from gatboxlib import browseindex as X

    # --- the invented content ------------------------------------------------------------------------------------------------
    tree = f"{T}/tree"
    for d in ("Docs", "Photos", "Old"):
        os.makedirs(f"{tree}/{d}")
    secret_bytes = b"".join(b"Widget Wars secret line %d\n" % i for i in range(500))
    open(f"{tree}/Docs/keep.txt", "w").write("kept\n")
    open(f"{tree}/Docs/secret notes.txt", "wb").write(secret_bytes)
    Image.effect_noise((800, 600), 60).convert("RGB").save(f"{tree}/Photos/holiday photo 2019.jpg", "JPEG", quality=85)
    Image.new("RGB", (300, 200), (30, 30, 200)).save(f"{tree}/Photos/kept.png")
    open(f"{tree}/Old/a.txt", "w").write("old a\n")
    open(f"{tree}/Old/b.txt", "w").write("old b\n")
    holiday_bytes = open(f"{tree}/Photos/holiday photo 2019.jpg", "rb").read()

    def make_fat(name):
        img = f"{T}/{name}.img"
        F.fat_image(img, [f"{tree}/Docs", f"{tree}/Photos", f"{tree}/Old"], label="WIDGETFAT")
        F.fat_delete(img, 2048, files=["Docs/secret notes.txt", "Photos/holiday photo 2019.jpg"], trees=["Old"])
        return img

    fat = make_fat("f")
    fat2 = make_fat("f2")                                              # the same, then something is written over the deleted photo
    ls_d = subprocess.run(["fls", "-o", "2048", "-r", "-p", "-d", fat2], capture_output=True, text=True, check=True).stdout
    hol_ino = next(l.split()[2].rstrip(":") for l in ls_d.splitlines() if "holiday" in l)
    scribble = F.fat_overwrite_deleted(fat2, 2048, hol_ino, 4096)
    ext_tree = f"{T}/etree"
    os.makedirs(f"{ext_tree}/Docs")
    open(f"{ext_tree}/Docs/keep.txt", "w").write("kept\n")
    open(f"{ext_tree}/Docs/gone-readable.txt", "wb").write(secret_bytes)
    open(f"{ext_tree}/Docs/gone-cleared.txt", "wb").write(secret_bytes)
    Image.effect_noise((400, 300), 60).convert("RGB").save(f"{ext_tree}/Docs/gone-cleared.jpg", "JPEG", quality=85)
    ext = f"{T}/e.img"
    F.ext4_whole(ext, ext_tree)
    F.ext4_delete(ext, "/Docs/gone-readable.txt")
    ino_cleared = F.ext4_delete(ext, "/Docs/gone-cleared.txt", clear=True)
    F.ext4_delete(ext, "/Docs/gone-cleared.jpg", clear=True)
    FA = F.put_in_store(store, "widget-wars", "fat", fat)
    FB2 = F.put_in_store(store, "widget-wars", "fatover", fat2)
    E = F.put_in_store(store, "unassigned", "ext", ext)
    sha_f = json.load(open(f"{store}/{FA[:-4]}.json"))["sha1"]
    before = {r: hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() for r in (FA, FB2, E)}

    def names(r):
        return [e["name"] for e in r["entries"]]

    def entry(r, name):
        return next(e for e in r["entries"] if e["name"] == name)

    print("a folder's listing:")
    root = B.ls(FA, 2048, None)
    check("by default deleted entries are hidden, and the count says how many (the folder Old)", names(root) == ["Docs", "Photos"]
          and root["deleted_hidden"] == 1 and root["deleted_total"] == 1 and root["deleted_shown"] is False)
    root_d = B.ls(FA, 2048, None, deleted=True)
    check("asked for, the deleted folder is listed, in order with the rest, marked deleted", names(root_d) == ["Docs", "Old", "Photos"]
          and entry(root_d, "Old").get("deleted") is True and entry(root_d, "Old")["kind"] == "folder"
          and "deleted" not in entry(root_d, "Docs") and root_d["total"] == 3)
    check("... and the count of hidden ones is 0 (nothing is hidden), with the total still said", root_d["deleted_hidden"] == 0
          and root_d["deleted_total"] == 1 and root_d["deleted_shown"] is True)
    docs_ino = entry(root, "Docs")["inode"]
    docs = B.ls(FA, 2048, docs_ino)
    docs_d = B.ls(FA, 2048, docs_ino, deleted=True)
    check("a folder's own deleted file: hidden, counted", names(docs) == ["keep.txt"] and docs["deleted_hidden"] == 1)
    check("... shown, it is named without sleuthkit's marker and is flagged", names(docs_d) == ["keep.txt", "secret notes.txt"]
          and entry(docs_d, "secret notes.txt")["deleted"] is True and not any("(deleted" in n for n in names(docs_d)))
    sec = entry(docs_d, "secret notes.txt")
    check("... with its size and kind as the drive recorded them", sec["size"] == len(secret_bytes) and sec["kind"] == "document")
    photos_ino = entry(root, "Photos")["inode"]
    ph = B.ls(FA, 2048, photos_ino, kind="photos", deleted=True)
    check("the PHOTOS filter keeps a deleted photo, flagged", names(ph) == ["holiday photo 2019.jpg", "kept.png"]
          and entry(ph, "holiday photo 2019.jpg")["deleted"] is True)
    check("... and the filter without the toggle does not", names(B.ls(FA, 2048, photos_ino, kind="photos")) == ["kept.png"])
    by_date = B.ls(FA, 2048, docs_ino, sort="size", order="desc", deleted=True)
    check("sorting applies to the whole shown set: by size, the larger deleted file first", names(by_date) == ["secret notes.txt", "keep.txt"])
    # a second look at a folder must not turn into the other kind: the cache holds both lists and the flag picks
    check("the same folder asked both ways, one after the other: each answer is its own", names(B.ls(FA, 2048, docs_ino)) == ["keep.txt"]
          and len(B.ls(FA, 2048, docs_ino, deleted=True)["entries"]) == 2 and names(B.ls(FA, 2048, docs_ino)) == ["keep.txt"])

    print("a deleted folder:")
    old = entry(root_d, "Old")
    inside = B.ls(FA, 2048, old["inode"], deleted=True)
    check("it can be listed, and what is in it is deleted too", len(inside["entries"]) == 2 and all(e.get("deleted") for e in inside["entries"]))
    check("FAT keeps no first letter for a deleted short name: both show '_.txt', flagged, so the screen can say so",
          names(inside) == ["_.txt", "_.txt"] and all(e.get("name_cut") for e in inside["entries"])
          and not any(e.get("name_cut") for e in docs_d["entries"] + ph["entries"]))
    check("... and a different file system says nothing of the kind: ext4 names are whole",
          not any(e.get("name_cut") for e in B.ls(E, 0, B.ls(E, 0, None)["entries"][0]["inode"], deleted=True)["entries"]))
    check("without the toggle the same folder shows nothing, and says two are hidden", B.ls(FA, 2048, old["inode"])["entries"] == []
          and B.ls(FA, 2048, old["inode"])["deleted_hidden"] == 2)

    print("what comes back out of a deleted file:")
    t = B.text(FA, 2048, sec["inode"])
    check("a deleted FAT text file reads back whole: the text is the original's", t["text"].encode() == secret_bytes and t["cut"] is False)
    hol = entry(ph, "holiday photo 2019.jpg")
    data, ctype = B.thumb(FA, 2048, hol["inode"], hol["size"])
    tim = Image.open(io.BytesIO(data))
    check("a deleted photo's thumbnail is drawn: a JPEG of 192 by 144, as for a live photo", ctype == "image/jpeg" and tim.size == (192, 144))
    f = B.extract(FA, 2048, hol["inode"], hol["size"])
    check("the bytes that come back are the original's, exactly (nothing has been written over them)", hashlib.sha1(open(f, "rb").read()).hexdigest()
          == hashlib.sha1(holiday_bytes).hexdigest())
    r2 = B.ls(FB2, 2048, None)
    ph2 = B.ls(FB2, 2048, entry(r2, "Photos")["inode"], deleted=True)
    h2 = entry(ph2, "holiday photo 2019.jpg")
    f2 = open(B.extract(FB2, 2048, h2["inode"], h2["size"]), "rb").read()
    check("once something has been written over its space, the deleted entry is still listed, as deleted, at its old size", h2["deleted"] is True
          and h2["size"] == len(holiday_bytes) and len(f2) == len(holiday_bytes))
    check("... and what comes back is the new bytes, then the rest of the original: NOT the original, and nothing says so (the screen's warning does)",
          f2[:4096] == scribble and f2[4096:] == holiday_bytes[4096:] and f2 != holiday_bytes)

    print("ext4:")
    droot = B.ls(E, 0, None)
    d_ino = entry(droot, "Docs")["inode"]
    dd = B.ls(E, 0, d_ino, deleted=True)
    check("a deleted ext4 file is listed as deleted, three of them; the live one is not", sorted(names(dd)) == ["gone-cleared.jpg", "gone-cleared.txt", "gone-readable.txt", "keep.txt"]
          and sum(1 for e in dd["entries"] if e.get("deleted")) == 3 and B.ls(E, 0, d_ino)["deleted_hidden"] == 3)
    rd = entry(dd, "gone-readable.txt")
    check("with its extents kept it reads back", B.text(E, 0, rd["inode"])["text"].startswith("Widget Wars secret line 0"))
    cl = entry(dd, "gone-cleared.txt")
    check("with its inode cleared (what ext4 does) it is listed with nothing in it: size 0", cl["size"] == 0 and cl["deleted"] is True and str(ino_cleared) == cl["inode"])
    try:
        info = B.info(E, 0, cl["inode"], cl["size"])
    except B.Err as e:
        info = {"kind": "refused", "message": e.msg}
    check("... and asking for it gives no kind of preview, not a crash and not an invented one", info["kind"] in ("other", "refused"))
    cj = entry(dd, "gone-cleared.jpg")
    check("a cleared deleted photo has no thumbnail: refused in words (415), and remembered", code_of(lambda: B.thumb(E, 0, cj["inode"], cj["size"])) == 415
          and code_of(lambda: B.thumb(E, 0, cj["inode"], cj["size"])) == 415)

    print("an inode that another file now holds (deleted-realloc):")
    canned = (b"0|/live.txt|12|r/rrw-r--r--|0|0|5|1794786586|1794786586|1794786586|1794786586\n"
              b"0|/old name.txt (deleted-realloc)|13|r/rrw-r--r--|0|0|5|1794786586|1794786586|1794786586|1794786586\n"
              b"0|/gone.txt (deleted)|14|r/rrw-r--r--|0|0|5|1794786586|1794786586|1794786586|1794786586\n")
    real_run = B.run

    def fake_run(argv, timeout, cap=None, stdout=None):
        return (0, canned, b"") if argv[0] == "fls" else real_run(argv, timeout, cap=cap, stdout=stdout)
    B.run = fake_run
    try:
        shown = B.ls(FA, 2048, "7777", deleted=True)
        hidden = B.ls(FA, 2048, "7778")
    finally:
        B.run = real_run
    re_ = entry(shown, "old name.txt")
    check("the entry is listed, deleted, and marked reused (so the screen offers to read nothing)", re_.get("deleted") is True and re_.get("reused") is True
          and names(shown) == ["gone.txt", "live.txt", "old name.txt"] and "reused" not in entry(shown, "gone.txt"))
    check("... hidden by default with the other deleted entry, both counted", names(hidden) == ["live.txt"] and hidden["deleted_hidden"] == 2)
    check("the marker helper: '(deleted)' and '(deleted-realloc)' are cut off the name, a name that only looks like one is not",
          B.gone_name("a b.txt (deleted)") == ("a b.txt", False) and B.gone_name("a.txt (deleted-realloc)") == ("a.txt", True)
          and B.gone_name("a.txt") is None and B.gone_name("report (deleted) final.txt") is None and B.gone_name("(deleted)") is None)

    print("the index and the counts:")
    X.IDX.request("start", FA)
    end = time.time() + 90
    while X.IDX.busy() and time.time() < end:
        time.sleep(0.1)
    st = X.state()["images"][sha_f]
    check("a FAT image with deleted entries is indexed, and it holds live names only: 2 folders, 2 files",
          st["state"] == "done" and st["files"] == 2 and st["entries"] == 4)
    check("a deleted file's name is not found (a search that leads to half a file is not what was asked)", X.search("secret")["total"] == 0
          and X.search("holiday")["total"] == 0 and X.search("a.txt")["total"] == 0)
    check("a live file beside it is", X.search("keep")["total"] == 1 and X.search("kept")["total"] == 1)
    s = B.stats(FA, 2048)
    end = time.time() + 60
    while s["state"] in ("computing", "waiting") and time.time() < end:
        time.sleep(0.1)
        s = B.stats(FA, 2048, begin=False)
    check("the counts keep deleted entries apart: they are counted as deleted and not as files (and FAT's volume label is not a file)",
          s["state"] == "done" and s["deleted"] == 5 and s["files"] == 2 and s["folders"] == 2)

    print("nothing was changed:")
    after = {r: hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() for r in (FA, FB2, E)}
    check("no image changed by listing, reading or indexing its deleted files", after == before)
    print(f"\ndeleted files: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
