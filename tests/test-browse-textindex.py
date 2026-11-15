#!/usr/bin/env python3
"""The drive browser's content search (docs/plans/2026-11-15-drive-browser.md), F3b: the text phase of gatboxlib/browseindex.py,
against an invented ext4 image holding documents of every kind the readers handle, and ones they cannot read. Nothing real is
opened or mounted.

What is checked is what the owner relies on: the words inside documents are found, with a snippet around the match; every
document is accounted for (read, or not and why), so an empty answer can say how much was not searched; documents are read in
inode order, one at a time; the pass stands still for a disk job, goes on after a stop or a restart without reading anything
twice, and lets names go first; a short job starts on its own and a long one waits for a tap; a reader that blows up, a read
that hangs and a document over the limit are codes, not crashes; and nothing is left in the temporary folder.

Needs the sleuthkit tools, mke2fs, poppler's pdftotext and Pillow; without them it skips, saying which.
    python3 tests/test-browse-textindex.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-textindex-test-")
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


def text_pdf(text):
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
    return (out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n").encode("latin-1")


def zipped(path, parts, locked=False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)
    if locked:
        raw = bytearray(open(path, "rb").read())
        for sig, at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = raw.find(sig)
            while i >= 0:
                raw[i + at] |= 0x1
                i = raw.find(sig, i + 4)
        open(path, "wb").write(bytes(raw))


def main():
    gone = F.missing()
    try:
        from PIL import Image
    except ImportError:
        gone.append("python3-pil")
    if not shutil.which("pdftotext"):
        gone.append("poppler-utils")
    if gone:
        print(f"  skip  the content search tests need {', '.join(gone)}")
        return 0
    store = f"{T}/store"
    os.makedirs(store)
    open(f"{store}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mountinfo = f"{T}/mountinfo"
    plugged = f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n"
    open(mountinfo, "w").write(plugged)
    spool = f"{T}/spool"
    os.makedirs(spool)
    os.environ.update(GATBOX_DISKS=store, GATBOX_MOUNTINFO=mountinfo, GATBOX_BROWSE_DIR=f"{store}/_browse", GATBOX_DISK_SPOOL=spool,
                      GATBOX_UDEV=f"{T}/udev", GATBOX_SYSFS=f"{T}/sys", GATBOX_DEV=f"{T}/dev")
    from gatboxlib import browse as B
    from gatboxlib import browseindex as X
    from gatboxlib import browsetext as TX
    B.PREVIEW_CAP = 8 * MiB

    tree = f"{T}/tree"
    d = f"{tree}/Docs"
    os.makedirs(d)
    os.makedirs(f"{tree}/Photos")
    open(f"{d}/notes.txt", "w").write("The cabinet needs a new flyback transformer before the show")
    open(f"{d}/page.html", "w").write("<html><body><p>Rail test at 5 V and 12 V passed</p></body></html>")
    zipped(f"{d}/memo.docx", {"word/document.xml": "<w:p><w:r><w:t>Order the coin door springs</w:t></w:r></w:p>"})
    zipped(f"{d}/budget.xlsx", {"xl/sharedStrings.xml": "<sst><si><t>quarterly budget zebra</t></si></sst>"})
    open(f"{d}/manual.pdf", "wb").write(text_pdf("Widget Wars manual hammerhead"))
    Image.new("RGB", (200, 200), (250, 250, 250)).save(f"{d}/scan.pdf", "PDF")
    zipped(f"{d}/locked.docx", {"word/document.xml": "<w:t>secret</w:t>"}, locked=True)
    open(f"{d}/broken.docx", "wb").write(b"PK\x03\x04 not really a zip")
    open(f"{d}/blob.txt", "wb").write(os.urandom(3000))
    open(f"{d}/readme.rtf", "w").write("{\\rtf1 rich text}")
    open(f"{d}/huge.txt", "wb").write(b"x " * (5 * MiB))                          # over the 8 MiB cap: 10 MiB
    open(f"{d}/long.txt", "w").write("needlefirst " + "filler " * 480_000 + "needlelast")       # 3.4 MiB, cut at 1 MiB of text
    open(f"{tree}/Photos/a.jpg", "wb").write(os.urandom(4000))
    for i in range(30):
        open(f"{d}/filler-{i:02d}.txt", "w").write(f"filler number {i} alpha{i:02d}")
    eligible = 11 + 30                                                           # not the .rtf, not the .jpg
    read_expected = 6 + 30
    img = f"{T}/e.img"
    F.ext4_image(img, tree)
    fat = f"{T}/f.img"
    F.fat_image(fat, [f"{tree}/Docs"], label="WIDGETFAT")
    E = F.put_in_store(store, "unassigned", "ext", img)
    FA = F.put_in_store(store, "widget-wars", "fat", fat)
    sha_e = json.load(open(f"{store}/{E[:-4]}.json"))["sha1"]
    sha_f = json.load(open(f"{store}/{FA[:-4]}.json"))["sha1"]

    def wait(timeout=90):
        end = time.time() + timeout
        while X.IDX.busy() and time.time() < end:
            time.sleep(0.1)
        return not X.IDX.busy()

    def until(pred, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.05)
        return False

    def tstate(sha=sha_e):
        return X.state()["images"].get(sha, {}).get("text", {})

    calls = []
    real_one = X.Indexer._one
    slow = [0.0]

    def one(self, path, start, inode, size, ext):
        calls.append(inode)
        if slow[0]:
            time.sleep(slow[0])
        return real_one(self, path, start, inode, size, ext)
    X.Indexer._one = one

    print("the names first, then the text on its own (the estimate is short):")
    r = X.search("flyback", scope="text")
    check("before anything: nothing found, and the text coverage says 0 of 0", r["total"] == 0 and r["coverage"]["text"]["documents"] == 0)
    check("the text of an image whose names are not indexed cannot be asked for: 409, says to index the names first",
          code_of(lambda: X.IDX.request("text", E)) == 409)
    X.IDX.request("start", E)
    check("it finishes: names, then the documents", wait())
    t = tstate()
    check(f"every document is accounted for: {eligible} with a reader, {read_expected} read, 5 not and each says why; the .rtf and the .jpg are not counted",
          t["state"] == "done" and t["eligible"] == eligible and t["read"] == read_expected and t["skipped"] == 5
          and t["codes"] == {"no text": 1, "encrypted": 1, "damaged": 1, "binary": 1, "too big": 1} and t["unread"] == 0)
    check("it was started on its own, once, in inode order, and read nothing twice", len(calls) == eligible and len(set(calls)) == eligible
          and [int(i.split("-")[0]) for i in calls] == sorted(int(i.split("-")[0]) for i in calls))

    print("searching inside documents:")
    r = X.search("flyback", scope="text")
    check("a word inside a text file: its path, kind and a snippet with the match marked", r["total"] == 1 and r["results"][0]["path"] == "Docs/notes.txt"
          and r["results"][0]["snippet"] == "The cabinet needs a new \x01flyback\x02 transformer before the show" and r["results"][0]["image"] == E)
    check("HTML, Word, Excel and PDF files are found by what is in them", X.search("rail", scope="text")["results"][0]["path"] == "Docs/page.html"
          and X.search("springs", scope="text")["results"][0]["path"] == "Docs/memo.docx"
          and X.search("zebra", scope="text")["results"][0]["path"] == "Docs/budget.xlsx"
          and X.search("hammerhead", scope="text")["results"][0]["path"] == "Docs/manual.pdf")
    check("a prefix, and every word required", X.search("transf", scope="text")["total"] == 1 and X.search("flyback transformer", scope="text")["total"] == 1
          and X.search("flyback springs", scope="text")["total"] == 0)
    check("the text kept is cut at 1 MB: a word near the start is found, one at the end of a 3 MB file is not",
          X.search("needlefirst", scope="text")["total"] == 1 and X.search("needlelast", scope="text")["total"] == 0)
    check("a word that is only in a file's name is not found inside documents, and one that is only inside is not found by name",
          X.search("broken", scope="text")["total"] == 0 and X.search("broken")["total"] >= 1 and X.search("flyback")["total"] == 0)
    check("a file that could not be read is not found by what it says, and the names still find it", X.search("secret", scope="text")["total"] == 0
          and X.search("locked")["total"] == 1)
    r = X.search("alpha", scope="text")
    check("paging works for text as for names: 30 matches", r["total"] == 30 and len(r["results"]) == 30 and r["years"] and r["years"][0][1] == 30)
    cv = X.search("flyback", scope="text")["coverage"]["text"]
    check("the coverage says what was read and what could not be, with the reasons", cv["documents"] == eligible and cv["read"] == read_expected
          and cv["skipped"] == 5 and cv["codes"]["encrypted"] == 1 and cv["images"] == 1)
    hostile = [X.search(q, scope="text") for q in ('flyback" OR *', "NEAR(flyback", "'; DROP TABLE body; --", "a:b c^d")]
    check("a query that is FTS syntax cannot break it", all(isinstance(h, dict) for h in hostile) and X.search("flyback", scope="text")["total"] == 1
          and code_of(lambda: X.search("x", scope="evil")) == 400)
    loc = X.locate(X.search("flyback", scope="text")["results"][0]["id"])
    check("SHOW IN FOLDER works from a text result: its folder is Docs", [d_["name"] for d_ in loc["dirs"]] == ["Docs"])

    print("a long job waits for a tap, a short one does not:")
    X.IDX.request("text_remove", E)
    check("text_remove: the text is gone and nothing is searchable inside, the names are untouched",
          tstate()["state"] == "none" and X.search("flyback", scope="text")["total"] == 0 and X.search("notes")["total"] >= 1)
    X.IDX.request("remove", E)
    X.AUTO_TEXT_S = 0
    X.IDX.request("start", E)
    wait()
    t = tstate()
    check("with the estimate over the limit nothing is read on its own: TEXT NOT READ, with the estimate and how many documents and bytes",
          t["state"] == "none" and t["eligible"] == eligible and t["estimate_s"] >= 1 and t["bytes"] > 3 * MiB and t["read"] == 0)
    n0 = len(calls)
    X.AUTO_TEXT_S = 1800

    print("reading, standing still, stopping and going on:")
    del calls[:]
    slow[0] = 0.06
    X.IDX.request("text", E)
    check("a tap starts it", until(lambda: tstate()["state"] == "reading" and tstate()["read"] + tstate()["skipped"] >= 4))
    t = tstate()
    check("while it reads, the count goes up and an estimate of the time left is the running average, not a guess", t["seconds_left"] is not None and t["seconds_left"] >= 0)
    open(f"{spool}/request.json", "w").write("{}")
    check("a disk job: it stands still, WAITING, with the reason", until(lambda: tstate()["state"] == "waiting") and "disk job" in (X.state()["reason"] or ""))
    n1 = len(calls)
    time.sleep(1.0)
    check("... and reads nothing while it waits", len(calls) - n1 <= 1)
    os.remove(f"{spool}/request.json")
    check("it goes on when the disk job is over", until(lambda: tstate()["state"] == "reading"))
    X.IDX.request("text_stop", E)
    check("stop: what was read stays and is searchable, the rest is not, and the screen says PARTLY (a tap goes on)",
          until(lambda: not X.IDX.busy()) and tstate()["state"] == "partial" and 0 < tstate()["read"] + tstate()["skipped"] < eligible)
    first_pass = list(calls)
    done_before = tstate()["read"] + tstate()["skipped"]
    c = sqlite3.connect(X.db_path())
    nbody = c.execute("SELECT count(*) FROM body").fetchone()[0]
    c.close()
    check("... the rows of the documents already read are in the index", nbody == tstate()["read"])
    del calls[:]
    slow[0] = 0
    X.IDX.request("text", E)
    check("a second tap finishes it, and reads only what was left (nothing twice)", wait() and tstate()["state"] == "done" and not set(calls) & set(first_pass[:done_before])
          and len(calls) == eligible - done_before and tstate()["read"] == read_expected and tstate()["skipped"] == 5)

    print("the browser is restarted in the middle of reading:")
    X.IDX.request("text_remove", E)
    slow[0] = 0.05
    del calls[:]
    X.IDX.request("text", E)
    until(lambda: tstate()["state"] == "reading" and tstate()["read"] + tstate()["skipped"] >= 6)
    X.IDX.request("text_stop", E)
    until(lambda: not X.IDX.busy())
    slow[0] = 0
    done_before = tstate()["read"] + tstate()["skipped"]
    c = sqlite3.connect(X.db_path())
    c.execute("UPDATE volumes SET tstate='reading' WHERE sha1=?", (sha_e,))              # what a crash leaves
    c.commit()
    c.close()
    os.makedirs(f"{store}/_browse/tmp", exist_ok=True)
    open(f"{store}/_browse/tmp/read-1-2.txt", "w").write("half a copy")
    X.IDX.__init__()
    del calls[:]
    X.IDX.kick()
    wait()
    c = sqlite3.connect(X.db_path())
    nbody = c.execute("SELECT count(*) FROM body").fetchone()[0]
    nrows = c.execute("SELECT count(*) FROM body WHERE rowid NOT IN (SELECT id FROM files)").fetchone()[0]
    c.close()
    check("a new start finishes what was left, reads nothing twice, leaves no duplicate or orphan rows", tstate()["state"] == "done" and len(calls) == eligible - done_before
          and nbody == read_expected and nrows == 0)
    check("the temporary folder is empty, a copy left by a crash is removed", os.listdir(f"{store}/_browse/tmp") == [])

    print("names go first:")
    X.IDX.request("text_remove", E)
    slow[0] = 0.08
    X.IDX.request("text", E)
    until(lambda: tstate()["state"] == "reading")
    X.IDX.request("start", FA)
    check("an image whose names are asked for while text is being read has its names indexed first", until(lambda: X.state()["images"].get(sha_f, {}).get("state") == "done", 20)
          and tstate()["state"] in ("reading", "queued"))
    X.IDX.request("text_stop_all")
    until(lambda: not X.IDX.busy())
    slow[0] = 0
    X.IDX.request("text_all")
    check("text_all reads every image that has documents; the FAT one too", wait() and tstate()["state"] == "done" and tstate(sha_f)["state"] == "done"
          and tstate(sha_f)["eligible"] > 0)
    check("a search spans both images", {x["label"] for x in X.search("flyback", scope="text")["results"]} == {"ext", "fat"})

    print("things that go wrong:")
    X.IDX.request("text_remove", E)
    real_extract = TX.extract

    def exploding(path, ext):
        if ext == "html":
            raise ValueError("a reader blew up")
        return real_extract(path, ext)
    TX.extract = exploding
    X.IDX.request("text", E)
    wait()
    t = tstate()
    check("a reader that raises on a hostile file is a damaged file, and the rest are read", t["state"] == "done" and t["codes"].get("damaged") == 2
          and t["read"] == read_expected - 1 and X.search("flyback", scope="text")["total"] >= 1)
    TX.extract = real_extract
    X.IDX.request("text_remove", E)
    real_run = B.run

    def hanging(argv, timeout, cap=None, stdout=None):
        if argv[0] == "icat" and argv[-1] == str(sorted(int(i.split("-")[0]) for i in calls[:1] or [0])[0]):
            pass
        if argv[0] == "icat" and hanging.n == 3:
            hanging.n += 1
            raise B.Err(504, "reading the image took too long, so it was stopped")
        if argv[0] == "icat":
            hanging.n += 1
        return real_run(argv, timeout, cap=cap, stdout=stdout)
    hanging.n = 0
    B.run = hanging
    X.IDX.request("text", E)
    wait()
    B.run = real_run
    check("a read out of the image that hangs is 'too slow' for that file only", tstate()["codes"].get("too slow") == 1 and tstate()["state"] == "done")
    X.IDX.request("text_remove", E)
    slow[0] = 0.05
    X.IDX.request("text", E)
    until(lambda: tstate()["state"] == "reading" and tstate()["read"] + tstate()["skipped"] >= 3)
    open(mountinfo, "w").write("23 22 179:1 / /boot/firmware rw shared:2 - vfat /dev/mmcblk0p1 rw\n")
    check("the store unplugged: it stands still and says so", until(lambda: tstate()["state"] == "waiting") and "not connected" in (X.state()["reason"] or ""))
    open(mountinfo, "w").write(plugged)
    slow[0] = 0
    check("plugged in again: it finishes", wait() and tstate()["state"] == "done")
    X.IDX.request("remove", E)
    check("removing an image removes its text with it: it is gone from the index, and only the other image's documents are found",
          X.state()["images"].get(sha_e) is None and {r["label"] for r in X.search("flyback", scope="text")["results"]} == {"fat"})
    c = sqlite3.connect(X.db_path())
    orphans = c.execute("SELECT count(*) FROM body WHERE rowid NOT IN (SELECT id FROM files)").fetchone()[0] + \
        c.execute("SELECT count(*) FROM tx WHERE file_id NOT IN (SELECT id FROM files)").fetchone()[0]
    c.close()
    X.IDX.request("start", E)
    wait()
    c = sqlite3.connect(X.db_path())
    orphans_after = c.execute("SELECT count(*) FROM body WHERE rowid NOT IN (SELECT id FROM files)").fetchone()[0]
    c.close()
    check("removing an image leaves no text or notes behind, so indexing it again (its row numbers are reused) reads all of it cleanly",
          orphans == 0 and orphans_after == 0 and tstate()["state"] == "done" and tstate()["read"] == read_expected and tstate()["skipped"] == 5)
    shutil.rmtree(f"{store}/_browse")
    check("the index deleted: nothing is found, nothing breaks", X.search("flyback", scope="text")["total"] == 0 and X.state()["images"] == {})
    check("the temporary folder never holds a copy once a pass is over", not os.path.exists(f"{store}/_browse/tmp") or os.listdir(f"{store}/_browse/tmp") == [])

    print("what it is allowed to do:")
    import re
    src = open(os.path.join(REPO, "backend/gatboxlib/browseindex.py")).read()
    code_only = re.sub(r'"""[\s\S]*?"""', "", "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
    banned = [w for w in ("mount", "umount", "losetup", "mkfs", "wipefs", "sfdisk", "dd", "mmls", "fsstat", "sudo") if re.search(rf"\b{w}\b", code_only)]
    check("the index module names no mount, no formatter, no dd; it starts only fls itself and icat through the browse core's runner",
          not banned and src.count("subprocess.Popen(") == 1 and 'B.run(["icat"' in src)
    return 0


try:
    rc = main()
    print(f"\ndrive browser F3b: {passed} passed, {failed} failed")
    rc = 1 if failed else rc
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(rc)
