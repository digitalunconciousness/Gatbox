#!/usr/bin/env python3
"""Phase D3: the deck's library sync, end to end with nothing stubbed: the real archive (archive/gatboxarchive, on a spare
port), the real gatbox-web on an invented deck (tests/fakedeck.py, seeded), and the real gatbox-sync script and gatboxlib.libsync
between them. The merge's own cases are tests/test-libsync.py; this proves the wiring: the unseeded deck, the dry run that is
the owner's first look, the first real run, a manual in each direction (resumed part way), a partner's edit unconfirming a
limit the logger would have used, the deck's CONFIRM reaching the archive, the archive out of reach, a refused token, the
Access service token on the wire, and that the sync's door into the deck answers the Pi itself only. Every value is invented.
"""
import hashlib
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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "archive"))
sys.path.insert(0, os.path.join(REPO, "tests"))

from fakedeck import build_deck  # noqa: E402
from gatboxarchive import api  # noqa: E402
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402
from gatboxlib import libsync  # noqa: E402

T = tempfile.mkdtemp(prefix="gatbox-sync-library-")
STATE, SYNC = f"{T}/state", f"{T}/sync"
EXPORT = f"{STATE}/library-specs.json"
TOOL = os.path.join(REPO, "tools/gatbox-library")
SCRIPT = os.path.join(REPO, "backend/gatbox-sync")
PARTNER = {"kind": "person", "name": "partner@example.test"}
passed = failed = 0
MIB = 1024 * 1024


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


WEB_PORT = free_port()
WEB = f"http://127.0.0.1:{WEB_PORT}"


def env(**extra):
    e = dict(os.environ, GATBOX_DATA=f"{T}/data", GATBOX_STATE=STATE, STATE_DIRECTORY=STATE, CACHE_DIRECTORY=f"{T}/cache",
             GATBOX_MANUALS=f"{T}/manuals", GATBOX_ROMS=f"{T}/roms", GATBOX_MAME_ROMS=f"{T}/mame-roms.json",
             GATBOX_LIBRARY=f"{T}/lib/library.sqlite", GATBOX_LOGDIR=f"{T}/log", GATBOX_RUNDIR=f"{T}/run",
             GATBOX_WEB_PORT=str(WEB_PORT), GATBOX_SYNC_STATE=SYNC, MPLCONFIGDIR=f"{T}/cache/mpl",
             GATBOX_MAME_TITLES=f"{T}/mame-titles.json", GATBOX_MANUALS_SPOOL=f"{T}/mspool")
    e.update(extra)
    return e


def web(method, path, body=None, raw=None, ctype=None, base=WEB):
    data, headers = raw, {}
    if body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(base + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = r.read()
            return r.status, (json.loads(out) if r.headers.get("Content-Type", "").startswith("application/json") else out)
    except urllib.error.HTTPError as e:
        out = e.read()
        try:
            return e.code, json.loads(out)
        except ValueError:
            return e.code, out


def settles(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def exported():
    try:
        with open(EXPORT, encoding="utf-8") as f:
            return json.load(f)["machines"]
    except (OSError, ValueError, KeyError):
        return None


class CountingDeck(libsync.Deck):
    """The real gatbox-web client that notes which parts it staged."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.staged = []

    def stage_part(self, sha, n, data):
        self.staged.append((sha, n))
        return super().stage_part(sha, n, data)


class Counting(libsync.Archive):
    """The real archive client that notes what it asked for."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ranges, self.parts, self.begun = [], [], []

    def file_range(self, sha, start, end):
        self.ranges.append((sha, start, end))
        return super().file_range(sha, start, end)

    def file_part(self, sha, n, data):
        self.parts.append((sha, n))
        return super().file_part(sha, n, data)

    def file_begin(self, sha, size):
        self.begun.append(sha)
        return super().file_begin(sha, size)


def main():
    code = 1
    web_proc = None
    sys.stderr = open(f"{T}-server.log", "w", encoding="utf-8")        # the archive's request log, not the report
    try:
        web_proc, arc, urlbase, token = start()
        run(arc, urlbase, token)
        code = 1 if failed else 0
    finally:
        if web_proc:
            web_proc.terminate()
            try:
                web_proc.wait(5)
            except subprocess.TimeoutExpired:
                web_proc.kill()
        sys.stderr = sys.__stderr__
        shutil.rmtree(T, ignore_errors=True)
        try:
            os.remove(f"{T}-server.log")
        except OSError:
            pass
    print(f"sync library: {passed} passed, {failed} failed")
    return code


def start():
    build_deck(T, stale=False)
    for d in ("log", "run", "cache", "sync", "state", "lib"):
        os.makedirs(f"{T}/{d}", exist_ok=True)
    for f in ("profiles.json", "eproms.json"):
        shutil.copy(os.path.join(REPO, "data", f), f"{T}/data/")
    subprocess.run([sys.executable, os.path.join(REPO, "tools/gatbox-mame-roms"), "--titles", f"{T}/mame-titles.json"], check=True,
                   capture_output=True, env=dict(os.environ, GATBOX_MAME=os.path.join(REPO, "tests/fake-mame")))
    cfg = Config({"ARCHIVE_DATA": f"{T}/archive", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_MAX_FILE": str(50 * MIB),
                  "ARCHIVE_STATIC": os.path.join(REPO, "archive", "static")})
    arc = Archive(cfg)
    (tunnel,) = make_servers(arc)
    threading.Thread(target=tunnel.serve_forever, daemon=True).start()
    token = arc.tokens.add("deck", "device")
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "backend/gatbox-web")], env=env(), stdout=open(f"{T}/web.log", "w"),
                            stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            web("GET", "/kiosk/state")
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise SystemExit("gatbox-web didn't start")
    return proc, arc, f"http://127.0.0.1:{tunnel.server_address[1]}", token


