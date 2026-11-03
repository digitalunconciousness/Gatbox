#!/usr/bin/env bash
# Phase C: gatbox-web's loopback-only reads for the sync (/api/sync/dumps, /api/sync/burns, /api/sync/image):
# tests/test-romsync.py, a real gatbox-web on a spare port over an invented dump archive.
#   bash tests/test-romsync.sh
exec python3 "$(dirname "$0")/test-romsync.py" "$@"
