"""A content-addressed file store with resumable, part-by-part uploads (stdlib only).

One file per SHA-256, kept at <root>/<aa>/<sha256>. This is the file side of the contract's `roms` calls (docs/contract/v1,
"Files"), and the archive and the deck's own loopback file calls both use it, so there is one implementation of it in
this repository. The hubs (cabcrawl, the Tech Tracker) implement the same calls over their own stores.

    begin(sha256, size)         stored, or open with the parts still to send (resumable)
    put_part(sha256, n, data)   one part: exactly PART_SIZE bytes, the last one the remainder
    finish(sha256)              joins the parts, checks the size and the hash, and stores the file
    path(sha256), size(sha256)  where it is, and how big, once it's stored

An upload in progress is a folder of numbered parts under <root>/.incoming/<sha256>/ with its announced size beside them.
Nothing in it is a file until finish has joined the parts and checked both. A half-sent upload older than a week is
removed the next time anyone begins one. Every call is safe to repeat.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

PART_SIZE = 8 * 1024 * 1024
STALE_AFTER = 7 * 24 * 3600
HEX = re.compile(r"^[0-9a-f]{64}$")


class FileError(Exception):
    """A refused call; `code` is the HTTP status the contract gives it."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def check_hash(value):
    if not isinstance(value, str) or not HEX.match(value):
        raise FileError("sha256 must be 64 lowercase hex characters")
    return value


def parts_for(size):
    return -(-size // PART_SIZE)


class FileStore:
    def __init__(self, root, max_size=400 * 1024 * 1024):
        self.root, self.max_size = root, max_size

    # where things are
    def path(self, sha256):
        return os.path.join(self.root, sha256[:2], sha256)

    def _incoming(self, sha256):
        return os.path.join(self.root, ".incoming", sha256)

    def size(self, sha256):
        """The size of a stored file, or None."""
        try:
            return os.path.getsize(self.path(sha256)) if HEX.match(sha256) else None
        except OSError:
            return None

    def _meta(self, sha256):
        try:
            with open(os.path.join(self._incoming(sha256), "size.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _have(self, sha256, parts):
        return [n for n in range(parts) if os.path.isfile(os.path.join(self._incoming(sha256), f"{n}.part"))]

    def _stored(self, sha256):
        size = self.size(sha256)
        return {"state": "stored", "sha256": sha256, "size": size} if size is not None else None

    def sweep(self):
        """Half-sent uploads nobody has touched for a week."""
        base = os.path.join(self.root, ".incoming")
        if not os.path.isdir(base):
            return
        cutoff = time.time() - STALE_AFTER
        for name in os.listdir(base):
            p = os.path.join(base, name)
            try:
                if os.stat(p).st_mtime < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass

    # the calls
    def begin(self, sha256, size):
        check_hash(sha256)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise FileError("size must be a whole number of bytes, 1 or more")
        if size > self.max_size:
            raise FileError(f"a file is at most {self.max_size} bytes, got {size}", 413)
        have = self._stored(sha256)
        if have:
            return have
        self.sweep()
        folder = self._incoming(sha256)
        meta = self._meta(sha256)
        if meta is not None and meta.get("size") != size:
            shutil.rmtree(folder, ignore_errors=True)                 # a different announcement: start over
        os.makedirs(folder, mode=0o750, exist_ok=True)
        with open(os.path.join(folder, "size.json"), "w", encoding="utf-8") as f:
            json.dump({"size": size}, f)
        os.utime(folder)
        parts = parts_for(size)
        return {"state": "open", "sha256": sha256, "size": size, "part_size": PART_SIZE, "parts": parts,
                "have": self._have(sha256, parts)}

    def put_part(self, sha256, n, data):
        check_hash(sha256)
        have = self._stored(sha256)
        if have:
            return have                                                # already stored: the bytes are ignored
        meta = self._meta(sha256)
        if meta is None:
            raise FileError("begin the file first (POST /api/v1/files)", 404)
        size = meta["size"]
        parts = parts_for(size)
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < parts:
            raise FileError(f"part {n} is outside 0..{parts - 1}")
        want = PART_SIZE if n < parts - 1 else size - PART_SIZE * (parts - 1)
        if len(data) != want:
            raise FileError(f"part {n} must be exactly {want} bytes, got {len(data)}")
        folder = self._incoming(sha256)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(folder, f"{n}.part"))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.utime(folder)
        return {"state": "open", "sha256": sha256, "part": n, "have": self._have(sha256, parts)}

    def finish(self, sha256):
        check_hash(sha256)
        have = self._stored(sha256)
        if have:
            return have
        meta = self._meta(sha256)
        if meta is None:
            raise FileError("begin the file first (POST /api/v1/files)", 404)
        size = meta["size"]
        parts = parts_for(size)
        got = self._have(sha256, parts)
        if len(got) != parts:
            raise FileError(f"parts missing: {[n for n in range(parts) if n not in got]}", 409)
        final = self.path(sha256)
        os.makedirs(os.path.dirname(final), mode=0o750, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(final), prefix=".tmp-")
        digest, total = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as out:
                for n in range(parts):
                    with open(os.path.join(self._incoming(sha256), f"{n}.part"), "rb") as part:
                        while chunk := part.read(1 << 20):
                            digest.update(chunk)
                            total += len(chunk)
                            out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if total != size or digest.hexdigest() != sha256:
                shutil.rmtree(self._incoming(sha256), ignore_errors=True)
                raise FileError("the parts don't join to the announced size and SHA-256: the upload was dropped, "
                                "send the file again", 422)
            os.replace(tmp, final)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        shutil.rmtree(self._incoming(sha256), ignore_errors=True)
        return self._stored(sha256)

    def put_stream(self, src, length, magic=None):
        """Store `length` bytes read from `src` (a request body) without holding them in memory; `magic` is what the file
        must start with (a PDF's %PDF-). -> (sha256, length). A file already stored is kept, not rewritten."""
        if length < 1:
            raise FileError("the body is empty")
        if length > self.max_size:
            raise FileError(f"a file is at most {self.max_size} bytes, got {length}", 413)
        os.makedirs(os.path.join(self.root, ".incoming"), mode=0o750, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.join(self.root, ".incoming"), prefix=".upload-")
        digest, left, first = hashlib.sha256(), length, True
        try:
            with os.fdopen(fd, "wb") as out:
                while left > 0:
                    chunk = src.read(min(1 << 20, left))
                    if not chunk:
                        raise FileError("the upload was cut off: send the file again")
                    if first and magic and not chunk.startswith(magic):
                        raise FileError("that isn't the kind of file this takes", 415)
                    first = False
                    digest.update(chunk)
                    out.write(chunk)
                    left -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            sha256 = digest.hexdigest()
            final = self.path(sha256)
            if os.path.exists(final):
                os.unlink(tmp)
            else:
                os.makedirs(os.path.dirname(final), mode=0o750, exist_ok=True)
                os.replace(tmp, final)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return sha256, length

    def put_file(self, data):
        """Store whole bytes (a file already in memory: a PDF the deck read from its own disk). -> the sha256."""
        sha256 = hashlib.sha256(data).hexdigest()
        if self._stored(sha256):
            return sha256
        self.begin(sha256, len(data))
        for n in range(parts_for(len(data))):
            self.put_part(sha256, n, data[n * PART_SIZE:(n + 1) * PART_SIZE])
        self.finish(sha256)
        return sha256
