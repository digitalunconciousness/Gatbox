#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 5), F4c: possible duplicates from the name index: tests/test-browse-duplicates.py.
#   bash tests/test-browse-duplicates.sh
exec python3 "$(dirname "$0")/test-browse-duplicates.py" "$@"
