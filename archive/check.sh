#!/usr/bin/env bash
# archive/check.sh: is the archive's public side what archive/RUNBOOK.md says it is? One command, from any machine,
# with nothing but curl. It changes nothing and prints no secret.
#
#   bash archive/check.sh https://archive.example.com
#   ARCHIVE_ACCESS_ID=... ARCHIVE_ACCESS_SECRET=... bash archive/check.sh https://archive.example.com
#   sudo bash archive/check.sh --conf /etc/gatbox/archive.conf     # on the deck: its address and service token
#   ARCHIVE_READER_TOKEN=gbx_... bash archive/check.sh --private http://<CONTAINER_IP>:8081   # the apps' listener
#
# Without credentials it checks what a stranger sees. With the service token (ARCHIVE_ACCESS_ID and
# ARCHIVE_ACCESS_SECRET, the same names as the deck's archive.conf) it checks what the deck sees as well. The two
# arguments can be combined. Exit 0 when every check passes, 1 when one fails, 2 for a mistake in how it was run.
#
# What it looks for, each a way the setup went wrong the first time (RUNBOOK step 4):
#   * a stranger's request for / or /api/v1/health must be sent to Cloudflare's login (302). A 401 from the archive itself
#     means Access did not cover that path, so the pages would believe any identity header a visitor made up.
#   * a made-up Cf-Access-Jwt-Assertion header, with no sign-in, must be sent to the login too: the archive reads the
#     email from it without checking a signature, so it is safe only while Access stands in front.
#   * the service token must reach /api/v1/health, and must not get a page (it has no email); a wrong secret is turned away.
#   * the private listener, if you name it, answers a reader token and no pages.
set -u

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

URL="" PRIVATE="" CONF=""
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --conf) CONF=${2:-}; [ -n "$CONF" ] || { echo "--conf needs a file" >&2; exit 2; }; shift 2 ;;
        --private) PRIVATE=${2:-}; [ -n "$PRIVATE" ] || { echo "--private needs an address" >&2; exit 2; }; shift 2 ;;
        -*) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
        *) [ -z "$URL" ] || { echo "one address only" >&2; exit 2; }; URL=$1; shift ;;
    esac
done

command -v curl >/dev/null 2>&1 || { echo "curl is needed" >&2; exit 2; }

# The deck's conf: KEY=value lines, no quoting, no code run (the same rules gatbox-sync reads it by).
if [ -n "$CONF" ]; then
    [ -r "$CONF" ] || { echo "can't read $CONF (it is mode 600: run with sudo)" >&2; exit 2; }
    conf() {
        awk -v k="$1" '{ l = $0; sub(/^[ \t]+/, "", l); if (l ~ /^#/) next; i = index(l, "="); if (!i) next
                         key = substr(l, 1, i - 1); gsub(/[ \t]+$/, "", key)
                         if (key == k) { v = substr(l, i + 1); sub(/^[ \t]+/, "", v); sub(/[ \t\r]+$/, "", v); print v; exit } }' "$CONF"
    }
    [ -n "$URL" ] || URL=$(conf ARCHIVE_URL)
    : "${ARCHIVE_ACCESS_ID:=$(conf ARCHIVE_ACCESS_ID)}" "${ARCHIVE_ACCESS_SECRET:=$(conf ARCHIVE_ACCESS_SECRET)}"
fi
ID=${ARCHIVE_ACCESS_ID:-} SECRET=${ARCHIVE_ACCESS_SECRET:-} READER=${ARCHIVE_READER_TOKEN:-}
[ -n "$URL$PRIVATE" ] || { usage >&2; exit 2; }

# https only, except for a loopback address (a test): nothing here should travel in the clear.
clean() {
    local u=${1%/}
    case "$u" in
        https://*/*|http://*/*) echo "$1 is not just an address (no path)" >&2; return 2 ;;
        https://?*) ;;
        http://127.0.0.1*|http://localhost*|http://\[::1\]*) ;;
        *) echo "$1 must be an https:// address" >&2; return 2 ;;
    esac
    printf '%s' "$u"
}
[ -z "$URL" ] || URL=$(clean "$URL") || exit 2
[ -z "$PRIVATE" ] || PRIVATE=$(clean "$PRIVATE") || exit 2

BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
pass=0 fail=0 skipped=0
say_ok()   { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
say_fail() { fail=$((fail + 1)); printf '  FAIL  %s\n        %s\n' "$1" "$2"; }
say_skip() { skipped=$((skipped + 1)); printf '  skip  %s\n' "$1"; }

# req BASE PATH [header-line ...]: CODE, LOC (the redirect target) and the body in $BODY. The headers go to curl as a
# config on its stdin, so a secret is never in a process listing.
req() {
    local base=$1 path=$2 cfg="" h out
    shift 2
    for h in "$@"; do cfg+="header = \"${h//\"/\\\"}\""$'\n'; done
    out=$(printf '%s' "$cfg" | curl -sS -K - -o "$BODY" -w '%{http_code} %{redirect_url}' --max-time 15 "$base$path" 2>&1) \
        || { CODE=000; LOC=$out; : > "$BODY"; return; }
    CODE=${out%% *}; LOC=${out#* }
}

b64url() { printf '%s' "$1" | base64 | tr -d '\n=' | tr '+/' '-_'; }
FORGED="$(b64url '{"alg":"none"}').$(b64url '{"email":"nobody@example.com"}').x"

# the login Access sends a visitor to
is_login() { [ "$CODE" = 302 ] && [[ $LOC == *cloudflareaccess.com* ]]; }
why() {     # what was seen, in a line
    case "$CODE" in
        000) printf 'no answer: %s' "$LOC" ;;
        302) printf 'sent to %s, which is not an Access login' "${LOC:-nowhere}" ;;
        *)   printf 'answered %s' "$CODE" ;;
    esac
}

if [ -n "$URL" ]; then
    echo "$URL: what a stranger sees"
    for p in / /api/v1/health; do
        req "$URL" "$p"
        if is_login; then say_ok "$p is sent to Cloudflare's login (302)"
        elif [ "$CODE" = 401 ] || [ "$CODE" = 200 ]; then
            say_fail "$p is not behind Access ($(why))" "the archive answered a stranger itself: the Access application does not cover this path (RUNBOOK step 4)"
        else say_fail "$p is not sent to Cloudflare's login ($(why))" "expected a 302 to <team>.cloudflareaccess.com"; fi
    done
    req "$URL" / "Cf-Access-Jwt-Assertion: $FORGED"
    if is_login; then say_ok "a made-up identity header, with no sign-in, is still sent to the login"
    elif [ "$CODE" = 200 ]; then
        say_fail "a made-up identity header gets a page ($(why))" "anyone can sign in as anyone: Access is not in front of this path (RUNBOOK step 4)"
    else say_fail "a made-up identity header is not sent to the login ($(why))" "expected a 302"; fi

    echo "$URL: the deck's service token"
    if [ -z "$ID" ] || [ -z "$SECRET" ]; then
        say_skip "no service token (set ARCHIVE_ACCESS_ID and ARCHIVE_ACCESS_SECRET, or give --conf)"
    else
        req "$URL" /api/v1/health "CF-Access-Client-Id: $ID" "CF-Access-Client-Secret: $SECRET"
        if [ "$CODE" = 200 ] && grep -q '"app": *"gatbox-archive"' "$BODY"; then say_ok "the service token reaches /api/v1/health: it is the archive"
        elif [ "$CODE" = 302 ]; then
            say_fail "the service token is not accepted ($(why))" "the secret cut short in the copy, the deck policy not Service Auth, or not ticked on the application"
        else say_fail "the service token does not reach the archive ($(why))" "expected 200 and the archive's health answer"; fi
        req "$URL" / "CF-Access-Client-Id: $ID" "CF-Access-Client-Secret: $SECRET"
        if [ "$CODE" = 401 ]; then say_ok "the service token gets no page (401: it has no email)"
        elif [ "$CODE" = 200 ]; then say_fail "the service token gets a page" "the pages are for people: a token must not read them"
        else say_fail "the service token on / is unexpected ($(why))" "expected the archive's own 401"; fi
        req "$URL" /api/v1/health "CF-Access-Client-Id: $ID" "CF-Access-Client-Secret: not-the-secret-$$"
        if [ "$CODE" = 200 ]; then say_fail "a wrong secret reaches the archive" "Access is not checking the service token"
        else say_ok "a wrong secret is turned away ($CODE)"; fi
    fi
fi

if [ -n "$PRIVATE" ]; then
    echo "$PRIVATE: the apps' listener"
    req "$PRIVATE" /api/v1/library/changes
    if [ "$CODE" = 401 ]; then say_ok "no token: refused (401)"
    else say_fail "no token is not refused ($(why))" "the private listener must answer reader tokens only"; fi
    req "$PRIVATE" /
    if [ "$CODE" = 401 ] || [ "$CODE" = 404 ]; then say_ok "no pages on it ($CODE)"
    else say_fail "the private listener serves a page ($(why))" "pages belong behind the tunnel and Access only"; fi
    if [ -z "$READER" ]; then
        say_skip "no reader token (set ARCHIVE_READER_TOKEN)"
    else
        req "$PRIVATE" '/api/v1/library/changes?since=0&limit=1' "Authorization: Bearer $READER"
        if [ "$CODE" = 200 ] && grep -q '^{"records"' "$BODY"; then say_ok "a reader token reads the change feed"
        elif [ "$CODE" = 000 ]; then say_fail "the listener is not reachable ($(why))" "connection refused: nothing listens there. Timeout: ARCHIVE_PRIVATE_ALLOW or a firewall drops it"
        else say_fail "the reader token is not accepted ($(why))" "revoked, wrong, or from another archive"; fi
    fi
fi

echo "checks: $pass passed, $fail failed, $skipped skipped"
[ "$fail" = 0 ]
