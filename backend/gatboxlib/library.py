"""The game library's store: what the owner knows about each game, in one SQLite file
(docs/plans/2026-11-03-phase-b-library.md; the design is docs/superpowers/specs/2026-10-28-shared-game-library-design.md).

Every record is (kind, id) with a revision, a hash of its canonical JSON and the uid of the edit that made it. Writes
are compare-and-set: `if_rev` 0 creates, n replaces only revision n. A resend of the same uid is `duplicate`, never a
false conflict. The deck and, later, the archive both run this module, so they can't disagree about the format.

No library record names a machine, a customer, a site or a world (design §2.3). Games are never deleted: they retire
with `replaced_by`. A rail limit's `confirmed` is cleared by the store whenever its numbers or its page change, so the
logger can only ever trust what a person confirmed (hard rule 12).

Stdlib only. The logger never opens this file: gatbox-web exports the confirmed limits as flat JSON for it.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import closing
from decimal import Decimal, InvalidOperation

KEY_MAX = 60                       # shop-hub's column; the deck's slug check allows 64
RESERVED_KEYS = {"unassigned"}     # captures and dumps use it for "no machine"
ROM_LIST_MAX = 2 * 1024 * 1024
KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
SCHEMA_VERSION = 1


class Reject(ValueError):
    """A record the store won't take, with the reason."""


# --- canonical JSON, hashes, keys ---------------------------------------------------------------------------------------

def _nfc(v):
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, list):
        return [_nfc(x) for x in v]
    if isinstance(v, dict):
        return {_nfc(k): _nfc(x) for k, x in v.items()}
    return v


