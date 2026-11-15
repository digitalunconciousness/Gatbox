#!/usr/bin/env python3
"""F3 of the drive browser (docs/plans/2026-11-15-drive-browser.md): the measurement to read off the Pi before the text search
is designed.

    sg gatbox-disk-queue -c 'python3 docs/plans/2026-11-15-drive-browser-f3.py [IMAGE] [--files 200] [--volume SECTOR]'

The text search has to read every document out of an image, and reading a file out of an image is the slow part (F0: about
6 MB/s on a large file, a fifth of a second to three seconds on a small one, three samples). A drive with 50,000 documents
is hours at three seconds each and minutes at a fifth of a second. This script finds out which, on the owner's drive:

  1. It lists one volume and counts the files a text reader could read (by extension), and how big they are.
  2. It takes a random sample (default 200) and reads each file out of the image with icat three times over, in three
     orders: random, inode order (which keeps the drive's head in one area), and random again. The two random runs show how
     much two identical runs differ, so that the difference with inode order can be told from noise. The image's cached pages
     are dropped before each run (posix_fadvise DONTNEED, a hint to the kernel and nothing more).
  3. It runs the text reader (backend/gatboxlib/browsetext.py, the one the browser will use) on up to 8 of the files of each
     kind and times it, so that the cost of reading and the cost of extracting can be told apart.
  4. It projects the time for every document in the volume from the sample, both ways.

READ-ONLY, and private. The image is opened only by icat, mmls, fsstat and fls, which read it and never change it: no mount,
no loop device, no drive, no dock. Copies of a few files are made in --bench-dir (default: the temporary folder) for the text
readers, and deleted. The report holds counts, sizes and timings by extension. It prints no file name, folder name, volume
label or path, so it can be pasted as it is. Run it with the dock idle: it reads the store's drive for several minutes.

Exit status: 0 done, 2 the image is missing or unreadable, 3 sleuthkit is not installed.
"""
import argparse
import glob
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "backend"))

STORE = os.environ.get("GATBOX_DISKS", "/srv/gatbox/disks")
TSK = ("mmls", "fsstat", "fls", "icat")                  # the only programs this script starts; the text readers start theirs through the browse core
MMLS_ROW = re.compile(r"^\d+:\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$")
MiB = 1 << 20
KEEP_PER_KIND = 8                                        # files of each kind whose reading is timed
KEEP_BYTES = 4 * MiB                                     # ... and no larger than this, so that the copies stay small
SEED = 20261115


def say(*a):
    print(*a, flush=True)


def head(title):
    say(f"\n== {title}")


def stop(p):
    """Kill the tool and anything it started (it runs in its own session, so a child holding the pipe open goes too)."""
    try:
        os.killpg(p.pid, 9)
    except (ProcessLookupError, PermissionError):
        p.kill()


def tsk(argv, timeout, stdout=None):
    """Run one sleuthkit tool. Returns (exit code or None if it timed out, stdout bytes, stderr text, seconds)."""
    assert argv[0] in TSK, argv[0]
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    killed = []
    timer = threading.Timer(timeout, lambda: (killed.append(1), stop(p)))
    timer.start()
    try:
        out = p.stdout.read()
    finally:
        timer.cancel()
    err = p.stderr.read(4096).decode("utf-8", "replace")
    p.wait()
    return (None if killed else p.returncode), out, err, time.time() - t0


def first_line(text):
    return (text.strip().splitlines() or [""])[0][:120]


def newest_image():
    found = glob.glob(os.path.join(STORE, "*", "*.img"))
    return max(found, key=os.path.getmtime) if found else None


def volumes(image):
    """[(start sector, length)] of the volumes with a filesystem, largest first."""
    rc, out, _, _ = tsk(["mmls", image], 60)
    rows = [(0, None)]
    if rc == 0:
        rows = []
        for line in out.decode("utf-8", "replace").splitlines():
            m = MMLS_ROW.match(line)
            if m and m.group(1) not in ("Meta", "-------") and not m.group(5).startswith(("DOS Extended", "Extended")):
                rows.append((int(m.group(2)), int(m.group(4))))
    found = []
    for start, length in rows:
        rc, out, _, _ = tsk(["fsstat", "-o", str(start), image], 120)
        if rc == 0 and b"File System Type" in out:
            found.append((start, length or 0))
    return sorted(found, key=lambda v: -v[1])


def candidates(image, start, timeout):
    """(inode, size, extension) of every live file a text reader could read, and how many entries were listed. Names are
    parsed to take the extension and dropped."""
    from gatboxlib import browse as B
    from gatboxlib import browsetext as X
    argv = ["fls", "-r", "-p", "-m", "/", "-o", str(start), image]
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    killed = []
    timer = threading.Timer(timeout, lambda: (killed.append(1), stop(p)))
    timer.start()
    found, entries, too_big = [], 0, 0
    try:
        for raw in p.stdout:
            got = B.parse_body(raw.decode("utf-8", "replace").rstrip("\n"))
            if got is None:
                continue
            name, inode, mode, size, _ = got
            entries += 1
            if mode[:1] != "r" or name.endswith(" (deleted)") or size <= 0:
                continue
            ext = X.extension(name.rsplit("/", 1)[-1])
            if ext not in X.READERS:
                continue
            if size > B.PREVIEW_CAP:
                too_big += 1
                continue
            found.append((inode, size, ext))
    finally:
        timer.cancel()
        p.stdout.close()
        p.wait()
    return found, entries, too_big, time.time() - t0, bool(killed)


