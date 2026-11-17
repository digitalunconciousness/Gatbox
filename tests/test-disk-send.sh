#!/usr/bin/env bash
# gatbox-disk-send: the store's disk images to the archive over the home network only (tests/test-disk-send.py).
#   bash tests/test-disk-send.sh
exec python3 "$(dirname "$0")/test-disk-send.py" "$@"
