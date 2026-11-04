"""The archive's HTTP server (stdlib): the library protocol and the file calls, behind tokens.

Two listeners, because the archive can't verify Cloudflare's signed Access token itself (stdlib only: no crypto library):

  tunnel   bound to localhost, where `cloudflared` delivers what has already passed Cloudflare Access. It takes a device
           token (the deck), a reader token, or -- no token -- a person whose email Access names in the JWT it sends in
           `Cf-Access-Jwt-Assertion` (her partner, on his phone). The JWT's `email` claim is read, not verified (no crypto
           library, and no calling out for Cloudflare's keys), and only here: nothing but cloudflared can reach this port,
           and Cloudflare's own docs say an origin behind a tunnel needn't validate the token. That holds only while every
           route of the tunnel that reaches this port is covered by the Access application (RUNBOOK).
  private  optional, on the node's private address, for the apps' pull jobs. It takes reader tokens and nothing else, so
           there is no identity to forge, and what it serves is read-only.

What each may do:   changes / digest   files (read)               records (write)   files (upload)
    device          yes                everything                 every kind        yes
    person          yes                everything                 five kinds *      yes
    reader          yes (models=)      manuals only, no ROMs      no                no
    * game, document, mame, rail_limit (never confirmed or unconfirmed), fact: see api.py.

It never calls out, and it keeps no record of what a reader asked for: the log has the method, the path without its query,
and the status, and no token.
"""
import base64
import json
import os
import re
import secrets
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from gatboxlib import library as L
from gatboxlib.filestore import FileError, FileStore

from . import api
from .tokens import TokenStore

DEFAULT_MAX_FILE = 400 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
PART_BODY = 8 * 1024 * 1024
IDENTITY = "Cf-Access-Jwt-Assertion"          # Cloudflare's documented header; a bare email in another header works too
SHA = r"[0-9a-f]{64}"


def email_from(value):
    """The email in an Access identity: the `email` claim of the JWT (payload read, signature not checked: see the module
    docstring), or a bare address when the configured header carries one. "" when there is none: a service token's JWT has
    no email, and a malformed value is no identity."""
    value = (value or "").strip()
    if value.count(".") == 2 and "@" not in value:
        part = value.split(".")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (ValueError, UnicodeDecodeError):
            return ""
        value = claims.get("email") if isinstance(claims, dict) else ""
    if not isinstance(value, str) or not 3 <= len(value) <= 120 or "@" not in value or any(c.isspace() for c in value):
        return ""
    return value.lower()


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.data = env.get("ARCHIVE_DATA", "/srv/archive")
        self.tunnel = self._bind(env.get("ARCHIVE_TUNNEL_BIND", "127.0.0.1:8080"))
        self.private = self._bind(env.get("ARCHIVE_PRIVATE_BIND", "")) or None
        self.identity = env.get("ARCHIVE_IDENTITY_HEADER", IDENTITY)
        people = [p.strip().lower() for p in env.get("ARCHIVE_PEOPLE", "").split(",") if p.strip()]
        self.people = set(people) or None
        self.max_file = int(env.get("ARCHIVE_MAX_FILE", DEFAULT_MAX_FILE))
        self.static = env.get("ARCHIVE_STATIC", "/usr/local/share/gatbox-archive/static")

    @staticmethod
    def _bind(text):
        if not text:
            return None
        host, _, port = text.rpartition(":")
        return (host or "127.0.0.1", int(port))


