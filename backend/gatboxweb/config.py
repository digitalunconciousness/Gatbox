"""Paths. Each has an environment override: the systemd unit sets the state and cache dirs, the tests set the rest."""
import os
import re

LOGDIR = os.environ.get("GATBOX_LOGDIR", "/var/log/gatbox")
RUNDIR = os.environ.get("GATBOX_RUNDIR", "/run/gatbox")                  # gatbox-raillog's current, mode, marks-seen
CACHE = os.environ.get("CACHE_DIRECTORY", "/tmp/gatbox-web")
CTRL = os.environ.get("STATE_DIRECTORY", "/var/lib/gatbox-web")          # flags, profile, machine, marks, captures
PORT = int(os.environ.get("GATBOX_WEB_PORT", "80"))
REPORT = os.environ.get("GATBOX_REPORT", "/usr/local/bin/gatbox-rail-report")
LABELS = os.environ.get("GATBOX_LABELS", "/usr/local/bin/gatbox-labels")    # the scanner's QR label sheet (M6)
FONTS = os.environ.get("GATBOX_WEB_FONTS", "/usr/local/share/gatbox-web/fonts")   # fetched by gatbox-bootstrap.sh
SYSSTATE = os.environ.get("GATBOX_SYSSTATE", "/var/lib/gatbox")          # root's: the RTC stamp
SRV = os.environ.get("GATBOX_SRV", "/srv/gatbox")                        # dumps (M7)
ROMS = os.environ.get("GATBOX_ROMS", os.path.join(SRV, "roms"))          # the dump archive: <machine>/<label>_<sha1:8>.bin
DUMP_SPOOL = os.environ.get("GATBOX_DUMP_SPOOL", "/var/spool/gatbox-dump")   # dump requests / progress (M7)
MINIPRO_PARTS = os.environ.get("GATBOX_MINIPRO_PARTS", "/usr/local/share/gatbox/minipro-parts-T48.txt")
SCAND_STATE = os.environ.get("GATBOX_SCAND_STATE", "/run/gatbox-scand/state.json")   # gatbox-scand's (M6)

FONT_FILES = {"ChakraPetch-SemiBold.ttf", "ShareTechMono-Regular.ttf"}


def _dash_dir():
    """The dashboard's static files: $GATBOX_WEB_DASH, else web/dash in a repo checkout, else the installed copy."""
    env = os.environ.get("GATBOX_WEB_DASH")
    if env:
        return env
    repo = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "web", "dash"))
    return repo if os.path.isfile(os.path.join(repo, "index.html")) else "/usr/local/share/gatbox-web/dash"


DASH = _dash_dir()
DASH_FILE = re.compile(r"^[a-z0-9-]+\.(js|css|svg)$")     # what /dash/<file> may serve
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
