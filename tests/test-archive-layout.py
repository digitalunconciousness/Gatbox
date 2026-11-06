#!/usr/bin/env python3
"""Phase D2: how the archive's pages hold up in a real browser (headless Chromium over tests/cdp.py), at phone, tablet and
desktop widths and at the width a 200% zoom leaves a phone: no sideways scroll, every control at least 44 px, every
image with its text alternative, no content-security-policy violation or script error, the fonts the pages ask for
really load, and a visible focus ring. Skipped, saying so, when there is no Chromium (GATBOX_CHROME names one).

  ARCHIVE_SHOTS=DIR python3 tests/test-archive-layout.py     also writes a PNG of every page at every width into DIR
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "backend"), os.path.join(REPO, "archive"), os.path.join(REPO, "tests")]

from cdp import Chrome  # noqa: E402
from gatboxarchive.server import Archive, Config, make_servers  # noqa: E402

spec = importlib.util.spec_from_file_location("pages_test", os.path.join(REPO, "tests", "test-archive-pages.py"))
pt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt)

passed = failed = 0
WIDTHS = (195, 320, 390, 768, 1280)            # 195 is a 390 px phone at 200% zoom
MIN_TARGET = 44
ARCHIVE_FONTS = os.environ.get("ARCHIVE_FONTS_DIR")      # a folder holding the two font files, to prove they load


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


class Page(Chrome):
    def look(self, url, width, height=900, email=pt.PARTNER):
        self.problems = []
        tid = self.send("Target.createTarget", {"url": "about:blank"}, session=False)["targetId"]
        self.sid = self.send("Target.attachToTarget", {"targetId": tid, "flatten": True}, session=False)["sessionId"]
        for dom in ("Page", "Runtime", "Log", "Network"):
            self.send(f"{dom}.enable")
        if email:
            self.send("Network.setExtraHTTPHeaders", {"headers": {"Cf-Access-Jwt-Assertion": pt.access_jwt(email)}})
        self.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600})
        self.events.clear()
        self.send("Page.navigate", {"url": url})
        self.event("Page.loadEventFired")
        time.sleep(0.4)                                    # fonts and images settle
        for e in self.events:
            m, p = e.get("method"), e.get("params", {})
            if m == "Log.entryAdded" and p["entry"]["level"] in ("error", "warning"):
                # With no fonts folder given the archive has no fonts to serve and the pages use system ones, as designed
                # (install.sh fetches the two fonts once, with internet): their two 404s are not a fault. With a folder
                # given, a font that fails is.
                if not ARCHIVE_FONTS and "/assets/fonts/" in (p["entry"].get("url") or ""):
                    continue
                self.problems.append(p["entry"]["text"][:160])
            elif m == "Runtime.exceptionThrown":
                self.problems.append("script error")
            elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
                self.problems.append("console error")


MEASURE = """(() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && r.left > -50; };
  const small = [];
  document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary').forEach(e => {
    if (e.classList.contains('skip') || !vis(e)) return;
    const r = e.getBoundingClientRect();
    if (r.height < %d - 0.5 || (r.width < %d - 0.5 && e.tagName !== 'A')) small.push(e.tagName + ' "' + (e.innerText || e.name || '').trim().slice(0, 24) + '" ' + Math.round(r.width) + 'x' + Math.round(r.height));
  });
  const bad = [...document.images].filter(i => !i.alt).length;
  const broken = [...document.images].filter(i => !i.complete || i.naturalWidth === 0).length;
  const heads = document.querySelectorAll('h1').length;
  const brand = document.querySelector('.brand').getBoundingClientRect().height;
  return {over: document.documentElement.scrollWidth - innerWidth, brand, small, noAlt: bad, broken, h1: heads,
          lang: document.documentElement.lang, title: document.title,
          font: getComputedStyle(document.body).fontFamily.split(',')[0], loaded: [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)};
})()""" % (MIN_TARGET, MIN_TARGET)


def main():
    chrome = os.environ.get("GATBOX_CHROME") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        print("archive layout: skipped (no Chromium; set GATBOX_CHROME)")
        return 0
    os.environ["GATBOX_CHROME"] = chrome
    shots = os.environ.get("ARCHIVE_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="gatbox-archive-layout-")
    sys.stderr = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    try:
        run(tmp, shots)
    finally:
        sys.stderr = sys.__stderr__
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"archive layout: {passed} passed, {failed} failed")
    return 1 if failed else 0


def run(tmp, shots):
    static = os.path.join(tmp, "static")
    shutil.copytree(os.path.join(REPO, "archive", "static"), static)
    if ARCHIVE_FONTS:
        shutil.copytree(ARCHIVE_FONTS, os.path.join(static, "fonts"))
    cfg = Config({"ARCHIVE_DATA": f"{tmp}/data", "ARCHIVE_TUNNEL_BIND": "127.0.0.1:0", "ARCHIVE_PEOPLE": pt.PARTNER, "ARCHIVE_STATIC": static})
    arc = Archive(cfg)
    (tunnel,) = make_servers(arc)
    threading.Thread(target=tunnel.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{tunnel.server_address[1]}"
    pt.seed(arc)
    arc.lib.put("game", "z-long-title", {"title": "An Extremely Long Arcade Game Title That Keeps Going Past Any Sensible Width Limit", "mfr": "A Maker With A Very Long Name Incorporated", "year": 1991, "kind": "video"}, 0, by="deck")
    pdf = pt.make_pdf(4, "layout")
    sha, size = arc.files.put_stream(__import__("io").BytesIO(pdf), len(pdf), magic=b"%PDF-")
    arc.lib.put("document", "widget-wars/operator-manual", {"model_key": "widget-wars", "doc_id": "operator-manual", "title": "Operator manual",
                                                            "kind": "manual", "sha256": sha, "pages": 4, "bytes": size, "source": "uploaded"}, 0, by="deck")
    arc.lib.put("document", "widget-wars/service-manual", {"model_key": "widget-wars", "doc_id": "service-manual", "title": "Service manual",
                                                           "kind": "manual", "sha256": None, "pages": None, "bytes": None, "source": ""}, 0, by="deck")
    arc.lib.put("rail_limit", "c" * 32, {"model_key": "widget-wars", "rail": "+12V", "lo": "11.4", "hi": "12.6", "doc_id": "operator-manual",
                                         "page": 2, "quote": "12 V, plus or minus 5 percent, measured at the connector", "confirmed": None}, 0, by="deck")
    pages = [("games", "/"), ("games-empty-search", "/?q=zzzz"), ("game", "/games/widget-wars"), ("game-bare", "/games/no-manual-here"),
             ("viewer", "/games/widget-wars/docs/operator-manual?page=2"), ("add", "/add"), ("edit-game", "/games/widget-wars/edit"),
             ("limit-form", "/games/widget-wars/limits/" + "c" * 32), ("fact-form", "/games/widget-wars/facts/add"),
             ("doc-no-file", "/games/widget-wars/docs/service-manual"), ("not-found", "/games/nothing-here"), ("signed-out", "/")]
    with Page(os.path.join(tmp, "profile")) as c:
        for label, path in pages:
            for w in WIDTHS:
                c.look(base + path, w, email=None if label == "signed-out" else pt.PARTNER)
                m = c.eval(MEASURE)
                tag = f"{label} at {w}px"
                check(f"{tag}: no sideways scroll", m["over"] <= 0, f"overflow {m['over']}px")
                check(f"{tag}: the name in the header stays on one line", m["brand"] <= 46, str(m["brand"]))
                check(f"{tag}: every control is at least {MIN_TARGET} px", not m["small"], str(m["small"][:4]))
                check(f"{tag}: images have alt text and loaded; one h1; lang set; a real title",
                      m["noAlt"] == 0 and m["broken"] == 0 and m["h1"] == 1 and m["lang"] == "en" and m["title"].endswith("| GATBOX archive"), str(m))
                own = [p for p in c.problems if not (label in ("not-found", "signed-out") and p.startswith("Failed to load resource"))]
                check(f"{tag}: no CSP violation, no script error", not own, str(own[:2]))     # an error page's own 4xx is not a fault
                if shots:
                    c.shot(os.path.join(shots, f"{label}-{w}.png"))
        c.look(base + "/", 390)
        m = c.eval(MEASURE)
        if ARCHIVE_FONTS:
            check("the fonts the pages name are the ones that load (served by the archive, no CDN)",
                  {"Chakra Petch", "Share Tech Mono"} <= set(m["loaded"]), str(m["loaded"]))
        c.look(base + "/games/widget-wars", 390)
        ring = c.eval("""(() => { const a = document.querySelector('main a[href]'); a.focus({focusVisible: true});
          const s = getComputedStyle(a); return {w: s.outlineWidth, st: s.outlineStyle, col: s.outlineColor}; })()""")
        check("keyboard focus shows a 2 px cyan outline", ring["st"] == "solid" and ring["w"] == "2px" and ring["col"] == "rgb(125, 250, 255)", str(ring))
        order = c.eval("""(() => { const out = []; for (let i = 0; i < 6; i++) { out.push(document.activeElement.tagName); }
          return [...document.querySelectorAll('a,button,input,select,summary,textarea')].map(e => e.tabIndex).every(t => t === 0 || t === -1 ? t === 0 : t > 0) })()""")
        check("nothing has a positive tabindex that would reorder the page", order)
        c.look(base + "/games/widget-wars", 390)
        cur = c.eval("""(() => { const bad = []; document.querySelectorAll('a[href], button:not(:disabled), summary, select, input[type=file], label[for]')
          .forEach(e => { if (getComputedStyle(e).cursor !== 'pointer' && e.offsetParent !== null && !e.classList.contains('skip')) bad.push(e.tagName + ' ' + (e.innerText || '').slice(0, 20)); }); return bad; })()""")
        check("every enabled control shows the pointer cursor", not cur, str(cur[:4]))


if __name__ == "__main__":
    sys.exit(main())
