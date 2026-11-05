"""The deck's side of the library's two-way sync with the archive (docs/plans/2026-11-04-phase-d-archive.md, slice D3).

Stdlib only. gatbox-sync calls `run()` once per timer tick. The deck's own library is reached through gatbox-web on
loopback (`Deck`), because gatbox-web is the one process that opens the SQLite file and writes the manuals folder; the
archive is reached over HTTPS (`Archive`). Everything else here is a pure merge over what those two return, so it is tested
without a network.

The merge, per record. Three states: what the deck holds (D), what the archive holds (A), and what both held the last time
they agreed (B, the base). A state is the record's hash, the word "deleted", or nothing.

    D == A                     agreed
    the deck has nothing       pull    (a rebuilt deck fills from the archive)
    the archive has nothing    push    (the archive lost it), unless the deck's is a tombstone: agreed
    D == B and A != B          pull    (only the archive changed it)
    A == B and D != B          push    (only the deck changed it)
    otherwise                  CONFLICT: nothing is written on either side

A conflict is kept, with both versions and what they started from, until a person picks one on the 7" (`resolve`), and no
write is ever forced: every write is a compare-and-set against the revision this run saw, so a record that moved meanwhile
is judged again next run, not overwritten. A lost reply is harmless: every write carries a uid derived from what it says.
State lives in `state_dir` (gatbox-sync's own folder), and deleting it is safe: the next run compares both libraries from
the start, agrees on everything identical and flags anything that differs.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

PART_SIZE = 8 * 1024 * 1024
BATCH = 100                                   # records a request (the archive's limit)
BATCH_BYTES = 8 * 1024 * 1024                 # and JSON a request (the archive takes 16 MiB)
PAGE = 500
MAX_PAGES = 40                                # feed pages in one run (20,000 records); the cursor moves on, the rest is next run
KIND_ORDER = {"game": 0, "document": 1, "mame": 1, "rom_list": 1, "rail_limit": 1, "fact": 1, "rom_image": 2}
USER_AGENT = "gatbox-sync/1 (+https://github.com/digitalunconciousness/Gatbox)"


class Unreachable(Exception):
    """The archive (or gatbox-web) didn't answer: nothing is changed, and the run says so."""


class Refused(Exception):
    """Answered, and said no in a way a person has to look at (a bad token, an expired Access service token)."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


# --- states -------------------------------------------------------------------------------------------------------------

def key_of(kind, rid):
    return f"{kind}|{rid}"


def split_key(key):
    kind, _, rid = key.partition("|")
    return kind, rid


def state_of(rec):
    """What a record is for the merge: its hash, "deleted", or None when there is no record."""
    if rec is None:
        return None
    return "deleted" if rec["deleted"] else rec["hash"]


def classify(b, d, a):
    """agreed | push | pull | conflict, from the base, deck and archive states (see the module docstring)."""
    if d == a:
        return "agreed"
    if d is None:
        return "pull"
    if a is None:
        return "agreed" if d == "deleted" else "push"
    if b is None:
        return "conflict"
    if d == b:
        return "pull"
    if a == b:
        return "push"
    return "conflict"


def uid_for(side, kind, rid, rev, state):
    """A write's uid, the same every time the same edit is sent, so a retry after a lost reply is a "duplicate"."""
    return hashlib.sha256(f"{side}|{kind}|{rid}|{rev}|{state}".encode()).hexdigest()[:32]


def order_of(rec):
    return (KIND_ORDER.get(rec["kind"], 1), rec["kind"], rec["id"])


def _snap(rec):
    """A record as a held write or a conflict keeps it: whole (a ROM list too, so a chosen version can still be sent)."""
    return None if rec is None else {k: rec[k] for k in ("kind", "id", "rev", "hash", "deleted", "at", "by", "data")}


def _base_data(rec):
    """What the base remembers of the version both sides held, for the 7" to show as BEFORE: all of it, but a ROM list
    (up to 2 MB a game) is kept as its hash only."""
    return None if rec is None or rec["kind"] == "rom_list" else rec["data"]


# --- the state files ----------------------------------------------------------------------------------------------------

