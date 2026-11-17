"""Disk images from the deck, kept apart from the library (docs/plans/2026-11-17-disk-image-drain.md).

The owner, 2026-11-17: every image goes to the archive, over the home network only, never the tunnel. So these calls are on the
private listener alone (server.py), for the deck's device token, and none of it is library-v1: no app reads it.

An image is announced (its SHA-256, its size and the deck's sidecar), sent in 8 MiB parts written in place into one file of the
image's size (an 80 GB image needs 80 GB here, not twice that, as joining parts would), then checked: the whole file is read back
and its SHA-256 must be the one announced. Only then is it stored. A check runs in the background, because reading 80 GB takes
longer than an HTTP request should; the deck asks again for the state.

    <root>/incoming/<sha256>/  meta.json, data (the image, filled part by part), have (one byte per part: 1 = written)
    <root>/stored/<sha256[:2]>/<sha256>.img  and  .json (the sidecar the deck sent, plus when it was stored)

Room: a new image is refused (507) when it would leave less than `reserve` bytes free, counting what the images still arriving
have yet to write. Nothing here deletes a stored image.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time

PART_SIZE = 8 * 1024 * 1024
SIDE_MAX = 64 * 1024                     # the sidecar the deck sends: JSON, at most this many bytes
_SHA = re.compile(r"^[0-9a-f]{64}$")


class DiskError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def parts_for(size):
    return -(-size // PART_SIZE)


class DiskStore:
    def __init__(self, root, reserve=20 << 30, max_size=8 << 40):
        self.root, self.reserve, self.max_size = root, reserve, max_size
        self._lock = threading.Lock()
        self._checking = set()           # images being checked by a thread of this process
        for d in ("incoming", "stored"):
            os.makedirs(os.path.join(root, d), mode=0o750, exist_ok=True)

    # --- where things are ----------------------------------------------------------------------------------------------
    @staticmethod
    def _check_hash(sha):
        if not isinstance(sha, str) or not _SHA.match(sha):
            raise DiskError("sha256: 64 lowercase hex digits")

    def _in(self, sha, name=""):
        return os.path.join(self.root, "incoming", sha, name)

    def _stored(self, sha, ext):
        return os.path.join(self.root, "stored", sha[:2], sha + ext)

    def _meta(self, sha):
        try:
            with open(self._in(sha, "meta.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save_meta(self, sha, meta):
        tmp = self._in(sha, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(tmp, self._in(sha, "meta.json"))

    def _have(self, sha):
        try:
            with open(self._in(sha, "have"), "rb") as f:
                return f.read()
        except OSError:
            return b""

    def _stored_state(self, sha):
        if not os.path.isfile(self._stored(sha, ".img")):
            return None
        try:
            with open(self._stored(sha, ".json"), encoding="utf-8") as f:
                side = json.load(f)
        except (OSError, ValueError):
            side = {}
        return {"state": "stored", "sha256": sha, "size": os.path.getsize(self._stored(sha, ".img")),
                "stored_at": side.get("stored_at")}

    def _owed(self):
        """Bytes the images still arriving have yet to write (their files are sparse until then)."""
        owed = 0
        for sha in os.listdir(os.path.join(self.root, "incoming")):
            meta = self._meta(sha) if _SHA.match(sha) else None
            if meta:
                owed += max(meta["size"] - self._have(sha).count(1) * PART_SIZE, 0)
        return owed

    # --- the calls -----------------------------------------------------------------------------------------------------
    def begin(self, sha, size, side):
        self._check_hash(sha)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise DiskError("size must be a whole number of bytes, 1 or more")
        if size > self.max_size:
            raise DiskError(f"an image here is at most {self.max_size} bytes, got {size}", 413)
        if not isinstance(side, dict) or len(json.dumps(side)) > SIDE_MAX:
            raise DiskError(f"side: the image's sidecar, a JSON object of at most {SIDE_MAX} bytes")
        with self._lock:
            done = self._stored_state(sha)
            if done:
                return done
            meta = self._meta(sha)
            if meta is not None and meta["size"] == size:
                return self.state(sha)                         # resume: the parts it has are kept
            if meta is not None:
                shutil.rmtree(self._in(sha), ignore_errors=True)       # announced again with another size: start over
            free = shutil.disk_usage(self.root).free
            room = free - self.reserve - self._owed()
            if size > room:
                raise DiskError(f"no room: the image is {size:,} bytes and the archive can take {max(room, 0):,} more "
                                f"(it keeps {self.reserve:,} free; {free:,} are free now)", 507)
            os.makedirs(self._in(sha), mode=0o750)
            with open(self._in(sha, "data"), "wb") as f:
                f.truncate(size)                               # sparse: the parts fill it
            with open(self._in(sha, "have"), "wb") as f:
                f.write(bytes(parts_for(size)))
            self._save_meta(sha, {"size": size, "side": side, "began": round(time.time(), 3), "state": "open"})
            return self.state(sha)

    def put_part(self, sha, n, data):
        self._check_hash(sha)
        if self._stored_state(sha):
            return self._stored_state(sha)
        meta = self._meta(sha)
        if meta is None:
            raise DiskError("begin the image first (POST /api/v1/disks)", 404)
        if meta.get("state") == "checking":
            raise DiskError("the image is being checked: ask for its state", 409)
        size, parts = meta["size"], parts_for(meta["size"])
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < parts:
            raise DiskError(f"part {n} is outside 0..{parts - 1}")
        want = PART_SIZE if n < parts - 1 else size - PART_SIZE * (parts - 1)
        if len(data) != want:
            raise DiskError(f"part {n} must be exactly {want} bytes, got {len(data)}")
        fd = os.open(self._in(sha, "data"), os.O_WRONLY)
        try:
            os.pwrite(fd, data, n * PART_SIZE)
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(self._in(sha, "have"), os.O_WRONLY)
        try:
            os.pwrite(fd, b"\x01", n)                        # only after the part's bytes are on the disk
        finally:
            os.close(fd)
        if meta.get("state") == "failed":                    # a part sent again after a failed check: open again
            meta["state"] = "open"
            meta.pop("why", None)
            self._save_meta(sha, meta)
        return {"state": "open", "sha256": sha, "part": n}

    def finish(self, sha):
        self._check_hash(sha)
        with self._lock:
            done = self._stored_state(sha)
            if done:
                return done
            meta = self._meta(sha)
            if meta is None:
                raise DiskError("begin the image first (POST /api/v1/disks)", 404)
            missing = self._have(sha).count(0)
            if missing:
                raise DiskError(f"{missing} part(s) not sent yet", 409)
            if meta.get("state") != "checking":
                meta["state"] = "checking"
                meta["check_began"] = round(time.time(), 3)
                self._save_meta(sha, meta)
            self._start_check(sha)
        return self.state(sha)

    def _start_check(self, sha):
        if sha in self._checking:
            return
        self._checking.add(sha)
        threading.Thread(target=self._check, args=(sha,), daemon=True, name=f"disk-check-{sha[:8]}").start()

    def _check(self, sha):
        """Read the whole image back and compare its SHA-256 with the one announced; store it, or say it failed."""
        try:
            meta = self._meta(sha)
            h = hashlib.sha256()
            with open(self._in(sha, "data"), "rb") as f:
                while chunk := f.read(4 << 20):
                    h.update(chunk)
            if h.hexdigest() != sha:
                # Which parts are wrong is unknown: all of them are sent again.
                with open(self._in(sha, "have"), "wb") as f:
                    f.write(bytes(parts_for(meta["size"])))
                meta.update(state="failed", why="its bytes did not hash to the SHA-256 announced: every part is wanted again")
                self._save_meta(sha, meta)
                return
            os.makedirs(os.path.dirname(self._stored(sha, ".img")), mode=0o750, exist_ok=True)
            side = dict(meta["side"], stored_at=round(time.time(), 3), began=meta["began"])
            with open(self._stored(sha, ".json") + ".tmp", "w", encoding="utf-8") as f:
                json.dump(side, f, indent=1)
            os.replace(self._stored(sha, ".json") + ".tmp", self._stored(sha, ".json"))
            os.replace(self._in(sha, "data"), self._stored(sha, ".img"))      # the image last: it is what makes it "stored"
            shutil.rmtree(self._in(sha), ignore_errors=True)
        finally:
            self._checking.discard(sha)

    def state(self, sha):
        self._check_hash(sha)
        done = self._stored_state(sha)
        if done:
            return done
        meta = self._meta(sha)
        if meta is None:
            raise DiskError("no such image", 404)
        if meta.get("state") == "checking" and sha not in self._checking:
            self._start_check(sha)                           # the archive restarted mid-check: check again
        have = self._have(sha)
        out = {"state": meta.get("state", "open"), "sha256": sha, "size": meta["size"], "part_size": PART_SIZE,
               "parts": len(have), "have": have.count(1)}
        if out["state"] != "checking":
            out["missing"] = [i for i, b in enumerate(have) if not b][:2048]    # the first ones: the deck sends in order
        if meta.get("why"):
            out["why"] = meta["why"]
        return out

    def list(self):
        """What is stored, newest first: each image's SHA-256, size, and what its sidecar says it is."""
        out = []
        base = os.path.join(self.root, "stored")
        for d in sorted(os.listdir(base)):
            for name in os.listdir(os.path.join(base, d)):
                if not name.endswith(".json"):
                    continue
                sha = name[:-5]
                st = self._stored_state(sha) if _SHA.match(sha) else None
                if not st:
                    continue
                try:
                    with open(os.path.join(base, d, name), encoding="utf-8") as f:
                        side = json.load(f)
                except (OSError, ValueError):
                    side = {}
                out.append(dict(st, machine=side.get("machine"), label=side.get("label"), sha1=side.get("sha1")))
        return sorted(out, key=lambda r: r.get("stored_at") or 0, reverse=True)
