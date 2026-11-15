#!/usr/bin/env python3
"""F0 of the drive browser (docs/plans/2026-11-15-drive-browser.md): the facts to read off the Pi before any code.

    sg gatbox-disk-queue -c 'python3 docs/plans/2026-11-15-drive-browser-f0.py [IMAGE] [--bench-dir DIR]'

It looks inside one stored image (default: the newest *.img in the store) with the sleuthkit tools and reports what the
drive browser needs to know: which tools and versions are here, how the image is laid out, what each volume is, how long
a full listing takes and how much memory it needs, how fast a file comes out of the image, what the drive holds by kind
(photos, documents, other), how fast SQLite's full-text index is on this machine and in this folder, and how fast Pillow
makes a thumbnail.

READ-ONLY, and private. The image is opened only by mmls, fsstat, fls and icat, which read it and never change it: no
mount, no loop device, no drive, no dock. The only thing written is one temporary benchmark database, in --bench-dir
(default: the temporary folder), deleted when the script ends. The report holds counts, sizes, dates and timings. It
does not print a file name, a folder name, a volume label, a UUID or a serial number, so it can be pasted as it is.

Exit status: 0 done, 2 the image is missing or unreadable, 3 sleuthkit is not installed.
"""
import argparse
import glob
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter

STORE = os.environ.get("GATBOX_DISKS", "/srv/gatbox/disks")
TSK = ("mmls", "fsstat", "fls", "icat")                  # the only programs that ever open the image
PHOTO = {"jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "webp", "heic", "heif", "cr2", "nef", "arw", "dng", "raw"}
DOC = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "txt", "rtf", "md", "csv", "html", "htm", "epub"}
FS_LINES = re.compile(r"^(File System Type|Block Size|Sector Size|Cluster Size|Block Range|Total Range|Total Cluster Range|"
                      r"Free Blocks|Free Inodes|Inode Range|Free Sector Count)\b")
MMLS_ROW = re.compile(r"^\d+:\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$")
MiB = 1 << 20


def say(*a):
    print(*a, flush=True)


def head(title):
    say(f"\n== {title}")


def tsk(argv, timeout, cap=None):
    """Run one sleuthkit tool. Returns (exit code or None if it timed out, stdout bytes, stderr text, seconds, peak MB).
    cap: stop after that many bytes of output (icat of a big file) and count it as finished, not as a timeout."""
    assert argv[0] in TSK, argv[0]
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    killed = []
    timer = threading.Timer(timeout, lambda: (killed.append(1), p.kill()))
    timer.start()
    out = b""
    try:
        while True:
            chunk = p.stdout.read(65536)
            if not chunk:
                break
            out += chunk
            if cap is not None and len(out) >= cap:
                out = out[:cap]
                p.kill()
                break
    finally:
        timer.cancel()
    err = p.stderr.read().decode("utf-8", "replace")
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    rc = None if killed else p.returncode
    return rc, out, err, time.time() - t0, ru.ru_maxrss / 1024


def first_line(text):
    return (text.strip().splitlines() or [""])[0][:120]


def newest_image():
    found = glob.glob(os.path.join(STORE, "*", "*.img"))
    return max(found, key=os.path.getmtime) if found else None


