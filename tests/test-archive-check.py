#!/usr/bin/env python3
"""archive/check.sh against a stand-in for Cloudflare Access in front of the archive (see test-archive-check.sh).

The stand-in answers the way Access does: a request with nothing, or with a made-up identity header, is sent to the login
(302); the service token's id and secret go through to the archive; the archive answers /api/v1/health, and refuses a
page to anyone without an email (401), as the real one does. Then each fault the first real setup had is switched on, and
the script must say so and exit 1. Every address, token and secret here is invented.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "archive", "check.sh")
ID, SECRET = "0123456789abcdef0123456789abcdef.access", "not-a-real-secret-0123456789"
READER = "gbx_a1b2c3d4e5f6.not-a-real-reader-secret"
LOGIN = "https://team.cloudflareaccess.com/cdn-cgi/access/login/archive.example.test?kid=x"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


class Front(http.server.BaseHTTPRequestHandler):
    """Access in front of the archive. server.mode switches a fault on."""
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/plain", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def archive(self, path, person):
        """What the archive itself answers: health to anyone, a page only to a person."""
        if path == "/api/v1/health":
            return self.send(200, json.dumps({"ok": True, "app": "gatbox-archive", "contract": "library-v1"}).encode(),
                             "application/json")
        if path == "/":
            return self.send(200, b"<h1>Games</h1>", "text/html") if person else self.send(401, b"Not signed in")
        self.send(404, b"not found")

    def do_GET(self):
        mode, path = self.server.mode, self.path.split("?")[0]
        token = (self.headers.get("CF-Access-Client-Id") == ID and self.headers.get("CF-Access-Client-Secret") == SECRET
                 and mode != "token-refused")
        forged = bool(self.headers.get("Cf-Access-Jwt-Assertion"))
        if mode == "private":                                           # the apps' listener: reader tokens, no pages
            if path == "/api/v1/library/changes" and self.headers.get("Authorization") == f"Bearer {READER}":
                return self.send(200, b'{"records": [], "cursor": 0, "more": false, "head": 0}', "application/json")
            return self.send(401 if path != "/" or mode != "private-pages" else 200, b"a valid token is required")
        if mode == "private-pages":
            return self.send(200, b"<h1>Games</h1>", "text/html") if path == "/" else self.send(401, b"no")
        if mode == "bypass-root" and path == "/":                       # Access does not cover /
            return self.archive(path, person=forged)
        if mode == "open-health" and path == "/api/v1/health":
            return self.archive(path, person=False)
        if mode == "forged-accepted" and forged:                        # Access not in front: the header is believed
            return self.archive(path, person=True)
        if mode == "wrong-secret-ok" and self.headers.get("CF-Access-Client-Id") == ID:
            return self.archive(path, person=False)                     # any secret accepted
        if token:
            return self.archive(path, person=False)                     # a service token has no email
        if mode == "login-elsewhere":
            return self.send(302, extra=[("Location", "https://example.test/login")])
        self.send(302, extra=[("Location", LOGIN)])


def serve(mode):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Front)
    srv.mode = mode
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def run(args, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ARCHIVE_",))}
    e.update(env)
    r = subprocess.run(["bash", CHECK, *args], capture_output=True, text=True, env=e, timeout=120)
    return r.returncode, r.stdout + r.stderr


TOKEN = {"ARCHIVE_ACCESS_ID": ID, "ARCHIVE_ACCESS_SECRET": SECRET}

print("the good setup:")
srv, url = serve("good")
code, out = run([url], **TOKEN)
check("a stranger and the service token: every check passes, exit 0", code == 0 and "0 failed" in out and "FAIL" not in out, out)
check("... a stranger's / and health go to the login, a forged header too, the token reaches health and no page",
      all(s in out for s in ("/ is sent to Cloudflare's login", "/api/v1/health is sent to Cloudflare's login",
                             "made-up identity header", "reaches /api/v1/health", "gets no page", "wrong secret is turned away")))
check("the secret is never printed", SECRET not in out and ID not in out)
code, out = run([url])
check("with no token: the stranger's checks run, the token's are skipped, exit 0",
      code == 0 and "skip  no service token" in out and "FAIL" not in out, out)

conf = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
conf.write(f"# the deck's archive.conf\nARCHIVE_URL={url}\nARCHIVE_TOKEN=gbx_x.y\n  ARCHIVE_ACCESS_ID = {ID}\nARCHIVE_ACCESS_SECRET={SECRET}\r\n")
conf.close()
code, out = run(["--conf", conf.name])
check("--conf: the address and the token come from the deck's conf, and it all passes",
      code == 0 and "reaches /api/v1/health" in out and SECRET not in out, out)
srv.shutdown()

print("each way it went wrong:")
for mode, expect in (
        ("bypass-root", "/ is not behind Access"),
        ("open-health", "/api/v1/health is not behind Access"),
        ("forged-accepted", "a made-up identity header gets a page"),
        ("token-refused", "the service token is not accepted"),
        ("wrong-secret-ok", "a wrong secret reaches the archive"),
        ("login-elsewhere", "is not sent to Cloudflare's login")):
    srv, url = serve(mode)
    code, out = run([url], **TOKEN)
    check(f"{mode}: exit 1 and the script says what is wrong, without printing the secret",
          code == 1 and "FAIL" in out and expect in out and SECRET not in out and ID not in out, out)
    srv.shutdown()

print("an address that doesn't answer:")
code, out = run(["http://127.0.0.1:9"], **TOKEN)
check("connection refused is a FAIL with the reason, not a hang or a pass", code == 1 and "no answer" in out, out)

print("how it is run:")
check("no address: usage, exit 2", run([])[0] == 2)
code, out = run(["http://archive.example.test"])
check("a plain http address that isn't loopback: refused, exit 2", code == 2 and "https://" in out, out)
code, out = run(["https://archive.example.test/games"])
check("an address with a path: refused, exit 2", code == 2 and "no path" in out, out)
check("an unknown option: exit 2", run(["--nope", "https://x.example"])[0] == 2)
check("--conf with a file that isn't there: exit 2", run(["--conf", "/nonexistent/archive.conf"])[0] == 2)

print("the apps' private listener:")
srv, purl = serve("private")
code, out = run(["--private", purl], ARCHIVE_READER_TOKEN=READER)
check("a reader token reads the feed; no token and no page are refused: exit 0",
      code == 0 and "a reader token reads the change feed" in out and "no token: refused" in out and "no pages on it" in out, out)
code, out = run(["--private", purl], ARCHIVE_READER_TOKEN="gbx_a1b2c3d4e5f6.someone-elses")
check("a reader token that isn't accepted: exit 1", code == 1 and "reader token is not accepted" in out, out)
check("the reader token is never printed", READER not in out and "someone-elses" not in out)
code, out = run(["--private", purl])
check("no reader token: skipped, the other two checks still run, exit 0", code == 0 and "skip  no reader token" in out, out)
srv.shutdown()
srv, purl = serve("private-pages")
code, out = run(["--private", purl], ARCHIVE_READER_TOKEN=READER)
check("a private listener that serves a page: exit 1", code == 1 and "serves a page" in out, out)
srv.shutdown()

os.unlink(conf.name)
print(f"archive check: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
