#!/usr/bin/env bash
# The drive browser draws a camera raw (a Nikon NEF) by the picture inside it: tests/test-browse-raw.py, on invented files.
#   bash tests/test-browse-raw.sh
exec python3 "$(dirname "$0")/test-browse-raw.py" "$@"
