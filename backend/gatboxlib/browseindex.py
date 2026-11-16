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
- Text (F3b) is a second phase, after the names: each document a reader exists for is read out of the image with icat into a
  temporary file, its text is taken (browsetext.py) and put in a second FTS5 table, one file at a time, in inode order, with a
  commit every few files, so a stop, a crash or a disk job costs nothing: the files already read stay searchable, and the next
  pass starts after them. A file that cannot be read is recorded with the reason (encrypted, damaged, no text, ...), and the
  search says how many documents it has read and how many it could not.

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
from . import browsetext as TX
from . import disks as dl

Err = B.Err
DB_NAME = "browse.sqlite"
VERSION = 2                                             # the schema: a different one is rebuilt from the images, not migrated
BATCH = 2000                                            # rows per transaction
INDEX_CAP = 10_000_000                                  # entries in one volume; more is refused, and said
DIR_CAP = 500_000                                       # folders remembered to give a file its parent (memory, about 100 bytes each)
RESULTS = 50                                            # a page of results
COUNT_CAP = 10_000                                      # the total is counted up to here ("10,000+")
FACET_CAP = 100_000                                     # rows looked at for the year chips
PASS_TIMEOUT = 6 * 3600
TEXT_PAGE = 40                                          # documents picked from the list at a time (each is committed as it is read)
TEXT_ICAT_TIMEOUT = 120                                 # seconds to read one document out of the image
TEXT_FILE_S = 0.025                                     # the measured cost of one small document: 20 ms to read, 3 to extract
TEXT_RATE = 5 * (1 << 20)                               # bytes a second for big documents (F0: 6 MB/s, a little under it)
AUTO_TEXT_S = 1800                                      # text is read on its own after the names when this is the estimate or less
DUP_MIN = 4096                                          # a smaller file is mostly the same icon or the same empty setting: not offered
DUP_GROUPS = 1000                                       # groups looked at (the most space first); the screen says when there are more
DUP_PAGE = 20                                           # groups on a screen page
DUP_MEMBERS = 12                                        # places listed for one group; the rest are counted
DUP_SECONDS = 30                                        # a comparison that takes longer is stopped, and says so
DUP_TICK = 20000                                        # database steps between looks at the clock
ACTIONS = ("start", "stop", "remove", "all", "stop_all", "text", "text_stop", "text_remove", "text_all", "text_stop_all")
KINDS = {"all": None, "photos": "photo", "documents": "document", "other": "other", "folders": "folder"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS wanted(sha1 TEXT PRIMARY KEY, rel TEXT NOT NULL, label TEXT, added REAL NOT NULL);
CREATE TABLE IF NOT EXISTS volumes(
  id INTEGER PRIMARY KEY, sha1 TEXT NOT NULL, rel TEXT, label TEXT, start INTEGER NOT NULL, fs TEXT, vlabel TEXT,
  state TEXT NOT NULL, entries INTEGER NOT NULL DEFAULT 0, files INTEGER NOT NULL DEFAULT 0, error TEXT, note TEXT,
  started REAL, finished REAL, docs INTEGER NOT NULL DEFAULT 0, doc_bytes INTEGER NOT NULL DEFAULT 0,
  tstate TEXT NOT NULL DEFAULT 'none', tread INTEGER NOT NULL DEFAULT 0, tskip INTEGER NOT NULL DEFAULT 0, tcodes TEXT,
  UNIQUE(sha1, start));
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, vol INTEGER NOT NULL, parent INTEGER, inode TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL, size INTEGER NOT NULL, mtime INTEGER, ext TEXT, inum INTEGER, readable INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS files_vol ON files(vol);
CREATE INDEX IF NOT EXISTS files_todo ON files(vol, inum, id) WHERE readable=1;
CREATE TABLE IF NOT EXISTS tx(file_id INTEGER PRIMARY KEY, state TEXT NOT NULL, code TEXT);
CREATE TABLE IF NOT EXISTS wanted_text(sha1 TEXT PRIMARY KEY, rel TEXT NOT NULL, label TEXT, added REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS body USING fts5(text, tokenize='unicode61 remove_diacritics 2', prefix='2 3');
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
        ver = c.execute("PRAGMA user_version").fetchone()[0]
        if ver not in (0, VERSION):
            c.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)
            c = sqlite3.connect(path, timeout=30)
            ver = 0
        if ver == 0:                                    # a new file: made once, so that looking at the index never asks for the write lock
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.execute(f"PRAGMA user_version={VERSION}")
            c.commit()
        c.execute("PRAGMA synchronous=NORMAL")
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
        self.live_text = None                           # the volume whose text is being read: {sha1, start, state, done, total, secs}
        self.tcancel = {}                               # sha1 -> "stop" or "remove": a text pass to abandon
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
            work = c.execute("SELECT (SELECT count(*) FROM wanted) + (SELECT count(*) FROM wanted_text) "
                             "+ (SELECT count(*) FROM volumes WHERE state IN ('pending','indexing') OR tstate IN ('queued','reading'))").fetchone()[0]
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
        if action not in ACTIONS:
            raise Err(400, "action is " + ", ".join(ACTIONS[:-1]) + " or " + ACTIONS[-1])
        c = connect()
        try:
            if action in ("text_all", "text_stop_all"):
                if action == "text_all":
                    for i in [i for i in dl.images() if i.get("sha1")]:
                        self._want_text(c, i["sha1"], strict=False)
                else:
                    self._stop_text(c, None)
            elif action in ("all", "stop_all"):
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
                if action == "text":
                    self._want_text(c, sha1)
                elif action == "text_stop":
                    self._stop_text(c, sha1)
                elif action == "text_remove":
                    self._stop_text(c, sha1, remove=True)
                elif action == "start":
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
        finally:
            c.close()
        self.kick()
        return state()

    def _want_text(self, c, sha1, strict=True):
        """Queue an image's documents for reading. Only volumes whose names are indexed and which hold documents."""
        rows = c.execute("SELECT id, rel, label FROM volumes WHERE sha1=? AND state='done' AND docs>0 AND tstate IN ('none')", (sha1,)).fetchall()
        if not rows:
            if strict and not c.execute("SELECT 1 FROM volumes WHERE sha1=? AND state='done'", (sha1,)).fetchone():
                raise Err(409, "index the names of this image first: its documents are listed there")
            return
        c.execute("UPDATE volumes SET tstate='queued' WHERE sha1=? AND state='done' AND docs>0 AND tstate='none'", (sha1,))
        c.execute("INSERT OR IGNORE INTO wanted_text(sha1, rel, label, added) VALUES(?,?,?,?)", (sha1, rows[0][1], rows[0][2], time.time()))
        c.commit()

    def _stop_text(self, c, sha1, remove=False):
        """Stop reading an image's text (or everything's). What was read stays searchable; remove throws it away."""
        where, args = ("sha1=?", (sha1,)) if sha1 else ("1=1", ())
        c.execute(f"DELETE FROM wanted_text WHERE {where}", args)
        c.execute(f"UPDATE volumes SET tstate='none' WHERE {where} AND tstate='queued'", args)
        c.commit()
        with self.lock:
            live = self.live_text
            running = bool(live and (sha1 is None or live["sha1"] == sha1))
            if running:
                self.tcancel[live["sha1"]] = "remove" if remove else "stop"
        if remove and not running:
            for (vid,) in c.execute(f"SELECT id FROM volumes WHERE {where}", args).fetchall():
                _drop_text(c, vid)

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
                    tjob = self._next_text(c)
                    if tjob is None:
                        return
                    try:
                        self._text(c, *tjob)
                    except _Cancelled:
                        pass
                    except Err as e:
                        c.execute("UPDATE volumes SET tstate='none' WHERE id=?", (tjob[0],))
                        c.execute("DELETE FROM wanted_text WHERE sha1=?", (tjob[1],))
                        c.commit()
                        self.reason = e.msg
                    except Exception:                   # a text pass that dies is stopped, not retried on its own
                        c.execute("UPDATE volumes SET tstate='none' WHERE id=?", (tjob[0],))
                        c.execute("DELETE FROM wanted_text WHERE sha1=?", (tjob[1],))
                        c.commit()
                    finally:
                        with self.lock:
                            self.live_text = None
                    continue
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

    def _next_text(self, c):
        """The next volume whose documents are to be read, or None. Waits for the store: with it unplugged there is nothing to read."""
        while True:
            row = c.execute("SELECT v.id, v.sha1, v.rel, v.start FROM volumes v JOIN wanted_text w ON w.sha1=v.sha1 "
                            "WHERE v.state='done' AND v.tstate IN ('queued','reading') ORDER BY w.added, v.start LIMIT 1").fetchone()
            if row is None:
                c.execute("DELETE FROM wanted_text WHERE sha1 NOT IN (SELECT sha1 FROM volumes WHERE tstate IN ('queued','reading'))")
                c.commit()
                return None
            vid, sha1, rel, start = row
            if not dl.store_mounted():
                self.reason = "the image store is not connected"
                return None
            try:
                path, side = B.image(rel)
                ok = side.get("sha1") == sha1
            except Err:
                ok = False
            if not ok:                                  # the image is gone or replaced: what was read stays, nothing more is read
                c.execute("UPDATE volumes SET tstate='none' WHERE sha1=? AND tstate IN ('queued','reading')", (sha1,))
                c.execute("DELETE FROM wanted_text WHERE sha1=?", (sha1,))
                c.commit()
                continue
            return vid, sha1, rel, start

    def _text(self, c, vid, sha1, rel, start):
        """Read the documents of one volume, in inode order, a few at a time, committing as it goes."""
        path, side = B.image(rel)
        total = c.execute("SELECT docs FROM volumes WHERE id=?", (vid,)).fetchone()[0]
        done_codes = {}
        read = skipped = 0
        for st, code, n in c.execute("SELECT t.state, t.code, count(*) FROM tx t JOIN files f ON f.id=t.file_id WHERE f.vol=? GROUP BY t.state, t.code", (vid,)):
            if st == "read":
                read += n
            else:
                skipped += n
                done_codes[code] = done_codes.get(code, 0) + n
        with self.lock:
            self.live_text = {"sha1": sha1, "start": start, "state": "reading", "read": read, "skipped": skipped, "total": total, "secs": 0.0, "files": 0}
            live = self.live_text
        c.execute("UPDATE volumes SET tstate='reading' WHERE id=?", (vid,))
        c.commit()
        last = (-1, -1)
        stopped = False
        while not stopped:
            rows = c.execute("SELECT f.id, f.inode, f.size, f.ext, f.inum FROM files f WHERE f.vol=? AND f.readable=1 AND (f.inum, f.id) > (?, ?) "
                             "AND NOT EXISTS (SELECT 1 FROM tx WHERE tx.file_id=f.id) ORDER BY f.inum, f.id LIMIT ?",
                             (vid, last[0], last[1], TEXT_PAGE)).fetchall()
            if not rows:
                break
            for fid, inode, size, ext, inum in rows:
                last = (inum, fid)
                while B.disk_busy() or not dl.store_mounted():                # imaging goes first: stand still until it is done
                    live["state"] = "waiting"
                    self.reason = "a disk job is running" if B.disk_busy() else "the image store is not connected"
                    if sha1 in self.tcancel:
                        break
                    time.sleep(1)
                live["state"], self.reason = "reading", None
                if sha1 in self.tcancel:
                    stopped = True
                    break
                if c.execute("SELECT 1 FROM wanted LIMIT 1").fetchone():      # names to index again: they go first, this volume resumes after
                    c.execute("UPDATE volumes SET tstate='queued' WHERE id=?", (vid,))
                    c.commit()
                    return
                t0 = time.time()
                outcome = self._one(path, start, inode, size, ext)
                live["secs"] += time.time() - t0
                live["files"] += 1
                if outcome[0] == "read":
                    c.execute("INSERT INTO body(rowid, text) VALUES(?,?)", (fid, outcome[1]))
                    c.execute("INSERT INTO tx(file_id, state, code) VALUES(?, 'read', ?)", (fid, "cut" if outcome[2] else None))
                    read += 1
                else:
                    c.execute("INSERT INTO tx(file_id, state, code) VALUES(?, 'skip', ?)", (fid, outcome[1]))
                    skipped += 1
                    done_codes[outcome[1]] = done_codes.get(outcome[1], 0) + 1
                live["read"], live["skipped"] = read, skipped
                # Every document is committed as it is read: a document can take seconds, and a transaction held open meanwhile
                # would keep the screen's own requests (stop, remove) waiting for the write lock.
                self._text_commit(c, vid, read, skipped, done_codes)
        self._text_commit(c, vid, read, skipped, done_codes)
        what = self.tcancel.pop(sha1, None)
        if what == "remove":
            _drop_text(c, vid)
            c.execute("UPDATE volumes SET tstate='none' WHERE sha1=?", (sha1,))
        elif what == "stop":
            c.execute("UPDATE volumes SET tstate='none' WHERE sha1=? AND tstate IN ('reading','queued')", (sha1,))
        else:
            c.execute("UPDATE volumes SET tstate='done' WHERE id=?", (vid,))
        c.commit()

    @staticmethod
    def _text_commit(c, vid, read, skipped, codes):
        c.execute("UPDATE volumes SET tread=?, tskip=?, tcodes=? WHERE id=?", (read, skipped, json.dumps(codes), vid))
        c.commit()

    def _one(self, path, start, inode, size, ext):
        """One document: ('read', text, cut) or ('skip', code). Nothing here raises: a file that cannot be read is a code."""
        if size > B.PREVIEW_CAP:
            return "skip", "too big"
        tmp_dir = os.path.join(B.BROWSE_DIR, "tmp")
        tmp = os.path.join(tmp_dir, f"read-{os.getpid()}-{threading.get_ident()}.{ext}")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            with open(tmp, "wb") as f:
                try:
                    with B._bg:
                        rc, n, err = B.run(["icat", "-o", str(start), path, inode], TEXT_ICAT_TIMEOUT, cap=B.PREVIEW_CAP + 1, stdout=f)
                except Err:
                    return "skip", "too slow"
            if n > B.PREVIEW_CAP:
                return "skip", "too big"
            if rc != 0 and n == 0:
                return "skip", "damaged"
            try:
                text, cut = TX.extract(tmp, ext)
            except TX.Unreadable as e:
                return "skip", e.code
            return "read", text, cut
        except Exception:                                # a reader that blows up on a hostile file is a damaged file
            return "skip", "damaged"
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

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
        entries = files = docs = doc_bytes = 0
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
                    if B.gone_name(path_text) or t not in ("d", "r") or path_text.endswith("(Volume Label Entry)"):
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
                        ext, readable = None, 0
                    else:
                        files += 1
                        ext = TX.extension(leaf)
                        readable = 1 if ext in TX.READERS and size > 0 else 0
                        if readable:
                            docs += 1
                            doc_bytes += size
                    inum = B._num(inode.split("-")[0], 0)
                    files_rows.append((nid, vol_id, parent, inode, leaf, B.kind_of(leaf, t == "d"), size,
                                       mtime if 631152000 <= mtime <= 4102444800 else None, ext, inum, readable))
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
        c.execute("UPDATE volumes SET state='done', entries=?, files=?, finished=?, note=?, docs=?, doc_bytes=?, "
                  "tstate='none', tread=0, tskip=0, tcodes=NULL WHERE id=?",
                  (entries, files, time.time(), "; ".join(noted) or None, docs, doc_bytes, vol_id))
        if docs and estimate(docs, doc_bytes) <= AUTO_TEXT_S:         # a short job is done on its own; a long one waits for a tap
            c.execute("UPDATE volumes SET tstate='queued' WHERE id=?", (vol_id,))
            c.execute("INSERT OR IGNORE INTO wanted_text(sha1, rel, label, added) SELECT sha1, rel, label, ? FROM volumes WHERE id=?", (time.time(), vol_id))
        c.commit()


def _flush(c, files_rows, name_rows, vol_id, entries, files):
    if files_rows:
        c.executemany("INSERT INTO files(id, vol, parent, inode, name, kind, size, mtime, ext, inum, readable) VALUES(?,?,?,?,?,?,?,?,?,?,?)", files_rows)
        c.executemany("INSERT INTO names(rowid, path) VALUES(?,?)", name_rows)
        del files_rows[:], name_rows[:]
    c.execute("UPDATE volumes SET entries=?, files=? WHERE id=?", (entries, files, vol_id))
    c.commit()


def estimate(docs, nbytes):
    """Seconds to read this many documents out of an image: a small document costs about 25 ms, a big one is bytes at about 5 MB/s
    (docs/plans/2026-11-15-drive-browser.md §3, F3's report). Shown with its basis; the running average replaces it once reading."""
    return docs * TEXT_FILE_S + nbytes / TEXT_RATE


def _drop_text(c, vol_id):
    """Forget a volume's text and what was learned about each document, and let it be read again."""
    c.execute("DELETE FROM body WHERE rowid IN (SELECT id FROM files WHERE vol=?)", (vol_id,))
    c.execute("DELETE FROM tx WHERE file_id IN (SELECT id FROM files WHERE vol=?)", (vol_id,))
    c.execute("UPDATE volumes SET tstate='none', tread=0, tskip=0, tcodes=NULL WHERE id=?", (vol_id,))
    c.commit()


def _drop_rows(c, vol_id):
    c.execute("DELETE FROM body WHERE rowid IN (SELECT id FROM files WHERE vol=?)", (vol_id,))
    c.execute("DELETE FROM tx WHERE file_id IN (SELECT id FROM files WHERE vol=?)", (vol_id,))
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
    for vid, sha1, rel, label in c.execute("SELECT id, sha1, rel, label FROM volumes WHERE tstate='reading'").fetchall():
        c.execute("UPDATE volumes SET tstate='queued' WHERE id=?", (vid,))       # what was read stays; the rest is read next
        c.execute("INSERT OR IGNORE INTO wanted_text(sha1, rel, label, added) VALUES(?,?,?,?)", (sha1, rel, label, time.time()))
    c.commit()
    tmp = os.path.join(B.BROWSE_DIR, "tmp")
    if os.path.isdir(tmp):
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass


IDX = Indexer()


# --- state ------------------------------------------------------------------------------------------------------------------

def state():
    """The index as the screen shows it: per image (by SHA-1) a state, and the worker's own."""
    c = connect(create=False)
    imgs, wanted = {}, set()
    if c is not None:
        try:
            wanted = {r[0] for r in c.execute("SELECT sha1 FROM wanted")}
            for sha1, rel, start, fs, vlabel, st, entries, files, error, note, finished, docs, doc_bytes, tstate, tread, tskip, tcodes in c.execute(
                    "SELECT sha1, rel, start, fs, vlabel, state, entries, files, error, note, finished, docs, doc_bytes, tstate, tread, tskip, tcodes "
                    "FROM volumes ORDER BY sha1, start"):
                im = imgs.setdefault(sha1, {"volumes": [], "entries": 0, "files": 0, "finished": None})
                tl = IDX.live_text
                tlive = tl if tl and tl["sha1"] == sha1 and tl["start"] == start else None
                if tlive and tstate == "reading":                 # the live counts run ahead of the last commit
                    tstate = "waiting" if tlive["state"] == "waiting" else "reading"
                    tread, tskip = tlive["read"], tlive["skipped"]
                    im["text_avg"] = (tlive["secs"] / tlive["files"]) if tlive["files"] else None
                t = im.setdefault("text", {"eligible": 0, "bytes": 0, "read": 0, "skipped": 0, "codes": {}, "states": []})
                if st == "done":
                    t["eligible"] += docs or 0
                    t["bytes"] += doc_bytes or 0
                    t["read"] += tread or 0
                    t["skipped"] += tskip or 0
                    for code, n in (json.loads(tcodes) if tcodes else {}).items():
                        t["codes"][code] = t["codes"].get(code, 0) + n
                    if docs:
                        t["states"].append(tstate)
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
        t = im.get("text")
        if t is not None:
            tsts = t.pop("states")
            if "waiting" in tsts:
                t["state"] = "waiting"
            elif "reading" in tsts:
                t["state"] = "reading"
            elif "queued" in tsts:
                t["state"] = "queued"
            elif not t["eligible"]:
                t["state"] = "nodocs" if im["state"] == "done" else "none"
            elif t["read"] + t["skipped"] >= t["eligible"]:
                t["state"] = "done"
            elif t["read"] + t["skipped"] > 0:
                t["state"] = "partial"
            else:
                t["state"] = "none"
            avg = im.pop("text_avg", None)
            if t["state"] in ("reading", "waiting"):             # once it is running the estimate is the running average
                left = t["eligible"] - t["read"] - t["skipped"]
                t["seconds_left"] = round(avg * max(left, 0)) if avg else None
            t["estimate_s"] = round(estimate(t["eligible"], t["bytes"])) if t["eligible"] else 0
            t["unread"] = max(0, t["eligible"] - t["read"] - t["skipped"])
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


def search(q, kind="all", year=None, size_min=None, size_max=None, page=0, scope="names"):
    """Files whose path has these words (each a prefix, all of them required), best first, 50 to a page. With scope "text":
    documents whose text has them, each with a snippet around the match (the match between \\x01 and \\x02)."""
    if scope not in ("names", "text"):
        raise Err(400, "in is names or text")
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
    ft = "body" if scope == "text" else "names"
    where, args = [f"{ft} MATCH ?", "v.state='done'"], [expr]
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
    base = (f" FROM {ft} JOIN files f ON f.id={ft}.rowid JOIN volumes v ON v.id=f.vol" + (" JOIN names ON names.rowid=f.id" if scope == "text" else "")
            + " WHERE ")
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
        snip = ", snippet(body, 0, char(1), char(2), ' … ', 20)" if scope == "text" else ", NULL"
        rows = c.execute("SELECT f.id, f.inode, f.name, f.kind, f.size, f.mtime, names.path, v.sha1, v.rel, v.label, v.start, v.fs, v.vlabel" + snip
                         + base + " AND ".join(where) + f" ORDER BY {ft}.rank, f.id LIMIT ? OFFSET ?", args + [RESULTS, page * RESULTS]).fetchall()
    except sqlite3.OperationalError:                    # a query FTS cannot read
        return empty
    finally:
        c.close()
    cache, out = {}, []
    for fid, inode, name, k, size, mtime, path, sha1, hint, label, start, fs, vlabel, snippet in rows:
        rel = _rel_of(sha1, hint, cache)
        item = {"id": fid, "inode": inode, "name": name, "kind": k, "size": size, "mtime": mtime, "path": path,
                "folder": path.rsplit("/", 1)[0] if "/" in path else "", "image": rel, "label": label, "vol": start,
                "volume": fs + (f" '{vlabel}'" if vlabel else "")}
        if snippet is not None:
            item["snippet"] = snippet
        out.append(item)
    return {"query": q, "results": out, "total": min(n, COUNT_CAP), "capped": n > COUNT_CAP, "page": page, "pages": pages,
            "years": [[int(y), n] for y, n in facet if y], "coverage": coverage}


_dup_lock = threading.Lock()


def duplicates(size_min=None, kind="all", page=0):
    """Files that may be the same file: the same name (capitals ignored) and exactly the same size, among every fully indexed volume
    of every image in the store, the groups wasting the most space first. NOTHING IS READ from an image and nothing is hashed, so
    these are possible duplicates only: two different files can share a name and a size, and a copy under another name is not found.
    Files under DUP_MIN bytes are left out, and so are folders."""
    if kind not in KINDS or kind == "folders":
        raise Err(400, "kind is all, photos, documents or other")
    lo = max(DUP_MIN, B._num(size_min, DUP_MIN))
    page = max(0, B._num(page, 0))
    if B.disk_busy():
        raise Err(409, "a disk job is running: the comparison waits for it")
    coverage = _coverage()
    c = connect(create=False)
    base = {"min": lo, "kind": kind, "groups": [], "total": 0, "capped": False, "waste": 0, "page": 0, "pages": 1, "coverage": coverage}
    if c is None:
        return base
    if not _dup_lock.acquire(timeout=DUP_SECONDS + 5):                  # a tap on another chip while one is running waits its turn
        raise Err(429, "another comparison is running: try again in a moment")
    try:
        deadline = time.time() + DUP_SECONDS
        c.set_progress_handler(lambda: 1 if time.time() > deadline else 0, DUP_TICK)
        where, args = ["v.state='done'", "f.kind!='folder'", "f.size>=?"], [lo]
        if KINDS[kind]:
            where.append("f.kind=?")
            args.append(KINDS[kind])
        try:
            groups = c.execute("SELECT lower(f.name) AS n, f.size AS s, count(*) AS k FROM files f JOIN volumes v ON v.id=f.vol WHERE "
                               + " AND ".join(where) + f" GROUP BY n, s HAVING k>1 ORDER BY (k-1)*s DESC, n LIMIT {DUP_GROUPS + 1}", args).fetchall()
            capped = len(groups) > DUP_GROUPS
            groups = groups[:DUP_GROUPS]
            pages = max(1, -(-len(groups) // DUP_PAGE))
            page = min(page, pages - 1)
            shown = groups[page * DUP_PAGE:(page + 1) * DUP_PAGE]
            rows = []
            if shown:
                pairs = ",".join("(?,?)" for _ in shown)
                rows = c.execute("SELECT f.id, f.inode, f.name, f.kind, f.size, f.mtime, names.path, v.sha1, v.rel, v.label, v.start, v.fs, v.vlabel "
                                 "FROM files f JOIN volumes v ON v.id=f.vol JOIN names ON names.rowid=f.id "
                                 f"WHERE v.state='done' AND f.kind!='folder' AND (f.size, lower(f.name)) IN (VALUES {pairs}) ORDER BY v.label, names.path",
                                 [x for g in shown for x in (g[1], g[0])]).fetchall()
        except sqlite3.OperationalError:
            raise Err(504, "there are too many files to compare in one go: try a bigger minimum size") from None
    finally:
        c.set_progress_handler(None, 0)
        c.close()
        _dup_lock.release()
    cache, by = {}, {}
    for fid, inode, name, k, size, mtime, path, sha1, hint, label, start, fs, vlabel in rows:
        by.setdefault((name.lower(), size), []).append({
            "id": fid, "inode": inode, "name": name, "kind": k, "size": size, "mtime": mtime, "path": path,
            "folder": path.rsplit("/", 1)[0] if "/" in path else "", "image": _rel_of(sha1, hint, cache), "label": label, "vol": start,
            "volume": fs + (f" '{vlabel}'" if vlabel else "")})
    out = []
    for n, size, count in shown:
        members = by.get((n, size), [])
        out.append({"name": members[0]["name"] if members else n, "size": size, "count": count, "waste": (count - 1) * size,
                    "members": members[:DUP_MEMBERS], "more": max(0, count - min(len(members), DUP_MEMBERS))})
    return {"min": lo, "kind": kind, "groups": out, "total": len(groups), "capped": capped, "waste": sum((k - 1) * s for _, s, k in groups),
            "page": page, "pages": pages, "coverage": coverage}


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
    text = {"documents": 0, "read": 0, "skipped": 0, "codes": {}, "images": 0, "reading": 0}
    c = connect(create=False)
    if c is not None:
        try:
            seen = set()
            for sha1, docs, tread, tskip, tcodes, tstate in c.execute("SELECT sha1, docs, tread, tskip, tcodes, tstate FROM volumes WHERE state='done'"):
                if sha1 not in imgs:
                    continue
                text["documents"] += docs or 0
                text["read"] += tread or 0
                text["skipped"] += tskip or 0
                for code, n in (json.loads(tcodes) if tcodes else {}).items():
                    text["codes"][code] = text["codes"].get(code, 0) + n
                if tread:
                    seen.add(sha1)
                if tstate in ("queued", "reading"):
                    text["reading"] += 1
            text["images"] = len(seen)
        finally:
            c.close()
    return {"images": len(imgs), "indexed": len(done), "indexing": working, "text": text}


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