def meminfo():
    out = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            out[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return out


def volumes(image):
    """[(label, start sector, length, description)] from mmls, or the whole image as one volume when there is no table."""
    rc, out, err, secs, _ = tsk(["mmls", image], 60)
    text = out.decode("utf-8", "replace")
    say(f"mmls: {'ran' if rc == 0 else 'no partition table found'} in {secs:.2f} s")
    if rc != 0:
        return [("whole image", 0, None, "no partition table: trying a filesystem at sector 0")], None
    kind = next((l for l in text.splitlines() if "Partition Table" in l or "Volume System" in l), "").strip()
    vols = []
    for line in text.splitlines():
        m = MMLS_ROW.match(line)
        if not m:
            continue
        slot, start, _, length, desc = m.groups()
        if slot in ("Meta", "-------") or desc.startswith(("DOS Extended", "Extended")):
            continue                                  # table sectors, gaps, and the extended container itself
        vols.append((f"slot {slot}", int(start), int(length), desc))
    return vols, kind


def fsstat(image, start):
    rc, out, err, secs, _ = tsk(["fsstat", "-o", str(start), image], 120)
    text = out.decode("utf-8", "replace")
    if rc != 0 or "File System Type" not in text:
        say(f"   no filesystem recognized ({first_line(err) or 'fsstat printed nothing'}) [{secs:.2f} s]")
        return False
    labelled = False
    for line in text.splitlines():
        if line.startswith(("Volume Name:", "Volume Label (Boot Sector):")):
            labelled = labelled or bool(line.partition(":")[2].strip()) and line.partition(":")[2].strip() != "NO NAME"
        if FS_LINES.match(line):
            say("   " + line.strip())
    say(f"   has a volume label: {'yes' if labelled else 'no'} (not printed)   fsstat took {secs:.2f} s")
    return True


def listing(image, start, timeout):
    """Stream `fls -r -p -m /` and count. Prints nothing about names. Returns sample inodes for the icat probes."""
    argv = ["fls", "-r", "-p", "-m", "/", "-o", str(start), image]
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    killed = []
    timer = threading.Timer(timeout, lambda: (killed.append(1), p.kill()))
    timer.start()
    c, kinds, size_by, exts = Counter(), Counter(), Counter(), Counter()
    children, depth, oldest, newest = Counter(), 0, None, None
    small = big = photo = None
    for raw in p.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        try:
            prefix, inode, mode, _, _, size, _, mtime, _, _ = line.rsplit("|", 9)
            name = prefix.split("|", 1)[1]
            size, mtime = int(size), int(mtime)
        except (ValueError, IndexError):
            c["unparsed"] += 1
            continue
        c["entries"] += 1
        deleted = name.endswith(" (deleted)")
        t = mode[:1]
        if deleted:
            c["deleted"] += 1
            continue
        if t == "d":
            c["folders"] += 1
        elif t == "r":
            c["files"] += 1
            ext = name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""
            kind = "photos" if ext in PHOTO else "documents" if ext in DOC else "other"
            kinds[kind] += 1
            size_by[kind] += size
            if ext:
                exts[ext] += 1
            if small is None and 1024 <= size <= MiB:
                small = (inode, size)
            if big is None and 8 * MiB <= size <= 200 * MiB:
                big = (inode, size)
            if photo is None and ext in ("jpg", "jpeg", "png") and 50 * 1024 <= size <= 20 * MiB:
                photo = (inode, size)
        elif t == "l":
            c["links"] += 1
        else:
            c["other entries"] += 1
            continue
        parent, _, _ = name.rpartition("/")
        children[parent] += 1
        depth = max(depth, name.count("/"))
        if 631152000 <= mtime <= 4102444800 and t == "r":          # 1990 to 2100; zero and junk dates are left out
            oldest = mtime if oldest is None else min(oldest, mtime)
            newest = mtime if newest is None else max(newest, mtime)
    timer.cancel()
    _, status, ru = os.wait4(p.pid, 0)
    secs = time.time() - t0
    p.returncode = os.waitstatus_to_exitcode(status)
    note = f"   STOPPED at the {timeout} s limit: the counts below are partial" if killed else ""
    say(f"fls -r: {c['entries']:,} entries in {secs:.1f} s ({c['entries'] / max(secs, 0.001):,.0f}/s), peak memory {ru.ru_maxrss / 1024:.0f} MB"
        + (f", exit {p.returncode}" if p.returncode not in (0, None) and not killed else ""))
    if note:
        say(note)
    say(f"   files {c['files']:,}  folders {c['folders']:,}  links {c['links']:,}  deleted entries {c['deleted']:,} (not counted below)"
        + (f"  unparsed lines {c['unparsed']:,}" if c["unparsed"] else ""))
    for k in ("photos", "documents", "other"):
        say(f"   {k:10} {kinds[k]:>9,} files  {size_by[k] / MiB:>12,.1f} MB")
    say(f"   deepest path {depth} folders down; most entries in one folder {max(children.values(), default=0):,}")
    if oldest:
        say(f"   file dates (modified): oldest {time.strftime('%Y-%m-%d', time.gmtime(oldest))}, newest {time.strftime('%Y-%m-%d', time.gmtime(newest))}")
    say("   commonest extensions: " + (", ".join(f".{e} {n:,}" for e, n in exts.most_common(8)) or "none"))
    return small, big, photo


def icat_probe(image, start, label, pick, cap, timeout=120):
    if not pick:
        say(f"icat {label}: no such file on this volume")
        return None
    inode, size = pick
    rc, out, err, secs, rss = tsk(["icat", "-o", str(start), image, inode], timeout, cap=cap)
    got = len(out)
    say(f"icat {label}: inode {inode}, {size:,} bytes in the file, read {got:,} in {secs:.2f} s "
        f"({got / MiB / max(secs, 0.001):,.1f} MB/s), peak memory {rss:.0f} MB")
    return out


def fts_bench(bench_dir, n=100_000):
    head(f"SQLite full-text index ({n:,} synthetic paths, in {bench_dir})")
    try:
        c = sqlite3.connect(":memory:")
        c.execute("create virtual table t using fts5(x)")
        c.close()
    except sqlite3.Error as e:
        say(f"FTS5 is NOT available in this SQLite ({sqlite3.sqlite_version}): {e}")
        return
    work = tempfile.mkdtemp(prefix=".f0-bench-", dir=bench_dir)
    try:
        db = os.path.join(work, "bench.sqlite")
        c = sqlite3.connect(db)
        c.execute("create virtual table t using fts5(path, tokenize='unicode61 remove_diacritics 2')")
        words = ["holiday", "invoice", "report", "scan", "photo", "tax", "family", "school", "backup", "notes", "2019", "2021", "draft"]
        t0 = time.time()
        with c:
            c.executemany("insert into t(path) values (?)",
                          ((f"/{words[i % 13]}/{words[(i // 13) % 13]}{i % 997}/{words[(i // 7) % 13]}_{i}.pdf",) for i in range(n)))
        ins = time.time() - t0
        c.close()
        c = sqlite3.connect(db)
        t0 = time.time()
        hits = c.execute("select count(*) from t where t match 'invoice scan'").fetchone()[0]
        one = time.time() - t0
        t0 = time.time()
        c.execute("select rowid from t where t match 'holiday*' limit 50").fetchall()
        pre = time.time() - t0
        c.close()
        size = os.path.getsize(db) / MiB
        say(f"SQLite {sqlite3.sqlite_version}, FTS5 yes: inserted {n:,} rows in {ins:.1f} s ({n / max(ins, 0.001):,.0f}/s), "
            f"database {size:.1f} MB ({size * 10:.0f} MB per million files)")
        say(f"   a two-word search over {n:,} rows: {one * 1000:.0f} ms ({hits:,} hits); a prefix search, first 50: {pre * 1000:.0f} ms")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def pillow_bench(sample=None):
    head("Pillow (thumbnails)")
    try:
        from PIL import Image, __version__
    except ImportError:
        say("Pillow is NOT installed (python3-pil)")
        return
    raw = io.BytesIO()
    Image.effect_noise((4000, 3000), 40).convert("RGB").save(raw, "JPEG", quality=90)
    jpeg = raw.getvalue()

    def thumb(data, draft):
        im = Image.open(io.BytesIO(data))
        if draft:
            im.draft("RGB", (512, 512))
        im.thumbnail((256, 256))
        im.convert("RGB")
    for label, draft in (("256 px thumbnail, with JPEG draft mode", True), ("256 px thumbnail, full decode", False)):
        t0 = time.time()
        for _ in range(5):
            thumb(jpeg, draft)
        say(f"Pillow {__version__}, a {len(jpeg) / MiB:.1f} MB 4000x3000 JPEG: {label}: {(time.time() - t0) / 5 * 1000:.0f} ms each")
    if sample:
        try:
            t0 = time.time()
            im = Image.open(io.BytesIO(sample))
            dims = im.size
            thumb(sample, True)
            say(f"a real photo from the image ({dims[0]}x{dims[1]}): thumbnail in {(time.time() - t0) * 1000:.0f} ms")
        except Exception as e:                                          # a damaged or unusual photo is a fact too
            say(f"a real photo from the image could not be decoded: {type(e).__name__}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", nargs="?", help="a stored image (default: the newest in the store)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds a full listing may take per volume (default 1800)")
    ap.add_argument("--bench-dir", default=tempfile.gettempdir(),
                    help="where the temporary benchmark database goes; use the store to measure its write speed")
    ap.add_argument("--skip-listing", action="store_true", help="skip the full listing (partitions and fsstat only)")
    a = ap.parse_args()

    say("F0: the drive browser's facts. READ-ONLY. Counts, sizes, dates and timings only: no file, folder or volume names.")
    head("Tools")
    missing = [t for t in TSK if not shutil.which(t)]
    if missing:
        say(f"NOT INSTALLED: {', '.join(missing)}. Run the bootstrap (it installs sleuthkit), then run this again.")
        return 3
    say(first_line(subprocess.run(["fls", "-V"], capture_output=True, text=True).stdout))
    say(f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}, machine {os.uname().machine}")
    m = meminfo()
    say(f"memory: {m.get('MemTotal', 0)} MB total, {m.get('MemAvailable', 0)} MB available")
    for label, path in (("the SD card (/)", "/"), ("the store", STORE)):
        try:
            u = shutil.disk_usage(path)
            say(f"free space on {label}: {u.free / 1e9:,.1f} GB of {u.total / 1e9:,.1f} GB")
        except OSError:
            say(f"free space on {label}: not available")

    image = a.image or newest_image()
    head("The image")
    if not image or not os.path.isfile(image):
        say(f"no image found{' at ' + a.image if a.image else ' in ' + STORE}")
        return 2
    try:
        with open(image, "rb") as f:
            f.read(512)
    except OSError as e:
        say(f"cannot read {os.path.basename(image)}: {e.strerror}. Run this as: sg gatbox-disk-queue -c 'python3 {sys.argv[0]}'")
        return 2
    say(f"{os.path.basename(image)}: {os.path.getsize(image):,} bytes ({os.stat(image).st_blocks * 512 / 1e9:,.1f} GB on disk)")

    head("Layout")
    vols, kind = volumes(image)
    if kind:
        say(kind)
    if not vols:
        say("no partitions found")
    sample_photo = None
    for label, start, length, desc in vols:
        say(f"\n{label}: {desc}, starts at sector {start}" + (f", {length:,} sectors ({length * 512 / 1e9:,.1f} GB)" if length else ""))
        if not fsstat(image, start):
            continue
        if a.skip_listing:
            continue
        small, big, photo = listing(image, start, a.timeout)
        icat_probe(image, start, "a small file", small, 64 * 1024)
        icat_probe(image, start, "a big file", big, 64 * MiB)
        sample_photo = icat_probe(image, start, "a photo", photo, 20 * MiB) or sample_photo

    fts_bench(a.bench_dir)
    pillow_bench(sample_photo)
    say("\nDone. Nothing was changed. Paste this whole report back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
