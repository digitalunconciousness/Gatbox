#!/usr/bin/env python3
"""The drive browser's F0 script (docs/plans/2026-11-15-drive-browser-f0.py) against invented images built here: an ext4
volume and a swap partition behind a DOS table, a FAT32 volume with a volume label, and a file of random bytes. What is
checked is what the owner will rely on when she pastes the report from her Pi: the numbers are right, the report never
carries a file name, a folder name or a volume label, the image is not changed, the temporary benchmark database is
gone, and the failure paths (no sleuthkit, no image, a hung listing) say so and stop.

Needs the sleuthkit tools, mke2fs, mkfs.fat and mtools. Without them it skips, saying which.
    python3 tests/test-drive-f0.py
"""
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "docs/plans/2026-11-15-drive-browser-f0.py")
T = tempfile.mkdtemp(prefix="gatbox-f0-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def need():
    gone = [t for t in ("fls", "mmls", "fsstat", "icat", "mke2fs", "mkfs.fat", "mcopy") if not shutil.which(t) and not os.path.exists("/usr/sbin/" + t)]
    return gone


def mbr(path, parts):
    """A DOS partition table: [(start sector, sectors, type, bootable)]."""
    with open(path, "r+b") as f:
        sec = bytearray(512)
        for i, (start, size, typ, boot) in enumerate(parts):
            sec[446 + 16 * i:446 + 16 * (i + 1)] = struct.pack("<B3sB3sII", 0x80 if boot else 0, b"\0\0\0", typ, b"\0\0\0", start, size)
        sec[510:512] = b"\x55\xaa"
        f.seek(0)
        f.write(sec)


def tree(root):
    """Invented content, with names the report must never repeat."""
    for d in ("Photos/2019", "Docs", "empty", "odd|name dir"):
        os.makedirs(f"{root}/{d}")
    try:
        from PIL import Image
        Image.effect_noise((800, 600), 60).convert("RGB").save(f"{root}/Photos/2019/IMG_0001.jpg", "JPEG", quality=85)
        real_photo = True
    except ImportError:
        open(f"{root}/Photos/2019/IMG_0001.jpg", "wb").write(os.urandom(70000))
        real_photo = False
    open(f"{root}/Docs/zebra-summary.pdf", "wb").write(os.urandom(12000))
    open(f"{root}/Docs/notes.txt", "w").write("hello world notes\n")
    open(f"{root}/Docs/budget.xlsx", "wb").write(os.urandom(5000))
    open(f"{root}/big.bin", "wb").write(os.urandom(9 * MiB))
    open(f"{root}/odd|name dir/pipe|file.TXT", "w").write("x\n")
    os.symlink("Docs/notes.txt", f"{root}/link.txt")
    return real_photo


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(MiB), b""):
            h.update(b)
    return h.hexdigest()


def run(*args, env=None, timeout=240):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, **(env or {})))


