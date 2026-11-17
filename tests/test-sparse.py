#!/usr/bin/env python3
"""gatboxlib/sparse.py: the zero blocks of an image become holes, and every byte reads back the same. Invented data only."""
import os
import random
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
from gatboxlib import sparse as SP  # noqa: E402

passed = failed = 0
KiB, MiB = 1024, 1024 * 1024


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def image(rng):
    """Data and zero runs of awkward lengths, so pieces and blocks never line up by luck."""
    out = bytearray()
    for _ in range(40):
        out += rng.randbytes(rng.randrange(1, 200 * KiB)) if rng.random() < 0.4 else bytes(rng.randrange(1, 900 * KiB))
    return bytes(out)


def took(p):
    return os.stat(p).st_blocks * 512


def main():
    rng = random.Random(20261117)
    tmp = tempfile.mkdtemp(prefix="gatbox-sparse-")
    data = image(rng)
    zeros = sum(1 for a in range(0, len(data) - SP.BLOCK + 1, SP.BLOCK) if data[a:a + SP.BLOCK] == bytes(SP.BLOCK)
                and a % SP.BLOCK == 0)
    print(f"an invented image: {len(data):,} bytes, {zeros} aligned zero blocks")

    print("write_sparse: pieces at odd offsets, the zero blocks left out")
    p = os.path.join(tmp, "copy")
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(fd, len(data))
    off, wrote = 0, 0
    while off < len(data):
        n = rng.randrange(1, 3 * MiB)
        wrote += SP.write_sparse(fd, off, data[off:off + n])
        off += n
    os.close(fd)
    check("every byte reads back the same", open(p, "rb").read() == data)
    check("it wrote less than the whole image, and the file takes less room than its size",
          wrote < len(data) and took(p) < len(data) - zeros * SP.BLOCK // 2)

    print("dig: an image written out in full")
    q = os.path.join(tmp, "full")
    open(q, "wb").write(data)
    before = took(q)
    r = SP.dig(q, chunk=rng.randrange(100 * KiB, 2 * MiB))
    check("its bytes are unchanged", open(q, "rb").read() == data)
    check("the room it takes fell by about its aligned zero blocks, and it says so",
          r["ok"] and r["before"] == before and r["after"] == took(q) and r["freed"] == before - r["after"]
          and r["freed"] >= zeros * SP.BLOCK * 0.9)
    r2 = SP.dig(q)
    check("a second pass frees nothing more, and reads only what holds data", r2["freed"] == 0 and open(q, "rb").read() == data)
    seen = []
    SP.dig(q, on_progress=lambda d, t: seen.append((d, t)))
    check("it says how far it has got, out of the data it has to look at (not the whole size)",
          seen and seen[-1][0] == seen[-1][1] < len(data))

    print("Holes: fed as the bytes go past")
    h = os.path.join(tmp, "fed")
    open(h, "wb").write(data)
    fd = os.open(h, os.O_RDWR)
    holes, off = SP.Holes(fd), 0
    while off < len(data):
        n = rng.randrange(1, 5 * MiB)
        holes.feed(off, data[off:off + n])
        off += n
    holes.close()
    os.close(fd)
    check("the same bytes, and about the same room as dig leaves", open(h, "rb").read() == data and abs(took(h) - took(q)) <= 2 * SP.BLOCK * 40)
    e = os.path.join(tmp, "empty")
    open(e, "wb").write(bytes(3 * MiB))
    check("an all-zero file: nothing left but holes", SP.dig(e)["after"] == 0 and open(e, "rb").read() == bytes(3 * MiB))
    print(f"sparse: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
