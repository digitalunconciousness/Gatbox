"""Manuals (2026-09-29): each machine's documents as PDFs in /srv/gatbox/manuals/<slug>/, and its spec sheet.

Where the PDFs come from: gatbox-manuals fetches the list (gatbox-manuals.json: git-ignored, it's the floor) from the
public archives; the owner drops her own in the folder, or uploads one from her phone. Next to a fetched or uploaded
<name>.pdf: <name>.json (title, kind, source, sha256, pages) and <name>.txt (its text, one page per form feed). A PDF
dropped in by hand has neither: its title is its file name, its page count and text are made here and cached.

gatbox-web lists them, serves them, draws one page as a PNG (pdftoppm, cached, sized for the screen) and searches
their text. None of it is in git: the documents are copyrighted.

The spec sheet, per machine:
  * from the manuals list: rail limits read off a manual page ({rail, lo, hi, doc, page, quote}: the manual's own
    words), and the rest of the spec page ({what, value, doc, page, quote}: fuses, line voltage, monitor...);
  * CONFIRMed rail limits: the owner checked the page. Only then do they reach the logger's window (hard rule 12).
    The API only confirms a limit that's on the list, never numbers sent to it;
  * actual values from the field: what this machine really runs at ("boosted to 5.20 V"), typed or taken from the
    meter, with a note, and optionally a window of her own for that rail on that machine. The manual's numbers stay
    as they are, shown beside it. Both state files live in gatbox-web's state dir (profiles.SPECS_CONFIRMED,
    profiles.ACTUALS); gatboxlib.profiles.machine_specs() layers them for the logger.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid

from gatboxlib import profiles

from . import config, roster
from .meter import Bad

KINDS = ["manual", "schematics", "parts", "kit", "bulletin", "other", "yours"]
LIST = "gatbox-manuals.json"
RAIL = re.compile(r"^[+-]\d{1,2}(\.\d)?V$")                  # "+5V", "-5V", "+12V", "+3.3V"
WIDTHS = (800, 1200, 1600, 2400)                            # page images come in these widths only
CACHE_MAX = 1024 ** 3                                       # page images + text for dropped PDFs: ~1 GB, oldest out
_locks, _locks_lock, _state_lock = {}, threading.Lock(), threading.Lock()
_info = {}                                                  # (path, size, mtime) -> pages, for PDFs without a sidecar


def _text(x, what, limit, required=False):
    if x is None and not required:
        return ""
    if not isinstance(x, str):
        raise Bad(400, f"{what}: text")
    x = " ".join(x.split())
    if not x.isprintable() or len(x) > limit:
        raise Bad(400, f"{what}: at most {limit} printable characters")
    if required and not x:
        raise Bad(400, f"{what}: required")
    return x


def _num(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 50:
        raise Bad(400, f"{what}: a number of volts (at most 50)")
    return round(float(x), 4)


# --- the documents --------------------------------------------------------------------------------------
def _slug(slug):
    if slug not in (roster.slugs() or set()):
        raise Bad(404, f"not in the roster: {slug!r}")
    return slug


def _listed(slug=None):
    d = profiles._load(os.path.join(profiles.data_dir(), LIST)) or {}
    m = d.get("machines") if isinstance(d.get("machines"), dict) else {}
    return m if slug is None else (m.get(slug) or {})


def _files(slug):
    try:
        return sorted(n for n in os.listdir(os.path.join(config.MANUALS, slug))
                      if n.lower().endswith(".pdf") and not n.startswith(".") and "/" not in n)
    except OSError:
        return []


def path(slug, name):
    """A PDF of this machine's, by its file name exactly as listed in its folder (nothing else becomes a path)."""
    _slug(slug)
    if name not in _files(slug):
        raise Bad(404, f"no such document: {name!r}")
    return os.path.join(config.MANUALS, slug, name)


def _sidecar(p):
    try:
        with open(p[:-4] + ".json", encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(p):
    st = os.stat(p)
    return hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16], st


def pages_of(p):
    k, _ = _key(p)
    if k not in _info:
        try:
            r = subprocess.run(["pdfinfo", p], capture_output=True, text=True, timeout=30)
            m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
            _info[k] = int(m.group(1)) if m else 0
        except (OSError, subprocess.SubprocessError):
            _info[k] = 0
    return _info[k]


