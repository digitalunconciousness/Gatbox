#!/usr/bin/env python3
"""Phase D2: the archive's pages. The real server (archive/gatboxarchive) on a spare port over a temp data directory,
driven over HTTP the way a phone does it (an Access identity header, forms with their CSRF field, a PDF as a request
body). Every name, key and byte is invented.
"""
import base64
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlencode

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))

from gatboxarchive import server  # noqa: E402
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402

passed = failed = 0
MIB = 1024 * 1024
PARTNER, OWNER = "partner@example.test", "owner@example.test"


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def access_jwt(email, **extra):
    """What Cloudflare Access puts in Cf-Access-Jwt-Assertion, minus a real signature (the archive reads the payload only)."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'RS256', 'kid': 'test'})}.{b64(dict({'email': email, 'aud': ['x'], 'sub': 'u1'}, **extra))}.c2ln"


def make_pdf(pages=3, tag="a"):
    """A small valid PDF with `pages` pages (pdfinfo and pdftoppm read it), different per `tag`."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{3 + 2 * i} 0 R" for i in range(pages)) + f"] /Count {pages} >>"]
    font = 3 + 2 * pages
    for i in range(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> >>")
        stream = f"BT /F1 24 Tf 40 200 Td (Page {i + 1} {tag}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class Browser:
    def __init__(self, port, email=PARTNER):
        self.port, self.email = port, email

    def call(self, method, path, body=None, headers=None, email="default"):
        h = dict(headers or {})
        who = self.email if email == "default" else email
        if who:
            h["Cf-Access-Jwt-Assertion"] = access_jwt(who)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r, data

    def get(self, path, **kw):
        c, r, d = self.call("GET", path, **kw)
        return c, r, (d.decode("utf-8", "replace") if "text" in (r.getheader("Content-Type") or "") else d)

    def csrf(self, path):
        page = self.get(path)[2]
        hit = re.search(r'name="csrf" value="([0-9a-f]{64})"', page)
        return hit.group(1) if hit else None

    def post(self, path, fields, csrf_from=None, csrf="auto", headers=None, **kw):
        fields = dict(fields)
        if csrf == "auto":
            csrf = self.csrf(csrf_from or path)
        if csrf is not None:
            fields["csrf"] = csrf
        c, r, d = self.call("POST", path, body=urlencode(fields).encode(),
                            headers=dict({"Content-Type": "application/x-www-form-urlencoded"}, **(headers or {})), **kw)
        return c, r, d.decode("utf-8", "replace")


def seed(arc):
    """Games, a document with a file, a limit the deck confirmed, facts: written the way the deck writes them."""
    lib = arc.lib
    lib.put("game", "widget-wars", {"title": "Widget Wars", "mfr": "Acme", "year": 1998, "kind": "video", "notes": "Cabinet is a dedicated."},
            0, by="deck")
    lib.put("game", "pin-comet-run", {"title": "Comet Run", "mfr": "Orbital", "year": 1985, "kind": "pinball"}, 0, by="deck")
    lib.put("game", "no-manual-here", {"title": "No Manual Here", "kind": "video"}, 0, by="deck")
    lib.put("game", "evil-tags", {"title": "<script>alert(1)</script> & co", "kind": "other"}, 0, by="deck")
    lib.put("game", "old-widget", {"title": "Old Widget", "kind": "video", "replaced_by": "widget-wars"}, 0, by="deck")
    lib.put("fact", "f" * 32, {"model_key": "widget-wars", "group": "fuse", "what": "F1", "value": "3 A slow-blow", "doc_id": None,
                               "page": None, "quote": ""}, 0, by="deck")
    lib.put("rail_limit", "a" * 32, {"model_key": "widget-wars", "rail": "+5V", "lo": "4.75", "hi": "5.25", "doc_id": None, "page": None,
                                     "quote": "", "confirmed": {"by": "deck", "at": 1760000000}}, 0, by="deck")
    lib.put("mame", "widget-wars", {"model_key": "widget-wars", "set": "widgetw", "prefer": None, "status": "sure", "why": "same title and year"},
            0, by="deck")
    lib.put("rom_list", "widget-wars", {"model_key": "widget-wars", "mame": "0.276", "set": "widgetw", "versions": [
        {"name": "widgetw", "parent": True, "description": "Widget Wars (rev 2)", "year": "1998", "mfr": "Acme",
         "roms": [{"name": "ww_u1.bin", "size": 65536, "crc": "deadbeef", "sha1": "1" * 40, "region": "maincpu", "status": None, "bios": None}],
         "disks": []}]}, 0, by="deck")
    lib.put("rom_image", "b" * 64, {"size": 65536, "crc32": "deadbeef", "sha1": "1" * 40, "label": "U1", "chip": "U1",
                                    "model_key": "widget-wars", "verdict": "good"}, 0, by="deck")


def main():
    tmp = tempfile.mkdtemp(prefix="gatbox-archive-pages-")
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    sys.stderr = log                              # the server's request log (method, path, status) goes here, not into the report
    try:
        run(tmp)
    finally:
        sys.stderr = sys.__stderr__
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"archive pages: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp):
    static = os.path.join(tmp, "static")             # a copy, with a fonts folder as install.sh makes it (the repo's has none)
    shutil.copytree(os.path.join(REPO, "archive", "static"), static)
    os.makedirs(os.path.join(static, "fonts"))
    with open(os.path.join(static, "fonts", "Test-Regular.ttf"), "wb") as f:
        f.write(b"\x00\x01\x00\x00 not a real font")
    cfg = Config({"ARCHIVE_DATA": f"{tmp}/data", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PRIVATE_BIND": "127.0.0.1:0",
                  "ARCHIVE_PEOPLE": f"{PARTNER},{OWNER}", "ARCHIVE_STATIC": static})
    arc = Archive(cfg)
    tunnel, private = make_servers(arc)
    for s in (tunnel, private):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    B, PRIV = Browser(tunnel.server_address[1]), Browser(private.server_address[1])
    dev = arc.tokens.add("deck", "device")

    print("who sees a page:")
    c, r, page = B.get("/", email=None)
    check("no identity: a plain page that says to sign in (401, HTML, not JSON)", c == 401 and "html" in r.getheader("Content-Type") and "Not signed in" in page)
    check("an email not on the list is refused the same way", B.get("/", email="stranger@example.test")[0] == 401)
    check("a device token is not a person: no pages", B.call("GET", "/", headers={"Authorization": f"Bearer {dev}"}, email=None)[0] == 401)
    check("the private listener serves no page, even with the header", PRIV.get("/")[0] == 401 and PRIV.get("/add")[0] == 401)
    check("an API path still answers in JSON", B.call("GET", "/api/v1/library/digest", email=None)[1].getheader("Content-Type").startswith("application/json"))
    check("a made-up page is a 404 page with a way back", "Back to the games" in B.get("/nope")[2] and B.get("/nope")[0] == 404)

    print("its own files:")
    c, r, css = B.get("/assets/archive.css", email=None)
    check("the stylesheet is served without a sign-in, as CSS", c == 200 and r.getheader("Content-Type").startswith("text/css") and "--mag:#ff2e9f" in css)
    c, r, _js = B.get("/assets/archive.js", email=None)
    check("the script is JavaScript", c == 200 and r.getheader("Content-Type").startswith("text/javascript"))
    c, r, _f = B.get("/assets/fonts/Test-Regular.ttf", email=None)
    check("an installed font is served as a font", c == 200 and r.getheader("Content-Type") == "font/ttf")
    check("a font that isn't installed is a 404, and so is a path out of the folder",
          B.get("/assets/fonts/Missing.ttf", email=None)[0] == 404 and B.get("/assets/fonts/..%2f..%2fetc%2fpasswd", email=None)[0] == 404
          and B.get("/assets/%2e%2e/server.py", email=None)[0] == 404)
    check("a raw ../ out of the fonts folder is a 404 too (it would otherwise reach the stylesheet)",
          B.get("/assets/fonts/../archive.css", email=None)[0] == 404 and B.get("/assets/fonts/..", email=None)[0] == 404)

    print("the games list:")
    c, r, page = B.get("/")
    check("an empty archive says so and offers the one next step", c == 200 and "no games yet" in page and 'href="/add"' in page)
    seed(arc)
    arc.lib.put("document", "widget-wars/service-manual", {"model_key": "widget-wars", "doc_id": "service-manual", "title": "Service manual",
                                                          "kind": "manual", "sha256": None, "pages": 40, "bytes": 4000000,
                                                          "source": "the owner's copy"}, 0, by="deck")
    c, r, page = B.get("/")
    check("games are listed by title, retired ones are not", "Widget Wars" in page and "Comet Run" in page and "Old Widget" not in page)
    check("a game without a document says it in words", "no manual yet" in page)
    check("the CSP forbids inline script and style, and nothing inline is emitted",
          "script-src 'self'" in r.getheader("Content-Security-Policy") and "style-src 'self'" in r.getheader("Content-Security-Policy")
          and " style=" not in page and "<script>" not in page.replace('<script src="/assets/archive.js" defer></script>', "") and " onclick" not in page)
    check("a title with markup in it is shown as text, never as markup", "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in page and "<script>alert" not in page)
    check("the page names the signed-in person, and has a skip link and one h1", PARTNER in page and 'class="skip"' in page and page.count("<h1>") == 1)
    c, r, page = B.get("/?q=comet")
    check("search by title", "Comet Run" in page and "Widget Wars" not in page and "1 of 4 games" in page)
    check("search reaches a fact's text and a document's title", "Widget Wars" in B.get("/?q=slow-blow")[2] and "Widget Wars" in B.get("/?q=service+manual")[2])
    page = B.get("/?q=zzzz")[2]
    check("a search with no hits says what was searched and offers to clear it", "Nothing matches zzzz" in page and "Clear the search" in page)

    print("a game:")
    c, r, page = B.get("/games/widget-wars")
    check("the game page shows its notes, document, limit, fact, MAME set and dumped image",
          c == 200 and "Cabinet is a dedicated." in page and "Service manual" in page and "+5V" in page and "4.75 to 5.25 V" in page
          and "F1" in page and "3 A slow-blow" in page and "widgetw" in page and "ww_u1.bin" in page and ">good<" in page)
    check("a limit the deck confirmed says CONFIRMED in words", ">CONFIRMED<" in page)
    check("a document with no file says so", "no file yet" in page)
    check("a retired key goes to the game that replaced it", B.call("GET", "/games/old-widget")[0] == 303
          and B.call("GET", "/games/old-widget")[1].getheader("Location") == "/games/widget-wars")
    c, r, page = B.get("/games/not-a-game")
    check("a key nobody has is a 404 page", c == 404 and "no game with the key not-a-game" in page)
    check("a game with nothing yet says so in each section", all(t in B.get("/games/no-manual-here")[2] for t in
                                                                 ("No documents for this game yet.", "No rail limits for this game yet.",
                                                                  "No facts for this game yet.", "No MAME entry for this game.")))

    print("adding a game:")
    check("the form is a page", "Check the key" in B.get("/add")[2])
    c, _, page = B.post("/add", {"title": "Rampage Reloaded", "mfr": "Orbital", "year": "1999", "kind": "video", "notes": ""}, csrf=None)
    check("a form without the CSRF field is refused", c == 403 and "expired" in page)
    c, _, page = B.post("/add", {"title": "Rampage Reloaded", "mfr": "Orbital", "year": "1999", "kind": "video", "notes": ""}, csrf="0" * 64)
    check("and so is a wrong one", c == 403)
    c, _, page = B.post("/add", {"title": "Rampage Reloaded", "mfr": "Orbital", "year": "1999", "kind": "video", "notes": ""}, csrf_from="/add",
                        headers={"Sec-Fetch-Site": "cross-site"})
    check("and a request the browser marks cross-site, even with the right field", c == 403 and "another site" in page)
    fields = {"title": "Rampage Reloaded", "mfr": "Orbital", "year": "1999", "kind": "video", "notes": "Found in a barn."}
    c, _, page = B.post("/add", fields, csrf_from="/add")
    check("the preview shows the key it will get and writes nothing yet",
          c == 200 and 'value="rampage-reloaded"' in page and "be changed later" in page and arc.lib.get("game", "rampage-reloaded") is None)
    uid = re.search(r'name="uid" value="([0-9a-f]{32})"', page).group(1)
    save = dict(fields, key="rampage-reloaded", uid=uid)
    c, r, _ = B.post("/add/save", save, csrf_from="/add")
    g = arc.lib.get("game", "rampage-reloaded")
    check("saving creates it, named by the person, and goes to its page", c == 303 and r.getheader("Location") == "/games/rampage-reloaded?done=added"
          and g and g["by"] == PARTNER and g["data"]["year"] == 1999 and g["data"]["notes"] == "Found in a barn.")
    c, r, _ = B.post("/add/save", save, csrf_from="/add")
    check("a double tap (the same form twice) is one game and no error", c == 303 and arc.lib.get("game", "rampage-reloaded")["rev"] == 1)
    check("the game page says it was added", "Game added." in B.get("/games/rampage-reloaded?done=added")[2])
    c, _, page = B.post("/add/save", dict(save, uid=uuid.uuid4().hex), csrf_from="/add")
    check("a key that's taken is refused in words, and what was typed is kept",
          c == 200 and "already taken" in page and 'value="Rampage Reloaded"' in page and arc.lib.get("game", "rampage-reloaded")["rev"] == 1)
    c, _, page = B.post("/add", dict(fields, year="nineteen"), csrf_from="/add")
    check("a year that isn't a number is explained, with the form back", "Year must be a whole number." in page and 'value="Rampage Reloaded"' in page)
    c, _, page = B.post("/add", dict(fields, title=""), csrf_from="/add")
    check("a missing title is explained", "A game needs a title." in page)
    c, _, page = B.post("/add", {"title": "Widget Wars", "kind": "video", "mfr": "", "year": "", "notes": ""}, csrf_from="/add")
    check("a title that's already here warns, and links to the one that exists",
          'href="/games/widget-wars"' in page and "already here" in page and 'value="widget-wars-' not in page.split("Key")[0] and "widget-wars" in page)
    c, _, page = B.post("/add", {"title": "Rocket Pinball", "kind": "pinball", "mfr": "", "year": "", "notes": "", "key": "pin-rocket-pinball"},
                        csrf_from="/add")
    check("a pinball's key starts with pin-", B.post("/add", {"title": "Rocket Pinball", "kind": "pinball", "mfr": "", "year": "", "notes": ""},
                                                     csrf_from="/add")[2].count('value="pin-rocket-pinball"') == 1)
    c, _, page = B.post("/add", dict(fields, key="Bad Key"), csrf_from="/add")
    check("a typed key that isn't a key is explained", c == 200 and "Bad Key" in page and "lower-case" in page.lower() or "key" in page.lower())
    c, _, page = B.post("/add", dict(fields, key="widget-wars"), csrf_from="/add")
    check("a typed key that another game has is refused at the preview", "already used by another game" in page)

    print("editing a game:")
    g = arc.lib.get("game", "rampage-reloaded")
    form = B.get("/games/rampage-reloaded/edit")[2]
    check("the edit form is filled in and says the key is permanent", 'value="Rampage Reloaded"' in form and "permanent" in form
          and f'name="rev" value="{g["rev"]}"' in form)
    new = {"title": "Rampage Reloaded", "mfr": "Orbital Games", "year": "1999", "kind": "video", "notes": "Found in a barn.", "rev": str(g["rev"])}
    c, r, _ = B.post("/games/rampage-reloaded/edit", new, csrf_from="/games/rampage-reloaded/edit")
    g2 = arc.lib.get("game", "rampage-reloaded")
    check("saving with the right revision updates it", c == 303 and r.getheader("Location") == "/games/rampage-reloaded?done=saved"
          and g2["rev"] == g["rev"] + 1 and g2["data"]["mfr"] == "Orbital Games")
    arc.lib.put("game", "rampage-reloaded", dict(g2["data"], notes="Moved to the back room."), g2["rev"], by="deck")
    c, _, page = B.post("/games/rampage-reloaded/edit", dict(new, mfr="Orbital Gaming", rev=str(g2["rev"])), csrf_from="/games/rampage-reloaded/edit")
    check("saving against an old revision writes nothing, says someone else saved, keeps what was typed and shows theirs",
          c == 200 and "Someone else saved this game" in page and 'value="Orbital Gaming"' in page and "Moved to the back room." in page
          and arc.lib.get("game", "rampage-reloaded")["data"]["notes"] == "Moved to the back room.")
    cur = arc.lib.get("game", "rampage-reloaded")
    check("and the form it gives back carries the new revision, so the next save is deliberate",
          f'name="rev" value="{cur["rev"]}"' in page)
    c, r, _ = B.post("/games/rampage-reloaded/edit", dict(new, mfr="Orbital Gaming", rev=str(cur["rev"])), csrf_from="/games/rampage-reloaded/edit")
    check("saving again replaces theirs", c == 303 and arc.lib.get("game", "rampage-reloaded")["data"]["mfr"] == "Orbital Gaming")
    check("a game that was merged away can't be edited", B.get("/games/old-widget/edit")[0] == 404)

    print("rail limits and facts:")
    base = "/games/widget-wars/limits/add"
    form = B.get(base)[2]
    rid = re.search(r'name="rid" value="([0-9a-f]{32})"', form).group(1)
    check("the add-a-limit form says it will wait for confirmation", "not confirmed until someone confirms it" in form)
    lim = {"rail": "+12V", "lo": "11.4 V", "hi": "12.6", "doc_id": "", "page": "7", "quote": "12 V +/- 5%", "rev": "0", "rid": rid, "uid": uuid.uuid4().hex}
    c, r, _ = B.post("/games/widget-wars/limits", dict(lim, confirmed='{"by":"x","at":1}'), csrf_from=base)
    rec = arc.lib.get("rail_limit", rid)
    check("a limit is created unconfirmed (a spoofed confirmed field is ignored), and '11.4 V' is read as 11.4",
          c == 303 and rec and rec["data"]["confirmed"] is None and rec["data"]["lo"] == "11.4" and rec["data"]["page"] == 7 and rec["by"] == PARTNER)
    page = B.get("/games/widget-wars")[2]
    check("the page lists it as NOT CONFIRMED, in words", ">NOT CONFIRMED<" in page and "+12V" in page and "12 V +/- 5%" in page)
    c, _, page = B.post("/games/widget-wars/limits", dict(lim, rid=uuid.uuid4().hex, uid=uuid.uuid4().hex, lo="13", hi="12"), csrf_from=base)
    check("a low above the high is refused with the reason and nothing written", c == 200 and "Not saved" in page and "lo is above hi" in page
          and 'value="+12V"' in page)
    c, _, page = B.post("/games/widget-wars/limits", dict(lim, rid=uuid.uuid4().hex, uid=uuid.uuid4().hex, rail=""), csrf_from=base)
    check("a limit with no rail name is explained", "Name the rail" in page)
    c, _, page = B.post("/games/widget-wars/limits", dict(lim, rid=uuid.uuid4().hex, uid=uuid.uuid4().hex, page="x"), csrf_from=base)
    check("a page that isn't a number is explained", "Page must be a whole number." in page)
    # editing the deck-confirmed +5V: the quote alone keeps the confirmation, the numbers clear it
    five = arc.lib.get("rail_limit", "a" * 32)
    edit = "/games/widget-wars/limits/" + "a" * 32
    body = {"rail": "+5V", "lo": "4.75", "hi": "5.25", "doc_id": "", "page": "", "quote": "5 V +/- 5%", "rev": str(five["rev"]), "rid": "a" * 32,
            "uid": uuid.uuid4().hex}
    B.post(edit, body, csrf_from=edit)
    five2 = arc.lib.get("rail_limit", "a" * 32)
    check("adding a quote to a confirmed limit leaves it confirmed", five2["data"]["quote"] == "5 V +/- 5%" and five2["data"]["confirmed"] is not None)
    B.post(edit, dict(body, hi="5.5", rev=str(five2["rev"]), uid=uuid.uuid4().hex), csrf_from=edit)
    five3 = arc.lib.get("rail_limit", "a" * 32)
    check("changing a number clears the confirmation: it is the deck's to confirm again", five3["data"]["hi"] == "5.5" and five3["data"]["confirmed"] is None)
    check("the edit page of a limit that isn't this game's is a 404", B.get("/games/comet-run/limits/" + "a" * 32)[0] == 404)
    c, _, page = B.post(edit, dict(body, hi="5.6", rev=str(five2["rev"]), uid=uuid.uuid4().hex), csrf_from=edit)
    check("an edit against an old revision shows theirs and keeps yours", "Someone else saved this limit" in page and 'value="5.6"' in page
          and arc.lib.get("rail_limit", "a" * 32)["data"]["hi"] == "5.5")
    fbase = "/games/widget-wars/facts/add"
    frid = uuid.uuid4().hex
    fact = {"group": "monitor", "what": "Chassis", "value": "25 inch", "doc_id": "", "page": "", "quote": "", "rev": "0", "rid": frid, "uid": uuid.uuid4().hex}
    c, _, _ = B.post("/games/widget-wars/facts", fact, csrf_from=fbase)
    check("a fact is added", c == 303 and arc.lib.get("fact", frid)["data"]["what"] == "Chassis" and "25 inch" in B.get("/games/widget-wars")[2])
    c, _, page = B.post("/games/widget-wars/facts", dict(fact, rid=uuid.uuid4().hex, uid=uuid.uuid4().hex, what=""), csrf_from=fbase)
    check("a fact about nothing is explained", "Say what the fact is about" in page)
    fedit = f"/games/widget-wars/facts/{frid}"
    page = B.get(fedit)[2]
    check("the edit page has a remove step that asks first", "Remove this fact" in page and "Yes, remove it" in page)
    frec = arc.lib.get("fact", frid)
    c, r, _ = B.post(fedit + "/remove", {"rev": "99", "uid": uuid.uuid4().hex}, csrf_from=fedit)
    check("removing against an old revision removes nothing and says it changed", c == 303 and "done=changed" in r.getheader("Location")
          and arc.lib.get("fact", frid) is not None and "Someone changed this while you were looking" in B.get(r.getheader("Location"))[2])
    c, r, _ = B.post(fedit + "/remove", {"rev": str(frec["rev"]), "uid": uuid.uuid4().hex}, csrf_from=fedit)
    check("removing is a tombstone the deck will see, and the page stops listing it",
          c == 303 and arc.lib.get("fact", frid) is None and arc.lib.get("fact", frid, deleted=True)["deleted"] and "Chassis" not in B.get("/games/widget-wars")[2])

    print("documents:")
    pdf = make_pdf(3, "x")
    up = "/games/widget-wars/upload?title=Operator+manual&kind=manual"
    tok = re.search(r'data-csrf="([0-9a-f]{64})"', B.get("/games/widget-wars")[2]).group(1)
    hdr = {"Content-Type": "application/pdf", "Accept": "application/json", "X-CSRF": tok}
    c, r, d = B.call("POST", up, body=pdf, headers=hdr)
    out = json.loads(d)
    rec = arc.lib.get("document", "widget-wars/operator-manual")
    check("a PDF upload stores the file by its hash and lists it as a document, counting its pages",
          c == 200 and out == {"ok": True, "doc_id": "operator-manual"} and rec and rec["data"]["pages"] == 3 and rec["data"]["bytes"] == len(pdf)
          and arc.files.size(rec["data"]["sha256"]) == len(pdf) and rec["by"] == PARTNER)
    c, r, d = B.call("POST", up, body=pdf, headers=hdr)
    check("the same PDF again is the same document, not a second one", json.loads(d).get("same") is True and len(arc.lib.all("document", "widget-wars")) == 2)
    c, r, d = B.call("POST", up, body=make_pdf(2, "y"), headers=hdr)
    check("another file with the same title gets its own id", json.loads(d)["doc_id"] == "operator-manual-2")
    c, r, d = B.call("POST", up, body=b"GIF89a not a pdf at all", headers=hdr)
    check("something that isn't a PDF is refused in JSON, and nothing is listed", c == 415 and "error" in json.loads(d) and len(arc.lib.all("document", "widget-wars")) == 3)
    c, r, d = B.call("POST", up, body=pdf, headers=dict(hdr, **{"X-CSRF": "0" * 64}))
    check("an upload without a good CSRF header is refused", c == 403 and "error" in json.loads(d))
    c, r, d = B.call("POST", "/games/widget-wars/upload?title=&kind=manual", body=pdf, headers=hdr)
    check("an upload with no title is explained", c == 400 and "title" in json.loads(d)["error"])
    c, r, d = B.call("POST", "/games/widget-wars/upload?title=X&kind=comic", body=pdf, headers=hdr)
    check("an unknown kind is refused", c == 400)
    conn = http.client.HTTPConnection("127.0.0.1", tunnel.server_address[1], timeout=20)
    conn.putrequest("POST", up)
    for k, v in dict(hdr, **{"Content-Length": str(95 * MIB), "Cf-Access-Jwt-Assertion": access_jwt(PARTNER)}).items():
        conn.putheader(k, v)
    conn.endheaders()
    r = conn.getresponse()
    check("a file over 90 MB is refused before it is read", r.status == 413 and "90 MB" in json.loads(r.read())["error"])
    conn.close()
    check("nothing is left in the upload folder after the refusals", not [n for n in os.listdir(os.path.join(tmp, "data", "files", ".incoming"))
                                                                          if n.startswith(".upload-")])
    c, r, page = B.get("/games/widget-wars/docs/operator-manual?page=2")
    check("the viewer shows page 2 of 3 as an image with a text alternative, and the way around",
          c == 200 and "Page 2 of 3" in page and 'src="/games/widget-wars/docs/operator-manual/page/2.png"' in page
          and 'alt="Page 2 of Operator manual"' in page and "Previous page" in page and "Next page" in page and "Open the PDF" in page)
    check("page 99 is the last page, and the first page has no Previous", "Page 3 of 3" in B.get("/games/widget-wars/docs/operator-manual?page=99")[2]
          and "Previous page" not in B.get("/games/widget-wars/docs/operator-manual")[2])
    c, r, png = B.get("/games/widget-wars/docs/operator-manual/page/2.png")
    check("the page image is a PNG, drawn once and kept", c == 200 and png[:4] == b"\x89PNG" and r.getheader("Content-Type") == "image/png"
          and os.path.isfile(os.path.join(tmp, "data", "cache", "pages", rec["data"]["sha256"], "2.png")))
    check("a page past the end is a 404", B.get("/games/widget-wars/docs/operator-manual/page/4.png")[0] == 404
          and B.get("/games/widget-wars/docs/operator-manual/page/0.png")[0] == 404)
    c, r, pdf_back = B.get(f"/api/v1/files/{rec['data']['sha256']}")
    check("Open the PDF is the archive's own file call, and a person may read it", c == 200 and pdf_back == pdf and r.getheader("Content-Type") == "application/pdf")
    check("a page image needs a person too", B.get("/games/widget-wars/docs/operator-manual/page/1.png", email=None)[0] == 401)
    check("a document with no file says so and points to the upload", "does not hold the file" in B.get("/games/widget-wars/docs/service-manual")[2])
    check("a document that isn't there is a 404", B.get("/games/widget-wars/docs/nothing")[0] == 404)
    edoc = "/games/widget-wars/docs/operator-manual/edit"
    rec = arc.lib.get("document", "widget-wars/operator-manual")
    c, r, _ = B.post(edoc, {"title": "Operator's manual", "kind": "parts", "source": "the box", "rev": str(rec["rev"]), "uid": uuid.uuid4().hex}, csrf_from=edoc)
    rec2 = arc.lib.get("document", "widget-wars/operator-manual")
    check("a document's title and kind can be changed and the file stays", c == 303 and rec2["data"]["title"] == "Operator's manual"
          and rec2["data"]["kind"] == "parts" and rec2["data"]["sha256"] == rec["data"]["sha256"])
    c, _, page = B.post(edoc, {"title": "", "kind": "parts", "source": "", "rev": str(rec2["rev"]), "uid": uuid.uuid4().hex}, csrf_from=edoc)
    check("a document without a title is explained", "A document needs a title." in page)
    c, r, _ = B.post("/games/widget-wars/docs/operator-manual/remove", {"rev": str(rec2["rev"]), "uid": uuid.uuid4().hex}, csrf_from=edoc)
    check("removing a document tombstones the entry and keeps the file", c == 303 and arc.lib.get("document", "widget-wars/operator-manual") is None
          and arc.files.size(rec["data"]["sha256"]) == len(pdf))

    print("when something breaks:")
    server.route("GET", r"/boom")(lambda h, m, q: 1 / 0)
    c, r, page = B.get("/boom")
    check("a bug is a plain page, not a dropped connection or a stack trace", c == 500 and "hit an error" in page and "ZeroDivisionError" not in page)
    check("and the archive is still answering", B.get("/")[0] == 200)
    return 0


if __name__ == "__main__":
    sys.exit(main())
