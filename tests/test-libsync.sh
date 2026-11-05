#!/usr/bin/env bash
# Phase D3: the merge (backend/gatboxlib/libsync.py) over two real libraries: every combination of base, deck and archive, first
# contact, one side or both changed, edit against delete, a lost reply, an unreachable archive, a record that moves, a refused
# write, resolving a conflict, state thrown away, a rebuilt deck, an archive restored from a backup, a dry run. tests/test-libsync.py.
exec python3 "$(dirname "$0")/test-libsync.py" "$@"
