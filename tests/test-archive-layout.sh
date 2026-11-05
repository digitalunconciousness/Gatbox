#!/usr/bin/env bash
# Phase D2: the archive's pages in a real browser (headless Chromium through tests/cdp.py): no sideways scroll at 195 (a
# phone at 200% zoom), 320, 390, 768 and 1280 px, controls of at least 44 px, no content-security-policy violation, a visible
# focus ring. Skips, saying so, without a Chromium. GATBOX_CHROME names one (as root it needs --no-sandbox: wrap it).
#   ARCHIVE_SHOTS=DIR bash tests/test-archive-layout.sh     also writes a PNG of every page at every width
#   ARCHIVE_FONTS_DIR=DIR                                    a folder with the two fonts, to prove they load
exec python3 "$(dirname "$0")/test-archive-layout.py" "$@"
