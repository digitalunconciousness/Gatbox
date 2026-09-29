"""GET /api/system: the Pi's health for the SYSTEM panel. Every field fails soft (null) on its own.

Sources, read off this Pi (2026-09-28, Pi 5, Trixie):
  * temperature: /sys/class/thermal/thermal_zone0/temp; fan: the pwmfan hwmon's fan1_input
  * throttling + EXT5V + the RTC cell: vcgencmd, which opens /dev/vcio_gencmd (root:video 0660; the unit gives
    gatbox-web the video group for this and nothing else). Under-voltage now also from the rpi_volt hwmon.
  * clock: NTP synced = the kernel's clock status (adjtimex, read only) with timedatectl's own rule, max error
    under 16 s. gatbox-raillog asks timedatectl, but D-Bus isn't reachable from this sandboxed service (seen
    2026-09-28: nmcli and timedatectl both fail under DynamicUser, and never reach dbus-daemon). The RTC checks
    mirror gatbox-raillog's rtc_trusted(), except the /dev/rtc0 test (root-only; sysfs has the same clock)
  * network: `ip -j addr` and `iw dev` (netlink, no D-Bus). A wireless interface of type AP is the GATBOX hotspot
    (NetworkManager's gatbox-ap profile), type managed is a client connection with its SSID.
Results are cached for CACHE_S so a panel polling it can't pile up forks. A source that fails says why in "errors".
"""
import ctypes
import ctypes.util
import glob
import json
import os
import platform
import shutil
import subprocess
import threading
import time

from . import config
from .live import LIVE

CACHE_S = 2.0
RTC = os.environ.get("GATBOX_RTC_SYSFS", "/sys/class/rtc/rtc0")
RTC_STAMP = os.environ.get("GATBOX_RTC_STAMP", os.path.join(config.SYSSTATE, "rtc-synced"))
AP_PROFILE = "gatbox-ap"
# vcgencmd get_throttled bits (Raspberry Pi firmware docs)
THROTTLE_BITS = {0: "under-voltage", 1: "arm frequency capped", 2: "throttled", 3: "soft temperature limit"}
_lock = threading.Lock()
_cache = [0.0, None]
_versions = None
_errors = {}          # this snapshot's failures: command -> first line of what it said


def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        _errors[" ".join(cmd[:2])] = repr(e)
        return None
    if r.returncode != 0:
        _errors[" ".join(cmd[:2])] = ((r.stderr or r.stdout).strip().splitlines() or [f"exit {r.returncode}"])[0][:200]
        return None
    return r.stdout


def ntp_synced():
    """The kernel's clock status, as timedatectl's NTPSynchronized reads it: synced when the maximum error is under
    16 s (systemd ignores STA_UNSYNC). adjtimex with modes = 0 only reads, and needs no privilege."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        buf = ctypes.create_string_buffer(512)            # struct timex (~208 bytes on arm64), zeroed: modes = 0
        if libc.adjtimex(buf) < 0:
            _errors["adjtimex"] = os.strerror(ctypes.get_errno())
            return None
        # struct timex on 64-bit: unsigned modes (4) + pad (4), long offset, long freq, long maxerror (µs) at 24
        return ctypes.c_long.from_buffer(buf, 24).value < 16_000_000
    except (OSError, AttributeError) as e:
        _errors["adjtimex"] = repr(e)
        return None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _float(x, scale=1.0):
    try:
        return float(x) * scale
    except (TypeError, ValueError):
        return None


def _vc_volts(name):
    out = _run(["vcgencmd", "pmic_read_adc", name])         # "     EXT5V_V volt(24)=5.15766000V"
    if out and "=" in out:
        return _float(out.rsplit("=", 1)[1].strip().rstrip("V"))
    return None


def throttled():
    out = _run(["vcgencmd", "get_throttled"])              # "throttled=0x0"
    if not out or "=" not in out:
        return None
    try:
        raw = int(out.split("=", 1)[1], 16)
    except ValueError:
        return None
    return {"raw": hex(raw), "ok": raw == 0,
            "now": [v for b, v in THROTTLE_BITS.items() if raw & (1 << b)],
            "since_boot": [v for b, v in THROTTLE_BITS.items() if raw & (1 << (b + 16))]}


def hwmon(name):
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(os.path.join(d, "name")) == name:
            return d
    return None


def clock():
    ntp = ntp_synced()
    rtc = _float(_read(os.path.join(RTC, "since_epoch")))
    stamp = _read(RTC_STAMP)                                 # "<epoch> <iso>", written by gatbox-rtc-sync
    st_epoch, st_iso = None, None
    if stamp:
        p = stamp.split(None, 1)
        st_epoch, st_iso = _float(p[0]), (p[1] if len(p) > 1 else None)
    now = time.time()
    diff = round(rtc - now, 1) if rtc is not None else None
    trusted = (rtc is not None and st_epoch is not None and rtc >= st_epoch and abs(rtc - now) <= 5)
    chg = _read(os.path.join(RTC, "charging_voltage"))     # must stay 0: the fitted cell is not rechargeable
    charging = None if chg is None else ("off" if chg == "0" else f"ON ({int(chg) / 1e6:.2f} V)")
    source = "ntp" if ntp else ("rtc" if trusted else "unverified")
    return {"source": source, "ntp_synced": ntp,
            "rtc": {"present": rtc is not None, "rtc_minus_system_s": diff, "last_set_from_ntp": st_iso,
                    "trusted": trusted, "cell_v": _vc_volts("BATT_V"), "charging": charging}}


def disk():
    out = {}
    for label, p in (("system", "/"), ("logs", config.LOGDIR), ("dumps", config.SRV)):
        try:
            u = shutil.disk_usage(p)
            out[label] = {"path": p, "total": u.total, "free": u.free, "used_pct": round(100 * u.used / u.total, 1)}
        except OSError:
            out[label] = None                                # /srv/gatbox arrives with the dumps (M7)
    return out


def network():
    ifs = []
    out = _run(["ip", "-j", "addr", "show"])
    try:
        for i in json.loads(out or "[]"):
            if i.get("ifname") == "lo":
                continue
            ifs.append({"name": i.get("ifname"), "up": "UP" in (i.get("flags") or []),
                        "ipv4": [a["local"] for a in i.get("addr_info", []) if a.get("family") == "inet"],
                        "ipv6": [a["local"] for a in i.get("addr_info", [])
                                 if a.get("family") == "inet6" and a.get("scope") == "global"]})
    except (ValueError, TypeError, KeyError):
        pass
    wifi = []
    out = _run(["iw", "dev"])                                # "Interface wlan0 / ssid … / type managed|AP"
    cur = None
    for line in (out or "").splitlines():
        w = line.strip().split(None, 1)
        if not w:
            continue
        if w[0] == "Interface":
            cur = {"device": w[1] if len(w) > 1 else None, "type": None, "ssid": None}
            wifi.append(cur)
        elif cur and w[0] == "type" and len(w) > 1:
            cur["type"] = w[1]
        elif cur and w[0] == "ssid" and len(w) > 1:
            cur["ssid"] = w[1]
    for w in wifi:
        w["ipv4"] = next((i["ipv4"] for i in ifs if i["name"] == w["device"]), [])
    client = next((w for w in wifi if w["type"] == "managed" and w["ssid"]), None)
    ap = next((w for w in wifi if w["type"] == "AP"), None)
    return {"interfaces": ifs, "wifi": client,
            "hotspot": {"active": bool(ap), "profile": AP_PROFILE, "ssid": ap and ap["ssid"],
                        "address": (ap["ipv4"] or [None])[0] if ap else None}}


def cmdlines():
    """Every process's command line (NUL-separated bytes). /proc, not systemctl: no D-Bus in this sandbox."""
    out = []
    for d in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(d, "rb") as f:
                out.append(f.read())
        except OSError:
            continue
    return out


