#!/usr/bin/env bash
# archive/install.sh — the GATBOX archive, installed in its own container from a checkout of this repository.
#
#   git clone https://github.com/digitalunconciousness/Gatbox.git && cd Gatbox
#   sudo bash archive/install.sh                    # install (or update: re-run it after a `git pull`)
#   bash archive/install.sh --check                 # what a run would change; no root, nothing changed
#   sudo bash archive/install.sh --allow-unmounted  # /srv/archive isn't a mount point (a test box only)
#
# Idempotent: a re-run changes nothing unless the checkout changed, and restarts the service only if its files did.
# It needs no internet once the packages are there. It does NOT touch Proxmox or Cloudflare: archive/RUNBOOK.md writes those
# steps out. The owner runs this herself, in the container; nothing else does.
#
# What it does:
#   1  packages: python3, poppler-utils (page images of a PDF for the pages), curl  (skipped when present)
#   2  the gatbox-archive user, and /srv/archive (its own mount point: it refuses a plain directory, or the root disk would
#      silently fill)
#   3  the files from this checkout (the table below), the pages' two fonts (pinned by sha256, fetched once: an offline
#      re-run needs nothing) and /etc/gatbox-archive/archive.conf (only if absent)
#   4  the service: enabled, started, restarted only when its files changed
#
# Test knobs: ARCHIVE_PREFIX=DIR installs under DIR and ARCHIVE_NO_SYSTEM=1 skips users, packages and systemctl;
# ARCHIVE_FONTS_BASE=URL and ARCHIVE_FONTS_TABLE=FILE point the font fetch at a local copy and its own checksums.
set -euo pipefail

REPO=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
PREFIX=${ARCHIVE_PREFIX:-}
NO_SYSTEM=${ARCHIVE_NO_SYSTEM:-}
DATA=${ARCHIVE_DATA:-/srv/archive}
CHECK=0; UNMOUNTED=0
for a in "$@"; do
    case "$a" in
        --check) CHECK=1 ;;
        --allow-unmounted) UNMOUNTED=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

