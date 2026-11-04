"""gatbox-archive: serve, and manage the tokens.

    gatbox-archive serve
    gatbox-archive token add NAME --role device|reader [--note TEXT]     prints the token ONCE
    gatbox-archive token list
    gatbox-archive token revoke NAME
    gatbox-archive status                                                what's in the library and the files

Settings come from the environment (the unit's EnvironmentFile, /etc/gatbox-archive/archive.conf): ARCHIVE_DATA,
ARCHIVE_TUNNEL_BIND, ARCHIVE_PRIVATE_BIND, ARCHIVE_IDENTITY_HEADER, ARCHIVE_PEOPLE, ARCHIVE_MAX_FILE.
"""
import argparse
import sys
import time

from .server import Archive, Config, serve
from .tokens import TokenError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gatbox-archive", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    tk = sub.add_parser("token")
    tsub = tk.add_subparsers(dest="action", required=True)
    add = tsub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--role", required=True, choices=("device", "reader"))
    add.add_argument("--note", default="")
    tsub.add_parser("list")
    rev = tsub.add_parser("revoke")
    rev.add_argument("name")
    args = ap.parse_args(argv)
    arc = Archive(Config())
    if args.cmd == "serve":
        serve(arc)
        return 0
    if args.cmd == "status":
        print(f"data   {arc.cfg.data}")
        print(f"library {arc.lib.counts()} · digest {arc.lib.digest()}")
        return 0
    try:
        if args.action == "add":
            token = arc.tokens.add(args.name, args.role, args.note)
            print(f"{args.role} token for {args.name}: copy it now, it is never shown again:\n\n  {token}\n")
        elif args.action == "list":
            for t in arc.tokens.list():
                when = time.strftime("%Y-%m-%d", time.gmtime(t["created"]))
                print(f"{t['name']:<20} {t['role']:<7} {t['id']}  made {when}{'  REVOKED' if t['revoked'] else ''}")
        else:
            print(f"revoked {arc.tokens.revoke(args.name)}")
    except TokenError as e:
        print(f"gatbox-archive: {e}", file=sys.stderr)
        return 2
    return 0
