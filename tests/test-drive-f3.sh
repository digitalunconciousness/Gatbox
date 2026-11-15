#!/usr/bin/env bash
# The drive browser's F3 measurement script (docs/plans/2026-11-15-drive-browser-f3.py) against invented images: tests/test-drive-f3.py.
#   bash tests/test-drive-f3.sh
exec python3 "$(dirname "$0")/test-drive-f3.py" "$@"
