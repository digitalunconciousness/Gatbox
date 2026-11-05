"""The sync's door into the deck (docs/plans/2026-11-04-phase-d-archive.md, D3): what gatbox-sync reaches on loopback.

gatbox-sync runs as its own user and imports nothing from here, so it can't open the library's SQLite file or write the
manuals folder, and gatbox-web is the one process that does both. These routes (all loopback only) are the whole of what
the sync may do to the deck:

    GET  /api/library/sync/changes?since=&limit=    the deck's own change feed
    PUT  /api/library/sync/records                  records from the archive, each a compare-and-set against the revision the
                                                    sync saw (a record that moved is answered "conflict" and left alone)
    GET  /api/library/sync/digest                   the content digest, for "the two libraries are identical"
    GET  /api/library/sync/files                    {have: {sha256: size}, want: [documents the library lists with no file here]}
    GET  /api/library/sync/files/<sha256>           a part of a manual, by Range
    POST /api/library/sync/files, PUT .../parts/<n>, POST .../finish    a manual from the archive, in 8 MiB parts, staged in
                                                    gatbox-web's own state folder and placed in its game's folder when whole
    GET  /api/library/sync/resolutions              her choices from the 7" (KEEP THIS / KEEP THE ARCHIVE'S), for the sync
    POST /api/library/sync/resolutions/done         ... and the sync says which it has used

A manual is found by its SHA-256: a document the library lists with a `sha256`, and a PDF in one of the game's folders that
hashes to it (a sidecar's sha256 is trusted; a PDF without one is hashed once and remembered). The sync never writes
/srv/gatbox/manuals; a file placed here gets the same sidecar an upload does, and is never put over one that's there.
"""
import hashlib
import json
import os
import re
import threading
import time

from gatboxlib import library as L
from gatboxlib.filestore import PART_SIZE, FileError, FileStore

from . import config, libsource, manuals
from .meter import Bad

RES_FILE = "library-resolutions.json"
WRITES_MAX = 100
BODY = 16 * 1024 * 1024
_hashes = {}                                      # (path, size, mtime_ns) -> sha256, for PDFs with no sidecar sha256
_lock = threading.Lock()
SHA = re.compile(r"^[0-9a-f]{64}$")


def _store():
    return FileStore(os.path.join(config.CTRL, "library-files"), 400 * 1024 * 1024)


def _lib():
    try:
        if os.path.exists(libsource.PATH):
            lib = L.Library(libsource.PATH, create=False)
            if lib.meta("seeded_at"):
                return lib
    except (OSError, ValueError):
        pass
    raise Bad(409, "this deck's library isn't set up yet (gatbox-library seed), so there is nothing to sync")


def local_only(h):
    if not h.is_local():
        raise Bad(403, "the library sync is for the Pi itself")


def q1(q, name, default=""):
    return (q.get(name) or [default])[0]


def _int(raw, name, default, lo, hi):
    if raw in (None, ""):
        return default
    try:
        n = int(raw)
    except ValueError:
        raise Bad(400, f"{name}: a whole number") from None
    if not lo <= n <= hi:
        raise Bad(400, f"{name}: {lo} to {hi}")
    return n


# --- the feed and the writes ------------------------------------------------------------------------------------------------

def changes(q):
    lib = _lib()
    return lib.changes(_int(q1(q, "since"), "since", 0, 0, 2 ** 62), _int(q1(q, "limit"), "limit", 500, 1, 500))


def digest():
    return _lib().content_digest()


def read_json(h):
    if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
        raise Bad(415, "Content-Type must be application/json")
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        raise Bad(400, "bad Content-Length") from None
    if not 0 < n <= BODY:
        raise Bad(413, f"the body is at most {BODY} bytes")
    try:
        return json.loads(h.rfile.read(n))
    except ValueError:
        raise Bad(400, "the body is not JSON") from None


