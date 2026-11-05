#!/usr/bin/env python3
"""Phase D1: the archive server. The real server (archive/gatboxarchive) on spare ports over a temp data directory,
called over HTTP. Every name, key and byte is invented.
"""
import base64
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))

from gatboxarchive import api  # noqa: E402
from gatboxarchive.server import Archive, Config, email_from, make_servers  # noqa: E402
from gatboxarchive.tokens import TokenError  # noqa: E402
from gatboxlib import library as L  # noqa: E402
from gatboxlib.filestore import PART_SIZE  # noqa: E402

passed = failed = 0
MIB = 1024 * 1024


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, token=None, headers=None, raw=None, ctype=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if ctype:
            h["Content-Type"] = ctype
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        out = r.read()
        conn.close()
        try:
            return r.status, json.loads(out), r
        except ValueError:
            return r.status, out, r


def access_jwt(email=None, **claims):
    """What Cloudflare Access puts in Cf-Access-Jwt-Assertion, minus a real signature (the archive reads the payload only)."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    body = dict({"aud": ["x"], "sub": "u1"}, **claims)
    if email is not None:
        body["email"] = email
    return f"{b64({'alg': 'RS256', 'kid': 'test'})}.{b64(body)}.c2ln"


def game(key, **kw):
    return {"kind": "game", "id": key, "uid": uuid.uuid4().hex, "if_rev": 0, "by": "the 7\"",
            "data": dict({"title": key.replace("-", " ").title(), "kind": "video"}, **kw)}


def child(kind, key, rid, if_rev=0, **data):
    return {"kind": kind, "id": rid, "uid": uuid.uuid4().hex, "if_rev": if_rev, "by": "the 7\"",
            "data": dict({"model_key": key}, **data)}


def limit(key, lo="4.75", hi="5.25", rid=None, if_rev=0, **kw):
    return child("rail_limit", key, rid or uuid.uuid4().hex.replace("-", ""), if_rev, rail="+5V", lo=lo, hi=hi, **kw)


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-archive-")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"archive: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    cfg = Config({"ARCHIVE_DATA": f"{tmp}/data", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0",
                  "ARCHIVE_MAX_FILE": str(20 * MIB), "ARCHIVE_PEOPLE": "partner@example.test,owner@example.test"})
    arc = Archive(cfg)
    tunnel, private = make_servers(arc)
    for s in (tunnel, private):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    T, P = Client(tunnel.server_address[1]), Client(private.server_address[1])
    dev = arc.tokens.add("deck", "device")
    rd = arc.tokens.add("cabcrawl", "reader")
    ACCESS = {"Cf-Access-Jwt-Assertion": access_jwt("Partner@Example.test")}

    print("health, and who is asking:")
    c, b, _ = T.call("GET", "/api/v1/health")
    check("health needs no token and says what it speaks", c == 200 and b["ok"] and b["app"] == "gatbox-archive" and "library" in b["capabilities"])
    for what, call in (("no credential at all", lambda: T.call("GET", "/api/v1/library/digest")),
                       ("a made-up token", lambda: T.call("GET", "/api/v1/library/digest", token="gbx_aaaaaaaaaaaa." + "x" * 43)),
                       ("junk where a token goes", lambda: T.call("GET", "/api/v1/library/digest", token="nope")),
                       ("an identity header from someone not on the list",
                        lambda: T.call("GET", "/api/v1/library/digest", headers={"Cf-Access-Jwt-Assertion": access_jwt("stranger@example.test")}))):
        c, b, _ = call()
        check(f"{what}: 401, and it says no more than that", c == 401 and b == {"error": "a valid token is required"})
    check("a device token reads", T.call("GET", "/api/v1/library/digest", token=dev)[0] == 200)
    wrong = dev[:-1] + ("A" if dev[-1] != "A" else "B")
    check("a real token id with the wrong secret is refused (even after the right one was remembered)",
          T.call("GET", "/api/v1/library/digest", token=wrong)[0] == 401)
    check("a reader token reads, on either listener", T.call("GET", "/api/v1/library/digest", token=rd)[0] == 200
          and P.call("GET", "/api/v1/library/digest", token=rd)[0] == 200)
    check("a person (the Access identity header, from the list) reads", T.call("GET", "/api/v1/library/digest", headers=ACCESS)[0] == 200)
    check("a service token's JWT (no email in it) is not a person", T.call("GET", "/api/v1/library/digest", headers={
        "Cf-Access-Jwt-Assertion": access_jwt(None, sub="", common_name="abc.access")})[0] == 401)
    check("a malformed JWT is no identity", all(T.call("GET", "/api/v1/library/digest", headers={"Cf-Access-Jwt-Assertion": v})[0] == 401
                                               for v in ("a.b.c", "x.!!!.z", "..", "eyJ.eyJ.sig", access_jwt(5), access_jwt("not an address"))))
    check("a bare address in the configured header is not read from the JWT header's place unless it is that header",
          T.call("GET", "/api/v1/library/digest", headers={"Cf-Access-Authenticated-User-Email": "partner@example.test"})[0] == 401)
    check("the private listener refuses a device token", P.call("GET", "/api/v1/library/digest", token=dev)[0] == 401)
    check("and never trusts the identity header (there is no Access in front of it)", P.call("GET", "/api/v1/library/digest", headers=ACCESS)[0] == 401)
    check("a bearer token wins over an identity header: a bad one is refused even with the header",
          T.call("GET", "/api/v1/library/digest", token="gbx_aaaaaaaaaaaa." + "x" * 43, headers=ACCESS)[0] == 401)
    check("a route that isn't one is 404, a wrong method 405",
          T.call("GET", "/nope", token=dev)[0] == 404 and T.call("POST", "/api/v1/library/digest", token=dev)[0] == 405)

    print("reading the email out of what Access sends:")
    check("a JWT gives its email claim, lower-cased", email_from(access_jwt("Partner@Example.test")) == "partner@example.test")
    check("a bare address is taken as it is, even one with two dots in it", email_from("first.last@mail.example.test") == "first.last@mail.example.test")
    check("no email claim, a number, spaces, no @, nothing or junk: no identity",
          all(email_from(v) == "" for v in (access_jwt(None), access_jwt(5), access_jwt("a b@example.test"), access_jwt("nobody"), "", None, "junk", "a.b.c")))

    print("writes are compare-and-set:")
    g = game("widget-wars", mfr="Midway", year=1998)
    c, b, _ = T.call("PUT", "/api/v1/library/records", {"records": [g]}, token=dev)
    r = b["results"][0]
    check("a game is created at revision 1, named by whoever sent it", c == 200 and r["status"] == "created" and r["rev"] == 1
          and arc.lib.get("game", "widget-wars")["by"] == "the 7\"")
    c, b, _ = T.call("PUT", "/api/v1/library/records", {"records": [g]}, token=dev)
    check("the same write again (a lost reply) is a duplicate, not a conflict", b["results"][0]["status"] == "duplicate" and b["results"][0]["rev"] == 1)
    upd = dict(game("widget-wars", mfr="Midway Games", year=1998), if_rev=1)
    b = T.call("PUT", "/api/v1/library/records", {"records": [upd]}, token=dev)[1]
    check("a replace naming the right revision is updated", b["results"][0]["status"] == "updated" and b["results"][0]["rev"] == 2)
    stale = dict(game("widget-wars", mfr="Other"), if_rev=1)
    b = T.call("PUT", "/api/v1/library/records", {"records": [stale]}, token=dev)[1]
    cur = b["results"][0].get("current") or {}
    check("a stale revision is a conflict that returns the current record, and writes nothing",
          b["results"][0]["status"] == "conflict" and cur.get("rev") == 2 and cur["data"]["mfr"] == "Midway Games"
          and arc.lib.get("game", "widget-wars")["rev"] == 2)
    b = T.call("PUT", "/api/v1/library/records", {"records": [child("fact", "no-such-game", uuid.uuid4().hex, group="fuse", what="F1")]}, token=dev)[1]
    check("a child of a game that isn't there is rejected, with the reason", b["results"][0]["status"] == "rejected" and "no game" in b["results"][0]["reason"])
    batch = [game("gadget-galaxy"), child("fact", "gadget-galaxy", uuid.uuid4().hex, group="fuse", what="F1", value="5A"),
             {"kind": "game", "id": "bad key!", "uid": "u1", "if_rev": 0, "data": {"title": "x", "kind": "video"}}, "not an object"]
    b = T.call("PUT", "/api/v1/library/records", {"records": batch}, token=dev)[1]["results"]
    check("one refused write leaves the rest: a game, its fact, a bad key, a non-object", [x["status"] for x in b] == ["created", "created", "rejected", "rejected"])
    check("a write without a uid is refused for a device (a retry must be harmless)",
          T.call("PUT", "/api/v1/library/records", {"records": [dict(game("no-uid"), uid=None)]}, token=dev)[1]["results"][0]["status"] == "rejected")
    for what, body, code in (("no records", {"records": []}, 400), ("not a list", {"records": "x"}, 400),
                             (f"over {api.MAX_RECORDS} records", {"records": [game(f"g-{i}") for i in range(api.MAX_RECORDS + 1)]}, 400),
                             ("not an object", ["x"], 400)):
        check(f"{what}: {code} and nothing written", T.call("PUT", "/api/v1/library/records", body, token=dev)[0] == code)
    check("a body that isn't JSON is 400; the wrong content type 415",
          T.call("PUT", "/api/v1/library/records", raw=b"{nope", ctype="application/json", token=dev)[0] == 400
          and T.call("PUT", "/api/v1/library/records", raw=b"{}", ctype="text/plain", token=dev)[0] == 415)
    check("a reader can't write", T.call("PUT", "/api/v1/library/records", {"records": [game("by-reader")]}, token=rd)[0] == 403
          and arc.lib.get("game", "by-reader") is None)
    check("a retired game's key still resolves for readers: replaced_by is stored",
          T.call("PUT", "/api/v1/library/records", {"records": [dict(game("widget-wars-old", replaced_by="widget-wars"))]}, token=dev)[1]["results"][0]["status"] == "created")

    print("a person on a phone:")
    lim = limit("widget-wars", doc_id=None, quote="+5 VDC")
    b = T.call("PUT", "/api/v1/library/records", {"records": [dict(lim, uid=None)]}, headers=ACCESS)[1]["results"][0]
    check("adds a limit, which arrives unconfirmed, stamped with his login and the server's time",
          b["status"] == "created" and arc.lib.get("rail_limit", lim["id"])["data"]["confirmed"] is None
          and arc.lib.get("rail_limit", lim["id"])["by"] == "partner@example.test")
    sneaky = dict(lim, uid=None, data=dict(lim["data"], confirmed={"by": "partner", "at": 1.0}))
    b = T.call("PUT", "/api/v1/library/records", {"records": [sneaky]}, headers=ACCESS)[1]["results"][0]
    check("can't confirm a limit: that is the 7\"", b["status"] == "rejected" and "7\"" in b["reason"])
    arc.lib.put("rail_limit", lim["id"], dict(lim["data"], confirmed={"by": "gatbox", "at": 2.0}), 1, by="gatbox")
    note = dict(lim, uid=None, if_rev=2, data=dict(lim["data"], quote="+5 VDC 4.75 to 5.25"))
    b = T.call("PUT", "/api/v1/library/records", {"records": [note]}, headers=ACCESS)[1]["results"][0]
    check("fixing a limit's quote leaves it confirmed (its numbers didn't change): he can't unconfirm one either",
          b["status"] == "updated" and arc.lib.get("rail_limit", lim["id"])["data"]["confirmed"] == {"by": "gatbox", "at": 2.0})
    nums = dict(lim, uid=None, if_rev=3, data=dict(lim["data"], lo="4.5"))
    T.call("PUT", "/api/v1/library/records", {"records": [nums]}, headers=ACCESS)
    check("changing its numbers clears confirmation, as the store always does on the 7\"",
          arc.lib.get("rail_limit", lim["id"])["data"]["confirmed"] is None)
    for kind, data in (("rom_image", {"size": 4, "crc32": "0" * 8, "sha1": "0" * 40, "verdict": "good"}),
                       ("rom_list", {"model_key": "widget-wars", "versions": []})):
        rid = "a" * 64 if kind == "rom_image" else "widget-wars"
        b = T.call("PUT", "/api/v1/library/records", {"records": [{"kind": kind, "id": rid, "if_rev": 0, "data": data}]}, headers=ACCESS)[1]["results"][0]
        check(f"a {kind} is the deck's, not his", b["status"] == "rejected" and "deck's" in b["reason"])
    b = T.call("PUT", "/api/v1/library/records", {"records": [dict(game("his-game"), uid=None, by="someone-else")]}, headers=ACCESS)[1]["results"][0]
    check("adds a game; what he writes as `by` doesn't count: Access names him", b["status"] == "created" and arc.lib.get("game", "his-game")["by"] == "partner@example.test")
    check("a device may write a rom_image and a rom_list",
          T.call("PUT", "/api/v1/library/records", {"records": [{"kind": "rom_image", "id": "a" * 64, "uid": "ri1", "if_rev": 0,
                 "data": {"size": 4, "crc32": "0" * 8, "sha1": "0" * 40, "verdict": "good", "model_key": "widget-wars"}}]}, token=dev)[1]["results"][0]["status"] == "created")

    print("the feed:")
    c, b, _ = T.call("GET", "/api/v1/library/changes", token=dev)
    keys = [(r["kind"], r["id"]) for r in b["records"]]
    check("everything, oldest first, with a cursor and `more` false", c == 200 and ("game", "widget-wars") in keys and b["more"] is False
          and b["cursor"] == max(1, b["cursor"]) and len(set(keys)) == len(keys))
    cur = b["cursor"]
    c, b2, _ = T.call("GET", f"/api/v1/library/changes?since={cur}", token=dev)
    check("from the cursor: nothing new, the cursor stays", b2["records"] == [] and b2["cursor"] == cur and b2["more"] is False)
    T.call("PUT", "/api/v1/library/records", {"records": [dict(game("widget-wars", mfr="Midway Games Inc"), if_rev=2)]}, token=dev)
    b3 = T.call("GET", f"/api/v1/library/changes?since={cur}", token=dev)[1]
    check("an edit shows once, at its latest revision", [(r["id"], r["rev"]) for r in b3["records"]] == [("widget-wars", 3)])
    pg = T.call("GET", "/api/v1/library/changes?limit=2", token=dev)[1]
    check("a page is limited, and says there is more", len(pg["records"]) == 2 and pg["more"] is True)
    got, since = [], 0
    while True:
        p = T.call("GET", f"/api/v1/library/changes?since={since}&limit=3", token=dev)[1]
        got += [(r["kind"], r["id"]) for r in p["records"]]
        since = p["cursor"]
        if not p["more"]:
            break
    check("paging by the cursor reaches every record exactly once", sorted(got) == sorted(keys))
    for bad in ("since=x", "since=-1", "limit=0", "limit=501", "models=BAD%20KEY"):
        check(f"{bad}: 400", T.call("GET", f"/api/v1/library/changes?{bad}", token=dev)[0] == 400)
    mine = T.call("GET", "/api/v1/library/changes?models=widget-wars", token=rd)[1]
    kinds = sorted({r["kind"] for r in mine["records"]})
    check("a reader's models= gives that game's records only: the game, its limits, its images",
          all(api.model_of(r) == "widget-wars" for r in mine["records"]) and "rail_limit" in kinds and "game" in kinds and "rom_image" in kinds
          and not any(r["id"] == "gadget-galaxy" for r in mine["records"]))
    two = T.call("GET", "/api/v1/library/changes?models=widget-wars,gadget-galaxy", token=rd)[1]
    check("several games at once", {api.model_of(r) for r in two["records"]} == {"widget-wars", "gadget-galaxy"})
    short = T.call("GET", "/api/v1/library/changes?models=gadget-galaxy&limit=1", token=rd)[1]
    check("a filtered page may be short, and the cursor still moves past what it skipped",
          short["more"] is True and short["cursor"] >= 1 and len(short["records"]) <= 1)
    walk, since = [], 0
    while True:
        p = T.call("GET", f"/api/v1/library/changes?since={since}&limit=2&models=gadget-galaxy", token=rd)[1]
        walk += [(r["kind"], r["id"]) for r in p["records"]]
        since = p["cursor"]
        if not p["more"]:
            break
    check("walking it to the end finds all of the game's records", ("game", "gadget-galaxy") in walk and len(walk) == 2)
    check("the feed carries no ROM bytes, only hashes", all(len(json.dumps(r)) < 20000 for r in mine["records"]))
    d = T.call("GET", "/api/v1/library/digest", token=dev)[1]
    check("the digest is the library's own", d == arc.lib.digest() and d["count"] == len(arc.lib.changes(0, 500)["records"]))

    print("files:")
    pdf = b"%PDF-1.4\n" + os.urandom(2000)
    rom = os.urandom(4096)
    sp, sr = hashlib.sha256(pdf).hexdigest(), hashlib.sha256(rom).hexdigest()

    def upload(client, data, token=None, headers=None):
        sha = hashlib.sha256(data).hexdigest()
        b = client.call("POST", "/api/v1/files", {"sha256": sha, "size": len(data)}, token=token, headers=headers)[1]
        if b.get("state") == "stored":
            return b
        for n in range(b["parts"]):
            client.call("PUT", f"/api/v1/files/{sha}/parts/{n}", raw=data[n * PART_SIZE:(n + 1) * PART_SIZE], ctype="application/octet-stream",
                        token=token, headers=headers)
        return client.call("POST", f"/api/v1/files/{sha}/finish", token=token, headers=headers)[1]

    check("a device uploads a PDF", upload(T, pdf, dev)["state"] == "stored")
    check("a person can upload too", upload(T, rom, headers=ACCESS)["state"] == "stored")
    check("a reader can't upload", T.call("POST", "/api/v1/files", {"sha256": sp, "size": 1}, token=rd)[0] == 403
          and T.call("PUT", f"/api/v1/files/{sp}/parts/0", raw=b"x", ctype="application/octet-stream", token=rd)[0] == 403)
    check("begin on a stored file says stored: nothing to send", T.call("POST", "/api/v1/files", {"sha256": sp, "size": len(pdf)}, token=dev)[1]["state"] == "stored")
    c, body, r = T.call("GET", f"/api/v1/files/{sp}", token=dev)
    check("a stored file reads back exactly, as a PDF", c == 200 and body == pdf and r.getheader("Content-Type") == "application/pdf"
          and r.getheader("Accept-Ranges") == "bytes")
    c, body, r = T.call("GET", f"/api/v1/files/{sr}", token=dev)
    check("a ROM image is an octet-stream", c == 200 and body == rom and r.getheader("Content-Type") == "application/octet-stream")
    c, body, r = T.call("GET", f"/api/v1/files/{sp}", token=dev, headers={"Range": "bytes=9-19"})
    check("a Range gives 206 and the bytes", c == 206 and body == pdf[9:20] and r.getheader("Content-Range") == f"bytes 9-{19}/{len(pdf)}")
    check("a suffix range and an open range", T.call("GET", f"/api/v1/files/{sp}", token=dev, headers={"Range": "bytes=-5"})[1] == pdf[-5:]
          and T.call("GET", f"/api/v1/files/{sp}", token=dev, headers={"Range": f"bytes={len(pdf) - 3}-"})[1] == pdf[-3:])
    check("a range past the end is 416", T.call("GET", f"/api/v1/files/{sp}", token=dev, headers={"Range": f"bytes={len(pdf)}-"})[0] == 416)
    check("an unknown file is 404, a bad hash 404", T.call("GET", f"/api/v1/files/{'e' * 64}", token=dev)[0] == 404
          and T.call("GET", "/api/v1/files/xyz", token=dev)[0] == 404)
    arc.lib.put("document", "widget-wars/manual", {"model_key": "widget-wars", "doc_id": "manual", "title": "Manual", "kind": "manual",
                                                  "sha256": sp, "pages": 1, "bytes": len(pdf)}, 0, by="gatbox")
    check("a reader takes a manual the library lists", T.call("GET", f"/api/v1/files/{sp}", token=rd)[1] == pdf
          and P.call("GET", f"/api/v1/files/{sp}", token=rd)[1] == pdf)
    c, b, _ = T.call("GET", f"/api/v1/files/{sr}", token=rd)
    check("but never a ROM image: told 404, as for nothing", c == 404 and b == {"error": "no such file"}
          and T.call("GET", f"/api/v1/files/{'e' * 64}", token=rd)[1] == b)
    big = os.urandom(PART_SIZE + 5000)
    sb = hashlib.sha256(big).hexdigest()
    b = T.call("POST", "/api/v1/files", {"sha256": sb, "size": len(big)}, token=dev)[1]
    check("a file in two parts: open, 8 MiB parts, nothing held", b["state"] == "open" and b["parts"] == 2 and b["have"] == [] and b["part_size"] == PART_SIZE)
    T.call("PUT", f"/api/v1/files/{sb}/parts/0", raw=big[:PART_SIZE], ctype="application/octet-stream", token=dev)
    b = T.call("POST", "/api/v1/files", {"sha256": sb, "size": len(big)}, token=dev)[1]
    check("cut off after part 0: asking again says which parts it holds", b["have"] == [0])
    c, b, _ = T.call("POST", f"/api/v1/files/{sb}/finish", token=dev)
    check("finish with a part missing is 409, naming it", c == 409 and "[1]" in b["error"])
    T.call("PUT", f"/api/v1/files/{sb}/parts/1", raw=big[PART_SIZE:], ctype="application/octet-stream", token=dev)
    check("and then it joins and verifies", T.call("POST", f"/api/v1/files/{sb}/finish", token=dev)[1] == {"state": "stored", "sha256": sb, "size": len(big)}
          and T.call("GET", f"/api/v1/files/{sb}", token=dev)[1] == big)
    wrong = hashlib.sha256(b"not these bytes").hexdigest()
    T.call("POST", "/api/v1/files", {"sha256": wrong, "size": len(rom)}, token=dev)
    T.call("PUT", f"/api/v1/files/{wrong}/parts/0", raw=rom, ctype="application/octet-stream", token=dev)
    c, b, _ = T.call("POST", f"/api/v1/files/{wrong}/finish", token=dev)
    check("a join that isn't the announced hash is 422 and dropped", c == 422 and not os.path.exists(arc.files.path(wrong))
          and not os.path.exists(os.path.join(cfg.data, "files", ".incoming", wrong)))
    check("a file over the limit is 413", T.call("POST", "/api/v1/files", {"sha256": "c" * 64, "size": 21 * MIB}, token=dev)[0] == 413)
    check("a part of a file never begun is 404; a wrong length 400",
          T.call("PUT", f"/api/v1/files/{'d' * 64}/parts/0", raw=b"x", ctype="application/octet-stream", token=dev)[0] == 404
          and T.call("PUT", f"/api/v1/files/{sb}/parts/0", raw=b"x", ctype="application/octet-stream", token=dev)[1]["state"] == "stored")
    sh2 = hashlib.sha256(b"y" * 100).hexdigest()
    T.call("POST", "/api/v1/files", {"sha256": sh2, "size": 100}, token=dev)
    check("part rules: wrong length 400, wrong type 415, out of range 400",
          T.call("PUT", f"/api/v1/files/{sh2}/parts/0", raw=b"x", ctype="application/octet-stream", token=dev)[0] == 400
          and T.call("PUT", f"/api/v1/files/{sh2}/parts/0", raw=b"y" * 100, ctype="application/json", token=dev)[0] == 415
          and T.call("PUT", f"/api/v1/files/{sh2}/parts/1", raw=b"y" * 100, ctype="application/octet-stream", token=dev)[0] == 400)
    conn = http.client.HTTPConnection("127.0.0.1", T.port, timeout=20)         # claims a huge body and sends none
    conn.putrequest("PUT", f"/api/v1/files/{sh2}/parts/0")
    for k, v in (("Authorization", f"Bearer {dev}"), ("Content-Type", "application/octet-stream"),
                 ("Content-Length", str(PART_SIZE + 1))):
        conn.putheader(k, v)
    conn.endheaders()
    r = conn.getresponse()
    check("an oversize body is 413 without being read, and the connection is closed", r.status == 413 and r.getheader("Connection") == "close")
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", T.port, timeout=20)         # a refused request with a body: no leftover parsed as the next
    conn.request("PUT", "/api/v1/library/records", body=b"{}", headers={"Authorization": f"Bearer {dev}", "Content-Type": "text/plain"})
    r = conn.getresponse()
    r.read()
    check("a refused request that had a body closes the connection too", r.status == 415 and r.getheader("Connection") == "close")
    conn.close()

    print("tokens:")
    tok = arc.tokens
    check("a token is gbx_<12 hex>.<43 chars> and only its hash is kept", dev.startswith("gbx_") and len(dev.split(".")[0]) == 4 + 12
          and len(dev.split(".")[1]) == 43 and dev.split(".")[1] not in open(os.path.join(cfg.data, "tokens.json")).read())
    check("the token file is mode 600", oct(os.stat(os.path.join(cfg.data, "tokens.json")).st_mode & 0o777) == "0o600")
    check("listing shows names and roles, never a secret", [(t["name"], t["role"]) for t in tok.list()] == [("deck", "device"), ("cabcrawl", "reader")]
          and "hash" not in json.dumps(tok.list()))
    for bad in (dict(name="deck", role="reader"), dict(name="x", role="admin"), dict(name="has space", role="reader"), dict(name="", role="reader")):
        try:
            tok.add(**bad)
            check(f"refuses {bad}", False)
        except TokenError:
            check(f"refuses {bad}", True)
    tok.revoke("cabcrawl")
    check("a revoked token stops working at once, on both listeners", T.call("GET", "/api/v1/library/digest", token=rd)[0] == 401
          and P.call("GET", "/api/v1/library/digest", token=rd)[0] == 401)
    check("and its name can't be reused", any(t["name"] == "cabcrawl" and t["revoked"] for t in tok.list()))
    try:
        tok.add("cabcrawl", "reader")
        check("a revoked name can't be taken again", False)
    except TokenError:
        check("a revoked name can't be taken again", True)

    print("the command line and the log:")
    env = dict(os.environ, ARCHIVE_DATA=f"{tmp}/cli", ARCHIVE_TUNNEL_BIND="127.0.0.1:0")
    cli = [sys.executable, os.path.join(REPO, "archive", "gatbox-archive")]
    r = subprocess.run(cli + ["token", "add", "cabcrawl", "--role", "reader"], env=env, capture_output=True, text=True)
    printed = [w for w in r.stdout.split() if w.startswith("gbx_")]
    check("token add prints the token once", r.returncode == 0 and len(printed) == 1 and "never shown again" in r.stdout)
    r = subprocess.run(cli + ["token", "list"], env=env, capture_output=True, text=True)
    check("token list never prints it", printed[0] not in r.stdout and "cabcrawl" in r.stdout)
    r = subprocess.run(cli + ["token", "add", "cabcrawl", "--role", "reader"], env=env, capture_output=True, text=True)
    check("a duplicate name exits 2", r.returncode == 2 and "already" in r.stderr)
    check("token revoke, then list says REVOKED", subprocess.run(cli + ["token", "revoke", "cabcrawl"], env=env, capture_output=True).returncode == 0
          and "REVOKED" in subprocess.run(cli + ["token", "list"], env=env, capture_output=True, text=True).stdout)
    r = subprocess.run(cli + ["status"], env=env, capture_output=True, text=True)
    check("status reads the library", r.returncode == 0 and "library" in r.stdout)
    srv_env = dict(env, ARCHIVE_TUNNEL_BIND="127.0.0.1:18741")
    p = subprocess.Popen(cli + ["serve"], env=srv_env, stderr=subprocess.PIPE, text=True)
    try:
        import time
        for _ in range(50):
            try:
                if Client(18741).call("GET", "/api/v1/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        secret = "gbx_" + "c" * 12 + "." + "s" * 43
        Client(18741).call("GET", "/api/v1/library/changes?models=widget-wars,secret-machine", token=secret)
        p.terminate()
        err = p.communicate(timeout=10)[1]
    finally:
        p.kill()
    check("the log has the method, the path and the status", "GET /api/v1/library/changes 401" in err)
    check("and never a query (a reader's models= is its machines), a token or a secret", "models" not in err and "secret-machine" not in err and "s" * 43 not in err)


if __name__ == "__main__":
    sys.exit(main())