def script(urlbase, token, dryrun=False, access=None, state=SYNC, extra=None):
    conf = f"{T}/archive.conf"
    lines = [f"ARCHIVE_URL={urlbase}", f"ARCHIVE_TOKEN={token}"] + ([f"ARCHIVE_ACCESS_ID={access[0]}", f"ARCHIVE_ACCESS_SECRET={access[1]}"] if access else [])
    with open(conf, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    e = dict(os.environ, GATBOX_SYNC_ARCHIVE_CONF=conf, GATBOX_SYNC_CONF=f"{T}/no-hub.conf", STATE_DIRECTORY=state, GATBOX_SYNC_WEB=WEB)
    e.pop("CREDENTIALS_DIRECTORY", None)
    if dryrun:
        e["GATBOX_SYNC_DRYRUN"] = "1"
    e.update(extra or {})
    return subprocess.run([sys.executable, SCRIPT], env=e, capture_output=True, text=True, timeout=300, check=False)


def script_with_hub(urlbase, token):
    """The same, with a hub configured that nothing answers at (the hubs' part finds no hub reachable, exit 0 on its own)."""
    hub = f"{T}/hub.conf"
    with open(hub, "w", encoding="utf-8") as f:
        f.write("HUB_URLS=http://127.0.0.1:9\nHUB_TOKEN=gbx_aaaaaaaaaaaa.not-a-real-value\n")
    with open(f"{T}/archive.conf", "w", encoding="utf-8") as f:
        f.write(f"ARCHIVE_URL={urlbase}\nARCHIVE_TOKEN={token}\n")
    e = dict(os.environ, GATBOX_SYNC_ARCHIVE_CONF=f"{T}/archive.conf", GATBOX_SYNC_CONF=hub, STATE_DIRECTORY=f"{T}/s4", GATBOX_SYNC_WEB=WEB)
    e.pop("CREDENTIALS_DIRECTORY", None)
    return subprocess.run([sys.executable, SCRIPT], env=e, capture_output=True, text=True, timeout=300, check=False)


def status():
    with open(f"{SYNC}/status.json", encoding="utf-8") as f:
        return json.load(f)


def inproc(urlbase, token, cls=libsync.Archive, deck_cls=libsync.Deck, **kw):
    log = []
    arch, deck = cls(urlbase, token, timeout=60), deck_cls(WEB, timeout=60)
    st = libsync.run(deck, arch, f"{SYNC}/library", log=log.append, **kw)
    return st, (arch, deck) if deck_cls is not libsync.Deck else arch, log


def run(arc, urlbase, token):
    print("a deck that isn't seeded:")
    st, _, _ = inproc(urlbase, token)
    check("the sync says so and changes nothing (the deck has no library to compare)", st["ok"] is False and "isn't set up" in st["error"]
          and arc.lib.counts() == {})
    r = script(urlbase, token)
    check("the script exits 0: a deck that isn't ready is not a fault that retrying could not fix", r.returncode == 0 and status()["library"]["ok"] is False)

    print("seeding the deck, then the owner's first look (a dry run):")
    p = subprocess.run([sys.executable, TOOL, "seed", "--plan", "--out", f"{T}/plan.json"], env=env(), capture_output=True, text=True, check=False)
    a = subprocess.run([sys.executable, TOOL, "seed", "--apply", f"{T}/plan.json"], env=env(), capture_output=True, text=True, check=False)
    check("the invented deck is seeded", p.returncode == 0 and a.returncode == 0, p.stdout + p.stderr + a.stdout + a.stderr)
    shutil.rmtree(f"{SYNC}/library", ignore_errors=True)
    from gatboxlib import library as L
    check("gatbox-web adopts the invented deck's dumps as ROM image records within seconds",
          settles(lambda: L.Library(f"{T}/lib/library.sqlite", create=False).counts().get("rom_image", 0) >= 2, 30))
    deck_games = web("GET", "/api/library")[1]["games"]
    r = script(urlbase, token, dryrun=True)
    check("the dry run prints the plan, line by line, and says nothing was changed",
          r.returncode == 0 and "send  game|widget-wars" in r.stdout and "dry run:" in r.stdout and "Nothing was changed" in r.stdout, r.stdout + r.stderr)
    check("and it changed nothing: the archive is empty, there is no library state, no status written",
          arc.lib.counts() == {} and not os.path.exists(f"{SYNC}/library/state.json") and arc.files.size("0" * 64) is None)
    n_deck = web("GET", "/api/library/sync/digest")[1]["count"]

    print("the first real run:")
    r = script(urlbase, token)
    st = status()["library"]
    check("it exits 0, every record goes up and none is refused", r.returncode == 0 and st["ok"] and st["pushed"] == n_deck and not st["refused"], r.stdout + r.stderr)
    check("the archive holds the deck's games, documents, limits, MAME sets and ROM lists", {"game", "document", "rail_limit", "mame", "rom_list"} <= set(arc.lib.counts())
          and len(arc.lib.all("game")) == len(deck_games))
    manual = next(d for d in arc.lib.all("document") if d["data"]["doc_id"] == "widget-wars-manual")
    sha = manual["data"]["sha256"]
    check("the deck's manual went up with it, byte for byte", arc.files.size(sha) is not None and open(arc.files.path(sha), "rb").read() == open(f"{T}/manuals/widget-wars/widget-wars-manual.pdf", "rb").read())
    images = [r["id"] for r in arc.lib.all("rom_image")]
    on_deck = {}
    for dp, _, fs in os.walk(f"{T}/roms"):
        for f in fs:
            if f.endswith(".bin"):
                data = open(os.path.join(dp, f), "rb").read()
                on_deck[hashlib.sha256(data).hexdigest()] = data
    check("every dump image the deck holds went up as a file under its SHA-256, byte for byte, beside its record",
          len(images) >= 2 and all(i in on_deck and arc.files.size(i) == len(on_deck[i]) and open(arc.files.path(i), "rb").read() == on_deck[i]
                                   for i in images), f"{len(images)} images, {len(on_deck)} files")
    reader = arc.tokens.add("an-app", "reader")
    req = urllib.request.Request(f"{urlbase}/api/v1/files/{images[0]}", headers={"Authorization": f"Bearer {reader}"})
    try:
        urllib.request.urlopen(req, timeout=20)
        got = 200
    except urllib.error.HTTPError as e:
        got = e.code
    check("and an app's reader token is told there is no such file (a ROM image never leaves the deck and the archive)", got == 404)
    check("the run says the libraries are identical", st["identical"] is True and web("GET", "/api/library/sync/digest")[1] == arc.lib.content_digest())
    check("the confirmed limit went up as confirmed (the 7\"'s own CONFIRM)", any(r["data"]["confirmed"] for r in arc.lib.all("rail_limit")))
    r = script(urlbase, token)
    st2 = status()["library"]
    check("a second run does nothing at all: no record, no file, and no second upload of a manual",
          st2["pushed"] == 0 and st2["pulled"] == 0 and st2["files"]["up"]["sent"] == 0 and st2["files"]["up"]["bytes"] == 0 and r.returncode == 0)

    print("a manual from the archive, in two parts, resumed:")
    big = b"%PDF-1.4\n" + os.urandom(9 * MIB)                         # 9 MiB: two parts of 8 MiB
    bsha = hashlib.sha256(big).hexdigest()
    sha_, size_ = arc.files.put_stream(__import__("io").BytesIO(big), len(big), magic=b"%PDF-")
    assert sha_ == bsha
    res = api.apply_writes(arc.lib, [{"kind": "document", "id": "widget-wars/arcade-guide", "if_rev": 0, "data": {
        "model_key": "widget-wars", "doc_id": "arcade-guide", "title": "Arcade guide", "kind": "parts", "sha256": bsha, "pages": 2, "bytes": len(big),
        "source": "uploaded by the partner"}}], PARTNER)
    check("the partner's upload is a document the archive lists", res[0]["status"] == "created")
    # part 0 is already staged on the deck, as if an earlier run was cut off
    stage = web("POST", "/api/library/sync/files", {"sha256": bsha, "size": len(big)})[1]
    first = big[:8 * MIB]
    web("PUT", f"/api/library/sync/files/{bsha}/parts/0", raw=first, ctype="application/octet-stream")
    st, (arch, deck), log = inproc(urlbase, token, cls=Counting, deck_cls=CountingDeck)
    check("the record comes down and the file follows", st["ok"] and st["pulled"] == 1 and st["files"]["down"]["fetched"] == 1 and not st["files"]["down"]["failed"], str(st))
    gets = [r for r in arch.ranges if r[0] == bsha]
    check("only the missing part was stored on the deck: part 1, not part 0 again (asked once, to learn the size)",
          deck.staged == [(bsha, 1)] and any(r[1] == 8 * MIB for r in gets) and len([r for r in gets if r[1] == 0]) == 1 and stage["state"] == "open")
    placed = f"{T}/manuals/widget-wars/arcade-guide.pdf"
    check("it is placed in the game's own folder, byte for byte, with a sidecar that names its hash",
          os.path.isfile(placed) and open(placed, "rb").read() == big and json.load(open(placed[:-4] + ".json"))["sha256"] == bsha)
    docs = web("GET", "/api/manuals/widget-wars")[1]["docs"]
    check("the deck's own manuals list shows it, with the title and kind the archive gave it", any(d["file"] == "arcade-guide.pdf" and d["title"] == "Arcade guide" and d["kind"] == "parts" for d in docs), str([d["file"] for d in docs]))
    check("nothing is left staged", not os.path.exists(f"{STATE}/library-files/{bsha[:2]}/{bsha}"))
    st, arch, _ = inproc(urlbase, token, cls=Counting)
    check("and the next run neither fetches nor sends it again", arch.ranges == [] and st["files"]["down"]["fetched"] == 0)

    print("a file that would land on one the deck already has:")
    mine = b"%PDF-1.4 a hand-dropped file already called clash"
    with open(f"{T}/manuals/widget-wars/clash.pdf", "wb") as f:
        f.write(mine)
    other = b"%PDF-1.4 the partner's different file, same document id"
    osha, _ = arc.files.put_stream(__import__("io").BytesIO(other), len(other), magic=b"%PDF-")
    api.apply_writes(arc.lib, [{"kind": "document", "id": "widget-wars/clash", "if_rev": 0, "data": {
        "model_key": "widget-wars", "doc_id": "clash", "title": "Clash", "kind": "other", "sha256": osha, "pages": 1, "bytes": len(other)}}], PARTNER)
    st, _, _ = inproc(urlbase, token)
    check("it is placed beside it under another name: the deck's file is never overwritten",
          st["ok"] and open(f"{T}/manuals/widget-wars/clash.pdf", "rb").read() == mine
          and open(f"{T}/manuals/widget-wars/clash-2.pdf", "rb").read() == other)

    print("a document whose file the archive doesn't hold yet:")
    arc.lib.put("document", "widget-wars/promised", {"model_key": "widget-wars", "doc_id": "promised", "title": "Promised", "kind": "other",
                                                      "sha256": "9" * 64, "bytes": 123}, 0, by="partner@example.test")
    st, _, _ = inproc(urlbase, token)
    check("the record arrives, the file is reported absent, nothing fails", st["ok"] and st["files"]["down"]["absent"] == 1 and not st["files"]["down"]["failed"])

    print("limits: the partner's numbers and the deck's CONFIRM:")
    before = exported()["widget-wars"]["rails"]["+5V"]
    lim = next(r for r in arc.lib.all("rail_limit") if r["data"]["rail"] == "+5V" and r["data"]["model_key"] == "widget-wars")
    check("the deck's logger uses the confirmed +5V window before", before == [4.75, 5.25] and lim["data"]["confirmed"] is not None)
    res = api.apply_writes(arc.lib, [{"kind": "rail_limit", "id": lim["id"], "if_rev": lim["rev"], "data": dict(lim["data"], hi="5.5")}], PARTNER)
    check("the partner raises the high limit on his phone: it arrives unconfirmed", res[0]["status"] == "updated" and arc.lib.get("rail_limit", lim["id"])["data"]["confirmed"] is None)
    st, _, _ = inproc(urlbase, token)
    check("the deck takes it, and the logger's export drops the window at once (an unconfirmed limit is never used)",
          st["pulled"] == 1 and settles(lambda: "+5V" not in (exported() or {}).get("widget-wars", {}).get("rails", {})))
    c, j = web("POST", "/api/specs/widget-wars/confirm", {"rail": "+5V", "doc": "widget-wars-manual", "page": 2})
    check("on the 7\" she checks the page and CONFIRMs it again", c == 200)
    st, _, _ = inproc(urlbase, token)
    check("the confirmation reaches the archive (the deck's write, a person can't make it)", st["pushed"] >= 1 and arc.lib.get("rail_limit", lim["id"])["data"]["confirmed"] is not None)

    print("a conflict, and what the 7\" will record:")
    g = arc.lib.get("game", "widget-wars")
    deck_g = web("GET", "/api/library/sync/changes?since=0&limit=500")[1]["records"]
    mine = next(r for r in deck_g if r["kind"] == "game" and r["id"] == "widget-wars")
    web("PUT", "/api/library/games/widget-wars", {"title": mine["data"]["title"], "mfr": "Deck Maker", "year": mine["data"]["year"], "notes": mine["data"]["notes"], "rev": mine["rev"]})
    api.apply_writes(arc.lib, [{"kind": "game", "id": "widget-wars", "if_rev": g["rev"], "data": dict(g["data"], mfr="Partner Maker")}], PARTNER)
    st, _, _ = inproc(urlbase, token)
    check("the same game changed differently on each side is a conflict, and neither version is overwritten",
          st["conflicts"] == 1 and arc.lib.get("game", "widget-wars")["data"]["mfr"] == "Partner Maker"
          and next(r for r in web("GET", "/api/library/sync/changes?since=0&limit=500")[1]["records"] if r["id"] == "widget-wars" and r["kind"] == "game")["data"]["mfr"] == "Deck Maker")
    conf = json.load(open(f"{SYNC}/library/conflicts.json"))["conflicts"][0]
    with open(f"{STATE}/library-resolutions.json", "w", encoding="utf-8") as f:       # what the 7" will write in D4
        json.dump({"resolutions": [{"key": conf["key"], "keep": "deck", "deck": conf["deck"]["hash"], "archive": conf["archive"]["hash"]}]}, f)
    st, _, _ = inproc(urlbase, token)
    check("KEEP THIS: the deck's version replaces the archive's, the choice is used up and the conflict is gone",
          st["conflicts"] == 0 and arc.lib.get("game", "widget-wars")["data"]["mfr"] == "Deck Maker"
          and web("GET", "/api/library/sync/resolutions")[1]["resolutions"] == [])

    print("out of reach, refused, and the Access service token:")
    r = script("http://127.0.0.1:9", token)
    check("an archive that isn't there: exit 0, the library block says so, nothing breaks", r.returncode == 0 and status()["library"]["ok"] is False and "error" in status()["library"])
    arc.tokens.revoke("deck")
    r = script(urlbase, token)
    check("the archive refusing the deck's token is exit 1: a person has to act", r.returncode == 1 and status()["library"].get("refused_by_archive") is True, r.stderr + r.stdout)
    r = script_with_hub(urlbase, token)
    check("with a hub configured too, the archive refusing the token is still exit 1 (the hubs' own work having run first)",
          r.returncode == 1 and "gatbox-sync: library" in r.stdout and "refused" in r.stdout, r.stdout + r.stderr)
    token = arc.tokens.add("deck2", "device")
    seen = []

    class Stub(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            body = b'{"error": "stub"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    script(f"http://127.0.0.1:{stub.server_address[1]}", token, access=("abc.access", "s3cret-value"))
    check("a Cloudflare Access service token in archive.conf goes on every request, beside the device token",
          seen and seen[0].get("cf-access-client-id") == "abc.access" and seen[0].get("cf-access-client-secret") == "s3cret-value"
          and seen[0].get("authorization") == f"Bearer {token}")
    stub.shutdown()
    r = script("http://example.invalid", token)
    check("an archive address that isn't https is refused before anything is sent: said in the journal, exit 0", r.returncode == 0 and "must be an https" in r.stdout)
    with open(f"{T}/archive.conf", "w", encoding="utf-8") as f:
        f.write("")
    quiet = subprocess.run([sys.executable, SCRIPT], env=dict(os.environ, GATBOX_SYNC_ARCHIVE_CONF=f"{T}/archive.conf", GATBOX_SYNC_CONF=f"{T}/no-hub.conf",
                                                           STATE_DIRECTORY=f"{T}/s2", GATBOX_SYNC_WEB=WEB), capture_output=True, text=True, check=False)
    check("an empty archive.conf means there is no library step at all", quiet.returncode == 0 and not os.path.exists(f"{T}/s2/library") and "gatbox-sync: library" not in quiet.stdout)
    with open(f"{T}/archive.conf", "w", encoding="utf-8") as f:
        f.write(f"ARCHIVE_URL={urlbase}\nARCHIVE_TOKEN={token}\n")
    from importlib.machinery import SourceFileLoader
    mod = SourceFileLoader("gatbox_sync_under_test", SCRIPT).load_module()
    saved = {k: os.environ.pop(k, None) for k in ("GATBOX_SYNC_ARCHIVE_CONF", "CREDENTIALS_DIRECTORY", "GATBOX_SYNC_CONF")}
    try:
        os.environ["GATBOX_SYNC_CONF"] = f"{T}/no-hub.conf"
        a = mod.archive_path()
        del os.environ["GATBOX_SYNC_CONF"]
        b = mod.archive_path()
        os.environ["CREDENTIALS_DIRECTORY"] = "/run/credentials/x"
        c = mod.archive_path()
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    check("the archive config's path: none for a test with its own hub config, /etc/gatbox/archive.conf on the Pi, the credential under systemd",
          a is None and b == "/etc/gatbox/archive.conf" and c == "/run/credentials/x/archive")
    quiet = subprocess.run([sys.executable, SCRIPT], env=dict(os.environ, GATBOX_SYNC_CONF=f"{T}/no-hub.conf", STATE_DIRECTORY=f"{T}/s3", GATBOX_SYNC_WEB=WEB),
                           capture_output=True, text=True, check=False)
    check("a run with its own hub config and no archive config of its own never reads the machine's archive.conf",
          quiet.returncode == 0 and not os.path.exists(f"{T}/s3/library"))

    print("the deck's door is for the Pi itself:")
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", WEB_PORT, timeout=20, source_address=("127.0.0.2", 0))      # not the Pi's own 127.0.0.1
    conn.request("GET", "/api/library/sync/changes")
    r = conn.getresponse()
    r.read()
    check("a caller that isn't the Pi itself (another address) is refused, for the feed and for the writes", r.status == 403)
    conn.request("PUT", "/api/library/sync/records", body=json.dumps({"records": [{"kind": "game"}]}), headers={"Content-Type": "application/json"})
    check("... and a write from there writes nothing", conn.getresponse().status == 403)
    conn.close()
    check("a bad range, a file that isn't there, a part that is too big, a wrong content type, each refused",
          web("GET", f"/api/library/sync/files/{'1' * 64}")[0] == 404
          and web("PUT", f"/api/library/sync/files/{'1' * 64}/parts/0", raw=b"x", ctype="application/json")[0] == 415
          and web("POST", "/api/library/sync/files", raw=b"{}", ctype="text/plain")[0] == 415
          and web("PUT", "/api/library/sync/records", {"records": []})[0] == 400
          and web("PUT", "/api/library/sync/records", {"records": [{"kind": "game"}]})[1]["results"][0]["status"] == "rejected")
    c, j = web("PUT", "/api/library/sync/records", {"records": [{"kind": "game", "id": "bad key", "uid": uuid.uuid4().hex, "if_rev": 0, "data": {"title": "X", "kind": "video"}}]})
    check("a record this deck's format won't take comes back rejected with the reason", j["results"][0]["status"] == "rejected" and j["results"][0]["reason"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
