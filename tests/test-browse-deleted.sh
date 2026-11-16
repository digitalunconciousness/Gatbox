#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser-f4.md, section 2), F4b: deleted files, on images where files really were deleted: tests/test-browse-deleted.py.
#   bash tests/test-browse-deleted.sh
exec python3 "$(dirname "$0")/test-browse-deleted.py" "$@"
