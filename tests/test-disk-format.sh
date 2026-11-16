#!/usr/bin/env bash
# FORMAT (docs/plans/2026-11-16-restore-and-format.md): tests/test-disk-format.py, on plain files standing in for drives.
#   bash tests/test-disk-format.sh
exec python3 "$(dirname "$0")/test-disk-format.py" "$@"
