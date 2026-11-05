#!/usr/bin/env bash
# The game library's store and its format (backend/gatboxlib/library.py, docs/contract/library-v1): tests/test-library.py.
#   bash tests/test-library.sh
exec python3 "$(dirname "$0")/test-library.py" "$@"
