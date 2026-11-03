"""A stand-in for archive.org on localhost, and a PDF poppler reads, for the tests of FIND A MANUAL (test-library-web.py,
test-dash.py): advancedsearch, one item's metadata, and downloads (one of them a web page). Nothing leaves the machine."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


def tiny_pdf(text="Widget Pinball operator manual"):
    """A one-page PDF poppler reads, built by hand so the test needs no PDF library."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)


ARCHIVE_SEEN = []                                    # what the fake archive was asked, newest last
GOOD_PDF = tiny_pdf()


def start_archive(port, word="Widget"):
    """The server, running in a thread. A search whose query holds `word` finds the Widget Pinball items; any other finds none."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlsplit(self.path)
            ARCHIVE_SEEN.append(u.path + ("?" + u.query if u.query else ""))
            if u.path == "/advancedsearch.php":
                q = parse_qs(u.query).get("q", [""])[0]
                docs = [{"identifier": "widget-pinball-manual", "title": "Widget Pinball Manual", "downloads": 90},
                        {"identifier": "page-only-item", "title": "A page, not a PDF", "downloads": 3},
                        {"identifier": "bad id with spaces", "title": "refused", "downloads": 1}] if word in q else []
                return self.send(200, json.dumps({"response": {"numFound": len(docs), "docs": docs}}).encode(), "application/json")
            if u.path.startswith("/metadata/"):
                item = unquote(u.path[len("/metadata/"):])
                files = {"widget-pinball-manual": [{"name": "Widget Pinball.pdf", "size": "1200", "format": "Text PDF"},
                                                   {"name": "Widget Pinball_text.pdf", "size": "3400"}, {"name": "scan.jp2", "size": "9999"},
                                                   {"name": "../evil.pdf", "size": "5"}],
                         "page-only-item": [{"name": "page.pdf", "size": "50"}]}.get(item, [])
                return self.send(200, json.dumps({"files": files}).encode(), "application/json")
            if u.path.startswith("/download/"):
                name = unquote(u.path.split("/", 3)[3])
                return self.send(200, GOOD_PDF if name.startswith("Widget Pinball") else b"<html>not a pdf</html>",
                                 "application/pdf" if name.startswith("Widget Pinball") else "text/html")
            self.send(404, b"no", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