def canonical(data):
    """Sorted keys, no whitespace, UTF-8, NFC strings: the text a record's hash is taken over."""
    return json.dumps(_nfc(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_of(data):
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def slug(text):
    """Lower-case words joined by hyphens, apostrophes dropped, "&" read as "and", accents folded to ASCII. The same
    rule as gatboxweb.roster.slugify (test-library checks they agree)."""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`]", "", s.replace("&", " and "))
    return "-".join(re.findall(r"[a-z0-9]+", s))


def model_key(title, kind="video", mfr=None, year=None, taken=()):
    """A game's permanent key: its title slug (pinball keys start with `pin-`). A title that's taken gets the maker as a
    suffix, then the year. The suffix is kept whole and the title part is cut to fit 60 characters."""
    base = slug(title)
    if not base:
        raise ValueError("no key can be made from that title: type one")
    if kind == "pinball":
        base = "pin-" + base
    for extra in ((), (mfr,), (mfr, year)):
        suffix = "".join("-" + slug(x) for x in extra if x and slug(x))
        if len(suffix) >= KEY_MAX:
            continue
        cand = (base[:KEY_MAX - len(suffix)].rstrip("-") + suffix) if suffix else base[:KEY_MAX].rstrip("-")
        if cand and cand not in taken and cand not in RESERVED_KEYS:
            return cand
    raise ValueError(f"{base}: every key for this title is taken: type one")


def volts(v):
    """A decimal string with no trailing zeros ("4.750" -> "4.75", "5.0" -> "5"). Floats and signs of "+" are refused so
    both sides hash the same text."""
    if not isinstance(v, str) or not re.fullmatch(r"-?\d+(\.\d+)?", v):
        raise Reject("a decimal string such as \"4.75\"")
    try:
        d = Decimal(v).normalize()
    except InvalidOperation:
        raise Reject("a decimal string such as \"4.75\"") from None
    out = format(d, "f")
    return "0" if out in ("-0", "") else out


# --- what each kind holds ----------------------------------------------------------------------------------------------

REQ = object()


def text(n, multiline=False):
    def f(v):
        if not isinstance(v, str):
            raise Reject("text")
        v = unicodedata.normalize("NFC", v)
        if len(v) > n:
            raise Reject(f"at most {n} characters")
        if any(unicodedata.category(c) == "Cc" and not (multiline and c == "\n") for c in v):
            raise Reject("no control characters")
        return v
    return f


def nonempty(inner):
    def f(v):
        v = inner(v)
        if not v.strip():
            raise Reject("required")
        return v
    return f


def enum(*vals):
    def f(v):
        if v not in vals:
            raise Reject("one of " + ", ".join(map(str, vals)))
        return v
    return f


def integer(lo, hi):
    def f(v):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise Reject(f"an integer from {lo} to {hi}")
        return v
    return f


def nullable(inner):
    return lambda v: None if v is None else inner(v)


def key_field(v):
    if not isinstance(v, str) or not KEY.match(v) or len(v) > KEY_MAX or v in RESERVED_KEYS:
        raise Reject("a model_key (lower-case words joined by hyphens, at most 60 characters)")
    return v


def doc_id_field(v):
    if not isinstance(v, str) or not DOC_ID.match(v):
        raise Reject("a document id (letters, digits, '.', '_', '-')")
    return v


def hexn(n):
    def f(v):
        if not isinstance(v, str) or not re.fullmatch(f"[0-9a-f]{{{n}}}", v):
            raise Reject(f"{n} lower-case hex digits")
        return v
    return f


def https_url(n):
    def f(v):
        v = text(n)(v)
        if not v.startswith("https://"):
            raise Reject("an https:// address")
        return v
    return f


def confirmed(v):
    if v is None:
        return None
    if (not isinstance(v, dict) or set(v) != {"by", "at"} or not isinstance(v["by"], str) or not v["by"]
            or isinstance(v["at"], bool) or not isinstance(v["at"], (int, float))):
        raise Reject("null, or {\"by\": who, \"at\": when}")
    return {"by": text(60)(v["by"]), "at": v["at"]}


def _opt_text(n):
    return nullable(text(n))


def rom_entries(v):
    """The ROMs of one MAME version, as `mame -listxml` gives them (gatbox-mame-roms keeps hashes only, no ROM data)."""
    if not isinstance(v, list):
        raise Reject("a list of ROMs")
    out = []
    for r in v:
        if not isinstance(r, dict) or set(r) - {"name", "size", "crc", "sha1", "region", "status", "bios"} \
                or not isinstance(r.get("name"), str):
            raise Reject("each ROM is {name, size, crc, sha1, region, status, bios}")
        out.append({"name": text(120)(r["name"]), "size": nullable(integer(0, 1 << 32))(r.get("size")),
                    "crc": _opt_text(8)(r.get("crc")), "sha1": _opt_text(40)(r.get("sha1")),
                    "region": _opt_text(60)(r.get("region")), "status": _opt_text(20)(r.get("status")),
                    "bios": _opt_text(60)(r.get("bios"))})
    return out


def rom_disks(v):
    """A version's CHDs (hard disk or CD images), by hash: what a disk image is checked against."""
    if not isinstance(v, list):
        raise Reject("a list of disks")
    out = []
    for d in v:
        if not isinstance(d, dict) or set(d) - {"name", "sha1", "region", "status"} or not isinstance(d.get("name"), str):
            raise Reject("each disk is {name, sha1, region, status}")
        out.append({"name": text(120)(d["name"]), "sha1": _opt_text(40)(d.get("sha1")),
                    "region": _opt_text(60)(d.get("region")), "status": _opt_text(20)(d.get("status"))})
    return out


def rom_versions(v):
    if not isinstance(v, list):
        raise Reject("a list of versions")
    out = []
    for x in v:
        if not isinstance(x, dict) or set(x) - {"name", "parent", "description", "year", "mfr", "roms", "disks"} \
                or not isinstance(x.get("name"), str):
            raise Reject("each version is {name, parent, description, year, mfr, roms, disks}")
        out.append({"name": text(60)(x["name"]), "parent": bool(x.get("parent")),
                    "description": text(200)(x.get("description", "")), "year": text(20)(x.get("year", "")),
                    "mfr": text(120)(x.get("mfr", "")), "roms": rom_entries(x.get("roms", [])),
                    "disks": rom_disks(x.get("disks", []))})
    return out


def romident_field(v):
    if v is None:
        return None
    if not isinstance(v, dict):
        raise Reject("MAME's answer as an object, or null")
    return v


DOC_KINDS = ("manual", "schematics", "parts", "kit", "bulletin", "other", "yours")
FACT_GROUPS = ("fuse", "monitor", "psu", "power", "other")

# kind -> {field: (check, default)}; REQ means the field must be given. Unknown fields are refused.
KINDS = {
    "game": {"title": (nonempty(text(120)), REQ), "mfr": (text(120), ""), "year": (nullable(integer(1900, 2100)), None),
             "kind": (enum("video", "pinball", "other"), REQ), "notes": (text(2000, True), ""),
             "replaced_by": (nullable(key_field), None)},
    "document": {"model_key": (key_field, REQ), "doc_id": (doc_id_field, REQ), "title": (nonempty(text(120)), REQ),
                 "kind": (enum(*DOC_KINDS), REQ), "source_url": (nullable(https_url(500)), None),
                 "source": (text(200), ""), "sha256": (nullable(hexn(64)), None),
                 "pages": (nullable(integer(0, 100000)), None), "bytes": (nullable(integer(0, 1 << 31)), None)},
    "mame": {"model_key": (key_field, REQ), "set": (nullable(text(60)), None), "prefer": (nullable(text(60)), None),
             "status": (nullable(enum("sure", "check")), None), "why": (text(500), "")},     # null: not in MAME
    "rom_list": {"model_key": (key_field, REQ), "mame": (text(60), ""), "set": (nullable(text(60)), None),
                 "versions": (rom_versions, REQ)},
    "rom_image": {"size": (integer(0, 1 << 31), REQ), "crc32": (hexn(8), REQ), "sha1": (hexn(40), REQ),
                  "label": (text(120), ""), "part": (text(60), ""), "romident": (romident_field, None),
                  "model_key": (nullable(key_field), None), "chip": (text(60), ""),
                  "verdict": (enum("good", "bad", "unknown"), "unknown"), "revision": (text(60), ""),
                  "note": (text(500, True), "")},
    "rail_limit": {"model_key": (key_field, REQ), "rail": (nonempty(text(20)), REQ), "lo": (volts, REQ),
                   "hi": (volts, REQ), "doc_id": (nullable(doc_id_field), None), "page": (nullable(integer(1, 100000)), None),
                   "quote": (text(500, True), ""), "confirmed": (confirmed, None)},
    "fact": {"model_key": (key_field, REQ), "group": (enum(*FACT_GROUPS), REQ), "what": (nonempty(text(60)), REQ),
             "value": (text(120), ""), "doc_id": (nullable(doc_id_field), None),
             "page": (nullable(integer(1, 100000)), None), "quote": (text(500, True), "")},
}
GAME_CHILDREN = ("document", "mame", "rom_list", "rail_limit", "fact")      # each names its game by model_key
LIMIT_NUMBERS = ("lo", "hi", "doc_id", "page")          # a change to any of these clears `confirmed`


def check_id(kind, rid, data):
    """The id a kind's records must carry, given its data."""
    if kind in ("game", "mame", "rom_list"):
        key_field(rid)
        if kind != "game" and rid != data["model_key"]:
            raise Reject("the id is the model_key")
    elif kind == "document":
        if rid != f"{data['model_key']}/{data['doc_id']}":
            raise Reject("the id is <model_key>/<doc_id>")
    elif kind == "rom_image":
        hexn(64)(rid)                                  # the image's sha256: it names the file
    elif kind in ("rail_limit", "fact"):
        if not isinstance(rid, str) or not HEX32.match(rid):
            raise Reject("the id is 32 lower-case hex digits")


def validate(kind, data):
    """The record's data in its stored form: every field present, checked and normalized. Raises Reject."""
    fields = KINDS.get(kind)
    if fields is None:
        raise Reject(f"unknown kind {kind!r}")
    if not isinstance(data, dict):
        raise Reject("data must be an object")
    extra = sorted(set(data) - set(fields))
    if extra:
        raise Reject("unknown field " + ", ".join(extra))
    out = {}
    for name, (check, default) in fields.items():
        if name not in data:
            if default is REQ:
                raise Reject(f"{name}: required")
            out[name] = default
            continue
        try:
            out[name] = check(data[name])
        except Reject as e:
            raise Reject(f"{name}: {e}") from None
    if kind == "rom_list" and len(canonical(out).encode("utf-8")) > ROM_LIST_MAX:
        raise Reject("a ROM list is at most 2 MB")
    if kind == "rail_limit" and Decimal(out["lo"]) > Decimal(out["hi"]):
        raise Reject("lo is above hi")
    return out


# --- the store ---------------------------------------------------------------------------------------------------------

class Library:
    def __init__(self, path, create=True):
        self.path = path
        if create:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with closing(self._conn()) as c:
            if c.execute("PRAGMA user_version").fetchone()[0] == 0:
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS records (
                        kind TEXT NOT NULL, id TEXT NOT NULL, rev INTEGER NOT NULL, hash TEXT NOT NULL,
                        deleted INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL, by TEXT NOT NULL, seq INTEGER NOT NULL,
                        data TEXT NOT NULL, PRIMARY KEY (kind, id));
                    CREATE INDEX IF NOT EXISTS records_seq ON records (seq);
                    CREATE TABLE IF NOT EXISTS uids (uid TEXT PRIMARY KEY, kind TEXT NOT NULL, id TEXT NOT NULL,
                        rev INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """)
                c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    @staticmethod
    def _rec(row):
        return {"kind": row["kind"], "id": row["id"], "rev": row["rev"], "hash": row["hash"],
                "deleted": bool(row["deleted"]), "at": row["at"], "by": row["by"], "data": json.loads(row["data"])}

    # reads
    def get(self, kind, rid, deleted=False):
        with closing(self._conn()) as c:
            row = c.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
        return self._rec(row) if row and (deleted or not row["deleted"]) else None

    def all(self, kind, model_key=None, deleted=False):
        sql, args = "SELECT * FROM records WHERE kind=?", [kind]
        if not deleted:
            sql += " AND deleted=0"
        sql += " ORDER BY id"
        with closing(self._conn()) as c:
            rows = [self._rec(r) for r in c.execute(sql, args)]
        if model_key is not None:                      # a game's key is its id; the others name their game
            rows = [r for r in rows if (r["id"] if kind == "game" else r["data"].get("model_key")) == model_key]
        return rows

    def resolve(self, key):
        """The game a key stands for now: retired keys point at a survivor (a few hops at most)."""
        for _ in range(8):
            g = self.get("game", key)
            if g is None:
                return None
            if not g["data"]["replaced_by"]:
                return g
            key = g["data"]["replaced_by"]
        return None

    def changes(self, since=0, limit=500):
        """Records changed after the cursor, oldest first: {records, cursor, more, head}. A record changed twice shows once,
        at its latest revision. `head` is the newest cursor this library has: a reader whose saved cursor is above it knows
        the library was rebuilt, or restored from an older copy, and starts again from 0."""
        limit = max(1, min(int(limit), 500))
        with closing(self._conn()) as c:
            rows = c.execute("SELECT * FROM records WHERE seq>? ORDER BY seq LIMIT ?", (int(since), limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        with closing(self._conn()) as c:
            head = c.execute("SELECT COALESCE(MAX(seq), 0) FROM records").fetchone()[0]
        return {"records": [self._rec(r) for r in rows], "cursor": rows[-1]["seq"] if rows else int(since), "more": more,
                "head": head}

    def digest(self):
        """{count, digest} over every record, tombstones too: two libraries with the same history agree."""
        h = hashlib.sha256()
        with closing(self._conn()) as c:
            rows = c.execute("SELECT kind, id, rev, hash, deleted FROM records ORDER BY kind, id").fetchall()
        for r in rows:
            h.update(f"{r['kind']}\t{r['id']}\t{r['rev']}\t{r['hash']}\t{r['deleted']}\n".encode())
        return {"count": len(rows), "digest": h.hexdigest()}

    def content_digest(self):
        """{count, digest} over (kind, id, hash, deleted), leaving revisions out. The deck and the archive number a record's
        revisions independently (the archive sees only the latest of several edits), so they compare this one: two
        libraries holding the same content agree whatever order the edits reached each."""
        h = hashlib.sha256()
        with closing(self._conn()) as c:
            rows = c.execute("SELECT kind, id, hash, deleted FROM records ORDER BY kind, id").fetchall()
        for r in rows:
            h.update(f"{r['kind']}\t{r['id']}\t{r['hash']}\t{r['deleted']}\n".encode())
        return {"count": len(rows), "digest": h.hexdigest()}

    def counts(self):
        with closing(self._conn()) as c:
            return {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) AS n FROM records WHERE deleted=0 GROUP BY kind")}

    def size(self):
        return sum(os.path.getsize(p) for p in (self.path, self.path + "-wal") if os.path.exists(p))

    def meta(self, key, default=None):
        with closing(self._conn()) as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        with closing(self._conn()) as c:
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))

    # writes
    def put(self, kind, rid, data, if_rev, uid=None, by="gatbox", at=None, deleted=False):
        """One compare-and-set write. -> {"result": created | updated | duplicate | conflict | rejected, "record":
        the current record (None when there's none), "reason": ...}. Nothing is written unless it's created or updated."""
        uid = uid or uuid.uuid4().hex
        try:
            if not isinstance(uid, str) or not 0 < len(uid) <= 64:
                raise Reject("uid: 1-64 characters")
            if not isinstance(by, str) or not 0 < len(by) <= 60:
                raise Reject("by: 1-60 characters")
            if isinstance(if_rev, bool) or not isinstance(if_rev, int) or if_rev < 0:
                raise Reject("if_rev: 0 to create, else the revision being replaced")
            if kind not in KINDS:
                raise Reject(f"unknown kind {kind!r}")
            if kind == "game" and deleted:
                raise Reject("games are never deleted: retire one with replaced_by")
            if not isinstance(rid, str) or not 0 < len(rid) <= 200:
                raise Reject("id: 1-200 characters")
            if not deleted:
                data = validate(kind, data)
                check_id(kind, rid, data)
        except Reject as e:
            known = self.get(kind, rid, deleted=True) if kind in KINDS and isinstance(rid, str) else None
            return {"result": "rejected", "record": known, "reason": str(e)}
        with closing(self._conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                out = self._put(c, kind, rid, data, if_rev, uid, by, at if at is not None else time.time(), deleted)
                c.execute("COMMIT" if out["result"] in ("created", "updated") else "ROLLBACK")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return out

    def bulk(self, writes, by="gatbox", create_only=False, at=None):
        """Many records in ONE transaction: writes is [(kind, id, data)], games first. A record that isn't there is
        created. One that is there is kept as it is (create_only: the seed), or updated to this data (an import). A
        deleted one stays deleted. If any record is refused, nothing at all is written.
        -> {"created": n, "updated": n, "kept": n, "rejected": [{"kind", "id", "reason"}]}"""
        out = {"created": 0, "updated": 0, "kept": 0, "rejected": []}
        checked = []
        for kind, rid, data in writes:
            try:
                if kind not in KINDS:
                    raise Reject(f"unknown kind {kind!r}")
                data = validate(kind, data)
                check_id(kind, rid, data)
                checked.append((kind, rid, data))
            except Reject as e:
                out["rejected"].append({"kind": kind, "id": rid, "reason": str(e)})
        if out["rejected"]:
            return out
        order = {k: i for i, k in enumerate(KINDS)}                  # a game before its documents, limits and facts
        checked.sort(key=lambda w: order[w[0]])
        at = at if at is not None else time.time()
        with closing(self._conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                for kind, rid, data in checked:
                    row = c.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
                    cur = self._rec(row) if row else None
                    if cur and (cur["deleted"] or create_only):
                        out["kept"] += 1
                        continue
                    res = self._put(c, kind, rid, data, cur["rev"] if cur else 0, uuid.uuid4().hex, by, at, False)
                    if res["result"] in ("created", "updated"):
                        out[res["result"]] += 1
                    elif res["result"] == "duplicate":
                        out["kept"] += 1
                    else:
                        out["rejected"].append({"kind": kind, "id": rid, "reason": res.get("reason") or res["result"]})
                        break
                c.execute("COMMIT" if not out["rejected"] else "ROLLBACK")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        if out["rejected"]:
            out["created"] = out["updated"] = 0
        return out

    def _put(self, c, kind, rid, data, if_rev, uid, by, at, deleted):
        row = c.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
        cur = self._rec(row) if row else None
        seen = c.execute("SELECT kind, id FROM uids WHERE uid=?", (uid,)).fetchone()
        if seen:
            if (seen["kind"], seen["id"]) != (kind, rid):
                return {"result": "rejected", "record": cur, "reason": "uid already used for another record"}
            return {"result": "duplicate", "record": cur}
        if (cur["rev"] if cur else 0) != if_rev:
            return {"result": "conflict", "record": cur}
        if deleted:
            if cur is None or cur["deleted"]:
                return {"result": "conflict", "record": cur}
            data = cur["data"]
        else:
            problem = self._problem(c, kind, rid, data)
            if problem:
                return {"result": "rejected", "record": cur, "reason": problem}
            if cur and kind == "rail_limit" and any(cur["data"][f] != data[f] for f in LIMIT_NUMBERS):
                data = dict(data, confirmed=None)
            if cur and not cur["deleted"] and cur["hash"] == hash_of(data):
                return {"result": "duplicate", "record": cur}       # nothing changed
        rev = (cur["rev"] if cur else 0) + 1
        seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM records").fetchone()[0]
        c.execute("INSERT INTO records (kind, id, rev, hash, deleted, at, by, seq, data) VALUES (?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(kind, id) DO UPDATE SET rev=excluded.rev, hash=excluded.hash, deleted=excluded.deleted, "
                  "at=excluded.at, by=excluded.by, seq=excluded.seq, data=excluded.data",
                  (kind, rid, rev, hash_of(data), int(deleted), at, by, seq, json.dumps(data, ensure_ascii=False)))
        c.execute("INSERT INTO uids (uid, kind, id, rev) VALUES (?,?,?,?)", (uid, kind, rid, rev))
        new = c.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
        return {"result": "updated" if cur else "created", "record": self._rec(new)}

    @staticmethod
    def _problem(c, kind, rid, data):
        """What stops this data being stored, given what's already in the library (None if nothing does)."""
        def game_exists(k):
            r = c.execute("SELECT deleted FROM records WHERE kind='game' AND id=?", (k,)).fetchone()
            return r is not None and not r["deleted"]
        if kind in GAME_CHILDREN and not game_exists(data["model_key"]):
            return f"no game {data['model_key']!r}: a game comes before its documents, limits and facts"
        if kind == "rom_image" and data["model_key"] and not game_exists(data["model_key"]):
            return f"no game {data['model_key']!r}"
        if kind == "game" and data["replaced_by"]:
            target = data["replaced_by"]
            for _ in range(8):
                if target == rid:
                    return "replaced_by points back at this game"
                r = c.execute("SELECT data FROM records WHERE kind='game' AND id=?", (target,)).fetchone()
                if r is None:
                    return f"replaced_by: no game {target!r}"
                target = json.loads(r["data"]).get("replaced_by")
                if not target:
                    break
        return None
