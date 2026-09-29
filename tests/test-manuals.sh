#!/usr/bin/env bash
# Manuals and the spec sheet: tests/test-manuals.py (gatbox-manuals fetch, pages, search, uploads, CONFIRM, actuals).
#   bash tests/test-manuals.sh
exec python3 "$(dirname "$0")/test-manuals.py" "$@"
