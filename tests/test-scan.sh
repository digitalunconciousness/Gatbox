#!/usr/bin/env bash
# M6, the barcode scanner: tests/test-scan.py (decoder, gatbox-scand end to end, /api/scan, labels).
#   bash tests/test-scan.sh
exec python3 "$(dirname "$0")/test-scan.py" "$@"
