"""The drive browser's core (docs/plans/2026-11-15-drive-browser.md): look inside a stored image without mounting it.

Everything here reads an image file in the store with sleuthkit's mmls, fsstat, fls and icat. Nothing is mounted, no block
device is opened, nothing is written to an image, and a file is found by its inode number, never by following a path on
this machine, so a symlink inside an image leads nowhere. Photos are drawn with Pillow and PDF pages with pdftoppm.

The caller (the gatbox-browse daemon, in a sandbox with the store read-only and no network) gets plain dicts and bytes, or
an Err with an HTTP-style code and a sentence for the screen. Limits keep a damaged or hostile file from hurting the Pi:
a time limit on every subprocess, 64 MB at most read for a preview, 1 MB of text, a pixel limit on photos, 100,000 entries
held for one folder, and at most three sleuthkit processes at once.

Environment (tests): GATBOX_DISKS (the store), GATBOX_BROWSE_DIR (cache and stats), GATBOX_DISK_SPOOL (to see a disk job).
"""
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import warnings
from collections import Counter, OrderedDict

from . import disks as dl

BROWSE_DIR = os.environ.get("GATBOX_BROWSE_DIR", os.path.join(dl.DISKS, "_browse"))
DISK_SPOOL = os.environ.get("GATBOX_DISK_SPOOL", "/var/spool/gatbox-disk")
TSK = ("mmls", "fsstat", "fls", "icat")                 # the only programs that ever open an image
PDF_TOOLS = ("pdfinfo", "pdftoppm")                     # they read a copy in the cache, never the image
TEXT_TOOLS = ("pdftotext", "catdoc", "xls2csv", "catppt")   # the text readers (browsetext.py), on a copy of a file as well
MiB = 1 << 20
PREVIEW_CAP = 64 * MiB                                  # the most read out of an image for one preview
TEXT_CAP = 64 * 1024
PER_PAGE = 200
FOLDER_CAP = 100_000                                    # entries held for one folder (sorting needs them in memory)
MAX_PIXELS = 50_000_000
CACHE_CAP = 256 * MiB                                   # extracted files kept for the next page or the next look
THUMB_EDGE = 192                                        # the longest side of a thumbnail, in pixels
THUMB_CAP = 2048 * MiB                                  # thumbnails kept on the store; the oldest go first
PHOTO = {"jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "webp", "heic", "heif", "cr2", "nef", "arw", "dng", "raw"}
DOC = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "txt", "rtf", "md", "csv", "html", "htm", "epub"}
INODE = re.compile(r"^[0-9]+(-[0-9]+){0,2}$")           # ext 12, NTFS 5-144-1
SORTS = ("name", "date", "size")
KINDS = ("all", "photos", "documents")
MMLS_ROW = re.compile(r"^\d+:\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$")

_sem = threading.BoundedSemaphore(3)                    # sleuthkit processes at once, whoever asks
_bg = threading.BoundedSemaphore(1)                     # and one background pass (the stats) among them
_lock = threading.Lock()


class Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def env():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "HOME": "/nonexistent"}


