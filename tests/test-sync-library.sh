#!/usr/bin/env bash
# Phase D3: the deck's library sync end to end: the real archive, the real gatbox-web on an invented seeded deck, and the real
# gatbox-sync between them (dry run, first run, a manual in each direction, limits, a conflict and its resolution, out of reach,
# a refused token, the Access service token). tests/test-sync-library.py. The merge's own cases are tests/test-libsync.sh.
exec python3 "$(dirname "$0")/test-sync-library.py" "$@"
