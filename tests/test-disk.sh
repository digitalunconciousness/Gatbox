#!/usr/bin/env bash
# Disk imaging (docs/plans/2026-11-02-disk-imaging.md): tests/test-disk.py (gatbox-disk against a fake sysfs, plain
# files standing in for devices, and tests/fake-ddrescue; no real block device is ever opened).
#   bash tests/test-disk.sh
exec python3 "$(dirname "$0")/test-disk.py" "$@"