def drop_cache(image):
    try:
        fd = os.open(image, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    except OSError:
        pass


def one_pass(image, start, files, keep, timeout):
    """Read each (inode, size, ext) out of the image with icat, in the order given. Returns the per-file seconds and bytes,
    how many failed or timed out, and (for pass 1 only, `keep` given) the bytes of the files to time the readers on."""
    secs, got, failed, timed_out, kept = [], 0, 0, 0, {}
    per_kind = Counter()
    for inode, size, ext in files:
        rc, out, _, s = tsk(["icat", "-o", str(start), image, inode], timeout)
        if rc is None:
            timed_out += 1
            continue
        if rc != 0 and not out:
            failed += 1
            continue
        secs.append(s)
        got += len(out)
        if keep is not None and per_kind[ext] < KEEP_PER_KIND and len(out) <= KEEP_BYTES:
            kept.setdefault(ext, []).append(out)
            per_kind[ext] += 1
    return secs, got, failed, timed_out, kept


def summary(label, secs, got, failed, timed_out, wall):
    if not secs:
        say(f"{label}: no file could be read ({failed} failed, {timed_out} timed out)")
        return None
    s = sorted(secs)
    p90 = s[min(len(s) - 1, int(len(s) * 0.9))]
    say(f"{label}: {len(secs)} files, {got / MiB:,.1f} MB in {wall:,.1f} s ({got / MiB / wall:,.1f} MB/s overall, {len(secs) / wall:,.2f} files/s)")
    say(f"   per file: median {statistics.median(s):.3f} s, mean {statistics.mean(s):.3f} s, 90th percentile {p90:.3f} s, slowest {s[-1]:.2f} s; "
        f"{sum(1 for x in s if x > 1)} took over 1 s, {sum(1 for x in s if x > 3)} over 3 s"
        + (f"; {failed} failed, {timed_out} timed out" if failed or timed_out else ""))
    return {"mean": statistics.mean(s), "wall": wall, "got": got, "n": len(secs)}


def readers(kept, bench):
    """Time the text reader on the copies; no names, only extension, seconds and characters."""
    from gatboxlib import browsetext as X
    head("Extracting text (the browser's own readers, one file at a time)")
    for tool in ("pdftotext", "catdoc", "xls2csv", "catppt"):
        say(f"{tool}: {'installed' if shutil.which(tool) else 'NOT INSTALLED (files that need it are counted as unreadable)'}")
    all_secs, kinds = [], defaultdict(lambda: {"n": 0, "ok": 0, "why": Counter(), "secs": [], "chars": [], "cut": 0})
    for ext in sorted(kept):
        for data in kept[ext]:
            path = os.path.join(bench, f"copy.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            t0 = time.time()
            k = kinds[ext]
            k["n"] += 1
            try:
                text, cut = X.extract(path, ext)
                k["ok"] += 1
                k["chars"].append(len(text))
                k["cut"] += cut
            except X.Unreadable as e:
                k["why"][e.code] += 1
            s = time.time() - t0
            k["secs"].append(s)
            all_secs.append(s)
            os.remove(path)
    if not kinds:
        say("no file of a readable kind was kept to try")
        return None
    say(f"{'kind':<6}{'tried':>6}{'read':>6}   {'median s':>9}{'slowest s':>10}{'median chars':>13}   not read (why)")
    for ext, k in sorted(kinds.items()):
        why = ", ".join(f"{c} {n}" for c, n in k["why"].most_common()) or "-"
        say(f".{ext:<5}{k['n']:>6}{k['ok']:>6}   {statistics.median(k['secs']):>9.3f}{max(k['secs']):>10.2f}"
            f"{(int(statistics.median(k['chars'])) if k['chars'] else 0):>13,}   {why}" + (f"; {k['cut']} cut at 1 MB" if k["cut"] else ""))
    return statistics.mean(all_secs)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", nargs="?", help="a stored image (default: the newest in the store)")
    ap.add_argument("--files", type=int, default=200, help="documents in the sample (default 200)")
    ap.add_argument("--volume", type=int, help="start sector of the volume (default: the largest with a filesystem)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds one file may take to read (default 120)")
    ap.add_argument("--list-timeout", type=int, default=1800, help="seconds the listing may take (default 1800)")
    ap.add_argument("--bench-dir", default=tempfile.gettempdir(), help="where the few copies for the text readers go (default: the temporary folder)")
    a = ap.parse_args()

    say("F3: how fast documents come out of an image, for the text search. READ-ONLY. Counts, sizes and timings only: no names.")
    head("Tools")
    missing = [t for t in TSK if not shutil.which(t)]
    if missing:
        say(f"NOT INSTALLED: {', '.join(missing)}. Run the bootstrap (it installs sleuthkit), then run this again.")
        return 3
    say(first_line(subprocess.run(["fls", "-V"], capture_output=True, text=True).stdout))
    say(f"Python {sys.version.split()[0]}, machine {os.uname().machine}")

    image = a.image or newest_image()
    head("The image")
    if not image or not os.path.isfile(image):
        say(f"no image found{' at ' + a.image if a.image else ' in ' + STORE}")
        return 2
    try:
        with open(image, "rb") as f:
            f.read(512)
    except OSError as e:
        say(f"cannot read the image: {e.strerror}. Run this as: sg gatbox-disk-queue -c 'python3 {sys.argv[0]}'")
        return 2
    say(f"{os.path.getsize(image):,} bytes ({os.stat(image).st_blocks * 512 / 1e9:,.1f} GB on disk)")

    head("The volume")
    vols = volumes(image)
    if not vols:
        say("no volume with a filesystem found: nothing to read documents from")
        return 0
    start = a.volume if a.volume is not None else vols[0][0]
    say(f"{len(vols)} volume(s) with a filesystem; using the " + ("one starting at the sector given" if a.volume is not None else "largest"))

    head("The documents in it")
    found, entries, too_big, secs, timed_out = candidates(image, start, a.list_timeout)
    say(f"{entries:,} entries listed in {secs:,.1f} s" + ("  (STOPPED at the limit: the counts below are partial)" if timed_out else ""))
    by = Counter(e for _, _, e in found)
    size_by = Counter()
    for _, size, ext in found:
        size_by[ext] += size
    total = sum(size_by.values())
    say(f"files with a text reader: {len(found):,}, {total / MiB:,.1f} MB; {too_big:,} more are over 64 MB and would not be read")
    for ext, n in by.most_common():
        say(f"   .{ext:<6}{n:>8,} files {size_by[ext] / MiB:>10,.1f} MB")
    if not found:
        say("nothing to measure: no document with a reader in this volume. Try another image or --volume.")
        return 0
    sizes = sorted(s for _, s, _ in found)
    say(f"file size: median {statistics.median(sizes) / 1024:,.0f} KB, 90th percentile {sizes[int(len(sizes) * 0.9)] / 1024:,.0f} KB, largest {sizes[-1] / MiB:,.1f} MB")

    n = min(a.files, len(found))
    sample = random.Random(SEED).sample(found, n)
    head(f"Reading {n} random documents out of the image, three times")
    if n < a.files:
        say(f"(only {n} documents here: all of them are the sample)")
    say("Each run reads the same files with icat. The image's cached pages are dropped before each run. The first read of a drive that was idle may")
    say("include its spin-up; the first run's first file is reported separately for that.")
    runs = [("run 1, random order", sample, True),
            ("run 2, inode order", sorted(sample, key=lambda f: int(f[0].split("-")[0])), False),
            ("run 3, random order again", random.Random(SEED + 1).sample(sample, n), False)]
    results, kept = [], {}
    for label, files, keep in runs:
        drop_cache(image)
        t0 = time.time()
        secs_, got, failed, timed, k = one_pass(image, start, files, {} if keep else None, a.timeout)
        wall = time.time() - t0
        if keep:
            kept = k
            if secs_:
                say(f"first file read of the first run: {secs_[0]:.2f} s")
        results.append(summary(label, secs_, got, failed, timed, wall))
    r1, r2, r3 = results
    head("Projection for every document in the volume")
    if r1 and r2 and r3:
        noise = abs(r1["wall"] - r3["wall"]) / max(r1["wall"], r3["wall"]) * 100
        gain = (r1["wall"] + r3["wall"]) / 2
        better = (gain - r2["wall"]) / gain * 100
        say(f"two runs in the same order differ by {noise:.0f}%; inode order is {abs(better):.0f}% {'faster' if better > 0 else 'slower'} than random order "
            f"(a difference smaller than the first number is noise)")
        sample_bytes = r1["got"] / r1["n"]
        for label, r in (("random order", r1), ("inode order", r2)):
            per_file = r["wall"] / r["n"]
            by_files = per_file * len(found)
            by_bytes = r["wall"] / r["got"] * total if r["got"] else 0
            say(f"reading all {len(found):,} documents in {label}: about {by_files / 3600:,.1f} h by the number of files, {by_bytes / 3600:,.1f} h by their bytes "
                f"({sample_bytes / 1024:,.0f} KB on average in the sample, {total / len(found) / 1024:,.0f} KB in the volume)")
    x = readers(kept, a.bench_dir)
    if x is not None and r2:
        say(f"\nextracting the text takes {x:.3f} s on average per file here, against {r2['wall'] / r2['n']:.3f} s to read it out of the image in inode order")
    say("\nNothing was changed. Paste this report as it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
