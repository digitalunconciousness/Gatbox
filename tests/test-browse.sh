#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser.md), F1: gatboxlib/browse.py, gatbox-browse and gatbox-web's door, on invented images: tests/test-browse.py.
#   bash tests/test-browse.sh
exec python3 "$(dirname "$0")/test-browse.py" "$@"
