#!/usr/bin/env bash
# gatbox-sync against a stub hub and a stub gatbox-web, both stdlib http.server on spare
# ports. No tracker and no Flask here: the hub's own repository tests the contract: this
# tests the sync's behaviour -- URL fallback, the ledger, the session cap, what counts as
# eligible, and that a rejected item stays queued.
#
# The real end-to-end against the live tracker is gdd-integration/verify-sync.sh, because it
# needs both repositories and so belongs in neither.
#   bash tests/test-sync.sh
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"; kill %1 %2 2>/dev/null' EXIT
SYNC="$REPO/backend/gatbox-sync"
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass + 1)); echo "  ok    $1"; else fail=$((fail + 1)); echo "  FAIL  $1"; fi; }

PORT_WEB=18731
PORT_HUB=18732
PORT_DEAD=18733           # nothing listens here: the fallback case
# Assembled from parts rather than written as one literal: privacy-check.sh flags a
# credential-shaped assignment, and it is right to -- the fix is not to write one.
DEV_ID=a1b2c3d4e5f6
BEARER="gbx_$DEV_ID.not-a-real-value"

# ---------------------------------------------------------------------------
# the stubs
# ---------------------------------------------------------------------------
cat > "$T/stub.py" <<'PY'
"""A stub gatbox-web and a stub hub. Each records what it was asked for, so the test can
assert on traffic rather than only on the sync's own log."""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE, PORT, WORK = sys.argv[1], int(sys.argv[2]), sys.argv[3]

def sessions():
    return json.load(open(os.path.join(WORK, "sessions.json")))

def report(name):
    p = os.path.join(WORK, f"report-{name}.json")
    return json.load(open(p)) if os.path.exists(p) else {"error": "no such session"}

def samples(name):
    p = os.path.join(WORK, f"samples-{name}.json")
    return json.load(open(p)) if os.path.exists(p) else {"fields": [], "rows": []}

