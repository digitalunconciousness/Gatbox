"""The archive's pages: a phone-first reference library for the two people who keep it (stdlib, no template engine).

Games and search, one game (its manuals and the page viewer, rail limits, facts, MAME set and ROM list, ROM images and
their verdicts), add a game, edit a game, add and fix facts and limits, upload a PDF. They are for people only: the
identity comes from Access's JWT on the tunnel listener (Handler.person), and there is no page on the private one.

What a person may change is api.PERSON_KINDS, enforced where the writes are made (api.apply_writes), so a page can't get
round it: a limit added here arrives unconfirmed, and CONFIRM stays on the deck's 7" beside its manual page. Every write
carries the revision the form was opened at; a disagreement shows both versions and keeps what was typed.

CSRF: a form carries an HMAC of the signed-in email, made with a key kept beside the tokens, and a request the browser
marks cross-site is refused. The Origin header isn't compared with Host, because a tunnel may rewrite Host.
"""
import hashlib
import hmac
import html
import os
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, quote

from gatboxlib import library as L

from . import api
from .server import Refuse, route

E = html.escape
KEY = r"([a-z0-9]+(?:-[a-z0-9]+)*)"
DOC = r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})"
HEX32 = r"([0-9a-f]{32})"
UPLOAD_MAX = 90 * 1024 * 1024                  # Cloudflare takes 100 MB a request on the Free and Pro plans
PAGE_DPI = 110
FORM_MAX = 64 * 1024
RENDERS = threading.Semaphore(2)               # pdftoppm runs: two at a time

NOTES = {"saved": "Saved.", "added": "Game added.", "removed": "Removed.", "uploaded": "PDF added.",
         "changed": "Someone changed this while you were looking. It is shown as it is now."}
DOC_KIND_LABEL = {"manual": "Manual", "schematics": "Schematics", "parts": "Parts list", "kit": "Kit", "bulletin": "Bulletin",
                  "other": "Other", "yours": "Yours"}
GROUP_LABEL = {"fuse": "Fuse", "monitor": "Monitor", "psu": "Power supply", "power": "Power", "other": "Other"}
GAME_KIND_LABEL = {"video": "Video", "pinball": "Pinball", "other": "Other"}
CHILDREN = {
    "facts": {"kind": "fact", "noun": "fact", "fields": ("group", "what", "value", "doc_id", "page", "quote")},
    "limits": {"kind": "rail_limit", "noun": "limit", "fields": ("rail", "lo", "hi", "doc_id", "page", "quote")},
}


class FormError(Exception):
    pass


# --- the page shell -----------------------------------------------------------------------------------------------------

NAV = (("/", "Games", "games"), ("/add", "Add a game", "add"))

# The tab's icon, inline: the browser asks for /favicon.ico when a page names none, and the archive has no such file, so
# every page load logged a 404. A data: image is allowed by the page policy (img-src 'self' data:) and is no request at
# all. A G in the headline magenta on the deep violet, from the design tokens.
ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='#150a28'/>"
            "<path d='M22 11h-8a5 5 0 0 0 0 10h8v-5h-4' fill='none' stroke='#ff2e9f' stroke-width='3' stroke-linejoin='round'/></svg>")
ICON = "data:image/svg+xml," + quote(ICON_SVG, safe="/:=',")


def layout(title, body, who=None, active="", footer=True):
    nav = ""
    if who:
        links = "".join(f'<a href="{href}"{" aria-current=page" if key == active else ""}>{E(label)}</a>' for href, label, key in NAV)
        nav = f'<nav aria-label="Archive">{links}</nav><span class="who">{E(who)}</span>'
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{E(title)} | GATBOX archive</title>'
            f'<link rel="icon" href="{ICON}"><link rel="stylesheet" href="/assets/archive.css"></head><body>'
            f'<a class="skip" href="#main">Skip to the page</a>'
            f'<header><a class="brand" href="/"><span>GAT<b>BOX</b></span><span class="word">archive</span></a>{nav}</header>'
            f'<main id="main">{body}</main>'
            + ("<footer>Changes made here reach the deck the next time it syncs.</footer>" if footer else "") +
            f'<script src="/assets/archive.js" defer></script></body></html>')


ERROR_TITLE = {401: "Not signed in", 403: "Not allowed", 404: "Not found", 405: "Not allowed", 413: "Too large",
               415: "Wrong kind of file"}


def error_page(code, message):
    title = ERROR_TITLE.get(code, "Something went wrong")
    body = (f'<h1>{E(title)}</h1><p>{E(message)}</p>'
            + ('<p class="dim">Open the archive\'s address in your browser and sign in with your email.</p>' if code == 401 else "")
            + '<p><a class="btn" href="/">Back to the games</a></p>')
    return layout(title, body, footer=False)


def actor(email):
    """The writer api.apply_writes wants for a person: it stamps the edit with the name and the time, and applies PERSON_KINDS."""
    return {"kind": "person", "name": email}


def tag(text, cls=""):
    return f'<span class="tag {cls}">{E(text)}</span>'


def note(text, cls="", role="status"):
    return f'<p class="note {cls}" role="{role}">{E(text)}</p>'


def day(at):
    return time.strftime("%Y-%m-%d", time.gmtime(at)) if at else ""


def size_text(n):
    if n is None:
        return ""
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def flash(q):
    text = NOTES.get((q.get("done") or [""])[0])
    return note(text, "ok") if text else ""


# --- form plumbing ------------------------------------------------------------------------------------------------------

def csrf_token(h):
    return hmac.new(h.archive.ui_secret.encode(), h.person().encode(), hashlib.sha256).hexdigest()


def check_csrf(h, supplied):
    if (h.headers.get("Sec-Fetch-Site") or "same-origin") == "cross-site":
        raise Refuse(403, "That request came from another site.")
    if not hmac.compare_digest(supplied or "", csrf_token(h)):
        raise Refuse(403, "That form has expired. Go back, reload the page and send it again.")


