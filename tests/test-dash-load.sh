#!/usr/bin/env bash
# The dashboard after a load that went wrong (no stylesheet, no script, a reload into gatbox-web's restart):
# tests/test-dash-load.py, headless Chromium against gatbox-web from the checkout. Temp dirs and a spare port only.
#   bash tests/test-dash-load.sh
exec python3 "$(dirname "$0")/test-dash-load.py" "$@"