def apply(body):
    """One compare-and-set write per record, in order; one refused doesn't stop the rest. The archive's own rules were applied
    where the record was made, and the library applies the format's rules again here, so a record this deck can't take comes back
    "rejected" with the reason."""
    lib = _lib()
    records = body.get("records") if isinstance(body, dict) else None
    if not isinstance(records, list) or not 0 < len(records) <= WRITES_MAX:
        raise Bad(400, f"records: a list of 1 to {WRITES_MAX}")
    out = []
    for raw in records:
        if not isinstance(raw, dict):
            out.append({"status": "rejected", "reason": "a record must be an object"})
            continue
        res = {"kind": raw.get("kind"), "id": raw.get("id"), "uid": raw.get("uid")}
        if not isinstance(raw.get("kind"), str) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("uid"), str) \
                or isinstance(raw.get("if_rev"), bool) or not isinstance(raw.get("if_rev"), int):
            out.append(dict(res, status="rejected", reason="kind, id, uid (text) and if_rev (a whole number) are required"))
            continue
        by = raw.get("by") if isinstance(raw.get("by"), str) and raw["by"] else "archive"
        at = raw.get("at") if isinstance(raw.get("at"), (int, float)) and not isinstance(raw.get("at"), bool) else None
        r = lib.put(raw["kind"], raw["id"], raw.get("data"), raw["if_rev"], uid=raw["uid"], by=by[:60], at=at, deleted=bool(raw.get("deleted")))
        rec = r.get("record")
        item = dict(res, status=r["result"])
        if r["result"] in ("created", "updated", "duplicate") and rec:
            item.update(rev=rec["rev"], hash=rec["hash"])
        elif r["result"] == "conflict":
            item["current"] = rec
        elif r["result"] == "rejected":
            item["reason"] = r.get("reason")
        out.append(item)
    try:
        libsource.refresh_export()                 # a limit that came down is the logger's within a request, not three seconds
    except OSError:
        pass
    return {"results": out}


# --- manuals, by hash ---------------------------------------------------------------------------------------------------------

def _sha_of(path):
    sidecar = path[:-4] + ".json"
    try:
        with open(sidecar, encoding="utf-8") as f:
            sha = json.load(f).get("sha256")
        if isinstance(sha, str) and SHA.match(sha) and not path.endswith(".bin"):       # a dump's bytes are hashed, not trusted to a sidecar
            return sha
    except (OSError, ValueError, AttributeError):
        pass
    st = os.stat(path)
    k = (path, st.st_size, st.st_mtime_ns)
    with _lock:
        if k in _hashes:
            return _hashes[k]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    with _lock:
        _hashes[k] = h.hexdigest()
    return _hashes[k]


def index(lib):
    """{sha256: path} for every PDF in the folders of the games the library lists documents for."""
    out = {}
    wanted = {d["data"]["sha256"] for d in lib.all("document") if d["data"]["sha256"]}
    if not wanted:
        return out
    seen = set()
    v = libsource.current()
    for d in lib.all("document"):
        key = d["data"]["model_key"]
        if key in seen:
            continue
        seen.add(key)
        for folder in (v.folders(key) if v else [key]):
            base = os.path.join(config.MANUALS, folder)
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for n in names:
                if n.lower().endswith(".pdf") and not n.startswith(".") and n[:-4] != "":
                    p = os.path.join(base, n)
                    try:
                        sha = _sha_of(p)
                    except OSError:
                        continue
                    if sha in wanted:
                        out.setdefault(sha, p)
    return out


def images(lib):
    """{sha256: path} for every ROM image the library lists (a rom_image's id is its bytes' SHA-256) that is in the dump archive:
    <machine>/<label>_<sha1:8>.bin and _images/*.bin. These only go up: a rebuilt deck's dump archive is not refilled by the sync."""
    wanted = {r["id"] for r in lib.all("rom_image")}
    out = {}
    if not wanted:
        return out
    for pattern in ("*", "_images"):
        base = config.ROMS
        try:
            folders = [os.path.join(base, n) for n in sorted(os.listdir(base))] if pattern == "*" else [os.path.join(base, "_images")]
        except OSError:
            continue
        for folder in folders:
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for n in names:
                p = os.path.join(folder, n)
                if n.endswith(".bin") and not n.startswith(".") and os.path.isfile(p):
                    try:
                        sha = _sha_of(p)
                    except OSError:
                        continue
                    if sha in wanted:
                        out.setdefault(sha, p)
    return out


def files():
    lib = _lib()
    adopt_staged(lib)
    idx = index(lib)
    have = {sha: os.path.getsize(p) for sha, p in {**images(lib), **idx}.items()}
    want, seen = [], set()
    for d in lib.all("document"):
        x = d["data"]
        if x["sha256"] and x["sha256"] not in idx and x["sha256"] not in seen:
            seen.add(x["sha256"])
            want.append({"sha256": x["sha256"], "size": x["bytes"], "model_key": x["model_key"], "doc_id": x["doc_id"], "title": x["title"]})
    return {"have": have, "want": want}


def read_range(sha, header):
    """(bytes, start, end, total) of one part of a manual the deck holds."""
    lib = _lib()
    path = (index(lib).get(sha) or images(lib).get(sha)) if SHA.match(sha) else None
    if path is None:
        raise Bad(404, "no such file here")
    total = os.path.getsize(path)
    hit = re.fullmatch(r"bytes=(\d+)-(\d*)", (header or "").strip())
    start = int(hit.group(1)) if hit else 0
    end = min(int(hit.group(2)) if hit and hit.group(2) else start + PART_SIZE - 1, start + PART_SIZE - 1, total - 1)
    if start >= total or end < start:
        raise Bad(416, "that range is outside the file")
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(end - start + 1), start, end, total


