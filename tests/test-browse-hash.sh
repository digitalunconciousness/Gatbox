#!/usr/bin/env bash
# The drive browser, COMPARE (a follow-up to F4c): hashing the files of one duplicate group out of their images: tests/test-browse-hash.py.
#   bash tests/test-browse-hash.sh
exec python3 "$(dirname "$0")/test-browse-hash.py" "$@"