# repo path -> installed path, mode
MANIFEST="
backend/gatboxlib/__init__.py                  /usr/local/lib/gatbox/gatboxlib/__init__.py           644
backend/gatboxlib/library.py                   /usr/local/lib/gatbox/gatboxlib/library.py            644
backend/gatboxlib/filestore.py                 /usr/local/lib/gatbox/gatboxlib/filestore.py          644
archive/gatboxarchive/__init__.py              /usr/local/lib/gatbox/gatboxarchive/__init__.py       644
archive/gatboxarchive/api.py                   /usr/local/lib/gatbox/gatboxarchive/api.py            644
archive/gatboxarchive/cli.py                   /usr/local/lib/gatbox/gatboxarchive/cli.py            644
archive/gatboxarchive/pages.py                 /usr/local/lib/gatbox/gatboxarchive/pages.py          644
archive/gatboxarchive/server.py                /usr/local/lib/gatbox/gatboxarchive/server.py         644
archive/gatboxarchive/tokens.py                /usr/local/lib/gatbox/gatboxarchive/tokens.py         644
archive/static/archive.css                     /usr/local/share/gatbox-archive/static/archive.css    644
archive/static/archive.js                      /usr/local/share/gatbox-archive/static/archive.js     644
archive/gatbox-archive                         /usr/local/bin/gatbox-archive                         755
archive/gatbox-archive.service                 /etc/systemd/system/gatbox-archive.service            644
"
# The pages' fonts: Chakra Petch + Share Tech Mono (SIL OFL, the design tokens' fonts), the same google/fonts commit and
# checksums as the deck's bootstrap. The archive serves them itself (no CDN); without them the pages use system fonts.
FONTS_REV=23e54b51ddffbc7713c583748e3bd86f62b1fa4a
FONTS_BASE=${ARCHIVE_FONTS_BASE:-https://raw.githubusercontent.com/google/fonts/$FONTS_REV}
FONTDIR=/usr/local/share/gatbox-archive/static/fonts
if [ -n "${ARCHIVE_FONTS_TABLE:-}" ]; then FONTS=$(cat "$ARCHIVE_FONTS_TABLE"); else FONTS="
ofl/chakrapetch/ChakraPetch-SemiBold.ttf      ChakraPetch-SemiBold.ttf   45264de3204ddbd5fb3e14a2402acd5c630d16650ae5fc221d2c52da46a6734b
ofl/chakrapetch/OFL.txt                       ChakraPetch-OFL.txt        13831d02389d917d22fcfa6c79f98f8acbf61d230add0e73a4d4dc5f3bfb9e56
ofl/sharetechmono/ShareTechMono-Regular.ttf   ShareTechMono-Regular.ttf  9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677
ofl/sharetechmono/OFL.txt                     ShareTechMono-OFL.txt      9d96f445b6e9c701428811d0177f894874f8d6f07ecc30d568c506542368f3ff
"; fi
WANT_FONTS=1
[ -z "$NO_SYSTEM" ] || [ -n "${ARCHIVE_FONTS_BASE:-}" ] || WANT_FONTS=0      # a test run has no network unless it brings its own copy
CONF_EXAMPLE=archive/archive.conf.example
CONF=/etc/gatbox-archive/archive.conf
UNIT=/etc/systemd/system/gatbox-archive.service
DROPIN=/etc/systemd/system/gatbox-archive.service.d/private.conf

log() { printf '  %s\n' "$*"; }
changed_files=()
DIFFS=()

differs() {   # <repo path> <installed path>: true when the installed file is missing or different
    [ ! -f "$PREFIX$2" ] || ! cmp -s "$REPO/$1" "$PREFIX$2"
}

font_good() {   # <local name> <sha256>: the installed font is the pinned one
    echo "$2  $PREFIX$FONTDIR/$1" | sha256sum -c --status 2>/dev/null
}
fetch_font() {   # <path in google/fonts> <local name> <sha256>
    local f="$PREFIX$FONTDIR/$2"
    font_good "$2" "$3" && return 0
    mkdir -p "$PREFIX$FONTDIR"
    curl -fsSL --retry 2 -o "$f.part" "$FONTS_BASE/$1" \
        && echo "$3  $f.part" | sha256sum -c --status && mv "$f.part" "$f" && chmod 644 "$f" && return 0
    rm -f "$f.part"
    return 1
}

# --- what would change ---------------------------------------------------------------------------------------------
while read -r src dst mode; do
    [ -n "$src" ] || continue
    [ -f "$REPO/$src" ] || { echo "the checkout has no $src" >&2; exit 1; }
    if differs "$src" "$dst"; then DIFFS+=("install    $dst"); changed_files+=("$dst"); fi
done <<<"$MANIFEST"
[ -f "$PREFIX$CONF" ] || DIFFS+=("create     $CONF (from $CONF_EXAMPLE; never overwritten after this)")
if [ "$WANT_FONTS" = 1 ]; then
    while read -r path name sum; do
        [ -n "$name" ] || continue
        font_good "$name" "$sum" || DIFFS+=("fetch      $FONTDIR/$name")
    done <<<"$FONTS"
fi

private_allow=""
if [ -f "$PREFIX$CONF" ]; then
    private_allow=$(sed -n 's/^ARCHIVE_PRIVATE_ALLOW=//p' "$PREFIX$CONF" | head -1 | tr -d ' "')
fi
want_dropin=""
[ -n "$private_allow" ] && want_dropin=$(printf '[Service]\nIPAddressAllow=%s\n' "$private_allow")
have_dropin=""
[ -f "$PREFIX$DROPIN" ] && have_dropin=$(cat "$PREFIX$DROPIN")
[ "$want_dropin" = "$have_dropin" ] || DIFFS+=("drop-in    $DROPIN (${private_allow:-removed})")

if [ -z "$NO_SYSTEM" ]; then
    command -v pdftoppm >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 \
        || DIFFS+=("apt        python3 poppler-utils curl ca-certificates")
    getent passwd gatbox-archive >/dev/null || DIFFS+=("create     user gatbox-archive")
    mountpoint -q "$DATA" 2>/dev/null || { [ "$UNMOUNTED" = 1 ] || DIFFS+=("REFUSE     $DATA is not a mount point (--allow-unmounted for a test box)"); }
fi

if [ "$CHECK" = 1 ]; then
    if [ "${#DIFFS[@]}" = 0 ]; then echo "archive: nothing to change"; else printf 'archive would:\n'; printf '  %s\n' "${DIFFS[@]}"; fi
    exit 0
fi

if [ -z "$NO_SYSTEM" ] && [ "$(id -u)" != 0 ]; then echo "run it with sudo (or --check to look)" >&2; exit 1; fi
if [ -z "$NO_SYSTEM" ] && ! mountpoint -q "$DATA" 2>/dev/null && [ "$UNMOUNTED" != 1 ]; then
    echo "$DATA is not a mount point. Create the data mount in Proxmox first (RUNBOOK step 1), or pass --allow-unmounted on a test box." >&2
    exit 1
fi

# --- 1, 2: packages, user, data -----------------------------------------------------------------------------------------
if [ -z "$NO_SYSTEM" ]; then
    if ! { command -v pdftoppm >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; }; then
        log "apt: python3 poppler-utils curl ca-certificates"
        apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 poppler-utils curl ca-certificates
    fi
    getent passwd gatbox-archive >/dev/null || {
        useradd --system --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin gatbox-archive && log "user gatbox-archive"; }
    install -d -m 0750 -o gatbox-archive -g gatbox-archive "$DATA"
fi

# --- 3: files -----------------------------------------------------------------------------------------------------------------
while read -r src dst mode; do
    [ -n "$src" ] || continue
    if differs "$src" "$dst"; then
        install -D -m "$mode" "$REPO/$src" "$PREFIX$dst" && log "installed $dst"
    fi
done <<<"$MANIFEST"
fonts_fetched=0
if [ "$WANT_FONTS" = 1 ]; then
    fonts_ok=1
    while read -r path name sum; do
        [ -n "$name" ] || continue
        if ! font_good "$name" "$sum"; then
            if fetch_font "$path" "$name" "$sum"; then log "font $name"; fonts_fetched=$((fonts_fetched + 1)); else fonts_ok=0; fi
        fi
    done <<<"$FONTS"
    [ "$fonts_ok" = 1 ] || log "!! couldn't fetch the fonts (no internet?). The pages use system fonts until a re-run gets them."
fi
if [ ! -f "$PREFIX$CONF" ]; then
    install -D -m 0644 "$REPO/$CONF_EXAMPLE" "$PREFIX$CONF" && log "created $CONF: look it over, then restart the service"
fi
if [ "$want_dropin" != "$have_dropin" ]; then
    if [ -n "$want_dropin" ]; then
        mkdir -p "$(dirname "$PREFIX$DROPIN")" && printf '%s\n' "$want_dropin" > "$PREFIX$DROPIN" && log "drop-in $DROPIN: IPAddressAllow=$private_allow"
    else
        rm -f "$PREFIX$DROPIN" && log "removed $DROPIN"
    fi
    changed_files+=("$DROPIN")
fi

# --- 4: the service ----------------------------------------------------------------------------------------------------------
if [ -z "$NO_SYSTEM" ]; then
    if [ "${#changed_files[@]}" -gt 0 ] || ! systemctl is-enabled --quiet gatbox-archive 2>/dev/null; then
        systemctl daemon-reload
        systemctl enable gatbox-archive >/dev/null 2>&1 || true
        systemctl restart gatbox-archive && log "gatbox-archive (re)started"
    fi
    systemctl is-active --quiet gatbox-archive && log "gatbox-archive is running on ${ARCHIVE_TUNNEL_BIND:-127.0.0.1:8080}" \
        || log "gatbox-archive is NOT running: journalctl -u gatbox-archive"
fi

if [ "${#changed_files[@]}" = 0 ] && [ "$fonts_fetched" = 0 ] && [ -f "$PREFIX$CONF" ]; then echo "archive: nothing to change"; fi
echo "Next: archive/RUNBOOK.md (the Cloudflare tunnel and Access, the tokens)."
