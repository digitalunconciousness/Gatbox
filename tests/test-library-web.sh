#!/usr/bin/env bash
# gatbox-web and the logger's limits through the game library (phase B3): tests/test-library-web.py.
#   bash tests/test-library-web.sh
exec python3 "$(dirname "$0")/test-library-web.py" "$@"
