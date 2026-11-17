"""The MAME CHD check (docs/plans/2026-11-02-disk-imaging.md §7, Task 3): is a drive image the hard disk MAME knows for its game?

A raw image's SHA-1 never equals MAME's: MAME's hash for a `<disk>` is a CHD's combined SHA-1, which covers the data and the CHD's
metadata (the drive's geometry among it). What does compare is a CHD's **Data SHA1**, over the data alone, whatever the compression.
So the owner puts reference CHDs (from her MAME sets) in the store's `_chd/` folder, and:

1. `chdman info` on each gives its combined SHA1, Data SHA1 and logical size (the format read off chdman 0.289's output, the
   owner's laptop, 2026-11-16).
2. A CHD whose combined SHA1 is a MAME disk names itself: the set, the version and the disk.
3. For an image: hash exactly the CHD's logical size of the image and compare with the Data SHA1. Equal is a MATCH, from the bytes.
4. Not equal: `chdman extracthd` to a scratch file on the store, compare the two piece by piece, and report how many bytes differ
   and in how many places. A few small places is a game that has written to its disk (audits, settings); many is another version
   or damage. The scratch file is removed.

It never decides a drive is bad, and it never makes a CHD a write source. Tier 2 of the plan (building a CHD from the image with the
drive's own geometry) is not here: the dock never reports a drive's geometry, so it could not run.
"""
import hashlib
import os
import re
import subprocess

CHDMAN = os.environ.get("GATBOX_CHDMAN", "chdman")
CHD_DIR = "_chd"                         # in the store: the owner's reference CHDs
SCRATCH = "_chd_scratch"                 # in the store: an extracted CHD while it is compared, then removed
CHUNK = 4 << 20
GAP = 64 << 10                           # differences closer than this are one place
FEW_PLACES, FEW_SHARE = 32, 0.01         # "the game wrote to it": at most this many places and this share of the bytes (a guess)

_HEX = re.compile(r"^[0-9a-f]{40}$")


class ChdError(Exception):
    pass


def parse_info(text):
    """`chdman info` -> {sha1, data_sha1, logical, hunk, chs, version}. Lines are "Name:  value"; numbers carry thousands commas;
    the CHS sits on the line after `Metadata: Tag='GDDD'`."""
    out, lines = {}, text.splitlines()
    for i, line in enumerate(lines):
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k, v = k.strip().lower(), v.strip()
        if k == "sha1":
            out["sha1"] = v.lower()
        elif k == "data sha1":
            out["data_sha1"] = v.lower()
        elif k == "logical size":
            m = re.match(r"([\d,]+)", v)
            out["logical"] = int(m.group(1).replace(",", "")) if m else None
        elif k == "hunk size":
            m = re.match(r"([\d,]+)", v)
            out["hunk"] = int(m.group(1).replace(",", "")) if m else None
        elif k == "file version":
            out["version"] = v
        elif k == "metadata" and "GDDD" in v and i + 1 < len(lines):
            m = re.search(r"CYLS:(\d+),HEADS:(\d+),SECS:(\d+),BPS:(\d+)", lines[i + 1])
            if m:
                out["chs"] = {"cyls": int(m.group(1)), "heads": int(m.group(2)), "secs": int(m.group(3)), "bps": int(m.group(4))}
    if not (_HEX.match(out.get("sha1") or "") and _HEX.match(out.get("data_sha1") or "") and out.get("logical")):
        raise ChdError("chdman info did not give a SHA1, a Data SHA1 and a logical size")
    return out


def info(path, timeout=120):
    try:
        r = subprocess.run([CHDMAN, "info", "-i", path], capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LC_ALL="C"))
    except FileNotFoundError:
        raise ChdError("chdman isn't installed on this Pi (the bootstrap installs it: mame-tools)") from None
    except subprocess.TimeoutExpired:
        raise ChdError("chdman info did not answer in time") from None
    if r.returncode != 0:
        last = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        raise ChdError(f"chdman info failed: {last[0][:160]}")
    return parse_info(r.stdout)


def mame_disks(mame_roms, machine=None):
    """{combined sha1: {machine, set, version, desc, disk}} for every MAME hard disk in the checklist (one machine's, or all)."""
    out = {}
    for slug, m in ((mame_roms or {}).get("machines") or {}).items():
        if machine is not None and slug != machine:
            continue
        for v in m.get("versions") or []:
            for d in v.get("disks") or []:
                s = (d.get("sha1") or "").lower()
                if _HEX.match(s):
                    out.setdefault(s, {"machine": slug, "set": v.get("name"), "desc": v.get("desc"), "disk": d.get("name"),
                                       "parent": bool(v.get("parent"))})
    return out