def read_form(h):
    if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        raise Refuse(415, "send a form")
    try:
        raw = parse_qs(h.read_body(FORM_MAX).decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        raise Refuse(400, "the form must be UTF-8 text") from None
    form = {k: v[-1].replace("\r\n", "\n").strip() for k, v in raw.items()}
    check_csrf(h, form.get("csrf"))
    return form


def number(form, name, label):
    raw = form.get(name, "")
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise FormError(f"{label} must be a whole number.") from None


def volts_text(raw):
    return re.sub(r"\s*[vV]$", "", raw.strip())


def f_input(name, label, value="", req=False, maxlen=None, hint="", kind="text", mode=None):
    fid = f"f-{name}"
    desc = f' aria-describedby="{fid}-hint"' if hint else ""
    attrs = (' required' if req else "") + (f' maxlength="{maxlen}"' if maxlen else "") + (f' inputmode="{mode}"' if mode else "")
    return (f'<label for="{fid}">{E(label)}</label>'
            f'<input id="{fid}" name="{name}" type="{kind}" value="{E(value)}" autocomplete="off"{attrs}{desc}>'
            + (f'<p class="dim" id="{fid}-hint">{E(hint)}</p>' if hint else ""))


def f_area(name, label, value="", maxlen=None, hint=""):
    fid = f"f-{name}"
    desc = f' aria-describedby="{fid}-hint"' if hint else ""
    return (f'<label for="{fid}">{E(label)}</label>'
            f'<textarea id="{fid}" name="{name}"{f' maxlength="{maxlen}"' if maxlen else ""}{desc}>{E(value)}</textarea>'
            + (f'<p class="dim" id="{fid}-hint">{E(hint)}</p>' if hint else ""))


def f_select(name, label, options, value=""):
    fid = f"f-{name}"
    opts = "".join(f'<option value="{E(v)}"{" selected" if v == value else ""}>{E(t)}</option>' for v, t in options)
    return f'<label for="{fid}">{E(label)}</label><select id="{fid}" name="{name}">{opts}</select>'


def hidden(**kw):
    return "".join(f'<input type="hidden" name="{k}" value="{E(str(v))}">' for k, v in kw.items())


def form_html(h, action, fields, hid, submit, cancel, extra_buttons=""):
    return (f'<form method="post" action="{E(action)}">{hidden(csrf=csrf_token(h), **hid)}{fields}'
            f'<div class="btns"><button class="primary" type="submit">{E(submit)}</button>{extra_buttons}'
            f'<a class="btn" href="{E(cancel)}">Cancel</a></div></form>')


# --- looking things up --------------------------------------------------------------------------------------------------

def by_model(records):
    out = {}
    for r in records:
        out.setdefault(r["data"]["model_key"], []).append(r)
    return out


def live_game(h, key):
    """The game a page is about, or a 404 that says where a merged one went."""
    lib = h.archive.lib
    g = lib.get("game", key)
    if g is None:
        raise Refuse(404, f"There is no game with the key {key}.")
    if g["data"]["replaced_by"]:
        to = lib.resolve(key)
        raise Refuse(404, f"{g['data']['title']} was merged into {to['data']['title'] if to else g['data']['replaced_by']}.")
    return g


def doc_options(docs):
    return [("", "None")] + [(d["data"]["doc_id"], d["data"]["title"]) for d in sorted(docs, key=lambda d: d["data"]["title"].casefold())]


# --- the games list -----------------------------------------------------------------------------------------------------

@route("GET", r"/")
def home(h, m, q):
    who, lib = h.person(), h.archive.lib
    needle = ((q.get("q") or [""])[0]).strip()[:80]
    games = sorted((g for g in lib.all("game") if not g["data"]["replaced_by"]), key=lambda g: g["data"]["title"].casefold())
    docs, limits, facts = (by_model(lib.all(k)) for k in ("document", "rail_limit", "fact"))
    words = needle.casefold().split()
    rows = []
    for g in games:
        d, key = g["data"], g["id"]
        if words:
            hay = " ".join([d["title"], d["mfr"], key] + [x["data"]["title"] for x in docs.get(key, [])]
                           + [f"{x['data']['what']} {x['data']['value']}" for x in facts.get(key, [])]
                           + [x["data"]["rail"] for x in limits.get(key, [])]).casefold()
            if not all(w in hay for w in words):
                continue
        n_docs = len(docs.get(key, []))
        waiting = sum(1 for x in limits.get(key, []) if not x["data"]["confirmed"])
        meta = " · ".join(x for x in (d["mfr"], str(d["year"]) if d["year"] else "", GAME_KIND_LABEL[d["kind"]]) if x)
        tags = (tag(f"{n_docs} document{'s' if n_docs != 1 else ''}") if n_docs else tag("no manual yet", "warn"))
        if waiting:
            tags += " " + tag(f"{waiting} limit{'s' if waiting != 1 else ''} not confirmed", "warn")
        rows.append(f'<div class="row"><div class="grow"><a class="t" href="/games/{key}">{E(d["title"])}</a>'
                    f'<div class="dim">{E(meta)}</div></div><div>{tags}</div></div>')
    if rows:
        listing = "".join(rows)
    elif games:
        listing = (f'<p class="empty">Nothing matches {E(needle)}. Search by title, maker, a document or a rail.</p>'
                   f'<div class="btns"><a class="btn" href="/">Clear the search</a><a class="btn" href="/add">Add a game</a></div>')
    else:
        listing = ('<p class="empty">The archive has no games yet. The deck fills it on its first sync, or add one by hand.</p>'
                   '<div class="btns"><a class="btn primary" href="/add">Add a game</a></div>')
    count = f'{len(rows)} of {len(games)} games' if words else f'{len(games)} game{"s" if len(games) != 1 else ""}'
    body = (f'<h1>Games</h1><p class="sub">{count}</p>{flash(q)}'
            f'<form method="get" action="/" role="search"><label for="q">Search</label>'
            f'<input id="q" name="q" type="search" value="{E(needle)}" maxlength="80" autocomplete="off">'
            f'<div class="btns"><button type="submit">Search</button></div></form>'
            f'<section aria-label="Games">{listing}</section>')
    h.html(200, layout("Games", body, who, "games"))


# --- one game -----------------------------------------------------------------------------------------------------------

def docs_section(h, key, docs, show_upload):
    rows = []
    for d in sorted(docs, key=lambda d: d["data"]["title"].casefold()):
        x = d["data"]
        bits = [DOC_KIND_LABEL[x["kind"]], f'{x["pages"]} pages' if x["pages"] else "", size_text(x["bytes"]), x["source"]]
        state = "" if x["sha256"] else " " + tag("no file yet", "warn")
        rows.append(f'<div class="row"><div class="grow"><a class="t" href="/games/{key}/docs/{quote(x["doc_id"])}">{E(x["title"])}</a>'
                    f'<div class="dim">{E(" · ".join(b for b in bits if b))}</div></div><div class="acts">{state}'
                    f'<a class="btn" href="/games/{key}/docs/{quote(x["doc_id"])}/edit" aria-label="Edit {E(x["title"])}">Edit</a></div></div>')
    empty = "" if rows else '<p class="empty">No documents for this game yet.</p>'
    token = csrf_token(h)
    kinds = "".join(f'<option value="{k}">{E(v)}</option>' for k, v in DOC_KIND_LABEL.items())
    upload = (f'<details{" open" if show_upload else ""}><summary>Upload a PDF</summary><div>'
              f'<form data-upload="/games/{key}/upload" data-csrf="{token}" data-done="/games/{key}?done=uploaded#documents">'
              f'<label for="up-title">Title</label>'
              f'<input id="up-title" name="title" maxlength="120" autocomplete="off" aria-describedby="up-hint">'
              f'<p class="dim" id="up-hint">Leave it empty to use the file name.</p>'
              f'<label for="up-kind">Kind</label><select id="up-kind" name="kind">{kinds}</select>'
              f'<label for="up-file">PDF, up to 90 MB</label>'
              f'<input id="up-file" name="file" type="file" accept="application/pdf">'
              f'<div class="btns"><button class="primary" type="submit">Upload</button></div>'
              f'<p data-status class="dim" role="status"></p></form></div></details>')
    return f'<section id="documents" aria-labelledby="h-docs"><h2 id="h-docs">Documents</h2>{empty}{"".join(rows)}{upload}</section>'


def where_text(key, doc_id, page, docs):
    """A link to the page of the document a limit or fact came from."""
    if not doc_id:
        return ""
    title = next((d["data"]["title"] for d in docs if d["data"]["doc_id"] == doc_id), doc_id)
    href = f"/games/{key}/docs/{quote(doc_id)}" + (f"?page={page}" if page else "")
    lead = f"p. {page} of " if page else ""
    return f'<a href="{href}">{E(lead + title)}</a>'


def limits_section(key, limits, docs):
    rows = []
    for r in sorted(limits, key=lambda r: r["data"]["rail"].casefold()):
        x = r["data"]
        state = tag("CONFIRMED", "ok") if x["confirmed"] else tag("NOT CONFIRMED", "warn")
        when = f' {day(x["confirmed"]["at"])}' if x["confirmed"] else ""
        src = where_text(key, x["doc_id"], x["page"], docs)
        quote_html = f'<div class="dim">“{E(x["quote"])}”</div>' if x["quote"] else ""
        rows.append(f'<div class="row"><div class="grow"><span class="t">{E(x["rail"])}</span> {E(x["lo"])} to {E(x["hi"])} V'
                    f'<div class="dim src">{src}</div>{quote_html}</div><div class="acts">{state}<span class="dim">{when}</span> '
                    f'<a class="btn" href="/games/{key}/limits/{r["id"]}" aria-label="Edit the {E(x["rail"])} limit">Edit</a></div></div>')
    empty = '<p class="empty">No rail limits for this game yet.</p>' if not rows else ""
    return (f'<section id="limits" aria-labelledby="h-limits"><h2 id="h-limits">Rail limits</h2>'
            f'<p class="dim">A limit added here is not used by the logger until someone confirms it on the deck\'s screen, '
            f'beside its manual page.</p>{empty}{"".join(rows)}'
            f'<div class="btns"><a class="btn" href="/games/{key}/limits/add">Add a limit</a></div></section>')


def facts_section(key, facts, docs):
    rows = []
    for r in sorted(facts, key=lambda r: (r["data"]["group"], r["data"]["what"].casefold())):
        x = r["data"]
        src = where_text(key, x["doc_id"], x["page"], docs)
        quote_html = f'<div class="dim">“{E(x["quote"])}”</div>' if x["quote"] else ""
        rows.append(f'<div class="row"><div class="grow"><span class="dim">{E(GROUP_LABEL[x["group"]])}</span> '
                    f'<span class="t">{E(x["what"])}</span> {E(x["value"])}<div class="dim src">{src}</div>{quote_html}</div>'
                    f'<div class="acts"><a class="btn" href="/games/{key}/facts/{r["id"]}" aria-label="Edit {E(x["what"])}">Edit</a></div></div>')
    empty = '<p class="empty">No facts for this game yet.</p>' if not rows else ""
    return (f'<section id="facts" aria-labelledby="h-facts"><h2 id="h-facts">Facts</h2>{empty}{"".join(rows)}'
            f'<div class="btns"><a class="btn" href="/games/{key}/facts/add">Add a fact</a></div></section>')


def mame_section(mame, rom_list, images):
    out = ['<section id="mame" aria-labelledby="h-mame"><h2 id="h-mame">MAME and ROM chips</h2>']
    if mame is None:
        out.append('<p class="empty">No MAME entry for this game.</p>')
    else:
        x = mame["data"]
        if x["set"] is None:
            out.append('<p>MAME does not list this game.</p>' + (f'<p class="dim">{E(x["why"])}</p>' if x["why"] else ""))
        else:
            match = {"sure": "match: sure", "check": "match: check it"}.get(x["status"], "")
            out.append(f'<p>Set <b>{E(x["set"])}</b>' + (f', preferred version {E(x["prefer"])}' if x["prefer"] else "")
                       + (f' {tag(match, "ok" if x["status"] == "sure" else "warn")}' if match else "") + '</p>'
                       + (f'<p class="dim">{E(x["why"])}</p>' if x["why"] else ""))
    if rom_list is not None:
        for v in rom_list["data"]["versions"]:
            roms = "".join(f'<tr><td>{E(r["name"])}</td><td>{E(size_text(r["size"]))}</td><td>{E(r["crc"] or "")}</td>'
                           f'<td>{E((r["sha1"] or "")[:10])}</td><td>{E(r["status"] or "")}</td></tr>' for r in v["roms"])
            label = f'{v["name"]}{" (parent)" if v["parent"] else ""}: {v["description"]}'
            out.append(f'<details><summary>{E(label)}, {len(v["roms"])} ROMs</summary><div class="scroll">'
                       f'<table class="chips"><thead><tr><th>ROM</th><th>Size</th><th>CRC</th><th>SHA-1</th><th>Status</th></tr></thead>'
                       f'<tbody>{roms}</tbody></table></div></details>')
    elif mame is not None and mame["data"]["set"] is not None:
        out.append('<p class="dim">No ROM list for this set yet.</p>')
    out.append('<h3>Dumped images</h3>')
    if images:
        rows = "".join(f'<tr><td>{E(i["data"]["chip"] or i["data"]["label"] or "image")}</td><td>{E(size_text(i["data"]["size"]))}</td>'
                       f'<td>{E(i["data"]["sha1"][:10])}</td>'
                       f'<td>{tag(i["data"]["verdict"], {"good": "ok", "bad": "bad"}.get(i["data"]["verdict"], ""))}</td></tr>'
                       for i in images)
        out.append(f'<div class="scroll"><table class="chips"><thead><tr><th>Chip</th><th>Size</th><th>SHA-1</th><th>Verdict</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table></div>')
    else:
        out.append('<p class="empty">No dumped images for this game yet. The deck sends them after a dump.</p>')
    out.append('</section>')
    return "".join(out)


@route("GET", rf"/games/{KEY}")
def game_page(h, m, q):
    who, lib, key = h.person(), h.archive.lib, m.group(1)
    g = lib.get("game", key)
    if g is not None and g["data"]["replaced_by"]:
        to = lib.resolve(key)
        if to:
            return h.redirect(f"/games/{to['id']}")
    g = live_game(h, key)
    d = g["data"]
    docs, limits, facts = (lib.all(k, key) for k in ("document", "rail_limit", "fact"))
    mame, rom_list = lib.get("mame", key), lib.get("rom_list", key)
    images = [i for i in lib.all("rom_image") if i["data"]["model_key"] == key]
    meta = " · ".join(x for x in (d["mfr"], str(d["year"]) if d["year"] else "", GAME_KIND_LABEL[d["kind"]]) if x)
    body = (f'<p class="line"><a href="/">Games</a></p><h1>{E(d["title"])}</h1><p class="sub">{E(meta)} · <span class="mono">{E(key)}</span></p>'
            f'{flash(q)}' + (f'<p>{E(d["notes"])}</p>' if d["notes"] else "")
            + f'<div class="btns"><a class="btn" href="/games/{key}/edit" aria-label="Edit {E(d["title"])}">Edit this game</a></div>'
            + docs_section(h, key, docs, not docs) + limits_section(key, limits, docs) + facts_section(key, facts, docs)
            + mame_section(mame, rom_list, images))
    h.html(200, layout(d["title"], body, who, "games"))


# --- add and edit a game ------------------------------------------------------------------------------------------------

def game_fields(vals, key=None):
    kinds = [(k, v) for k, v in GAME_KIND_LABEL.items()]
    out = (f_input("title", "Title", vals.get("title", ""), req=True, maxlen=120)
           + f_input("mfr", "Maker", vals.get("mfr", ""), maxlen=120)
           + f_input("year", "Year", vals.get("year", ""), mode="numeric", maxlen=4)
           + f_select("kind", "Kind", kinds, vals.get("kind", "video"))
           + f_area("notes", "Notes", vals.get("notes", ""), maxlen=2000))
    if key is not None:
        out += f_input("key", "Key", key, req=True, maxlen=L.KEY_MAX,
                       hint="The key names this game everywhere (labels, the deck, the apps) and can't be changed later.")
    return out


def game_values(form):
    return {k: form.get(k, "") for k in ("title", "mfr", "year", "kind", "notes")}


def game_data(form):
    year = number(form, "year", "Year")
    kind = form.get("kind", "video")
    if not form.get("title"):
        raise FormError("A game needs a title.")
    return {"title": form["title"], "mfr": form.get("mfr", ""), "year": year, "kind": kind, "notes": form.get("notes", ""),
            "replaced_by": None}


@route("GET", r"/add")
def add_form(h, m, q):
    who = h.person()
    body = ('<h1>Add a game</h1><p class="sub">Title first. You will see the key it gets before anything is saved.</p>'
            + form_html(h, "/add", game_fields({}), {}, "Check the key", "/"))
    h.html(200, layout("Add a game", body, who, "add"))


def add_render(h, who, vals, key, error=""):
    lib = h.archive.lib
    twins = [g for g in lib.all("game") if L.slug(g["data"]["title"]) == L.slug(vals.get("title", "")) and g["id"] != key]
    warn = ""
    if twins:
        links = ", ".join(f'<a href="/games/{g["id"]}">{E(g["data"]["title"])}</a>' for g in twins[:3])
        warn = (f'<p class="note warn" role="status">A game with this title is already here: {links}. '
                f'Add another only if it is a different maker or year.</p>')
    err = note(error, "bad", "alert") if error else ""
    fields = game_fields(vals, key)
    if key is None:
        save, again = '<button class="primary" type="submit" formaction="/add">Check the key</button>', ""
    else:
        save = '<button class="primary" type="submit" formaction="/add/save">Save game</button>'
        again = '<button type="submit" formaction="/add">Check the key again</button>'
    body = (f'<h1>Add a game</h1>{err}{warn}'
            f'<form method="post" action="/add/save">{hidden(csrf=csrf_token(h), uid=vals.get("uid") or uuid.uuid4().hex)}{fields}'
            f'<div class="btns">{save}{again}<a class="btn" href="/">Cancel</a></div></form>')
    h.html(200, layout("Add a game", body, who, "add"))


@route("POST", r"/add")
def add_preview(h, m, q):
    who = h.person()
    form = read_form(h)
    vals = game_values(form)
    vals["uid"] = form.get("uid", "")
    typed = form.get("key") or None
    try:
        game_data(form)
        taken = {g["id"] for g in h.archive.lib.all("game", deleted=True)}
        if typed:
            key = L.key_field(typed)
            if key in taken:
                raise FormError(f"The key {key} is already used by another game. Pick a different one.")
        else:
            key = L.model_key(form["title"], form.get("kind", "video"), form.get("mfr") or None, number(form, "year", "Year"), taken)
    except (FormError, ValueError, L.Reject) as e:
        return add_render(h, who, vals, typed, str(e))
    add_render(h, who, vals, key)


@route("POST", r"/add/save")
def add_save(h, m, q):
    who = h.person()
    form = read_form(h)
    vals = game_values(form)
    vals["uid"] = form.get("uid", "")
    try:
        data = game_data(form)
        key = L.key_field(form.get("key", ""))
    except (FormError, L.Reject) as e:
        return add_render(h, who, vals, form.get("key") or None, str(e))
    res = api.apply_writes(h.archive.lib, [{"kind": "game", "id": key, "if_rev": 0, "uid": form.get("uid") or None, "data": data}], actor(who))[0]
    if res["status"] in ("created", "duplicate"):
        return h.redirect(f"/games/{key}?done=added")
    if res["status"] == "conflict":
        return add_render(h, who, vals, key, f"The key {key} is already taken. Pick another key.")
    add_render(h, who, vals, key, f"Not saved: {res.get('reason', 'the archive refused it')}")


def edit_game_render(h, who, g, vals, rev, error="", theirs=None):
    key = g["id"]
    extra = ""
    if theirs is not None:
        extra = note("Someone else saved this game while you were editing it. Your changes are still in the form. "
                     "Saving now replaces their version.", "warn")
        diff = [(f, theirs["data"][f], vals.get(f, "")) for f in ("title", "mfr", "year", "kind", "notes")
                if str(theirs["data"][f] if theirs["data"][f] is not None else "") != vals.get(f, "")]
        if diff:
            extra += "<dl>" + "".join(f"<dt>{E(f)}: theirs</dt><dd>{E(str(t if t is not None else ''))}</dd>" for f, t, _ in diff) + "</dl>"
    err = note(error, "bad", "alert") if error else ""
    body = (f'<p class="line"><a href="/games/{key}">{E(g["data"]["title"])}</a></p><h1>Edit {E(g["data"]["title"])}</h1>'
            f'<p class="sub">Key <span class="mono">{E(key)}</span> (permanent)</p>{err}{extra}'
            + form_html(h, f"/games/{key}/edit", game_fields(vals), {"rev": rev, "uid": uuid.uuid4().hex}, "Save", f"/games/{key}"))
    h.html(200, layout(f"Edit {g['data']['title']}", body, who, "games"))


def values_of_game(d):
    return {"title": d["title"], "mfr": d["mfr"], "year": str(d["year"]) if d["year"] else "", "kind": d["kind"], "notes": d["notes"]}


@route("GET", rf"/games/{KEY}/edit")
def edit_game_form(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    edit_game_render(h, who, g, values_of_game(g["data"]), g["rev"])


@route("POST", rf"/games/{KEY}/edit")
def edit_game_save(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    form = read_form(h)
    vals = game_values(form)
    try:
        rev = int(form.get("rev", ""))
        data = dict(game_data(form), replaced_by=g["data"]["replaced_by"])
    except (FormError, ValueError) as e:
        return edit_game_render(h, who, g, vals, g["rev"], str(e) if isinstance(e, FormError) else "That form is out of date. Reload it.")
    res = api.apply_writes(h.archive.lib, [{"kind": "game", "id": g["id"], "if_rev": rev, "uid": form.get("uid") or None, "data": data}], actor(who))[0]
    if res["status"] in ("updated", "duplicate"):
        return h.redirect(f"/games/{g['id']}?done=saved")
    if res["status"] == "conflict":
        return edit_game_render(h, who, g, vals, res["current"]["rev"], theirs=res["current"])
    edit_game_render(h, who, g, vals, rev, f"Not saved: {res.get('reason', 'the archive refused it')}")


# --- facts and limits ---------------------------------------------------------------------------------------------------

def child_values(spec, data):
    return {f: "" if data.get(f) is None else str(data[f]) for f in spec["fields"]}


def child_data(spec, key, form):
    data = {"model_key": key}
    for f in spec["fields"]:
        data[f] = form.get(f, "")
    data["doc_id"] = data["doc_id"] or None
    data["page"] = number(form, "page", "Page")
    if spec["kind"] == "rail_limit":
        data["lo"], data["hi"] = volts_text(data["lo"]), volts_text(data["hi"])
        if not data["rail"]:
            raise FormError("Name the rail, for example +5V.")
        if not data["lo"] or not data["hi"]:
            raise FormError("Give both the low and the high limit, in volts.")
    elif not data["what"]:
        raise FormError("Say what the fact is about, for example the fuse position.")
    return data


def child_form(h, who, g, path, rid, rev, vals, docs, error="", theirs=None, gone=False, notice=""):
    spec, key = CHILDREN[path], g["id"]
    noun = spec["noun"]
    if spec["kind"] == "fact":
        fields = (f_select("group", "Group", [(k, v) for k, v in GROUP_LABEL.items()], vals.get("group", "other"))
                  + f_input("what", "About", vals.get("what", ""), req=True, maxlen=60, hint="For example: fuse F1, monitor chassis.")
                  + f_input("value", "Value", vals.get("value", ""), maxlen=120))
    else:
        fields = (f_input("rail", "Rail", vals.get("rail", ""), req=True, maxlen=20, hint="For example: +5V.")
                  + f_input("lo", "Lowest, in volts", vals.get("lo", ""), req=True, mode="decimal", maxlen=12)
                  + f_input("hi", "Highest, in volts", vals.get("hi", ""), req=True, mode="decimal", maxlen=12))
    fields += (f_select("doc_id", "Document it came from", doc_options(docs), vals.get("doc_id", ""))
               + f_input("page", "Page", vals.get("page", ""), mode="numeric", maxlen=6)
               + f_area("quote", "What the page says", vals.get("quote", ""), maxlen=500,
                        hint="The words from the page, so the next person can check them."))
    extra = ""
    if gone:
        extra = note(f"Someone removed this {noun} while you were editing it. Saving now adds it back.", "warn")
    elif theirs is not None:
        extra = note(f"Someone else saved this {noun} while you were editing it. Your changes are still in the form. "
                     f"Saving now replaces their version.", "warn")
        diff = [(f, str(theirs["data"].get(f) if theirs["data"].get(f) is not None else "")) for f in spec["fields"]
                if str(theirs["data"].get(f) if theirs["data"].get(f) is not None else "") != vals.get(f, "")]
        if diff:
            extra += "<dl>" + "".join(f"<dt>{E(f)}: theirs</dt><dd>{E(t)}</dd>" for f, t in diff) + "</dl>"
    err = note(error, "bad", "alert") if error else ""
    new = rev == 0
    action = f"/games/{key}/{path}" + ("" if new else f"/{rid}")
    heading = f"{'Add a' if new else 'Edit the'} {noun}"
    remove = ""
    if not new:
        remove = (f'<details><summary>Remove this {noun}</summary><div>'
                  f'<form method="post" action="/games/{key}/{path}/{rid}/remove">{hidden(csrf=csrf_token(h), rev=rev, uid=uuid.uuid4().hex)}'
                  f'<p>It is removed for everyone, on the deck too, at the next sync.</p>'
                  f'<div class="btns"><button class="danger" type="submit">Yes, remove it</button></div></form></div></details>')
    note_line = ('<p class="dim">It will show as not confirmed until someone confirms it on the deck\'s screen.</p>'
                 if spec["kind"] == "rail_limit" else "")
    body = (f'<p class="line"><a href="/games/{key}">{E(g["data"]["title"])}</a></p><h1>{heading}</h1>{notice}{err}{extra}{note_line}'
            + form_html(h, action, fields, {"rev": rev, "rid": rid, "uid": uuid.uuid4().hex}, "Save", f"/games/{key}#{path}") + remove)
    h.html(200, layout(heading, body, who, "games"))


CHILD_ROUTE = rf"/games/{KEY}/(facts|limits)"


@route("GET", CHILD_ROUTE + "/add")
def child_add(h, m, q):
    who, g, path = h.person(), live_game(h, m.group(1)), m.group(2)
    child_form(h, who, g, path, uuid.uuid4().hex, 0, {"group": "other"}, h.archive.lib.all("document", g["id"]))


@route("GET", CHILD_ROUTE + rf"/{HEX32}")
def child_edit(h, m, q):
    who, g, path, rid = h.person(), live_game(h, m.group(1)), m.group(2), m.group(3)
    spec = CHILDREN[path]
    rec = h.archive.lib.get(spec["kind"], rid)
    if rec is None or rec["data"]["model_key"] != g["id"]:
        raise Refuse(404, f"That {spec['noun']} isn't here any more.")
    child_form(h, who, g, path, rid, rec["rev"], child_values(spec, rec["data"]), h.archive.lib.all("document", g["id"]),
               notice=note(NOTES["changed"], "warn") if (q.get("done") or [""])[0] == "changed" else "")


@route("POST", CHILD_ROUTE + rf"(?:/{HEX32})?")
def child_save(h, m, q):
    who, g, path = h.person(), live_game(h, m.group(1)), m.group(2)
    spec, lib = CHILDREN[path], h.archive.lib
    form = read_form(h)
    rid = m.group(3) or form.get("rid", "")
    docs = lib.all("document", g["id"])
    vals = {f: form.get(f, "") for f in spec["fields"]}
    if not re.fullmatch(r"[0-9a-f]{32}", rid):
        raise Refuse(400, "That form is out of date. Reload it.")
    try:
        rev = int(form.get("rev", ""))
        data = child_data(spec, g["id"], form)
    except (FormError, ValueError) as e:
        return child_form(h, who, g, path, rid, int(form["rev"]) if form.get("rev", "").isdigit() else 0, vals, docs,
                          str(e) if isinstance(e, FormError) else "That form is out of date. Reload it.")
    res = api.apply_writes(lib, [{"kind": spec["kind"], "id": rid, "if_rev": rev, "uid": form.get("uid") or None, "data": data}], actor(who))[0]
    if res["status"] in ("created", "updated", "duplicate"):
        return h.redirect(f"/games/{g['id']}?done=saved#{path}")
    if res["status"] == "conflict":
        cur = res["current"]
        return child_form(h, who, g, path, rid, cur["rev"] if cur else 0, vals, docs,
                          theirs=None if (cur is None or cur["deleted"]) else cur, gone=bool(cur and cur["deleted"]))
    child_form(h, who, g, path, rid, rev, vals, docs, f"Not saved: {res.get('reason', 'the archive refused it')}")


@route("POST", CHILD_ROUTE + rf"/{HEX32}/remove")
def child_remove(h, m, q):
    who, g, path, rid = h.person(), live_game(h, m.group(1)), m.group(2), m.group(3)
    spec = CHILDREN[path]
    form = read_form(h)
    try:
        rev = int(form.get("rev", ""))
    except ValueError:
        raise Refuse(400, "That form is out of date. Reload it.") from None
    rec = h.archive.lib.get(spec["kind"], rid, deleted=True)
    if rec is None or rec["data"]["model_key"] != g["id"]:
        raise Refuse(404, f"That {spec['noun']} isn't here.")
    res = api.apply_writes(h.archive.lib, [{"kind": spec["kind"], "id": rid, "if_rev": rev, "uid": form.get("uid") or None,
                                            "deleted": True}], actor(who))[0]
    if res["status"] in ("updated", "duplicate") or (rec["deleted"]):
        return h.redirect(f"/games/{g['id']}?done=removed#{path}")
    h.redirect(f"/games/{g['id']}/{path}/{rid}?done=changed")


# --- documents ----------------------------------------------------------------------------------------------------------

def pdf_path(h, sha):
    return h.archive.files.path(sha)


def cache_dir(h, sha):
    return os.path.join(h.archive.cfg.data, "cache", "pages", sha)


def page_total(h, doc):
    """Pages in a document: the record's number, else what pdfinfo counts (kept beside the images), else None."""
    if doc["pages"]:
        return doc["pages"]
    sha = doc["sha256"]
    mark = os.path.join(cache_dir(h, sha), "pages")
    try:
        with open(mark, encoding="utf-8") as f:
            return int(f.read())
    except (OSError, ValueError):
        pass
    count = pdf_pages(pdf_path(h, sha))
    if count:
        os.makedirs(cache_dir(h, sha), exist_ok=True)
        with open(mark, "w", encoding="utf-8") as f:
            f.write(str(count))
    return count


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    hit = re.search(r"^Pages:\s+(\d+)", out.stdout, re.MULTILINE)
    return int(hit.group(1)) if hit else None


def page_png(h, sha, n):
    """The PNG of page n, made once with pdftoppm and kept in the cache folder (which can be deleted at any time)."""
    folder = cache_dir(h, sha)
    final = os.path.join(folder, f"{n}.png")
    if os.path.isfile(final):
        return final
    os.makedirs(folder, exist_ok=True)
    prefix = os.path.join(folder, f".{n}-{os.getpid()}-{threading.get_ident()}")
    with RENDERS:
        try:
            subprocess.run(["pdftoppm", "-png", "-r", str(PAGE_DPI), "-f", str(n), "-l", str(n), "-singlefile", pdf_path(h, sha), prefix],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
    if not os.path.isfile(prefix + ".png"):
        return None
    os.replace(prefix + ".png", final)
    return final


def doc_of(h, key, doc_id):
    rec = h.archive.lib.get("document", f"{key}/{doc_id}")
    if rec is None:
        raise Refuse(404, "That document isn't here.")
    return rec


@route("GET", rf"/games/{KEY}/docs/{DOC}")
def doc_view(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    key, rec = g["id"], doc_of(h, m.group(1), m.group(2))
    d = rec["data"]
    head = (f'<p class="line"><a href="/games/{key}">{E(g["data"]["title"])}</a></p><h1>{E(d["title"])}</h1>'
            f'<p class="sub">{E(DOC_KIND_LABEL[d["kind"]])}' + (f' · {E(d["source"])}' if d["source"] else "") + '</p>')
    if not d["sha256"] or h.archive.files.size(d["sha256"]) is None:
        link = f'<p class="line"><a href="{E(d["source_url"])}" rel="noopener noreferrer">Where it can be downloaded</a></p>' if d["source_url"] else ""
        body = (head + '<p class="empty">The archive does not hold the file for this document yet.</p>' + link
                + f'<div class="btns"><a class="btn" href="/games/{key}#documents">Upload it</a></div>')
        return h.html(200, layout(d["title"], body, who, "games"))
    total = page_total(h, d)
    try:
        page = int((q.get("page") or ["1"])[0])
    except ValueError:
        page = 1
    page = max(1, min(page, total)) if total else max(1, page)
    base = f"/games/{key}/docs/{quote(d['doc_id'])}"
    nav = ""
    if total:
        prev = f'<a class="btn" href="{base}?page={page - 1}">Previous page</a>' if page > 1 else ""
        nxt = f'<a class="btn" href="{base}?page={page + 1}">Next page</a>' if page < total else ""
        nav = (f'<div class="btns">{prev}{nxt}</div>'
               f'<form method="get" action="{base}"><label for="pg">Go to page (1 to {total})</label>'
               f'<input id="pg" name="page" type="number" min="1" max="{total}" value="{page}" inputmode="numeric">'
               f'<div class="btns"><button type="submit">Go</button></div></form>')
    body = (head + f'<p>Page {page}' + (f' of {total}' if total else '') + '</p>'
            f'<div class="viewer"><img src="{base}/page/{page}.png" alt="Page {page} of {E(d["title"])}"></div>{nav}'
            f'<div class="btns"><a class="btn" href="/api/v1/files/{d["sha256"]}">Open the PDF</a>'
            f'<a class="btn" href="{base}/edit">Edit this document</a></div>')
    h.html(200, layout(d["title"], body, who, "games"))


@route("GET", rf"/games/{KEY}/docs/{DOC}/page/(\d{{1,5}})\.png")
def doc_page_image(h, m, q):
    h.person()
    live_game(h, m.group(1))
    d = doc_of(h, m.group(1), m.group(2))["data"]
    n = int(m.group(3))
    if not d["sha256"] or h.archive.files.size(d["sha256"]) is None or n < 1:
        raise Refuse(404, "No such page.")
    total = page_total(h, d)
    if total and n > total:
        raise Refuse(404, "No such page.")
    png = page_png(h, d["sha256"], n)
    if png is None:
        raise Refuse(404, "That page could not be drawn. Open the PDF instead.")
    with open(png, "rb") as f:
        h.send_bytes(200, f.read(), "image/png", (("Cache-Control", "private, max-age=3600"),))


def doc_form(h, who, g, rec, vals, error="", theirs=None):
    key, d = g["id"], rec["data"]
    base = f"/games/{key}/docs/{quote(d['doc_id'])}"
    extra = ""
    if theirs is not None:
        extra = note("Someone else saved this document while you were editing it. Your changes are still in the form. "
                     "Saving now replaces their version.", "warn")
    err = note(error, "bad", "alert") if error else ""
    fields = (f_input("title", "Title", vals.get("title", ""), req=True, maxlen=120)
              + f_select("kind", "Kind", list(DOC_KIND_LABEL.items()), vals.get("kind", "manual"))
              + f_input("source", "Where it came from", vals.get("source", ""), maxlen=200))
    remove = (f'<details><summary>Remove this document</summary><div>'
              f'<form method="post" action="{base}/remove">{hidden(csrf=csrf_token(h), rev=rec["rev"], uid=uuid.uuid4().hex)}'
              f'<p>The entry is removed for everyone. The file stays in the archive.</p>'
              f'<div class="btns"><button class="danger" type="submit">Yes, remove it</button></div></form></div></details>')
    body = (f'<p class="line"><a href="{base}">{E(d["title"])}</a></p><h1>Edit the document</h1>{err}{extra}'
            + form_html(h, f"{base}/edit", fields, {"rev": rec["rev"], "uid": uuid.uuid4().hex}, "Save", base) + remove)
    h.html(200, layout("Edit the document", body, who, "games"))


@route("GET", rf"/games/{KEY}/docs/{DOC}/edit")
def doc_edit_form(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    rec = doc_of(h, m.group(1), m.group(2))
    doc_form(h, who, g, rec, {k: rec["data"][k] for k in ("title", "kind", "source")})


@route("POST", rf"/games/{KEY}/docs/{DOC}/edit")
def doc_edit_save(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    rec = doc_of(h, m.group(1), m.group(2))
    form = read_form(h)
    vals = {k: form.get(k, "") for k in ("title", "kind", "source")}
    if not vals["title"]:
        return doc_form(h, who, g, rec, vals, "A document needs a title.")
    try:
        rev = int(form.get("rev", ""))
    except ValueError:
        raise Refuse(400, "That form is out of date. Reload it.") from None
    data = dict(rec["data"], **vals)
    res = api.apply_writes(h.archive.lib, [{"kind": "document", "id": rec["id"], "if_rev": rev, "uid": form.get("uid") or None,
                                            "data": data}], actor(who))[0]
    if res["status"] in ("updated", "duplicate"):
        return h.redirect(f"/games/{g['id']}?done=saved#documents")
    if res["status"] == "conflict" and res["current"] and not res["current"]["deleted"]:
        return doc_form(h, who, g, res["current"], vals, theirs=res["current"])
    doc_form(h, who, g, rec, vals, f"Not saved: {res.get('reason', 'the archive refused it')}")


@route("POST", rf"/games/{KEY}/docs/{DOC}/remove")
def doc_remove(h, m, q):
    who, g = h.person(), live_game(h, m.group(1))
    rec = doc_of(h, m.group(1), m.group(2))
    form = read_form(h)
    try:
        rev = int(form.get("rev", ""))
    except ValueError:
        raise Refuse(400, "That form is out of date. Reload it.") from None
    res = api.apply_writes(h.archive.lib, [{"kind": "document", "id": rec["id"], "if_rev": rev, "uid": form.get("uid") or None,
                                            "deleted": True}], actor(who))[0]
    if res["status"] in ("updated", "duplicate"):
        return h.redirect(f"/games/{g['id']}?done=removed#documents")
    h.redirect(f"/games/{g['id']}/docs/{quote(rec['data']['doc_id'])}/edit")


@route("POST", rf"/games/{KEY}/upload")
def upload(h, m, q):
    """A PDF sent as the request body (the page's script does it), stored by hash and listed as a document of the game."""
    who, g = h.person(), live_game(h, m.group(1))
    check_csrf(h, h.headers.get("X-CSRF"))
    lib, key = h.archive.lib, g["id"]
    title = ((q.get("title") or [""])[0]).strip()[:120]
    kind = ((q.get("kind") or ["manual"])[0]).strip()
    if not title:
        raise Refuse(400, "Give the PDF a title.")
    if kind not in L.DOC_KINDS:
        raise Refuse(400, "Pick one of the kinds in the list.")
    if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/pdf":
        raise Refuse(415, "Send the file as application/pdf.")
    try:
        length = int(h.headers.get("Content-Length", ""))
    except ValueError:
        raise Refuse(411, "Content-Length is required") from None
    if length > UPLOAD_MAX:
        raise Refuse(413, f"A PDF here is at most {UPLOAD_MAX // 1048576} MB.")
    h._read = True                               # the body is consumed (or the connection is dropped) from here on
    try:
        sha, size = h.archive.files.put_stream(h.rfile, length, magic=b"%PDF-")
    except Exception:
        h.close_connection = True                # whatever of the body is left would be read as the next request
        raise
    same = next((d for d in lib.all("document", key) if d["data"]["sha256"] == sha), None)
    if same:
        return h.json(200, {"ok": True, "doc_id": same["data"]["doc_id"], "same": True})
    base = (L.slug(title) or "document")[:60].strip("-") or "document"
    doc_id, n = base, 1
    while lib.get("document", f"{key}/{doc_id}", deleted=True) is not None:
        n += 1
        doc_id = f"{base}-{n}"
    data = {"model_key": key, "doc_id": doc_id, "title": title, "kind": kind, "source_url": None,
            "source": "Uploaded through the archive's pages", "sha256": sha, "pages": pdf_pages(h.archive.files.path(sha)), "bytes": size}
    res = api.apply_writes(lib, [{"kind": "document", "id": f"{key}/{doc_id}", "if_rev": 0, "data": data}], actor(who))[0]
    if res["status"] not in ("created", "duplicate"):
        raise Refuse(409, f"The PDF is stored, but the entry was not saved: {res.get('reason', res['status'])}")
    h.json(200, {"ok": True, "doc_id": doc_id})


# --- the page's own files -----------------------------------------------------------------------------------------------

STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".ttf": "font/ttf",
                ".txt": "text/plain; charset=utf-8"}


def send_static(h, folder, name):
    ctype = STATIC_TYPES.get(os.path.splitext(name)[1])
    path = os.path.join(h.archive.cfg.static, folder, name)
    if ctype is None or not os.path.isfile(path):
        raise Refuse(404, "not found")
    with open(path, "rb") as f:
        h.send_bytes(200, f.read(), ctype, (("Cache-Control", "public, max-age=3600"),))


@route("GET", r"/assets/(archive\.css|archive\.js)")
def asset(h, m, q):
    send_static(h, "", m.group(1))


@route("GET", r"/assets/fonts/([A-Za-z0-9][A-Za-z0-9._-]{0,80})")
def font(h, m, q):
    send_static(h, "fonts", m.group(1))