def kill_group(p):
    """Stop a tool and anything it started: it runs in its own session, so its whole group goes."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        p.kill()


def run(argv, timeout, cap=None, stdout=None):
    """One sleuthkit or poppler tool. Returns (exit code, stdout bytes, stderr text). Killed at `timeout` (Err 504). With
    cap, output past that many bytes is cut and the process is stopped (the tool itself is not at fault). With stdout=a
    file object, the output goes there instead of into memory and the byte count is returned in place of the bytes."""
    if argv[0] not in TSK + PDF_TOOLS + TEXT_TOOLS:
        raise Err(500, f"refusing to start {argv[0]}")
    with _sem:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env(), start_new_session=True)
        killed = []
        timer = threading.Timer(timeout, lambda: (killed.append(1), kill_group(p)))
        timer.start()
        out, n = [], 0
        try:
            while True:
                chunk = p.stdout.read(65536)
                if not chunk:
                    break
                if cap is not None and n + len(chunk) > cap:
                    chunk = chunk[:cap - n]
                    n += len(chunk)
                    (stdout.write(chunk) if stdout else out.append(chunk))
                    kill_group(p)
                    break
                n += len(chunk)
                (stdout.write(chunk) if stdout else out.append(chunk))
        finally:
            timer.cancel()
        err = p.stderr.read(4096).decode("utf-8", "replace")
        p.stdout.close()
        p.stderr.close()
        rc = p.wait()
    if killed:
        raise Err(504, "reading the image took too long, so it was stopped")
    return rc, (n if stdout else b"".join(out)), err


def _spool_json(name):
    try:
        with open(os.path.join(DISK_SPOOL, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def disk_busy(store=False):
    """A disk job is queued or running (gatbox-disk's spool). Background passes wait for it: imaging has priority.

    store=True asks whether the job is using the image store, which is what a thumbnail competes for. A WRITE in its read-back reads
    only the target drive, so the store is idle then: an 80 GB restore spent about 80 of its 150 minutes there (the Pi, 2026-11-16),
    and thumbnails waited all that time. The exception is narrow: the job running is a write, the status file is that same job's
    (matching id), and its step is the read-back; a queued request, or any other step or job, is busy."""
    if os.path.exists(os.path.join(DISK_SPOOL, "request.json")):
        return True
    if not os.path.exists(os.path.join(DISK_SPOOL, "running.json")):
        return False
    if store:
        run, st = _spool_json("running.json"), _spool_json("status.json")
        if (isinstance(run, dict) and isinstance(st, dict) and run.get("op") == "write" and st.get("op") == "write"
                and st.get("state") == "running" and st.get("step") == "verify" and run.get("id") and st.get("job") == run.get("id")):
            return False
    return True


def kind_of(name, folder=False):
    if folder:
        return "folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return "photo" if ext in PHOTO else "document" if ext in DOC else "other"


# --- the image and its volumes ---------------------------------------------------------------------------------------------

def image(rel):
    """(path, sidecar) of a stored image: one the store lists, nothing else, and only while the store is mounted."""
    if not dl.store_mounted():
        raise Err(409, "IMAGE STORE NOT CONNECTED")
    side = dl.sidecar(rel)
    if side is None:
        raise Err(404, f"not an image in the store: {rel!r}")
    return os.path.join(os.path.realpath(dl.DISKS), rel), side


class _Lru(OrderedDict):
    def __init__(self, size):
        super().__init__()
        self.size = size

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > self.size:
            self.popitem(last=False)

    def fetch(self, k, ttl=60):
        v = self.get(k)
        if v is None or time.time() - v[0] > ttl:
            return None
        self.move_to_end(k)
        return v[1]


_vols = _Lru(16)
_dirs = _Lru(8)
_kinds = _Lru(512)


def _num(text, default=None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def parse_fsstat(text):
    """The volume facts fsstat prints for ext, FAT and NTFS, in bytes where it can tell: {} when it printed none."""
    kv = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            kv.setdefault(k.strip(), v.strip())
    typ = kv.get("File System Type")
    if not typ:
        return None
    bs = _num(kv.get("Block Size")) or _num(kv.get("Cluster Size")) or _num(kv.get("Sector Size"))
    sector = _num(kv.get("Sector Size")) or bs

    def end(s):
        m = re.search(r"-\s*(\d+)\s*$", s or "")
        return int(m.group(1)) + 1 if m else None
    blocks, free, size, free_bytes = end(kv.get("Block Range")), _num(kv.get("Free Blocks")), None, None
    if blocks and bs:
        size = blocks * bs
        free_bytes = free * bs if free is not None else None
    elif end(kv.get("Total Range")) and sector:                            # FAT: sectors
        size = end(kv.get("Total Range")) * sector
        free_s = _num(kv.get("Free Sector Count (FS Info)"))
        free_bytes = free_s * sector if free_s is not None else None
    elif end(kv.get("Total Cluster Range")) and bs:                       # NTFS: clusters
        size = end(kv.get("Total Cluster Range")) * bs
    label = kv.get("Volume Name") or kv.get("Volume Label (Boot Sector)") or ""
    label = label.strip()
    return {"type": typ, "block_size": bs, "size_bytes": size, "free_bytes": free_bytes,
            "used_bytes": size - free_bytes if size is not None and free_bytes is not None else None,
            "inodes": end(kv.get("Inode Range")), "free_inodes": _num(kv.get("Free Inodes")),
            "label": label if label.upper() != "NO NAME" else ""}


def _signature(path, start):
    """What the first sector of a volume says when no filesystem was found: an encryption header, and a short hex peek."""
    try:
        with open(path, "rb") as f:
            f.seek(start * 512)
            head = f.read(512)
    except OSError:
        return None, None
    enc = "BitLocker" if head[3:11] == b"-FVE-FS-" else "LUKS" if head[:6] == b"LUKS\xba\xbe" else None
    return enc, head[:48].hex(" ")


def volumes(rel):
    """The image's volumes: each partition (or the whole image when there is no table) with its filesystem, or why it has none."""
    path, side = image(rel)
    key = (path, os.stat(path).st_mtime_ns)
    hit = _vols.fetch(key)
    if hit is not None:
        return hit
    rc, out, err = run(["mmls", path], 60)
    text = out.decode("utf-8", "replace")
    table = next((l.strip() for l in text.splitlines() if "Partition Table" in l or "Volume System" in l), None) if rc == 0 else None
    rows = []
    if rc == 0:
        for line in text.splitlines():
            m = MMLS_ROW.match(line)
            if not m:
                continue
            slot, start, _, length, desc = m.groups()
            if slot in ("Meta", "-------") or desc.startswith(("DOS Extended", "Extended")):
                continue
            rows.append((slot, int(start), int(length), desc))
    else:
        rows = [("whole image", 0, None, "no partition table")]
    vols = []
    for slot, start, length, desc in rows:
        rc, out, err = run(["fsstat", "-o", str(start), path], 120)
        fs = parse_fsstat(out.decode("utf-8", "replace")) if rc == 0 else None
        v = {"start": start, "length": length, "slot": slot, "desc": desc, "fs": fs, "encrypted": None, "peek": None, "note": None}
        if not fs:
            v["encrypted"], v["peek"] = _signature(path, start)
            v["note"] = (f"encrypted ({v['encrypted']}): it cannot be opened" if v["encrypted"]
                         else "swap space" if "swap" in desc.lower() else "no filesystem recognized")
        vols.append(v)
    res = {"image": rel, "bytes": os.path.getsize(path), "table": table, "volumes": vols}
    _vols.put(key, (time.time(), res))
    return res


def _volume(rel, start):
    """The listed volume with this start sector: a start that mmls did not report is refused, so no offset is invented."""
    start = _num(start)
    v = next((v for v in volumes(rel)["volumes"] if v["start"] == start), None) if start is not None else None
    if v is None:
        raise Err(404, f"no volume starting at sector {start!r} in this image")
    if not v["fs"]:
        raise Err(415, v["note"] or "no filesystem recognized")
    return v


def _inode(text):
    if text in (None, ""):
        return None
    if not INODE.match(text):
        raise Err(400, "inode: a number such as 12 or 5-144-1")
    return text


def _file_inode(text):
    ino = _inode(text)
    if not ino:
        raise Err(400, "inode: the file's inode")
    return ino


# --- folders ---------------------------------------------------------------------------------------------------------------

GONE_CAP = 20_000                                       # deleted entries held for one folder (kept apart, so they never crowd out live ones)


def gone_name(name):
    """(name without the marker, reused) when fls marked this entry deleted, else None. "(deleted-realloc)" is a deleted name
    whose inode a newer file now holds: reading that inode gives the newer file, never the deleted one."""
    for mark, reused in ((" (deleted-realloc)", True), (" (deleted)", False)):
        if name.endswith(mark):
            return name[:-len(mark)], reused
    return None


def parse_body(line):
    """One line of fls -m: (name, inode, mode string, size, mtime), or None. The name may itself contain a pipe, so the
    nine fixed fields are cut off from the right."""
    try:
        prefix, inode, mode, _, _, size, _, mtime, _, _ = line.rsplit("|", 9)
        return prefix.split("|", 1)[1].lstrip("/"), inode, mode, int(size), int(mtime)
    except (ValueError, IndexError):
        return None


def _entries(path, start, inode, fat=False):
    """All entries of one folder, up to the caps: (live [dict], how many are deleted, capped, deleted [dict]). Virtual and
    label entries are left out. A deleted entry carries "deleted": True (and "reused": True when its inode now holds another
    file); the listing shows them only when asked. On FAT a deleted short name loses its first letter (the drive writes a marker
    over it, and sleuthkit shows "_"): such an entry carries "name_cut": True."""
    argv = ["fls", "-m", "/", "-o", str(start), path] + ([inode] if inode else [])
    rc, out, err = run(argv, 60)
    if rc != 0 and not out:
        raise Err(415, "this folder could not be read" + (f": {err.strip().splitlines()[0][:120]}" if err.strip() else ""))
    entries, gone, hidden, capped = [], [], 0, False
    for raw in out.decode("utf-8", "replace").splitlines():
        p = parse_body(raw)
        if p is None:
            continue
        name, ino, mode, size, mtime = p
        t = mode[:1]
        dead = gone_name(name)
        if dead:
            hidden += 1
            name = dead[0]
        if t in ("V", "v") or name.endswith("(Volume Label Entry)") or t not in ("d", "r", "l"):
            continue
        if len(gone if dead else entries) >= (GONE_CAP if dead else FOLDER_CAP):
            capped = capped or not dead
            if dead:
                continue
            break
        target = None
        if t == "l" and " -> " in name:
            name, _, target = name.partition(" -> ")
        e = {"name": name, "kind": kind_of(name, t == "d") if t != "l" else "link", "inode": ino, "size": size,
             "mtime": mtime if 631152000 <= mtime <= 4102444800 else None}
        if target:
            e["target"] = target[:200]
        if dead:
            e["deleted"] = True
            if dead[1]:
                e["reused"] = True
            if fat and name.startswith("_"):
                e["name_cut"] = True
            gone.append(e)
        else:
            entries.append(e)
    return entries, hidden, capped, gone


def ls(rel, start, dir_inode=None, sort="name", order="asc", kind="all", page=0, per_page=PER_PAGE, at=None, deleted=False):
    """One page of a folder. The folder is named by its inode (None is the root), and every entry carries its own, so a
    client walks down by inode and never builds a path. `at` is an entry's inode: the page that holds it is returned
    (SHOW IN FOLDER from a search result), whatever `page` said. Deleted entries are listed only with `deleted` set; they are
    marked, and sorted in with the rest."""
    path, side = image(rel)
    v = _volume(rel, start)
    ino = _inode(dir_inode)
    if sort not in SORTS or order not in ("asc", "desc") or kind not in KINDS:
        raise Err(400, "sort is name, date or size; order asc or desc; kind all, photos or documents")
    key = (path, v["start"], ino)
    hit = _dirs.fetch(key)
    if hit is None:
        hit = _entries(path, v["start"], ino, fat=(v["fs"].get("type") or "").upper() in ("FAT12", "FAT16", "FAT32"))
        _dirs.put(key, (time.time(), hit))
    entries, hidden, capped, gone = hit
    entries = entries + gone if deleted else entries
    want = {"all": None, "photos": "photo", "documents": "document"}[kind]
    shown = [e for e in entries if e["kind"] == "folder" or want is None or e["kind"] == want]
    rev = order == "desc"
    if sort == "name":
        keyf = lambda e: e["name"].casefold()                      # noqa: E731
    elif sort == "date":
        keyf = lambda e: e["mtime"] or 0                           # noqa: E731
    else:
        keyf = lambda e: e["size"]                                 # noqa: E731
    folders = sorted((e for e in shown if e["kind"] == "folder"), key=keyf, reverse=rev)
    files = sorted((e for e in shown if e["kind"] != "folder"), key=keyf, reverse=rev)
    ordered = folders + files                                       # folders first, whatever the sort
    page = max(0, _num(page, 0))
    found = None
    if at:
        found = _inode(at)
        where = next((i for i, e in enumerate(ordered) if e["inode"] == found), None)
        if where is None:
            found = None
        else:
            page = where // per_page
    pages = max(1, -(-len(ordered) // per_page))
    page = min(page, pages - 1)
    return {"image": rel, "start": v["start"], "dir": ino, "total": len(ordered), "page": page, "pages": pages,
            "entries": ordered[page * per_page:(page + 1) * per_page], "capped": capped, "deleted_hidden": 0 if deleted else hidden,
            "deleted_shown": bool(deleted), "deleted_total": hidden, "at": found}


# --- what is on a volume (counted by a background pass) ---------------------------------------------------------------------

_stats = {}                                                         # (path, start) -> state dict


def _stats_file(side, start):
    return os.path.join(BROWSE_DIR, "stats", f"{side.get('sha1', 'x')[:16]}-{start}.stats")


def stats(rel, start, begin=True):
    """What a volume holds, counted from one pass over its listing: {"state": "computing"|"done"|"error"|"waiting", ...}.
    The pass runs in the background, waits while a disk job runs, and is kept on the store so the next look is instant."""
    path, side = image(rel)
    v = _volume(rel, start)
    key = (path, v["start"])
    with _lock:
        st = _stats.get(key)
        if st and st["state"] in ("computing", "waiting", "done"):
            return dict(st, volume=v["fs"])
        f = _stats_file(side, v["start"])
        try:
            with open(f) as fh:
                st = json.load(fh)
            _stats[key] = st
            return dict(st, volume=v["fs"])
        except (OSError, ValueError):
            pass
        if not begin:
            return {"state": "none", "volume": v["fs"]}
        if disk_busy():
            raise Err(409, "a disk job is running: counting waits for it")
        st = {"state": "computing", "entries": 0, "started": time.time()}
        _stats[key] = st
    threading.Thread(target=_count, args=(key, path, v["start"], f), daemon=True, name="stats").start()
    return dict(st, volume=v["fs"])


def _count(key, path, start, outfile):
    st = _stats[key]
    try:
        with _bg:
            _count_pass(st, path, start)
        st.update(state="done", seconds=round(time.time() - st["started"], 1))
        try:
            os.makedirs(os.path.dirname(outfile), exist_ok=True)
            tmp = outfile + ".tmp"
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, outfile)
        except OSError:
            pass                                                    # no cache folder: the answer is still kept in memory
    except Err as e:
        st.update(state="error", error=e.msg)
    except Exception as e:                                          # a pass that dies says so and is not retried silently
        st.update(state="error", error=f"{type(e).__name__}")


def _count_pass(st, path, start, timeout=3600):
    c, kinds, size_by, exts = Counter(), Counter(), Counter(), Counter()
    oldest = newest = None
    argv = ["fls", "-r", "-p", "-m", "/", "-o", str(start), path]
    with _sem:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env(), start_new_session=True)
        killed = []
        timer = threading.Timer(timeout, lambda: (killed.append(1), kill_group(p)))
        timer.start()
        try:
            for raw in p.stdout:
                while disk_busy():                                  # imaging goes first: stand still until it is done
                    st["state"] = "waiting"
                    time.sleep(2)
                st["state"] = "computing"
                got = parse_body(raw.decode("utf-8", "replace").rstrip("\n"))
                if got is None:
                    continue
                name, _, mode, size, mtime = got
                if name.endswith("(Volume Label Entry)"):
                    continue                                        # FAT's label is stored as a file entry; it is not one
                c["entries"] += 1
                st["entries"] = c["entries"]
                if gone_name(name):
                    c["deleted"] += 1
                    continue
                t = mode[:1]
                if t == "d":
                    c["folders"] += 1
                elif t == "l":
                    c["links"] += 1
                elif t == "r":
                    c["files"] += 1
                    k = kind_of(name) + "s"
                    kinds[k] += 1
                    size_by[k] += size
                    if "." in name.rsplit("/", 1)[-1]:
                        exts[name.rsplit(".", 1)[-1].lower()] += 1
                    if 631152000 <= mtime <= 4102444800:
                        oldest = mtime if oldest is None else min(oldest, mtime)
                        newest = mtime if newest is None else max(newest, mtime)
        finally:
            timer.cancel()
            p.stdout.close()
            p.wait()
    if killed:
        raise Err(504, "counting took too long and was stopped")
    st.update(files=c["files"], folders=c["folders"], links=c["links"], deleted=c["deleted"], entries=c["entries"],
              photos={"files": kinds["photos"], "bytes": size_by["photos"]},
              documents={"files": kinds["documents"], "bytes": size_by["documents"]},
              other={"files": kinds["others"], "bytes": size_by["others"]},
              oldest=oldest, newest=newest, extensions=[[e, n] for e, n in exts.most_common(8)])


# --- reading one file ------------------------------------------------------------------------------------------------------

def sniff(head):
    """(kind, mime) from the first bytes, not the name: photo, pdf, text or other."""
    if head[:3] == b"\xff\xd8\xff":
        return "photo", "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "photo", "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "photo", "image/gif"
    if head[:2] == b"BM":
        return "photo", "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "photo", "image/tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "photo", "image/webp"
    if b"%PDF-" in head[:1024]:
        return "pdf", "application/pdf"
    if head[4:8] == b"ftyp":
        return "other", "image/heic" if head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc") else "video/mp4"
    if head and b"\x00" not in head[:4096]:
        try:
            sample = head[:4096].decode("utf-8")
        except UnicodeDecodeError:
            sample = head[:4096].decode("latin-1")
        printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
        if printable / max(len(sample), 1) > 0.95:
            return "text", "text/plain"
    return "other", "application/octet-stream"


class _Cache:
    """Files read out of an image, kept so the next page of a PDF or the sharper copy of a photo is not another slow read.
    On the store's _browse folder when it is there, else in a private temporary folder; the oldest go at CACHE_CAP."""
    def __init__(self):
        self.dir = None

    def folder(self):
        if self.dir and os.path.isdir(self.dir):
            return self.dir
        for base in (os.path.join(BROWSE_DIR, "cache"),):
            try:
                os.makedirs(base, exist_ok=True)
                if os.access(base, os.W_OK):
                    self.dir = base
                    return base
            except OSError:
                pass
        self.dir = tempfile.mkdtemp(prefix="gatbox-browse-")
        return self.dir

    def trim(self, keep=None):
        try:
            files = [(os.path.getmtime(p), os.path.getsize(p), p) for p in (os.path.join(self.dir, n) for n in os.listdir(self.dir))
                     if os.path.isfile(p)]
        except OSError:
            return
        total = sum(s for _, s, _ in files)
        for _, size, p in sorted(files):
            if total <= CACHE_CAP:
                break
            if p == keep:
                continue                                           # the file being returned stays, whatever the cap says
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass


_cache = _Cache()
_extract_lock = threading.Lock()


def extract(rel, start, inode, size_hint=None):
    """The file with this inode, copied out of the image into the cache (once). Returns its path. A file over the preview
    limit is refused, quickly when the listing's size says so, else after the limit's worth has been read."""
    path, side = image(rel)
    v = _volume(rel, start)
    ino = _file_inode(inode)
    if size_hint is not None and _num(size_hint, 0) > PREVIEW_CAP:
        raise Err(413, f"too large to preview ({_num(size_hint) / MiB:,.0f} MB; the limit is {PREVIEW_CAP // MiB} MB)")
    name = hashlib.sha1(f"{side.get('sha1')}:{v['start']}:{ino}".encode()).hexdigest()[:24]
    folder = _cache.folder()
    dest = os.path.join(folder, name + ".bin")
    with _extract_lock:                                             # one extraction at a time: they are slow, and heavy on the store
        if os.path.isfile(dest):
            os.utime(dest)
            return dest
        tmp = dest + ".part"
        try:
            with open(tmp, "wb") as f:
                rc, n, err = run(["icat", "-o", str(v["start"]), path, ino], 180, cap=PREVIEW_CAP + 1, stdout=f)
            if n > PREVIEW_CAP:
                raise Err(413, f"too large to preview (the limit is {PREVIEW_CAP // MiB} MB)")
            if rc != 0 and n == 0:
                raise Err(415, "this file could not be read out of the image" + (f": {err.strip().splitlines()[0][:100]}" if err.strip() else ""))
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        _cache.trim(keep=dest)
    return dest


def _head(rel, start, inode, n=4096):
    path, side = image(rel)
    v = _volume(rel, start)
    rc, out, err = run(["icat", "-o", str(v["start"]), path, _file_inode(inode)], 60, cap=n)
    if not out and rc != 0:
        raise Err(415, "this file could not be read out of the image")
    return out


def _sniff_file(rel, start, inode):
    """(kind, mime) of a file, read from its first bytes once: the next page of a PDF does not go back to the image for it."""
    path, side = image(rel)
    v = _volume(rel, start)
    ino = _file_inode(inode)
    key = (side.get("sha1"), v["start"], ino)
    hit = _kinds.fetch(key, ttl=600)
    if hit is None:
        hit = sniff(_head(rel, start, inode))
        _kinds.put(key, (time.time(), hit))
    return hit


def info(rel, start, inode, size=None):
    """What a file is and how to show it: {"kind": photo|pdf|text|other, "pages", "width", "height", "message"}."""
    if size is not None and _num(size, 0) > PREVIEW_CAP:
        kind, mime = _sniff_file(rel, start, inode)
        if kind == "text":                                          # a big text file is shown from its start
            return {"kind": "text", "mime": mime, "message": "a large file: only the start is shown"}
        raise Err(413, f"too large to preview ({_num(size) / MiB:,.0f} MB; the limit is {PREVIEW_CAP // MiB} MB)")
    kind, mime = _sniff_file(rel, start, inode)
    res = {"kind": kind, "mime": mime, "pages": 1, "width": None, "height": None, "message": None}
    if kind == "text":
        return res
    if kind == "other":
        res["message"] = ("a HEIC photo: not shown yet" if mime == "image/heic" else "no preview for this kind of file")
        return res
    f = extract(rel, start, inode, size)
    if kind == "photo":
        im = _open_photo(f)
        res["width"], res["height"] = im.size
        im.close()
    else:
        rc, out, err = run(["pdfinfo", f], 30)
        text = out.decode("utf-8", "replace")
        m = re.search(r"^Pages:\s+(\d+)", text, re.M)
        if rc != 0 or not m:
            raise Err(415, "this PDF could not be read")
        res["pages"] = int(m.group(1))
    return res


RAW_PREVIEW_EDGE = 640          # a camera raw whose first image is smaller than this is a thumbnail: a larger one inside it is better


def _is_tiff(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4) in (b"II*\x00", b"MM\x00*")
    except OSError:
        return False


def _tiff_or_raw(im, path):
    """A camera raw (a Nikon NEF, a Canon CR2, a Sony ARW) is a TIFF whose first image is often a tiny thumbnail, or raw data
    Pillow will not even open (that case is the `except` below). When the first image is small, a larger picture inside the file
    is drawn instead (gatboxlib/rawpreview.py). An ordinary TIFF, whose first image is not small, is left exactly as it was,
    and is not decoded here."""
    if max(im.size) >= RAW_PREVIEW_EDGE:
        return im
    from . import rawpreview
    alt = rawpreview.preview_image(path, MAX_PIXELS)
    if alt is not None and max(alt.size) > max(im.size):
        im.close()
        return alt
    return im


def _open_photo(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)       # a photo over the limit is an error, not a warning
    try:
        im = Image.open(path)
        if im.size[0] * im.size[1] > MAX_PIXELS:
            raise Err(413, f"too large to draw ({im.size[0]}x{im.size[1]} pixels)")
        if im.format == "TIFF":
            im = _tiff_or_raw(im, path)
        return im
    except Err:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Err(413, "too large to draw (more pixels than the Pi will decode)") from None
    except Exception:
        if _is_tiff(path):                                               # Pillow would not even open it: a raw's pictures may still be there
            from . import rawpreview
            alt = rawpreview.preview_image(path, MAX_PIXELS)
            if alt is not None:
                return alt
        raise Err(415, "this photo could not be read (damaged, or a format this does not know)") from None


def preview(rel, start, inode, page=1, width=1200, size=None):
    """(JPEG bytes, "image/jpeg") for a photo, or for one page of a PDF, drawn at about `width` pixels across."""
    width = max(200, min(_num(width, 1200), 2400))
    page = max(1, _num(page, 1))
    kind, mime = _sniff_file(rel, start, inode)
    if kind not in ("photo", "pdf"):
        raise Err(415, "no preview for this kind of file")
    f = extract(rel, start, inode, size)
    if kind == "photo":
        from PIL import Image, ImageOps
        im = _open_photo(f)
        try:
            im.load()
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode == "L":
                im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
        except Err:
            raise
        except Exception:
            raise Err(415, "this photo could not be drawn") from None
        return buf.getvalue(), "image/jpeg"
    pages = info(rel, start, inode, size)["pages"]
    if page > pages:
        raise Err(404, f"this PDF has {pages} page{'s' if pages != 1 else ''}")
    work = tempfile.mkdtemp(prefix="pg-")
    try:
        rc, out, err = run(["pdftoppm", "-jpeg", "-scale-to-x", str(width), "-scale-to-y", "-1", "-f", str(page), "-l", str(page),
                            "-singlefile", f, os.path.join(work, "p")], 60)
        made = os.path.join(work, "p.jpg")
        if rc != 0 or not os.path.isfile(made):
            raise Err(415, "this page could not be drawn")
        with open(made, "rb") as fh:
            return fh.read(30 * MiB), "image/jpeg"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- thumbnails ------------------------------------------------------------------------------------------------------------------

_thumb_lock = threading.Lock()                           # one at a time: each is a slow read out of the image
_thumb_made = [0]


def _thumb_paths(side, start, ino):
    """Where a photo's thumbnail lives: named by a hash of the image's SHA-1, the volume and the inode, never by anything the
    client typed, so there is no path to walk out of the folder with."""
    key = hashlib.sha1(f"{side.get('sha1')}:{start}:{ino}".encode()).hexdigest()
    d = os.path.join(BROWSE_DIR, "thumbs", key[:2])
    return d, os.path.join(d, key[2:] + ".jpg"), os.path.join(d, key[2:] + ".bad")


def _trim_thumbs():
    """Keep the thumbnails under THUMB_CAP, the oldest looked-at going first. The folder is derived data: a thumbnail that goes
    is made again the next time it is asked for."""
    root = os.path.join(BROWSE_DIR, "thumbs")
    files = []
    for d, _, names in os.walk(root):
        for n in names:
            if n.endswith(".jpg"):
                p = os.path.join(d, n)
                try:
                    st = os.stat(p)
                    files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass
    total = sum(s for _, s, _ in files)
    if total <= THUMB_CAP:
        return
    for _, size, p in sorted(files):
        if total <= THUMB_CAP * 0.9:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def thumb(rel, start, inode, size=None):
    """(JPEG bytes, "image/jpeg") of a small thumbnail of a photo. Made once, kept in the store's _browse folder, and a photo
    that cannot be drawn is remembered as such, so a grid does not try it again on every look. Waits for no disk job: while one
    runs it is refused (409) and the screen shows its placeholder."""
    path, side = image(rel)
    v = _volume(rel, start)
    ino = _file_inode(inode)
    d, jpg, bad = _thumb_paths(side, v["start"], ino)
    for name in (jpg,):
        try:
            with open(name, "rb") as fh:
                data = fh.read(4 * MiB)
            os.utime(name)
            return data, "image/jpeg"
        except OSError:
            pass
    try:
        with open(bad) as fh:
            code, _, msg = fh.read(240).partition("\n")
        raise Err(_num(code, 415), msg or "no thumbnail for this file")          # said once: the same answer, with nothing read
    except OSError:
        pass
    if disk_busy(store=True):
        raise Err(409, "a disk job is running: thumbnails wait for it")
    with _thumb_lock:
        try:
            with open(jpg, "rb") as fh:                              # made by a request that was ahead of this one
                return fh.read(4 * MiB), "image/jpeg"
        except OSError:
            pass
        try:
            kind, _ = _sniff_file(rel, v["start"], ino)
            if kind != "photo":
                raise Err(415, "this is not a photo")
            from PIL import Image, ImageOps
            f = extract(rel, v["start"], ino, size)
            im = _open_photo(f)
            try:
                im.draft("RGB", (THUMB_EDGE * 2, THUMB_EDGE * 2))    # a JPEG is decoded at a fraction of its size
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                elif im.mode == "L":
                    im = im.convert("RGB")
                im.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=75)
            except Err:
                raise
            except Exception:
                raise Err(415, "this photo could not be drawn") from None
            data = buf.getvalue()
        except Err as e:
            if e.code in (413, 415):                                  # said once; a 409 or a 5xx is tried again
                try:
                    os.makedirs(d, exist_ok=True)
                    with open(bad, "w") as fh:
                        fh.write(f"{e.code}\n{e.msg[:200]}")
                except OSError:
                    pass
            raise
        try:
            os.makedirs(d, exist_ok=True)
            tmp = jpg + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, jpg)
            _thumb_made[0] += 1
            if _thumb_made[0] % 50 == 0:
                _trim_thumbs()
        except OSError:
            pass                                                      # no folder to keep it in: it is still returned
    return data, "image/jpeg"


def text(rel, start, inode):
    """The start of a text file: {"text", "cut", "encoding"}. At most 64 KB is read, UTF-8 first and Latin-1 if that fails."""
    data = _head(rel, start, inode, TEXT_CAP + 1)
    kind, _ = sniff(data[:4096])
    if kind != "text" and data:
        raise Err(415, "this does not look like a text file")
    cut = len(data) > TEXT_CAP
    data = data[:TEXT_CAP]
    try:
        return {"text": data.decode("utf-8"), "cut": cut, "encoding": "UTF-8"}
    except UnicodeDecodeError as e:
        if cut and e.start >= len(data) - 3:                         # the cut fell inside a UTF-8 character
            return {"text": data[:e.start].decode("utf-8"), "cut": True, "encoding": "UTF-8"}
        return {"text": data.decode("latin-1"), "cut": cut, "encoding": "Latin-1"}
