"""COMPARE (a follow-up to F4c's possible duplicates): read the files of one duplicate group out of their images and say which are
really the same file, by SHA-1.

F4c groups files by name and size alone and says so: two different files can share both. This is the tap that settles it. It reads
every member of ONE group, in full, out of its stored image with `icat` (the same read COPY TO THE STORE uses), hashes it as it goes,
and reports the hashes: files with the same SHA-1 are the same bytes. What it does to stay small:

- it writes nothing: no file on the store, no cache, no list. The answer lives in this service's memory and on the screen;
- it reads the files of one group at a time, at most MAX_MEMBERS of them and at most HASH_CAP bytes in all, and says when a group is
  too big, instead of tying the store up for a quarter of an hour;
- it stands still while a disk job is queued or running (imaging goes first), stops when asked, and gives up on a read that stalls;
- it refuses to start while a copy to the store is running (two readers of one USB drive would each take twice as long);
- a file that will not come out of the image is reported as failed, and the others are still compared.

Nothing here opens a drive or changes an image.
"""
import hashlib
import os
import select
import subprocess
import threading
import time

from . import browse as B
from . import browsecopy as C

MiB = 1 << 20
MAX_MEMBERS = 12                                        # the places F4c lists for one group
HASH_CAP = 2048 * MiB                                   # bytes read for one tap (about a minute at USB 2 speed)