class State:
    """state.json (cursors, the base, writes still to settle), conflicts.json (what the 7" shows), files.json (what's sent)."""

    def __init__(self, path):
        self.dir = path
        raw = self._load("state.json", {})
        self.deck_cursor = int(raw.get("deck_cursor", 0))
        self.archive_cursor = int(raw.get("archive_cursor", 0))
        self.deck_head = int(raw.get("deck_head", 0))                  # each library's newest cursor when this last finished:
        self.archive_head = int(raw.get("archive_head", 0))            # a library whose head is lower than that was replaced
        self.base = raw.get("base", {})
        self.held = raw.get("held", {})
        self.files = self._load("files.json", {})

    def _load(self, name, default):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _save(self, name, obj):
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, sort_keys=True)
        os.replace(tmp, path)

    def save(self):
        self._save("state.json", {"deck_cursor": self.deck_cursor, "archive_cursor": self.archive_cursor, "deck_head": self.deck_head,
                                  "archive_head": self.archive_head, "base": self.base, "held": self.held})
        self._save("conflicts.json", {"conflicts": [dict(v, key=k) for k, v in sorted(self.held.items()) if v["why"] == "conflict"]})
        self._save("files.json", self.files)

    def conflicts(self):
        return {k: v for k, v in self.held.items() if v["why"] == "conflict"}


# --- the two sides ------------------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect. urllib would send the Authorization header and the Access service token on to wherever it
    points, and Cloudflare Access answers a request it doesn't accept with a redirect to its login page, which comes back as
    a 200 of HTML that no caller can read."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class Http:
    def __init__(self, base, headers=None, timeout=60.0):
        self.base, self.headers, self.timeout = base.rstrip("/"), dict(headers or {}), timeout

    def call(self, method, path, body=None, raw=None, ctype=None, extra=None):
        """(status, parsed JSON or bytes, response headers). An HTTP error answer is returned, not raised; a network
        failure is Unreachable."""
        headers = {"User-Agent": USER_AGENT, **self.headers, **(extra or {})}
        data = raw
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with OPENER.open(req, timeout=self.timeout) as r:
                status, out, got = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, out, got = e.code, e.read(), e.headers
        except (urllib.error.URLError, OSError) as e:
            raise Unreachable(f"{self.base}: {e}") from None
        if (got.get("Content-Type") or "").startswith("application/json"):
            try:
                return status, json.loads(out), got
            except ValueError:
                pass
        return status, out, got

    def ok(self, method, path, binary=False, **kw):
        """(parsed JSON, headers), or the bytes when `binary`. Anything else is an error that says what came back."""
        status, out, got = self.call(method, path, **kw)
        if status in (401, 403):
            raise Refused(f"{self.base} refused the request (HTTP {status}): " + (out.get("error", "") if isinstance(out, dict) else ""), status)
        if 300 <= status < 400:
            where = urllib.parse.urlsplit(got.get("Location") or "").netloc or "somewhere else"
            raise Refused(f"{self.base} sent us to {where} instead of answering (HTTP {status}); the tokens were not sent on. Behind "
                          "Cloudflare Access this means it did not accept the service token: check it in the archive.conf and that "
                          "the application's policy for it is Service Auth", status)
        if status >= 400:
            msg = out.get("error") if isinstance(out, dict) else (out[:120].decode("utf-8", "replace") if out else "")
            raise Unreachable(f"{self.base}{path.split('?')[0]}: HTTP {status} {msg}")
        if not binary and out and isinstance(out, (bytes, bytearray)):
            raise Unreachable(f"{self.base}{path.split('?')[0]}: answered something that isn't JSON ({got.get('Content-Type') or 'no type'}): not the archive?")
        return out, got