# --- manuals from the archive ---------------------------------------------------------------------------------------------------

def stage_begin(body):
    _lib()
    if not isinstance(body, dict):
        raise Bad(400, "the body must be an object")
    try:
        return _store().begin(body.get("sha256"), body.get("size"))
    except FileError as e:
        raise Bad(e.code, str(e)) from None


def stage_part(sha, n, data):
    _lib()
    try:
        return _store().put_part(sha, n, data)
    except FileError as e:
        raise Bad(e.code, str(e)) from None


def stage_finish(sha):
    lib = _lib()
    try:
        out = _store().finish(sha)
    except FileError as e:
        raise Bad(e.code, str(e)) from None
    out["placed"] = place(lib, sha)
    return out


def place(lib, sha):
    """Put a whole, verified file in the folder of every game whose document names it and has no file here. The staged copy is
    removed once it is placed. -> [{game, file}]."""
    store = _store()
    src = store.path(sha)
    if not os.path.isfile(src) or index(lib).get(sha):
        return []
    v = libsource.current()
    placed = []
    for d in lib.all("document"):
        x = d["data"]
        if x["sha256"] != sha:
            continue
        key = x["model_key"]
        folder = (v.folders(key)[0] if v else key)
        name = _put_file(src, os.path.join(config.MANUALS, folder), x, sha)
        placed.append({"game": key, "file": name})
    if placed:
        try:
            os.unlink(src)
        except OSError:
            pass
    return placed


def _put_file(src, dest, doc, sha):
    """Copy the PDF into `dest` as <doc_id>.pdf (a different file already there: a suffix), with the sidecar an upload makes."""
    os.makedirs(dest, exist_ok=True)
    try:
        os.chmod(dest, 0o2775)
    except OSError:
        pass
    stem, i = doc["doc_id"], 2
    while os.path.exists(os.path.join(dest, stem + ".pdf")):
        stem, i = f"{doc['doc_id']}-{i}", i + 1
    final = os.path.join(dest, stem + ".pdf")
    tmp = os.path.join(dest, f".sync-{sha[:12]}.tmp")
    try:
        with open(src, "rb") as a, open(tmp, "wb") as b:
            while chunk := a.read(1 << 20):
                b.write(chunk)
            b.flush()
            os.fsync(b.fileno())
        os.chmod(tmp, 0o664)
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    side = {"id": stem, "title": doc["title"], "kind": doc["kind"], "source": doc["source"] or "the archive", "sha256": sha,
            "pages": doc["pages"] or manuals.pages_of(final), "bytes": os.path.getsize(final), "added": "archive",
            "date": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with open(final[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f, indent=1)
    try:
        os.chmod(final[:-4] + ".json", 0o664)
    except OSError:
        pass
    return stem + ".pdf"


def adopt_staged(lib):
    """A file that finished before its document arrived (or was wanted by a document added since) is placed now; staged files
    nothing wants for a week are removed."""
    root = os.path.join(config.CTRL, "library-files")
    if not os.path.isdir(root):
        return
    want = {d["data"]["sha256"] for d in lib.all("document") if d["data"]["sha256"]}
    store = _store()
    for sub in os.listdir(root):
        if len(sub) != 2:
            continue
        for sha in os.listdir(os.path.join(root, sub)):
            p = os.path.join(root, sub, sha)
            if sha in want:
                place(lib, sha)
            else:
                try:
                    if time.time() - os.stat(p).st_mtime > 7 * 24 * 3600:
                        os.unlink(p)
                except OSError:
                    pass
    store.sweep()


# --- her choices from the 7" ---------------------------------------------------------------------------------------------------

def _res_path():
    return os.path.join(config.CTRL, RES_FILE)


def resolutions():
    try:
        with open(_res_path(), encoding="utf-8") as f:
            rows = json.load(f).get("resolutions", [])
    except (OSError, ValueError, AttributeError):
        rows = []
    return {"resolutions": [r for r in rows if isinstance(r, dict) and r.get("keep") in ("deck", "archive") and isinstance(r.get("key"), str)]}


def resolutions_done(body):
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, list):
        raise Bad(400, "keys: a list")
    left = [r for r in resolutions()["resolutions"] if r["key"] not in keys]
    os.makedirs(config.CTRL, exist_ok=True)
    tmp = _res_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"resolutions": left}, f)
    os.replace(tmp, _res_path())
    return {"left": len(left)}
