#!/usr/bin/env bash
# The drive browser (docs/plans/2026-11-15-drive-browser.md), F3: the text readers, gatboxlib/browsetext.py, on files built in the test: tests/test-browse-text.py.
#   bash tests/test-browse-text.sh
exec python3 "$(dirname "$0")/test-browse-text.py" "$@"