class Archive(Http):
    """The archive, over HTTPS, as the deck's device: a Bearer token, and Cloudflare Access's service token when the archive
    is behind Access (CF-Access-Client-Id / CF-Access-Client-Secret)."""

    def __init__(self, url, token, access_id="", access_secret="", timeout=60.0):
        headers = {"Authorization": f"Bearer {token}"}
        if access_id and access_secret:
            headers["CF-Access-Client-Id"], headers["CF-Access-Client-Secret"] = access_id, access_secret
        super().__init__(url, headers, timeout)

    def changes(self, since, limit=PAGE):
        return self.ok("GET", f"/api/v1/library/changes?since={since}&limit={limit}")[0]

    def push(self, writes):
        return self.ok("PUT", "/api/v1/library/records", body={"records": writes})[0]["results"]

    def content_digest(self):
        return self.ok("GET", "/api/v1/library/digest?content=1")[0]

    def file_begin(self, sha, size):
        return self.ok("POST", "/api/v1/files", body={"sha256": sha, "size": size})[0]

    def file_part(self, sha, n, data):
        return self.ok("PUT", f"/api/v1/files/{sha}/parts/{n}", raw=data, ctype="application/octet-stream")[0]

    def file_finish(self, sha):
        return self.ok("POST", f"/api/v1/files/{sha}/finish")[0]

    def file_range(self, sha, start, end):
        """(bytes, total size), or None when the archive hasn't the file."""
        status, out, got = self.call("GET", f"/api/v1/files/{sha}", extra={"Range": f"bytes={start}-{end}"})
        if status == 404:
            return None
        if status in (401, 403):
            raise Refused(f"the archive refused the request (HTTP {status})", status)
        if status not in (200, 206):
            raise Unreachable(f"the archive answered HTTP {status} for a file")
        total = int(got.get("Content-Range", f"bytes 0-0/{len(out)}").rpartition("/")[2])
        return out, total


class Deck(Http):
    """gatbox-web on loopback: the deck's library and manuals, through /api/library/sync/*."""

    def changes(self, since, limit=PAGE):
        return self.ok("GET", f"/api/library/sync/changes?since={since}&limit={limit}")[0]

    def apply(self, writes):
        return self.ok("PUT", "/api/library/sync/records", body={"records": writes})[0]["results"]

    def content_digest(self):
        return self.ok("GET", "/api/library/sync/digest")[0]

    def files(self):
        return self.ok("GET", "/api/library/sync/files")[0]

    def file_range(self, sha, start, end):
        return self.ok("GET", f"/api/library/sync/files/{sha}", binary=True, extra={"Range": f"bytes={start}-{end}"})[0]

    def stage_begin(self, sha, size):
        return self.ok("POST", "/api/library/sync/files", body={"sha256": sha, "size": size})[0]

    def stage_part(self, sha, n, data):
        return self.ok("PUT", f"/api/library/sync/files/{sha}/parts/{n}", raw=data, ctype="application/octet-stream")[0]

    def stage_finish(self, sha):
        return self.ok("POST", f"/api/library/sync/files/{sha}/finish")[0]

    def resolutions(self):
        return self.ok("GET", "/api/library/sync/resolutions")[0].get("resolutions", [])

    def resolutions_done(self, keys):
        return self.ok("POST", "/api/library/sync/resolutions/done", body={"keys": keys})[0]


# --- one pass of the merge ----------------------------------------------------------------------------------------------

def read_feed(side, since, saved_head, log):
    """({key: record}, the cursor to keep, complete?, rebuilt?) for everything changed after `since`, a page at a time. A library
    whose newest cursor (`head`) is lower than it was when this last finished was rebuilt, or restored from an older copy
    (a head never goes down otherwise): that is reported, and nothing is read."""
    out, cursor = {}, since
    for _ in range(MAX_PAGES):
        page = side.changes(cursor, PAGE)
        if page.get("head", saved_head) < saved_head:
            return {}, cursor, False, True
        for r in page["records"]:
            out[key_of(r["kind"], r["id"])] = r
        cursor = page["cursor"]
        if not page["more"]:
            return out, cursor, True, False
    log(f"  feed: {len(out)} records so far; the rest next run")
    return out, cursor, False, False


def view(rec, held, side, base, rev_key):
    """(state, rev, record) of one side for a key: the feed's record, else the snapshot kept when the write was held (which
    may be "nothing there"), else what the base says (nothing changed on that side since)."""
    if rec is not None:
        return state_of(rec), rec["rev"], rec
    if side in held:
        snap = held[side]
        return (state_of(snap), snap["rev"], snap) if snap is not None else (None, 0, None)
    if base is not None:
        return base["s"], base[rev_key], None
    return None, 0, None


