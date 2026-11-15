#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser.md), F3b: the content search, the text phase of gatboxlib/browseindex.py, on invented images: tests/test-browse-textindex.py.
#   bash tests/test-browse-textindex.sh
exec python3 "$(dirname "$0")/test-browse-textindex.py" "$@"
