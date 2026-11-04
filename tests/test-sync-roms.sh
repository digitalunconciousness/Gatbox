#!/usr/bin/env bash
# Phase C: gatbox-sync sends dumps and burns (their images first, in parts, then the items) to the hub whose roster
# has the machine. Stub gatbox-web and stub hubs, no network: tests/test-sync-roms.py.
#   bash tests/test-sync-roms.sh
exec python3 "$(dirname "$0")/test-sync-roms.py" "$@"
