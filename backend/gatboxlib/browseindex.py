"""The drive browser's name index (F2 of docs/plans/2026-11-15-drive-browser.md): find a file by the words in its path, across
every image that has been indexed.

One SQLite file with an FTS5 table, in the browser's own folder on the image store (<store>/_browse/browse.sqlite). It is
disposable: every row is derived from an image, an image never changes, and the index is keyed by the image's SHA-1, so
deleting the file loses nothing of record (the image and its sidecar are the record). It is built by reading each volume's
whole listing once (fls -r), the same pass F0 timed: about 6,000 entries a second and 76 MB of index per million files.

How it behaves:
- A volume is indexed in one pass and becomes searchable only when the pass is complete, so a search never shows half a
  drive, and a stop or a crash leaves nothing behind (the next start deletes the rows of a pass that never finished).
- The work is a queue (table `wanted`), one volume at a time, and it stands still while a disk job is queued or running:
  imaging has priority, and it is the same rule the volume counts follow.
- The answer to a search says how many of the store's images have been indexed, so an empty result is never mistaken for
  "not there".
- Deleted entries and links are not indexed (hidden in v1, as in the folder view).

Tests: GATBOX_BROWSE_DIR (where the index lives), and the limits below are module variables.
"""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time

from . import browse as B
from . import disks as dl

