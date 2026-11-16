#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 6), F4d: COPY TO THE STORE, on invented images: tests/test-browse-copy.py.
#   bash tests/test-browse-copy.sh
exec python3 "$(dirname "$0")/test-browse-copy.py" "$@"
