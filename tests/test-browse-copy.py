#!/usr/bin/env python3
"""The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 6), F4d: COPY TO THE STORE, on invented ext4 and FAT images.

What is checked is what the owner relies on, because this is the one place the browser writes: a file and a folder come out of an
image byte for byte, with a SHA-1 list beside them; a copy is always a new folder and nothing already there is touched; the only
files the job creates are under _exports; names out of an image are made safe and two that collide are kept apart; links are
skipped and said so; a deleted file is copied only when it is the thing asked for, and is marked as recovered; a file the image will
not give up is listed as failed and the rest is copied; a read that stalls, a copy read back from the store that does not match,
too little room, a disk job queued, and a stop each end the way they should; it stands still while a disk job runs and carries on
after; and no image is ever changed.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools, debugfs and Pillow; without them it skips, saying which.
    python3 tests/test-browse-copy.py
"""
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-copy-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(MiB), b""):
            h.update(b)
    return h.hexdigest()


def tree_files(root):
    """{relative path: sha1} of every regular file under root."""
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, root)] = sha(p)
    return out


def main():
    gone = F.missing() + [t for t in F.DELETE_TOOLS if not any(os.path.exists(f"{d}/{t}") for d in os.environ["PATH"].split(":") + ["/usr/sbin", "/sbin"])]
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the copy tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
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
    from gatboxlib import browse as B
    from gatboxlib import browsecopy as C

    # --- the invented content ------------------------------------------------------------------------------------------------
    src = f"{T}/src"
    os.makedirs(f"{src}/Docs/Sub/Deep")
    os.makedirs(f"{src}/Photos")
    os.makedirs(f"{src}/odd|name dir")
    os.makedirs(f"{src}/Empty")
    big = os.urandom(3 * MiB + 123)                                   # across three chunks and a bit
    files = {"Docs/keep.txt": b"kept\n", "Docs/Report.TXT": b"upper\n" * 100, "Docs/report.txt": b"lower\n" * 100, "Docs/empty.txt": b"",
             "Docs/big.bin": big, "Docs/Sub/a.txt": b"sub a\n", "Docs/Sub/Deep/b.txt": b"deep b\n", "Docs/gone.txt": b"gone readable\n" * 300,
             "Docs/gonec.txt": b"gone cleared\n" * 300, "Photos/p1.jpg": os.urandom(7000), "odd|name dir/pipe|file.TXT": b"pipe\n",
             "Docs/bad\nname.txt": b"newline in the name\n", "Docs/dot.": b"trailing dot\n"}
    for rel, data in files.items():
        open(f"{src}/{rel}", "wb").write(data)
    os.symlink("keep.txt", f"{src}/Docs/link")
    ext = f"{T}/e.img"
    F.ext4_whole(ext, src)
    F.ext4_delete(ext, "/Docs/gone.txt")
    F.ext4_delete(ext, "/Docs/gonec.txt", clear=True)
    # a FAT volume with a deleted folder
    ftree = f"{T}/ftree"
    os.makedirs(f"{ftree}/Old")
    open(f"{ftree}/Old/a.txt", "w").write("old a\n")
    open(f"{ftree}/Old/b.txt", "w").write("old b\n")
    open(f"{ftree}/keep.txt", "w").write("fat kept\n")
    fat = f"{T}/f.img"
    F.fat_image(fat, [f"{ftree}/Old", f"{ftree}/keep.txt"], label="WIDGETFAT")
    F.fat_delete(fat, 2048, trees=["Old"])
    E = F.put_in_store(store, "unassigned", "ext", ext)
    FA = F.put_in_store(store, "widget-wars", "fat", fat)
    image_sha = {r: sha(f"{store}/{r}") for r in (E, FA)}
    ext_sha1 = json.load(open(f"{store}/{E[:-4]}.json"))["sha1"]

    def store_state():
        """Every file on the store outside the browser's own folders, with its SHA-1: what a copy must leave exactly as it was."""
        return {os.path.relpath(os.path.join(d, f), store): sha(os.path.join(d, f)) for d, _, fs in os.walk(store) for f in fs
                if not os.path.relpath(os.path.join(d, f), store).startswith(("_exports", "_browse"))}
    outside_before = store_state()

    root = B.ls(E, 0, None)
    ino = {e["name"]: e for e in root["entries"]}
    docs = B.ls(E, 0, ino["Docs"]["inode"], deleted=True)
    dino = {e["name"]: e for e in docs["entries"]}
    froot = B.ls(FA, 2048, None, deleted=True)
    fino = {e["name"]: e for e in froot["entries"]}

    def wait(timeout=60):
        end = time.time() + timeout
        while C.EXP.busy() and time.time() < end:
            time.sleep(0.05)
        return not C.EXP.busy()

    def job(image=E, vol=0, entry=None, kind="file", name=None, deleted=False, **kw):
        body = {"action": "start", "image": image, "vol": vol, "kind": kind, "inode": entry["inode"] if entry else None,
                "name": name or (entry["name"] if entry else None), "size": entry["size"] if entry else 0, "deleted": deleted}
        body.update(kw)
        return C.EXP.request(body)

    def code_of(fn):
        try:
            fn()
        except B.Err as e:
            return e.code
        return None

    def final_dirs():
        return sorted(n for n in os.listdir(C.EXPORTS) if os.path.isdir(f"{C.EXPORTS}/{n}") and not n.endswith(".partial")) if os.path.isdir(C.EXPORTS) else []

    print("a file:")
    check("before anything: idle, and no exports folder is made by looking", C.EXP.status()["state"] == "idle" and not os.path.exists(C.EXPORTS))
    # What the unit gives it (ExecStartPre makes _exports 2775), and what its sandbox refuses: RestrictSUIDSGID=yes denies any chmod that names
    # the setgid bit. A copy once relied on `chmod 2775` for its folders, which worked here and left every folder 0700 on the Pi.
    os.umask(0o077)                                                        # the unit's UMask=0077: what the service runs with
    os.makedirs(C.EXPORTS)
    os.chmod(C.EXPORTS, 0o2775)
    probe = f"{C.EXPORTS}/.probe"
    os.mkdir(probe, 0o775)
    inherits = bool(os.stat(probe).st_mode & stat.S_ISGID)                 # a file system that does not pass the setgid bit down cannot show it
    os.rmdir(probe)
    real_chmod = os.chmod

    def sandbox_chmod(path, mode, *a, **k):
        if mode & 0o6000:
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(path, mode, *a, **k)
    os.chmod = sandbox_chmod
    want_dir = 0o2775 if inherits else 0o775
    job(entry=dino["keep.txt"])
    check("it finishes", wait())
    st = C.EXP.status()
    d = final_dirs()
    check("done: one new folder named for the time, the image's label and the item, and no .partial left",
          st["state"] == "done" and len(d) == 1 and re.fullmatch(r"\d{8}-\d{6}_ext_keep\.txt", d[0]) and not any(n.endswith(".partial") for n in os.listdir(C.EXPORTS)))
    f = f"{C.EXPORTS}/{d[0]}/keep.txt"
    check("the file is the original, byte for byte", open(f, "rb").read() == files["Docs/keep.txt"])
    m = json.load(open(f"{C.EXPORTS}/{d[0]}/{C.MANIFEST}"))
    check("the list: image SHA-1, volume, the item, one file with its size and SHA-1, verified, not a recovery",
          m["image"]["sha1"] == ext_sha1 and m["image"]["label"] == "ext" and m["volume"]["fs"] == "Ext4" and m["item"]["kind"] == "file"
          and m["files"][0]["path"] == "keep.txt" and m["files"][0]["sha1"] == sha(f) and m["files"][0]["size"] == 5 and m["verified"] is True
          and m["recovered"] is False and m["failed"] == [] and m["files"][0]["deleted"] is False)
    check("readable and writable by the group that shares the spool (0664), the time of the file kept",
          stat.S_IMODE(os.stat(f).st_mode) == 0o664 and os.stat(f).st_mtime > 631152000)
    check("the copy's folder is 0775 and takes the setgid bit from _exports (2775), with the sandbox refusing every chmod that names it: anyone can list it, the group can write (it was 0700)",
          stat.S_IMODE(os.stat(f"{C.EXPORTS}/{d[0]}").st_mode) == want_dir)
    check("recent copies lists it, with its file count and size", C.EXP.status()["recent"][0]["dir"] == d[0] and C.EXP.status()["recent"][0]["files"] == 1)
    C.EXP.request({"action": "dismiss"})
    check("dismiss clears the finished state", C.EXP.status()["state"] == "idle")

    print("a folder:")
    job(entry=ino["Docs"], kind="folder")
    check("it finishes", wait())
    st = C.EXP.status()
    d2 = [n for n in final_dirs() if n not in d]
    dest = f"{C.EXPORTS}/{d2[0]}"
    got = tree_files(dest)
    got.pop(C.MANIFEST, None)
    expect = {"keep.txt": files["Docs/keep.txt"], "empty.txt": b"", "big.bin": big, "Sub/a.txt": files["Docs/Sub/a.txt"],
              "Sub/Deep/b.txt": files["Docs/Sub/Deep/b.txt"], "bad^name.txt": files["Docs/bad\nname.txt"], "dot": files["Docs/dot."]}
    check("the folder's live files, byte for byte, in the same shape (across chunk boundaries too): seven, and the two that differ only in capitals",
          st["state"] == "done" and all(got.get(k) == hashlib.sha1(v).hexdigest() for k, v in expect.items()) and len(got) == 9)
    rep = [n for n in os.listdir(dest) if n.lower().startswith("report")]
    check("... the two names that differ only in capitals are kept apart: both are there, the second with (2), each with its own content",
          len(rep) == 2 and sum("(2)" in n for n in rep) == 1
          and {open(f"{dest}/{n}", "rb").read() for n in rep} == {files["Docs/Report.TXT"], files["Docs/report.txt"]})
    check("... a newline in a name arrives as sleuthkit shows it (a ^), and a trailing dot is made safe", os.path.exists(f"{dest}/bad^name.txt") and os.path.exists(f"{dest}/dot"))
    check("... an empty file is copied (empty)", os.path.exists(f"{dest}/empty.txt") and os.path.getsize(f"{dest}/empty.txt") == 0)
    check("... deleted files are not in a live folder's copy", not os.path.exists(f"{dest}/gone.txt") and not os.path.exists(f"{dest}/gonec.txt"))
    m = json.load(open(f"{dest}/{C.MANIFEST}"))
    check("the list has every file with its SHA-1, the link is skipped and said so, the folders are counted",
          len(m["files"]) == 9 and all(r["sha1"] == sha(f"{dest}/{r['path']}") for r in m["files"]) and m["skipped"] == [{"path": "link", "why": "a link"}]
          and m["folders"] == 2 and m["bytes"] == sum(r["size"] for r in m["files"]) and m["recovered"] is False)
    check("the folder's own sub-folders have the same mode, however deep", stat.S_IMODE(os.stat(f"{dest}/Sub").st_mode) == want_dir
          and stat.S_IMODE(os.stat(f"{dest}/Sub/Deep").st_mode) == want_dir)

    print("deleted files:")
    job(entry=dino["gone.txt"], deleted=True)
    wait()
    d3 = [n for n in final_dirs() if n not in d + d2]
    r = json.load(open(f"{C.EXPORTS}/{d3[0]}/{C.MANIFEST}"))
    check("a deleted file asked for by name is copied, and marked as recovered in the list (both on the file and on the copy)",
          open(f"{C.EXPORTS}/{d3[0]}/gone.txt", "rb").read() == files["Docs/gone.txt"] and r["recovered"] is True and r["files"][0]["deleted"] is True
          and "may be incomplete" in r["note"])
    job(entry=dino["gonec.txt"], deleted=True)
    wait()
    d4 = [n for n in final_dirs() if n not in d + d2 + d3]
    r = json.load(open(f"{C.EXPORTS}/{d4[0]}/{C.MANIFEST}"))
    check("a deleted file the drive cleared is copied as what it is: empty, and marked deleted", os.path.getsize(f"{C.EXPORTS}/{d4[0]}/gonec.txt") == 0
          and r["files"][0]["deleted"] is True)
    job(image=FA, vol=2048, entry=fino["Old"], kind="folder", deleted=True)
    wait()
    d5 = [n for n in final_dirs() if n not in d + d2 + d3 + d4]
    r = json.load(open(f"{C.EXPORTS}/{d5[0]}/{C.MANIFEST}"))
    names5 = sorted(x["path"] for x in r["files"])
    check("a deleted folder is copied with what is in it (FAT lost the first letters: the two names are kept apart), all marked recovered",
          names5 == ["_ (2).txt", "_.txt"] and all(x["deleted"] for x in r["files"]) and r["recovered"] is True
          and sorted(open(f"{C.EXPORTS}/{d5[0]}/{n}").read() for n in names5) == ["old a\n", "old b\n"])
    job(image=FA, vol=2048, kind="folder", inode=None, name="WIDGETFAT")
    wait()
    d6 = [n for n in final_dirs() if n not in d + d2 + d3 + d4 + d5]
    r = json.load(open(f"{C.EXPORTS}/{d6[0]}/{C.MANIFEST}"))
    check("the whole volume is a folder too (its root), live files only: the deleted folder is not in it", [x["path"] for x in r["files"]] == ["keep.txt"])

    print("never overwriting:")
    old = {n: tree_files(f"{C.EXPORTS}/{n}") for n in final_dirs()}
    real_strftime = time.strftime
    time.strftime = lambda fmt, *a: "20990101-000000" if fmt == "%Y%m%d-%H%M%S" else real_strftime(fmt, *a)
    try:
        job(entry=dino["keep.txt"])
        wait()
        job(entry=dino["keep.txt"])
        wait()
    finally:
        time.strftime = real_strftime
    same = sorted(n for n in final_dirs() if n.startswith("20990101-000000"))
    check("the same item copied twice at the same second: two folders, the second with a suffix, neither mixed with the other",
          same == ["20990101-000000_ext_keep.txt", "20990101-000000_ext_keep.txt-2"] and all(open(f"{C.EXPORTS}/{n}/keep.txt", "rb").read() == b"kept\n" for n in same))
    check("... and every copy made before is exactly as it was", all(tree_files(f"{C.EXPORTS}/{n}") == v for n, v in old.items()))
    check("only the files the job made are new: nothing outside _exports changed (images, sidecars), after a dozen copies",
          store_state() == outside_before and {r_: sha(f"{store}/{r_}") for r_ in (E, FA)} == image_sha)

    print("what it refuses before it starts:")
    check("an image that is not in the store -> 404, a volume that is not there -> 404, a kind that is not one -> 400, a bad inode -> 400",
          code_of(lambda: job(image="../x.img", entry=dino["keep.txt"])) == 404 and code_of(lambda: job(entry=dino["keep.txt"], vol=999)) == 404
          and code_of(lambda: job(entry=dino["keep.txt"], kind="evil")) == 400 and code_of(lambda: job(entry=dino["keep.txt"], inode="1; rm -rf /")) == 400
          and code_of(lambda: job(kind="file", inode=None)) == 400)
    check("an action that is not one -> 400, and a body that is not an object -> 400", code_of(lambda: C.EXP.request({"action": "format"})) == 400
          and code_of(lambda: C.EXP.request([1])) == 400)
    open(f"{spool}/request.json", "w").write("{}")
    check("while a disk job is queued or running: 409, and nothing is made", code_of(lambda: job(entry=dino["keep.txt"])) == 409)
    os.remove(f"{spool}/request.json")
    n_before = len(final_dirs())

    print("room, and what it does when things go wrong:")
    real_free = C.free_space
    C.free_space = lambda p: (5 * MiB, 10**12)
    job(entry=dino["big.bin"])
    wait()
    st = C.EXP.status()
    check("not enough room (a reserve is kept): it says so, with the numbers, and creates nothing", st["state"] == "error" and "not enough room" in st["error"]
          and len(final_dirs()) == n_before and not any(n.endswith(".partial") for n in os.listdir(C.EXPORTS)))
    C.free_space = real_free
    C.EXP.request({"action": "dismiss"})
    # a stand-in icat in front of the real one: control files beside it (the browser scrubs its environment)
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
    open(f"{stub}/failino", "w").write(dino["keep.txt"]["inode"])
    job(entry=ino["Docs"], kind="folder")
    wait()
    st = C.EXP.status()
    dX = [n for n in final_dirs() if n not in same + d + d2 + d3 + d4 + d5 + d6]
    rX = json.load(open(f"{C.EXPORTS}/{dX[0]}/{C.MANIFEST}"))
    check("a file the image will not give up: the rest of the folder is copied, it is listed as failed (in the list and on the screen), no empty file is left for it",
          st["state"] == "done" and st["failed"] == 1 and [x["path"] for x in rX["failed"]] == ["keep.txt"] and not os.path.exists(f"{C.EXPORTS}/{dX[0]}/keep.txt")
          and os.path.exists(f"{C.EXPORTS}/{dX[0]}/big.bin") and len(rX["files"]) == 8)
    os.remove(f"{stub}/failino")
    open(f"{stub}/hang", "w").close()
    C.STALL_S = 1
    job(entry=dino["big.bin"])
    wait(30)
    st = C.EXP.status()
    check("a read that gives nothing for too long is stopped, and says which file: the copy stops (an error), the half is removed, the .partial is kept",
          st["state"] == "error" and "stalled" in st["error"] and "big.bin" in st["error"]
          and any(n.endswith(".partial") for n in os.listdir(C.EXPORTS)) and not [p for d_, _, fs in os.walk(C.EXPORTS) for p in fs if p == "big.bin" and d_.endswith(".partial")])
    C.STALL_S = 180
    os.remove(f"{stub}/hang")
    os.environ["PATH"] = real_path
    C.EXP.request({"action": "dismiss"})
    check("... and the incomplete copy is listed as incomplete, not as a copy", any(r_["incomplete"] for r_ in C.EXP.status()["recent"]))

    real_sha1, calls = hashlib.sha1, [0]


    class Liar:
        def __init__(self):
            self.h = real_sha1()

        def update(self, b):
            self.h.update(b)

        def hexdigest(self):
            return "0" * 40

    def sha1_second_wrong(*a):
        calls[0] += 1
        return real_sha1(*a) if calls[0] % 2 else Liar()

    class Shim:                                           # the copy module's own hashlib: nothing else is affected
        sha1 = staticmethod(sha1_second_wrong)
    C.hashlib = Shim
    try:
        job(entry=dino["keep.txt"])
        wait()
    finally:
        C.hashlib = hashlib
    st = C.EXP.status()
    check("a copy read back from the store that does not match what was written stops the job, loudly, and no copy is finished",
          st["state"] == "error" and "does not match what was written" in st["error"] and "keep.txt" in st["error"])
    C.EXP.request({"action": "dismiss"})

    print("a disk job, and a stop:")
    real_wait = C.Exporter._wait_turn
    seen, n, polling = [], [0], threading.Event()

    def poll():
        while not polling.is_set():
            seen.append(C.EXP.state.get("state"))
            time.sleep(0.05)

    def wait_with_disk_job(self):
        n[0] += 1
        if n[0] == 3:
            open(f"{spool}/running.json", "w").write("{}")
            threading.Timer(2.0, lambda: os.remove(f"{spool}/running.json")).start()
        return real_wait(self)
    C.Exporter._wait_turn = wait_with_disk_job
    C.CHUNK = 64 * 1024
    threading.Thread(target=poll, daemon=True).start()
    try:
        job(entry=dino["big.bin"])
        t0 = time.time()
        wait(60)
    finally:
        polling.set()
        C.Exporter._wait_turn = real_wait
        C.CHUNK = MiB
    d7 = [n_ for n_ in final_dirs() if n_ not in same + d + d2 + d3 + d4 + d5 + d6 + dX]
    check("a disk job that starts in the middle of a copy makes it stand still (the screen sees 'waiting'), and it carries on and finishes when that is over",
          "waiting" in seen and time.time() - t0 >= 1.5 and C.EXP.status()["state"] == "done" and len(d7) == 1
          and open(f"{C.EXPORTS}/{d7[0]}/big.bin", "rb").read() == big)
    C.EXP.request({"action": "dismiss"})

    def slow_wait(self):
        time.sleep(0.02)
        return real_wait(self)
    C.Exporter._wait_turn = slow_wait
    C.CHUNK = 4096
    try:
        job(entry=dino["big.bin"])
        time.sleep(0.6)
        check("one at a time: a second start while one is running -> 409", code_of(lambda: job(entry=dino["keep.txt"])) == 409)
        C.EXP.request({"action": "stop"})
        wait(30)
    finally:
        C.Exporter._wait_turn = real_wait
        C.CHUNK = MiB
    st = C.EXP.status()
    where = re.search(r"in (\S+\.partial)", st.get("error") or "")
    check("stop: it stops, says where what was copied so far is, and the half-written file is removed",
          st["state"] == "stopped" and where and os.path.isdir(f"{C.EXPORTS}/{where.group(1)}") and not os.path.exists(f"{C.EXPORTS}/{where.group(1)}/big.bin"))
    C.EXP.request({"action": "dismiss"})

    # a file whose mode cannot be set stops the copy, loudly: it used to be swallowed, which hid the failure above
    def refuse_all(path, mode, *a, **k):
        raise PermissionError(1, "Operation not permitted")
    os.chmod = refuse_all
    job(entry=dino["keep.txt"])
    wait()
    st = C.EXP.status()
    check("a copy whose files cannot be given their mode stops with a sentence, and finishes nothing",
          st["state"] == "error" and "could not set the permissions" in st["error"])
    os.chmod = sandbox_chmod
    C.EXP.request({"action": "dismiss"})
    os.chmod = real_chmod

    again = os.umask(0o077)
    os.umask(again)
    check("the umask is put back after every folder made (it is process-wide, and the service runs with 0077)", again == 0o077)
    print(f"  note  the setgid bit {'was' if inherits else 'was NOT'} passed down by this file system, so the folder checks {'include' if inherits else 'leave out'} it")

    print("names:")
    check("safe_name: separators, control characters, '.' and '..', trailing dots and spaces",
          C.safe_name("a/b\\c") == "a_b_c" and C.safe_name("x\ny\x00z") == "x_y_z" and C.safe_name("..") == "_" and C.safe_name(".") == "_"
          and C.safe_name("name. . ") == "name" and C.safe_name("   ") == "_" and C.safe_name("") == "_")
    long = "é" * 300
    check("safe_name: a long name is cut to at most 200 bytes without splitting a character", len(C.safe_name(long).encode()) <= 200 and C.safe_name(long) == "é" * 100)
    taken = set()
    check("unique: a name taken in any capitals gets (2), (3)...; the extension stays last",
          [C.unique(taken, x) for x in ("a.txt", "A.TXT", "a.txt", "b")] == ["a.txt", "A (2).TXT", "a (3).txt", "b"])
    print("nothing was changed:")
    check("no image changed", {r_: sha(f"{store}/{r_}") for r_ in (E, FA)} == image_sha)
    print(f"\ncopy to the store: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