Err = B.Err
DB_NAME = "browse.sqlite"
VERSION = 1                                             # the schema: a different one is rebuilt from the images, not migrated
BATCH = 2000                                            # rows per transaction
INDEX_CAP = 10_000_000                                  # entries in one volume; more is refused, and said
DIR_CAP = 500_000                                       # folders remembered to give a file its parent (memory, about 100 bytes each)
RESULTS = 50                                            # a page of results
COUNT_CAP = 10_000                                      # the total is counted up to here ("10,000+")
FACET_CAP = 100_000                                     # rows looked at for the year chips
PASS_TIMEOUT = 6 * 3600
KINDS = {"all": None, "photos": "photo", "documents": "document", "other": "other", "folders": "folder"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS wanted(sha1 TEXT PRIMARY KEY, rel TEXT NOT NULL, label TEXT, added REAL NOT NULL);
CREATE TABLE IF NOT EXISTS volumes(
  id INTEGER PRIMARY KEY, sha1 TEXT NOT NULL, rel TEXT, label TEXT, start INTEGER NOT NULL, fs TEXT, vlabel TEXT,
  state TEXT NOT NULL, entries INTEGER NOT NULL DEFAULT 0, files INTEGER NOT NULL DEFAULT 0, error TEXT, note TEXT,
  started REAL, finished REAL, UNIQUE(sha1, start));
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, vol INTEGER NOT NULL, parent INTEGER, inode TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL, size INTEGER NOT NULL, mtime INTEGER);
CREATE INDEX IF NOT EXISTS files_vol ON files(vol);
CREATE VIRTUAL TABLE IF NOT EXISTS names USING fts5(path, tokenize='unicode61 remove_diacritics 2', prefix='2 3');
"""


def db_path():
    return os.path.join(B.BROWSE_DIR, DB_NAME)


def connect(create=True):
    """A connection for this thread. None when there is no index and none is wanted. The index is made or rebuilt here:
    a file from another schema version is deleted, because everything in it can be derived again."""
    path = db_path()
    if not os.path.exists(path) and not create:
        return None
    try:
        os.makedirs(B.BROWSE_DIR, exist_ok=True)
        c = sqlite3.connect(path, timeout=30)
        if c.execute("PRAGMA user_version").fetchone()[0] not in (0, VERSION):
            c.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)
            c = sqlite3.connect(path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        c.execute(f"PRAGMA user_version={VERSION}")
        c.commit()
    except (OSError, sqlite3.Error):
        raise Err(503, "the browser's folder on the image store isn't there: plug the store in and restart the browser") from None
    return c


# --- indexing ---------------------------------------------------------------------------------------------------------------

class _Cancelled(Exception):
    pass


class Indexer:
    """The queue and the one thread that works through it. One per process (IDX)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.live = None                                # the volume being indexed now: {sha1, start, entries, state}
        self.cancel = {}                                # sha1 -> "stop" or "remove": a pass to abandon
        self.reason = None                              # why it is standing still, for the screen

    # -- what the screen asks and tells it -------------------------------------------------------------------------------------

    def busy(self):
        with self.lock:
            return self.thread is not None and self.thread.is_alive()

    def kick(self):
        """Start the worker if there is work and it is not running (also after a restart: the queue is in the database).
        Looking at the index starts nothing and creates nothing when there is no work."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
        c = connect(create=False)
        if c is None:
            return
        try:
            work = c.execute("SELECT (SELECT count(*) FROM wanted) + (SELECT count(*) FROM volumes WHERE state IN ('pending','indexing'))").fetchone()[0]
        finally:
            c.close()
        if not work:
            return
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True, name="indexer")
                self.thread.start()

    def request(self, action, rel=None):
        """start / remove / stop an image, or all / stop_all. Returns the new state."""
        if action not in ("start", "stop", "remove", "all", "stop_all"):
            raise Err(400, "action is start, stop, remove, all or stop_all")
        c = connect()
        try:
            if action in ("all", "stop_all"):
                imgs = [i for i in dl.images() if i.get("sha1")] if action == "all" else []
                if action == "all":
                    for i in imgs:
                        self._want(c, i["image"], i)
                else:
                    c.execute("DELETE FROM wanted")
                    c.execute("DELETE FROM volumes WHERE state='pending'")
                    c.commit()
                    with self.lock:
                        if self.live:
                            self.cancel[self.live["sha1"]] = "stop"
            else:
                path, side = B.image(rel)
                sha1 = side.get("sha1")
                if not sha1:
                    raise Err(409, "this image has no checksum in its sidecar, so its index could not be kept")
                if action == "start":
                    self._want(c, rel, side)
                elif action == "stop":
                    c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                    c.execute("DELETE FROM volumes WHERE sha1=? AND state='pending'", (sha1,))
                    c.commit()
                    with self.lock:
                        if self.live and self.live["sha1"] == sha1:
                            self.cancel[sha1] = "stop"
                elif action == "remove":
                    c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                    c.commit()
                    with self.lock:
                        running = bool(self.live and self.live["sha1"] == sha1)
                        if running:
                            self.cancel[sha1] = "remove"
                    if not running:
                        _drop_image(c, sha1)
                else:
                    raise Err(400, "action is start, stop, remove, all or stop_all")
        finally:
            c.close()
        self.kick()
        return state()

    def _want(self, c, rel, side):
        sha1 = side.get("sha1")
        if not sha1:
            return
        c.execute("DELETE FROM volumes WHERE sha1=? AND state='error'", (sha1,))     # a second try starts those again
        c.execute("INSERT OR IGNORE INTO wanted(sha1, rel, label, added) VALUES(?,?,?,?)",
                  (sha1, rel, side.get("label") or os.path.basename(rel), time.time()))
        c.commit()

    # -- the worker ------------------------------------------------------------------------------------------------------------

    def _loop(self):
        c = connect()
        try:
            _recover(c)
            while True:
                job = self._next(c)
                if job is None:
                    return
                vol_id, sha1, rel, start = job
                try:
                    self._index(c, vol_id, sha1, rel, start)
                except _Cancelled:
                    what = self.cancel.pop(sha1, "stop")
                    if what == "remove":
                        _drop_image(c, sha1)
                    else:                               # stop: this volume's partial rows go, finished volumes stay
                        _drop_rows(c, vol_id)
                        c.execute("DELETE FROM volumes WHERE id=? OR (sha1=? AND state='pending')", (vol_id, sha1))
                        c.commit()
                except Err as e:
                    _fail(c, vol_id, e.msg)
                except Exception as e:                  # a pass that dies says so and is not retried on its own
                    _fail(c, vol_id, f"the index pass stopped ({type(e).__name__})")
                finally:
                    with self.lock:
                        self.live = None
        finally:
            c.close()

    def _next(self, c):
        """The next volume to index, or None when the queue is empty or cannot go on (store unplugged)."""
        while True:
            row = c.execute("SELECT sha1, rel, label FROM wanted ORDER BY added LIMIT 1").fetchone()
            if row is None:
                self.reason = None
                return None
            sha1, rel, label = row
            if not dl.store_mounted():
                self.reason = "the image store is not connected"
                return None
            try:
                path, side = B.image(rel)
            except Err:
                c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                c.commit()
                continue
            if side.get("sha1") != sha1:                # a different image has taken the name: the queued one is gone
                c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                c.commit()
                continue
            try:
                vols = B.volumes(rel)["volumes"]
            except Err as e:
                c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                c.execute("INSERT OR REPLACE INTO volumes(sha1, rel, label, start, state, error) VALUES(?,?,?,-1,'error',?)",
                          (sha1, rel, label, e.msg))
                c.commit()
                continue
            if not any(v["fs"] for v in vols):
                c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                c.execute("INSERT OR REPLACE INTO volumes(sha1, rel, label, start, state, error) VALUES(?,?,?,-1,'error',?)",
                          (sha1, rel, label, "no filesystem found in this image: nothing to index"))
                c.commit()
                continue
            for v in vols:
                if not v["fs"]:
                    continue
                fs = v["fs"]
                c.execute("INSERT OR IGNORE INTO volumes(sha1, rel, label, start, fs, vlabel, state) VALUES(?,?,?,?,?,?,'pending')",
                          (sha1, rel, label, v["start"], fs["type"], fs.get("label") or ""))
            c.commit()
            nxt = c.execute("SELECT id, start FROM volumes WHERE sha1=? AND state='pending' ORDER BY start LIMIT 1", (sha1,)).fetchone()
            if nxt is None:                             # nothing (left) to index in this image: it is done with
                c.execute("DELETE FROM wanted WHERE sha1=?", (sha1,))
                c.commit()
                continue
            return nxt[0], sha1, rel, nxt[1]

    def _index(self, c, vol_id, sha1, rel, start):
        path, side = B.image(rel)
        with self.lock:
            self.live = {"sha1": sha1, "start": start, "entries": 0, "state": "indexing"}
            live = self.live
        _drop_rows(c, vol_id)
        c.execute("UPDATE volumes SET state='indexing', entries=0, files=0, error=NULL, started=? WHERE id=?", (time.time(), vol_id))
        c.commit()
        nid = (c.execute("SELECT COALESCE(MAX(id), 0) FROM files").fetchone()[0] or 0) + 1
        dirs, files_rows, name_rows = {}, [], []
        entries = files = 0
        noted = []
        argv = ["fls", "-r", "-p", "-m", "/", "-o", str(start), path]
        with B._bg, B._sem:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=B.env(), start_new_session=True)
            killed = []
            timer = threading.Timer(PASS_TIMEOUT, lambda: (killed.append(1), B.kill_group(p)))
            timer.start()
            try:
                for raw in p.stdout:
                    while B.disk_busy() or not dl.store_mounted():          # imaging goes first: stand still until it is done
                        live["state"] = "waiting"
                        self.reason = "a disk job is running" if B.disk_busy() else "the image store is not connected"
                        if sha1 in self.cancel:
                            break
                        time.sleep(1)
                    live["state"], self.reason = "indexing", None
                    if sha1 in self.cancel:
                        B.kill_group(p)
                        raise _Cancelled()
                    got = B.parse_body(raw.decode("utf-8", "replace").rstrip("\n"))
                    if got is None:
                        continue
                    path_text, inode, mode, size, mtime = got
                    t = mode[:1]
                    if path_text.endswith(" (deleted)") or t not in ("d", "r") or path_text.endswith("(Volume Label Entry)"):
                        continue
                    entries += 1
                    live["entries"] = entries
                    if entries > INDEX_CAP:
                        B.kill_group(p)
                        raise Err(413, f"more than {INDEX_CAP:,} entries in one volume: not indexed")
                    leaf = path_text.rsplit("/", 1)[-1]
                    parent = dirs.get(path_text.rsplit("/", 1)[0]) if "/" in path_text else None
                    if t == "d":
                        if len(dirs) < DIR_CAP:
                            dirs[path_text] = nid
                        elif not noted:
                            noted.append("a very large number of folders: files below the first "
                                         f"{DIR_CAP:,} are shown from the top of the volume")
                    else:
                        files += 1
                    files_rows.append((nid, vol_id, parent, inode, leaf, B.kind_of(leaf, t == "d"), size,
                                       mtime if 631152000 <= mtime <= 4102444800 else None))
                    name_rows.append((nid, path_text))
                    nid += 1
                    if len(files_rows) >= BATCH:
                        _flush(c, files_rows, name_rows, vol_id, entries, files)
            finally:
                timer.cancel()
                p.stdout.close()
                rc = p.wait()
            if killed:
                raise Err(504, "listing the volume took too long and was stopped")
        _flush(c, files_rows, name_rows, vol_id, entries, files)
        if rc != 0 and entries == 0:
            raise Err(415, "this volume could not be listed")
        c.execute("UPDATE volumes SET state='done', entries=?, files=?, finished=?, note=? WHERE id=?",
                  (entries, files, time.time(), "; ".join(noted) or None, vol_id))
        c.commit()


def _flush(c, files_rows, name_rows, vol_id, entries, files):
    if files_rows:
        c.executemany("INSERT INTO files(id, vol, parent, inode, name, kind, size, mtime) VALUES(?,?,?,?,?,?,?,?)", files_rows)
        c.executemany("INSERT INTO names(rowid, path) VALUES(?,?)", name_rows)
        del files_rows[:], name_rows[:]
    c.execute("UPDATE volumes SET entries=?, files=? WHERE id=?", (entries, files, vol_id))
    c.commit()


def _drop_rows(c, vol_id):
    c.execute("DELETE FROM names WHERE rowid IN (SELECT id FROM files WHERE vol=?)", (vol_id,))
    c.execute("DELETE FROM files WHERE vol=?", (vol_id,))
    c.commit()


def _drop_image(c, sha1):
    for (vid,) in c.execute("SELECT id FROM volumes WHERE sha1=?", (sha1,)).fetchall():
        _drop_rows(c, vid)
    c.execute("DELETE FROM volumes WHERE sha1=?", (sha1,))
    c.commit()


def _fail(c, vol_id, msg):
    _drop_rows(c, vol_id)
    c.execute("UPDATE volumes SET state='error', error=?, entries=0, files=0 WHERE id=?", (msg, vol_id))
    c.commit()


def _recover(c):
    """A pass that was running when the browser stopped never finished: its rows are removed, and it goes back in the queue
    (unless it was being cancelled, which `remove` finishes)."""
    for vid, sha1, rel, label in c.execute("SELECT id, sha1, rel, label FROM volumes WHERE state='indexing'").fetchall():
        _drop_rows(c, vid)
        c.execute("UPDATE volumes SET state='pending', entries=0, files=0 WHERE id=?", (vid,))
        c.execute("INSERT OR IGNORE INTO wanted(sha1, rel, label, added) VALUES(?,?,?,?)", (sha1, rel, label, time.time()))
    c.commit()


IDX = Indexer()


# --- state ------------------------------------------------------------------------------------------------------------------

def state():
    """The index as the screen shows it: per image (by SHA-1) a state, and the worker's own."""
    c = connect(create=False)
    imgs, wanted = {}, set()
    if c is not None:
        try:
            wanted = {r[0] for r in c.execute("SELECT sha1 FROM wanted")}
            for sha1, rel, start, fs, vlabel, st, entries, files, error, note, finished in c.execute(
                    "SELECT sha1, rel, start, fs, vlabel, state, entries, files, error, note, finished FROM volumes ORDER BY sha1, start"):
                im = imgs.setdefault(sha1, {"volumes": [], "entries": 0, "files": 0, "finished": None})
                live = IDX.live
                if live and live["sha1"] == sha1 and live["start"] == start and st == "indexing":
                    entries = live["entries"]
                    st = live["state"]
                im["volumes"].append({"start": start, "fs": fs, "label": vlabel, "state": st, "entries": entries, "files": files,
                                      "error": error, "note": note})
                im["entries"] += entries or 0
                im["files"] += files or 0
                if finished:
                    im["finished"] = max(im["finished"] or 0, finished)
            for sha1 in wanted:
                imgs.setdefault(sha1, {"volumes": [], "entries": 0, "files": 0, "finished": None})
        finally:
            c.close()
    for sha1, im in imgs.items():
        sts = [v["state"] for v in im["volumes"]]
        if "waiting" in sts:
            im["state"] = "waiting"
        elif "indexing" in sts:
            im["state"] = "indexing"
        elif "pending" in sts or (not sts and sha1 in wanted):
            im["state"] = "queued"
        elif sts and all(s == "error" for s in sts):
            im["state"] = "error"
            im["error"] = next((v["error"] for v in im["volumes"] if v["error"]), None)
        elif "error" in sts:
            im["state"] = "partial"
        else:
            im["state"] = "done"
    return {"images": imgs, "working": IDX.busy(), "reason": IDX.reason}