def docs(slug):
    """GET /api/manuals/<slug>: the documents in its folder (the list's titles and kinds where it has them) and the
    list's entries not fetched yet."""
    _slug(slug)
    entry = _listed(slug)
    by_id = {d.get("id"): d for d in entry.get("docs", []) if isinstance(d, dict)}
    out = []
    for n in _files(slug):
        p = os.path.join(config.MANUALS, slug, n)
        side, stem = _sidecar(p), n[:-4]
        li = by_id.get(side.get("id") or stem, {})
        _, st = _key(p)
        out.append({"file": n, "id": side.get("id") or stem, "title": li.get("title") or side.get("title") or stem,
                    "kind": li.get("kind") or side.get("kind") or "yours", "pages": side.get("pages") or pages_of(p),
                    "bytes": st.st_size, "added": side.get("added", "folder"), "source": li.get("source") or side.get("source"),
                    "date": side.get("date")})
    here = {d["id"] for d in out}
    missing = [{"id": i, "title": d.get("title"), "kind": d.get("kind")} for i, d in by_id.items() if i not in here]
    order = {k: i for i, k in enumerate(KINDS)}
    out.sort(key=lambda d: (order.get(d["kind"], len(KINDS)), d["title"].lower()))
    return {"slug": slug, "docs": out, "not_fetched": missing, "why": entry.get("why", ""), "note": entry.get("note", ""),
            "kinds": KINDS, "upload_max": config.UPLOAD_MAX}


def overview():
    """GET /api/manuals: documents per machine (for the pick list)."""
    L = _listed()
    return {s: {"docs": len(_files(s)), "listed": len((L.get(s) or {}).get("docs", []))} for s in sorted(roster.slugs() or [])}


# --- pages and text (cached) ------------------------------------------------------------------------------
def _cache(k):
    d = os.path.join(config.CACHE, "manuals", k)
    os.makedirs(d, exist_ok=True)
    return d


def _trim():
    root = os.path.join(config.CACHE, "manuals")
    files = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
    total = sum(s for _, s, _ in files)
    for _, s, p in sorted(files):
        if total <= CACHE_MAX:
            break
        try:
            os.remove(p)
            total -= s
        except OSError:
            pass


def _lock(k):
    with _locks_lock:
        return _locks.setdefault(k, threading.Lock())


def page_png(slug, name, page, width):
    """One page as a PNG, `width` px wide (snapped to WIDTHS): pdftoppm, cached."""
    p = path(slug, name)
    try:
        page, width = int(page), int(width or 1200)
    except ValueError:
        raise Bad(400, "page and w: whole numbers")
    n = _sidecar(p).get("pages") or pages_of(p)
    if not 1 <= page <= max(n, 1):
        raise Bad(404, f"page {page}: this document has {n}")
    w = min(WIDTHS, key=lambda x: abs(x - width))
    k, _ = _key(p)
    out = os.path.join(_cache(k), f"{page}-{w}.png")
    with _lock(out):
        if not os.path.exists(out):
            base = out[:-4] + ".tmp"
            try:
                r = subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-scale-to-x", str(w), "-scale-to-y",
                                    "-1", "-png", "-singlefile", p, base], capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                raise Bad(504, "that page took too long to draw")
            if r.returncode or not os.path.exists(base + ".png"):
                raise Bad(500, f"pdftoppm: {(r.stderr or '').strip()[:200] or 'no image'}")
            os.replace(base + ".png", out)
            _trim()
    os.utime(out)                                        # recently used: kept longest
    with open(out, "rb") as f:
        return f.read()


def pages_text(slug, name):
    """The document's text, one string per page: <name>.txt next to it, else pdftotext into the cache."""
    p = path(slug, name)
    txt = p[:-4] + ".txt"
    if not (os.path.exists(txt) and os.path.getmtime(txt) >= os.path.getmtime(p)):
        k, _ = _key(p)
        txt = os.path.join(_cache(k), "text.txt")
        with _lock(txt):
            if not os.path.exists(txt):
                try:
                    subprocess.run(["pdftotext", "-layout", p, txt + ".tmp"], capture_output=True, timeout=180)
                    os.replace(txt + ".tmp", txt)
                except (OSError, subprocess.SubprocessError):
                    return []
    with open(txt, encoding="utf-8", errors="replace") as f:
        return f.read().split("\f")


def search(slug, name, q):
    """GET /api/manuals/<slug>/search?file=&q=: the pages that have q (any case), with a line of context."""
    q = _text(q, "q", 60, required=True)
    if len(q) < 2:
        raise Bad(400, "q: at least 2 characters")
    hits, ql, pages = [], q.lower(), pages_text(slug, name)
    for i, t in enumerate(pages, 1):
        j = t.lower().find(ql)
        if j >= 0:
            a = t.rfind("\n", 0, j) + 1
            b = t.find("\n", j)
            hits.append({"page": i, "snippet": " ".join(t[a:b if b >= 0 else None].split())[:160],
                         "count": t.lower().count(ql)})
            if len(hits) >= 50:
                break
    return {"q": q, "file": name, "hits": hits, "text": any(x.strip() for x in pages)}


