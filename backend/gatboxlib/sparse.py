"""Holes in stored disk images (the owner, 2026-11-17: "the holes after read and import").

A drive image is mostly zeros when the drive was: an 80 GB drive with 10 GB on it. A sparse file keeps those zeros as holes, which
take no room on the store's ext4 and read back as zeros, so the image's bytes, its SHA-1 and its SHA-256 are unchanged. Only the
room it takes changes (`du` shows it; `ls -l` still shows the full size).

The unit is BLOCK (64 KiB, aligned): a block that is all zeros becomes a hole. Smaller zero runs stay as they are; they are not
worth the bookkeeping. Nothing here changes a byte a reader can see: a hole is only ever punched where the bytes just read were
zeros, in a file nothing else writes (the store's lock is held by the caller).
"""
import ctypes
import errno
import os

BLOCK = 64 << 10
_ZERO = bytes(BLOCK)
_PUNCH = 0x02 | 0x01                     # FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE (linux/falloc.h): the size stays
_libc = None


def _fallocate():
    global _libc
    if _libc is None:
        lib = ctypes.CDLL(None, use_errno=True)
        lib.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_long, ctypes.c_long]
        lib.fallocate.restype = ctypes.c_int
        _libc = lib
    return _libc.fallocate


def punch(fd, offset, length):
    """Make OFFSET..OFFSET+LENGTH of the open file a hole. -> False where the filesystem can't (the bytes are left as they are)."""
    if length <= 0:
        return True
    if _fallocate()(fd, _PUNCH, offset, length) == 0:
        return True
    e = ctypes.get_errno()
    if e in (errno.EOPNOTSUPP, errno.ENOSYS):
        return False
    raise OSError(e, os.strerror(e))


def allocated(path_or_fd):
    """Bytes the file takes on its filesystem (st_blocks is in 512-byte units whatever the block size)."""
    st = os.fstat(path_or_fd) if isinstance(path_or_fd, int) else os.stat(path_or_fd)
    return st.st_blocks * 512


class Holes:
    """Punches the all-zero blocks of a file as its bytes go past. feed(offset, data) with each piece read from (or written to)
    the file, in order; close() punches what is still pending. A run of zero blocks is punched as one hole."""

    def __init__(self, fd):
        self.fd, self.start, self.end, self.ok = fd, None, None, True

    def feed(self, offset, data):
        if not self.ok:
            return
        view = memoryview(data)
        first = -(-offset // BLOCK) * BLOCK              # only whole, aligned blocks inside this piece
        for a in range(first - offset, len(view) - BLOCK + 1, BLOCK):
            at = offset + a
            if view[a:a + BLOCK] == _ZERO:
                if self.end == at:
                    self.end += BLOCK
                else:
                    self._flush()
                    self.start, self.end = at, at + BLOCK
            elif self.end is not None:
                self._flush()

    def _flush(self):
        if self.start is not None and self.ok:
            self.ok = punch(self.fd, self.start, self.end - self.start)
        self.start = self.end = None

    def close(self):
        self._flush()
        return self.ok


def write_sparse(fd, offset, data):
    """Write DATA at OFFSET into a file already truncated to its full size, leaving out the aligned 64 KiB blocks that are all
    zeros: those stay holes. -> bytes actually written."""
    view, wrote, run = memoryview(data), 0, None          # run: (start, end) within VIEW of bytes still to write
    first = min(len(view), -(-offset // BLOCK) * BLOCK - offset)
    if first:
        run = (0, first)                                 # a piece before the first aligned block: written as it is
    a = first
    while a < len(view):
        b = min(a + BLOCK, len(view))
        if b - a == BLOCK and view[a:b] == _ZERO:
            if run:
                wrote += _pwrite_all(fd, view[run[0]:run[1]], offset + run[0])
                run = None
        else:
            run = (run[0], b) if run else (a, b)
        a = b
    if run:
        wrote += _pwrite_all(fd, view[run[0]:run[1]], offset + run[0])
    return wrote


def _pwrite_all(fd, view, offset):
    done = 0
    while done < len(view):
        done += os.pwrite(fd, view[done:], offset + done)
    return done


def data_ranges(fd):
    """[(offset, length)] of the parts of the file that are not holes already (SEEK_DATA / SEEK_HOLE)."""
    out, end, off = [], os.fstat(fd).st_size, 0
    while off < end:
        try:
            d = os.lseek(fd, off, os.SEEK_DATA)
        except OSError:                                  # ENXIO: nothing but a hole from here on
            break
        off = os.lseek(fd, d, os.SEEK_HOLE)
        out.append((d, off - d))
    return out


def dig(path, chunk=4 << 20, on_progress=None):
    """Punch the all-zero blocks of an image already in the store. Only the parts that hold data are read, so an image that is
    mostly holes already takes little time. ON_PROGRESS(bytes looked at, bytes to look at). -> {bytes, before, after, freed, ok}:
    ok False when the filesystem can't make holes (nothing changed)."""
    fd = os.open(path, os.O_RDWR)
    try:
        before = allocated(fd)
        ranges = data_ranges(fd)
        total, done = sum(n for _, n in ranges), 0
        h = Holes(fd)
        step = max(BLOCK, chunk // BLOCK * BLOCK)
        for start, n in ranges:
            off = start
            while off < start + n and h.ok:
                stop = min(start + n, (off // BLOCK) * BLOCK + step)    # pieces end on a block boundary: no block is split
                piece = os.pread(fd, stop - off, off)
                if not piece:
                    break
                h.feed(off, piece)
                off += len(piece)
                done += len(piece)
                if on_progress:
                    on_progress(done, total)
            h._flush()                                   # a run never joins across a hole that was already there
        ok = h.close()
        os.fsync(fd)
        after = allocated(fd)
        return {"bytes": os.fstat(fd).st_size, "before": before, "after": after, "freed": max(before - after, 0), "ok": ok}
    finally:
        os.close(fd)
