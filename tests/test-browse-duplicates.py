#!/usr/bin/env python3
"""The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 5), F4c: possible duplicates, from the name index alone, on
two invented ext4 images that share some files.

What is checked: files with the same name (capitals ignored) and exactly the same size are grouped across every fully indexed image,
the groups wasting the most space first; the minimum size, the kind and the paging work; a group says where each copy is, with what
SHOW IN FOLDER needs; a file under the minimum is never offered; a copy under another name is not found, and two different files
with the same name and size ARE offered (these are POSSIBLE duplicates, and the screen says so); nothing is read from an image;
an image that is not indexed yet, or has been removed from the index, takes no part; a disk job holds the comparison back; and a
comparison that has too much to do says so instead of hanging.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools and Pillow; without them it skips, saying which.
    python3 tests/test-browse-duplicates.py
"""
import hashlib
import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-dups-test-")
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
    gone = F.missing()
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the duplicates tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
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

    # --- two drives that share some files ------------------------------------------------------------------------------------
    def write(root, rel, data):
        os.makedirs(os.path.dirname(f"{root}/{rel}"), exist_ok=True)
        open(f"{root}/{rel}", "wb").write(data)

    photo1 = f"{T}/p1.jpg"
    Image.effect_noise((1200, 900), 60).convert("RGB").save(photo1, "JPEG", quality=90)
    photo1_bytes = open(photo1, "rb").read()
    photo2 = f"{T}/p2.jpg"
    Image.effect_noise((600, 400), 60).convert("RGB").save(photo2, "JPEG", quality=90)
    photo2_bytes = open(photo2, "rb").read()
    notes = b"Widget Wars notes\n" * 340                                     # 6120 bytes
    A, Bt = f"{T}/A", f"{T}/B"
    write(A, "Photos/IMG_0001.jpg", photo1_bytes)
    write(A, "Photos/IMG_0002.jpg", photo2_bytes)
    write(A, "Photos/IMG_0002_copy.jpg", photo2_bytes)                         # the same bytes under another name: not found
    write(A, "Docs/Notes.TXT", notes)
    write(A, "Docs/budget.xlsx", os.urandom(5000))
    write(A, "Docs/report.pdf", b"%PDF-1.4\n" + os.urandom(9000))
    write(A, "big.bin", os.urandom(2 * MiB))
    write(A, "desktop.ini", b"[.ShellClassInfo]\n" * 5)                         # tiny: never offered
    write(A, "Docs/solo.txt", os.urandom(7000))                                # only here
    for i in range(45):                                                       # many groups, to page through
        write(A, f"pairs/p{i:02d}.dat", os.urandom(5000))
    write(Bt, "Photos/IMG_0001.jpg", photo1_bytes)
    write(Bt, "Backup/Photos/IMG_0001.jpg", photo1_bytes)                      # the same photo twice here
    write(Bt, "docs/notes.txt", notes)                                          # lower-case: the same name
    write(Bt, "Docs/budget.xlsx", os.urandom(5000))                            # same name and size, different bytes
    write(Bt, "Docs/report.pdf", b"%PDF-1.4\n" + os.urandom(12000))            # a different size: not a group
    write(Bt, "big.bin", os.urandom(2 * MiB))                                  # same name and size, different bytes
    write(Bt, "desktop.ini", b"[.ShellClassInfo]\n" * 5)
    for i in range(45):
        write(Bt, f"pairs/p{i:02d}.dat", os.urandom(5000))
    ea, eb = f"{T}/a.img", f"{T}/b.img"
    F.ext4_whole(ea, A)
    F.ext4_whole(eb, Bt)
    IA = F.put_in_store(store, "unassigned", "driveA", ea)
    IB = F.put_in_store(store, "unassigned", "driveB", eb)
    before = {r: hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() for r in (IA, IB)}
    sha = {r: json.load(open(f"{store}/{r[:-4]}.json"))["sha1"] for r in (IA, IB)}

    def wait(timeout=120):
        end = time.time() + timeout
        while X.IDX.busy() and time.time() < end:
            time.sleep(0.1)
        return not X.IDX.busy()

    j1, big = len(photo1_bytes), 2 * MiB
    print("before anything is indexed:")
    r = X.duplicates()
    check("nothing is compared, and it says how many images were looked at: none of 2", r["groups"] == [] and r["total"] == 0
          and r["coverage"]["images"] == 2 and r["coverage"]["indexed"] == 0)
    check("the index is not created by looking", not os.path.exists(X.db_path()))
    check("a kind that is not one -> 400 (folders are not files)", code_of(lambda: X.duplicates(kind="folders")) == 400 and code_of(lambda: X.duplicates(kind="evil")) == 400)

    print("one image indexed:")
    X.IDX.request("start", IA)
    check("it finishes", wait())
    r = X.duplicates()
    check("one drive on its own has no duplicates (a copy under another name is not one), and the screen is told 1 of 2 was looked at",
          r["groups"] == [] and r["coverage"]["indexed"] == 1)

    print("both:")
    X.IDX.request("start", IB)
    check("it finishes", wait())
    c = X.connect(create=False)
    c.execute("UPDATE volumes SET state='indexing' WHERE sha1=?", (sha[IB],))
    c.commit()
    c.close()
    part = X.duplicates()
    check("a volume whose pass is not whole is not compared: with drive B part-way there are no duplicates, and the screen is told 1 of 2 images was compared",
          part["groups"] == [] and part["coverage"]["indexed"] == 1)
    c = X.connect(create=False)
    c.execute("UPDATE volumes SET state='done' WHERE sha1=?", (sha[IB],))
    c.commit()
    c.close()
    r = X.duplicates(size_min=1)                                                # a minimum below the floor is raised to it
    groups = {g["name"].lower(): g for g in r["groups"]}
    every = [g for pg in range(r["pages"]) for g in X.duplicates(size_min=1, page=pg)["groups"]]
    check("the groups: the photo (three copies), the big file, the notes in two capitalizations, the spreadsheet, and the 45 pairs",
          set(groups) >= {"img_0001.jpg", "big.bin", "notes.txt", "budget.xlsx"} and r["total"] == 49 and len(every) == 49
          and sum(1 for g in every if g["name"].startswith("p")) == 45)
    check("nothing else: not the report (two sizes), not the copy under another name, not the file that is only on one drive, not the tiny file",
          not any(n in groups for n in ("report.pdf", "img_0002.jpg", "img_0002_copy.jpg", "solo.txt", "desktop.ini")))
    waste = [g["waste"] for g in r["groups"]]
    expect = {"img_0001.jpg": 2 * j1, "big.bin": big, "notes.txt": len(notes), "budget.xlsx": 5000}
    check("each group's count, size and wasted space (the extra copies) are right", all(groups[n]["waste"] == w for n, w in expect.items())
          and groups["img_0001.jpg"]["count"] == 3 and groups["img_0001.jpg"]["size"] == j1 and groups["big.bin"]["count"] == 2)
    check("the most space first", waste == sorted(waste, reverse=True) and r["groups"][0]["waste"] == max(2 * j1, big))
    check("the total waste is the sum over every group, not the page", r["waste"] == sum(g["waste"] for pg in range(r["pages"]) for g in X.duplicates(size_min=1, page=pg)["groups"]))
    members = groups["img_0001.jpg"]["members"]
    check("a group says where each copy is: its image, drive label, volume and folder, and what SHOW IN FOLDER needs (id, inode, volume start)",
          sorted((m["label"], m["path"]) for m in members) == [("driveA", "Photos/IMG_0001.jpg"), ("driveB", "Backup/Photos/IMG_0001.jpg"), ("driveB", "Photos/IMG_0001.jpg")]
          and all(m["image"] in (IA, IB) and m["id"] and m["inode"] and m["vol"] == 0 and m["volume"].startswith("Ext") for m in members)
          and groups["img_0001.jpg"]["more"] == 0)
    check("... and a result's id goes to locate() unchanged, which says which folders to open", X.locate(members[0]["id"])["image"] in (IA, IB))
    check("the same name in two capitalizations is one group, with both spellings among its members",
          sorted(m["name"] for m in groups["notes.txt"]["members"]) == ["Notes.TXT", "notes.txt"])
    check("two different files with the same name and size ARE offered: 'possible', not 'the same' (nothing was read to tell them apart)",
          "big.bin" in groups and "budget.xlsx" in groups)

    print("the filters:")
    r1 = X.duplicates(size_min=MiB)
    check("a minimum of 1 MB: the big file and the photo only (if it is over 1 MB), none of the small ones",
          {g["name"].lower() for g in r1["groups"]} == ({"big.bin"} | ({"img_0001.jpg"} if j1 >= MiB else set())) and r1["min"] == MiB)
    check("a minimum above every file: nothing, and no page that does not exist", X.duplicates(size_min=100 * MiB)["groups"] == []
          and X.duplicates(size_min=100 * MiB)["pages"] == 1)
    check("photos only", [g["name"] for g in X.duplicates(kind="photos")["groups"]] == ["IMG_0001.jpg"] and all(m["kind"] == "photo" for m in X.duplicates(kind="photos")["groups"][0]["members"]))
    check("documents only: the notes and the spreadsheet, not the big file or the photo",
          {g["name"].lower() for g in X.duplicates(kind="documents")["groups"]} == {"notes.txt", "budget.xlsx"})
    check("other: the big file and the pairs (.dat), not the photo",
          "big.bin" in {g["name"] for g in X.duplicates(kind="other")["groups"]} and "IMG_0001.jpg" not in {g["name"] for g in X.duplicates(kind="other")["groups"]})

    print("paging:")
    p0, p1, p2 = X.duplicates(page=0), X.duplicates(page=1), X.duplicates(page=2)
    check("49 groups, 20 to a page, three pages, the last with 9", p0["total"] == 49 and p0["pages"] == 3 and len(p0["groups"]) == 20
          and len(p1["groups"]) == 20 and len(p2["groups"]) == 9)
    check("a page past the end is the last, and the pages do not repeat a group",
          X.duplicates(page=99)["page"] == 2 and len({g["name"].lower() + str(g["size"]) for g in p0["groups"] + p1["groups"] + p2["groups"]}) == 49)

    print("the limits:")
    X.DUP_GROUPS = 5
    rc = X.duplicates()
    check("only the biggest groups are looked at when there are too many, and it says so", rc["capped"] is True and rc["total"] == 5)
    X.DUP_GROUPS = 1000
    X.DUP_MEMBERS = 2
    rm = {g["name"].lower(): g for g in X.duplicates()["groups"]}
    check("a group with more copies than it lists says how many more", len(rm["img_0001.jpg"]["members"]) == 2 and rm["img_0001.jpg"]["more"] == 1 and rm["img_0001.jpg"]["count"] == 3)
    X.DUP_MEMBERS = 12
    open(f"{spool}/request.json", "w").write("{}")
    check("while a disk job is queued or running the comparison waits: 409", code_of(lambda: X.duplicates()) == 409)
    os.remove(f"{spool}/request.json")
    check("... and works again once it is over", X.duplicates()["total"] == 49)
    X.DUP_SECONDS, X.DUP_TICK = -1, 1
    check("a comparison that runs out of time is stopped, in words (504), not left hanging", code_of(lambda: X.duplicates()) == 504)
    X.DUP_SECONDS, X.DUP_TICK = 30, 20000
    check("... and the next one is not stuck behind it", X.duplicates()["total"] == 49)

    print("an image leaves the index:")
    X.IDX.request("remove", IB)
    check("it is gone", wait())
    check("with drive B removed from the index, its copies no longer count: no duplicates", X.duplicates()["groups"] == [] and X.duplicates()["coverage"]["indexed"] == 1)
    print("nothing was changed:")
    after = {r: hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() for r in (IA, IB)}
    check("no image changed", after == before)
    print(f"\npossible duplicates: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
