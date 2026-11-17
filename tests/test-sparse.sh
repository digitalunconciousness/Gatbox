#!/usr/bin/env bash
# Holes in stored disk images (backend/gatboxlib/sparse.py): zero blocks take no room, and no byte changes.
#   bash tests/test-sparse.sh
exec python3 "$(dirname "$0")/test-sparse.py" "$@"