def resolve(state, plan, key, keep, seen_deck, seen_archive, log=None):
    """A person's choice for a conflict, as the 7" records it ({key, keep, deck, archive}: the two states she was shown). It
    applies only if both sides still hold exactly those (judged on this run's own plan, so a record she EDITed on the deck
    is judged as edited); otherwise it is not applied and she will see the conflict as it is now.
    keep="deck": the base is rewritten as the archive's version, so the deck's is now the only change and is pushed.
    keep="archive": the base is rewritten as the deck's version, so the archive's is the only change and is pulled.
    -> True when it applied."""
    item = next((i for i in plan["conflict"] if i["key"] == key), None)
    if item is None or keep not in ("deck", "archive") or item["ds"] != seen_deck or item["as"] != seen_archive:
        return False
    other, state_str = (item["archive"], item["as"]) if keep == "deck" else (item["deck"], item["ds"])
    item["base"] = state.base[key] = {"s": state_str, "dr": item["dr"], "ar": item["ar"], "data": _base_data(other)}
    verdict = classify(state_str, item["ds"], item["as"])
    plan["conflict"].remove(item)
    plan[verdict].append(item)
    side = "deck" if verdict == "push" else "archive"
    plan[verdict].sort(key=lambda i: order_of(i[side]))
    return True


def plan_pass(deck, archive, state, log):
    """Read both feeds and decide every record. Writes nothing. -> a plan dict."""
    dfeed, dcur, dfull, drebuilt = read_feed(deck, state.deck_cursor, state.deck_head, log)
    afeed, acur, afull, arebuilt = read_feed(archive, state.archive_cursor, state.archive_head, log)
    if drebuilt or arebuilt:
        # One library is not the one this state was made against (a rebuilt deck, an archive restored from an older backup).
        # Forget the agreement and compare both from the start: what is identical agrees, what differs is a conflict, and
        # what only one side holds goes to the other.
        log(f"  the {'deck' if drebuilt else 'archive'}'s library is not the one last synced: comparing both from the start")
        state.deck_cursor = state.archive_cursor = state.deck_head = state.archive_head = 0
        state.base, state.held = {}, {}
        dfeed, dcur, dfull, _ = read_feed(deck, 0, 0, log)
        afeed, acur, afull, _ = read_feed(archive, 0, 0, log)
    keys = set(dfeed) | set(afeed) | set(state.held)
    plan = {"push": [], "pull": [], "agreed": [], "conflict": [], "dcur": dcur, "acur": acur, "complete": dfull and afull}
    for key in sorted(keys):
        held = state.held.get(key, {})
        base = state.base.get(key)
        ds, dr, drec = view(dfeed.get(key), held, "deck", base, "dr")
        as_, ar, arec = view(afeed.get(key), held, "archive", base, "ar")
        verdict = classify(base["s"] if base else None, ds, as_)
        item = {"key": key, "deck": drec, "archive": arec, "ds": ds, "as": as_, "dr": dr, "ar": ar, "base": base}
        plan[verdict].append(item)
    for bucket in ("push", "pull"):
        plan[bucket].sort(key=lambda i: order_of(i["deck"] if bucket == "push" else i["archive"]))
    return plan


def describe(plan):
    """One line per kind of change, for the log and the dry run."""
    def count(items, side):
        c = {}
        for i in items:
            rec = i[side]
            what = rec["kind"] + (" (removal)" if rec["deleted"] else "")
            c[what] = c.get(what, 0) + 1
        return ", ".join(f"{n} {k}" for k, n in sorted(c.items())) or "nothing"
    return {"push": count(plan["push"], "deck"), "pull": count(plan["pull"], "archive"),
            "agreed": len(plan["agreed"]), "conflicts": len(plan["conflict"])}


