#!/usr/bin/env bash
# The drive browser's F0 script (docs/plans/2026-11-15-drive-browser-f0.py) against invented ext4, FAT32 and random images: tests/test-drive-f0.py.
#   bash tests/test-drive-f0.sh
exec python3 "$(dirname "$0")/test-drive-f0.py" "$@"