def upload(slug, stream, n, title, kind):
    """POST /api/manuals/<slug>?title=&kind= (the body is the PDF itself): into the machine's folder, never over a
    file that's there, with a sidecar and its text."""
    _slug(slug)
    if n <= 0:
        raise Bad(400, "empty upload")
    if n > config.UPLOAD_MAX:
        raise Bad(413, f"at most {config.UPLOAD_MAX // (1024 * 1024)} MB")
    title = _text(title, "title", 120) or "Uploaded document"
    kind = kind if kind in KINDS else "yours"
    d = os.path.join(config.MANUALS, slug)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o2775)                              # the owner (group gatbox-manuals) can add files here too
    except OSError:
        pass
    tmp, h, left = os.path.join(d, f".upload-{uuid.uuid4().hex[:12]}.tmp"), hashlib.sha256(), n
    try:
        with open(tmp, "wb") as f:
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    raise Bad(400, "the upload stopped part way")
                f.write(chunk)
                h.update(chunk)
                left -= len(chunk)
        with open(tmp, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise Bad(400, "not a PDF")
        pages = pages_of(tmp)
        if not pages:
            raise Bad(400, "a PDF this Pi can't read (pdfinfo found no pages)")
        stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "upload"
        name, i = f"{stem}.pdf", 2
        while os.path.exists(os.path.join(d, name)):
            name, i = f"{stem}-{i}.pdf", i + 1
        final = os.path.join(d, name)
        os.chmod(tmp, 0o664)
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    side = {"id": name[:-4], "title": title, "kind": kind, "pages": pages, "bytes": n, "sha256": h.hexdigest(),
            "added": "upload", "date": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with open(final[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f, indent=1)
    try:
        subprocess.run(["pdftotext", "-layout", final, final[:-4] + ".txt"], capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        pass                                             # search makes it later, into the cache
    for x in (final[:-4] + ".json", final[:-4] + ".txt"):
        try:
            os.chmod(x, 0o664)
        except OSError:
            pass
    return dict(side, file=name)


# --- the spec sheet ---------------------------------------------------------------------------------------
def _state(name):
    return profiles._state_json(name, config.CTRL)


def _save(name, d):
    p = os.path.join(config.CTRL, name)
    os.makedirs(config.CTRL, exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
        f.write("\n")
    os.replace(p + ".tmp", p)


def _titles(slug):
    t = {d.get("id"): d.get("title") for d in _listed(slug).get("docs", []) if isinstance(d, dict)}
    for d in docs(slug)["docs"]:
        t.setdefault(d["id"], d["title"])
    return t


def sheet(slug):
    """GET /api/specs/<slug>: per rail, the manual's limits (page, words, confirmed or not), the specs file's, the
    actual value from the field, and the window in use; the spec page's other facts, each with its actual."""
    _slug(slug)
    specs = _listed(slug).get("specs") or {}
    conf, act = _state(profiles.SPECS_CONFIRMED).get(slug) or {}, _state(profiles.ACTUALS).get(slug) or {}
    file_spec = ((profiles._load(os.path.join(profiles.data_dir(), "gatbox-machine-specs.json")) or {})
                 .get("machines", {}).get(slug) or {})
    eff = profiles.machine_specs().get(slug) or {}
    titles = _titles(slug)
    rails = {}
    for r in specs.get("rails", []):
        if isinstance(r, dict) and RAIL.match(str(r.get("rail", ""))):
            c = conf.get(r["rail"]) or {}
            mine = c.get("doc") == r.get("doc") and c.get("page") == r.get("page") \
                and [c.get("lo"), c.get("hi")] == [r.get("lo"), r.get("hi")]
            rails.setdefault(r["rail"], {"rail": r["rail"], "manual": []})["manual"].append(
                dict(r, title=titles.get(r.get("doc"), r.get("doc")), confirmed=bool(mine)))
    for rail, w in (file_spec.get("rails") or {}).items():
        rails.setdefault(rail, {"rail": rail, "manual": []})["file"] = {"window": w, "source": file_spec.get("source")}
    for rail in list(conf) + list((act.get("rails") or {})):
        rails.setdefault(rail, {"rail": rail, "manual": []})
    for rail, x in rails.items():
        x["actual"] = (act.get("rails") or {}).get(rail)
        x["window"] = (eff.get("rails") or {}).get(rail)
        x["source"] = (eff.get("sources") or {}).get(rail) or ("specs file" if x["window"] else None)
        if conf.get(rail) and not any(m["confirmed"] for m in x["manual"]):
            x["confirmed_elsewhere"] = conf[rail]       # confirmed from a list entry that has since changed
    facts = [dict(f, title=titles.get(f.get("doc"), f.get("doc")), actual=(act.get("facts") or {}).get(f.get("what")))
             for f in specs.get("sheet", []) if isinstance(f, dict) and f.get("what")]
    listed = {f["what"] for f in facts}
    facts += [{"what": w, "value": None, "actual": a} for w, a in (act.get("facts") or {}).items() if w not in listed]
    order = lambda r: (0 if r.startswith("+") else 1, float(re.sub(r"[^0-9.]", "", r) or 0))   # noqa: E731
    return {"slug": slug, "rails": [rails[r] for r in sorted(rails, key=order)], "facts": facts}


def confirm(slug, body):
    """POST /api/specs/<slug>/confirm {rail, doc, page}: the owner checked that page. Only a limit on the list."""
    _slug(slug)
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    rail, doc, page = body.get("rail"), body.get("doc"), body.get("page")
    p = next((r for r in (_listed(slug).get("specs") or {}).get("rails", [])
              if isinstance(r, dict) and r.get("rail") == rail and r.get("doc") == doc and r.get("page") == page), None)
    if p is None:
        raise Bad(400, "no such limit on this machine's spec sheet (rail, doc and page must match one)")
    if not (isinstance(p.get("lo"), (int, float)) and isinstance(p.get("hi"), (int, float)) and p["lo"] < p["hi"]):
        raise Bad(400, "that entry has no usable limits")
    with _state_lock:
        d = _state(profiles.SPECS_CONFIRMED)
        d.setdefault(slug, {})[rail] = {"lo": p["lo"], "hi": p["hi"], "doc": doc, "page": page,
                                        "source": f"{_titles(slug).get(doc, doc)} p.{page}",
                                        "confirmed": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _save(profiles.SPECS_CONFIRMED, d)
    return d[slug][rail]


def unconfirm(slug, body):
    _slug(slug)
    rail = body.get("rail") if isinstance(body, dict) else None
    with _state_lock:
        d = _state(profiles.SPECS_CONFIRMED)
        if not (d.get(slug) or {}).pop(rail, None):
            raise Bad(404, f"{rail!r} isn't confirmed on {slug}")
        if not d[slug]:
            del d[slug]
        _save(profiles.SPECS_CONFIRMED, d)


def set_actual(slug, body):
    """PUT /api/actuals/<slug>: {rail, value, window?, note?, from?} or {fact, value, note?}."""
    _slug(slug)
    if not isinstance(body, dict):
        raise Bad(400, "expected a JSON object")
    note = _text(body.get("note"), "note", 200)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if "rail" in body:
        rail = body["rail"]
        if not isinstance(rail, str) or not RAIL.match(rail):
            raise Bad(400, 'rail: like "+5V", "-5V", "+12V"')
        v = _num(body.get("value"), "value")
        if (v < 0) != rail.startswith("-"):
            raise Bad(400, f"value: {rail} is {'negative' if rail.startswith('-') else 'positive'}")
        w = body.get("window")
        if w is not None:
            if not isinstance(w, list) or len(w) != 2:
                raise Bad(400, "window: [lo, hi] or null")
            w = [_num(w[0], "window lo"), _num(w[1], "window hi")]
            if w[0] >= w[1]:
                raise Bad(400, "window: lo must be below hi")
        rec = {"value": v, "window": w, "note": note, "from": "meter" if body.get("from") == "meter" else "typed",
               "date": stamp}
        with _state_lock:
            d = _state(profiles.ACTUALS)
            d.setdefault(slug, {}).setdefault("rails", {})[rail] = rec
            _save(profiles.ACTUALS, d)
        return {"rail": rail, **rec}
    if "fact" in body:
        what = _text(body.get("fact"), "fact", 60, required=True)
        rec = {"value": _text(body.get("value"), "value", 120, required=True), "note": note, "date": stamp}
        with _state_lock:
            d = _state(profiles.ACTUALS)
            d.setdefault(slug, {}).setdefault("facts", {})[what] = rec
            _save(profiles.ACTUALS, d)
        return {"fact": what, **rec}
    raise Bad(400, "expected {rail, value, ...} or {fact, value, ...}")


def clear_actual(slug, body):
    """DELETE /api/actuals/<slug> {rail} or {fact}."""
    _slug(slug)
    if not isinstance(body, dict) or not ({"rail", "fact"} & set(body)):
        raise Bad(400, "expected {rail} or {fact}")
    group, key = ("rails", body["rail"]) if "rail" in body else ("facts", body["fact"])
    with _state_lock:
        d = _state(profiles.ACTUALS)
        if not ((d.get(slug) or {}).get(group) or {}).pop(key, None):
            raise Bad(404, f"no actual for {key!r} on {slug}")
        _save(profiles.ACTUALS, d)