def write_for(side, rec, rev):
    """The compare-and-set write that makes `side` hold `rec`, replacing the revision `rev`."""
    w = {"kind": rec["kind"], "id": rec["id"], "if_rev": rev, "uid": uid_for(side, rec["kind"], rec["id"], rec["rev"], state_of(rec)),
         "by": rec["by"], "at": rec["at"]}
    if rec["deleted"]:
        w["deleted"] = True
    else:
        w["data"] = rec["data"]
    return w


def apply_plan(deck, archive, state, plan, log, now=time.time):
    """Do the plan: push, pull, and note agreed and conflicting records. -> counts. State is updated in memory; the caller saves."""
    done = {"pushed": 0, "pulled": 0, "agreed": 0, "refused": [], "moved": 0}
    for item in plan["agreed"]:
        _settle(state, item["key"], item["ds"], item["dr"], item["ar"], item["deck"] or item["archive"])
        done["agreed"] += 1
    for item in plan["conflict"]:
        old = state.held.get(item["key"], {})
        state.held[item["key"]] = {"why": "conflict", "since": old["since"] if old.get("why") == "conflict" else now(),
                                   "deck": _snap(item["deck"]), "archive": _snap(item["archive"]),
                                   "base": None if item["base"] is None else {"s": item["base"]["s"], "data": item["base"].get("data")}}
        if old.get("why") != "conflict":
            log(f"  CONFLICT {item['key']}: the deck and the archive both changed it; neither was touched")
    for batch in _batches(plan["push"], "deck", "archive", "ar", "as"):
        writes = [w for _, w in batch]
        for (item, _), res in zip(batch, archive.push(writes), strict=True):
            _result(state, item, res, "archive", done, log)
    for batch in _batches(plan["pull"], "archive", "deck", "dr", "ds"):
        writes = [w for _, w in batch]
        for (item, _), res in zip(batch, deck.apply(writes), strict=True):
            _result(state, item, res, "deck", done, log)
    return done


