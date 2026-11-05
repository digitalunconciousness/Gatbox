#!/usr/bin/env python3
"""Phase D3: the merge (backend/gatboxlib/libsync.py) over two real libraries, with no network between them. Every case the
spec lists: first contact, one side changed, both changed, an edit against a delete, a lost reply, an unreachable archive, a
record that moves while a run is working, a refused write that is judged again and never forced, a conflict kept and then
resolved, state thrown away, a rebuilt deck, an archive restored from an older backup, and a dry run that writes nothing.
Every name and value is invented.
"""
import json
import os
import shutil
import sys
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))

from gatboxarchive import api  # noqa: E402
from gatboxlib import libsync  # noqa: E402
from gatboxlib import library as L  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


class Side:
    """A library behind the interface libsync wants, writing the way the archive does (api.apply_writes, as a device)."""

    def __init__(self, path):
        self.lib = L.Library(path)
        self.writes, self.down, self.on_write, self.lose_reply, self.reject = 0, False, None, False, set()
        self.asked = []                                # resolutions the 7" recorded
        self.done = []

    def changes(self, since, limit=500):
        if self.down:
            raise libsync.Unreachable("down")
        return self.lib.changes(since, limit)

    def apply(self, writes):
        if self.down:
            raise libsync.Unreachable("down")
        self.writes += len(writes)
        if self.on_write:
            self.on_write(writes)
        refused = [(w["kind"], w["id"]) in self.reject for w in writes]
        taken = api.apply_writes(self.lib, [w for w, no in zip(writes, refused, strict=True) if not no], {"kind": "device", "name": "deck"}) \
            if not all(refused) else []
        got = iter(taken)
        out = [{"kind": w["kind"], "id": w["id"], "uid": w["uid"], "status": "rejected", "reason": "refused for this test"} if no else next(got)
               for w, no in zip(writes, refused, strict=True)]
        if self.lose_reply:
            raise libsync.Unreachable("the reply was lost")
        return out

    push = apply

    def content_digest(self):
        return self.lib.content_digest()

    def files(self):
        return {"have": {}, "want": []}

    def resolutions(self):
        return list(self.asked)

    def resolutions_done(self, keys):
        self.done += keys
        self.asked = [a for a in self.asked if a["key"] not in keys]


def game(side, key, title=None, **kw):
    r = side.lib.put("game", key, dict({"title": title or key.replace("-", " ").title(), "kind": "video"}, **kw), 0, by="test")
    assert r["result"] == "created", r
    return r["record"]


def edit(side, kind, rid, **fields):
    cur = side.lib.get(kind, rid)
    r = side.lib.put(kind, rid, dict(cur["data"], **fields), cur["rev"], by="test")
    assert r["result"] in ("updated", "duplicate"), r
    return r["record"]


def fact(side, key, what="Fuse F1", rid=None, **kw):
    rid = rid or uuid.uuid4().hex
    side.lib.put("fact", rid, dict({"model_key": key, "group": "fuse", "what": what, "value": "3 A"}, **kw), 0, by="test")
    return rid


def doc(side, key, did="manual", **kw):
    side.lib.put("document", f"{key}/{did}", dict({"model_key": key, "doc_id": did, "title": did.title(), "kind": "manual"}, **kw), 0, by="test")
    return f"{key}/{did}"


