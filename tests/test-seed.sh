#!/usr/bin/env bash
# The library's seed and imports (backend/gatboxlib/seed.py, tools/gatbox-library): tests/test-seed.py.
#   bash tests/test-seed.sh
exec python3 "$(dirname "$0")/test-seed.py" "$@"
