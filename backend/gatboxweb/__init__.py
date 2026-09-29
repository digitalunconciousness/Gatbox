"""gatbox-web, the one server on :80: the phone view (HTML) and the dashboard's JSON API.

    server.py    the handler and the routes table (every URL is listed there)
    config.py    paths and environment overrides
    phone.py     the phone view: /, /s/…, /png/…, /pdf/…, /csv/…, /font/…, POST /control, /kiosk/*
    sessions.py  the session files: names, quick summaries, the /api/rail/sessions list
    report.py    gatbox-rail-report runs (text, JSON, PNG; cached) and the PDF
    live.py      follows /run/gatbox/current: last reading, the live alarm, the SSE stream
    meter.py     /api/meter, the profile / machine / alarm switch state, marks, NEW
    captures.py  saved readings (SAVE READING) per machine
    roster.py    roster entries merged with platform, critical actions and machine spec; machines added on the Pi
    mame.py      each machine's ROM chips from MAME (built at install), ticked from the dump archive
    manuals.py   each machine's documents (pages as PNGs, search, uploads) and its spec sheet: CONFIRM, actuals
    system.py    temperature, throttling, EXT5V, disk, network, clock + RTC, kiosk, versions
    devices.py   the USB devices GATBOX knows (IDs read off this Pi), from sysfs
    dump.py      the DUMP flow: requests to gatbox-dump's spool, progress back, the archive's list

Stdlib only. Installed to /usr/local/lib/gatbox/gatboxweb next to gatboxlib; /usr/local/bin/gatbox-web starts it.
"""
