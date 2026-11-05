#!/usr/bin/env bash
# archive/check.sh against a stand-in for Cloudflare Access in front of the archive: the good setup, and each way the first
# real setup went wrong (a path Access doesn't cover, a made-up identity header believed, a service token not accepted,
# a host with its health open, a private listener serving pages). No network: the stand-in is on loopback.
#   bash tests/test-archive-check.sh
exec python3 "$(dirname "$0")/test-archive-check.py" "$@"
