#!/usr/bin/env bash
# READ LIMITS: rail limits read off a manual (gatboxlib/railread.py) and the manuals job's read_limits (tests/test-railread.py).
#   bash tests/test-railread.sh
exec python3 "$(dirname "$0")/test-railread.py" "$@"