def kiosk(procs):
    """The 7" kiosk: is its Chromium running (its own profile dir), and the last EXIT / SHUT DOWN requests."""
    running = any(b"--kiosk" in c and b"gatbox-kiosk" in c for c in procs)

    def at(name):
        try:
            return int(_read(os.path.join(config.CTRL, name)) or 0)
        except ValueError:
            return 0
    return {"running": running, "exit_at": at("kiosk-exit"), "shutdown_at": at("kiosk-shutdown")}


def versions():
    global _versions
    if _versions is None:
        v = {"os": None, "kernel": platform.release(), "python": platform.python_version()}
        for line in (_read("/etc/os-release") or "").splitlines():
            if line.startswith("PRETTY_NAME="):
                v["os"] = line.split("=", 1)[1].strip('"')
        out = _run(["dpkg-query", "-W", "-f", "${Package} ${Version}\\n", "sigrok-cli", "mame"]) or ""
        for line in out.splitlines():
            p = line.split()
            if len(p) == 2:
                v[p[0]] = p[1]
        _versions = v
    return _versions


def logger(procs):
    running = any(c.split(b"\0")[1:2] == [b"/usr/local/bin/gatbox-raillog"] for c in procs)   # bash <script>
    return {"running": running, "stopped": os.path.exists(os.path.join(config.CTRL, "stopped")),
            "logging": LIVE.logging(), "file": LIVE.name, "age_s": LIVE.age()}


def snapshot():
    with _lock:
        if _cache[1] and time.monotonic() - _cache[0] < CACHE_S:
            return _cache[1]
        _errors.clear()
        procs = cmdlines()
        vm = hwmon("rpi_volt")
        fan = hwmon("pwmfan")
        up = _float(_read("/proc/uptime").split()[0]) if _read("/proc/uptime") else None
        out = {
            "time": {"epoch": round(time.time(), 3), "iso": time.strftime("%Y-%m-%dT%H:%M:%S")},
            "uptime_s": up, "load": list(os.getloadavg()),
            "temp_c": _float(_read("/sys/class/thermal/thermal_zone0/temp"), 1e-3),
            "fan_rpm": _float(_read(os.path.join(fan, "fan1_input"))) if fan else None,
            "throttled": throttled(),
            "undervoltage_now": (_read(os.path.join(vm, "in0_lcrit_alarm")) == "1") if vm else None,
            "ext5v_v": _vc_volts("EXT5V_V"),
            "disk": disk(), "network": network(), "clock": clock(), "kiosk": kiosk(procs),
            "logger": logger(procs), "versions": versions(),
        }
        out["errors"] = dict(_errors)
        _cache[:] = [time.monotonic(), out]
        return out