def list_chds(store, mame_roms=None):
    """The reference CHDs in <store>/_chd/, each with what chdman says and the MAME disk it is: [{file, bytes, sha1, data_sha1,
    logical, chs, mame: {...} or None, error}]. A CHD chdman can't read is listed with its error."""
    folder = os.path.join(store, CHD_DIR)
    names = {}
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".chd"))
    except OSError:
        return []
    known = mame_disks(mame_roms)
    for f in files:
        p = os.path.join(folder, f)
        row = {"file": f, "bytes": os.path.getsize(p)}
        try:
            row.update(info(p))
            row["mame"] = known.get(row["sha1"])
        except ChdError as e:
            row["error"] = str(e)
        names[f] = row
    return [names[f] for f in files]


def hashed(path, length, on_progress=None):
    h, done = hashlib.sha1(), 0
    with open(path, "rb") as f:
        while done < length:
            b = f.read(min(CHUNK, length - done))
            if not b:
                break
            h.update(b)
            done += len(b)
            if on_progress:
                on_progress(done)
    return h.hexdigest(), done


BLOCK = 4096
EXACT_BLOCKS = 4096                      # differing 4 KiB blocks counted byte by byte; past that, by the block (it is "other" by then)


def differences(a, b, length, on_progress=None):
    """Where A and B differ in their first LENGTH bytes: (differing bytes, [[offset, length], ...] places merged within GAP, exact).
    Whole 4 KiB blocks are compared first (fast); the bytes of a differing block are counted one by one for the first EXACT_BLOCKS
    such blocks, and after that a differing block counts as 4 KiB, so a drive that is another version entirely is not walked byte
    by byte. `exact` says which."""
    places, total, off, slow = [], 0, 0, 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while off < length:
            want = min(CHUNK, length - off)
            x, y = fa.read(want), fb.read(want)
            if len(x) < want or len(y) < want:
                raise ChdError("a file ended early while comparing")
            if x != y:
                for k in range(0, want, BLOCK):
                    bx, by = x[k:k + BLOCK], y[k:k + BLOCK]
                    if bx == by:
                        continue
                    if slow < EXACT_BLOCKS:
                        slow += 1
                        first = next(i for i in range(len(bx)) if bx[i] != by[i])
                        last = next(i for i in range(len(bx) - 1, -1, -1) if bx[i] != by[i])
                        total += sum(1 for p, q in zip(bx, by) if p != q)
                        start, end = off + k + first, off + k + last + 1
                    else:
                        total += len(bx)
                        start, end = off + k, off + k + len(bx)
                    if places and start - (places[-1][0] + places[-1][1]) <= GAP:
                        places[-1][1] = end - places[-1][0]
                    else:
                        places.append([start, end - start])
            off += want
            if on_progress:
                on_progress(off)
    return total, places, slow < EXACT_BLOCKS


def compare(image, chd_path, chd, scratch_dir, free_bytes, on_progress=None, say=lambda *a: None):
    """One image against one reference CHD. -> {state: match | differs, ...}. Raises ChdError when it can't be compared."""
    size = os.path.getsize(image)
    logical = chd["logical"]
    if size < logical:
        return {"state": "differs", "verdict": "shorter", "why": f"the image is {size:,} bytes and MAME's disk is {logical:,}: "
                f"it can't be that disk (a smaller drive, or a read that stopped short)"}
    say("hash", f"the image's first {logical:,} bytes against the CHD's data")
    got, _ = hashed(image, logical, lambda d: on_progress and on_progress(0.5 * d / logical))
    if got == chd["data_sha1"]:
        return {"state": "match", "why": "the image's bytes are exactly MAME's disk" + (f" ({size - logical:,} bytes past it, not part of the disk)" if size > logical else "")}
    if free_bytes < logical + (1 << 30):
        raise ChdError(f"the store needs {logical / 1e9:.1f} GB free (and 1 GB to spare) to unpack the CHD and find where it differs")
    os.makedirs(scratch_dir, exist_ok=True)
    raw = os.path.join(scratch_dir, f"{os.getpid()}.raw")
    try:
        say("extract", "unpacking the CHD to see where they differ")
        r = subprocess.run([CHDMAN, "extracthd", "-i", chd_path, "-o", raw, "-f"], capture_output=True, text=True, timeout=6 * 3600,
                           env=dict(os.environ, LC_ALL="C"))
        if r.returncode != 0 or not os.path.exists(raw):
            last = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
            raise ChdError(f"chdman extracthd failed: {last[0][:160]}")
        say("compare", "the image against MAME's disk, piece by piece")
        total, places, exact = differences(image, raw, logical, lambda d: on_progress and on_progress(0.5 + 0.5 * d / logical))
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    few = len(places) <= FEW_PLACES and total <= FEW_SHARE * logical
    return {"state": "differs", "verdict": "written" if few else "other",
            "bytes": total, "exact": exact, "places": len(places), "where": places[:20],
            "why": (f"{total:,} bytes differ in {len(places)} place{'s' if len(places) != 1 else ''}: most likely this version, and the game has "
                    "written to these areas (audits, settings)") if few else
                   (f"{'' if exact else 'at least '}{total:,} bytes differ in {len(places)} places: another version of the game, or damage")}