def main():
    gone = need()
    if gone:
        print(f"  skip  the F0 script's tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, dosfstools, mtools)")
        return 0
    src = f"{T}/tree"
    os.makedirs(src)
    real_photo = tree(src)
    NAMES = ["IMG_0001", "zebra-summary", "notes", "budget", "Photos", "Docs", "big.bin", "link.txt", "odd|name", "pipe|file", "PHOTOS", "empty"]

    ext = f"{T}/ext.img"
    with open(ext, "wb") as f:
        f.truncate(128 * MiB)
    mbr(ext, [(2048, 200000, 0x83, True), (202048, 40000, 0x82, False)])
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", "-d", src, "-E", f"offset={2048 * 512}", ext, "100000k"], check=True)
    fat = f"{T}/fat.img"
    with open(fat, "wb") as f:
        f.truncate(96 * MiB)
    subprocess.run(["mkfs.fat", "-F", "32", "--offset", "2048", "-n", "PHOTOS", fat, "90000"], check=True, capture_output=True)
    subprocess.run(["mcopy", "-s", "-i", f"{fat}@@{2048 * 512}", f"{src}/Photos", f"{src}/Docs", "::/"], check=True,
                   env=dict(os.environ, MTOOLS_SKIP_CHECK="1"))
    mbr(fat, [(2048, 180000, 0x0C, True)])
    rnd = f"{T}/random.img"
    open(rnd, "wb").write(os.urandom(4 * MiB))

    print("an ext4 volume and a swap partition behind a DOS table:")
    before = (sha(ext), os.stat(ext).st_mtime_ns)
    bench = f"{T}/bench"
    os.makedirs(bench)
    r = run(ext, "--bench-dir", bench)
    out = r.stdout
    check("it finishes (exit 0) and says it read nothing but counts and timings", r.returncode == 0 and "Nothing was changed" in out)
    check("tools and the machine: sleuthkit's version, Python, SQLite, memory, free space",
          "The Sleuth Kit ver" in out and "SQLite" in out and "memory:" in out and "free space on the SD card" in out)
    check("layout: a DOS table, the Linux volume found, the swap partition tried and said to have no filesystem",
          "DOS Partition Table" in out and "Linux (0x83)" in out and "Linux Swap" in out and "no filesystem recognized" in out)
    check("the ext4 volume: its type, block size and free blocks from fsstat", "File System Type: Ext4" in out
          and "Block Size: 4096" in out and "Free Blocks:" in out)
    check("the listing counts right: 5 files, 6 folders, 1 link, nothing deleted",
          "files 6" in out and "folders 6" in out and "links 1" in out and "deleted entries 0" in out)
    check("... by kind: 1 photo, 4 documents (pdf, txt, xlsx, and the .TXT whose name has a pipe in it), 1 other",
          re.search(r"photos\s+1 files", out) and re.search(r"documents\s+4 files", out) and re.search(r"other\s+1 files", out))
    check("... the shape: how deep, the extensions, the dates", "deepest path 3 folders down" in out and ".txt 2" in out
          and "file dates (modified)" in out)
    check("icat: a small file, a big file (MB/s) and a photo come out of the image", "icat a small file: inode" in out
          and "icat a big file: inode" in out and "MB/s" in out and "icat a photo: inode" in out)
    check("the FTS5 benchmark ran, and the Pillow one", "FTS5 yes: inserted 100,000 rows" in out and "thumbnail" in out)
    if real_photo:
        check("... on a real photo from the image: decoded and thumbnailed", "a real photo from the image (800x600)" in out)
    leaked = [n for n in NAMES if n in out]
    check("NO file name, folder name or volume label is in the report", not leaked)
    if leaked:
        print("        leaked:", leaked)
    check("the image is byte-for-byte and time-for-time unchanged", (sha(ext), os.stat(ext).st_mtime_ns) == before)
    check("the benchmark database is gone: the bench folder is empty", os.listdir(bench) == [])

    print("a FAT32 volume with a label:")
    r = run(fat, "--bench-dir", bench)
    out = r.stdout
    check("FAT32 is found and listed: its type, sector and cluster size, free sectors, 2 photos' worth of files",
          r.returncode == 0 and "File System Type: FAT32" in out and "Cluster Size:" in out and "Free Sector Count" in out
          and re.search(r"photos\s+1 files", out) and re.search(r"documents\s+3 files", out))
    check("the volume label is reported as present, never printed", "has a volume label: yes (not printed)" in out
          and "PHOTOS" not in out and "Volume Label" not in out)

    print("no partition table, no filesystem:")
    r = run(rnd, "--bench-dir", bench)
    check("random bytes: no partition table, no filesystem recognized, and it still finishes with the other benchmarks",
          r.returncode == 0 and "no partition table found" in r.stdout and "no filesystem recognized" in r.stdout
          and "FTS5 yes" in r.stdout)

    print("the ways it stops:")
    r = run(ext, "--skip-listing", "--bench-dir", bench)
    check("--skip-listing: partitions and fsstat only, no listing", r.returncode == 0 and "fls -r:" not in r.stdout
          and "File System Type: Ext4" in r.stdout)
    r = run(f"{T}/nope.img")
    check("no such image -> exit 2, says so", r.returncode == 2 and "no image found" in r.stdout)
    if os.geteuid() != 0:
        os.chmod(rnd, 0)
        r = run(rnd)
        os.chmod(rnd, 0o644)
        check("an image this user cannot read -> exit 2, with the sg command to use", r.returncode == 2 and "sg gatbox-disk-queue" in r.stdout)
    else:
        print("  skip  an unreadable image (this is root, which can read anything)")
    empty = f"{T}/empty-path"
    os.makedirs(empty)
    r = subprocess.run([sys.executable, SCRIPT, ext], capture_output=True, text=True, env=dict(os.environ, PATH=empty))
    check("without sleuthkit on the PATH -> exit 3, 'NOT INSTALLED', and to run the bootstrap", r.returncode == 3
          and "NOT INSTALLED" in r.stdout and "bootstrap" in r.stdout)
    slow = f"{T}/slowbin"
    os.makedirs(slow)
    open(f"{slow}/fls", "w").write("#!/bin/sh\nsleep 30\n")
    os.chmod(f"{slow}/fls", 0o755)
    r = run(ext, "--timeout", "1", "--bench-dir", bench, env={"PATH": slow + ":" + os.environ["PATH"]}, timeout=120)
    check("a listing that hangs is killed at its limit, said to be partial, and the rest of the report still comes",
          r.returncode == 0 and "STOPPED at the 1 s limit" in r.stdout and "FTS5 yes" in r.stdout)

    print("what it is allowed to do:")
    src_text = open(SCRIPT).read()
    code = "\n".join(l for l in src_text.splitlines() if not l.lstrip().startswith("#"))
    code = code.split('"""', 2)[2]                                  # the docstring explains what it does not do
    banned = [w for w in ("mount", "umount", "losetup", "mkfs", "wipefs", "sfdisk", "blkdiscard", "dd", "chmod", "chown", "sudo")
              if re.search(rf"\b{w}\b", code)]
    check("the script's code never names a mount, a loop device, a format, a partitioner, dd or sudo", not banned)
    if banned:
        print("        found:", banned)
    check("the only programs it starts are sleuthkit's: every call goes through the one function that refuses any other",
          'assert argv[0] in TSK' in src_text and 'TSK = ("mmls", "fsstat", "fls", "icat")' in src_text)

    print(f"\ndrive browser F0: {passed} passed, {failed} failed")
    return 1 if failed else 0


try:
    code = main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(code)
