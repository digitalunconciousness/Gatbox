#!/usr/bin/env python3
"""Manuals and the spec sheet (2026-09-29), with made-up PDFs (matplotlib) served from a local web server:
  gatbox-manuals fetch: a real PDF only, allowed hosts only, the sha256 pinned then checked, sidecar + text, re-runs
    fetch nothing;
  gatbox-web: the documents (the list's titles and kinds, a PDF dropped in by hand, uploads), the PDF itself, a page as
    a PNG (sizes snapped, cached), search, uploads (PDF only, size cap, never over a file), paths never from a request;
  the spec sheet: a rail limit read off a manual page, CONFIRM (only what's on the list), the window it gives the
    logger (gatbox-meta's header), an actual value from the field with a window of its own (the manual's numbers kept
    beside it), facts and their actuals, and taking each back.
Temp dirs and spare ports only.
    python3 tests/test-manuals.py
"""
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools/gatbox-manuals")
T = tempfile.mkdtemp(prefix="gatbox-manuals-")
PORT, SRC_PORT = 8090, 8089
B, SRC = f"http://127.0.0.1:{PORT}", f"http://127.0.0.1:{SRC_PORT}"
SLUG = "segasonic-the-hedgehog"
passed = failed = 0
procs = []


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def pdf(path, pages):
    """A PDF with one text block per page (TrueType fonts, so pdftotext reads it back)."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(path) as pp:
        for text in pages:
            fig = plt.figure(figsize=(8.5, 11))
            fig.text(0.1, 0.8, text, fontsize=14)
            pp.savefig(fig)
            plt.close(fig)


def api(method, path, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(B + path, method=method, data=data,
                                 headers={"Content-Type": ctype} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def js(method, path, body=None, **kw):
    c, _, b = api(method, path, body, **kw)
    try:
        return c, json.loads(b or b"null")
    except ValueError:
        return c, None


def tool(*args, **env):
    e = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_MANUALS=f"{T}/manuals", GATBOX_MANUAL_HOSTS="127.0.0.1")
    e.update(env)
    return subprocess.run(["python3", TOOL, *args], capture_output=True, text=True, env=e, timeout=300)


def png_width(b):
    return int.from_bytes(b[16:20], "big") if b[:8] == b"\x89PNG\r\n\x1a\n" else None


def meta_header():
    r = subprocess.run(["python3", os.path.join(REPO, "backend/gatbox-meta"), "header"], capture_output=True, text=True,
                       env=dict(os.environ, GATBOX_STATE=f"{T}/ctrl", GATBOX_DATA=f"{T}/data"), timeout=30)
    return r.stdout


def main():
    for d in ("src", "data", "manuals", "ctrl", "cache", "log", "run"):
        os.makedirs(f"{T}/{d}")
    pdf(f"{T}/src/a.pdf", ["SEGASONIC OWNER'S MANUAL", "SPECIFICATIONS\n+5 VDC 4.75 to 5.25 V\nFUSE F1 5A SLOW BLOW",
                           "TEST MODE"])
    pdf(f"{T}/src/b.pdf", ["SCHEMATIC SHEET 1"])
    open(f"{T}/src/page.html", "w").write("<html>not a pdf</html>")
    for f in ("profiles.json", "gatbox-machine-specs.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    json.dump({"meta": {"platforms": {}}, "pinball": [], "retired": [], "video_games": [
        {"slug": SLUG, "name": "SegaSonic The Hedgehog", "platform": "x", "mfr": "Sega", "risk": [], "faults": [],
         "parts": [], "notes": ""},
        {"slug": "gauntlet-legends", "name": "Gauntlet Legends", "platform": "x", "mfr": "Atari", "risk": [],
         "faults": [], "parts": [], "notes": ""}]}, open(f"{T}/data/gatbox-barcade-roster.json", "w"))
    listing = {"machines": {
        SLUG: {"docs": [{"id": "sonic-manual", "title": "SegaSonic Owner's Manual", "kind": "manual", "url": f"{SRC}/a.pdf",
                         "source": "test"},
                        {"id": "sonic-schematics", "title": "SegaSonic Schematics", "kind": "schematics", "url": f"{SRC}/b.pdf"},
                        {"id": "not-a-pdf", "title": "A web page", "kind": "other", "url": f"{SRC}/page.html"},
                        {"id": "off-list", "title": "Elsewhere", "kind": "other", "url": "https://example.com/x.pdf"}],
               "specs": {"rails": [{"rail": "+5V", "lo": 4.75, "hi": 5.25, "doc": "sonic-manual", "page": 2,
                                    "quote": "+5 VDC 4.75 to 5.25 V"}],
                         "sheet": [{"what": "Fuse F1", "value": "5 A slow blow", "doc": "sonic-manual", "page": 2,
                                    "quote": "FUSE F1 5A SLOW BLOW"}]}},
        "gauntlet-legends": {"docs": [], "why": "test: none found"}}}
    json.dump(listing, open(f"{T}/data/gatbox-manuals.json", "w"), indent=2)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    src = http.server.ThreadingHTTPServer(("127.0.0.1", SRC_PORT), functools.partial(Quiet, directory=f"{T}/src"))
    threading.Thread(target=src.serve_forever, daemon=True).start()

    print("gatbox-manuals fetch:")
    r = tool("fetch", "--pin", "--pause", "0")
    out = r.stdout
    check("two PDFs fetched, the web page and the off-list host refused, exit 1 for the failures",
          "2 fetched, 0 already here, 2 failed" in out and r.returncode == 1
          and "not-a-pdf: not a PDF" in out and "off-list: not from an allowed archive" in out)
    d = f"{T}/manuals/{SLUG}"
    side = json.load(open(f"{d}/sonic-manual.json"))
    check("sidecar: title, kind, sha256, pages; text with one page per form feed",
          (side["title"], side["kind"], side["pages"], side["added"]) == ("SegaSonic Owner's Manual", "manual", 3, "fetch")
          and len(side["sha256"]) == 64 and "4.75" in open(f"{d}/sonic-manual.txt").read().split("\f")[1])
    pinned = json.load(open(f"{T}/data/gatbox-manuals.json"))["machines"][SLUG]["docs"]
    check("--pin wrote the sha256 of the two into the list", pinned[0].get("sha256") == side["sha256"] and pinned[1].get("sha256"))
    check("group-writable files in a setgid folder", oct(os.stat(f"{d}/sonic-manual.pdf").st_mode & 0o777) == "0o664"
          and os.stat(d).st_mode & 0o2000)
    r = tool("fetch", "--pause", "0")
    check("a re-run fetches nothing", "0 fetched, 2 already here" in r.stdout)
    os.remove(f"{d}/sonic-manual.pdf")
    pdf(f"{T}/src/a.pdf", ["CHANGED UPSTREAM"])
    r = tool("fetch", SLUG, "--pause", "0")
    check("changed upstream: refused (the pinned sha256), nothing left behind",
          "isn't the list's" in r.stdout and not os.path.exists(f"{d}/sonic-manual.pdf")
          and not any(n.endswith(".part") for n in os.listdir(d)))
    pdf(f"{T}/src/a.pdf", ["SEGASONIC OWNER'S MANUAL", "SPECIFICATIONS\n+5 VDC 4.75 to 5.25 V\nFUSE F1 5A SLOW BLOW",
                           "TEST MODE"])
    os.remove(f"{d}/sonic-manual.json")                  # matplotlib stamps a date: the rebuilt PDF has a new sha256
    data = json.load(open(f"{T}/data/gatbox-manuals.json"))
    data["machines"][SLUG]["docs"][0].pop("sha256")
    json.dump(data, open(f"{T}/data/gatbox-manuals.json", "w"), indent=2)
    check("fetched again", "1 fetched" in tool("fetch", SLUG, "--pause", "0").stdout)
    check("list: here, missing, sizes", "segasonic-the-hedgehog" in tool("list").stdout and "missing: not-a-pdf, off-list"
          in tool("list").stdout)
    shutil.copy(f"{T}/src/b.pdf", f"{d}/My Scan (1).pdf")  # dropped in by hand: no sidecar, no text

    print("gatbox-web:")
    env = dict(os.environ, GATBOX_WEB_PORT=str(PORT), STATE_DIRECTORY=f"{T}/ctrl", CACHE_DIRECTORY=f"{T}/cache",
               GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run", GATBOX_DATA=f"{T}/data", GATBOX_MANUALS=f"{T}/manuals",
               GATBOX_UPLOAD_MAX="200000", MPLCONFIGDIR=f"{T}/cache/mpl")
    with socket.socket() as so:
        if so.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"port {PORT} is in use")
    procs.append(subprocess.Popen(["python3", os.path.join(REPO, "backend/gatbox-web")], env=env,
                                  stdout=open(f"{T}/web.log", "w"), stderr=subprocess.STDOUT))
    for _ in range(50):
        try:
            api("GET", "/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)
    c, j = js("GET", "/api/manuals")
    check("overview: documents per machine", c == 200 and j["machines"][SLUG] == {"docs": 3, "listed": 4}
          and j["machines"]["gauntlet-legends"]["docs"] == 0)
    c, j = js("GET", f"/api/manuals/{SLUG}")
    got = [(x["title"], x["kind"], x["pages"], x["added"]) for x in j["docs"]]
    check("documents: the list's titles and kinds, manual first, a dropped PDF as 'yours'",
          got == [("SegaSonic Owner's Manual", "manual", 3, "fetch"), ("SegaSonic Schematics", "schematics", 1, "fetch"),
                  ("My Scan (1)", "yours", 1, "folder")])
    check("... and what's listed but not here", sorted(x["id"] for x in j["not_fetched"]) == ["not-a-pdf", "off-list"])
    check("nothing found: says why", js("GET", "/api/manuals/gauntlet-legends")[1]["why"] == "test: none found")
    c, h, b = api("GET", f"/manual/{SLUG}/sonic-manual.pdf")
    check("the PDF itself", c == 200 and h.get("Content-Type") == "application/pdf" and b == open(f"{d}/sonic-manual.pdf", "rb").read())
    check("a dropped file by its name (spaces, brackets)", api("GET", f"/manual/{SLUG}/My%20Scan%20(1).pdf")[0] == 200)
    check("paths never from a request: traversal, another folder, unknown -> 404",
          [api("GET", p)[0] for p in (f"/manual/{SLUG}/..%2F..%2Fdata%2Fgatbox-manuals.json", f"/manual/nope/a.pdf",
                                       f"/manual/{SLUG}/sonic-manual.txt", f"/manual/{SLUG}/x.pdf")] == [404] * 4)
    c, h, b = api("GET", f"/manual/{SLUG}/sonic-manual.pdf/2.png?w=1100")
    check("page 2 as a PNG, width snapped to 1200", c == 200 and h.get("Content-Type") == "image/png" and png_width(b) == 1200)
    cached = [f for _, _, fs in os.walk(f"{T}/cache/manuals") for f in fs if f.endswith(".png")]
    c2, _, b2 = api("GET", f"/manual/{SLUG}/sonic-manual.pdf/2.png?w=1100")
    check("... cached: the same image the second time", c2 == 200 and b2 == b and cached == ["2-1200.png"])
    check("a page past the end -> 404, a dropped PDF's page draws too",
          api("GET", f"/manual/{SLUG}/sonic-manual.pdf/9.png")[0] == 404
          and png_width(api("GET", f"/manual/{SLUG}/My%20Scan%20(1).pdf/1.png?w=800")[2]) == 800)
    c, j = js("GET", f"/api/manuals/{SLUG}/search?file=sonic-manual.pdf&q=4.75")
    check("search: the page with it, and its line", c == 200 and [x["page"] for x in j["hits"]] == [2]
          and "4.75" in j["hits"][0]["snippet"])
    c, j = js("GET", f"/api/manuals/{SLUG}/search?file=My%20Scan%20(1).pdf&q=sheet")
    check("search in a dropped PDF (text made on the spot)", c == 200 and [x["page"] for x in j["hits"]] == [1])
    check("search: too short -> 400", js("GET", f"/api/manuals/{SLUG}/search?file=sonic-manual.pdf&q=x")[0] == 400)

    print("uploads (from a phone):")
    pdfb = open(f"{T}/src/b.pdf", "rb").read()
    check("not application/pdf -> 415", api("POST", f"/api/manuals/{SLUG}?title=x", raw=pdfb, ctype="text/plain")[0] == 415)
    check("not a PDF -> 400", api("POST", f"/api/manuals/{SLUG}?title=x", raw=b"hello world", ctype="application/pdf")[0] == 400)
    check("over the cap -> 413", api("POST", f"/api/manuals/{SLUG}?title=x", raw=b"%PDF-" + b"0" * 200001,
                                     ctype="application/pdf")[0] == 413)
    c, j = js("POST", f"/api/manuals/{SLUG}?title=Kit%20Sheet&kind=kit", raw=pdfb, ctype="application/pdf")
    check("a PDF -> 201, named from its title, sidecar + text", c == 201 and j["file"] == "kit-sheet.pdf" and j["kind"] == "kit"
          and json.load(open(f"{d}/kit-sheet.json"))["added"] == "upload" and os.path.exists(f"{d}/kit-sheet.txt"))
    c, j = js("POST", f"/api/manuals/{SLUG}?title=Kit%20Sheet&kind=nonsense", raw=pdfb, ctype="application/pdf")
    check("the same title again: a new name, never over a file; an unknown kind is 'yours'",
          c == 201 and j["file"] == "kit-sheet-2.pdf" and j["kind"] == "yours")
    check("uploads to a machine not on the roster -> 404",
          api("POST", "/api/manuals/nope?title=x", raw=pdfb, ctype="application/pdf")[0] == 404)
    check("no half-written files left", not any(n.startswith(".upload-") for n in os.listdir(d)))

    print("the spec sheet:")
    c, j = js("GET", f"/api/specs/{SLUG}")
    r5 = j["rails"][0]
    check("a rail limit read off a manual page: its words, page, title; not confirmed, no window yet",
          c == 200 and r5["rail"] == "+5V" and r5["manual"][0]["page"] == 2 and r5["manual"][0]["title"] == "SegaSonic Owner's Manual"
          and r5["manual"][0]["confirmed"] is False and r5["window"] is None)
    check("the spec page's other facts", [(f["what"], f["value"], f["page"]) for f in j["facts"]] == [("Fuse F1", "5 A slow blow", 2)])
    check("CONFIRM only what's on the list: other numbers, page or doc -> 400",
          [js("POST", f"/api/specs/{SLUG}/confirm", b)[0] for b in ({"rail": "+5V", "doc": "sonic-manual", "page": 3},
           {"rail": "+12V", "doc": "sonic-manual", "page": 2}, {"rail": "+5V", "doc": "x", "page": 2})] == [400, 400, 400])
    c, j = js("POST", f"/api/specs/{SLUG}/confirm", {"rail": "+5V", "doc": "sonic-manual", "page": 2})
    check("CONFIRM -> confirmed, the window in use, cited", c == 200 and j["sheet"]["rails"][0]["manual"][0]["confirmed"]
          and j["sheet"]["rails"][0]["window"] == [4.75, 5.25] and j["result"]["source"] == "SegaSonic Owner's Manual p.2"
          and j["new_file"] is False)
    js("PUT", "/api/machine", {"slug": SLUG})
    c, j = js("GET", "/api/meter/profile")
    check("the logger's window: the confirmed limits, source machine:<slug>",
          j["profile"]["window"] == [4.75, 5.25] and j["profile"]["source"] == f"machine:{SLUG}")
    check("gatbox-meta writes it in the next file's header", f"# window=4.75..5.25 source=machine:{SLUG}" in meta_header())

    print("actual values from the field:")
    bad = [{"rail": "-5V", "value": 5.0}, {"rail": "5V", "value": 5.0}, {"rail": "+5V", "value": 5.2, "window": [5.3, 5.1]},
           {"rail": "+5V", "value": "5.2"}, {"rail": "+5V", "value": 99}, {"fact": "", "value": "x"}, {"value": 1}]
    check("bad actuals -> 400 each", [js("PUT", f"/api/actuals/{SLUG}", b)[0] for b in bad] == [400] * len(bad))
    c, j = js("PUT", f"/api/actuals/{SLUG}", {"rail": "+5V", "value": 5.2, "window": [5.1, 5.3], "note": "boosted at the PSU",
                                              "from": "meter"})
    r5 = j["sheet"]["rails"][0]
    check("an actual with its own window: in use, the manual's numbers kept beside it (still confirmed)",
          c == 200 and r5["actual"]["value"] == 5.2 and r5["actual"]["from"] == "meter" and r5["window"] == [5.1, 5.3]
          and r5["source"] == "actual" and r5["manual"][0]["lo"] == 4.75 and r5["manual"][0]["confirmed"])
    check("the logger's window: source actual:<slug>", f"# window=5.1..5.3 source=actual:{SLUG}" in meta_header()
          and js("GET", "/api/meter/profile")[1]["profile"]["source"] == f"actual:{SLUG}")
    c, j = js("PUT", f"/api/actuals/{SLUG}", {"rail": "+12V", "value": 12.1})
    r12 = next(r for r in j["sheet"]["rails"] if r["rail"] == "+12V")
    check("an actual on a rail the manual doesn't give: a value, no window", r12["actual"]["value"] == 12.1 and r12["window"] is None
          and r12["manual"] == [])
    c, j = js("PUT", f"/api/actuals/{SLUG}", {"fact": "Fuse F1", "value": "8 A slow blow", "note": "the 5 A kept blowing"})
    check("a fact's actual, beside the manual's", [(f["value"], f["actual"]["value"]) for f in j["sheet"]["facts"]
                                                    if f["what"] == "Fuse F1"] == [("5 A slow blow", "8 A slow blow")])
    c, j = js("PUT", f"/api/actuals/{SLUG}", {"fact": "Monitor", "value": "LCD swap"})
    check("a fact the manual doesn't list", any(f["what"] == "Monitor" and f["value"] is None for f in j["sheet"]["facts"]))
    c, j = js("DELETE", f"/api/actuals/{SLUG}", {"rail": "+5V"})
    check("clear the actual: back to the confirmed manual window", c == 200 and j["sheet"]["rails"][0]["window"] == [4.75, 5.25]
          and f"source=machine:{SLUG}" in meta_header())
    c, j = js("DELETE", f"/api/specs/{SLUG}/confirm", {"rail": "+5V"})
    check("take the CONFIRM back: the profile's window again", c == 200 and j["sheet"]["rails"][0]["window"] is None
          and "source=profile" in meta_header())
    check("clearing what isn't there -> 404", js("DELETE", f"/api/actuals/{SLUG}", {"rail": "+5V"})[0] == 404
          and js("DELETE", f"/api/specs/{SLUG}/confirm", {"rail": "+5V"})[0] == 404)
    check("the files are the owner's state, not the data", os.path.exists(f"{T}/ctrl/machine-actuals.json")
          and json.load(open(f"{T}/data/gatbox-manuals.json"))["machines"][SLUG]["specs"]["rails"][0]["lo"] == 4.75)
    web = open(f"{T}/web.log").read()
    check("server log: no errors", "Traceback" not in web and "error on" not in web)
    src.shutdown()
    print(f"manuals: {passed} passed, {failed} failed")


try:
    main()
finally:
    for p in procs:
        p.terminate()
        p.wait(10)
    shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
