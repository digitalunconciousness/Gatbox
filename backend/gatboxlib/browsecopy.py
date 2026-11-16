"""COPY TO THE STORE (F4d of docs/plans/2026-11-15-drive-browser-f4.md): a file, or a folder and everything in it, out of a stored image
into the store's own _exports folder, where it leaves the Pi by taking the drive to another computer.

It is the one thing in the drive browser that writes, and it writes only there. The owner's reading of hard rule 6 (2026-11-15): a
write into the store's exports folder is not a hardware write: it is the drive the READ jobs already write to. What it does to stay
that small:

- every copy is a new folder, named for the time, the image and the item, made as <name>.partial and renamed when the copy is whole,
  so nothing already on the store can be overwritten, mixed with, or half-replaced;
- every file is created new (O_EXCL), written from `icat`, hashed (SHA-1) as it is written, flushed, read back from the store and
  hashed again; a copy that does not match stops the whole job;
- a list of every file, with its size and SHA-1, and where it came from (image SHA-1, volume, inode, whether it was a deleted file) is
  written with the copy (_export.json);
- names out of an image are other people's: path separators, control characters, "." and ".." and over-long names are made safe, and
  two names that come to the same one are kept apart;
- a folder copy takes its live files only. A deleted file or folder is copied when it is the thing asked for, and is marked as
  recovered in the list: what comes back may be incomplete, damaged or another file's data;
- it looks at the free space first and keeps a reserve; it stands still while a disk job is queued or running (imaging goes first),
  and it stops when asked; one at a time.

Nothing here opens a drive or changes an image.
"""
import hashlib
import json
import os
import select
import subprocess
import threading
import time

from . import browse as B
from . import disks as dl

MiB = 1 << 20
EXPORTS = os.environ.get("GATBOX_EXPORTS_DIR", os.path.join(dl.DISKS, "_exports"))
CHUNK = MiB
MAX_FILES = 100_000                                     # files in one copy (the plan is held in memory)
RESERVE = 2 * 1024 * MiB                                # kept free on the store, and at least 2% of it
STALL_S = 180                                           # a read out of the image that gives nothing for this long is stopped
PLAN_S = 900                                            # listing a big folder
MANIFEST = "_export.json"
RECENT = 8


_umask_lock = threading.Lock()


def mkdir_shared(path):
    """A new folder the owner's group can use: mode 0775, and its group and setgid bit come from its parent (the unit makes _exports
    2775, so every folder made in it is setgid and in the spool's group). Done with the umask, not with a chmod: the browser's unit has
    RestrictSUIDSGID=yes, which refuses any chmod that names the setgid bit, and a copy made with `chmod 2775` left every folder 0700
    and unreadable to the owner (seen on the Pi, 2026-11-15; outside the sandbox nothing refuses it). The umask is process-wide; it is
    cleared for this one mkdir, and everything else this service creates is inside _browse (0700) or its private /tmp."""
    with _umask_lock:
        old = os.umask(0)
        try:
            os.mkdir(path, 0o775)
        finally:
            os.umask(old)


class Cancelled(Exception):
    pass


class Fatal(Exception):
    """A reason the whole copy stops: said on the screen as it is."""


def free_space(path):
    """(bytes free for a normal user, bytes in all) of the file system `path` is on."""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def safe_name(name, fallback="_"):
    """A name from inside an image made safe to create: no separators or control characters, never "." or "..", no trailing dots or
    spaces, and at most 200 bytes."""
    n = "".join("_" if (ord(c) < 32 or c in "/\\\x7f") else c for c in str(name)).strip().rstrip(". ")
    if n in ("", ".", ".."):
        n = fallback
    while len(n.encode("utf-8", "replace")) > 200:
        n = n[:-1]
    return n or fallback


def unique(taken, name):
    """`name`, or `name (2)`, `name (3)`... when its lower case is taken already (two names that differ only in capitals, or that
    were made the same by safe_name, must not land on one file). Adds the answer to `taken`."""
    cand, i = name, 1
    stem, ext = os.path.splitext(name)
    while cand.lower() in taken:
        i += 1
        cand = f"{stem} ({i}){ext}"
    taken.add(cand.lower())
    return cand