# --- searching ---------------------------------------------------------------------------------------------------------------

def _rel_of(sha1, hint, cache):
    """Where an indexed image is in the store now: its old place if it is still there, else wherever its SHA-1 is, else None."""
    if sha1 in cache:
        return cache[sha1]
    side = dl.sidecar(hint) if hint else None
    rel = hint if side and side.get("sha1") == sha1 else next((i["image"] for i in dl.images() if i.get("sha1") == sha1), None)
    cache[sha1] = rel
    return rel


def search(q, kind="all", year=None, size_min=None, size_max=None, page=0):
    """Files whose path has these words (each a prefix, all of them required), best first, 50 to a page."""
    words = [w for w in re.findall(r"\w+", q or "", re.UNICODE) if w.strip("_")][:8]
    if not words:
        raise Err(400, "type a word to look for")
    if kind not in KINDS:
        raise Err(400, "kind is all, photos, documents, other or folders")
    page = max(0, B._num(page, 0))
    coverage = _coverage()
    c = connect(create=False)
    empty = {"query": q, "results": [], "total": 0, "capped": False, "page": 0, "pages": 1, "years": [], "coverage": coverage}
    if c is None:
        return empty
    expr = " ".join(f'"{w}"*' for w in words)
    where, args = ["names MATCH ?", "v.state='done'"], [expr]
    if KINDS[kind]:
        where.append("f.kind=?")
        args.append(KINDS[kind])
    lo, hi = B._num(size_min), B._num(size_max)
    if lo is not None:
        where.append("f.size>=?")
        args.append(lo)
    if hi is not None:
        where.append("f.size<=?")
        args.append(hi)
    base = " FROM names JOIN files f ON f.id=names.rowid JOIN volumes v ON v.id=f.vol WHERE "
    try:
        facet = c.execute("SELECT y, count(*) FROM (SELECT strftime('%Y', f.mtime, 'unixepoch') AS y" + base + " AND ".join(where)
                          + f" AND f.mtime IS NOT NULL LIMIT {FACET_CAP}) GROUP BY y ORDER BY y DESC LIMIT 12", args).fetchall()
        y = B._num(year)
        if y is not None:
            where.append("strftime('%Y', f.mtime, 'unixepoch')=?")
            args.append(f"{y:04d}")
        n = c.execute("SELECT count(*) FROM (SELECT 1" + base + " AND ".join(where) + f" LIMIT {COUNT_CAP + 1})", args).fetchone()[0]
        pages = max(1, -(-min(n, COUNT_CAP) // RESULTS))
        page = min(page, pages - 1)
        rows = c.execute("SELECT f.id, f.inode, f.name, f.kind, f.size, f.mtime, names.path, v.sha1, v.rel, v.label, v.start, v.fs, v.vlabel"
                         + base + " AND ".join(where) + " ORDER BY names.rank, f.id LIMIT ? OFFSET ?", args + [RESULTS, page * RESULTS]).fetchall()
    except sqlite3.OperationalError:                    # a query FTS cannot read
        return empty
    finally:
        c.close()
    cache, out = {}, []
    for fid, inode, name, k, size, mtime, path, sha1, hint, label, start, fs, vlabel in rows:
        rel = _rel_of(sha1, hint, cache)
        out.append({"id": fid, "inode": inode, "name": name, "kind": k, "size": size, "mtime": mtime, "path": path,
                    "folder": path.rsplit("/", 1)[0] if "/" in path else "", "image": rel, "label": label, "vol": start,
                    "volume": fs + (f" '{vlabel}'" if vlabel else "")})
    return {"query": q, "results": out, "total": min(n, COUNT_CAP), "capped": n > COUNT_CAP, "page": page, "pages": pages,
            "years": [[int(y), n] for y, n in facet if y], "coverage": coverage}


def _coverage():
    """How much of the store has been searched: images in the store, and how many have at least one indexed volume."""
    imgs = {i["sha1"] for i in dl.images() if i.get("sha1")}
    c = connect(create=False)
    done = set()
    working = 0
    if c is not None:
        try:
            done = {r[0] for r in c.execute("SELECT DISTINCT sha1 FROM volumes WHERE state='done'")} & imgs
            working = c.execute("SELECT count(DISTINCT sha1) FROM volumes WHERE state IN ('pending','indexing')").fetchone()[0]
        finally:
            c.close()
    return {"images": len(imgs), "indexed": len(done), "indexing": working}


def locate(fid):
    """Where a search result is: its image, volume and the folders above it (root first, each with its inode), so the folder
    view can open it. A file whose folders were not remembered (the folder cap) is shown from the top of the volume."""
    c = connect(create=False)
    if c is None:
        raise Err(404, "nothing is indexed")
    fid = B._num(fid)
    try:
        row = c.execute("SELECT f.id, f.parent, f.inode, f.name, f.kind, v.sha1, v.rel, v.label, v.start, v.fs, v.vlabel "
                        "FROM files f JOIN volumes v ON v.id=f.vol WHERE f.id=? AND v.state='done'", (fid,)).fetchone() if fid is not None else None
        if row is None:
            raise Err(404, "that result is no longer in the index")
        _, parent, inode, name, kind, sha1, hint, label, start, fs, vlabel = row
        chain, seen = [], set()
        while parent is not None and parent not in seen and len(chain) < 64:
            seen.add(parent)
            r = c.execute("SELECT parent, inode, name FROM files WHERE id=?", (parent,)).fetchone()
            if r is None:
                break
            chain.append({"name": r[2], "inode": r[1]})
            parent = r[0]
    finally:
        c.close()
    rel = _rel_of(sha1, hint, {})
    if rel is None:
        raise Err(404, "the image this result is in is no longer in the store")
    return {"image": rel, "label": label, "vol": start, "volume": fs + (f" '{vlabel}'" if vlabel else ""),
            "dirs": list(reversed(chain)), "inode": inode, "name": name, "kind": kind}
