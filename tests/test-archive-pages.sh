#!/usr/bin/env bash
# Phase D2: the archive's pages (archive/gatboxarchive/pages.py), the real server driven the way a phone does it: who sees a page, the games list
# and search, a game, add and edit with their conflicts, rail limits and facts (a limit stays unconfirmed), PDF upload and the
# page viewer, CSRF, escaping. tests/test-archive-pages.py.
#   bash tests/test-archive-pages.sh
exec python3 "$(dirname "$0")/test-archive-pages.py" "$@"
