/* GATBOX chart: one series of meter readings over time, on a canvas. No libraries (hard rule 10).
 *
 *   const c = new Chart(canvas, {onSelect, onView});
 *   c.setData(samples)      samples: [{t (epoch s), v (base units, null = OL), raw, unit, mode, alarm}], oldest first
 *   c.push(sample)          live: one more reading
 *   c.setBand([lo, hi]|null) the profile / machine window (a lime wash), c.setLimit(v|null) the over-voltage line
 *   c.setMarks([{t, label, source}])
 *   c.live() / c.fit() / c.zoom(f)
 *
 * Touch: tap = the reading nearest in time (crosshair + readout); drag = pan (leaves LIVE); two fingers = zoom;
 * double tap = back to LIVE (or fit, for a past session). Mouse: wheel zooms, drag pans.
 * Marks follow the dataviz rules: 2px line, recessive hairline grid, text in text colours (never the series
 * colour), SPIKE/ALARM dots with a 2px ring in the surface colour. */
(function () {
  "use strict";
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const PREFIX = [[1e6, "M"], [1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"], [1e-12, "p"]];

  function si(v, base, step) {
    // tick / readout text in the unit's SI prefix; volts stay in V (rails read 4.998 V, not 4998 mV)
    if (v == null || !isFinite(v)) return "OL";
    let k = 1, p = "";
    if (base !== "V" && base !== "%" && base !== "") {
      const a = Math.max(Math.abs(v), Math.abs(step || 0));
      for (const [kk, pp] of PREFIX) if (a >= kk) { k = kk; p = pp; break; }
    }
    const s = step ? Math.abs(step) / k : 0;
    const dec = s ? Math.max(0, Math.min(6, -Math.floor(Math.log10(s) + 1e-9))) : 3;
    return (v / k).toFixed(dec) + (base ? " " + p + base : "");
  }

  function g(v) {                             // like Python's :g: 4.75, 0.15, 1200
    return String(Number(v.toPrecision(6)));
  }
  function range(lo, hi, base) {              // a window as text: "4.75–5.25 V", "1–100 mA", "0–150 mV"
    let k = 1, p = "";
    const a = Math.max(Math.abs(lo), Math.abs(hi));
    if (base !== "%" && base !== "" && !(base === "V" && a >= 1))
      for (const [kk, pp] of PREFIX) if (a >= kk) { k = kk; p = pp; break; }
    const m = x => g(x).replace("-", "−");
    return m(lo / k) + (lo < 0 || hi < 0 ? " – " : "–") + m(hi / k) + (base ? " " + p + base : "");
  }

  function niceStep(span, n) {
    const raw = span / Math.max(1, n), mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
    return 10 * mag;
  }
  const TSTEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200];
  const pad2 = n => String(n).padStart(2, "0");
  function hms(t, withS) {
    const d = new Date(t * 1000);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + (withS ? ":" + pad2(d.getSeconds()) : "");
  }

  class Chart {
    constructor(canvas, opts) {
      this.cv = canvas; this.ctx = canvas.getContext("2d"); this.o = opts || {};
      this.data = []; this.band = null; this.limit = null; this.marks = []; this.base = "V";
      this.follow = !this.o.history; this.span = this.o.span || 300; this.t0 = 0; this.t1 = 0;
      this.sel = null; this.ptrs = new Map(); this.dirty = true;
      this.P = {l: 64, r: 12, t: 62, b: 24};          // the top strip holds the zoom buttons and the view note
      this.ro = new ResizeObserver(() => this.redraw());
      this.ro.observe(canvas);
      canvas.addEventListener("pointerdown", e => this._down(e));
      canvas.addEventListener("pointermove", e => this._move(e));
      ["pointerup", "pointercancel", "pointerleave"].forEach(k => canvas.addEventListener(k, e => this._up(e)));
      canvas.addEventListener("wheel", e => { e.preventDefault(); this._zoomAt(e.deltaY > 0 ? 1.25 : 0.8, e.offsetX); },
                              {passive: false});
      canvas.addEventListener("dblclick", () => this.o.history ? this.fit() : this.live());
      const loop = () => { if (this.dead) return; if (this.dirty) this._draw(); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    // --- data --------------------------------------------------------------------------
    setData(arr) {
      this.data = arr.slice(); this.sel = null;
      if (this.data.length) this.base = this.data[this.data.length - 1].base || this.base;
      if (this.o.history) this.fit(); else this._followNow();
      this.redraw();
    }
    push(s) {
      this.data.push(s);
      if (this.data.length > (this.o.max || 7200)) this.data.shift();
      this.base = s.base || this.base;
      if (this.follow) this._followNow();
      this.redraw();
    }
    setBand(b) { this.band = b; this.redraw(); }
    setLimit(v) { this.limit = v; this.redraw(); }
    setMarks(m) { this.marks = m || []; this.redraw(); }
    escalate(t0) {                             // the live stream only knows a run is an ALARM at its 2nd reading
      for (let i = this.data.length - 1; i >= 0 && this.data[i].t >= t0; i--)
        if (this.data[i].alarm === "spike") this.data[i].alarm = "alarm";
      this.redraw();
    }
    redraw() { this.dirty = true; }
    destroy() { this.dead = true; this.ro.disconnect(); }

    // --- view ---------------------------------------------------------------------------
    _followNow() {
      // live: the chosen span, but a young session grows into it (from 1 min) instead of sitting at the far right
      const last = this.data.length ? this.data[this.data.length - 1].t : Date.now() / 1000;
      const age = this.data.length ? last - this.data[0].t : 0;
      const span = Math.min(this.span, Math.max(60, age * 1.08));
      this.t1 = last + span * 0.02; this.t0 = this.t1 - span;
    }
    live() { this.follow = true; this.sel = null; this._followNow(); this._view(); this.redraw(); }
    fit() {
      if (!this.data.length) return;
      const a = this.data[0].t, b = this.data[this.data.length - 1].t, m = Math.max(1, (b - a) * 0.02);
      this.t0 = a - m; this.t1 = b + m; this.span = this.t1 - this.t0; this.follow = false; this._view(); this.redraw();
    }
    zoom(f) { this._zoomAt(f, null); }
    _zoomAt(f, px) {
      const w = this._plotW(), x = px == null ? (this.follow ? w + this.P.l : this.P.l + w / 2) : px;
      const tc = this.t0 + (x - this.P.l) / w * (this.t1 - this.t0);
      const span = Math.min(Math.max((this.t1 - this.t0) * f, 5), 7 * 86400);
      this.t0 = tc - (tc - this.t0) * span / (this.t1 - this.t0); this.t1 = this.t0 + span; this.span = span;
      if (this.follow) this._followNow();
      this._view(); this.redraw();
    }
    _view() { if (this.o.onView) this.o.onView({follow: this.follow, span: this.t1 - this.t0}); }
    _plotW() { return Math.max(10, this.cv.clientWidth - this.P.l - this.P.r); }
    _plotH() { return Math.max(10, this.cv.clientHeight - this.P.t - this.P.b); }
    _x(t) { return this.P.l + (t - this.t0) / (this.t1 - this.t0) * this._plotW(); }
    _t(x) { return this.t0 + (x - this.P.l) / this._plotW() * (this.t1 - this.t0); }

    // --- touch / mouse ------------------------------------------------------------------
    _down(e) {
      this.cv.setPointerCapture(e.pointerId);
      this.ptrs.set(e.pointerId, {x: e.offsetX, y: e.offsetY, x0: e.offsetX, t0: this.t0, t1: this.t1});
      if (this.ptrs.size === 2) {
        const [a, b] = [...this.ptrs.values()];
        this.pinch = {d: Math.abs(a.x - b.x) || 1, c: (a.x + b.x) / 2, t0: this.t0, t1: this.t1};
      }
      this.moved = false;
    }
    _move(e) {
      const p = this.ptrs.get(e.pointerId);
      if (!p) return;
      p.x = e.offsetX; p.y = e.offsetY;
      if (this.ptrs.size >= 2 && this.pinch) {             // pinch: keep the midpoint's time under the fingers
        const [a, b] = [...this.ptrs.values()], d = Math.abs(a.x - b.x) || 1, c = (a.x + b.x) / 2;
        const w = this._plotW(), pt = this.pinch, span0 = pt.t1 - pt.t0;
        const span = Math.min(Math.max(span0 * pt.d / d, 5), 7 * 86400);
        const tc = pt.t0 + (pt.c - this.P.l) / w * span0;
        this.t0 = tc - (c - this.P.l) / w * span; this.t1 = this.t0 + span; this.span = span;
        this.follow = false; this.moved = true; this._view(); this.redraw();
      } else if (Math.abs(p.x - p.x0) > 8 || this.moved) {  // drag: pan
        const dt = (p.x - p.x0) / this._plotW() * (p.t1 - p.t0);
        this.t0 = p.t0 - dt; this.t1 = p.t1 - dt; this.follow = false; this.moved = true; this._view(); this.redraw();
      }
    }
    _up(e) {
      const p = this.ptrs.get(e.pointerId);
      if (!p) return;
      this.ptrs.delete(e.pointerId);
      if (this.ptrs.size < 2) this.pinch = null;
      const now = performance.now();
      if (!this.moved && e.type === "pointerup") {
        if (now - (this.lastTap || 0) < 300) { this.lastTap = 0; this.o.history ? this.fit() : this.live(); return; }
        this.lastTap = now;
        this._select(p.x);
      }
    }
    _select(x) {
      if (!this.data.length || x < this.P.l) { this.sel = null; this.redraw(); return; }
      const t = this._t(x);
      let lo = 0, hi = this.data.length - 1;                // nearest reading in time: binary search
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (this.data[m].t < t) lo = m; else hi = m; }
      const i = Math.abs(this.data[lo].t - t) <= Math.abs(this.data[hi].t - t) ? lo : hi;
      const s = this.data[i];
      this.sel = (this.sel === s) ? null : s;
      if (this.o.onSelect) this.o.onSelect(this.sel);
      this.redraw();
    }

    // --- drawing -------------------------------------------------------------------------
    _yRange(vis) {
      let lo = Infinity, hi = -Infinity;
      for (const s of vis) if (s.v != null && isFinite(s.v)) { lo = Math.min(lo, s.v); hi = Math.max(hi, s.v); }
      if (this.band) { lo = Math.min(lo, this.band[0]); hi = Math.max(hi, this.band[1]); }
      if (this.limit != null && isFinite(hi) && Math.max(Math.abs(hi), Math.abs(lo)) > Math.abs(this.limit) * 0.97) {
        const L = this.limit; lo = Math.min(lo, L); hi = Math.max(hi, L);
      }
      if (!isFinite(lo)) return [0, 1];
      let span = hi - lo;
      if (span < Math.max(Math.abs(hi), 1e-9) * 0.004) span = Math.max(Math.abs(hi) * 0.004, 1e-6);
      const mid = (hi + lo) / 2, half = span / 2 * 1.12;
      return [mid - half, mid + half];
    }
    _draw() {
      this.dirty = false;
      const cv = this.cv, dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      if (!W || !H) return;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
        cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      }
      const g = this.ctx; g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      const C = {line: css("--line"), fg: css("--fg"), mut: css("--mut"), cyan: css("--cyan"), ok: css("--ok"),
                 bad: css("--bad"), warn: css("--warn"), mag: css("--mag"), surf: css("--panel")};
      const P = this.P, pw = this._plotW(), ph = this._plotH();
      const vis = [];
      for (const s of this.data) if (s.t >= this.t0 && s.t <= this.t1) vis.push(s);
      const [y0, y1] = this._yRange(vis);
      const Y = v => P.t + (1 - (v - y0) / (y1 - y0)) * ph;
      g.font = "12px " + css("--mono"); g.textBaseline = "middle";
      // view note, left of the buttons: what this chart shows right now
      g.fillStyle = C.mut; g.textAlign = "left";
      if (W >= 600) {
      const spanTxt = (this.t1 - this.t0) >= 90 ? Math.round((this.t1 - this.t0) / 60) + " min" : Math.round(this.t1 - this.t0) + " s";
      g.fillText(this.o.history ? `${spanTxt} shown · tap a point for its reading`
                                : this.follow ? `last ${spanTxt} · following · tap a point for its reading`
                                              : `${spanTxt} · paused · LIVE to follow again`, 12, 30);
      }

      // window band (lime wash) and its edges
      if (this.band) {
        const a = Y(this.band[1]), b = Y(this.band[0]);
        g.fillStyle = "rgba(94,242,176,0.10)"; g.fillRect(P.l, a, pw, b - a);
        g.strokeStyle = "rgba(94,242,176,0.45)"; g.lineWidth = 1;
        [a, b].forEach(y => { g.beginPath(); g.moveTo(P.l, Math.round(y) + .5); g.lineTo(P.l + pw, Math.round(y) + .5); g.stroke(); });
      }
      // y grid + labels
      const ys = niceStep(y1 - y0, 4);
      g.strokeStyle = C.line; g.fillStyle = C.mut; g.textAlign = "right";
      for (let v = Math.ceil(y0 / ys) * ys; v <= y1; v += ys) {
        const y = Math.round(Y(v)) + .5;
        g.beginPath(); g.moveTo(P.l, y); g.lineTo(P.l + pw, y); g.stroke();
        g.fillText(si(v, this.base, ys), P.l - 6, y);
      }
      // x grid + labels
      const span = this.t1 - this.t0, want = span / (pw / 110);
      const ts = TSTEPS.find(s => s >= want) || 86400;
      const tz = new Date().getTimezoneOffset() * -60;
      g.textAlign = "center";
      for (let t = Math.ceil((this.t0 + tz) / ts) * ts - tz; t <= this.t1; t += ts) {
        const x = Math.round(this._x(t)) + .5;
        g.strokeStyle = C.line; g.beginPath(); g.moveTo(x, P.t); g.lineTo(x, P.t + ph); g.stroke();
        g.fillStyle = C.mut; g.fillText(hms(t, ts < 60), x, P.t + ph + 13);
      }
      // over-voltage limit (a magnitude: on a negative rail it's drawn below zero)
      if (this.limit != null && this.limit >= y0 && this.limit <= y1) {
        const y = Math.round(Y(this.limit)) + .5;
        g.strokeStyle = C.bad; g.lineWidth = 1; g.beginPath(); g.moveTo(P.l, y); g.lineTo(P.l + pw, y); g.stroke();
        g.fillStyle = C.mut; g.textAlign = "left";
        g.fillText("limit " + si(this.limit, this.base, 0.001), P.l + 6, y + (y > P.t + 14 ? -9 : 9));
      }
      // marks: magenta hairlines, labels in text colour
      g.textAlign = "left";
      for (const m of this.marks) {
        if (m.t < this.t0 || m.t > this.t1) continue;
        const x = Math.round(this._x(m.t)) + .5;
        g.strokeStyle = C.mag; g.lineWidth = 1; g.beginPath(); g.moveTo(x, P.t); g.lineTo(x, P.t + ph); g.stroke();
        g.fillStyle = C.mag; g.fillRect(x - 3, P.t, 6, 6);
        const lab = m.label || "mark", lw = g.measureText(lab).width;
        g.fillStyle = C.fg; g.textAlign = x + 6 + lw > P.l + pw ? "right" : "left";
        g.fillText(lab, g.textAlign === "right" ? x - 6 : x + 6, P.t + 6);
        g.textAlign = "left";
      }
      // the series: 2px, broken at OL and at gaps > 5 s; min/max per pixel column when dense
      g.save(); g.beginPath(); g.rect(P.l, P.t, pw, ph); g.clip();
      g.strokeStyle = C.cyan; g.lineWidth = 2; g.lineJoin = "round"; g.lineCap = "round";
      g.beginPath();
      if (vis.length > pw * 1.5) {
        // dense: one column per pixel (lowest to highest reading in it), joined to the next unless OL / a gap
        const cols = [];
        let c = null, brk = true, prevT = null;
        for (const s of vis) {
          if (s.v == null || !isFinite(s.v)) { brk = true; continue; }
          const cx = Math.round(this._x(s.t)), y = Y(s.v);
          if (prevT != null && s.t - prevT > 5) brk = true;
          prevT = s.t;
          if (c && c.x === cx && !brk) { c.lo = Math.min(c.lo, y); c.hi = Math.max(c.hi, y); c.last = y; continue; }
          c = {x: cx, lo: y, hi: y, first: y, last: y, join: !brk};
          cols.push(c);
          brk = false;
        }
        let p = null;
        for (const k of cols) {
          if (p && k.join) { g.moveTo(p.x, p.last); g.lineTo(k.x, k.first); }
          g.moveTo(k.x, k.lo); g.lineTo(k.x, k.hi + 0.01);
          p = k;
        }
      } else {
        let prev = null;
        for (const s of vis) {
          if (s.v == null || !isFinite(s.v)) { prev = null; continue; }
          const x = this._x(s.t), y = Y(s.v);
          if (prev && s.t - prev.t <= 5) g.lineTo(x, y); else g.moveTo(x, y);
          prev = s;
        }
      }
      g.stroke();
      // SPIKE / ALARM dots (r 4 + 2px surface ring), OL ticks along the top
      for (const s of vis) {
        if (s.alarm === "spike" || s.alarm === "alarm") {
          const x = this._x(s.t), y = Math.min(Math.max(Y(s.v), P.t + 4), P.t + ph - 4);
          g.fillStyle = C.surf; g.beginPath(); g.arc(x, y, 6, 0, 7); g.fill();
          g.fillStyle = s.alarm === "alarm" ? C.bad : C.warn; g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill();
        } else if (s.v == null) {
          const x = this._x(s.t);
          g.fillStyle = C.warn; g.beginPath(); g.moveTo(x - 4, P.t); g.lineTo(x + 4, P.t); g.lineTo(x, P.t + 7); g.fill();
        }
      }
      // live end-dot
      const last = this.data[this.data.length - 1];
      if (!this.o.history && last && last.v != null && last.t >= this.t0 && last.t <= this.t1) {
        const x = this._x(last.t), y = Y(last.v);
        g.fillStyle = C.surf; g.beginPath(); g.arc(x, y, 6, 0, 7); g.fill();
        g.fillStyle = C.cyan; g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill();
      }
      g.restore();
      // selection: crosshair + readout (value first, then time and mode)
      if (this.sel && this.sel.t >= this.t0 && this.sel.t <= this.t1) {
        const s = this.sel, x = Math.round(this._x(s.t)) + .5;
        g.strokeStyle = "rgba(236,227,255,0.55)"; g.lineWidth = 1;
        g.beginPath(); g.moveTo(x, P.t); g.lineTo(x, P.t + ph); g.stroke();
        if (s.v != null) {
          const y = Y(s.v);
          g.fillStyle = C.surf; g.beginPath(); g.arc(x, y, 7, 0, 7); g.fill();
          g.fillStyle = C.fg; g.beginPath(); g.arc(x, y, 5, 0, 7); g.fill();
        }
        const val = s.v == null ? "OL" : (s.raw + " " + s.unit).trim();
        const d = new Date(s.t * 1000);
        const sub = hms(s.t, true) + "." + String(d.getMilliseconds()).padStart(3, "0").slice(0, 1) + "  " + (s.label || s.mode || "")
                    + (s.alarm === "alarm" ? "  ALARM" : s.alarm === "spike" ? "  SPIKE" : "");
        g.font = "600 22px " + css("--disp");
        const w1 = g.measureText(val).width;
        g.font = "13px " + css("--mono");
        const w = Math.max(w1, g.measureText(sub).width) + 20, h = 52;
        let bx = x + 10; if (bx + w > P.l + pw) bx = x - 10 - w;
        const by = P.t + 8;
        g.fillStyle = "rgba(21,10,40,0.92)"; g.strokeStyle = C.line; g.lineWidth = 1;
        g.beginPath(); g.roundRect(bx, by, w, h, 6); g.fill(); g.stroke();
        g.textAlign = "left"; g.fillStyle = C.fg; g.font = "600 22px " + css("--disp"); g.fillText(val, bx + 10, by + 17);
        g.fillStyle = C.mut; g.font = "13px " + css("--mono"); g.fillText(sub, bx + 10, by + 39);
      }
    }
  }
  window.GChart = Chart;
  window.GFormat = {si, hms, range};
})();