def fmt(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def recent():
    """The latest copies on the store: [{"dir", "files", "bytes", "finished", "name", "incomplete"}], newest first."""
    out = []
    try:
        names = os.listdir(EXPORTS)
    except OSError:
        return out
    for n in names:
        p = os.path.join(EXPORTS, n)
        if not os.path.isdir(p):
            continue
        if n.endswith(".partial"):
            out.append({"dir": n, "incomplete": True, "finished": os.path.getmtime(p)})
            continue
        try:
            with open(os.path.join(p, MANIFEST)) as f:
                m = json.load(f)
            out.append({"dir": n, "incomplete": False, "files": len(m.get("files", [])), "bytes": m.get("bytes", 0),
                        "finished": m.get("finished"), "name": (m.get("item") or {}).get("name"), "failed": len(m.get("failed", []))})
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: r.get("finished") or 0, reverse=True)
    return out[:RECENT]


class Exporter:
    """The one copy at a time, and what the screen shows of it."""

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
        st["recent"] = recent()
        return st

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    # -- what the screen asks -------------------------------------------------------------------------------------------------

    def request(self, body):
        if not isinstance(body, dict):
            raise B.Err(400, "a JSON object: {\"action\": \"start\", \"image\": ..., \"vol\": ..., \"inode\": ..., \"kind\": ...}")
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
                raise B.Err(409, "a copy is already running: wait for it, or stop it")
            self.stop = False
            self.state = {"state": "queued", "name": job["name"], "kind": job["kind"], "label": job["label"], "files_done": 0, "files_total": None,
                          "bytes_done": 0, "bytes_total": None, "current": None, "started": time.time(), "deleted": job["deleted"]}
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="copy")
            self.thread.start()
        return self.status()

    def _check(self, body):
        """The job a request names, or the sentence for why not. Nothing is written yet."""
        if B.disk_busy():
            raise B.Err(409, "a disk job is running: copying waits for it")
        rel = body.get("image")
        path, side = B.image(rel)
        v = B._volume(rel, body.get("vol"))
        kind = body.get("kind")
        if kind not in ("file", "folder"):
            raise B.Err(400, "kind is file or folder")
        ino = B._file_inode(body.get("inode")) if kind == "file" else B._inode(body.get("inode") or None)
        default = (v["fs"].get("label") or "volume") if kind == "folder" and ino is None else "copy"
        return {"rel": rel, "path": path, "side": side, "start": v["start"], "fs": v["fs"], "kind": kind, "inode": ino,
                "name": safe_name(body.get("name") or default), "size": max(0, B._num(body.get("size"), 0)),
                "deleted": bool(body.get("deleted")), "label": side.get("label") or os.path.basename(rel)}

    # -- the work ---------------------------------------------------------------------------------------------------------------

    def _run(self, job):
        dest = None
        try:
            os.makedirs(EXPORTS, exist_ok=True)
        except OSError:
            return self._set(state="error", error="the exports folder isn't on the store and cannot be made: restart the drive browser with the store plugged in")
        try:
            self._set(state="planning")
            items, folders, skipped = self._plan(job)
            total = sum(i["size"] for i in items)
            self._set(files_total=len(items), bytes_total=total, skipped=len(skipped))
            free, whole = free_space(EXPORTS)
            reserve = max(RESERVE, whole // 50)
            if total + reserve > free:
                raise Fatal(f"not enough room on the store: this needs {fmt(total)} and {fmt(max(0, free))} is free (it keeps {fmt(reserve)} free)")
            dest = self._make_dest(job)
            self._set(dest=os.path.basename(dest)[:-len(".partial")])
            files, failed = self._copy(job, dest, items, folders)
            now = time.time()
            manifest = {"version": 1, "started": self.state.get("started"), "finished": now, "tool": "gatbox-browse",
                        "image": {"rel": job["rel"], "sha1": job["side"].get("sha1"), "label": job["label"]},
                        "volume": {"start": job["start"], "fs": job["fs"].get("type"), "label": job["fs"].get("label")},
                        "item": {"name": job["name"], "inode": job["inode"], "kind": job["kind"], "deleted": job["deleted"]},
                        "recovered": job["deleted"], "bytes": sum(f["size"] for f in files), "files": files, "folders": len(folders), "failed": failed,
                        "skipped": skipped, "verified": True,
                        "note": "SHA-1 of each file as read from the image and read back from the store. A file from a deleted entry is a recovery: "
                                "it may be incomplete, damaged or another file's data."}
            fd = os.open(os.path.join(dest, MANIFEST), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
            with os.fdopen(fd, "w") as f:
                json.dump(manifest, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            self._chmod(os.path.join(dest, MANIFEST), 0o664)
            final = dest[:-len(".partial")]
            if os.path.exists(final):
                raise Fatal("the finished folder's name is taken: nothing was overwritten, and the copy is in " + os.path.basename(dest))
            os.rename(dest, final)
            self._set(state="done", finished=now, files_done=len(files), bytes_done=manifest["bytes"], failed=len(failed), current=None,
                      dest=os.path.basename(final), verified=True)
        except Cancelled:
            self._set(state="stopped", current=None, error=("stopped: what was copied so far is in " + os.path.basename(dest)) if dest else "stopped")
        except Fatal as e:
            self._set(state="error", error=str(e), current=None)
        except B.Err as e:
            self._set(state="error", error=e.msg, current=None)
        except OSError as e:
            self._set(state="error", error=f"the store refused a write ({e.strerror or e}): nothing was overwritten", current=None)
        except Exception as e:                                   # a copy that dies says so, and is not retried silently
            self._set(state="error", error=f"the copy hit an error ({type(e).__name__})", current=None)

    @staticmethod
    def _chmod(path, mode):
        """Set a file's mode, and stop the copy when it cannot be set: a copy the owner cannot read is not a copy (this used to be
        swallowed, which hid the setgid failure the unit's RestrictSUIDSGID caused)."""
        try:
            os.chmod(path, mode)
        except OSError as e:
            raise Fatal(f"could not set the permissions on a copied file ({e.strerror}): the copy is not usable") from None

    def _make_dest(self, job):
        base = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_name(job['label'])}_{job['name']}"[:120].rstrip(". ")
        for i in range(1, 100):
            name = base if i == 1 else f"{base}-{i}"
            dest = os.path.join(EXPORTS, name + ".partial")
            if os.path.exists(os.path.join(EXPORTS, name)):
                continue
            try:
                mkdir_shared(dest)
            except FileExistsError:
                continue
            return dest
        raise Fatal("could not find a free name for this copy")

    def _plan(self, job):
        """([file items], [folder paths], [skipped]) for the job. A file is one item; a folder is everything under it that is a regular
        file (a link or a special file is skipped and said so), live files only unless the folder itself was deleted."""
        if job["kind"] == "file":
            return [{"rel": job["name"], "inode": job["inode"], "size": job["size"], "mtime": None, "deleted": job["deleted"]}], [], []
        argv = ["fls", "-r", "-m", "/", "-o", str(job["start"]), job["path"]] + ([job["inode"]] if job["inode"] else [])
        rc, out, err = B.run(argv, PLAN_S, cap=96 * MiB)
        if rc != 0 and not out:
            raise Fatal("this folder could not be listed" + (f": {err.strip().splitlines()[0][:120]}" if err.strip() else ""))
        if len(out) >= 96 * MiB:
            raise Fatal("this folder has too many entries to copy in one go: copy a smaller folder")
        items, folders, skipped = [], [], []
        for raw in out.decode("utf-8", "replace").splitlines():
            p = B.parse_body(raw)
            if p is None:
                continue
            name, ino, mode, size, mtime = p
            t = mode[:1]
            dead = B.gone_name(name)
            if dead:
                if not job["deleted"]:
                    continue
                name = dead[0]
                if dead[1]:
                    skipped.append({"path": name, "why": "deleted, and its space now holds another file"})
                    continue
            if t in ("V", "v") or name.endswith("(Volume Label Entry)"):
                continue
            if t == "d":
                folders.append(name)
            elif t == "r":
                items.append({"rel": name, "inode": ino, "size": size, "mtime": mtime if 631152000 <= mtime <= 4102444800 else None, "deleted": bool(dead)})
            elif t == "l":
                skipped.append({"path": name.partition(" -> ")[0], "why": "a link"})
            if len(items) > MAX_FILES:
                raise Fatal(f"more than {MAX_FILES:,} files in one copy: copy a smaller folder")
        return items, folders, skipped

    def _copy(self, job, dest, items, folders):
        """Create every file under `dest`, verified. Returns ([manifest rows], [failed])."""
        taken = {"": set()}                                      # lower-case names taken in each created folder (key: its path under dest)
        dirmap = {"": dest}                                      # original folder path -> path made for it

        def ensure(parts):
            key = "/".join(parts)
            if key in dirmap:
                return dirmap[key]
            parent = ensure(parts[:-1]) if parts[:-1] else dest
            pkey = "/".join(parts[:-1])
            name = unique(taken.setdefault(pkey, set()), safe_name(parts[-1]))
            path = os.path.join(parent, name)
            mkdir_shared(path)
            dirmap[key] = path
            taken.setdefault(key, set())
            return path
        for f in sorted(folders):
            ensure([p for p in f.split("/") if p])
        rows, failed, done_bytes = [], [], 0
        for n, it in enumerate(items, 1):
            parts = [p for p in it["rel"].split("/") if p]
            parent = ensure(parts[:-1]) if parts[:-1] else dest
            pkey = "/".join(parts[:-1])
            name = unique(taken.setdefault(pkey, set()), safe_name(parts[-1]))
            target = os.path.join(parent, name)
            self._set(current=it["rel"], files_done=n - 1, bytes_done=done_bytes)
            row = self._one(job, it, target, done_bytes)
            if row is None:
                failed.append({"path": it["rel"], "why": "could not be read out of the image"})
            else:
                row["path"] = os.path.relpath(target, dest)
                rows.append(row)
                done_bytes += row["size"]
        return rows, failed

    def _wait_turn(self):
        """Between chunks: stop when asked, and stand still while a disk job runs."""
        while True:
            if self.stop:
                raise Cancelled()
            if not B.disk_busy():
                if self.state.get("state") == "waiting":
                    self._set(state="copying")
                return
            self._set(state="waiting")
            time.sleep(1)

    def _one(self, job, it, target, done_before):
        """Copy one file out of the image into `target` (new), hash it, read it back. The manifest row, or None when the image would not
        give it up (the half-written file is removed)."""
        self._set(state="copying")
        argv = ["icat", "-o", str(job["start"]), job["path"], it["inode"]]
        h, n = hashlib.sha1(), 0
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
        ok = False
        try:
            with os.fdopen(fd, "wb") as out, B._sem:
                p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=B.env(), start_new_session=True)
                try:
                    last = time.time()
                    while True:
                        self._wait_turn()
                        ready, _, _ = select.select([p.stdout], [], [], 5)
                        if not ready:
                            if time.time() - last > STALL_S:
                                B.kill_group(p)
                                raise Fatal(f"reading {it['rel']} out of the image stalled for {STALL_S} seconds, so the copy was stopped")
                            continue
                        chunk = os.read(p.stdout.fileno(), CHUNK)
                        if not chunk:
                            break
                        last = time.time()
                        out.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
                        self._set(bytes_done=done_before + n)
                finally:
                    if p.poll() is None:
                        B.kill_group(p)
                    p.stdout.close()
                    rc = p.wait()
                out.flush()
                os.fsync(out.fileno())
            if rc != 0 or (n == 0 and it["size"] > 0 and not it["deleted"]):
                return None
            digest = h.hexdigest()
            back = hashlib.sha1()
            with open(target, "rb") as f:
                for blk in iter(lambda: f.read(CHUNK), b""):
                    back.update(blk)
            if back.hexdigest() != digest:
                raise Fatal(f"{it['rel']} was read back from the store and does not match what was written: the store may be failing, so the copy was stopped")
            self._chmod(target, 0o664)
            if it.get("mtime"):
                try:
                    os.utime(target, (it["mtime"], it["mtime"]))
                except OSError:
                    pass
            ok = True
            row = {"size": n, "sha1": digest, "inode": it["inode"], "deleted": it["deleted"]}
            if n != it["size"]:
                row["note"] = f"the image listed {it['size']} bytes and {n} were read"
            return row
        finally:
            if not ok:
                try:
                    os.remove(target)
                except OSError:
                    pass


EXP = Exporter()
