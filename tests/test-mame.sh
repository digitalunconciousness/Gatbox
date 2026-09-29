#!/usr/bin/env bash
# Each machine's ROM chips from MAME: tests/test-mame.py (gatbox-mame-roms, /api/mame/<slug>, the real SegaSonic set).
#   bash tests/test-mame.sh
exec python3 "$(dirname "$0")/test-mame.py" "$@"
