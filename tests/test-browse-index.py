#!/usr/bin/env python3
"""The drive browser's name index and search (docs/plans/2026-11-15-drive-browser.md), F2: gatboxlib/browseindex.py, its
routes in gatbox-browse and gatbox-web, against invented images (tests/fakeimage.py). Nothing real is opened or mounted.

What is checked is what the owner relies on: every file and folder of an indexed image is found by the words in its path
(prefixes, all words required, kinds, years, sizes, paging); a volume is searchable only when its pass is complete, so a stop
or a crash leaves nothing; the pass stands still for a disk job and goes on after it; a restart finishes the queue; the index
can be deleted and rebuilt; a result says where its image is now; the search says how much of the store it looked at; a
query cannot break the index; limits are said in words; and gatbox-web answers only the Pi's own screen.

Needs the sleuthkit tools, mke2fs, mkfs.fat, mtools and Pillow; without them it skips, saying which.
    python3 tests/test-browse-index.py
"""
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "backend"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-index-test-")
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
        print(f"  skip  the index tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools, python3-pil)")
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
    from gatboxlib import browseindex as X

    tree = f"{T}/tree"
    os.makedirs(tree)
    F.sample_tree(tree, many=230)
    nfiles = sum(1 for d, _, fs in os.walk(tree) for f in fs if not os.path.islink(os.path.join(d, f)))
    ndirs = sum(len(ds) for _, ds, _ in os.walk(tree)) + 1                      # and lost+found
    ext = f"{T}/e.img"
    F.ext4_image(ext, tree)
    fat = f"{T}/f.img"
    F.fat_image(fat, [f"{tree}/Photos", f"{tree}/Docs"], label="WIDGETFAT")
    rnd = f"{T}/r.img"
    open(rnd, "wb").write(os.urandom(2 * MiB))
    E = F.put_in_store(store, "unassigned", "ext", ext)
    FA = F.put_in_store(store, "widget-wars", "fat", fat)
    R = F.put_in_store(store, "unassigned", "rnd", rnd)
    sha_e = json.load(open(f"{store}/{E[:-4]}.json"))["sha1"]
    sha_f = json.load(open(f"{store}/{FA[:-4]}.json"))["sha1"]
    sha_r = json.load(open(f"{store}/{R[:-4]}.json"))["sha1"]

    def cov(r):
        c = r["coverage"]
        return c["images"], c["indexed"], c["indexing"]

    def wait(timeout=90):
        end = time.time() + timeout
        while X.IDX.busy() and time.time() < end:
            time.sleep(0.1)
        return not X.IDX.busy()

    def waiting_for(pred, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False

    print("before anything is indexed:")
    r = X.search("img")
    check("a search finds nothing, and says no image has been searched: 0 of 3", r["total"] == 0 and r["results"] == []
          and cov(r) == (3, 0, 0))
    check("the index is not created by looking: no file yet", not os.path.exists(X.db_path()))
    X.IDX.kick()
    X.state()
    check("looking at the index (its state, starting a worker) creates nothing and starts nothing", not os.path.exists(X.db_path()) and not X.IDX.busy())
    check("no words, or a kind that is not one -> 400", code_of(lambda: X.search("")) == 400 and code_of(lambda: X.search("   ")) == 400
          and code_of(lambda: X.search("img", kind="evil")) == 400 and code_of(lambda: X.search("___")) == 400
          and code_of(lambda: X.search('"')) == 400)

    print("indexing an ext4 image:")
    X.IDX.request("start", E)
    check("queued or running right away", X.state()["images"][sha_e]["state"] in ("queued", "indexing", "done"))
    check("it finishes", wait())
    st = X.state()["images"][sha_e]
    check(f"done: {nfiles} files and {ndirs} folders, the swap partition left out", st["state"] == "done" and st["files"] == nfiles
          and st["entries"] == nfiles + ndirs and len(st["volumes"]) == 1 and st["volumes"][0]["fs"] == "Ext4")
    r = X.search("img")
    check("a word finds the file by its path, with its kind, size and image", r["total"] == 1 and r["results"][0]["path"] == "Photos/2019/IMG_0001.jpg"
          and r["results"][0]["kind"] == "photo" and r["results"][0]["image"] == E and r["results"][0]["folder"] == "Photos/2019"
          and r["results"][0]["size"] > 0 and r["results"][0]["label"] == "ext" and r["results"][0]["volume"] == "Ext4")
    check("... and it has searched 1 of 3 images, saying so", cov(r) == (3, 1, 0))
    check("a name with an underscore, a prefix and capitals all find it", X.search("IMG_0001")["total"] == 1 and X.search("im")["total"] == 1
          and X.search("MANU")["results"][0]["path"] == "Docs/manual.pdf")
    both = X.search("photos 2019")
    check("every word must be in the path: the folder and the two photos under it", sorted(x["path"] for x in both["results"])
          == ["Photos/2019", "Photos/2019/IMG_0001.jpg", "Photos/2019/red.png"] and X.search("photos zebra")["total"] == 0)
    check("a folder is a result, as a folder", [x["kind"] for x in both["results"] if x["name"] == "2019"] == ["folder"])
    check("a name with a pipe in it is found whole", X.search("pipe")["results"][0]["path"] == "odd|name dir/pipe|file.TXT")
    check("kinds: photos only the two pictures, folders only the folder, documents none of them",
          sorted(x["name"] for x in X.search("2019", kind="photos")["results"]) == ["IMG_0001.jpg", "red.png"]
          and [x["name"] for x in X.search("2019", kind="folders")["results"]] == ["2019"]
          and X.search("2019", kind="documents")["total"] == 0 and X.search("big", kind="other")["results"][0]["name"] == "big.bin")
    check("size: a minimum and a maximum", X.search("big", size_min=1_000_000)["total"] == 1 and X.search("big", size_max=1000)["total"] == 0)
    this_year = int(time.strftime("%Y"))
    ys = X.search("img")["years"]
    check("years: the chips come from the matches, and a year narrows them", ys and ys[0][0] == this_year and X.search("img", year=this_year)["total"] == 1
          and X.search("img", year=1999)["total"] == 0)
    p = X.search("n0")
    check("paging: 230 results, 50 a page, 5 pages, the last one 30; a page past the end is the last", p["total"] == 230 and p["pages"] == 5
          and len(p["results"]) == 50 and len(X.search("n0", page=4)["results"]) == 30 and X.search("n0", page=99)["page"] == 4)
    hostile = [X.search(q) for q in ('img" OR *', "NEAR(img", "img*", "'; DROP TABLE files; --", "a:b c^d")]
    check("a query that is FTS syntax cannot break it: words only, no error, and the index is intact",
          all(isinstance(h, dict) for h in hostile) and X.search("img")["total"] == 1)

    print("where a result is:")
    hit = X.search("img")["results"][0]
    loc = X.locate(hit["id"])
    vol = B.volumes(E)["volumes"][0]["start"]
    root = {e["name"]: e["inode"] for e in B.ls(E, vol)["entries"]}
    sub = {e["name"]: e["inode"] for e in B.ls(E, vol, root["Photos"])["entries"]}
    check("locate: the image, the volume and the folders above it with their inodes, root first",
          loc["image"] == E and loc["vol"] == vol and [d["name"] for d in loc["dirs"]] == ["Photos", "2019"]
          and loc["dirs"][0]["inode"] == root["Photos"] and loc["dirs"][1]["inode"] == sub["2019"] and loc["name"] == "IMG_0001.jpg")
    deep = B.ls(E, vol, sub["2019"], at=loc["inode"])
    check("ls at= the file's inode: that folder, with the file in it", loc["inode"] in [e["inode"] for e in deep["entries"]] and deep["at"] == loc["inode"])
    last = X.search("n0229")["results"][0]
    lloc = X.locate(last["id"])
    page = B.ls(E, vol, lloc["dirs"][-1]["inode"], at=lloc["inode"])
    check("ls at= an entry past the first 200: the page that holds it (page 2 of 2), not the first", page["page"] == 1 and page["pages"] == 2
          and lloc["inode"] in [e["inode"] for e in page["entries"]] and B.ls(E, vol, lloc["dirs"][-1]["inode"])["page"] == 0)
    check("ls at= an inode that is not there: the first page, no error", B.ls(E, vol, lloc["dirs"][-1]["inode"], at="999999")["at"] is None
          and code_of(lambda: B.ls(E, vol, None, at="12;rm")) == 400)
    check("locate a result that is not in the index -> 404", code_of(lambda: X.locate(10 ** 9)) == 404 and code_of(lambda: X.locate("x")) == 404)

    print("every image:")
    X.IDX.request("all")
    check("INDEX ALL finishes: the ext4, the FAT volume with its label; the one with no filesystem says so", wait())
    s = X.state()["images"]
    check("... the FAT image is done with its label, the ext4 one is not done twice", s[sha_f]["state"] == "done" and s[sha_f]["volumes"][0]["label"] == "WIDGETFAT"
          and s[sha_e]["files"] == nfiles)
    check("... the image with no filesystem is an error that says why, not an empty success", s[sha_r]["state"] == "error"
          and "no filesystem" in s[sha_r]["error"])
    r = X.search("2019")
    check("a search covers both images and shows which each result is in", {x["label"] for x in r["results"]} == {"ext", "fat"}
          and cov(r) == (3, 2, 0))
    fatpaths = {x["path"] for x in X.search("img", kind="photos")["results"]}
    check("FAT paths are indexed the way they are listed", "Photos/2019/IMG_0001.jpg" in fatpaths or any(p.lower().endswith("img_0001.jpg") for p in fatpaths))

    print("a copy of an image under another machine (the same bytes):")
    os.makedirs(f"{store}/simpsons")
    shutil.copy(f"{store}/{E}", f"{store}/simpsons/ext2_copy.img")
    side = json.load(open(f"{store}/{E[:-4]}.json"))
    side["machine"], side["label"] = "simpsons", "ext2"
    json.dump(side, open(f"{store}/simpsons/ext2_copy.json", "w"))
    X.IDX.request("all")
    wait()
    c = sqlite3.connect(X.db_path())
    n = c.execute("SELECT count(*) FROM volumes WHERE sha1=?", (sha_e,)).fetchone()[0]
    c.close()
    check("indexed once, by its SHA-1, whichever copy is asked for; the store has 3 distinct images still", n == 1 and X.search("img")["coverage"]["images"] == 3)
    os.remove(f"{store}/simpsons/ext2_copy.img")
    os.remove(f"{store}/simpsons/ext2_copy.json")

    print("the image moves, goes, or is replaced:")
    shutil.move(f"{store}/{E}", f"{store}/{E}.away")
    shutil.move(f"{store}/{E[:-4]}.json", f"{store}/{E[:-4]}.json.away")
    gone_hit = X.search("img")["results"]
    check("an image that is not in the store any more: its results say image None, and locate says so", [h["image"] for h in gone_hit if h["label"] == "ext"] == [None]
          and code_of(lambda: X.locate(hit["id"])) == 404)
    os.makedirs(f"{store}/widget-wars", exist_ok=True)
    os.rename(f"{store}/{E}.away", f"{store}/widget-wars/ext_moved.img")
    side = json.load(open(f"{store}/{E[:-4]}.json.away"))
    json.dump(side, open(f"{store}/widget-wars/ext_moved.json", "w"))
    os.remove(f"{store}/{E[:-4]}.json.away")
    moved = [h for h in X.search("img")["results"] if h["label"] == "ext"]
    check("moved to another folder: found again by its SHA-1, with its new name", moved and moved[0]["image"] == "widget-wars/ext_moved.img"
          and X.locate(hit["id"])["image"] == "widget-wars/ext_moved.img")
    os.rename(f"{store}/widget-wars/ext_moved.img", f"{store}/{E}")
    os.rename(f"{store}/widget-wars/ext_moved.json", f"{store}/{E[:-4]}.json")
    check("... and back", [h["image"] for h in X.search("img")["results"] if h["label"] == "ext"] == [E])

    print("a stand-in fls that takes its time (a pass that can be seen, stopped and paused):")
    real_fls = shutil.which("fls")
    stub = f"{T}/bin"
    os.makedirs(stub)
    # The browser starts its tools with a scrubbed environment, so the stand-in is steered by files beside it: `slow` holds the
    # seconds to wait after each line, `fail` makes it exit 1 with nothing.
    open(f"{stub}/fls", "w").write(
        "#!/bin/sh\n"
        "here=$(dirname \"$0\")\n"
        "if [ -e \"$here/fail\" ]; then echo 'fls: cannot open image' >&2; exit 1; fi\n"
        f"{real_fls} \"$@\" | while IFS= read -r l; do printf '%s\\n' \"$l\"; s=$(cat \"$here/slow\" 2>/dev/null); sleep ${{s:-0}}; done\n")
    os.chmod(f"{stub}/fls", 0o755)
    os.environ["PATH"] = f"{stub}:" + os.environ["PATH"]

    def slow(seconds):
        open(f"{stub}/slow", "w").write(str(seconds))
    X.IDX.request("remove", E)
    check("remove: the image's rows are gone, and it is searched no more", X.state()["images"].get(sha_e) is None
          and all(h["label"] != "ext" for h in X.search("img")["results"]))
    slow(0.03)
    X.BATCH = 20                                      # so that a pass that is stopped half way has rows in the index to remove
    X.IDX.request("start", E)
    check("a pass that is under way is shown, with its count going up", waiting_for(lambda: X.state()["images"].get(sha_e, {}).get("state") == "indexing"
                                                                                  and X.state()["images"][sha_e]["entries"] > 5))
    def unfinished_rows():
        c = sqlite3.connect(X.db_path())
        try:
            return c.execute("SELECT count(*) FROM files WHERE vol IN (SELECT id FROM volumes WHERE state != 'done')").fetchone()[0]
        finally:
            c.close()
    waiting_for(lambda: unfinished_rows() > 0, 20)
    partial_rows = unfinished_rows()
    mid = X.search("2019")
    check("... and none of its half-finished volume can be found, though rows are already written: nothing from this image, and the coverage says it is being indexed",
          partial_rows > 0 and all(h["label"] != "ext" for h in mid["results"]) and mid["coverage"]["indexing"] >= 1)
    X.IDX.request("stop", E)
    check("stop: the pass ends and leaves nothing behind (no rows, no volume, not queued)", waiting_for(lambda: not X.IDX.busy()) and X.state()["images"].get(sha_e) is None)
    time.sleep(0.5)
    c = sqlite3.connect(X.db_path())
    left = c.execute("SELECT count(*) FROM files WHERE vol NOT IN (SELECT id FROM volumes WHERE state='done')").fetchone()[0] + \
        c.execute("SELECT count(*) FROM names WHERE rowid NOT IN (SELECT id FROM files)").fetchone()[0]
    c.close()
    check("... and no row of it is left in the index (every row belongs to a finished volume)", left == 0)
    X.BATCH = 2000

    print("a disk job takes priority:")
    X.IDX.request("start", E)
    waiting_for(lambda: X.state()["images"].get(sha_e, {}).get("state") == "indexing" and X.state()["images"][sha_e]["entries"] > 3)
    open(f"{spool}/request.json", "w").write("{}")
    check("the pass stands still: WAITING, with the reason", waiting_for(lambda: X.state()["images"][sha_e]["state"] == "waiting")
          and "disk job" in (X.state()["reason"] or ""))
    n1 = X.state()["images"][sha_e]["entries"]
    time.sleep(1.5)
    n2 = X.state()["images"][sha_e]["entries"]
    check("... and makes no progress while it waits", n2 - n1 <= 2)
    os.remove(f"{spool}/request.json")
    slow(0)
    check("the disk job is over: it goes on and finishes, whole", waiting_for(lambda: X.state()["images"][sha_e]["state"] == "done", 60)
          and X.state()["images"][sha_e]["files"] == nfiles)

    print("the browser is restarted in the middle of a pass:")
    X.IDX.request("remove", E)
    c = sqlite3.connect(X.db_path())
    c.execute("INSERT INTO volumes(id, sha1, rel, label, start, fs, vlabel, state, entries) VALUES(900, ?, ?, 'ext', ?, 'Ext4', '', 'indexing', 50)", (sha_e, E, vol))
    c.executemany("INSERT INTO files(id, vol, parent, inode, name, kind, size, mtime) VALUES(?,?,?,?,?,?,?,?)",
                  [(1000 + i, 900, None, str(i), f"ghost{i}", "other", 1, None) for i in range(50)])
    c.executemany("INSERT INTO names(rowid, path) VALUES(?,?)", [(1000 + i, f"ghost{i}") for i in range(50)])
    c.commit()
    c.close()
    X.IDX.__init__()                                 # a new process: nothing in memory
    check("the half-written volume is not searchable", X.search("ghost0")["total"] == 0)
    X.IDX.kick()
    check("a start finishes the work that was left: the ghost rows are gone, the volume is whole", wait() and X.search("ghost0")["total"] == 0
          and X.state()["images"][sha_e]["state"] == "done" and X.state()["images"][sha_e]["files"] == nfiles)

    print("the index is disposable:")
    shutil.rmtree(f"{store}/_browse")
    check("deleted: nothing is found, nothing breaks, and the images are as they were", X.search("img")["total"] == 0 and X.state()["images"] == {}
          and os.path.exists(f"{store}/{E}"))
    X.IDX.request("all")
    check("and it is built again", wait() and X.search("img")["total"] == 2)
    with open(X.db_path(), "r+b") as f:
        f.write(b"this is not a database at all" * 10)
    code = code_of(lambda: X.search("img"))
    check("a file that is not a database is an error in words, not a crash", code in (None, 503))

    print("failures and limits, in words:")
    shutil.rmtree(f"{store}/_browse")
    open(f"{stub}/fail", "w").close()
    X.IDX.request("start", E)
    wait()
    st = X.state()["images"][sha_e]
    check("fls that fails: the volume is an error that says it could not be listed, and nothing is half-indexed", st["state"] == "error"
          and "could not be listed" in st["error"] and X.search("img")["total"] == 0)
    os.remove(f"{stub}/fail")
    X.IDX.request("start", E)
    check("tapping again tries again, and it works", wait() and X.state()["images"][sha_e]["state"] == "done")
    X.IDX.request("remove", E)
    X.INDEX_CAP = 100
    X.IDX.request("start", E)
    wait()
    st = X.state()["images"][sha_e]
    check("more entries than the cap: an error that says so, and nothing is kept", st["state"] == "error" and "more than 100 entries" in st["error"]
          and X.search("img")["total"] == 0)
    X.INDEX_CAP = 10_000_000
    X.DIR_CAP = 2
    X.IDX.request("start", E)
    wait()
    st = X.state()["images"][sha_e]
    got = X.search("img")["results"]
    loc = X.locate(got[0]["id"]) if got else None
    check("more folders than can be remembered: still indexed, with a note, and a file whose folder was forgotten opens from the top, not an error",
          st["state"] == "done" and any("folders" in (v["note"] or "") for v in st["volumes"]) and loc is not None and isinstance(loc["dirs"], list))
    X.DIR_CAP = 500_000
    X.IDX.request("remove", E)
    X.IDX.request("stop_all")

    print("the store is unplugged:")
    X.IDX.request("start", E)
    wait()
    open(mountinfo, "w").write("23 22 179:1 / /boot/firmware rw shared:2 - vfat /dev/mmcblk0p1 rw\n")
    check("a search is answered from what is indexed, not from the store", isinstance(X.search("img"), dict))
    check("asking to index says the store is not connected", code_of(lambda: X.IDX.request("start", FA)) == 409)
    open(mountinfo, "w").write(f"40 22 8:33 / {store} rw,noatime shared:9 - ext4 /dev/sdc1 rw\n")

    print("what the service does and does not do:")
    src = open(os.path.join(REPO, "backend/gatboxlib/browseindex.py")).read()
    check("the index module starts only fls, and only through the browse core's own list (no mmls, icat, mount, dd, ...)",
          'argv = ["fls", "-r", "-p", "-m", "/", "-o", str(start), path]' in src
          and "import os\nimport re\nimport sqlite3\nimport subprocess" in src and src.count("subprocess.Popen(") == 1)
    import re
    code_only = re.sub(r'"""[\s\S]*?"""', "", "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
    banned = [w for w in ("mount", "umount", "losetup", "mkfs", "wipefs", "sfdisk", "dd", "mmls", "fsstat", "sudo") if re.search(rf"\b{w}\b", code_only)]
    check("... and never names a mount, a format, dd or a partitioner", not banned)
    if banned:
        print("        found:", banned)
    idle_side(E, sha_e, slow)
    return web_side(E, sha_e)


def idle_side(E, sha_e, slow):
    """The service leaves when nothing has asked for a while, but not while it is indexing."""
    shutil.rmtree(f"{T}/store/_browse", ignore_errors=True)
    sock = f"{T}/idle.sock"

    def ux(method, path, body=None):
        class C(http.client.HTTPConnection):
            def connect(self):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(30)
                s.connect(sock)
                self.sock = s
        c = C("localhost")
        try:
            c.request(method, path, body=json.dumps(body) if body is not None else None)
            return json.loads(c.getresponse().read())
        finally:
            c.close()
    print("the service and its idle exit:")
    slow(0.04)
    d = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-browse"), "--socket", sock, "--idle", "2"],
                         env=dict(os.environ, GATBOX_LIB=os.path.join(REPO, "backend")))
    try:
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        ux("POST", "/index", {"action": "start", "image": E})
        time.sleep(4.5)                                              # more than twice --idle, with nothing asking
        check("it is still there after 4 s of nobody asking, because it is indexing", d.poll() is None)
        end = time.time() + 60
        done = False
        while time.time() < end and d.poll() is None and not done:
            time.sleep(0.5)
            c = sqlite3.connect(f"{T}/store/_browse/browse.sqlite")
            done = c.execute("SELECT count(*) FROM volumes WHERE state='done'").fetchone()[0] == 1
            c.close()
        check("the pass finishes in the background with nobody watching", done)
        slow(0)
        try:
            rc = d.wait(timeout=12)
        except subprocess.TimeoutExpired:
            rc = None
        check("and once it is done and nothing asks, it leaves (exit 0)", rc == 0)
    finally:
        if d.poll() is None:
            d.terminate()
            d.wait(10)


def _lan_addrs():
    try:
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=False).stdout.split()
    except (FileNotFoundError, OSError):
        return []


