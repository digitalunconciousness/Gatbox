#!/usr/bin/env bash
# gatbox-web's write gate (the owner, 2026-11-17): from the network only the phone's own changes are taken, never one sent by
# another site's page; every other change is the Pi's own screen's. And the headers every response carries.
#   bash tests/test-web-gate.sh
exec python3 "$(dirname "$0")/test-web-gate.py" "$@"