def note(line):
    with open(os.path.join(WORK, f"{ROLE}.log"), "a") as fh:
        fh.write(line + "\n")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        note(f"GET {self.path} ua={self.headers.get('User-Agent', '-')}")
        path = self.path.split("?")[0]
        if ROLE == "web":
            if path == "/api/rail/sessions":
                return self.reply(200, {"sessions": sessions()})
            if path.startswith("/api/rail/report/"):
                return self.reply(200, report(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/rail/samples/"):
                return self.reply(200, samples(path.rsplit("/", 1)[-1]))
            return self.reply(404, {"error": "not found"})
        # the hub
        if path == "/api/v1/health":
            if os.path.exists(os.path.join(WORK, "hub-down")):
                return self.reply(503, {"error": "down"})
            if os.path.exists(os.path.join(WORK, "hub-403")):
                # What a tunnel or WAF in front of the hub does; the application itself
                # cannot produce this for a route that takes no token.
                return self.reply(403, {"error": "Forbidden"})
            return self.reply(200, {"ok": True, "contract": "v1", "time": 0})
        if not self.headers.get("Authorization", "").startswith("Bearer gbx_"):
            return self.reply(401, {"error": "a valid device token is required"})
        if path == "/api/v1/roster":
            return self.reply(200, {"contract": "v1", "machines":
                                    [{"slug": "widget-wars", "name": "Widget Wars",
                                      "status": "Working", "location": "Floor"}]})
        if path.startswith("/api/v1/machines/"):
            return self.reply(200, {"contract": "v1", "machine": "widget-wars",
                                    "status": "open", "orders": []})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        note(f"POST {self.path} items={len(body.get('items') or [])} "
             f"readings={sum(len(i.get('readings') or []) for i in body.get('items') or [])} "
             f"ua={self.headers.get('User-Agent', '-')}")
        with open(os.path.join(WORK, "posted.json"), "a") as fh:
            fh.write(json.dumps(body) + "\n")
        if (not self.headers.get("Authorization", "").startswith("Bearer gbx_")
                or os.path.exists(os.path.join(WORK, "hub-401"))):
            return self.reply(401, {"error": "a valid device token is required"})
        if os.path.exists(os.path.join(WORK, "hub-500")):
            return self.reply(500, {"error": "the hub fell over"})
        # created the first time a uid is seen, duplicate after -- the real hub's behaviour,
        # which is what makes the ledger an optimisation rather than correctness.
        seen_path = os.path.join(WORK, "seen.json")
        seen = json.load(open(seen_path)) if os.path.exists(seen_path) else []
        reject = os.path.exists(os.path.join(WORK, "hub-rejects"))
        results = []
        for item in body.get("items") or []:
            uid = item.get("uid")
            if reject:
                results.append({"uid": uid, "kind": item.get("kind"),
                                "status": "rejected", "reason": "no machine with that slug"})
                continue
            state = "duplicate" if uid in seen else "created"
            if state == "created":
                seen.append(uid)
            results.append({"uid": uid, "kind": item.get("kind"), "status": state,
                            "readings": {"created": len(item.get("readings") or []) if state == "created" else 0,
                                         "duplicate": 0 if state == "created" else len(item.get("readings") or [])}})
        json.dump(seen, open(seen_path, "w"))
        self.reply(200, {"contract": "v1", "received": len(results), "results": results})

ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
PY

mkdir -p "$T/work" "$T/state"

# Three sessions: two finished and metered, one live, plus one with no machine and one whose
# report failed -- the last two must never be sent.
python3 - "$T/work" <<'PY'
import json, sys
w = sys.argv[1]
def sess(name, **kw):
    d = {"file": name, "live": False, "machine": "widget-wars", "error": None}
    d.update(kw); return d
json.dump([
    sess("rail_20260920_010000.csv"),
    sess("rail_20260921_010000.csv"),
    sess("rail_20260922_010000.csv", live=True),
    sess("rail_20260923_010000.csv", machine=None),
    sess("rail_20260924_010000.csv", error="gatbox-rail-report exited 1"),
], open(f"{w}/sessions.json", "w"))

for n, start in (("rail_20260920_010000.csv", 1790000000.0),
                 ("rail_20260921_010000.csv", 1790100000.0),
                 ("rail_20260922_010000.csv", 1790200000.0)):
    json.dump({
        "session": {"start": "2026-09-20T01:00:00", "end": "2026-09-20T01:00:02",
                    "duration_s": 2.0, "samples": 4, "rate": 2.0, "timebase": "monotonic"},
        "clock": {"source": "rtc", "note": None, "ntp_from": "2026-09-19T23:00:00"},
        "profile": {"id": "rail-5v", "label": "+5V rail", "kind": "rail", "rail": "+5V",
                    "from_header": True},
        "machine": "widget-wars", "mode_key": "VDC",
        "window": {"lo": 4.75, "hi": 5.25, "unit": "V", "source": "profile"},
        "alarm_hi": 5.775,
        "powered": {"mean": 5.01, "min": 5.0, "max": 5.02, "readings": 3,
                    "in_window_pct": 100.0},
        "verdict": {"state": "held", "title": "HELD THE WINDOW", "css": "ok",
                    "detail": "all session", "suspect": 0},
        "over_voltage": [], "excursions": [], "power_cycles": [{}], "gaps": [],
        "ol_events": [], "marks": [],
    }, open(f"{w}/report-{n}.json", "w"))
    json.dump({"fields": ["epoch", "up", "raw", "unit", "v", "mode", "alarm"],
               "rows": [[start + i / 2, 100.0 + i / 2, "5.010", "V", 5.01, "VDC", "ok"]
                        for i in range(3)]
                       + [[start + 1.5, 101.5, "inf", "TΩ", None, "VDC", None]]},
              open(f"{w}/samples-{n}.json", "w"))
PY

python3 "$T/stub.py" web "$PORT_WEB" "$T/work" & sleep 0.1
python3 "$T/stub.py" hub "$PORT_HUB" "$T/work" & sleep 0.6

run() {   # run [extra env assignments...]
    env GATBOX_SYNC_CONF="$T/hub.conf" GATBOX_SYNC_WEB="http://127.0.0.1:$PORT_WEB" \
        STATE_DIRECTORY="$T/state" "$@" python3 "$SYNC" >"$T/out" 2>&1; echo $? > "$T/rc"
}

echo "no config:"
printf '' > "$T/empty.conf"
env GATBOX_SYNC_CONF="$T/nope.conf" STATE_DIRECTORY="$T/state" python3 "$SYNC" >"$T/out" 2>&1
check "a missing hub.conf exits 0, not 1"      '[ "$?" = 0 ] && grep -q "nothing to do" "$T/out"'
env GATBOX_SYNC_CONF="$T/empty.conf" STATE_DIRECTORY="$T/state" python3 "$SYNC" >"$T/out" 2>&1
check "an empty hub.conf exits 0 too"          '[ "$?" = 0 ] && grep -q "both required" "$T/out"'

# The dead URL first: the fallback is the whole reason URLs are a list.
cat > "$T/hub.conf" <<CONF
HUB_URLS=http://127.0.0.1:$PORT_DEAD http://127.0.0.1:$PORT_HUB
HUB_TOKEN=$BEARER
CONF

echo "first run:"
run
check "exits 0"                                '[ "$(cat "$T/rc")" = 0 ]'
check "falls past the unreachable URL to the live one" 'grep -q "127.0.0.1:$PORT_DEAD: unreachable" "$T/out" && grep -q "127.0.0.1:$PORT_HUB: ok" "$T/out"'
check "sends the 2 finished sessions, not 5"   'grep -q "2 to send" "$T/out" && grep -q "posting 2 session(s)" "$T/out"'
check "2 created"                              'grep -q "2 created, 0 duplicate, 0 rejected" "$T/out"'
check "one POST, 8 readings"                   '[ "$(grep -c "^POST /api/v1/ingest" "$T/work/hub.log")" = 1 ] && grep -q "items=2 readings=8" "$T/work/hub.log"'
check "never asks gatbox-web for the live file" '! grep -q "20260922" "$T/work/web.log"'
check "nor for the one with no machine"         '! grep -q "20260923" "$T/work/web.log"'
check "nor for the one whose report failed"     '! grep -q "20260924" "$T/work/web.log"'

echo "what it says it is:"
# urllib's default is "Python-urllib/3.x", which generic bot protection blocks outright:
# Cloudflare in front of the hub answered 403 to exactly that and 200 to every other
# User-Agent, curl's included. Found on the bench, 2026-10-05.
check "never identifies as Python-urllib"      '! grep -q "Python-urllib" "$T/work/hub.log" "$T/work/web.log"'
check "names itself to the hub"                'grep -q "POST /api/v1/ingest .*ua=gatbox-sync/" "$T/work/hub.log"'
check "and to gatbox-web"                      'grep -q "ua=gatbox-sync/" "$T/work/web.log"'

echo "the payload:"
check "contract v1, device from the token"     '[ "$(python3 -c "
import json;d=json.loads(open(\"$T/work/posted.json\").readline())
print(d[\"contract\"], d[\"device\"])")" = "v1 a1b2c3d4e5f6" ]'
check "uids match the contract rule exactly"   'python3 -c "
import hashlib, json
d = json.loads(open(\"$T/work/posted.json\").readline())
for it in d[\"items\"]:
    fn = it[\"file\"]
    want = hashlib.sha256(f\"a1b2c3d4e5f6|rail_session|{fn}\".encode()).hexdigest()[:32]
    assert it[\"uid\"] == want, (it[\"uid\"], want)
    for r in it[\"readings\"]:
        ep = r[\"epoch\"]
        w = hashlib.sha256(f\"a1b2c3d4e5f6|reading|{fn}|{ep:.3f}\".encode()).hexdigest()[:32]
        assert r[\"uid\"] == w, (ep, r[\"uid\"], w)
"'
check "statistics are passed through, not recomputed" 'python3 -c "
import json
it = json.loads(open(\"$T/work/posted.json\").readline())[\"items\"][0]
assert it[\"powered\"][\"mean\"] == 5.01, it[\"powered\"]
assert it[\"window\"][\"lo\"] == 4.75
assert it[\"alarm_hi\"] == 5.775
assert it[\"verdict\"][\"state\"] == \"held\"
assert it[\"profile\"][\"rail\"] == \"+5V\"
assert it[\"counts\"][\"power_cycles\"] == 1
assert it[\"clock\"][\"source\"] == \"rtc\"
"'
check "over-range reading: ol true, v null, raw kept" 'python3 -c "
import json
rs = json.loads(open(\"$T/work/posted.json\").readline())[\"items\"][0][\"readings\"]
ol = [r for r in rs if r[\"ol\"]]
assert len(ol) == 1, len(ol)
assert ol[0][\"v\"] is None and ol[0][\"raw\"] == \"inf\", ol[0]
assert all(not r[\"ol\"] for r in rs if r[\"raw\"] != \"inf\")
"'
check "started/ended are epochs, not local ISO" 'python3 -c "
import json
it = json.loads(open(\"$T/work/posted.json\").readline())[\"items\"][0]
assert isinstance(it[\"started\"], float) and it[\"started\"] > 1_700_000_000, it[\"started\"]
assert it[\"ended\"] >= it[\"started\"]
"'

echo "second run — the ledger:"
run
check "nothing left to send"                   'grep -q "0 to send" "$T/out"'
check "posts nothing at all"                   '[ "$(grep -c "^POST" "$T/work/hub.log")" = 1 ]'
check "still refreshes the roster cache"       '[ -f "$T/state/roster.json" ] && python3 -c "
import json; d=json.load(open(\"$T/state/roster.json\"))
assert d[\"machines\"][0][\"slug\"] == \"widget-wars\", d"'
check "and the open-orders cache"              '[ -f "$T/state/orders.json" ]'

echo "the ledger is an optimisation, not correctness:"
rm -f "$T/state/sent.json"
run
check "losing it re-sends"                     'grep -q "2 to send" "$T/out"'
check "and the hub answers duplicate, so nothing is doubled" 'grep -q "0 created, 2 duplicate, 0 rejected" "$T/out"'
check "exits 0: a duplicate is not a failure"  '[ "$(cat "$T/rc")" = 0 ]'

echo "the hub unreachable -- the normal case for a box that travels:"
touch "$T/work/hub-down"
rm -f "$T/state/sent.json"
run
# systemd marks a oneshot failed on a non-zero exit. A timer that fails every two minutes
# whenever GATBOX is off its home network fills systemctl --failed with noise and hides the
# failures that matter, so this is deliberately a success with the detail in the journal.
check "exits 0: being off the network is not a fault" '[ "$(cat "$T/rc")" = 0 ]'
check "says how many are queued"               'grep -q "no hub reachable; 2 queued" "$T/out"'
check "records it for the SYSTEM tile"         'python3 -c "
import json; d=json.load(open(\"$T/state/status.json\"))
assert d[\"ok\"] is False and d[\"queued\"] == 2, d"'
rm -f "$T/work/hub-down"

echo "a hub that answers and refuses is not 'unreachable':"
touch "$T/work/hub-403"
rm -f "$T/state/sent.json"
run
check "says refused, with the status"          'grep -q "refused (HTTP 403" "$T/out"'
check "and points past the hub, since health takes no token" 'grep -q "something in front of the hub" "$T/out"'
check "still exits 0"                          '[ "$(cat "$T/rc")" = 0 ]'
rm -f "$T/work/hub-403"

echo "a rejected item stays queued:"
touch "$T/work/hub-rejects"
rm -f "$T/state/sent.json" "$T/state/status.json"
run
# A rejected item usually means a slug the hub does not know, which a person must fix -- but
# failing the unit every two minutes until they do is exactly the noise this avoids.
check "exits 0, with the reason in the journal" '[ "$(cat "$T/rc")" = 0 ] && grep -q "left queued" "$T/out"'
check "the reason is logged"                   'grep -q "rejected rail_20260920_010000.csv: no machine with that slug" "$T/out"'
check "the ledger does not record it"          '[ ! -f "$T/state/sent.json" ] || python3 -c "
import json; d=json.load(open(\"$T/state/sent.json\"))
assert \"rail_20260920_010000.csv\" not in d, d"'
rm -f "$T/work/hub-rejects"
run
check "so a later run sends it again"          'grep -q "2 to send" "$T/out"'

echo "the session cap:"
python3 - "$T/work" <<'PY'
import json, shutil, sys
w = sys.argv[1]
base = json.load(open(f"{w}/sessions.json"))
extra = [{"file": f"rail_202610{n:02d}_010000.csv", "live": False,
          "machine": "widget-wars", "error": None} for n in range(1, 16)]
for s in extra:
    shutil.copy(f"{w}/report-rail_20260920_010000.csv.json", f"{w}/report-{s['file']}.json")
    shutil.copy(f"{w}/samples-rail_20260920_010000.csv.json", f"{w}/samples-{s['file']}.json")
json.dump(base + extra, open(f"{w}/sessions.json", "w"))
PY
rm -f "$T/state/sent.json" "$T/work/hub.log"
run
check "17 eligible, 10 sent"                   'grep -q "17 to send" "$T/out" && grep -q "posting 10 session(s)" "$T/out"'
check "the cap is sessions, and still one POST" '[ "$(grep -c "^POST" "$T/work/hub.log")" = 1 ] && grep -q "items=10 " "$T/work/hub.log"'
check "GATBOX_SYNC_MAX overrides it"           'rm -f "$T/state/sent.json"; run GATBOX_SYNC_MAX=3; grep -q "posting 3 session(s)" "$T/out"'
# 17 eligible, 10 a run: the queue must actually empty rather than stall.
rm -f "$T/state/sent.json" "$T/work/hub.log"
check "a full run sends the cap"               'run; grep -q "posting 10 session(s)" "$T/out"'
check "the next run sends the remaining 7"     'run; grep -q "7 to send" "$T/out" && grep -q "posting 7 session(s)" "$T/out"'
check "and then the queue is empty"            'run; grep -q "0 to send" "$T/out" && [ "$(cat "$T/rc")" = 0 ]'
check "which took exactly 2 posts"             '[ "$(grep -c "^POST" "$T/work/hub.log")" = 2 ]'

echo "a dry run:"
rm -f "$T/state/sent.json" "$T/work/hub.log"
touch "$T/work/hub.log"
check "builds but posts nothing"               'run GATBOX_SYNC_DRYRUN=1; grep -q "dry run: posting nothing" "$T/out" && [ "$(grep -c "^POST" "$T/work/hub.log")" = 0 ]'

echo "the other half of the policy -- what SHOULD fail the unit:"
touch "$T/work/hub-401"
rm -f "$T/state/sent.json"
run
# A revoked or disabled token needs a person, and no amount of retrying helps. This is
# precisely what systemctl --failed should be reserved for.
check "a refused token exits 1"                '[ "$(cat "$T/rc")" = 1 ]'
check "and says so"                            'grep -q "ingest failed: HTTP 401" "$T/out"'
rm -f "$T/work/hub-401"
touch "$T/work/hub-500"
rm -f "$T/state/sent.json"
run
check "but the hub falling over exits 0"       '[ "$(cat "$T/rc")" = 0 ]'
check "because that clears on its own"         'grep -q "ingest failed: HTTP 500" "$T/out"'
rm -f "$T/work/hub-500"
check "nothing was recorded as sent either way" '[ ! -f "$T/state/sent.json" ] || [ "$(python3 -c "
import json; print(len(json.load(open(\"$T/state/sent.json\"))))")" = 0 ]'

echo "a bad token:"
MALFORMED=not-of-the-right-shape
cat > "$T/hub.conf" <<CONF
HUB_URLS=http://127.0.0.1:$PORT_HUB
HUB_TOKEN=$MALFORMED
CONF
run
check "exits 1 and says what is wrong"         '[ "$(cat "$T/rc")" = 1 ] && grep -q "not of the form" "$T/out"'

echo "sync: $pass passed, $fail failed"
[ "$fail" = 0 ]