class Hasher:
    """The one comparison at a time, and what the screen shows of it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.state = {"state": "idle"}
        self.stop = False

    def busy(self):
        with self.lock:
            return self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.lock:
            st = dict(self.state)
            st["files"] = [dict(f) for f in st.get("files", [])]
        return st

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _file(self, i, **kw):
        with self.lock:
            self.state["files"][i].update(kw)

    # -- what the screen asks -------------------------------------------------------------------------------------------------

    def request(self, body):
        if not isinstance(body, dict):
            raise B.Err(400, "a JSON object: {\"action\": \"start\", \"name\": ..., \"members\": [{\"id\", \"image\", \"vol\", \"inode\", \"size\"}]}")
        action = body.get("action") or "start"
        if action == "stop":
            with self.lock:
                if self.thread is not None and self.thread.is_alive():
                    self.stop = True
            return self.status()
        if action == "dismiss":
            with self.lock:
                if self.thread is None or not self.thread.is_alive():
                    self.state = {"state": "idle"}
            return self.status()
        if action != "start":
            raise B.Err(400, "action is start, stop or dismiss")
        job = self._check(body)
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise B.Err(409, "a comparison is already running: wait for it, or stop it")
            self.stop = False
            self.state = {"state": "queued", "name": job["name"], "key": job["key"], "started": time.time(), "bytes_done": 0,
                          "bytes_total": job["total"], "current": None, "verdict": None,
                          "files": [{"id": m["id"], "state": "pending", "sha1": None, "bytes": None, "note": None} for m in job["members"]]}
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="hash")
            self.thread.start()
        return self.status()

    def _check(self, body):
        """The job a request names, or the sentence for why not. Nothing is read yet."""
        if B.disk_busy():
            raise B.Err(409, "a disk job is running: comparing waits for it")
        if C.EXP.busy():
            raise B.Err(409, "a copy to the store is running: comparing waits for it")
        members = body.get("members")
        if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS:
            raise B.Err(400, f"members: from 2 to {MAX_MEMBERS} files of one group")
        out, seen = [], set()
        for m in members:
            if not isinstance(m, dict):
                raise B.Err(400, "each member is {\"id\", \"image\", \"vol\", \"inode\", \"size\"}")
            ident = B._num(m.get("id"))
            if ident is None or ident in seen:
                raise B.Err(400, "each member has its own id")
            seen.add(ident)
            rel = m.get("image")
            path, _ = B.image(rel)
            v = B._volume(rel, m.get("vol"))
            out.append({"id": ident, "rel": rel, "path": path, "start": v["start"], "inode": B._file_inode(m.get("inode")),
                        "size": max(0, B._num(m.get("size"), 0))})
        total = sum(m["size"] for m in out)
        if total > HASH_CAP:
            raise B.Err(413, f"these files add up to {C.fmt(total)}: this reads at most {C.fmt(HASH_CAP)} at a time. Copy them to the store and compare them there")
        name = str(body.get("name") or "")[:200]
        return {"members": out, "total": total, "name": name, "key": f"{name.lower()}|{out[0]['size']}"}

    # -- the work ---------------------------------------------------------------------------------------------------------------

    def _run(self, job):
        try:
            done = 0
            for i, m in enumerate(job["members"]):
                self._set(current=i, state="hashing")
                self._file(i, state="hashing")
                got = self._one(m, done)
                if got is None:
                    self._file(i, state="failed", note="could not be read out of the image")
                    continue
                digest, n = got
                done += n
                self._file(i, state="done", sha1=digest, bytes=n,
                           note=None if n == m["size"] else f"the image listed {m['size']} bytes and {n} were read")
                self._set(bytes_done=done)
            self._set(state="done", current=None, finished=time.time(), verdict=self._verdict())
        except C.Cancelled:
            self._set(state="stopped", current=None, error="stopped before every file was read", verdict=self._verdict())
        except C.Fatal as e:
            self._set(state="error", current=None, error=str(e))
        except B.Err as e:
            self._set(state="error", current=None, error=e.msg)
        except Exception as e:                                   # a comparison that dies says so, and is not retried silently
            self._set(state="error", current=None, error=f"the comparison hit an error ({type(e).__name__})")

    def _verdict(self):
        """What the hashes say: "same" (every file read has one SHA-1 and none failed), "different" (more than one SHA-1 among those
        read), "partly" (the ones read agree, but some were not read), or None (nothing was read)."""
        with self.lock:
            files = [dict(f) for f in self.state["files"]]
        hashes = {f["sha1"] for f in files if f["sha1"]}
        unread = [f for f in files if not f["sha1"]]
        if not hashes:
            return None
        if len(hashes) > 1:
            return "different"
        return "partly" if unread else "same"

    def _wait_turn(self):
        """Between chunks: stop when asked, and stand still while a disk job runs."""
        while True:
            if self.stop:
                raise C.Cancelled()
            if not B.disk_busy():
                if self.state.get("state") == "waiting":
                    self._set(state="hashing")
                return
            self._set(state="waiting")
            time.sleep(1)

    def _one(self, m, done_before):
        """(sha1, bytes) of one file read out of its image, or None when the image would not give it up."""
        argv = ["icat", "-o", str(m["start"]), m["path"], m["inode"]]
        h, n = hashlib.sha1(), 0
        with B._sem:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=B.env(), start_new_session=True)
            try:
                last = time.time()
                while True:
                    self._wait_turn()
                    ready, _, _ = select.select([p.stdout], [], [], 5)
                    if not ready:
                        if time.time() - last > C.STALL_S:
                            B.kill_group(p)
                            raise C.Fatal(f"reading a file out of the image stalled for {C.STALL_S} seconds, so the comparison was stopped")
                        continue
                    chunk = os.read(p.stdout.fileno(), C.CHUNK)
                    if not chunk:
                        break
                    last = time.time()
                    h.update(chunk)
                    n += len(chunk)
                    if done_before + n > HASH_CAP:               # the sizes came from the screen: what is actually read is held to the cap too
                        B.kill_group(p)
                        raise C.Fatal(f"these files are bigger than the {C.fmt(HASH_CAP)} one comparison reads: it was stopped. Copy them to the store and compare them there")
                    self._set(bytes_done=done_before + n)
            finally:
                if p.poll() is None:
                    B.kill_group(p)
                p.stdout.close()
                rc = p.wait()
        if rc != 0 or (n == 0 and m["size"] > 0):
            return None
        return h.hexdigest(), n


HASH = Hasher()
