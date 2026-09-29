#!/usr/bin/env bash
# The M5 dashboard in headless Chromium over the replay harness: tests/test-dash.py (states, DOM checks, 1024x600
# screenshots). Screenshots are kept in $1 when given (look at them), else thrown away.
#   bash tests/test-dash.sh [SCREENSHOT_DIR]
exec python3 "$(dirname "$0")/test-dash.py" "$@"
