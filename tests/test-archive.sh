#!/usr/bin/env bash
# Phase D1: the archive server (archive/), the real one on spare ports over a temp data directory: tokens and who may
# do what on each listener, compare-and-set writes, the change feed and a reader's models= filter, the digest, resumable
# files, and what a person on a phone can't change. tests/test-archive.py.
#   bash tests/test-archive.sh
exec python3 "$(dirname "$0")/test-archive.py" "$@"
