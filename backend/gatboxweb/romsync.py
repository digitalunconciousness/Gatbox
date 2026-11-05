"""What gatbox-sync sends a hub of the dump archive (phase C): the dumps, the burns, and their images.

gatbox-sync has no access to /srv/gatbox/roms (its sandbox is one loopback socket, one credential and its own
state), so it reads everything from here, the way it reads sessions. Three reads, all **loopback only**: a phone on
the deck's Wi-Fi never gets a ROM image off this box, and the hubs get them only through the sync.

    GET /api/sync/dumps                 every archived dump, with what the contract's rom_dump carries and the SHA-256
    GET /api/sync/burns                 every line of burns.jsonl, its image resolved to the archive and hashed
    GET /api/sync/image?name=&part=     part n (8 MiB) of one archived image: <machine>/<file>.bin or _images/<name>.bin

An entry that can't be sent carries a `hold`: why (no machine, not read twice identically, the file no longer
matches its sidecar, over the hub's 32 MiB, the image gone). The sync shows the reason and sends nothing. Nothing here
writes: the archive belongs to gatbox-dump.
"""
import glob
import hashlib
import json
import os
import re
import threading

from . import config
from .meter import Bad

PART_SIZE = 8 * 1024 * 1024               # contract v1, "Files"
MAX_FILE = 32 * 1024 * 1024               # a bigger image stays on the deck (and goes to the archive in phase D)
NAME = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._ -]*\.bin)$")     # _images/ is a folder here too
_hashes = {}                              # (real path, size, mtime_ns) -> (sha1, sha256)
_lock = threading.Lock()


def _inside(path):
    """The real path of a file inside the archive, or None: a link out of it is never read."""
    root = os.path.realpath(config.ROMS)
    real = os.path.realpath(path)
    return real if real.startswith(root + os.sep) and os.path.isfile(real) else None


def _digests(real):
    """(sha1, sha256) of an archived file, kept until the file changes."""
    st = os.stat(real)
    key = (real, st.st_size, st.st_mtime_ns)
    with _lock:
        if key not in _hashes:
            sha1, sha256 = hashlib.sha1(), hashlib.sha256()
            with open(real, "rb") as f:
                while chunk := f.read(1 << 20):
                    sha1.update(chunk)
                    sha256.update(chunk)
            _hashes[key] = (sha1.hexdigest(), sha256.hexdigest())
        return _hashes[key]


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else None
    except (OSError, ValueError):
        return None


def _hold_for(machine, real, size, sha1):
    """Why an image can't be sent as it is, or None."""
    if not machine or machine == "unassigned":
        return "no machine: it goes to the archive only"
    if real is None:
        return "the image is gone from the archive"
    if os.path.getsize(real) > MAX_FILE:
        return "over the hubs' 32 MiB: it stays on the deck"
    if size is not None and os.path.getsize(real) != size:
        return "the file's size isn't the one recorded when it was dumped"
    have = _digests(real)[0]
    if sha1 and have != sha1:
        return "the file's SHA-1 isn't the one recorded when it was dumped"
    return None


def dumps():
    """[{key, machine, file, label, part, size, sha1, crc32, sha256, romident, reads, identical, blank, epoch,
    clock, order, hold}] for every archived dump, oldest first."""
    out = []
    for side in sorted(glob.glob(os.path.join(config.ROMS, "*", "*.json"))):
        folder = os.path.basename(os.path.dirname(side))
        j = _json(side)
        if folder == "_images" or not j or not j.get("sha1"):
            continue
        name = os.path.basename(side)[:-5] + ".bin"
        real = _inside(os.path.join(os.path.dirname(side), name))
        hold = _hold_for(folder, real, j.get("size"), j.get("sha1"))
        if hold is None and (j.get("identical") is not True or (j.get("reads_compared") or 0) < 2):
            hold = "it wasn't read twice with identical results"
        out.append({"key": f"dump:{folder}/{name}", "machine": folder, "file": f"{folder}/{name}",
                    "label": j.get("label"), "part": j.get("part"), "size": os.path.getsize(real) if real else j.get("size"),
                    "sha1": j.get("sha1"), "crc32": j.get("crc32"), "sha256": _digests(real)[1] if real else None,
                    "romident": j.get("romident"), "reads": j.get("reads_compared"), "identical": j.get("identical"),
                    "blank": bool(j.get("blank")), "epoch": j.get("epoch"), "clock": j.get("clock") or "unknown",
                    "order": j.get("order"), "hold": hold})
    return sorted(out, key=lambda d: (d["epoch"] or 0, d["key"]))


def _burn_key(epoch, sha1):
    return f"burn:{float(epoch):.3f}:{sha1}"


def burns():
    """[{key, machine, image, size, sha1, sha256, part, vpp, blank_ok, state, error, epoch, order, hold}] for every
    line of burns.jsonl, its image resolved to a file in the archive (and so named by archive-relative path)."""
    out = []
    try:
        with open(os.path.join(config.ROMS, "burns.jsonl"), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    root = os.path.realpath(config.ROMS)
    for raw in lines:
        try:
            b = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(b, dict) or not isinstance(b.get("epoch"), (int, float)):
            continue
        real = _inside(str(b.get("image") or "")) if b.get("image") else None
        rel = os.path.relpath(real, root) if real else None
        sha1 = b.get("sha1")
        hold = _hold_for(b.get("machine"), real, b.get("size"), sha1)
        if hold is None and not sha1:
            hold = "the burn stopped before its image was read: there's no hash to send"
        out.append({"key": _burn_key(b["epoch"], sha1), "machine": b.get("machine"), "image": rel,
                    "size": b.get("size"), "sha1": sha1, "sha256": _digests(real)[1] if real else None,
                    "part": b.get("part"), "vpp": b.get("vpp"), "blank_ok": b.get("blank_ok"),
                    "state": b.get("state"), "error": b.get("error"), "epoch": b["epoch"],
                    "order": b.get("order"), "hold": hold})
    return sorted(out, key=lambda x: (x["epoch"], x["key"]))


def image_part(name, part):
    """Bytes of part `part` of one archived image: the whole file when it fits one part."""
    m = NAME.match(name or "")
    if not m:
        raise Bad(400, "name: <machine>/<file>.bin, as the archive names it")
    real = _inside(os.path.join(config.ROMS, m.group(1), m.group(2)))
    if real is None:
        raise Bad(404, f"no such image: {name!r}")
    try:
        n = int(part)
    except (TypeError, ValueError):
        raise Bad(400, "part: a number from 0") from None
    size = os.path.getsize(real)
    if size > MAX_FILE:
        raise Bad(413, "that image is over 32 MiB and isn't sent to a hub")
    if not 0 <= n * PART_SIZE < max(size, 1):
        raise Bad(404, f"part {n} is outside the file")
    with open(real, "rb") as f:
        f.seek(n * PART_SIZE)
        return f.read(PART_SIZE)
