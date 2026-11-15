#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser.md), F2: the name index and search, gatboxlib/browseindex.py and its routes, on invented images: tests/test-browse-index.py.
#   bash tests/test-browse-index.sh
exec python3 "$(dirname "$0")/test-browse-index.py" "$@"