class Rig:
    def __init__(self, tmp, name):
        self.dir = os.path.join(tmp, name)
        self.deck, self.arc = Side(os.path.join(self.dir, "deck.sqlite")), Side(os.path.join(self.dir, "arc.sqlite"))
        self.state = os.path.join(self.dir, "state")
        self.log = []

    def sync(self, **kw):
        self.log = []
        return libsync.run(self.deck, self.arc, self.state, log=self.log.append, **kw)

    def same(self):
        return self.deck.lib.content_digest() == self.arc.lib.content_digest()

    def conflicts(self):
        with open(os.path.join(self.state, "conflicts.json"), encoding="utf-8") as f:
            return json.load(f)["conflicts"]


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-libsync-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"libsync: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    print("the rule, for every combination:")
    table = {  # (base, deck, archive) -> verdict; states are a hash, "deleted" or None
        (None, "x", "x"): "agreed", ("x", "x", "x"): "agreed", ("x", "deleted", "deleted"): "agreed",
        (None, "x", None): "push", (None, None, "x"): "pull", (None, "x", "y"): "conflict",
        ("x", "x", "y"): "pull", ("x", "y", "x"): "push", ("x", "y", "z"): "conflict", ("x", "y", "y"): "agreed",
        ("x", "deleted", "x"): "push", ("x", "x", "deleted"): "pull", ("x", "deleted", "y"): "conflict", ("x", "y", "deleted"): "conflict",
        ("x", None, "x"): "pull", ("x", None, "y"): "pull", ("x", "y", None): "push", ("x", "deleted", None): "agreed",
        (None, "deleted", None): "agreed", (None, None, "deleted"): "pull", (None, "deleted", "x"): "conflict",
    }
    bad = [(k, v, libsync.classify(*k)) for k, v in table.items() if libsync.classify(*k) != v]
    check(f"classify gives the documented verdict for {len(table)} cases", not bad, str(bad))

    print("first contact:")
    r = Rig(tmp, "first")
    game(r.deck, "widget-wars", year=1998)
    game(r.deck, "comet-run")
    fact(r.deck, "widget-wars")
    doc(r.deck, "comet-run")
    lim = "a" * 32
    r.deck.lib.put("rail_limit", lim, {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25",
                                       "confirmed": {"by": "the 7\"", "at": 1760000000}}, 0, by="deck")
    st = r.sync()
    check("a deck with records and an empty archive: everything goes up, games before what hangs off them, none refused",
          st["ok"] and st["pushed"] == 5 and not st["refused"] and r.arc.lib.get("game", "widget-wars") and r.arc.lib.get("rail_limit", lim))
    check("a confirmed limit arrives confirmed (the deck's CONFIRM is the deck's own write)", r.arc.lib.get("rail_limit", lim)["data"]["confirmed"]["by"] == "the 7\"")
    check("both libraries hold the same content, and the run says so", r.same() and st["identical"] is True)
    w = r.arc.writes
    st = r.sync()
    check("a second run sends and fetches nothing at all", r.arc.writes == w and st["pushed"] == 0 and st["pulled"] == 0 and r.deck.writes == 0)
    check("the merge is quiet when nothing changed", not [ln for ln in r.log if "send" in ln or "fetch" in ln])

    r = Rig(tmp, "reverse")
    game(r.arc, "widget-wars")
    fact(r.arc, "widget-wars", "Monitor")
    st = r.sync()
    check("an archive with records and an empty deck: they come down", st["pulled"] == 2 and r.deck.lib.get("game", "widget-wars") and r.same())

    r = Rig(tmp, "both-same")
    for s in (r.deck, r.arc):
        game(s, "widget-wars", year=1998)
    st = r.sync()
    check("the same record on both sides is agreed, with no write either way", r.deck.writes == 0 and r.arc.writes == 0 and st["agreed"] == 1 and not st["conflicts"])

    r = Rig(tmp, "first-differs")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    st = r.sync()
    check("the same record, different on each side, with no history: a conflict, and neither side is touched",
          st["conflicts"] == 1 and r.deck.writes == 0 and r.arc.writes == 0 and r.deck.lib.get("game", "widget-wars")["data"]["year"] == 1998
          and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 1999)
    c = r.conflicts()[0]
    check("it is kept with both versions, and the key the 7\" will name", c["key"] == "game|widget-wars" and c["deck"]["data"]["year"] == 1998
          and c["archive"]["data"]["year"] == 1999 and c["base"] is None and c["since"] > 0)

    print("one side changed:")
    r = Rig(tmp, "one-side")
    game(r.deck, "widget-wars")
    game(r.deck, "comet-run")
    r.sync()
    edit(r.deck, "game", "widget-wars", notes="deck wrote this")
    edit(r.arc, "game", "comet-run", notes="the partner wrote this")
    st = r.sync()
    check("a deck edit goes up and an archive edit comes down in the same run",
          st["pushed"] == 1 and st["pulled"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["notes"] == "deck wrote this"
          and r.deck.lib.get("game", "comet-run")["data"]["notes"] == "the partner wrote this" and r.same())
    check("each side's record keeps the author it was written with", r.arc.lib.get("game", "widget-wars")["by"] == "test")
    st = r.sync()
    check("and then it is quiet again", st["pushed"] == 0 and st["pulled"] == 0 and st["agreed"] >= 2 and not st["conflicts"])

    print("both changed:")
    edit(r.deck, "game", "widget-wars", notes="deck again")
    edit(r.arc, "game", "widget-wars", notes="archive again")
    before = (r.deck.lib.get("game", "widget-wars")["data"], r.arc.lib.get("game", "widget-wars")["data"])
    st = r.sync()
    check("the same record changed differently on each side is a conflict that writes nothing",
          st["conflicts"] == 1 and (r.deck.lib.get("game", "widget-wars")["data"], r.arc.lib.get("game", "widget-wars")["data"]) == before)
    c = r.conflicts()[0]
    check("the conflict keeps what they both started from", c["base"]["data"]["notes"] == "deck wrote this")
    writes = (r.arc.writes, r.deck.writes)
    st = r.sync()
    check("run again: still a conflict, still nothing written, and it keeps the time it was first seen",
          st["conflicts"] == 1 and r.conflicts()[0]["since"] == c["since"] and (r.arc.writes, r.deck.writes) == writes)
    edit(r.deck, "game", "widget-wars", notes="archive again")
    st = r.sync()
    check("when the two come to hold the same thing the conflict clears itself", st["conflicts"] == 0 and r.conflicts() == [])
    edit(r.deck, "game", "comet-run", year=2001)
    edit(r.arc, "game", "comet-run", year=2001)
    check("the same edit made on both sides at once is not a conflict", r.sync()["conflicts"] == 0 and r.same())

    print("deletes:")
    r = Rig(tmp, "deletes")
    game(r.deck, "widget-wars")
    f1, f2, f3 = fact(r.deck, "widget-wars", "One"), fact(r.deck, "widget-wars", "Two"), fact(r.deck, "widget-wars", "Three")
    r.sync()
    r.deck.lib.put("fact", f1, None, r.deck.lib.get("fact", f1)["rev"], deleted=True, by="test")
    st = r.sync()
    check("a removal on the deck reaches the archive as a removal", r.arc.lib.get("fact", f1) is None and r.arc.lib.get("fact", f1, deleted=True)["deleted"] and st["pushed"] == 1)
    r.arc.lib.put("fact", f2, None, r.arc.lib.get("fact", f2)["rev"], deleted=True, by="partner")
    st = r.sync()
    check("a removal on the archive reaches the deck", r.deck.lib.get("fact", f2) is None and st["pulled"] == 1 and r.same())
    edit(r.deck, "fact", f3, value="4 A")
    r.arc.lib.put("fact", f3, None, r.arc.lib.get("fact", f3)["rev"], deleted=True, by="partner")
    st = r.sync()
    check("an edit on one side against a removal on the other is a conflict, and the edit is not lost",
          st["conflicts"] == 1 and r.deck.lib.get("fact", f3)["data"]["value"] == "4 A" and r.arc.lib.get("fact", f3) is None)
    c = r.conflicts()[0]
    check("... with the removal shown as the archive's version", c["archive"]["deleted"] is True and c["deck"]["deleted"] is False)
    r.deck.lib.put("fact", f3, None, r.deck.lib.get("fact", f3)["rev"], deleted=True, by="test")
    check("both removed it: agreed", r.sync()["conflicts"] == 0)

    print("a lost reply, an unreachable archive, a record that moves:")
    r = Rig(tmp, "lost")
    game(r.deck, "widget-wars")
    r.arc.lose_reply = True
    st = r.sync()
    check("the write got through but the reply was lost: the run reports the archive unreachable and keeps its cursors back",
          st["ok"] is False and r.arc.lib.get("game", "widget-wars") is not None)
    r.arc.lose_reply = False
    st = r.sync()
    check("next run finds it already there and agrees: no second copy, nothing refused", st["ok"] and r.arc.lib.get("game", "widget-wars")["rev"] == 1
          and st["conflicts"] == 0 and r.same())

    r = Rig(tmp, "down")
    game(r.deck, "widget-wars")
    r.arc.down = True
    st = r.sync()
    check("an archive that doesn't answer: nothing changes, and the run says so", st["ok"] is False and "down" in st["error"] and r.arc.writes == 0)
    check("... and the state holds no cursor past anything unread", libsync.State(r.state).archive_cursor == 0 and libsync.State(r.state).deck_cursor == 0)
    r.arc.down = False
    check("it catches up the moment the archive is back", r.sync()["pushed"] == 1 and r.same())

    r = Rig(tmp, "moved")
    game(r.deck, "widget-wars")
    r.sync()
    edit(r.deck, "game", "widget-wars", notes="from the deck")
    r.arc.on_write = lambda w: (edit(r.arc, "game", "widget-wars", notes="the partner, a moment earlier"), setattr(r.arc, "on_write", None))
    st = r.sync()
    check("the archive record moved between this run reading it and writing: the write is refused, not forced, the partner's edit stays",
          st["moved"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["notes"] == "the partner, a moment earlier")
    st = r.sync()
    check("next run judges it from fresh state: both changed it, so it is a conflict, with nothing lost",
          st["conflicts"] == 1 and r.deck.lib.get("game", "widget-wars")["data"]["notes"] == "from the deck")

    print("a refused write is judged again and never forced:")
    r = Rig(tmp, "refused")
    game(r.deck, "widget-wars")
    game(r.deck, "comet-run")
    r.arc.reject = {("game", "comet-run")}
    st = r.sync()
    check("a record the archive refuses is reported, the others still go", len(st["refused"]) == 1 and st["pushed"] == 1
          and r.arc.lib.get("game", "widget-wars") and not r.arc.lib.get("game", "comet-run"))
    st = r.sync()
    check("it is tried again next run (and refused again), and still not forced", len(st["refused"]) == 1 and st["pending"] == 1)
    r.arc.reject = set()
    st = r.sync()
    check("once the archive takes it, it settles and nothing is pending", st["pushed"] == 1 and st["pending"] == 0 and r.same())

    print("resolving a conflict (what the 7\" will do):")
    r = Rig(tmp, "resolve")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    r.deck.asked = [{"key": "game|widget-wars", "keep": "deck", "deck": seen["deck"]["hash"], "archive": seen["archive"]["hash"]}]
    st = r.sync()
    check("KEEP THIS (the deck's): the deck's version goes to the archive on top of the archive's, once, and the conflict is gone",
          st["pushed"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 1998 and st["conflicts"] == 0 and r.same()
          and r.deck.done == ["game|widget-wars"])
    r = Rig(tmp, "resolve2")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    r.deck.asked = [{"key": "game|widget-wars", "keep": "archive", "deck": seen["deck"]["hash"], "archive": seen["archive"]["hash"]}]
    st = r.sync()
    check("the archive's: it comes down over the deck's", st["pulled"] == 1 and r.deck.lib.get("game", "widget-wars")["data"]["year"] == 1999 and r.same())
    r = Rig(tmp, "resolve3")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    edit(r.arc, "game", "widget-wars", year=2005)                     # the partner changes it again after she looked
    r.deck.asked = [{"key": "game|widget-wars", "keep": "deck", "deck": seen["deck"]["hash"], "archive": seen["archive"]["hash"]}]
    st = r.sync()
    check("a choice made against what has since changed is not applied: it stays a conflict, shown as it is now",
          st["conflicts"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 2005 and r.deck.lib.get("game", "widget-wars")["data"]["year"] == 1998
          and r.conflicts()[0]["archive"]["data"]["year"] == 2005)
    check("... and the stale choice is cleared so it can't linger", r.deck.done == ["game|widget-wars"])
    r = Rig(tmp, "resolve4")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    edit(r.deck, "game", "widget-wars", year=2020)                     # EDIT: she writes her own version on the deck, then keeps it
    seen2 = r.sync() and r.conflicts()[0]
    r.deck.asked = [{"key": "game|widget-wars", "keep": "deck", "deck": seen2["deck"]["hash"], "archive": seen2["archive"]["hash"]}]
    st = r.sync()
    check("EDIT then keep: her own version is what the archive ends up with", r.arc.lib.get("game", "widget-wars")["data"]["year"] == 2020 and r.same() and st["conflicts"] == 0)

    r = Rig(tmp, "resolve5")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    edit(r.arc, "game", "widget-wars", year=2005)                     # the partner changes it again, and a run notes it before her tap arrives
    r.sync()
    check("the conflict now shows the archive's newer version", r.conflicts()[0]["archive"]["data"]["year"] == 2005)
    r.deck.asked = [{"key": "game|widget-wars", "keep": "deck", "deck": seen["deck"]["hash"], "archive": seen["archive"]["hash"]}]
    st = r.sync()
    check("her choice, made about the version before that, is not applied to this one: the partner's 2005 is never overwritten by a choice she didn't make about it",
          st["conflicts"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 2005 and r.deck.done == ["game|widget-wars"])

    r = Rig(tmp, "resolve6")
    game(r.deck, "widget-wars", year=1998)
    game(r.arc, "widget-wars", year=1999)
    r.sync()
    seen = r.conflicts()[0]
    new_hash = edit(r.deck, "game", "widget-wars", year=2020)["hash"]        # EDIT on the 7": her own version, before any run notices
    r.deck.asked = [{"key": "game|widget-wars", "keep": "deck", "deck": new_hash, "archive": seen["archive"]["hash"]}]
    st = r.sync(dryrun=True)
    check("a dry run says what the choice would do and applies nothing (the choice stays for the real run)",
          st["resolved"] == 0 and any("dry run: not applied" in ln for ln in r.log) and r.deck.done == [] and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 1999)
    st = r.sync()
    check("EDIT then keep, in one run: the record she wrote is the one the archive ends up with (judged as edited, not as the conflict she saw)",
          st["resolved"] == 1 and r.arc.lib.get("game", "widget-wars")["data"]["year"] == 2020 and r.same() and st["conflicts"] == 0)

    print("state thrown away, a rebuilt deck, an archive restored from an older copy:")
    r = Rig(tmp, "lostall")
    game(r.deck, "widget-wars")
    game(r.deck, "comet-run")
    r.sync()
    shutil.rmtree(r.state)
    edit(r.arc, "game", "comet-run", year=1990)
    st = r.sync()
    check("with no state at all the next run compares both from the start: identical records agree and nothing is rewritten",
          st["ok"] and r.deck.writes == 0 and st["agreed"] >= 1 and r.arc.lib.get("game", "widget-wars")["rev"] == 1)
    check("... and the one that differs (no history to say who is newer) is a conflict, not a guess", st["conflicts"] == 1)

    r = Rig(tmp, "rebuilt")
    game(r.deck, "widget-wars")
    game(r.deck, "comet-run")
    r.sync()
    r.deck = Side(os.path.join(r.dir, "deck-new.sqlite"))                 # the deck is rebuilt: an empty library, the old state
    st = r.sync()
    check("a rebuilt deck is noticed (its feed is behind the saved cursor), and fills from the archive",
          st["ok"] and r.deck.lib.get("game", "widget-wars") and r.deck.lib.get("game", "comet-run") and st["pulled"] == 2 and r.same()
          and any("not the one last synced" in ln for ln in r.log))

    r = Rig(tmp, "restored")
    game(r.deck, "widget-wars")
    r.sync()
    game(r.deck, "comet-run")
    edit(r.deck, "game", "widget-wars", notes="newer")
    r.sync()
    r.arc = Side(os.path.join(r.dir, "arc-old.sqlite"))                    # restored from a backup taken before any of that
    game(r.arc, "widget-wars")
    st = r.sync()
    check("an archive restored from an older backup is noticed: what it lacks goes back up, and what differs (no history to say who is "
          "newer) is a conflict, not a guess; the deck is untouched",
          st["ok"] and r.arc.lib.get("game", "comet-run") and st["conflicts"] == 1 and r.deck.writes == 0
          and r.deck.lib.get("game", "widget-wars")["data"]["notes"] == "newer" and any("not the one last synced" in ln for ln in r.log))

    print("a dry run:")
    r = Rig(tmp, "dry")
    game(r.deck, "widget-wars")
    fact(r.deck, "widget-wars")
    game(r.arc, "comet-run")
    st = r.sync(dryrun=True)
    check("it says what it would do", st["dryrun"] and "1 game" in st["plan"]["push"] and "1 fact" in st["plan"]["push"] and "1 game" in st["plan"]["pull"] and any("send  game|widget-wars" in ln for ln in r.log))
    check("and changes nothing: no write to either library, no state folder at all",
          r.deck.writes == 0 and r.arc.writes == 0 and not os.path.exists(r.state) and not r.arc.lib.get("game", "widget-wars") and not r.deck.lib.get("game", "comet-run"))
    st = r.sync()
    check("the real run after it does what it said", st["pushed"] == 2 and st["pulled"] == 1 and r.same())

    print("a bigger library, in batches:")
    r = Rig(tmp, "big")
    for i in range(130):
        game(r.deck, f"game-{i:03d}")
    for i in range(40):
        fact(r.deck, "game-005", f"Fact {i}")
    st = r.sync()
    check("170 records go up in batches of 100 (games first), all accepted", st["pushed"] == 170 and not st["refused"] and r.same())
    return 0


if __name__ == "__main__":
    sys.exit(main())
