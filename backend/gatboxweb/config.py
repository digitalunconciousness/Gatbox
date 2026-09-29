"""Paths. Each has an environment override: the systemd unit sets the state and cache dirs, the tests set the rest."""
import os
import re

LOGDIR = os.environ.get("GATBOX_LOGDIR", "/var/log/gatbox")
RUNDIR = os.environ.get("GATBOX_RUNDIR", "/run/gatbox")                  # gatbox-raillog's current, mode, marks-seen
CACHE = os.environ.get("CACHE_DIRECTORY", "/tmp/gatbox-web")
CTRL = os.environ.get("STATE_DIRECTORY", "/var/lib/gatbox-web")          # flags, profile, machine, marks, captures
PORT = int(os.environ.get("GATBOX_WEB_PORT", "80"))
REPORT = os.environ.get("GATBOX_REPORT", "/usr/local/bin/gatbox-rail-report")
FONTS = os.environ.get("GATBOX_WEB_FONTS", "/usr/local/share/gatbox-web/fonts")   # fetched by gatbox-bootstrap.sh
SYSSTATE = os.environ.get("GATBOX_SYSSTATE", "/var/lib/gatbox")          # root's: the RTC stamp
SRV = os.environ.get("GATBOX_SRV", "/srv/gatbox")                        # dumps (M7)

FONT_FILES = {"ChakraPetch-SemiBold.ttf", "ShareTechMono-Regular.ttf"}
NAME = re.compile(r"^rail_\d{8}_\d{6}(_\d{1,3})?\.csv$")   # _2, _3…: several files in one second (dial turns)
LIVE_S = 10          # a session whose last sample is newer than this is "logging now"
HEARTBEAT_S = float(os.environ.get("GATBOX_WEB_HEARTBEAT", "15"))   # SSE heartbeat (tests: shorter)

# gatboxlib reads the writable state from $GATBOX_STATE: here it must be the state this server writes
os.environ["GATBOX_STATE"] = CTRL


def log_path(name):
    """A session file's path, or None: only names gatbox-raillog writes, only in LOGDIR (never a path from a request)."""
    if not isinstance(name, str) or not NAME.match(name):
        return None
    p = os.path.join(LOGDIR, name)
    return p if os.path.isfile(p) else None
