#!/usr/bin/env bash
# The MAME CHD check (docs/plans/2026-11-02-disk-imaging.md §7, Task 3): tests/test-disk-mame.py, with tests/fake-chdman.
#   bash tests/test-disk-mame.sh
exec python3 "$(dirname "$0")/test-disk-mame.py" "$@"
