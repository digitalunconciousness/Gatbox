"""The archive's library calls, apart from the HTTP: the change feed (with a reader's `models=` filter), the batch of
compare-and-set writes (with what a person on a phone may and may not change), and which files a reader may take.
"""
import json
import time
import uuid

from gatboxlib import library as L

MAX_RECORDS = 100                                    # writes in one PUT
PERSON_KINDS = ("game", "document", "mame", "rail_limit", "fact")      # what her partner edits on his phone (D-1)
MODEL_KEY = L.key_field


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def model_of(rec):
    """The game a record belongs to: a game is its own key; every other kind names its game (a ROM image may name none)."""
    return rec["id"] if rec["kind"] == "game" else (rec["data"] or {}).get("model_key")


def models_param(raw):
    """`models=a,b,c` -> ['a','b','c'], each a model_key; None when there's no filter."""
    if raw is None or raw == "":
        return None
    keys = raw.split(",")
    if len(keys) > 500:
        raise ApiError("models: at most 500 keys")
    try:
        return [MODEL_KEY(k) for k in keys]
    except L.Reject as e:
        raise ApiError(f"models: {e}") from None


def int_param(raw, name, default, lo, hi):
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except ValueError:
        raise ApiError(f"{name}: a whole number") from None
    if not lo <= n <= hi:
        raise ApiError(f"{name}: {lo} to {hi}")
    return n


def changes(lib, since, limit, models=None):
    """{records, cursor, more}. With `models`, only those games' records (and nothing else): a page may come back short,
    even empty, with `more` true: the cursor still moves past what was skipped, so a reader keeps asking while `more`."""
    page = lib.changes(since, limit)
    if models is not None:
        wanted = set(models)
        page["records"] = [r for r in page["records"] if model_of(r) in wanted]
    return page


def _shape(raw):
    """A write's own fields, or the reason it can't be read as one."""
    if not isinstance(raw, dict):
        return "a record must be an object"
    for name, kind in (("kind", str), ("id", str)):
        if not isinstance(raw.get(name), kind):
            return f"{name}: text"
    if raw.get("uid") is not None and not isinstance(raw["uid"], str):
        return "uid: text"
    if isinstance(raw.get("if_rev"), bool) or not isinstance(raw.get("if_rev"), int):
        return "if_rev: 0 to create, else the revision being replaced"
    if raw.get("deleted") not in (None, True, False):
        return "deleted: true or false"
    if raw.get("at") is not None and (isinstance(raw["at"], bool) or not isinstance(raw["at"], (int, float))):
        return "at: epoch seconds"
    if raw.get("by") is not None and not isinstance(raw["by"], str):
        return "by: text"
    return None


def apply_writes(lib, records, who):
    """One result per write, in order: {kind, id, uid, status, ...}. `who` is {"kind": "device"|"person", "name": ...}.
    Each write is its own compare-and-set: one refused doesn't stop the rest (a game goes before its children, so the
    sender puts it first). A device names itself and the edit's time; a person is named by Access and stamped now, may
    touch only PERSON_KINDS, and can neither confirm nor unconfirm a rail limit (that is the 7")."""
    if not isinstance(records, list) or not records:
        raise ApiError("records: a non-empty list")
    if len(records) > MAX_RECORDS:
        raise ApiError(f"at most {MAX_RECORDS} records per request, got {len(records)}")
    person = who["kind"] == "person"
    out = []
    for raw in records:
        res = {"kind": raw.get("kind") if isinstance(raw, dict) else None, "id": raw.get("id") if isinstance(raw, dict) else None,
               "uid": raw.get("uid") if isinstance(raw, dict) else None}
        bad = _shape(raw)
        if bad:
            out.append(dict(res, status="rejected", reason=bad))
            continue
        kind, rid, deleted = raw["kind"], raw["id"], bool(raw.get("deleted"))
        uid = raw.get("uid") or (uuid.uuid4().hex if person else None)
        if uid is None:
            out.append(dict(res, status="rejected", reason="uid: minted once per edit, so a retry is harmless"))
            continue
        res["uid"] = uid
        data, by, at = raw.get("data"), raw.get("by") or who["name"], raw.get("at")
        if person:
            by, at = who["name"], time.time()
            if kind not in PERSON_KINDS:
                out.append(dict(res, status="rejected", reason=f"{kind} records are the deck's: not edited here"))
                continue
            if kind == "rail_limit" and not deleted:
                if not isinstance(data, dict):
                    out.append(dict(res, status="rejected", reason="data must be an object"))
                    continue
                cur = lib.get("rail_limit", rid)
                held = cur["data"]["confirmed"] if cur else None
                if "confirmed" in data and data["confirmed"] != held:
                    out.append(dict(res, status="rejected", reason="confirming a limit happens on the 7\", beside its manual page"))
                    continue
                data = dict(data, confirmed=held)
        r = lib.put(kind, rid, data, raw["if_rev"], uid=uid, by=by, at=at, deleted=deleted)
        rec = r.get("record")
        item = dict(res, status=r["result"])
        if r["result"] in ("created", "updated", "duplicate") and rec:
            item.update(rev=rec["rev"], hash=rec["hash"])
        elif r["result"] == "conflict":
            item["current"] = rec
        elif r["result"] == "rejected":
            item["reason"] = r.get("reason")
        out.append(item)
    return out


def document_hashes(lib):
    """The SHA-256 of every manual the library lists: the only files a reader token may take (ROM images stay here)."""
    return {r["data"]["sha256"] for r in lib.all("document") if r["data"]["sha256"]}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
