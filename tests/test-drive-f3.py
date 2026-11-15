#!/usr/bin/env python3
"""The drive browser's F3 measurement script (docs/plans/2026-11-15-drive-browser-f3.py) against an invented ext4 image with
documents of several kinds. What is checked is what the owner will rely on when she pastes the report from her Pi: the counts
are right, the three runs and the projection are there, the text readers are timed, the report never carries a file name, a
folder name or a path, the image is not changed, the copies it made are gone, and the failure paths (no sleuthkit, no image, a
hung icat, an image with no documents) say so and stop.

Needs the sleuthkit tools, mke2fs and Pillow; without them it skips, saying which.
    python3 tests/test-drive-f3.py
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "docs/plans/2026-11-15-drive-browser-f3.py")
sys.path.insert(0, os.path.join(REPO, "tests"))
import fakeimage as F  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-f3-test-")
passed = failed = 0
MiB = 1 << 20


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(MiB), b""):
            h.update(b)
    return h.hexdigest()


def run(*args, env=None, timeout=300):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **(env or {})))


def main():
    gone = F.missing()
    try:
        import PIL  # noqa: F401
    except ImportError:
        gone.append("python3-pil")
    if gone:
        print(f"  skip  the F3 script's tests need {', '.join(gone)} (apt: sleuthkit, e2fsprogs, python3-pil)")
        return 0
    tree = f"{T}/tree"
    for d in ("Zebra-Manuals", "Quarterly Taxes/2019", "pics", "empty"):
        os.makedirs(f"{tree}/{d}")
    NAMES = ["Zebra", "Quarterly", "Taxes", "manual-alpha", "invoice", "holiday", "secret-plans", "budget", "IMG_0042", "pics", "slides-final"]
    for i in range(40):
        open(f"{tree}/Zebra-Manuals/manual-alpha-{i}.txt", "w").write(f"service manual {i} " + "word " * (200 + i * 50))
    for i in range(6):
        open(f"{tree}/Quarterly Taxes/2019/invoice-{i}.html", "w").write(f"<html><body><p>invoice {i}</p></body></html>")
    with zipfile.ZipFile(f"{tree}/Quarterly Taxes/holiday-budget.docx", "w") as z:
        z.writestr("word/document.xml", "<w:p><w:r><w:t>holiday budget</w:t></w:r></w:p>")
    with zipfile.ZipFile(f"{tree}/secret-plans.xlsx", "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>secret plans</t></si></sst>")
    open(f"{tree}/slides-final.ppt", "wb").write(os.urandom(3000))                # a reader exists; this is not a real .ppt
    open(f"{tree}/IMG_0042.jpg", "wb").write(os.urandom(5000))                    # no reader: not counted
    open(f"{tree}/pics/notes.bin", "wb").write(os.urandom(5000))
    os.symlink("secret-plans.xlsx", f"{tree}/link.txt")                            # a link is not a file
    open(f"{tree}/zero.txt", "w").close()                                          # an empty file is not counted
    readable_files = 40 + 6 + 1 + 1 + 1
    ext = f"{T}/e.img"
    F.ext4_image(ext, tree)
    swapless = f"{T}/s.img"
    shutil.copy(ext, swapless)
    rnd = f"{T}/r.img"
    open(rnd, "wb").write(os.urandom(2 * MiB))
    plain = f"{T}/plain-tree"
    os.makedirs(plain)
    open(f"{plain}/a.jpg", "wb").write(os.urandom(2000))
    nodocs = f"{T}/n.img"
    F.ext4_image(nodocs, plain)

    print("an ext4 image with documents:")
    before = (sha(ext), os.stat(ext).st_mtime_ns)
    bench = f"{T}/bench"
    os.makedirs(bench)
    r = run(ext, "--files", "30", "--bench-dir", bench)
    out = r.stdout
    check("it finishes (exit 0) and says it changed nothing", r.returncode == 0 and "Nothing was changed" in out)
    check(f"the documents are counted: {readable_files} with a reader, by extension; the picture, the link and the empty file are not",
          f"files with a text reader: {readable_files}," in out and re.search(r"\.txt\s+40 files", out) and re.search(r"\.html\s+6 files", out)
          and re.search(r"\.docx\s+1 files", out) and ".jpg" not in out and ".bin" not in out)
    check("the sample is the 30 asked for, read three times: random, inode, random again, each with its per-file spread and MB/s",
          "Reading 30 random documents" in out and all(re.search(rf"{x}: 30 files", out) for x in ("run 1, random order", "run 2, inode order", "run 3, random order again"))
          and out.count("per file: median") == 3 and "MB/s overall" in out and "first file read of the first run" in out)
    check("the noise between two runs in the same order is reported next to inode order's difference, with the projection for every document",
          re.search(r"two runs in the same order differ by \d+%", out) and re.search(r"reading all 49 documents in random order: about", out)
          and re.search(r"reading all 49 documents in inode order: about", out))
    check("the readers are named, installed or not", all(f"{t}:" in out for t in ("pdftotext", "catdoc", "xls2csv", "catppt")))
    check("... and extraction is compared with reading: 'extracting the text takes ... on average per file here'", "extracting the text takes" in out)
    leaked = [n for n in NAMES if n in out]
    check("NO file name, folder name or path is in the report (the image's own folder and name are not either)", not leaked and T not in out and "e.img" not in out)
    if leaked:
        print("        leaked:", leaked)
    check("the image is byte-for-byte and time-for-time unchanged", (sha(ext), os.stat(ext).st_mtime_ns) == before)
    check("the copies made for the readers are gone: the bench folder is empty", os.listdir(bench) == [])

    print("fewer documents than asked for:")
    r = run(ext, "--files", "500", "--bench-dir", bench)
    check("all of them are the sample, and it says so", r.returncode == 0 and "only 49 documents here: all of them are the sample" in r.stdout
          and "Reading 49 random documents" in r.stdout)
    rows = {m.group(1): (int(m.group(2)), int(m.group(3)), m.group(4)) for m in re.finditer(r"^\.(\w+)\s+(\d+)\s+(\d+)\s+[\d.]+\s+[\d.]+\s+[\d,]+\s+(.*)$", r.stdout, re.M)}
    check("the text readers are timed per kind: txt, html, docx and xlsx are read; the fake .ppt is tried, not read, and says why",
          rows.get("txt", (0, 0))[:2] == (8, 8) and rows.get("html", (0, 0))[:2] == (6, 6) and rows.get("docx", (0, 0))[:2] == (1, 1)
          and rows.get("xlsx", (0, 0))[:2] == (1, 1) and rows.get("ppt", (0, 9))[:2] == (1, 0) and re.search(r"damaged|no text|no reader", rows.get("ppt", (0, 0, ""))[2]))

    print("no documents, no filesystem, and the ways it stops:")
    r = run(nodocs, "--bench-dir", bench)
    check("a volume with no document a reader could read: says so and stops cleanly (exit 0)", r.returncode == 0 and "no document with a reader" in r.stdout)
    r = run(rnd, "--bench-dir", bench)
    check("an image with no filesystem: says so and stops cleanly (exit 0)", r.returncode == 0 and "no volume with a filesystem" in r.stdout)
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
    check("without sleuthkit on the PATH -> exit 3, 'NOT INSTALLED', and to run the bootstrap", r.returncode == 3 and "NOT INSTALLED" in r.stdout and "bootstrap" in r.stdout)
    slow = f"{T}/slowbin"
    os.makedirs(slow)
    open(f"{slow}/icat", "w").write("#!/bin/sh\nsleep 30\n")                       # a grandchild that holds the pipe: the whole group must be killed
    os.chmod(f"{slow}/icat", 0o755)
    r = run(ext, "--files", "4", "--timeout", "1", "--bench-dir", bench, env={"PATH": slow + ":" + os.environ["PATH"]}, timeout=200)
    check("an icat that hangs is killed at its limit and counted, and the report still comes (exit 0)", r.returncode == 0 and "timed out" in r.stdout
          and "no file could be read" in r.stdout and "Nothing was changed" in r.stdout)

    print("what it is allowed to do:")
    src = open(SCRIPT).read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")).split('"""', 2)[2]
    banned = [w for w in ("mount", "umount", "losetup", "mkfs", "wipefs", "sfdisk", "blkdiscard", "dd", "chmod", "chown", "sudo") if re.search(rf"\b{w}\b", code)]
    check("the script's code never names a mount, a loop device, a format, a partitioner, dd or sudo", not banned)
    check("the only programs it starts itself are sleuthkit's, through the one function that refuses any other (the readers start theirs through the browse core)",
          'assert argv[0] in TSK' in src and 'TSK = ("mmls", "fsstat", "fls", "icat")' in src and src.count("subprocess.Popen(") == 2
          and src.count("subprocess.run(") == 1)
    print(f"\ndrive browser F3 measurement: {passed} passed, {failed} failed")
    return 1 if failed else 0


try:
    code = main()
finally:
    shutil.rmtree(T, ignore_errors=True)
sys.exit(code)
