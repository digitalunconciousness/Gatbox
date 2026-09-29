#!/usr/bin/env bash
# M7, the T48 dump: tests/test-dump.py (gatbox-dump against stand-ins for minipro and MAME).
#   bash tests/test-dump.sh
exec python3 "$(dirname "$0")/test-dump.py" "$@"