def web_side(E, sha_e):
    import urllib.error
    import urllib.request
    port = 8098
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is in use")
    print("the routes (gatbox-browse and gatbox-web):")
    w = f"{T}/web"
    for d in ("log", "run", "ctrl", "cache", "data", "spool", "sync"):
        os.makedirs(f"{w}/{d}")
    for f in ("profiles.json", "gatbox-machine-specs.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{w}/data/")
    json.dump({"meta": {}, "video_games": [{"slug": "widget-wars", "name": "Widget Wars", "platform": "x"}], "pinball": []},
              open(f"{w}/data/gatbox-barcade-roster.json", "w"))
    sock = f"{T}/web-browse.sock"
    shutil.rmtree(f"{T}/store/_browse", ignore_errors=True)
    denv = dict(os.environ, GATBOX_LIB=os.path.join(REPO, "backend"))
    wenv = dict(denv, GATBOX_WEB_PORT=str(port), STATE_DIRECTORY=f"{w}/ctrl", CACHE_DIRECTORY=f"{w}/cache", GATBOX_LOGDIR=f"{w}/log",
                GATBOX_RUNDIR=f"{w}/run", GATBOX_DATA=f"{w}/data", GATBOX_SYNC_STATE=f"{w}/sync", GATBOX_DISK_SPOOL=f"{T}/spool",
                GATBOX_DUMP_SPOOL=f"{w}/nodump", MPLCONFIGDIR=f"{w}/cache/mpl", GATBOX_BROWSE_SOCK=sock)
    d = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-browse"), "--socket", sock, "--idle", "120"], env=wenv)
    web = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-web")], env=wenv, stdout=open(f"{w}/web.log", "w"),
                           stderr=subprocess.STDOUT)
    B = f"http://127.0.0.1:{port}"

    def req(path, method="GET", body=None, base=B, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype} if data is not None else {})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw[:80]}
    try:
        for _ in range(100):
            try:
                req("/kiosk/state")
                break
            except OSError:
                time.sleep(0.1)
        code, j = req("/api/browse/index")
        check("GET /api/browse/index: the state, nothing indexed", code == 200 and j["images"] == {} and j["working"] is False)
        code, j = req("/api/browse/search?q=img")
        check("GET /api/browse/search with nothing indexed: no results, 0 of 3 searched", code == 200 and j["total"] == 0 and j["coverage"]["indexed"] == 0)
        code, j = req("/api/browse/index", "POST", {"action": "start", "image": E})
        check("POST /api/browse/index start: accepted, the image is queued or running", code == 200 and j["images"][sha_e]["state"] in ("queued", "indexing", "done"))
        end = time.time() + 60
        while time.time() < end and req("/api/browse/index")[1]["images"][sha_e]["state"] != "done":
            time.sleep(0.3)
        code, j = req("/api/browse/search?q=img&kind=photos")
        check("... and it is searchable when done, through gatbox-web", code == 200 and j["total"] == 1 and j["results"][0]["path"] == "Photos/2019/IMG_0001.jpg")
        code, loc = req(f"/api/browse/locate?id={j['results'][0]['id']}")
        check("GET /api/browse/locate", code == 200 and [x["name"] for x in loc["dirs"]] == ["Photos", "2019"])
        code, ls = req(f"/api/browse/ls?image={E}&vol={loc['vol']}&dir={loc['dirs'][-1]['inode']}&at={loc['inode']}")
        check("GET /api/browse/ls?at=: the folder's page with the file", code == 200 and loc["inode"] in [e["inode"] for e in ls["entries"]] and ls["at"] == loc["inode"])
        end = time.time() + 60
        while time.time() < end and (req("/api/browse/index")[1]["images"][sha_e].get("text") or {}).get("state") != "done":
            time.sleep(0.3)
        code, j = req("/api/browse/search?q=cabinet&in=text")
        check("the text of the documents was read after the names, and a word inside one finds it with a snippet, through gatbox-web",
              code == 200 and j["total"] == 1 and j["results"][0]["path"] == "Docs/notes.txt" and "\x01cabinet\x02" in j["results"][0]["snippet"]
              and j["coverage"]["text"]["read"] >= 1)
        check("search in=evil -> 400", req("/api/browse/search?q=cabinet&in=evil")[0] == 400)
        code, j = req("/api/browse/index", "POST", {"action": "text_remove", "image": E})
        check("POST text_remove: the text is gone, the names stay", code == 200 and j["images"][sha_e]["text"]["state"] == "none"
              and req("/api/browse/search?q=cabinet&in=text")[1]["total"] == 0 and req("/api/browse/search?q=notes")[1]["total"] >= 1)
        code, j = req("/api/browse/index", "POST", {"action": "text", "image": E})
        end = time.time() + 60
        while time.time() < end and req("/api/browse/index")[1]["images"][sha_e]["text"]["state"] != "done":
            time.sleep(0.3)
        check("POST text: it is read again", code == 200 and req("/api/browse/search?q=cabinet&in=text")[1]["total"] == 1)
        check("a bad action, a body that is not JSON, and a route that is not the index's -> 400, 400, 405",
              req("/api/browse/index", "POST", {"action": "format"})[0] == 400
              and req("/api/browse/index", "POST", b"not json")[0] == 400
              and req("/api/browse/volumes", "POST", {"image": E})[0] in (404, 405))
        check("an unknown parameter and a missing one are dropped, a long query is not a crash", req("/api/browse/search?q=img&evil=1")[0] == 200
              and req("/api/browse/search")[0] == 400 and req("/api/browse/search?q=" + "a" * 5000)[0] == 200)
        lan = next((a for a in _lan_addrs() if "." in a and not a.startswith("127.")), None)
        if lan:
            ph = f"http://{lan}:{port}"
            res = [req("/api/browse/search?q=img", base=ph)[0], req("/api/browse/locate?id=1", base=ph)[0], req("/api/browse/index", base=ph)[0],
                   req("/api/browse/index", "POST", {"action": "all"}, base=ph)[0]]
            check("from a phone (the LAN address): search, locate, index state and index control are all 403", res == [403, 403, 403, 403])
            check("... and a phone cannot read text or start reading it either", req("/api/browse/search?q=cabinet&in=text", base=ph)[0] == 403
                  and req("/api/browse/index", "POST", {"action": "text_remove", "image": E}, base=ph)[0] == 403
                  and req("/api/browse/search?q=cabinet&in=text")[1]["total"] == 1)
            check("... and the phone's POST changed nothing", req("/api/browse/index")[1]["images"][sha_e]["state"] == "done")
        else:
            print("  skip  the phone's 403 (no LAN address here)")
        d.terminate()
        d.wait(10)
        os.remove(sock) if os.path.exists(sock) else None
        code, j = req("/api/browse/search?q=img")
        check("with the service gone: 503, 'isn't installed yet'", code == 503 and "isn't installed" in j["error"])
        check("server log: no errors", "Traceback" not in open(f"{w}/web.log").read())
    finally:
        for pr in (d, web):
            if pr.poll() is None:
                pr.terminate()
                pr.wait(10)
    print(f"\ndrive browser F2: {passed} passed, {failed} failed")
    return 1 if failed else 0


try:
    rc = main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(rc)
