/* GATBOX dashboard (M5): the 7" kiosk's main screen, also usable on a phone.
 * Live data comes from gatbox-web's SSE stream (/api/rail/live); everything else from the JSON API. No framework,
 * no CDN, no browser storage (hard rule 10): state lives in this page's memory, so a reload starts clean (and may
 * show a latched over-voltage alarm again, which is fine). No simulated data anywhere: an empty panel says why. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const add = (parent, ...kids) => { kids.forEach(k => k && parent.appendChild(k)); return parent; };
  const clear = e => { while (e.firstChild) e.removeChild(e.firstChild); return e; };
  const {si, hms, range} = window.GFormat;

  const WARN = {HOLD: ["HOLD", "the meter repeats one frozen reading"],
                REL: ["REL (Δ)", "readings are offsets, not the real value"],
                MAX: ["MAX/MIN", "the meter sends its max or min, not the live value"],
                MIN: ["MAX/MIN", "the meter sends its max or min, not the live value"]};
  const BASE = {VDC: "V", VAC: "V", VACDC: "V", DIODE: "V", ADC: "A", AAC: "A", OHM: "Ω", CAP: "F", HZ: "Hz",
                DUTY: "%", CONT: ""};
  const LIVE_S = 10;

  const S = {
    st: null, profiles: [], modes: {}, sys: null, dev: null, local: false, offset: 0,
    file: null, last: null, lastAt: 0, marks: [], pending: [], events: [],
    alarmShown: null, alarmAck: new Set(), leadWarn: false, view: "meter", connected: false, fileStart: null, endedAt: 0,
  };

  // --- API ---------------------------------------------------------------------------------
  async function api(method, path, body) {
    const opt = {method, headers: {}};
    if (method !== "GET") { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body || {}); }
    const r = await fetch(path, opt);
    let d = null;
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((d && d.error) || ("HTTP " + r.status));
    return d;
  }
  function toast(text, cls, ms) {
    const t = $("#toast");
    t.textContent = text; t.className = cls || "";
    clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.add("hide"), ms || 4000);
  }
  const fail = e => toast(String(e.message || e), "bad", 6000);

  // --- helpers ------------------------------------------------------------------------------
  const logging = () => !!S.file && S.last && (Date.now() - S.lastAt) / 1000 < LIVE_S;
  const prof = () => (S.st && S.st.profile) || {};
  const modeLabel = k => (S.modes[k] && S.modes[k].label) || k || "";
  const dialOf = k => (S.modes[k] && S.modes[k].dial) || k;
  const profBase = p => BASE[(p.modes || [])[0]] ?? "V";
  const dialOk = () => { const p = prof(); return !S.last ? null : (!(p.modes || []).length || p.modes.includes(S.last.mode)); };
  const fmtWin = (w, base) => w ? range(w[0], w[1], base) : "no window";
  function sourceText(src) {
    if (!src) return "";
    if (src.startsWith("machine:")) return "machine spec";
    return {profile: "profile", user: "your value"}[src] || src;
  }
  function fileStart(name) {                  // rail_YYYYmmdd_HHMMSS[_N].csv -> epoch (Pi local time)
    const m = /^rail_(\d{4})(\d\d)(\d\d)_(\d\d)(\d\d)(\d\d)/.exec(name || "");
    return m ? new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() / 1000 : null;
  }
  function dur(s) {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
  }
  const chartSample = s => ({t: s.epoch, v: s.v, raw: s.raw, unit: s.unit, mode: s.mode, label: s.label || modeLabel(s.mode),
                             base: s.base ?? BASE[s.mode] ?? "", alarm: s.alarm});
  const rowSample = r => chartSample({epoch: r[0], raw: r[2], unit: r[3], v: r[4], mode: r[5], alarm: r[6]});

  // --- the live chart -----------------------------------------------------------------------
  const chart = new window.GChart($("#c-live"), {
    onView: v => $('[data-z="live"]').classList.toggle("on", v.follow),
  });
  document.querySelectorAll(".chart-ctl button").forEach(b => b.addEventListener("click", () => {
    const z = b.dataset.z;
    if (z === "in") chart.zoom(0.5); else if (z === "out") chart.zoom(2); else chart.live();
  }));
  function chartDecor() {
    const p = prof(), ok = dialOk();
    chart.setBand(ok !== false && p.window ? p.window : null);
    const a = S.st && S.st.alarm;
    chart.setLimit(a && a.applies && ok !== false ? (p.rail && p.rail.startsWith("-") ? -a.limit : a.limit) : null);
    chart.setMarks(S.marks.concat(S.pending).map(m => ({t: m.epoch, label: m.label || m.source, source: m.source})));
  }

  // --- the stream ---------------------------------------------------------------------------
  function connect() {
    const es = new EventSource("/api/rail/live?backlog=7200");
    const on = (k, f) => es.addEventListener(k, e => { try { f(JSON.parse(e.data)); } catch (x) { console.error(k, x); } });
    on("hello", onHello);
    on("backlog", b => { chart.setData(b.rows.map(rowSample)); $("#c-empty").classList.toggle("hide", b.rows.length > 0); });
    on("sample", onSample);
    on("session", onSession);
    on("alarm", onAlarm);
    on("mark", m => { if (m.pending) S.pending.push(m); else S.marks.push(m); chartDecor();
                      toast(`MARK ${hms(m.epoch, true)}${m.label ? " · " + m.label : ""}${m.pending ? " (goes into the next file)" : ""}`); });
    on("capture", c => toast(`CAPTURED ${c.label}: ${c.display} → ${c.machine}`));
    on("state", st => onState(st));
    on("heartbeat", () => { S.connected = true; renderHeader(); });
    es.addEventListener("resync", () => { es.close(); setTimeout(connect, 300); });
    es.onopen = () => { S.connected = true; renderHeader(); };
    es.onerror = () => { S.connected = false; renderHeader(); };   // EventSource reconnects on its own
  }
  function onHello(m) {
    S.connected = true;
    onState({profile: m.profile, alarm: {on: m.alarm.on, applies: m.alarm.applies, limit: m.alarm.limit},
             machine: m.machine, machine_name: m.machine_name, stopped: m.stopped}, true);
    S.file = m.session.file; S.fileStart = fileStart(S.file);
    S.marks = (m.session.marks || []).slice(); S.pending = (m.marks_pending || []).slice();
    S.events = m.alarm.events || [];
    if (m.logging && m.raw != null) {
      S.last = {epoch: m.epoch, raw: m.raw, unit: m.unit, v: m.value, ol: m.ol, display: m.display, mode: m.mode,
                label: m.mode_label, base: m.base, warn: m.warn, alarm: m.alarm.state, file: m.file};
      S.lastAt = Date.now() - (m.age_s || 0) * 1000;
    }
    const last = S.events.filter(e => e.kind === "alarm").pop();   // a reload may show a latched alarm again
    if (last && S.st.alarm.on) showAlarm(last);
    chartDecor(); renderMeter(); renderHeader();
  }
  function onSample(s) {
    if (s.file !== S.file) { S.file = s.file; S.fileStart = fileStart(s.file); }
    const modeChanged = !S.last || S.last.mode !== s.mode;
    S.last = s; S.lastAt = Date.now();
    chart.push(chartSample(s));
    if (modeChanged) chartDecor();
    $("#c-empty").classList.add("hide");
    const d = $("#h-dot"); d.classList.remove("pulse"); void d.offsetWidth; d.classList.add("pulse");
    renderMeter(); renderHeader();
  }
  function onSession(d) {
    if (d.file) {
      S.file = d.file; S.fileStart = fileStart(d.file); S.marks = d.marks || []; S.events = [];
      S.pending = S.pending.filter(p => !S.marks.some(m => Math.abs(m.epoch - p.epoch) < 0.01));
      chart.setData([]); $("#c-empty").textContent = "Waiting for readings…";
    } else {
      S.file = null; S.endedAt = Date.now();
      $("#c-empty").textContent = "Logging ended. The last session stays on the chart until the next one starts.";
    }
    chartDecor(); renderMeter(); renderHeader();
    if (S.view === "sessions") loadSessions();
  }
  function onState(st, quiet) {
    const was = prof();
    S.st = Object.assign({}, S.st || {}, st);
    const now = prof();
    // a current profile left: the red lead may still be in the A jack, which shorts the next rail through the meter
    if (!quiet && was.jack && !now.jack) S.leadWarn = true;
    if (now.jack) S.leadWarn = false;
    chartDecor(); renderMeter(); renderHeader(); renderNav();
    if (S.view === "machine") loadMachine();
  }

  // --- over-voltage -------------------------------------------------------------------------
  function onAlarm(ev) {
    const i = S.events.findIndex(e => e.epoch === ev.epoch);
    if (i >= 0) S.events[i] = ev; else S.events.push(ev);
    if (ev.kind === "alarm" && S.st && S.st.alarm.on && !S.alarmAck.has(ev.epoch)) showAlarm(ev);
    else if (S.alarmShown === ev.epoch) showAlarm(ev);      // keep the latched card's peak up to date
    if (ev.kind === "alarm") chart.escalate(ev.epoch);
    if (ev.kind === "spike" && !ev.open)
      toast(`SPIKE ${si(ev.peak, "V", 0.001)} at ${hms(ev.epoch, true)}: one reading over the ${si(ev.limit, "V", 0.001)} limit, `
            + "likely an autorange glitch", "warn", 8000);
  }
  function showAlarm(ev) {
    S.alarmShown = ev.epoch;
    $("#al-v").textContent = si(ev.peak, "V", 0.001);
    const p = prof();
    $("#al-sub").textContent = `${hms(ev.epoch, true)} · ${ev.n} reading${ev.n > 1 ? "s" : ""} over the ${si(ev.limit, "V", 0.001)} limit`
      + ` · ${p.label || ""}${S.st.machine_name ? " · " + S.st.machine_name : ""}${ev.open ? "" : " · back under now"}`;
    $("#alarm").classList.remove("hide");
  }
  $("#al-ack").addEventListener("click", () => { S.alarmAck.add(S.alarmShown); S.alarmShown = null; $("#alarm").classList.add("hide"); });

  // --- header -------------------------------------------------------------------------------
  function renderHeader() {
    const on = logging(), stopped = S.st && S.st.stopped;
    $("#h-dot").classList.toggle("on", on);
    const t = $("#h-logtxt");
    t.textContent = !S.connected ? "NO LINK" : stopped ? "STOPPED" : on ? "LOGGING" : "IDLE";
    t.className = !S.connected || stopped ? "bad" : on ? "ok" : "mut";
    const sys = S.sys;
    if (sys) {
      const src = (sys.clock && sys.clock.source) || "?";
      const b = $("#h-src");
      b.textContent = {ntp: "NTP", rtc: "RTC", unverified: "UNVERIFIED"}[src] || src.toUpperCase();
      b.className = "badge " + ({ntp: "ok", rtc: "cy", unverified: "warn"}[src] || "mut");
      const n = sys.network || {}, net = $("#h-net");
      if (n.hotspot && n.hotspot.active) { net.textContent = `HOTSPOT ${n.hotspot.ssid || "GATBOX"} ${n.hotspot.address || ""}`; net.className = "cy"; }
      else if (n.wifi) { net.textContent = `WIFI ${n.wifi.ssid} ${(n.wifi.ipv4 || [])[0] || ""}`; net.className = "mut"; }
      else {
        const eth = (n.interfaces || []).find(i => i.ipv4 && i.ipv4.length);
        net.textContent = eth ? `${eth.name.toUpperCase()} ${eth.ipv4[0]}` : "OFFLINE"; net.className = eth ? "mut" : "warn";
      }
    }
  }
  function tick() {
    const d = new Date(Date.now() + S.offset);
    $("#h-clock").textContent = d.toTimeString().slice(0, 8);
    if (S.view === "meter") renderMeter();
    renderHeader();
  }
  setInterval(tick, 1000);

  // --- METER --------------------------------------------------------------------------------
  function banner(cls, big, small, btn) {
    const b = el("div", "banner " + cls);
    add(b, el("span", null, big), small ? el("small", null, small) : null, btn || null);
    return b;
  }
  function renderMeter() {
    if (!S.st) return;
    const p = prof(), on = logging(), s = on ? S.last : null, ok = dialOk(), a = S.st.alarm || {};
    // banners
    const B = clear($("#banners"));
    if (S.st.stopped) B.appendChild(banner("warn", "LOGGER STOPPED", "Nothing is being recorded. START LOGGING starts a new file."));
    if (s && ok === false) {
      const want = (p.modes || []).map(k => dialOf(k) + " " + modeLabel(k)).join(" or ");
      B.appendChild(banner("warn", "SET DIAL TO " + want, `the dial is on ${modeLabel(s.mode)}`));
    }
    if (s && ok && p.kind === "rail" && p.rail && s.v != null && Math.abs(s.v) >= 0.5 && (s.v < 0) !== p.rail.startsWith("-"))
      B.appendChild(banner("warn", "LEADS REVERSED?", `a ${p.rail} rail reading ${s.display}`));
    if (s) for (const k of [...new Set((s.warn || []).map(w => WARN[w] && WARN[w][0]))].filter(Boolean)) {
      const w = Object.values(WARN).find(x => x[0] === k);
      B.appendChild(banner("warn", k + " IS ON", w[1] + (k === "HOLD" ? ": an overnight log would repeat one value" : "")));
    }
    if (p.jack) B.appendChild(banner("info", "RED LEAD IN THE A OR mA/µA JACK", "move it back to VΩ before measuring a rail"));
    if (S.leadWarn) {
      const done = el("button", null, "DONE");
      done.addEventListener("click", () => { S.leadWarn = false; renderMeter(); });
      B.appendChild(banner("bad", "MOVE THE RED LEAD BACK TO VΩ", "left in the A jack it shorts the next rail through the meter", done));
    }
    if (!s && !S.st.stopped) {
      if (Date.now() - S.endedAt < 6000) B.appendChild(banner("dim", "SESSION ENDED", "the next reading starts a new file"));
      else B.appendChild(banner("dim", "NO READINGS", "meter off, D02 head off the IR window, or adapter unplugged"));
    }
    // hero
    $("#m-mode").textContent = s ? (s.label || modeLabel(s.mode)) : "—";
    $("#m-profile").textContent = (p.label || "PROFILE") + " ▸";
    const base = profBase(p);
    $("#m-window").textContent = p.window ? `${fmtWin(p.window, base)} · ${sourceText(p.source)}${S.st.machine_name ? " · " + S.st.machine_name : ""}`
                                          : `no window${S.st.machine_name ? " · " + S.st.machine_name : ""}`;
    let val = "no reading", unit = "";
    if (s) {
      if (s.ol || s.v == null) val = s.display || "OL";
      else { val = s.raw; unit = (s.display || "").slice(s.raw.length).trim(); }
      if (s.mode === "CONT") { val = s.display; unit = ""; }
    }
    $("#m-value").textContent = val; $("#m-unit").textContent = unit;
    $("#m-value").classList.toggle("none", !s);
    const st = $("#m-state");
    let badge = null;
    if (s && ok && s.v != null && p.window) {
      if (p.kind === "rail" && Math.abs(s.v) < 0.5) badge = ["OFF", "mut"];
      else if (s.alarm === "alarm") badge = ["OVER-VOLTAGE", "bad"];
      else if (s.alarm === "spike") badge = ["SPIKE", "warn"];
      else if (s.v < p.window[0]) badge = ["LOW", "warn"];
      else if (s.v > p.window[1]) badge = ["HIGH", "warn"];
      else badge = ["IN WINDOW", "ok"];
    }
    st.classList.toggle("hide", !badge);
    if (badge) { st.textContent = badge[0]; st.className = "badge glow " + badge[1]; }
    const sess = $("#m-session");
    if (on) {
      const age = S.fileStart ? Date.now() / 1000 + S.offset / 1000 - S.fileStart : null;
      sess.textContent = `LOGGING${age != null ? " · " + dur(age) : ""} · ${S.file}`;
      sess.className = "ok";
    } else {
      sess.textContent = S.st.stopped ? "not logging (stopped)" : "not logging";
      sess.className = "mut";
    }
    // actions
    const nb = $("#b-new");
    nb.firstChild.textContent = S.st.stopped ? "START LOGGING" : "NEW FILE";
    nb.lastChild.textContent = S.st.stopped ? "logging is stopped" : "end this one";
    const ab = $("#b-alarm");
    ab.disabled = !a.applies;
    ab.firstChild.textContent = !a.applies ? "NO ALARM" : a.on ? "ALARM ON" : "ALARM OFF";
    ab.className = "act " + (!a.applies ? "" : a.on ? "okk" : "warn");
    ab.lastChild.textContent = a.applies ? `over ${si(a.limit, "V", 0.001)} takes the screen` : "this profile has no limit";
    const cb = $("#b-capture");
    cb.disabled = !(p.kind === "bench" && on);
    cb.lastChild.textContent = p.kind === "bench" ? (on ? "save this reading" : "needs a live reading") : "bench profiles only";
  }

  // MARK: tap = now; hold = now, then a label on the keypad (the time is the press, not the typing)
  (function () {
    const b = $("#b-mark");
    let t = null, held = false;
    b.addEventListener("pointerdown", () => { held = false; t = setTimeout(() => { held = true; markLabelled(); }, 700); });
    ["pointerup", "pointerleave", "pointercancel"].forEach(k => b.addEventListener(k, () => clearTimeout(t)));
    b.addEventListener("click", () => { if (!held) api("POST", "/api/mark", {source: S.local ? "dashboard" : "phone"}).catch(fail); });
  })();
  async function markLabelled() {
    const label = await keypad({title: "Mark label", max: 40});
    if (label == null) return;
    api("POST", "/api/mark", {source: S.local ? "dashboard" : "phone", label}).catch(fail);
  }
  $("#b-new").addEventListener("click", async () => {
    const stopped = S.st && S.st.stopped;
    if (!stopped && !await confirmBox("NEW FILE?", "The current file ends here and the next reading starts a new one.", "NEW FILE")) return;
    api("POST", "/api/session/new").then(() => toast(stopped ? "Logging started" : "New file requested")).catch(fail);
  });
  $("#b-alarm").addEventListener("click", () => {
    const on = !(S.st.alarm && S.st.alarm.on);
    api("PUT", "/api/meter/alarm", {on}).then(st => { onState(st); toast(on ? "ALARM ON" : "ALARM OFF: over-voltage won't take the screen (still logged)", on ? "" : "warn"); }).catch(fail);
  });
  $("#b-capture").addEventListener("click", async () => {
    const label = await keypad({title: "Capture label (e.g. U12 PIN 3)", max: 40});
    if (!label) return;
    api("POST", "/api/captures", {label}).catch(fail);
  });
  $("#m-profile").addEventListener("click", profilePicker);

  // --- sheets: profile picker, keypad, confirm, machine picker --------------------------------
  function sheet(title, build) {
    const pane = clear($("#pane"));
    const head = el("div", "pane-head");
    add(head, el("h2", null, title));
    const close = el("button", null, "CLOSE");
    add(head, close);
    pane.appendChild(head);
    $("#sheet").classList.remove("hide");
    return new Promise(resolve => {
      const done = v => { $("#sheet").classList.add("hide"); document.removeEventListener("keydown", S.kd); resolve(v); };
      close.addEventListener("click", () => done(null));
      build(pane, done);
    });
  }
  function keypad({title, max, number, value}) {
    return sheet(title, (pane, done) => {
      let txt = value || "";
      const field = el("div", "kp-field");
      const show = () => { field.textContent = txt; field.appendChild(el("span", "cur")); };
      show();
      pane.appendChild(field);
      if (number) pane.classList.add("num"); else pane.classList.remove("num");
      const keys = el("div", "keys");
      const rows = number ? ["789⌫", "456-", "123.", "0"] : ["1234567890", "QWERTYUIOP", "ASDFGHJKL-", "ZXCVBNM./⌫"];
      const press = k => {
        if (k === "⌫") txt = txt.slice(0, -1);
        else if (txt.length < (max || 40)) txt += k;
        show();
      };
      for (const r of rows) for (const k of r) {
        const b = el("button", null, k);
        b.addEventListener("click", () => press(k));
        keys.appendChild(b);
      }
      if (!number) { const sp = el("button", "x4", "SPACE"); sp.addEventListener("click", () => press(" ")); keys.appendChild(sp); }
      const cancel = el("button", number ? "" : "wide", "CANCEL"), ok = el("button", number ? "" : "x4", "OK");
      ok.style.borderColor = "var(--mag)";
      cancel.addEventListener("click", () => done(null));
      ok.addEventListener("click", () => done(txt.trim()));
      add(keys, cancel, ok);
      pane.appendChild(keys);
      S.kd = e => {                                  // a real keyboard works too (phone, bench keyboard)
        if (e.key === "Enter") done(txt.trim());
        else if (e.key === "Escape") done(null);
        else if (e.key === "Backspace") press("⌫");
        else if (e.key.length === 1) press(number ? e.key : e.key);
      };
      document.addEventListener("keydown", S.kd);
    });
  }
  function confirmBox(title, text, okLabel) {
    return sheet(title, (pane, done) => {
      pane.appendChild(el("p", "big", text));
      const row = el("div", "btnrow"), ok = el("button", null, okLabel || "OK"), no = el("button", null, "CANCEL");
      ok.style.borderColor = "var(--mag)";
      ok.addEventListener("click", () => done(true)); no.addEventListener("click", () => done(false));
      add(row, ok, no); pane.appendChild(row);
    });
  }
  async function loadProfiles() {
    const d = await api("GET", "/api/meter/profile");
    S.profiles = d.profiles; S.modes = d.modes;
    onState(d, true);
  }
  async function profilePicker() {
    if (!S.profiles.length) await loadProfiles().catch(fail);
    const pick = await sheet("Profile: what are you measuring?", (pane, done) => {
      const tiles = el("div", "tiles");
      for (const p of S.profiles) {
        const b = el("button", "tile" + (p.id === prof().id ? " on" : ""));
        add(b, el("b", null, p.label),
            el("span", "mut", (p.modes || []).map(k => dialOf(k)).join(" / ") || "any dial"),
            el("span", null, p.window ? fmtWin(p.window, profBase(p)) : p.user_window ? "you set the " + (p.user_window === "ceiling" ? "ceiling" : "range") : "no window"),
            el("span", "mut", p.notes.length > 70 ? p.notes.slice(0, 68) + "…" : p.notes));
        b.addEventListener("click", () => done(p));
        tiles.appendChild(b);
      }
      pane.appendChild(tiles);
    });
    if (!pick) return;
    const body = {id: pick.id};
    if (pick.user_window === "ceiling") {
      const v = await keypad({title: `${pick.label}: ceiling in mV (empty = keep)`, number: true, max: 8});
      if (v) body.ceiling = parseFloat(v) / 1000;
    } else if (pick.user_window === "range") {
      const u = profBase(pick) === "A" ? ["mA", 1e-3] : [profBase(pick), 1];
      const lo = await keypad({title: `${pick.label}: low limit in ${u[0]} (empty = none)`, number: true, max: 10});
      const hi = lo ? await keypad({title: `${pick.label}: high limit in ${u[0]}`, number: true, max: 10}) : null;
      if (lo && hi) body.window = [parseFloat(lo) * u[1], parseFloat(hi) * u[1]];
    }
    try {
      const st = await api("PUT", "/api/meter/profile", body);
      onState(st);
      toast(`Profile: ${st.profile.label}${st.new_file ? " · new file started" : ""}`);
    } catch (e) { fail(e); }
  }

  // --- navigation ---------------------------------------------------------------------------
  function show(view) {
    S.view = view;
    document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === view));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + view));
    if (view === "sessions") loadSessions();
    if (view === "machine") loadMachine();
    if (view === "system") loadSystem();
    if (view === "devices" || view === "dump") loadDevices();
    if (view === "meter") chart.redraw();
  }
  document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => show(b.dataset.view)));
  function renderNav() {
    $("#n-machine").textContent = (S.st && (S.st.machine_name || S.st.machine)) || "none";
    const t48 = S.dev && S.dev.t48 && S.dev.t48.present;
    $("#n-dump").classList.toggle("dim", !t48);
    $("#n-dump-s").textContent = t48 ? "T48 ready (M7)" : "T48 not plugged in";
  }

  // --- SESSIONS -----------------------------------------------------------------------------
  let histChart = null;
  async function loadSessions() {
    let d;
    try { d = await api("GET", "/api/rail/sessions?limit=200"); } catch (e) { return fail(e); }
    const L = clear($("#s-list"));
    if (!d.sessions.length) L.appendChild(el("div", "card mut", "No sessions yet."));
    for (const s of d.sessions) {
      const b = el("button", "row" + (S.selSession === s.file ? " on" : ""));
      const top = el("div");
      add(top, el("b", null, (s.start || "").replace("T", " ")),
          el("span", "tag " + ({ntp: "ok", rtc: "cy", unverified: "warn"}[s.clock.source] || "mut"), (s.clock.source || "?").toUpperCase()),
          s.live ? el("span", "tag ok", "LIVE") : null,
          s.ov ? el("span", "tag " + (s.ov > s.ov_suspect ? "bad" : "warn"), `OV ${s.ov}${s.ov_suspect ? ` (${s.ov_suspect} suspect)` : ""}`) : null,
          s.marks ? el("span", "tag cy", `${s.marks} mark${s.marks > 1 ? "s" : ""}`) : null);
      add(b, top,
          el("div", "mut", `${dur(s.duration_s)} · ${s.modes.map(m => m.label).join(", ") || "?"} · ${s.profile ? s.profile.label : "?"}`),
          s.machine ? el("div", "cy", s.machine) : null);
      b.addEventListener("click", () => openSession(s.file));
      L.appendChild(b);
    }
  }
  async function openSession(name) {
    S.selSession = name;
    document.querySelectorAll("#s-list .row").forEach(r => r.classList.remove("on"));
    const D = clear($("#s-detail"));
    D.appendChild(el("div", "card mut", "Loading " + name + "…"));
    let J, smp;
    try {
      [J, smp] = await Promise.all([api("GET", "/api/rail/report/" + encodeURIComponent(name)),
                                    api("GET", "/api/rail/samples/" + encodeURIComponent(name) + "?max=1500")]);
    } catch (e) { clear(D); D.appendChild(el("div", "card bad", String(e.message))); return; }
    clear(D);
    const head = el("div", "card");
    add(head, el("h2", null, name),
        el("div", "mut", [J.profile && J.profile.label, J.machine, J.window ? `window ${fmtWin([J.window.lo, J.window.hi], J.window.unit || "V")} (${J.window.source.startsWith("default") ? "default" : sourceText(J.window.source)})` : "no window",
                          J.clock && J.clock.source ? "clock " + J.clock.source.toUpperCase() : null].filter(Boolean).join(" · ")));
    if (J.powered) head.appendChild(el("div", null,
        `powered ${si(J.powered.min, "V", 0.001)} – ${si(J.powered.max, "V", 0.001)}, mean ${si(J.powered.mean, "V", 0.0001)} · ${J.powered.in_window_pct != null ? J.powered.in_window_pct.toFixed(2) + "% in window" : ""}`));
    const btns = el("div", "btnrow");
    if (!S.local) {                             // on the kiosk a PDF has no way back: phone only
      const pdf = el("a", null, "PDF"); pdf.href = J.pdf; pdf.target = "_blank";
      const csv = el("a", null, "CSV"); csv.href = J.csv;
      add(btns, pdf, el("span", "mut", " · "), csv, el("span", "mut", " · "));
    }
    const ph = el("a", null, "phone page"); ph.href = "/s/" + name; btns.appendChild(ph);
    head.appendChild(btns);
    D.appendChild(head);
    const box = el("div", "chartbox"), cv = el("canvas");
    const ctl = el("div", "chart-ctl");
    for (const [z, t] of [["out", "−"], ["in", "+"], ["fit", "ALL"]]) {
      const b = el("button", null, t);
      b.addEventListener("click", () => z === "fit" ? histChart.fit() : histChart.zoom(z === "in" ? 0.5 : 2));
      ctl.appendChild(b);
    }
    add(box, cv, ctl);
    D.appendChild(box);
    if (histChart) histChart.destroy();
    histChart = new window.GChart(cv, {history: true});
    const rows = smp.rows.map(rowSample);
    histChart.setData(rows);
    if (J.window && (!rows.length || BASE[rows[0].mode] === (J.window.unit || "V"))) histChart.setBand([J.window.lo, J.window.hi]);
    if (smp.alarm_hi != null) histChart.setLimit(J.profile && /neg/.test(J.profile.id || "") ? -smp.alarm_hi : smp.alarm_hi);
    histChart.setMarks((smp.marks || []).map(m => ({t: m.epoch, label: m.label || m.source})));
    D.appendChild(el("div", "mut", `${smp.n} readings${smp.downsampled ? ` (drawn from ${smp.rows.length}: every spike kept)` : ""} · tap for a reading, drag to pan, pinch to zoom`));
    D.appendChild(reportCards(J));
    const full = el("details", "card sect"), sum = el("summary", null, "Full text report");
    add(full, sum, el("pre", null, J.text || J.error || ""));
    D.appendChild(full);
    loadSessions();
  }

  // the report as a verdict, stat tiles and short tables (the same layout as the phone page's; full text collapsed)
  const ROWS = 8;
  function durS(s) { s = Math.max(0, +s || 0); return s < 90 ? (s < 10 ? s.toFixed(1) : s.toFixed(0)) + " s" : dur(s); }
  const vtxt = x => x == null ? "—" : (x.toFixed(3) + " V").replace("-", "−");
  const clock = iso => (iso || "").slice(11, 19);
  function verdictOf(J) {
    if (!J.window) return null;
    const ov = J.over_voltage || [], real = ov.filter(o => !o.suspect), exc = J.excursions || [];
    const sus = ov.length - real.length;
    const extra = sus ? ` · ${sus} suspect reading${sus > 1 ? "s" : ""} set aside (autorange glitches)` : "";
    if (real.length) return ["OVER-VOLTAGE", "bad", `${real.length} time${real.length > 1 ? "s" : ""} above ${J.alarm_hi} V${extra}`];
    if (exc.length) return ["LEFT THE WINDOW", "warn", `${exc.length} excursion${exc.length > 1 ? "s" : ""}${extra}`];
    return ["HELD THE WINDOW", "ok", ((J.power_cycles || []).length ? "whenever the board was on" : "all session") + extra];
  }
  function reportCards(J) {
    const c = el("div", "card");
    if (J.error) { c.appendChild(el("div", "warn", J.error)); return c; }
    const v = verdictOf(J);
    if (v) { const h = el("div", "verdict " + v[1], v[0]); h.appendChild(el("small", null, v[2])); c.appendChild(h); }
    const ses = J.session || {}, pw = J.powered, w = J.window;
    const ov = J.over_voltage || [], exc = J.excursions || [], pc = J.power_cycles || [], mk = J.marks || [];
    const tiles = [["Duration", dur(ses.duration_s || 0), `${clock(ses.start)} → ${clock(ses.end)}`],
                   ["Readings", (ses.samples || 0).toLocaleString("en-US"), `${(ses.rate || 0).toFixed(1)} per second`]];
    if (pw && pw.min != null) {
      tiles.push(["Powered", `${pw.min.toFixed(3)}–${pw.max.toFixed(3)} V`, `mean ${pw.mean.toFixed(4)} V`]);
      if (pw.in_window_pct != null) tiles.push(["In window", pw.in_window_pct.toFixed(2) + "%", `of ${(pw.readings || 0).toLocaleString("en-US")} readings on`]);
    } else if ((J.modes || []).length) tiles.push([J.modes[0].label, J.modes[0].stats.split("   ")[0].replace("mean ", ""), "mean"]);
    if (pc.length) tiles.push(["Power cycles", String(pc.length), durS(pc.reduce((a, p) => a + p.duration_s, 0)) + " off in all"]);
    if (w) {
      const sus = ov.filter(o => o.suspect).length;
      tiles.push(["Over-voltage", String(ov.length), ov.length && sus === ov.length ? "all suspect" : sus ? `${sus} suspect`
                  : J.alarm_hi != null ? `above ${J.alarm_hi} V` : "no limit"]);
      tiles.push(["Excursions", String(exc.length), "outside the window"]);
    }
    if (mk.length) tiles.push(["Marks", String(mk.length), ""]);
    const st = el("div", "stat");
    for (const [a, b, d] of tiles) add(st, add(el("div"), el("i", null, a), el("b", null, b), el("span", null, d)));
    c.appendChild(st);
    const table = (title, head, rows) => {
      if (!rows.length) return;
      const t = el("table", "t"), hr = el("tr");
      head.forEach(h => hr.appendChild(el("th", null, h)));
      t.appendChild(hr);
      for (const r of rows.slice(0, ROWS)) {
        const tr = el("tr");
        r.forEach(x => tr.appendChild(x instanceof Node ? add(el("td"), x) : el("td", null, x)));
        t.appendChild(tr);
      }
      add(c, el("h3", null, title), t, rows.length > ROWS ? el("div", "mut", `+${rows.length - ROWS} more in the full text`) : null);
    };
    const tag = (txt, cls) => el("span", "tag " + cls, txt);
    table("Over-voltage", ["at", "peak", "readings", ""],
          ov.map(o => [clock(o.start), vtxt(o.peak), String(o.samples), o.suspect ? tag("suspect", "warn") : tag("real", "bad")]));
    table("Excursions", ["from", "for", "worst", "readings"], exc.map(x => [clock(x.start), durS(x.duration_s), vtxt(x.worst), String(x.samples)]));
    table("Power cycles", ["off at", "back at", "down for"], pc.map(p => [clock(p.off_at), p.back_at ? clock(p.back_at) : "end of log", durS(p.duration_s)]));
    table("Marks", ["at", "from", "label"], mk.map(m => [clock(m.iso), m.source, m.label + (m.before_start ? " (before the file)" : "")]));
    table("Open input (OL)", ["at", "for", "mode"], (J.ol_events || []).map(o => [clock(o.at), durS(o.duration_s), o.mode]));
    table("Warnings", ["flag", "readings", "first"], (J.warnings || []).map(x => [x.flag, String(x.samples), clock(x.first)]));
    table("Dial turns", ["at", "from", "to"], (J.mode_changes || []).map(x => [clock(x.at), x.from, x.to]));
    table("Gaps", ["until", "for"], (J.gaps || []).map(x => [clock(x.end), durS(x.duration_s)]));
    table("Clock steps", ["at", "jump"], (J.clock_steps || []).map(x => [clock(x.at), (x.jump_s > 0 ? "+" : "") + x.jump_s.toFixed(1) + " s"]));
    if ((J.modes || []).length > 1) table("Per mode", ["mode", "readings", "stats"], J.modes.map(m => [m.label, String(m.samples), m.stats]));
    return c;
  }

  // --- MACHINE ------------------------------------------------------------------------------
  async function loadMachine() {
    const B = clear($("#mc-body"));
    let m;
    try { m = await api("GET", "/api/machine"); } catch (e) { return fail(e); }
    $("#mc-clear").disabled = !m.slug;
    $("#mc-pick").disabled = !m.roster;
    if (!m.roster) { B.appendChild(el("div", "card warn", "No roster installed on this Pi (data/gatbox-barcade-roster.json).")); return; }
    if (!m.slug) { B.appendChild(el("div", "card mut", "No machine set. PICK MACHINE, or scan its QR code (the scanner comes in M6).")); return; }
    const e = m.entry, c = el("div", "card");
    add(c, el("div", "big", e.name), el("div", "mut", `${e.slug} · ${e.mfr || ""} · ${e.kind === "pinball" ? "pinball" : "video game"}`));
    if (e.platform_info) add(c, el("div", null, e.platform_info.desc || e.platform));
    if (e.risk_info && e.risk_info.length) {
      const ch = el("div", "chips");
      for (const r of e.risk_info) ch.appendChild(el("span", "badge warn", r.flag + (r.meaning ? ": " + r.meaning : "")));
      add(c, el("h3", null, "Risks"), ch);
    }
    const list = (title, items) => {
      if (!items || !items.length) return;
      const ul = el("ul", "plain");
      items.forEach(i => ul.appendChild(el("li", null, i)));
      add(c, el("h3", null, title), ul);
    };
    list("Faults", e.faults_all); list("Parts", e.parts_all);
    if (e.platform_info && e.platform_info.pm) add(c, el("h3", null, "PM"), el("div", null, e.platform_info.pm));
    if (e.critical_actions && e.critical_actions.length) {
      c.appendChild(el("h3", null, "Critical actions"));
      for (const a of e.critical_actions) {
        const d = el("div", "card");
        add(d, el("b", "bad", a.path.replace(/_/g, " ").replace(/\//g, " › ")), a.note ? el("div", null, a.note) : null);
        if (a.detail) for (const [k, v] of Object.entries(a.detail)) {
          if (k === "sources") continue;
          d.appendChild(el("div", "mut", `${k.replace(/_/g, " ")}: ${typeof v === "string" ? v : JSON.stringify(v)}`));
        }
        c.appendChild(d);
      }
    }
    c.appendChild(el("h3", null, "Rail specs"));
    if (e.spec && e.spec.rails) {
      for (const [r, w] of Object.entries(e.spec.rails)) c.appendChild(el("div", null, `${r.replace("-", "−")}  ${w[0]}–${w[1]} V`));
      if (e.spec.source) c.appendChild(el("div", "mut", e.spec.source));
    } else c.appendChild(el("div", "mut", "None on file (gatbox-machine-specs.json takes numbers from the machine's own manual)."));
    c.appendChild(el("h3", null, "Captures"));
    try {
      const cp = await api("GET", "/api/captures?machine=" + encodeURIComponent(e.slug));
      if (!cp.captures.length) c.appendChild(el("div", "mut", "None yet: pick a bench profile and press CAPTURE."));
      for (const k of cp.captures.slice(-30).reverse())
        c.appendChild(el("div", null, `${k.iso.replace("T", " ")}  ${k.label}  ${k.value} ${k.unit}  (${modeLabel(k.mode)}${k.flags ? ", " + k.flags : ""})`));
    } catch (x) { c.appendChild(el("div", "bad", String(x.message))); }
    add(c, el("h3", null, "Dumps"), el("div", "mut", "The T48 dump archive arrives in M7."));
    B.appendChild(c);
  }
  $("#mc-clear").addEventListener("click", async () => {
    if (!await confirmBox("CLEAR MACHINE?", "New files won't carry a machine (a live session starts a new file).", "CLEAR")) return;
    api("DELETE", "/api/machine").then(() => loadMachine()).catch(fail);
  });
  $("#mc-pick").addEventListener("click", async () => {
    let R;
    try { R = await api("GET", "/api/roster"); } catch (e) { return fail(e); }
    const pick = await sheet("Pick the machine", (pane, done) => {
      let q = "";
      const field = el("div", "kp-field");
      const list = el("div", "picklist");
      const draw = () => {
        field.textContent = q || "type to filter"; field.appendChild(el("span", "cur"));
        clear(list);
        const Q = q.toLowerCase();
        for (const m of R.machines.filter(m => !Q || m.name.toLowerCase().includes(Q) || m.slug.includes(Q)).slice(0, 60)) {
          const b = el("button", "row");
          add(b, el("b", null, m.name), el("div", "mut", m.slug + (m.spec ? " · rail spec" : "")));
          b.addEventListener("click", () => done(m.slug));
          list.appendChild(b);
        }
      };
      const keys = el("div", "keys");
      for (const k of "QWERTYUIOPASDFGHJKL⌫ZXCVBNM-0123456789") {
        const b = el("button", null, k);
        b.addEventListener("click", () => { q = k === "⌫" ? q.slice(0, -1) : q + k; draw(); });
        keys.appendChild(b);
      }
      S.kd = e => { if (e.key === "Backspace") q = q.slice(0, -1); else if (e.key.length === 1) q += e.key; else return; draw(); };
      document.addEventListener("keydown", S.kd);
      add(pane, field, list, keys);
      draw();
    });
    if (!pick) return;
    api("PUT", "/api/machine", {slug: pick}).then(r => { toast(`Machine: ${r.entry.name}${r.new_file ? " · new file started" : ""}`); loadMachine(); }).catch(fail);
  });

  // --- SYSTEM -------------------------------------------------------------------------------
  async function loadSystem() {
    try {
      const d = await api("GET", "/api/system");
      S.sys = d; S.local = !!(d.client && d.client.local);
      S.offset = d.time.epoch * 1000 - Date.now();
    } catch (e) { return; }
    renderHeader();
    if (S.view !== "system") return;
    const d = S.sys, G = clear($("#sy-grid"));
    const tile = (title, rows, cls) => {
      const c = el("div", "card " + (cls || ""));
      c.appendChild(el("h2", null, title));
      const dl = el("dl", "kv");
      for (const [k, v, vc] of rows) if (v != null) add(dl, el("dt", null, k), el("dd", vc || null, String(v)));
      c.appendChild(dl);
      G.appendChild(c);
      return c;
    };
    const th = d.throttled;
    tile("Temperature + power", [
      ["CPU", d.temp_c != null ? d.temp_c.toFixed(1) + " °C" : "?", d.temp_c > 80 ? "bad" : d.temp_c > 70 ? "warn" : "ok"],
      ["fan", d.fan_rpm != null ? Math.round(d.fan_rpm) + " rpm" : null],
      ["EXT5V", d.ext5v_v != null ? d.ext5v_v.toFixed(3) + " V" : "?", d.ext5v_v < 4.8 ? "bad" : "ok"],
      ["under-voltage now", d.undervoltage_now == null ? "?" : d.undervoltage_now ? "YES" : "no", d.undervoltage_now ? "bad" : "ok"],
      ["throttling now", th ? (th.now.join(", ") || "none") : "?", th && th.now.length ? "bad" : "ok"],
      ["since boot", th ? (th.since_boot.join(", ") || "none") : "?", th && th.since_boot.length ? "warn" : "ok"]]);
    const c = d.clock || {}, r = c.rtc || {};
    tile("Clock", [
      ["source", (c.source || "?").toUpperCase(), {ntp: "ok", rtc: "cy", unverified: "warn"}[c.source]],
      ["NTP", c.ntp_synced == null ? "?" : c.ntp_synced ? "synced" : "not synced"],
      ["RTC", r.present ? (r.trusted ? "trusted" : "not trusted") : "missing", r.trusted ? "ok" : "warn"],
      ["RTC − system", r.rtc_minus_system_s != null ? r.rtc_minus_system_s + " s" : null],
      ["set from NTP", r.last_set_from_ntp ? r.last_set_from_ntp.replace("T", " ") : "never"],
      ["cell", r.cell_v != null ? r.cell_v.toFixed(2) + " V" : null],
      ["charging", r.charging || "?", r.charging === "off" ? "ok" : "bad"]]);
    const n = d.network || {};
    tile("Network", [
      ["Wi-Fi", n.wifi ? `${n.wifi.ssid} (${n.wifi.device})` : "not connected"],
      ["hotspot", n.hotspot && n.hotspot.active ? `${n.hotspot.ssid || "GATBOX"} ${n.hotspot.address || ""}` : "off (comes up when no known Wi-Fi)"],
      ...(n.interfaces || []).map(i => [i.name, i.ipv4.length ? i.ipv4.join(", ") : (i.up ? "up, no address" : "down")])]);
    tile("Storage", Object.entries(d.disk || {}).map(([k, v]) =>
      [k, v ? `${v.used_pct}% used · ${(v.free / 1e9).toFixed(1)} GB free` : "not there yet (M7)", v && v.used_pct > 90 ? "bad" : null]));
    const lg = d.logger || {};
    const lt = tile("Logger", [
      ["service", lg.running ? "running" : "NOT RUNNING", lg.running ? "ok" : "bad"],
      ["state", lg.stopped ? "stopped from a web page" : lg.logging ? "logging" : "waiting for readings", lg.stopped ? "warn" : lg.logging ? "ok" : null],
      ["file", lg.file]]);
    const row = el("div", "btnrow");
    const go = el("button", null, lg.stopped ? "START LOGGING" : "NEW FILE"), stop = el("button", null, "STOP LOGGING");
    stop.disabled = !!lg.stopped;
    go.addEventListener("click", () => api("POST", "/api/session/new").then(loadSystem).catch(fail));
    stop.addEventListener("click", async () => {
      if (await confirmBox("STOP LOGGING?", "Nothing is recorded until someone presses START.", "STOP")) api("POST", "/api/session/stop").then(loadSystem).catch(fail);
    });
    add(row, go, stop); lt.appendChild(row);
    const v = d.versions || {};
    tile("Software", Object.entries(v).map(([k, x]) => [k, x]));
    if (d.errors && Object.keys(d.errors).length) tile("Couldn't read", Object.entries(d.errors).map(([k, x]) => [k, x, "warn"]));
    const lk = el("div", "card");
    const a = el("a", null, "Phone view (sessions, PDF, CSV) →"); a.href = "/";
    add(lk, el("h2", null, "Elsewhere"), a);
    G.appendChild(lk);
    $("#sy-kiosk").classList.toggle("hide", !S.local);
  }
  // EXIT KIOSK / SHUT DOWN: this screen only (the server refuses them from anywhere else), hold to confirm
  document.querySelectorAll("#sy-kiosk .hold").forEach(b => {
    let t = null;
    const idle = b.textContent;
    const stop = () => { clearTimeout(t); b.classList.remove("arm"); if (!b.dataset.done) b.textContent = idle; };
    b.style.setProperty("--ms", b.dataset.ms + "ms");
    b.addEventListener("pointerdown", e => {
      e.preventDefault(); b.classList.add("arm"); b.textContent = "KEEP HOLDING…";
      t = setTimeout(() => {
        b.dataset.done = 1;
        b.textContent = b.dataset.act === "exit" ? "CLOSING…" : "SHUTTING DOWN…";
        fetch("/kiosk/" + b.dataset.act, {method: "POST"});
        if (b.dataset.act === "shutdown") $("#kx-off").classList.remove("hide");
      }, +b.dataset.ms);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach(k => b.addEventListener(k, stop));
  });

  // --- DEVICES + DUMP -----------------------------------------------------------------------
  async function loadDevices() {
    try { S.dev = await api("GET", "/api/devices"); } catch (e) { return; }
    renderNav();
    const d = S.dev;
    if (S.view === "devices") {
      const G = clear($("#dv-grid"));
      const card = (title, lines, cls) => {
        const c = el("div", "card " + (cls || ""));
        c.appendChild(el("h2", null, title));
        lines.forEach(([t, k]) => c.appendChild(el("div", k || null, t)));
        G.appendChild(c);
      };
      const a = d.dmm.adapter, rd = d.dmm.readings;
      card("DMM chain", [[d.dmm.chain, "mut"],
        [a.present ? `adapter ${a.usb_id}${a.tty ? " on " + a.tty : ""}` : "adapter NOT PLUGGED IN", a.present ? "ok" : "bad"],
        [rd.flowing ? `readings flowing · ${modeLabel(rd.mode)}` : "no readings (meter off, or D02 head off the IR window)", rd.flowing ? "ok" : "warn"]]);
      card("T48 programmer", [[d.t48.present ? `connected · USB ${d.t48.usb_id}` : "not plugged in", d.t48.present ? "ok" : "mut"],
                              ["reads only from here; the dump workflow is M7", "mut"]]);
      card("Barcode scanner", [["Eyoyo EY-H2: set up in M6 (its USB ID hasn't been read on this Pi yet)", "mut"]]);
      card("Touch panel", [[d.touch.present ? `Waveshare 7" (C) · USB ${d.touch.usb_id}` : "not found", d.touch.present ? "ok" : "warn"]]);
      for (const [t, why] of [["M2K SCOPE", "ADALM2000: not fitted"], ["HUB ARM", "UUGear MEGA4 / uhubctl: not fitted"],
                              ["BOARD POWER", "not fitted: GATBOX never powers a board"]])
        card(t, [[why, "mut"]], "notfit");
    }
    if (S.view === "dump") {
      const B = clear($("#du-body")), c = el("div", "card");
      if (d.t48.present) add(c, el("div", "big ok", "T48 CONNECTED"), el("div", "mut", "USB " + d.t48.usb_id),
        el("p", null, "The dump workflow arrives in M7: pick the part (names exactly as minipro lists them, never guessed), " +
                      "label the chip, read it twice and compare, identify it with MAME, archive it under this machine."),
        el("p", "mut", "Reads only: nothing on this screen will ever write to a chip."));
      else add(c, el("div", "big mut", "T48 NOT PLUGGED IN"),
        el("p", null, "Plug the XGecu T48 into the Pi. This screen lights up when it's seen (USB a466:0a53)."));
      B.appendChild(c);
    }
  }

  // --- start ----------------------------------------------------------------------------------
  tick();
  loadProfiles().then(() => { renderMeter(); }).catch(fail);
  loadSystem(); loadDevices();
  setInterval(loadSystem, 10000);
  setInterval(() => { if (S.view === "system") loadSystem(); }, 5000);
  setInterval(loadDevices, 15000);
  setInterval(() => { if (S.view === "sessions") loadSessions(); }, 30000);
  connect();
})();
