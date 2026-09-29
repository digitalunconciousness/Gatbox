"""GET /api/system: the Pi's health for the SYSTEM panel. Every field fails soft (null) on its own.

Sources, read off this Pi (2026-09-28, Pi 5, Trixie):
  * temperature: /sys/class/thermal/thermal_zone0/temp; fan: the pwmfan hwmon's fan1_input
  * throttling + EXT5V + the RTC cell: vcgencmd, which opens /dev/vcio_gencmd (root:video 0660; the unit gives
    gatbox-web the video group for this and nothing else). Under-voltage now also from the rpi_volt hwmon.
  * clock: NTP from timedatectl (as gatbox-raillog); the RTC checks mirror gatbox-raillog's rtc_trusted(), except
    the /dev/rtc0 test (root-only; sysfs has the same clock)
  * network: `ip -j addr`, nmcli for connections (the GATBOX hotspot is the NM profile gatbox-ap), iw for the SSID
Results are cached for CACHE_S so a panel polling it can't pile up forks.
"""
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


def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
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
    ntp = None
    out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if out is not None:
        ntp = out.strip() == "yes"
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
    for p in ("/", config.LOGDIR, config.SRV):
        try:
            u = shutil.disk_usage(p)
            out[p] = {"total": u.total, "free": u.free, "used_pct": round(100 * u.used / u.total, 1)}
        except OSError:
            out[p] = None                                    # /srv/gatbox arrives with the dumps (M7)
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
    conns = []
    out = _run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active"])
    for line in (out or "").splitlines():
        p = line.replace("\\:", "\0").split(":")
        if len(p) >= 3:
            conns.append({"name": p[0].replace("\0", ":"), "type": p[1], "device": p[2]})
    wifi = next((c for c in conns if c["type"] == "802-11-wireless"), None)
    ssid = None
    if wifi:
        link = _run(["iw", "dev", wifi["device"], "link"]) or ""
        ssid = next((ln.split(":", 1)[1].strip() for ln in link.splitlines() if ln.strip().startswith("SSID:")), None)
    ap = next((c for c in conns if c["name"] == AP_PROFILE), None)
    ap_addr = next((i["ipv4"][0] for i in ifs if ap and i["name"] == ap["device"] and i["ipv4"]), None)
    return {"interfaces": ifs, "connections": conns if out is not None else None,
            "wifi": {"device": wifi["device"], "connection": wifi["name"], "ssid": ssid} if wifi else None,
            "hotspot": {"active": bool(ap), "profile": AP_PROFILE, "address": ap_addr}}


def kiosk():
    """The 7" kiosk: is its Chromium running (its own profile dir), and the last EXIT / SHUT DOWN requests."""
    running = False
    for d in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(d, "rb") as f:
                c = f.read()
        except OSError:
            continue
        if b"--kiosk" in c and b"gatbox-kiosk" in c:
            running = True
            break

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


def logger():
    out = _run(["systemctl", "is-active", "gatbox-raillog.service"])
    return {"service": (out or "").strip() or None, "stopped": os.path.exists(os.path.join(config.CTRL, "stopped")),
            "logging": LIVE.logging(), "file": LIVE.name, "age_s": LIVE.age()}


def snapshot():
    with _lock:
        if _cache[1] and time.monotonic() - _cache[0] < CACHE_S:
            return _cache[1]
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
            "disk": disk(), "network": network(), "clock": clock(), "kiosk": kiosk(),
            "logger": logger(), "versions": versions(),
        }
        _cache[:] = [time.monotonic(), out]
        return out