class Archive:
    """What every request shares: the library, the files and the tokens, all under one data directory."""

    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg.data, exist_ok=True)
        self.lib = L.Library(os.path.join(cfg.data, "library.sqlite"))
        self.files = FileStore(os.path.join(cfg.data, "files"), cfg.max_file)
        self.tokens = TokenStore(os.path.join(cfg.data, "tokens.json"))
        self.ui_secret = self._secret(os.path.join(cfg.data, "ui-secret"))

    @staticmethod
    def _secret(path):
        """The key the pages' forms are signed with, made once and kept (mode 600) beside the tokens."""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            value = secrets.token_hex(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
            return value


class Refuse(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Handler(BaseHTTPRequestHandler):
    server_version = "gatbox-archive"
    protocol_version = "HTTP/1.1"
    archive = None
    listener = "tunnel"
    ROUTES = []

    # --- plumbing ---------------------------------------------------------------------------------------------------
    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        # The method, the path with no query, and the status. Never the query (a reader's `models=` is its machines) or a token.
        sys.stderr.write(f"{self.command} {urlsplit(self.path).path} {code}\n")

    def send_bytes(self, code, body, ctype, extra=()):
        self.send_response(code)
        if self.close_connection:
            self.send_header("Connection", "close")
        headers = {"Cache-Control": "no-store", **dict(extra)}        # a caller's own Cache-Control replaces the default
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, code, obj, extra=()):
        self.send_bytes(code, api.dumps(obj), "application/json; charset=utf-8", extra)

    PAGE_CSP = ("default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self' data:; script-src 'self'; "
                "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

    def html(self, code, text, extra=()):
        self.send_bytes(code, text.encode("utf-8"), "text/html; charset=utf-8",
                        (("Content-Security-Policy", self.PAGE_CSP), ("Referrer-Policy", "no-referrer"), *extra))

    def redirect(self, where):
        self.send_bytes(303, b"", "text/plain", (("Location", where),))

    def person(self):
        """The person on the other end of the tunnel, from the Access identity: the pages are for people only, and the
        private listener trusts no identity, so no page is served there."""
        email = self.identity()
        if email:
            return email[:60]
        raise Refuse(401, "sign in through Cloudflare Access")

    def identity(self):
        """The signed-in person's email, if this is the tunnel listener and they are on the list (when there is one)."""
        if self.listener != "tunnel":
            return ""
        email = email_from(self.headers.get(self.archive.cfg.identity))
        return email if email and (self.archive.cfg.people is None or email in self.archive.cfg.people) else ""

    def read_body(self, limit):
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise Refuse(411, "Content-Length is required") from None
        if n < 0 or n > limit:
            raise Refuse(413, f"the body is over {limit} bytes")      # not read: route() closes the connection
        self._read = True
        return self.rfile.read(n)

    def json_body(self):
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise Refuse(415, "send application/json")
        try:
            return json.loads(self.read_body(MAX_JSON))
        except (ValueError, UnicodeDecodeError):
            raise Refuse(400, "the body must be JSON") from None

    def refused(self, code, message):
        """An error answer. A request body we didn't read would be parsed as the next request on this connection, so
        when one is left, the connection is closed with the answer."""
        try:
            unread = int(self.headers.get("Content-Length") or 0) > 0 and not getattr(self, "_read", False)
        except ValueError:
            unread = True
        if unread:
            self.close_connection = True
        if self.path.startswith("/api/") or "application/json" in (self.headers.get("Accept") or ""):
            self.json(code, {"error": message})
        else:
            from . import pages  # a person gets a page, not JSON
            self.html(code, pages.error_page(code, message))

    def route(self, method):
        self._read = False
        url = urlsplit(self.path)
        known = False
        for m, rx, fn in self.ROUTES:
            hit = rx.fullmatch(url.path)
            if not hit:
                continue
            known = True
            if m != method:
                continue
            try:
                return fn(self, hit, parse_qs(url.query))
            except (Refuse, FileError, api.ApiError) as e:
                return self.refused(e.code, str(e))
            except Exception:                                      # a bug: logged without the query, the person told plainly
                traceback.print_exc()
                self.close_connection = True
                return self.refused(500, "The archive hit an error and logged it. Try again in a moment.")
        self.refused(405 if known else 404, "wrong method" if known else "not found")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    # --- who is asking ----------------------------------------------------------------------------------------------
    def who(self):
        """{"kind": device|reader|person, "name"}, or a 401 that says no more than that."""
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            row = self.archive.tokens.verify(auth[7:].strip())
            if row and (self.listener == "tunnel" or row["role"] == "reader"):
                return {"kind": row["role"], "name": row["name"]}
        elif self.identity():
            return {"kind": "person", "name": self.identity()[:60]}
        raise Refuse(401, "a valid token is required")

    def need(self, who, *kinds):
        if who["kind"] not in kinds:
            raise Refuse(403, f"a {who['kind']} may not do that")
        return who


def route(method, pattern):
    def add(fn):
        Handler.ROUTES.append((method, re.compile(pattern), fn))
        return fn
    return add


# --- the calls ----------------------------------------------------------------------------------------------------------

@route("GET", r"/api/v1/health")
def health(h, m, q):
    h.json(200, {"ok": True, "app": "gatbox-archive", "contract": "library-v1", "capabilities": ["library", "files"]})


@route("GET", r"/api/v1/library/changes")
def changes(h, m, q):
    h.who()
    since = api.int_param(q.get("since", [None])[0], "since", 0, 0, 2 ** 62)
    limit = api.int_param(q.get("limit", [None])[0], "limit", 500, 1, 500)
    h.json(200, api.changes(h.archive.lib, since, limit, api.models_param(q.get("models", [None])[0])))


@route("GET", r"/api/v1/library/digest")
def digest(h, m, q):
    h.who()
    lib = h.archive.lib
    h.json(200, lib.content_digest() if (q.get("content") or [""])[0] == "1" else lib.digest())


@route("PUT", r"/api/v1/library/records")
def records(h, m, q):
    who = h.need(h.who(), "device", "person")
    body = h.json_body()
    if not isinstance(body, dict):
        raise Refuse(400, "the body must be an object with a records list")
    h.json(200, {"results": api.apply_writes(h.archive.lib, body.get("records"), who)})


@route("POST", r"/api/v1/files")
def files_begin(h, m, q):
    h.need(h.who(), "device", "person")
    body = h.json_body()
    if not isinstance(body, dict):
        raise Refuse(400, "the body must be an object")
    h.json(200, h.archive.files.begin(body.get("sha256"), body.get("size")))


@route("PUT", rf"/api/v1/files/({SHA})/parts/(\d{{1,6}})")
def files_part(h, m, q):
    h.need(h.who(), "device", "person")
    if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
        raise Refuse(415, "send application/octet-stream")
    h.json(200, h.archive.files.put_part(m.group(1), int(m.group(2)), h.read_body(PART_BODY)))


@route("POST", rf"/api/v1/files/({SHA})/finish")
def files_finish(h, m, q):
    h.need(h.who(), "device", "person")
    h.json(200, h.archive.files.finish(m.group(1)))


def _range(header, size):
    """(start, end) of a single `bytes=` range, inclusive, or None for the whole file; raises Refuse(416)."""
    if not header:
        return None
    hit = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not hit or (hit.group(1) == "" and hit.group(2) == ""):
        raise Refuse(416, "one range, bytes=a-b")
    first, last = hit.groups()
    if first == "":
        n = int(last)
        start, end = max(size - n, 0), size - 1
    else:
        start, end = int(first), int(last) if last else size - 1
    if start >= size or end < start:
        raise Refuse(416, "that range is outside the file")
    return start, min(end, size - 1)


@route("GET", rf"/api/v1/files/({SHA})")
def files_read(h, m, q):
    who, sha = h.who(), m.group(1)
    size = h.archive.files.size(sha)
    if size is None or (who["kind"] == "reader" and sha not in api.document_hashes(h.archive.lib)):
        raise Refuse(404, "no such file")           # a reader is told the same for a ROM image as for nothing
    rng = _range(h.headers.get("Range"), size)
    start, end = rng or (0, size - 1)
    path = h.archive.files.path(sha)
    with open(path, "rb") as f:
        sniff = f.read(5)
        ctype = "application/pdf" if sniff == b"%PDF-" else "application/octet-stream"
        h.send_response(206 if rng else 200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(end - start + 1))
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Cache-Control", "private, no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("ETag", f'"{sha}"')
        if rng:
            h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        h.end_headers()
        f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = f.read(min(1 << 20, left))
            if not chunk:
                break
            h.wfile.write(chunk)
            left -= len(chunk)


from . import pages  # noqa: F401  (registers the pages' routes)

# --- running it -------------------------------------------------------------------------------------------------------------

def make_servers(archive):
    """A ThreadingHTTPServer per listener: the tunnel one, and the private one when it's configured."""
    out = []
    for name, bind in (("tunnel", archive.cfg.tunnel), ("private", archive.cfg.private)):
        if bind:
            handler = type(f"{name.title()}Handler", (Handler,), {"archive": archive, "listener": name})
            srv = ThreadingHTTPServer(bind, handler)
            srv.daemon_threads = True
            out.append(srv)
    return out


def serve(archive):
    servers = make_servers(archive)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    host, port = servers[0].server_address[:2]
    sys.stderr.write(f"gatbox-archive: data {archive.cfg.data}, tunnel listener {host}:{port}"
                     + (f", private listener {servers[1].server_address[0]}:{servers[1].server_address[1]}" if len(servers) > 1 else "")
                     + "\n")
    servers[0].serve_forever()
