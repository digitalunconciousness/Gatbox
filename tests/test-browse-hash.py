#!/usr/bin/env python3
"""The drive browser, COMPARE (a follow-up to F4c's possible duplicates): the files of one duplicate group are read out of their images,
in full, and hashed, so the screen can say which really are the same file. On two invented ext4 images that share some files.

What is checked: a group of copies that are the same bytes comes back "same", with the SHA-1 each file really has; two files that share a
name and a size but not their bytes come back "different", each with its own SHA-1; a member the image will not give up is "failed" and
the others are still compared ("partly"); nothing is written anywhere (the store, the images and the browser's own folder are as they
were) and no image is changed; a group over the byte limit is refused before anything is read, and a file bigger than its listed size is
held to the same limit while it is read; a disk job, or a copy to the store, running holds it back; it stands still while a disk job runs
and carries on after; a read that stalls is stopped; a stop ends it and says so; one at a time; and a request that is not shaped right is
refused with a sentence.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools and Pillow; without them it skips, saying which.
    python3 tests/test-browse-hash.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-hash-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def sha(data):
    return hashlib.sha1(data).hexdigest()


def code_of(fn):
    from gatboxlib import browse as B
    try:
        fn()
    except B.Err as e:
        return e.code
    return None


def tree(root):
    """Every path under root with its size and mtime, to see that nothing was written."""
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs + [""]:
            p = os.path.join(d, f) if f else d
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def main():
    gone = F.missing()
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the compare tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
        return 0
    store = f"{T}/store"
    os.makedirs(store)
    open(f"{store}/.gatbox-image-store", "w").write("GATBOX image store\n")
    mountinfo = f"{T}/mountinfo"
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")
    spool = f"{T}/spool"
    os.makedirs(spool)
    os.environ.update(GATBOX_DISKS=store, GATBOX_MOUNTINFO=mountinfo, GATBOX_BROWSE_DIR=f"{store}/_browse", GATBOX_DISK_SPOOL=spool,
                      GATBOX_EXPORTS_DIR=f"{store}/_exports", GATBOX_UDEV=f"{T}/udev", GATBOX_SYSFS=f"{T}/sys", GATBOX_DEV=f"{T}/dev")
    from gatboxlib import browse as B
    from gatboxlib import browsecopy as C
    from gatboxlib import browsehash as H
    from gatboxlib import browseindex as X

    def write(root, rel, data):
        os.makedirs(os.path.dirname(f"{root}/{rel}"), exist_ok=True)
        open(f"{root}/{rel}", "wb").write(data)

    photo = os.urandom(300_000)
    notes = b"Widget Wars notes\n" * 340
    big_a, big_b = os.urandom(2 * MiB), os.urandom(2 * MiB)
    budget_a, budget_b = os.urandom(5000), os.urandom(5000)
    A, Bt = f"{T}/A", f"{T}/B"
    write(A, "Photos/IMG_0001.jpg", photo)
    write(A, "Docs/Notes.TXT", notes)
    write(A, "Docs/budget.xlsx", budget_a)
    write(A, "big.bin", big_a)
    write(Bt, "Photos/IMG_0001.jpg", photo)
    write(Bt, "Backup/Photos/IMG_0001.jpg", photo)
    write(Bt, "docs/notes.txt", notes)
    write(Bt, "Docs/budget.xlsx", budget_b)                                    # same name and size, different bytes
    write(Bt, "big.bin", big_b)
    ea, eb = f"{T}/a.img", f"{T}/b.img"
    F.ext4_whole(ea, A)
    F.ext4_whole(eb, Bt)
    IA = F.put_in_store(store, "unassigned", "driveA", ea)
    IB = F.put_in_store(store, "unassigned", "driveB", eb)
    images = {r: hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() for r in (IA, IB)}

    for r in (IA, IB):
        X.IDX.request("start", r)
        end = time.time() + 120
        while X.IDX.busy() and time.time() < end:
            time.sleep(0.1)
    groups = {g["name"].lower(): g for g in X.duplicates(size_min=4096)["groups"]}

    def members(g):
        return [{"id": m["id"], "image": m["image"], "vol": m["vol"], "inode": m["inode"], "size": m["size"]} for m in g["members"]]

    def go(name, mem, **kw):
        body = {"action": "start", "name": name, "members": mem}
        body.update(kw)
        H.HASH.request(body)
        wait()
        return H.HASH.status()

    def wait(timeout=60):
        end = time.time() + timeout
        while H.HASH.busy() and time.time() < end:
            time.sleep(0.05)
        return not H.HASH.busy()

    check("the index gave the groups to compare (the photo three times, the notes, the budget and the big file twice)",
          {"img_0001.jpg", "notes.txt", "budget.xlsx", "big.bin"} <= set(groups) and len(groups["img_0001.jpg"]["members"]) == 3)
    st = H.HASH.status()
    check("nothing is being compared before the first tap: idle, and no files", st["state"] == "idle" and st["files"] == [])

    print("a group of the same bytes:")
    snap = tree(store)
    st = go("IMG_0001.jpg", members(groups["img_0001.jpg"]))
    check("the three copies of the photo come back 'same', each with the SHA-1 the file really has and the number of bytes it really is",
          st["state"] == "done" and st["verdict"] == "same" and len(st["files"]) == 3
          and all(f["state"] == "done" and f["sha1"] == sha(photo) and f["bytes"] == len(photo) and not f["note"] for f in st["files"]))
    check("... each file keeps the id the screen sent, so it can say which place is which",
          [f["id"] for f in st["files"]] == [m["id"] for m in groups["img_0001.jpg"]["members"]])
    check("... nothing was written: the store, the browser's own folder and the images are exactly as they were, and there is no _exports folder",
          tree(store) == snap and not os.path.exists(f"{store}/_exports")
          and all(hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() == images[r] for r in (IA, IB)))
    check("... the byte count is the whole of what was read", st["bytes_done"] == 3 * len(photo) == st["bytes_total"])
    st = go("Notes.TXT", members(groups["notes.txt"]))
    check("the notes (one file's name in capitals, one's in lower case) are 'same' too", st["verdict"] == "same" and {f["sha1"] for f in st["files"]} == {sha(notes)})

    print("a group that only looks the same:")
    st = go("budget.xlsx", members(groups["budget.xlsx"]))
    check("same name, same size, different bytes: 'different', and each file has its own SHA-1",
          st["verdict"] == "different" and {f["sha1"] for f in st["files"]} == {sha(budget_a), sha(budget_b)} and len({f["sha1"] for f in st["files"]}) == 2)
    st = go("big.bin", members(groups["big.bin"]))
    check("a 2 MB file is read whole, and a different one is told apart", st["verdict"] == "different" and {f["sha1"] for f in st["files"]} == {sha(big_a), sha(big_b)}
          and all(f["bytes"] == 2 * MiB for f in st["files"]))

    print("a member the image will not give up:")
    stub = f"{T}/bin"
    os.makedirs(stub)
    real_icat = shutil.which("icat")
    open(f"{stub}/icat", "w").write(
        "#!/bin/sh\nhere=$(dirname \"$0\")\n"
        "for a; do last=$a; done\n"
        "if [ -e \"$here/hang\" ]; then sleep 30; exit 0; fi\n"
        "if [ -e \"$here/failino\" ] && [ \"$last\" = \"$(cat \"$here/failino\")\" ]; then echo 'icat: cannot read' >&2; exit 1; fi\n"
        f"exec {real_icat} \"$@\"\n")
    os.chmod(f"{stub}/icat", 0o755)
    real_path = os.environ["PATH"]
    os.environ["PATH"] = f"{stub}:{real_path}"
    ph = members(groups["img_0001.jpg"])
    open(f"{stub}/failino", "w").write(ph[1]["inode"])
    st = go("IMG_0001.jpg", ph)
    check("one of three fails: it says so on that file, the other two are still compared, and the verdict is 'partly' (the two that were read agree)",
          st["state"] == "done" and st["verdict"] == "partly" and [f["state"] for f in st["files"]] == ["done", "failed", "done"]
          and st["files"][1]["sha1"] is None and "could not be read" in st["files"][1]["note"] and st["files"][0]["sha1"] == st["files"][2]["sha1"] == sha(photo))
    open(f"{stub}/failino", "w").write(ph[0]["inode"])
    st = go("IMG_0001.jpg", ph[:2])
    check("one of two fails: there is nothing to compare against, so the verdict is 'partly' and the one that was read has its SHA-1",
          st["verdict"] == "partly" and st["files"][0]["state"] == "failed" and st["files"][1]["sha1"] == sha(photo))
    both = [dict(ph[0]), dict(ph[0], id=ph[0]["id"] + 100000)]
    st = go("IMG_0001.jpg", both)
    check("every file fails: the verdict is none, and the screen can say that nothing was read", st["state"] == "done" and st["verdict"] is None
          and all(f["state"] == "failed" for f in st["files"]))
    os.remove(f"{stub}/failino")

    print("a read that stalls, and a stop:")
    open(f"{stub}/hang", "w").close()
    H.C.STALL_S = 1
    st = go("big.bin", members(groups["big.bin"]))
    check("a read that gives nothing for too long is stopped, and says so (an error), and nothing is written", st["state"] == "error" and "stalled" in st["error"]
          and tree(store) == snap)
    H.C.STALL_S = 180
    os.remove(f"{stub}/hang")
    os.environ["PATH"] = real_path
    H.HASH.request({"action": "dismiss"})
    check("dismiss puts it back to idle", H.HASH.status()["state"] == "idle")

    real_wait = H.Hasher._wait_turn

    def slow_wait(self):
        time.sleep(0.05)
        return real_wait(self)
    H.Hasher._wait_turn = slow_wait
    C.CHUNK = 4096
    try:
        H.HASH.request({"action": "start", "name": "big.bin", "members": members(groups["big.bin"])})
        time.sleep(0.5)
        check("one at a time: a second start while one is running -> 409", code_of(lambda: H.HASH.request({"action": "start", "name": "x", "members": members(groups["notes.txt"])})) == 409)
        H.HASH.request({"action": "stop"})
        wait(30)
    finally:
        H.Hasher._wait_turn = real_wait
        C.CHUNK = MiB
    st = H.HASH.status()
    check("stop: it stops, says so, and what was read so far is not passed off as an answer",
          st["state"] == "stopped" and "stopped" in st["error"] and st["verdict"] in (None, "partly"))
    H.HASH.request({"action": "dismiss"})

    print("a disk job, and a copy:")
    open(f"{spool}/request.json", "w").write("{}")
    check("a disk job queued: 409, and nothing starts", code_of(lambda: H.HASH.request({"action": "start", "name": "n", "members": members(groups["notes.txt"])})) == 409
          and H.HASH.status()["state"] == "idle")
    os.remove(f"{spool}/request.json")
    keep = C.EXP.thread
    C.EXP.thread = threading.Thread(target=time.sleep, args=(2,), daemon=True)
    C.EXP.thread.start()
    check("a copy to the store running: 409, and nothing starts", code_of(lambda: H.HASH.request({"action": "start", "name": "n", "members": members(groups["notes.txt"])})) == 409)
    C.EXP.thread = keep

    n, seen, polling = [0], [], threading.Event()

    def poll():
        while not polling.is_set():
            seen.append(H.HASH.state.get("state"))
            time.sleep(0.05)

    def wait_with_disk_job(self):
        n[0] += 1
        if n[0] == 3:
            open(f"{spool}/running.json", "w").write("{}")
            threading.Timer(2.0, lambda: os.remove(f"{spool}/running.json")).start()
        return real_wait(self)
    H.Hasher._wait_turn = wait_with_disk_job
    C.CHUNK = 64 * 1024
    threading.Thread(target=poll, daemon=True).start()
    try:
        t0 = time.time()
        st = go("big.bin", members(groups["big.bin"]))
    finally:
        polling.set()
        H.Hasher._wait_turn = real_wait
        C.CHUNK = MiB
    check("a disk job that starts in the middle makes it stand still (the screen sees 'waiting'), and it carries on and finishes with the right answer when that is over",
          "waiting" in seen and time.time() - t0 >= 1.5 and st["state"] == "done" and st["verdict"] == "different"
          and {f["sha1"] for f in st["files"]} == {sha(big_a), sha(big_b)})

    print("limits, and requests that are not shaped right:")
    H.HASH.request({"action": "dismiss"})
    real_cap = H.HASH_CAP
    H.HASH_CAP = 3 * MiB
    check("a group whose sizes add up to more than the limit is refused before anything is read (413)", code_of(lambda: H.HASH.request({"action": "start", "name": "big.bin", "members": members(groups["big.bin"])})) == 413
          and H.HASH.status()["state"] == "idle")
    lie = [dict(m, size=10) for m in members(groups["big.bin"])]
    st = go("big.bin", lie)
    check("a size the screen got wrong does not get past the limit: the read itself is held to it (an error, said in words)", st["state"] == "error" and "bigger than" in st["error"] and tree(store) == snap)
    H.HASH_CAP = real_cap
    H.HASH.request({"action": "dismiss"})
    bad = members(groups["notes.txt"])
    check("fewer than two members, or more than the group limit: 400", code_of(lambda: H.HASH.request({"action": "start", "members": bad[:1]})) == 400
          and code_of(lambda: H.HASH.request({"action": "start", "members": bad * 7})) == 400)
    check("two members with the same id: 400", code_of(lambda: H.HASH.request({"action": "start", "members": [bad[0], bad[0]]})) == 400)
    check("an image the store does not list, a path out of the store: 404", code_of(lambda: H.HASH.request({"action": "start", "members": [dict(bad[0], image="../outside.img"), bad[1]]})) == 404
          and code_of(lambda: H.HASH.request({"action": "start", "members": [dict(bad[0], image="unassigned/nope.img"), bad[1]]})) == 404)
    check("a volume that was not listed: 404; an inode that is not one: 400", code_of(lambda: H.HASH.request({"action": "start", "members": [dict(bad[0], vol=7), bad[1]]})) == 404
          and code_of(lambda: H.HASH.request({"action": "start", "members": [dict(bad[0], inode="12; rm -rf /"), bad[1]]})) == 400)
    check("a body that is not an object, a member that is not an object, an action that is not one: 400", code_of(lambda: H.HASH.request([])) == 400
          and code_of(lambda: H.HASH.request({"action": "start", "members": ["x", "y"]})) == 400 and code_of(lambda: H.HASH.request({"action": "evil"})) == 400)
    plugged = open(mountinfo).read()
    open(mountinfo, "w").write("")
    unplugged = code_of(lambda: H.HASH.request({"action": "start", "members": bad}))
    open(mountinfo, "w").write(plugged)
    check("with the store unplugged: 409, IMAGE STORE NOT CONNECTED", unplugged == 409)
    check("every image is still exactly the bytes it was", all(hashlib.sha1(open(f"{store}/{r}", "rb").read()).hexdigest() == images[r] for r in (IA, IB)))

    print(f"\nhash: {passed} passed, {failed} failed")
    shutil.rmtree(T, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
