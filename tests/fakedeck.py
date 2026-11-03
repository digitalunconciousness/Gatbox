"""The invented deck the library's tests run against: a roster, a manuals list with sidecars and a hand-dropped PDF, the
specs file, a confirmed limit, MAME lists and a dump archive (Widget Wars; nothing real). Shared by test-seed.py and
test-library-web.py."""
import hashlib
import json
import os
import zlib


def write(path, data, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w") as f:
        f.write(data if binary else (data if isinstance(data, str) else json.dumps(data)))


def build_deck(T, stale=True):
    """The files the seed reads, as they'd be on the Pi. `stale`: also a confirmed limit nothing on the list matches
    (+3.3V) and one confirmed at other numbers than the list now has (+12V); the equivalence tests leave them out."""
    write(f"{T}/data/gatbox-barcade-roster.json", {
        "meta": {}, "video_games": [
            {"slug": "widget-wars", "name": "Widget Wars", "mfr": "Widgetco", "platform": "x"},
            {"slug": "widget-wars-cocktail", "name": "Widget Wars Cocktail", "mfr": "Widgetco", "platform": "x"},
            {"slug": "gizmo-racer", "name": "Gizmo Racer", "mfr": "Gizmo Inc", "platform": "x"},
            {"slug": "ws-9", "name": "Sprocket & Cog", "mfr": "", "platform": "x"}],
        "pinball": [{"slug": "pin-widget-pinball", "name": "Widget Pinball", "mfr": "Widgetco", "platform": "y"}], "retired": []})
    pdf = b"%PDF-1.4 widget wars manual bytes"
    write(f"{T}/manuals/widget-wars/widget-wars-manual.pdf", pdf, True)
    write(f"{T}/manuals/widget-wars/widget-wars-manual.json", {
        "id": "widget-wars-manual", "title": "Widget Wars Operations Manual", "kind": "manual", "url": "https://archive.example/ww.pdf",
        "source": "an archive", "sha256": hashlib.sha256(pdf).hexdigest(), "pages": 3, "added": "fetch"})
    write(f"{T}/manuals/widget-wars/My Notes (1).pdf", b"%PDF-1.4 hand dropped", True)         # no sidecar, a name an id can't have
    write(f"{T}/data/gatbox-manuals.json", {"machines": {
        "widget-wars": {"docs": [{"id": "widget-wars-manual", "title": "Widget Wars Operations Manual", "kind": "manual",
                                  "url": "https://archive.example/ww.pdf", "source": "an archive"},
                                 {"id": "widget-wars-schematics", "title": "Schematics", "kind": "schematics",
                                  "url": "http://insecure.example/s.pdf"}],
                        "why": "the schematics are only on a site that doesn't serve plain PDFs",
                        "specs": {"rails": [{"rail": "+5V", "lo": 4.75, "hi": 5.25, "doc": "widget-wars-manual", "page": 2,
                                             "quote": "+5 VDC 4.75 to 5.25 V"},
                                            {"rail": "+12V", "lo": 11.4, "hi": 12.6, "doc": "widget-wars-manual", "page": 2,
                                             "quote": "+12 VDC"},
                                            {"rail": "nonsense", "lo": 1, "hi": 2, "doc": "x", "page": 1, "quote": ""}],
                                  "sheet": [{"what": "Fuse F1", "value": "5 A slow-blow", "doc": "widget-wars-manual", "page": 2,
                                             "quote": "FUSE F1 5A SLO-BLO"},
                                            {"what": "Monitor", "value": "19 inch raster", "doc": None, "page": None, "quote": ""}]}},
        "widget-wars-cocktail": {"docs": [], "note": "Cocktail table too"},
        "ghost-machine": {"docs": [], "why": "not on the roster"}}})
    write(f"{T}/data/gatbox-machine-specs.json", {"meta": {"updated": "2026-09-26"}, "machines": {
        "gizmo-racer": {"rails": {"+5V": [4.9, 5.1]}, "source": "Gizmo Racer manual, DC limits (per the owner)"}}})
    conf = {"+5V": {"lo": 4.75, "hi": 5.25, "doc": "widget-wars-manual", "page": 2, "source": "Widget Wars Operations Manual p.2",
                    "confirmed": "2026-10-01T12:00:00+0000"}}
    if stale:
        conf["+12V"] = {"lo": 11.4, "hi": 12.5, "doc": "widget-wars-manual", "page": 2, "confirmed": "2026-10-01T12:00:00+0000"}
        conf["+3.3V"] = {"lo": 3.2, "hi": 3.4, "doc": "gone", "page": 1, "confirmed": "2026-10-01T12:00:00+0000"}
    write(f"{T}/state/machine-specs-confirmed.json", {"widget-wars": conf})
    write(f"{T}/data/gatbox-mame-sets.json", {"machines": {
        "widget-wars": {"set": "widgetw", "status": "sure"}, "gizmo-racer": {"set": None, "status": "none", "why": "PC-based"}}})
    img = os.urandom(2048)
    sha1, crc = hashlib.sha1(img).hexdigest(), f"{zlib.crc32(img) & 0xffffffff:08x}"
    rom = {"name": "ww1.u10", "size": 2048, "crc": crc, "sha1": sha1, "region": "user1"}
    write(f"{T}/mame-roms.json", {"meta": {"mame": "0.276 (mame0276)"}, "machines": {
        "widget-wars": {"set": "widgetw", "status": "sure", "why": "", "prefer": "widgetw", "versions": [
            {"name": "widgetw", "desc": "Widget Wars (version 1.2)", "year": "1983", "mfr": "Widgetco", "parent": True,
             "roms": [rom], "disks": [{"name": "widgetw", "sha1": "e" * 40, "region": "ide:0:hdd"}]}]},
        "gizmo-racer": {"set": None, "status": "none", "why": "PC-based"}}})
    write(f"{T}/roms/widget-wars/WW-U10_{sha1[:8]}.bin", img, True)
    write(f"{T}/roms/widget-wars/WW-U10_{sha1[:8]}.json", {
        "label": "WW-U10", "part": "27C040@DIP32", "machine": "widget-wars", "size": 2048, "sha1": sha1, "crc32": crc,
        "romident": {"match": True, "matches": [{"set": "widgetw", "rom": "ww1.u10", "description": "x"}]}})
    write(f"{T}/roms/unknown-board/NOBIN_aaaaaaaa.json", {"label": "NOBIN", "sha1": "a" * 40, "crc32": "0" * 8, "size": 1})
    write(f"{T}/roms/_images/loose-image.bin", b"loose image bytes", True)
    return img
