"""Tokens for the archive: a device token for the deck and a read-only reader token for each app.

The same shape as the hubs' device tokens (`gbx_<public_id>.<secret>`): the public id is stored in the clear and picks the
row, the secret is only ever kept as a scrypt hash, and a token is shown once, when it's made. A leaked token file doesn't
leak a working token. The file is mode 600 and written atomically; revoking is a flag, so a name can't be reused by mistake.

    device   the deck: reads everything, writes records, uploads files
    reader   an app's pull job: reads records and the manuals (PDFs) only, never ROM images, never writes
"""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time

PREFIX = "gbx_"
ROLES = ("device", "reader")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class TokenError(ValueError):
    pass


def _hash(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=salt, **_SCRYPT).hex()


class TokenStore:
    def __init__(self, path):
        self.path = path
        self._seen = None                       # (mtime_ns, rows)
        self._ok = {}                           # sha256(token) -> row id, until the file changes

    def _rows(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self._seen is None or self._seen[0] != st.st_mtime_ns:
            try:
                with open(self.path, encoding="utf-8") as f:
                    rows = json.load(f).get("tokens") or []
            except (OSError, ValueError):
                rows = []
            self._seen, self._ok = (st.st_mtime_ns, rows), {}
        return self._seen[1]

    def _save(self, rows):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)), prefix=".tokens-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"tokens": rows}, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        self._seen = None

    def add(self, name, role, note=""):
        """A new token, returned once. The name is how it's listed and revoked: 1-40 letters, digits, dashes."""
        if role not in ROLES:
            raise TokenError(f"role: one of {', '.join(ROLES)}")
        if not name or len(name) > 40 or not all(c.isalnum() or c in "-_" for c in name):
            raise TokenError("name: 1-40 letters, digits, '-' or '_'")
        rows = list(self._rows())
        if any(r["name"] == name for r in rows):
            raise TokenError(f"there is already a token named {name!r} (revoked ones keep their name)")
        public_id, secret, salt = secrets.token_hex(6), secrets.token_urlsafe(32), os.urandom(16)
        rows.append({"id": public_id, "name": name, "role": role, "note": note[:200], "salt": salt.hex(),
                     "hash": _hash(secret, salt), "created": round(time.time(), 3), "revoked": None})
        self._save(rows)
        return f"{PREFIX}{public_id}.{secret}"

    def verify(self, token):
        """{id, name, role} for a good token, else None. Constant-time on the hash; a good one is remembered until the
        file changes (scrypt is slow on purpose, and a sync makes several requests a minute)."""
        if not isinstance(token, str) or not token.startswith(PREFIX) or "." not in token or len(token) > 120:
            return None
        public_id, _, secret = token[len(PREFIX):].partition(".")
        rows = self._rows()
        key = hashlib.sha256(token.encode()).hexdigest()
        if key in self._ok:
            hit = next((r for r in rows if r["id"] == self._ok[key] and not r["revoked"]), None)
            return {"id": hit["id"], "name": hit["name"], "role": hit["role"]} if hit else None
        row = next((r for r in rows if r["id"] == public_id), None)
        if row is None or row["revoked"]:
            _hash(secret, b"x" * 16)                      # about the same time as a real check
            return None
        if not hmac.compare_digest(_hash(secret, bytes.fromhex(row["salt"])), row["hash"]):
            return None
        self._ok[key] = row["id"]
        return {"id": row["id"], "name": row["name"], "role": row["role"]}

    def list(self):
        return [{k: r[k] for k in ("id", "name", "role", "note", "created", "revoked")} for r in self._rows()]

    def revoke(self, name):
        rows = list(self._rows())
        row = next((r for r in rows if r["name"] == name or r["id"] == name), None)
        if row is None:
            raise TokenError(f"no token named {name!r}")
        if not row["revoked"]:
            row["revoked"] = round(time.time(), 3)
            self._save(rows)
        return row["name"]
