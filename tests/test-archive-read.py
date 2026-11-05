#!/usr/bin/env python3
"""The archive's end of the library's read-side contract (docs/contract/library-v1/read/): the real archive server, asked the way an
app asks (a reader token, `models=`, `Range`), must give exactly what `examples/` says. The examples are the normative wire
format; the apps' tests (cabcrawl, the Tech Tracker) serve the same files from a stub and read them with their own pull job, so
neither end can drift from the other. Every game, name and byte here is invented.

    python3 tests/test-archive-read.py            check
    python3 tests/test-archive-read.py --write    regenerate examples/ from the real server (then CHECKSUMS: docs/contract/checksums.sh --write DIR)
"""
import base64
import hashlib
import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "backend"), os.path.join(REPO, "archive")]
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402

READ = os.path.join(REPO, "docs", "contract", "library-v1", "read")
EX = os.path.join(READ, "examples")
WRITE = "--write" in sys.argv
passed = failed = 0
AT = 1760000000.0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def make_pdf():
    spec = importlib.util.spec_from_file_location("pages_test", os.path.join(REPO, "tests", "test-archive-pages.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_pdf(1, "contract")


def seed(arc, pdf):
    """The invented library the examples describe, written the way the deck writes it (fixed times, so the answers repeat)."""
    sha = arc.files.put_file(pdf)
    put = lambda kind, rid, data, **kw: arc.lib.put(kind, rid, data, 0, uid=f"example-{kind}-{rid}"[:64], by=kw.get("by", "the deck"), at=AT)  # noqa: E731
    put("game", "widget-wars", {"title": "Widget Wars", "mfr": "Acme", "year": 1998, "kind": "video", "notes": "A dedicated cabinet."})
    put("document", "widget-wars/service-manual", {"model_key": "widget-wars", "doc_id": "service-manual", "title": "Service manual", "kind": "manual",
                                                  "source_url": None, "source": "an invented archive", "sha256": sha, "pages": 1, "bytes": len(pdf)})
    put("mame", "widget-wars", {"model_key": "widget-wars", "set": "widgetw", "prefer": "widgetw", "status": "sure", "why": "same title and year"})
    put("rom_list", "widget-wars", {"model_key": "widget-wars", "mame": "0.276 (mame0276)", "set": "widgetw", "versions": [
        {"name": "widgetw", "parent": True, "description": "Widget Wars (rev 2)", "year": "1998", "mfr": "Acme",
         "roms": [{"name": "ww_u1.bin", "size": 65536, "crc": "deadbeef", "sha1": "1" * 40, "region": "maincpu", "status": None, "bios": None},
                  {"name": "ww_u2.bin", "size": 65536, "crc": "feedface", "sha1": "2" * 40, "region": "maincpu", "status": None, "bios": None}],
         "disks": []}]})
    put("rail_limit", "c0ffee00" * 4, {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25", "doc_id": "service-manual", "page": 1,
                                       "quote": "+5 VDC, 4.75 to 5.25 V", "confirmed": {"by": "the deck", "at": AT}})
    put("fact", "f1" * 16, {"model_key": "widget-wars", "group": "fuse", "what": "Fuse F1", "value": "3 A slow-blow", "doc_id": "service-manual",
                            "page": 1, "quote": "F1 3A SLO-BLO"})
    put("rom_image", "b" * 64, {"size": 65536, "crc32": "deadbeef", "sha1": "1" * 40, "label": "U1", "part": "27C512", "romident": None,
                                "model_key": "widget-wars", "chip": "U1", "verdict": "good", "revision": "rev 2", "note": "dumped twice, identical"})
    put("game", "pin-comet-run", {"title": "Comet Run", "mfr": "Orbital", "year": 1985, "kind": "pinball", "notes": ""})
    put("fact", "ab" * 16, {"model_key": "pin-comet-run", "group": "power", "what": "Line voltage", "value": "120 V", "doc_id": None, "page": None, "quote": ""})
    put("fact", "e1" * 16, {"model_key": "widget-wars", "group": "monitor", "what": "Chassis", "value": "25 inch", "doc_id": None, "page": None, "quote": ""})
    arc.lib.put("fact", "e1" * 16, None, 1, uid="example-fact-removed", by="the deck", at=AT, deleted=True)
    return sha


def get(port, path, token=None, rng=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if rng:
        headers["Range"] = rng
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    body = r.read()
    out = (r.status, dict(r.getheaders()), body)
    conn.close()
    return out


def pretty(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-archive-read-")
    sys.stderr = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    try:
        run(tmp)
    finally:
        sys.stderr = sys.__stderr__
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"archive read contract: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    pdf = make_pdf()
    cfg = Config({"ARCHIVE_DATA": f"{tmp}/data", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0"})
    arc = Archive(cfg)
    tunnel, private = make_servers(arc)
    for s in (tunnel, private):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    port = private.server_address[1]                       # the apps' own path: the private listener, a reader token
    token = arc.tokens.add("an-app", "reader")
    sha = seed(arc, pdf)
    asks = {
        "changes-response.json": "/api/v1/library/changes?since=0&limit=4&models=widget-wars",
        "changes-response-filtered.json": "/api/v1/library/changes?since=8&limit=1&models=widget-wars",
        "changes-response-last.json": "/api/v1/library/changes?since=8&limit=4&models=widget-wars",
        "changes-response-empty.json": "/api/v1/library/changes?since=11&limit=4&models=widget-wars",
        "changes-response-all.json": "/api/v1/library/changes?since=0&limit=500&models=widget-wars,pin-comet-run",
    }
    if WRITE:
        os.makedirs(EX, exist_ok=True)
        with open(os.path.join(EX, "manual.pdf.b64"), "wb") as f:       # base64: the repositories refuse a .pdf file by rule
            f.write(base64.encodebytes(pdf))
        for name, path in asks.items():
            code, _, body = get(port, path, token)
            assert code == 200, (name, code, body)
            with open(os.path.join(EX, name), "w", encoding="utf-8") as f:
                f.write(pretty(json.loads(body)))
        for name, (path, tok) in {"error-unauthorized.json": ("/api/v1/library/digest", None), "error-not-found.json": (f"/api/v1/files/{'0' * 64}", token)}.items():
            code, _, body = get(port, path, tok)
            with open(os.path.join(EX, name), "w", encoding="utf-8") as f:
                f.write(pretty(json.loads(body)))
        code, h, body = get(port, f"/api/v1/files/{sha}", token, "bytes=0-127")
        with open(os.path.join(EX, "files-range-response.txt"), "w", encoding="utf-8") as f:
            f.write(f"GET /api/v1/files/{sha} HTTP/1.1\nRange: bytes=0-127\n\nHTTP/1.1 {code} Partial Content\nContent-Type: {h['Content-Type']}\n"
                    f"Content-Length: {h['Content-Length']}\nAccept-Ranges: {h['Accept-Ranges']}\nETag: {h['ETag']}\nContent-Range: {h['Content-Range']}\n")
        print("examples written; now: bash docs/contract/checksums.sh --write docs/contract/library-v1/read")

    print("the change feed, as an app asks (a reader token, the private listener):")
    for name, path in asks.items():
        code, h, body = get(port, path, token)
        want = json.load(open(os.path.join(EX, name), encoding="utf-8"))
        check(f"{name}: the answer is the example", code == 200 and json.loads(body) == want, f"{code} {body[:200]!r}")
    first = json.load(open(os.path.join(EX, "changes-response.json"), encoding="utf-8"))
    last = json.load(open(os.path.join(EX, "changes-response-last.json"), encoding="utf-8"))
    flt = json.load(open(os.path.join(EX, "changes-response-filtered.json"), encoding="utf-8"))
    check("the first page is a game and what names it, oldest first, with more to read", [r["kind"] for r in first["records"]] == ["game", "document", "mame", "rom_list"]
          and first["more"] is True and first["cursor"] == 4 and first["head"] == 11)
    check("a page the filter emptied is empty with more true and the cursor moved past what it skipped", flt["records"] == [] and flt["more"] is True and flt["cursor"] == 9)
    check("the last page carries a tombstone, and no more", last["records"][-1]["deleted"] is True and last["more"] is False)
    check("a reader only ever sees the games it asked for", all(r["id"].startswith("widget-wars") or r["data"].get("model_key") == "widget-wars"
                                                              for p in (first, last) for r in p["records"]))
    code, _, body = get(port, "/api/v1/library/changes?since=0&limit=500&models=widget-wars,pin-comet-run", token)
    allrecs = json.loads(body)["records"]
    kinds = sorted({r["kind"] for r in allrecs})
    check("with both keys, every kind of record is there: game, document, mame, rom_list, rom_image, rail_limit, fact", kinds == sorted(
        ["game", "document", "mame", "rom_list", "rom_image", "rail_limit", "fact"]))
    check("a record's hash is the SHA-256 of its data's canonical JSON (an app may check it)", all(
        hashlib.sha256(json.dumps(r["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest() == r["hash"]
        for r in allrecs if not r["deleted"]))
    code, _, body = get(port, "/api/v1/library/changes?since=0&limit=500", token)
    check("without models a reader gets every record", len(json.loads(body)["records"]) == 10)

    print("manuals, by range:")
    text = open(os.path.join(EX, "files-range-response.txt"), encoding="utf-8").read()
    code, h, body = get(port, f"/api/v1/files/{sha}", token, "bytes=0-127")
    got = (f"GET /api/v1/files/{sha} HTTP/1.1\nRange: bytes=0-127\n\nHTTP/1.1 {code} Partial Content\nContent-Type: {h.get('Content-Type')}\n"
           f"Content-Length: {h.get('Content-Length')}\nAccept-Ranges: {h.get('Accept-Ranges')}\nETag: {h.get('ETag')}\nContent-Range: {h.get('Content-Range')}\n")
    check("a range of a manual: 206 with the headers the example names", code == 206 and got == text and body == pdf[:128])
    code, _, body = get(port, f"/api/v1/files/{sha}", token)
    check("without Range, the whole file, and it hashes to its name", code == 200 and hashlib.sha256(body).hexdigest() == sha
          and body == base64.b64decode(open(os.path.join(EX, "manual.pdf.b64"), "rb").read()))
    check("the example document names exactly that file", json.load(open(os.path.join(EX, "changes-response.json")))["records"][1]["data"]["sha256"] == sha)
    check("the committed example file decodes to that same PDF", base64.b64decode(open(os.path.join(EX, "manual.pdf.b64"), "rb").read()) == pdf)
    code, _, _ = get(port, f"/api/v1/files/{sha}", token, f"bytes={len(pdf) + 10}-")
    check("a range outside the file is 416", code == 416)
    rom = arc.files.put_file(b"rom image bytes that a reader must never get")
    code, _, body = get(port, f"/api/v1/files/{rom}", token)
    code2, _, body2 = get(port, f"/api/v1/files/{'0' * 64}", token)
    check("a ROM image is the same 404 as a file that isn't there", code == 404 and code2 == 404 and body == body2
          and json.loads(body2) == json.load(open(os.path.join(EX, "error-not-found.json"))))
    code, _, body = get(port, "/api/v1/library/changes?since=0", None)
    check("no token: 401 with the example's body", code == 401 and json.loads(body) == json.load(open(os.path.join(EX, "error-unauthorized.json"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
