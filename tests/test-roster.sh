#!/usr/bin/env bash
# Adding a machine on the Pi: tests/test-roster.py (POST/PUT /api/roster, the merge, /roster.json, labels).
#   bash tests/test-roster.sh
exec python3 "$(dirname "$0")/test-roster.py" "$@"
