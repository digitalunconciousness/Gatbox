#!/usr/bin/env bash
# The deck's disk images on the archive (archive/gatboxarchive/diskstore.py): home network only, resumable, checked, room kept.
#   bash tests/test-archive-disks.sh
exec python3 "$(dirname "$0")/test-archive-disks.py" "$@"
