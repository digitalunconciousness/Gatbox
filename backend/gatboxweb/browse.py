"""The drive browser's door in gatbox-web (docs/plans/2026-11-15-drive-browser.md).

gatbox-web parses nothing and opens no image. It passes a request to gatbox-browse over a unix socket and returns the answer.
Every route is for the Pi's own screen only (server.py checks h.is_local() first): what is inside a drive is private, and
gatbox-web has never served bytes from an image. It still never does: a preview is a JPEG the service drew, or a piece of text.
"""
import http.client
import json
import os
import socket
from urllib.parse import urlencode

from . import config
from .meter import Bad

PARAMS = ("image", "vol", "dir", "sort", "order", "kind", "page", "inode", "size", "w", "begin",
          "q", "year", "min", "max", "id", "at", "in", "deleted")                                                      # all the service reads


class _Conn(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.unix = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.unix)
        self.sock = s


def call(route, query, timeout=300):
    """GET `route` on the service with the allowed parameters. Returns (status, content type, body bytes)."""
    path = config.BROWSE_SOCK
    if not os.path.exists(path):
        raise Bad(503, "the drive browser isn't installed yet")
    qs = urlencode({k: v for k, v in query.items() if k in PARAMS and v not in (None, "")})
    conn = _Conn(path, timeout)
    try:
        conn.request("GET", f"{route}?{qs}" if qs else route)
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type") or "application/json", r.read()
    except (OSError, http.client.HTTPException):
        raise Bad(503, "the drive browser isn't answering") from None
    finally:
        conn.close()


def post(route, body):
    """POST a small JSON body to the service (/index and /export take one). Returns (status, content type, body bytes)."""
    path = config.BROWSE_SOCK
    if not os.path.exists(path):
        raise Bad(503, "the drive browser isn't installed yet")
    data = json.dumps({k: body.get(k) for k in ("action", "image", "vol", "inode", "kind", "name", "size", "deleted") if isinstance(body, dict) and body.get(k) is not None}).encode()
    conn = _Conn(path, 60)
    try:
        conn.request("POST", route, body=data, headers={"Content-Type": "application/json", "Content-Length": str(len(data))})
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type") or "application/json", r.read()
    except (OSError, http.client.HTTPException):
        raise Bad(503, "the drive browser isn't answering") from None
    finally:
        conn.close()