def _batches(items, src, dst, rev_key, state_key):
    """[(item, write)] in batches of at most BATCH writes and BATCH_BYTES of JSON (ROM lists are up to 2 MB each); one write
    bigger than that goes alone."""
    batch, size = [], 0
    for i in items:
        w = write_for("deck" if src == "deck" else "archive", i[src], i[rev_key] if i[state_key] is not None else 0)
        n = len(json.dumps(w))
        if batch and (len(batch) >= BATCH or size + n > BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append((i, w))
        size += n
    if batch:
        yield batch


def _settle(state, key, st, dr, ar, rec):
    """Both sides hold the same thing now: it becomes the base, and nothing is held for it. `rec` is a record carrying its
    data (either side's), or None when neither side's record was in this run's feeds: then the base keeps the data it had."""
    old = state.base.get(key) or {}
    state.base[key] = {"s": st, "dr": dr, "ar": ar, "data": _base_data(rec) if rec is not None else old.get("data")}
    state.held.pop(key, None)


def _result(state, item, res, target, done, log):
    status = res["status"]
    key = item["key"]
    if status in ("created", "updated", "duplicate"):
        rec = item["deck"] if target == "archive" else item["archive"]
        got = res.get("rev") or (res.get("current") or {}).get("rev") or 0
        _settle(state, key, state_of(rec), rec["rev"] if target == "archive" else got, got if target == "archive" else rec["rev"], rec)
        done["pushed" if target == "archive" else "pulled"] += 1
    elif status == "conflict":
        # The side moved since this run read it: hold what it says now, and judge again next run. Nothing was written.
        cur = res.get("current")
        held = state.held.setdefault(key, {"why": "moved", "since": time.time()})
        held["why"] = "moved"
        held["deck" if target == "deck" else "archive"] = _snap(cur) if cur else None
        held.setdefault("deck", _snap(item["deck"]))
        held.setdefault("archive", _snap(item["archive"]))
        done["moved"] += 1
        log(f"  {key}: it moved on the {target} while this run was working; judged again next run")
    else:
        reason = res.get("reason") or status
        held = state.held.setdefault(key, {"why": "refused", "since": time.time()})
        held.update(why="refused", reason=str(reason)[:200], deck=_snap(item["deck"]), archive=_snap(item["archive"]))
        done["refused"].append({"key": key, "target": target, "reason": str(reason)[:200]})
        log(f"  {key}: the {target} refused it ({reason}); it stays pending and is judged again, never forced")


# --- files ---------------------------------------------------------------------------------------------------------------

def send_files(deck, archive, state, deadline, log, limit=20):
    """Every manual the deck holds goes up, resumable, once. -> {sent, bytes, failed}."""
    out = {"sent": 0, "bytes": 0, "failed": []}
    have = deck.files().get("have", {})
    up = state.files.setdefault("up", {})
    todo = [(sha, size) for sha, size in sorted(have.items()) if sha not in up][:limit]
    for sha, size in todo:
        if time.monotonic() > deadline:
            log("  files: out of time, the rest next run")
            break
        try:
            begun = archive.file_begin(sha, size)
            if begun.get("state") != "stored":
                parts = -(-size // PART_SIZE)
                have_parts = set(begun.get("have", []))
                for n in range(parts):
                    if n in have_parts:
                        continue
                    if time.monotonic() > deadline:
                        raise Unreachable("out of time part way: resumes next run")
                    start = n * PART_SIZE
                    chunk = deck.file_range(sha, start, min(start + PART_SIZE, size) - 1)
                    archive.file_part(sha, n, chunk)
                archive.file_finish(sha)
                out["bytes"] += size
            up[sha] = time.time()
            out["sent"] += 1
        except Unreachable as e:
            out["failed"].append({"sha256": sha, "why": str(e)[:160]})
            log(f"  file {sha[:12]}: {e}")
            break
    return out


def fetch_files(deck, archive, state, deadline, log, limit=20):
    """Every manual the library lists that the deck has no file for comes down, resumable. The deck places it in its game's
    folder itself when the last part is in. -> {fetched, bytes, absent, failed}."""
    out = {"fetched": 0, "bytes": 0, "absent": 0, "failed": []}
    want = deck.files().get("want", [])
    for w in want[:limit]:
        sha = w["sha256"]
        if time.monotonic() > deadline:
            log("  files: out of time, the rest next run")
            break
        try:
            first = archive.file_range(sha, 0, PART_SIZE - 1)
            if first is None:
                out["absent"] += 1                         # listed, but the archive doesn't hold the file (yet)
                continue
            data, size = first
            begun = deck.stage_begin(sha, size)
            if begun.get("state") != "stored":
                have = set(begun.get("have", []))
                for n in range(-(-size // PART_SIZE)):
                    if n in have:
                        continue
                    if time.monotonic() > deadline:
                        raise Unreachable("out of time part way: resumes next run")
                    if n == 0:
                        part = data
                    else:
                        got = archive.file_range(sha, n * PART_SIZE, min((n + 1) * PART_SIZE, size) - 1)
                        if got is None:
                            raise Unreachable("the archive lost the file part way")
                        part = got[0]
                    deck.stage_part(sha, n, part)
                deck.stage_finish(sha)
                out["bytes"] += size
            out["fetched"] += 1
        except Unreachable as e:
            out["failed"].append({"sha256": sha, "why": str(e)[:160]})
            log(f"  file {sha[:12]}: {e}")
            break
    return out


# --- the run -------------------------------------------------------------------------------------------------------------

DRYRUN_SAMPLE = 5          # in a dry run, how many records of each kind are named before "and N more"


def log_plan(plan, log, sample=None):
    """One line per record the pass will send, fetch or leave in conflict: `send  game|widget-wars`. With SAMPLE, only the
    first SAMPLE of each kind and direction are named and the rest counted, so a first sync of a whole library is a screen
    and not a hundred lines. The real run names every one: that is its record of what moved."""
    for bucket, side, word in (("push", "deck", "send"), ("pull", "archive", "fetch"), ("conflict", None, "conflict")):
        shown = {}
        for item in plan[bucket]:
            rec = item[side] if side else None
            kind = rec["kind"] if rec else item["key"].split("|", 1)[0]
            shown[kind] = shown.get(kind, 0) + 1
            if sample is None or shown[kind] <= sample:
                log(f"  {word}  {item['key']}" + (" (removal)" if rec and rec["deleted"] else ""))
        if sample is not None:
            for kind, n in sorted(shown.items()):
                if n > sample:
                    log(f"  {word}  ... and {n - sample} more {kind} ({n} in all; GATBOX_SYNC_DRYRUN=full names every one)")


def run(deck, archive, state_dir, dryrun=False, deadline=None, log=print, now=time.time, sample=DRYRUN_SAMPLE):
    """One sync pass. -> the library block for status.json. Nothing is written anywhere when `dryrun`. A dry run says
    the totals first, then names SAMPLE records of each kind (None: every one); a real run names every record it moves."""
    deadline = deadline if deadline is not None else time.monotonic() + 200
    state = State(state_dir)
    status = {"at": now(), "ok": True, "dryrun": bool(dryrun)}
    try:
        plan = plan_pass(deck, archive, state, log)
        used, applied = _resolutions(deck, state, plan, log, dryrun)
        status["resolved"] = applied
        status["plan"] = describe(plan)
        if dryrun:
            # The totals come first: they are what the person reading it wants, and they used to be the last line of a
            # hundred.
            log("  dry run: " + "; ".join(f"{k} {v}" for k, v in status["plan"].items()) + ". Nothing was changed.")
        log_plan(plan, log, sample if dryrun else None)
        if dryrun:
            log("  looking at the files to send and fetch (this can take a minute)")
            listed = deck.files()
            status["files"] = {"to_send": len([s for s in listed.get("have", {}) if s not in state.files.get("up", {})]),
                               "to_fetch": len(listed.get("want", []))}
            log(f"  files: {status['files']['to_send']} to send, {status['files']['to_fetch']} to fetch")
            return status
        done = apply_plan(deck, archive, state, plan, log, now)
        if used:
            deck.resolutions_done(used)
        # Forward past everything this pass judged. What it could not settle (a conflict, a refusal, a record that moved)
        # is in `held` and judged again from there, so nothing is lost by the cursor having moved on.
        state.deck_cursor, state.archive_cursor = plan["dcur"], plan["acur"]
        state.deck_head, state.archive_head = deck.changes(2 ** 62, 1)["head"], archive.changes(2 ** 62, 1)["head"]   # after our own writes
        status.update(done, more=not plan["complete"])
        status["files"] = {"up": send_files(deck, archive, state, deadline, log), "down": fetch_files(deck, archive, state, deadline, log)}
        state.save()
        status["identical"] = _identical(deck, archive, state)
    except Refused as e:
        status.update(ok=False, error=str(e), refused_by_archive=True)
        log(f"  library: {e}")
    except Unreachable as e:
        status.update(ok=False, error=str(e)[:200])
        log(f"  library: {e}")
    if not dryrun:
        state.save()
    status["pending"] = len(state.held)
    status["conflicts"] = len(state.conflicts())
    return status


def _identical(deck, archive, state):
    """True when both libraries hold the same content, False when not, None when it couldn't be asked."""
    if state.held:
        return False
    try:
        return deck.content_digest() == archive.content_digest()
    except (Unreachable, Refused):
        return None


def _resolutions(deck, state, plan, log, dryrun):
    """Her choices from the 7" (read through gatbox-web, which can't write this folder), applied to the plan if the conflict is as
    she saw it. -> (the keys to clear from the 7"'s file, how many applied). A dry run says what it would do and changes nothing."""
    try:
        rows = deck.resolutions()
    except Unreachable:
        return [], 0
    used, applied = [], 0
    for r in rows:
        if dryrun:
            item = next((i for i in plan["conflict"] if i["key"] == r["key"]), None)
            ok = bool(item and item["ds"] == r.get("deck") and item["as"] == r.get("archive"))
        else:
            ok = resolve(state, plan, r["key"], r["keep"], r.get("deck"), r.get("archive"))
            used.append(r["key"])
            applied += ok
        log(f"  resolution {r['key']}: keep {r['keep']}" + ("" if ok else " (not applied: it changed since she looked)")
            + (" (dry run: not applied)" if dryrun and ok else ""))
    return used, applied
