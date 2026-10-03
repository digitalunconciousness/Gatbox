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
    if (src.startsWith("actual:")) return "this machine's actual";
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
    on("capture", c => {
      const to = c.machine === "unassigned" ? "unassigned (no machine set)"
               : c.machine === (S.st && S.st.machine) ? (S.st.machine_name || c.machine) : c.machine;
      toast(`SAVED ${c.label}: ${c.display} → ${to}`);
      loadSaved();
    });
    on("roster", r => {
      toast(r.action === "added" ? `ADDED ${r.name} · ID ${r.slug} · it's in PICK MACHINE and the label list`
                                 : `UPDATED ${r.name}`);
      if (S.view === "machine") loadMachine();
    });
    on("scan", onScan);
    on("dump", onDump);
    on("specs", x => { if (S.view === "manuals" && x.slug === mnSlug()) loadManuals(); });
    on("manuals", x => { if (S.view === "manuals" && x.slug === mnSlug()) loadManuals(); });
    on("state", st => onState(st));
    on("heartbeat", () => { S.connected = true; renderHeader(); });
    es.addEventListener("resync", () => { es.close(); setTimeout(connect, 300); });
    es.onopen = () => { S.connected = true; renderHeader(); };
    es.onerror = () => { S.connected = false; renderHeader(); };   // EventSource reconnects on its own
  }
  function onScan(s) {                        // gatbox-scand: a code from the EY-H2 (a mark has its own toast)
    if (s.action === "machine") toast(`SCANNED ${s.name}${s.new_file ? " · new file started" : ""}`);
    else if (s.action === "same-machine") toast(`SCANNED ${s.name} (already the machine)`);
    else if (s.action === "new") toast("SCANNED NEW FILE");
    else if (s.action === "unknown") toast(`UNKNOWN CODE: ${s.code}`, "warn", 8000);
    if (S.view === "devices") loadDevices();
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
    if (S.view === "dump" && D.st) renderDump();          // FOR <machine> and its chip list follow the machine
    if (S.view === "manuals" && !M.slug && M.for !== (S.st.machine || null)) { M.for = S.st.machine || null; loadManuals(); }
    if (S.savedFor !== (S.st.machine || null)) loadSaved();
  }

  // SAVE READING's notes: the current machine's last three, on one line under the reading
  const savedText = k => /inf/i.test(k.value) ? "OL" : `${k.value} ${k.unit}`;
  async function loadSaved() {
    const slug = (S.st && S.st.machine) || null;
    S.savedFor = slug;
    let d;
    try { d = await api("GET", "/api/captures" + (slug ? "?machine=" + encodeURIComponent(slug) : "")); } catch (e) { return; }
    if (S.savedFor !== slug) return;                // the machine changed while this was on its way
    const L = d.captures.slice(-3).reverse(), box = clear($("#m-saved"));
    box.classList.toggle("hide", !L.length);
    if (!L.length) return;
    box.appendChild(document.createTextNode("SAVED: "));
    L.forEach((k, i) => add(box, document.createTextNode(i ? " · " : ""), el("b", null, k.label), document.createTextNode(" " + savedText(k))));
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
    const to = S.st.machine ? `to ${S.st.machine_name || S.st.machine}` : "to unassigned (no machine)";
    cb.lastChild.textContent = p.kind === "bench" ? (on ? to : "needs a live reading") : "bench profiles only";
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
    const label = await keypad({title: "Save reading: a label (e.g. U12 PIN 3)", max: 40});
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
      let txt = value || "", lower = false;
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
      const letters = [];
      const key = (k, cls, label) => {
        const b = el("button", cls || null, label || k), letter = /^[A-Z]$/.test(k);
        b.addEventListener("click", () => press(letter && lower ? k.toLowerCase() : k));
        if (letter) letters.push(b);
        keys.appendChild(b);
        return b;
      };
      for (const r of rows) for (const k of r) key(k);
      if (!number) {                                 // shift (names aren't all caps), the symbols names use, space
        const sh = el("button", "wide shift", "abc");
        sh.addEventListener("click", () => {
          lower = !lower;
          sh.textContent = lower ? "ABC" : "abc"; sh.classList.toggle("on", lower);
          letters.forEach(b => { b.textContent = lower ? b.textContent.toLowerCase() : b.textContent.toUpperCase(); });
        });
        keys.appendChild(sh);
        key("'"); key("&");
        const sp = el("button", "x4", "SPACE"); sp.addEventListener("click", () => press(" ")); keys.appendChild(sp);
        key(":"); key("!");
      }
      const cancel = el("button", number ? "" : "x4", "CANCEL"), ok = el("button", number ? "" : "x6", "OK");
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
    if (view === "manuals") loadManuals();
    if (view === "system") loadSystem();
    if (view === "devices" || view === "dump") loadDevices();
    if (view === "meter") chart.redraw();
  }
  document.querySelectorAll("#nav button").forEach(b => b.addEventListener("click", () => show(b.dataset.view)));
  function renderNav() {
    $("#n-machine").textContent = (S.st && (S.st.machine_name || S.st.machine)) || "none";
    const t48 = S.dev && S.dev.t48 && S.dev.t48.present;
    $("#n-dump").classList.toggle("dim", !t48);
    $("#n-dump-s").textContent = t48 ? "T48 ready" : "T48 not plugged in";
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
  // The report decides the verdict (report.verdict in report.py) and sends it. This used to
  // re-derive the rule here, so the dashboard and the phone view each had their own copy and
  // could in principle disagree about the same session.
  function verdictOf(J) {
    return J.verdict ? [J.verdict.title, J.verdict.css, J.verdict.detail] : null;
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
    table("Marks", ["at", "from", "label"], mk.map(m => [clock(m.iso), m.source, (m.label || "(no label)") + (m.before_start ? " (before the file)" : "")]));
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
    $("#mc-pick").disabled = $("#mc-export").disabled = $("#mc-labels").disabled = !m.roster;
    if (!m.roster) { B.appendChild(el("div", "card warn", "No roster installed on this Pi (data/gatbox-barcade-roster.json). + ADD MACHINE starts one here.")); return; }
    if (!m.slug) { B.appendChild(el("div", "card mut", "No machine set. PICK MACHINE, or scan its QR code.")); return; }
    const e = m.entry, c = el("div", "card");
    add(c, el("div", "big", e.name), el("div", "mut", `${e.slug} · ${e.mfr || ""} · ${e.kind === "pinball" ? "pinball" : "video game"}`));
    if (e.added) {
      const r = el("div", "btnrow"), ed = el("button", null, "EDIT");
      r.style.cssText = "align-items:center;margin-top:6px";
      ed.addEventListener("click", () => editMachine(e));
      add(r, el("span", "badge cy", "ADDED ON THIS PI"), el("span", "mut", "EXPORT ROSTER takes it to the maintenance app"), ed);
      c.appendChild(r);
    }
    if (e.platform_info) add(c, el("div", null, e.platform_info.desc || e.platform));
    else if (e.platform === NOT_SURE) add(c, el("div", "warn", "Platform: not sure yet"));
    if (e.notes) add(c, el("div", "mut", e.notes));
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
      for (const [r, w] of Object.entries(e.spec.rails))
        c.appendChild(el("div", null, `${r.replace("-", "−")}  ${w[0]}–${w[1]} V` + (e.spec.sources ? ` · ${srcLabel(e.spec.sources[r])}` : "")));
      if (e.spec.source) c.appendChild(el("div", "mut", e.spec.source));
    } else c.appendChild(el("div", "mut", "None yet: the MANUALS tab has the machine's spec sheet (CONFIRM a limit from its manual)."));
    const sp = el("button", null, "SPEC SHEET & MANUALS ▸");
    sp.addEventListener("click", () => { M.slug = M.name = null; show("manuals"); });   // the card is the current machine's
    c.appendChild(sp);
    c.appendChild(el("h3", null, "Saved readings"));
    try {
      const cp = await api("GET", "/api/captures?machine=" + encodeURIComponent(e.slug));
      if (!cp.captures.length) c.appendChild(el("div", "mut", "None yet: pick a bench profile and press SAVE READING."));
      for (const k of cp.captures.slice(-30).reverse())
        c.appendChild(el("div", null, `${k.iso.replace("T", " ")}  ${k.label}  ${savedText(k)}  (${modeLabel(k.mode)}${k.flags ? ", " + k.flags : ""})`));
    } catch (x) { c.appendChild(el("div", "bad", String(x.message))); }
    await mameSection(c, e.slug);
    c.appendChild(el("h3", null, "Dumps"));
    try { dumpList(c, (await api("GET", "/api/dumps?machine=" + encodeURIComponent(e.slug))).dumps); }
    catch (x) { c.appendChild(el("div", "mut", "No dump archive on this Pi yet.")); }
    B.appendChild(c);
  }
  // LABEL LIST: what to type into a label maker's QR text (Katasymbol and the like). Tap COPY, paste in the app.
  function copyText(t) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
    const ta = el("textarea");                     // plain http on the LAN: the old way still works
    ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok ? Promise.resolve() : Promise.reject(new Error("copy blocked: long-press the code instead"));
  }
  $("#mc-labels").addEventListener("click", async () => {
    let R;
    try { R = await api("GET", "/api/roster"); } catch (e) { return fail(e); }
    await sheet("Label list: the text for each QR", (pane, done) => {
      const note = el("div", "mut", "Each QR holds just this text. Tap COPY, then paste it as the QR text in the label app.");
      const filter = el("input");
      filter.placeholder = "filter"; filter.className = "kp-field"; filter.style.fontSize = "18px";
      const list = el("div", "scroll");
      list.style.cssText = "overflow:auto;min-height:0;flex:1";
      const row = (name, code) => {
        const r = el("div", "row card");
        r.style.cssText = "display:flex;align-items:center;gap:10px";
        const txt = el("div");
        txt.style.flex = "1";
        add(txt, el("b", null, name), el("div", "cy", code));
        txt.lastChild.style.userSelect = "all";
        const b = el("button", null, "COPY");
        b.addEventListener("click", () => copyText(code).then(() => toast("Copied " + code)).catch(fail));
        add(r, txt, b);
        return r;
      };
      const draw = () => {
        clear(list);
        const Q = filter.value.trim().toLowerCase();
        if (!Q) { list.appendChild(row("MARK (it just crashed)", "GATBOX:MARK")); list.appendChild(row("NEW FILE", "GATBOX:NEW")); }
        for (const m of R.machines.slice().sort((a, b) => a.name.localeCompare(b.name)))
          if (!Q || m.name.toLowerCase().includes(Q) || m.slug.includes(Q)) list.appendChild(row(m.name, m.slug));
      };
      filter.addEventListener("input", draw);
      add(pane, note, filter, list);
      if (!S.local) { const a = el("a", null, "Or a printable sheet of all of them (PDF) →"); a.href = "/labels.pdf"; a.target = "_blank"; pane.appendChild(a); }
      draw();
    });
  });
  $("#mc-clear").addEventListener("click", async () => {
    if (!await confirmBox("CLEAR MACHINE?", "New files won't carry a machine (a live session starts a new file).", "CLEAR")) return;
    api("DELETE", "/api/machine").then(() => loadMachine()).catch(fail);
  });
  $("#mc-pick").addEventListener("click", async () => {
    let R;
    try { R = await api("GET", "/api/roster"); } catch (e) { return fail(e); }
    const pick = await pickFrom("Pick the machine", R.machines.map(m => ({
      key: m.slug, title: m.name, find: [m.name.toLowerCase(), m.slug],
      sub: m.slug + (m.spec ? " · rail spec" : "") + (m.added ? " · added here" : "")})), S.st && S.st.machine);
    if (!pick) return;
    api("PUT", "/api/machine", {slug: pick}).then(r => { toast(`Machine: ${r.entry.name}${r.new_file ? " · new file started" : ""}`); loadMachine(); }).catch(fail);
  });

  // a pick list filtered with its own letter keys (the kiosk has no keyboard): machines, platforms
  function pickFrom(title, items, cur) {
    return sheet(title, (pane, done) => {
      let q = "";
      const field = el("div", "kp-field"), list = el("div", "picklist");
      const draw = () => {
        field.textContent = q || "type to filter"; field.appendChild(el("span", "cur"));
        clear(list);
        const Q = q.toLowerCase();
        for (const it of items.filter(it => !Q || it.find.some(f => f.includes(Q))).slice(0, 60)) {
          const b = el("button", "row" + (it.key === cur ? " on" : ""));
          add(b, el("b", null, it.title), el("div", "mut", it.sub));
          b.addEventListener("click", () => done(it.key));
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
  }

  // + ADD MACHINE / EDIT: the roster's own fields. The server makes the ID from the name; a dry run shows it before
  // saving, because it's permanent (it's what the QR code holds). EDIT is for machines added here: ID and kind stay.
  const NOT_SURE = "confirm";           // the roster's own mark for a board nobody has confirmed yet
  async function machineForm(f, slug) {
    let plats = [];
    try { plats = (await api("GET", "/api/roster")).platforms || []; } catch (e) { /* no roster yet: NOT SURE only */ }
    const pdesc = k => k === NOT_SURE ? "NOT SURE (confirm later)" : ((plats.find(p => p.key === k) || {}).desc || k);
    for (;;) {
      const act = await sheet(slug ? `Edit ${f.name}` : "Add a machine", (pane, done) => {
        const form = el("div", "form");
        const fld = (label, value, a, hint) => {
          const b = el(a ? "button" : "div", "row fld");
          add(b, el("span", "mut", label), el("b", value ? null : "mut", value || hint));
          if (a) b.addEventListener("click", () => done(a));
          return b;
        };
        add(form, fld("NAME", f.name, "name", "tap to type"), fld("MAKER", f.mfr, "mfr", "tap to type"));
        if (slug) form.appendChild(fld("TYPE", f.kind === "pinball" ? "Pinball" : "Video game"));
        else {
          const seg = el("div", "seg");
          for (const [k, t] of [["video_games", "VIDEO GAME"], ["pinball", "PINBALL"]]) {
            const b = el("button", f.kind === k ? "on" : null, t);
            b.addEventListener("click", () => done("kind:" + k));
            seg.appendChild(b);
          }
          form.appendChild(seg);
        }
        add(form, fld("PLATFORM", f.platform ? pdesc(f.platform) : "", "platform", "tap to pick"),
            fld("NOTES", f.notes, "notes", "optional"));
        const idl = el("div", "idline mut"), row = el("div", "btnrow");
        const save = el("button", "primary", slug ? "SAVE" : "ADD MACHINE"), cancel = el("button", null, "CANCEL");
        save.disabled = true;
        save.addEventListener("click", () => done("save"));
        cancel.addEventListener("click", () => done(null));
        add(row, save, cancel);
        add(pane, form, idl, row);
        const ready = f.name && f.mfr && f.platform;
        if (slug) { idl.textContent = `ID ${slug} (permanent)`; save.disabled = !ready; }
        else if (!ready) idl.textContent = "Needs a name, a maker and a platform (NOT SURE is fine).";
        else {
          idl.textContent = "checking the name…";
          api("POST", "/api/roster", Object.assign({}, f, {dry_run: true})).then(d => {
            idl.textContent = `ID ${d.entry.slug} · permanent: it's what the QR code holds`;
            idl.className = "idline ok"; save.disabled = false;
          }).catch(e => { idl.textContent = e.message; idl.className = "idline bad"; });
        }
      });
      if (act == null) return null;
      if (act === "name") { const v = await keypad({title: "Machine name (as on the marquee)", max: 60, value: f.name}); if (v != null) f.name = v; }
      else if (act === "mfr") { const v = await keypad({title: "Maker (e.g. CAPCOM, WILLIAMS)", max: 40, value: f.mfr}); if (v != null) f.mfr = v; }
      else if (act === "notes") { const v = await keypad({title: "Notes (optional)", max: 200, value: f.notes}); if (v != null) f.notes = v; }
      else if (act === "platform") {
        const p = await pickFrom("Platform: the board family", [
          {key: NOT_SURE, title: pdesc(NOT_SURE), sub: "flagged 'confirm' until someone checks the board", find: ["not sure", NOT_SURE]},
          ...plats.map(p => ({key: p.key, title: p.desc, sub: p.key, find: [p.desc.toLowerCase(), p.key]}))], f.platform);
        if (p) f.platform = p;
      }
      else if (act.startsWith("kind:")) f.kind = act.slice(5);
      else if (act === "save") {
        const body = {name: f.name, mfr: f.mfr, platform: f.platform, notes: f.notes};
        try {
          return (slug ? await api("PUT", "/api/roster/" + encodeURIComponent(slug), body)
                       : await api("POST", "/api/roster", Object.assign(body, {kind: f.kind}))).entry;
        } catch (e) { fail(e); }
      }
    }
  }
  const editMachine = e => machineForm({name: e.name, mfr: e.mfr || "", kind: e.kind, platform: e.platform, notes: e.notes || ""}, e.slug);
  $("#mc-add").addEventListener("click", () => machineForm({name: "", mfr: "", kind: "video_games", platform: null, notes: ""}));

  // EXPORT ROSTER: the roster file + the machines added here, for the maintenance app (a download on a phone; the
  // kiosk can't hand a file to anyone, so it shows the address to open)
  function piAddr() {
    const n = (S.sys && S.sys.network) || {};
    if (n.hotspot && n.hotspot.active && n.hotspot.address) return n.hotspot.address;
    if (n.wifi && (n.wifi.ipv4 || []).length) return n.wifi.ipv4[0];
    const i = (n.interfaces || []).find(x => x.ipv4 && x.ipv4.length);
    return i ? i.ipv4[0] : null;
  }
  $("#mc-export").addEventListener("click", async () => {
    let R;
    try { R = await api("GET", "/api/roster"); } catch (e) { return fail(e); }
    const mine = R.machines.filter(m => m.added);
    const pick = await sheet("Export the roster", (pane, done) => {
      pane.appendChild(el("div", null, (mine.length ? `The roster file plus the ${mine.length} machine${mine.length > 1 ? "s" : ""} added on this Pi`
                                                    : "The roster file as installed (nothing added on this Pi yet)")
                                        + ", in the file's own format, ready for the maintenance app to import."));
      if (S.local) {
        const a = piAddr();
        add(pane, el("div", "mut", "On your phone (same network), open:"),
            el("div", "big cy", `http://${a ? a.split("/")[0] : "<the Pi's address>"}/roster.json`));
      } else {
        const a = el("a", "btnlink", "DOWNLOAD gatbox-barcade-roster.json");
        a.href = "/roster.json"; a.setAttribute("download", "gatbox-barcade-roster.json");
        pane.appendChild(a);
      }
      if (!mine.length) return;
      const list = el("div", "form");
      for (const m of mine) {
        const r = el("div", "row card"), t = el("div"), b = el("button", null, "EDIT");
        r.style.cssText = "display:flex;align-items:center;gap:10px"; t.style.flex = "1";
        add(t, el("b", null, m.name), el("div", "mut", `${m.slug} · ${m.mfr || ""}`));
        b.addEventListener("click", () => done(m.slug));
        add(r, t, b);
        list.appendChild(r);
      }
      add(pane, el("h3", null, "Added on this Pi"), list);
    });
    if (!pick) return;
    try { editMachine(await api("GET", "/api/roster/" + encodeURIComponent(pick))); } catch (e) { fail(e); }
  });

  // --- SYSTEM -------------------------------------------------------------------------------
  async function loadSystem() {
    try {
      const d = await api("GET", "/api/system");
      S.sys = d; S.local = !!(d.client && d.client.local);
      S.offset = d.time.epoch * 1000 - Date.now();
      // a new install changed the dashboard's files: load them (the 7" kiosk never reloads on its own)
      if (d.dash_version) {
        if (!S.dashVersion) S.dashVersion = d.dash_version;
        else if (S.dashVersion !== d.dash_version) { location.reload(); return; }
      }
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
      const sc = d.scanner;
      card("Barcode scanner", [
        [sc.present ? `Eyoyo EY-H2 · USB ${sc.usb_id}` : "Eyoyo EY-H2 not plugged in", sc.present ? "ok" : "mut"],
        [!sc.daemon ? "gatbox-scand isn't running: scans type into the focused window"
                    : sc.grabbed ? "held by gatbox-scand: scans go to GATBOX, not into windows" : "gatbox-scand waiting for it",
         sc.grabbed ? "ok" : "warn"],
        ...(sc.recent || []).slice(0, 3).map(r => [`${hms(r.at, true)}  ${r.action === "unknown" ? "unknown code " + r.code
                                                     : r.action === "mark" ? "MARK" : r.action === "new" ? "NEW FILE" : r.name || r.code}`, "mut"])]);
      card("Touch panel", [[d.touch.present ? `Waveshare 7" (C) · USB ${d.touch.usb_id}` : "not found", d.touch.present ? "ok" : "warn"]]);
      for (const [t, why] of [["M2K SCOPE", "ADALM2000: not fitted"], ["HUB ARM", "UUGear MEGA4 / uhubctl: not fitted"],
                              ["BOARD POWER", "not fitted: GATBOX never powers a board"]])
        card(t, [[why, "mut"]], "notfit");
    }
    if (S.view === "dump") loadDump();
  }

  // --- MANUALS: a machine's documents (fetched, dropped in, uploaded) and its spec sheet -------------------------
  // The spec sheet keeps the manual's numbers as the manual gives them (its words, its page) and this machine's actual
  // values from the field beside them. A manual limit reaches the logger only after CONFIRM, with its page on screen
  // (hard rule 12); an actual only when it's given a window of its own. FOR <machine> like DUMP: reading a machine's
  // manuals never changes the logger's machine.
  const M = {slug: null, name: null, docs: null, sheet: null};
  const mnSlug = () => M.slug || (S.st && S.st.machine) || null;
  const fmtV = v => `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(Math.abs(v) >= 10 ? 1 : 2)} V`;
  const fmtW = w => w ? range(w[0], w[1], "V") : "—";
  const srcLabel = s => !s ? "" : s === "actual" ? "this machine's actual" : s.startsWith("manual:") ? "the manual, confirmed"
                          : s === "specs file" ? "specs file" : s;
  const OTHER_RAILS = ["+5V", "+12V", "-5V", "+3.3V", "-12V", "+25V"];
  async function loadManuals() {
    const B = $("#mn-body"), slug = mnSlug();
    const head = el("div", "card"), row = el("div", "btnrow"), other = el("button", null, "OTHER MACHINE");
    row.style.alignItems = "center";
    other.addEventListener("click", pickManualsMachine);
    add(row, el("b", null, "FOR"), el("span", "chip", slug ? (M.slug ? M.name : (S.st.machine_name || slug)) || slug : "no machine"), other);
    if (M.slug) {
      const back = el("button", null, "CURRENT");
      back.addEventListener("click", () => { M.slug = M.name = null; loadManuals(); });
      row.appendChild(back);
    }
    head.appendChild(row);
    if (!slug) {
      add(clear(B), head, el("div", "card mut", "No machine set: OTHER MACHINE picks one to read (the logger's machine stays as it is)."));
      return;
    }
    let d, s;
    try {
      [d, s] = await Promise.all([api("GET", "/api/manuals/" + encodeURIComponent(slug)), api("GET", "/api/specs/" + encodeURIComponent(slug))]);
    } catch (e) { add(clear(B), head, el("div", "card warn", e.message)); return; }
    if (mnSlug() !== slug) return;                 // another machine was picked meanwhile
    M.docs = d; M.sheet = s;
    add(clear(B), head, specCard(slug, s, d), docsCard(slug, d));
  }
  async function pickManualsMachine() {
    let R, O;
    try { [R, O] = await Promise.all([api("GET", "/api/roster"), api("GET", "/api/manuals")]); } catch (e) { return fail(e); }
    const pick = await pickFrom("Whose manuals?", R.machines.map(m => {
      const n = (O.machines[m.slug] || {}).docs || 0;
      return {key: m.slug, title: m.name, find: [m.name.toLowerCase(), m.slug],
              sub: `${m.slug} · ${n ? `${n} document${n > 1 ? "s" : ""}` : "no documents yet"}`};
    }), mnSlug());
    if (!pick) return;
    M.slug = pick; M.name = (R.machines.find(m => m.slug === pick) || {}).name || pick;
    loadManuals();
  }
  const pageUrl = (slug, file, page, w) => `/manual/${encodeURIComponent(slug)}/${encodeURIComponent(file)}/${page}.png?w=${w}`;
  function specCard(slug, s, d) {
    const c = el("div", "card"), docOf = id => d.docs.find(x => x.id === id);
    add(c, el("h2", null, "Spec sheet"),
        el("div", "mut", "The manual's numbers as the manual gives them (p.N opens the page), and this machine's actual values " +
                         "from the field beside them. A manual limit sets the logger's window only once you CONFIRM it; an " +
                         "actual only if you give it a window of its own."));
    const cite = x => {
      const doc = docOf(x.doc), b = el("button", "cite", `p.${x.page} ▸`);
      b.disabled = !doc;
      b.addEventListener("click", () => openViewer(slug, doc, x.page));
      return b;
    };
    const buttons = (...bs) => { const r = el("div", "btnrow"); bs.forEach(b => b && r.appendChild(b)); return r; };
    const btn = (label, cls, f) => { const b = el("button", "mini" + (cls ? " " + cls : ""), label); b.addEventListener("click", f); return b; };
    if (!s.rails.length && !s.facts.length) c.appendChild(el("div", "mut", "Nothing read off the manuals yet."));
    if (s.rails.length) {
      const wrap = el("div"), t = el("table", "t spec-t"), hr = el("tr");
      wrap.style.overflowX = "auto";
      for (const h of ["RAIL", "THE MANUAL", "ACTUAL · THIS MACHINE", "WINDOW IN USE"]) hr.appendChild(el("th", null, h));
      t.appendChild(hr);
      for (const r of s.rails) {
        const man = el("td"), act = el("td");
        if (!r.manual.length && !r.file) man.appendChild(el("span", "mut", "not in the manuals"));
        for (const m of r.manual) {
          const line = el("div", "specline");
          add(line, el("b", null, fmtW([m.lo, m.hi])), cite(m));
          if (m.confirmed) add(line, el("span", "badge ok", "✓ CONFIRMED"),
                               btn("UNDO", "", () => specCall("DELETE", `/api/specs/${encodeURIComponent(slug)}/confirm`, {rail: r.rail}, `${r.rail}: confirm taken back`)));
          else line.appendChild(btn("CONFIRM", "primary", () => confirmLimit(slug, r, m, docOf(m.doc))));
          man.appendChild(line);
          if (m.quote) man.appendChild(el("div", "quote", `“${m.quote}” · ${m.title}`));
        }
        if (r.file) man.appendChild(el("div", "mut", `specs file: ${fmtW(r.file.window)}${r.file.source ? " · " + r.file.source : ""}`));
        if (r.actual) add(act, el("b", null, fmtV(r.actual.value)),
                          r.actual.window ? el("div", null, `own window ${fmtW(r.actual.window)}`) : null,
                          r.actual.note ? el("div", "quote", r.actual.note) : null,
                          el("div", "mut", `${r.actual.from === "meter" ? "from the meter" : "typed"} · ${(r.actual.date || "").slice(0, 10)}`));
        act.appendChild(buttons(btn(r.actual ? "EDIT" : "SET ACTUAL", "", () => setRailActual(slug, r)),
                                r.actual ? btn("CLEAR", "", () => specCall("DELETE", `/api/actuals/${encodeURIComponent(slug)}`, {rail: r.rail}, `${r.rail}: actual cleared`)) : null));
        const tr = el("tr");
        add(tr, el("td", "rail", r.rail.replace("-", "−")), man, act,
            el("td", r.window ? "ok" : "mut", r.window ? `${fmtW(r.window)} · ${srcLabel(r.source)}` : "the profile's"));
        t.appendChild(tr);
      }
      wrap.appendChild(t);
      c.appendChild(wrap);
    }
    if (s.facts.length) {
      const wrap = el("div"), t = el("table", "t spec-t"), hr = el("tr");
      wrap.style.overflowX = "auto";
      for (const h of ["", "THE MANUAL", "ACTUAL · THIS MACHINE"]) hr.appendChild(el("th", null, h));
      t.appendChild(hr);
      for (const f of s.facts) {
        const man = el("td"), act = el("td"), tr = el("tr");
        if (f.value != null) { add(man, el("span", null, f.value + " "), f.page ? cite(f) : null); if (f.quote) man.appendChild(el("div", "quote", `“${f.quote}”`)); }
        else man.appendChild(el("span", "mut", "not in the manuals"));
        if (f.actual) add(act, el("b", null, f.actual.value), f.actual.note ? el("div", "quote", f.actual.note) : null);
        act.appendChild(buttons(btn(f.actual ? "EDIT" : "SET ACTUAL", "", () => setFactActual(slug, f.what, f.actual)),
                                f.actual ? btn("CLEAR", "", () => specCall("DELETE", `/api/actuals/${encodeURIComponent(slug)}`, {fact: f.what}, `${f.what}: actual cleared`)) : null));
        add(tr, el("td", "rail", f.what), man, act);
        t.appendChild(tr);
      }
      wrap.appendChild(t);
      c.appendChild(wrap);
    }
    const have = new Set(s.rails.map(r => r.rail));
    c.appendChild(buttons(
      btn("+ ACTUAL FOR A RAIL", "", async () => {
        const rail = await pickFrom("Which rail?", OTHER_RAILS.filter(r => !have.has(r)).map(r => ({key: r, title: r.replace("-", "−"), sub: "", find: [r]})));
        if (rail) setRailActual(slug, {rail, manual: [], actual: null, window: null});
      }),
      btn("+ ACTUAL FACT", "", () => setFactActual(slug, null, null))));
    return c;
  }
  async function specCall(method, path, body, msg) {
    try {
      const r = await api(method, path, body);
      toast(msg + (r.new_file ? " · new file started" : ""));
      if (S.view === "manuals") loadManuals();
    } catch (e) { fail(e); }
  }
  // CONFIRM: the page itself on screen, the manual's words, then the owner's say-so
  async function confirmLimit(slug, r, m, doc) {
    const ok = await sheet(`CONFIRM ${r.rail} ${fmtW([m.lo, m.hi])}?`, (pane, done) => {
      pane.appendChild(el("div", null, `${m.title}, page ${m.page}: “${m.quote || ""}”. Check it on the page. Once confirmed, ` +
                                       `it's this machine's ${r.rail} window in the logger (unless an actual of its own says otherwise).`));
      if (doc) { const box = el("div", "pagebox"), im = el("img"); im.src = pageUrl(slug, doc.file, m.page, 1200); box.appendChild(im); pane.appendChild(box); }
      const row = el("div", "btnrow"), y = el("button", "primary", "CONFIRM"), n = el("button", null, "CANCEL");
      y.addEventListener("click", () => done(true)); n.addEventListener("click", () => done(false));
      add(row, y, n);
      pane.appendChild(row);
    });
    if (ok) specCall("POST", `/api/specs/${encodeURIComponent(slug)}/confirm`, {rail: r.rail, doc: m.doc, page: m.page},
                     `${r.rail} confirmed from ${m.title} p.${m.page}`);
  }
  function choose(title, text, options) {           // [[key, label, primary?], ...] -> key or null
    return sheet(title, (pane, done) => {
      pane.appendChild(el("div", null, text));
      const row = el("div", "btnrow");
      for (const [k, label, primary] of options) { const b = el("button", primary ? "primary" : null, label); b.addEventListener("click", () => done(k)); row.appendChild(b); }
      const n = el("button", null, "CANCEL");
      n.addEventListener("click", () => done(null));
      row.appendChild(n);
      pane.appendChild(row);
    });
  }
  // an actual value from the field: the live reading (this machine, the right rail, logging) or typed; then whether
  // it gets a window of its own; then a note. The manual's numbers are never touched.
  async function setRailActual(slug, r) {
    const neg = r.rail.startsWith("-"), cur = r.actual;
    const live = logging() && S.last && S.last.mode === "VDC" && S.last.v != null && slug === (S.st && S.st.machine) && (S.last.v < 0) === neg;
    let v = null, from = "typed";
    if (live) {
      const how = await choose(`Actual ${r.rail} on this machine`, "What it really runs at, measured in the field. The manual's numbers stay as they are.",
                               [["live", `USE THE LIVE READING ${fmtV(S.last.v)}`, true], ["type", "TYPE IT"]]);
      if (!how) return;
      if (how === "live") { v = S.last.v; from = "meter"; }
    }
    if (v == null) {
      const t = await keypad({title: `Actual ${r.rail} on this machine, in volts`, number: true, max: 8, value: cur ? String(cur.value) : ""});
      if (!t) return;
      v = parseFloat(t);
      if (!isFinite(v)) return toast("Not a number", "bad");
    }
    const opts = [["none", "NO OWN WINDOW", !(cur && cur.window)], ["set", "SET ITS OWN WINDOW"]];
    if (cur && cur.window) opts.unshift(["same", `KEEP ITS OWN ${fmtW(cur.window)}`, true]);
    const own = await choose(`${r.rail}: a window of its own?`,
                             `Now: ${r.window ? fmtW(r.window) + " (" + srcLabel(r.source) + ")" : "the profile's window"}. Give it one of its own ` +
                             "if it runs outside that on purpose (a boosted +5V), so it doesn't read HIGH all night.", opts);
    if (!own) return;
    let win = own === "same" ? cur.window : null;
    if (own === "set") {
      const lo = await keypad({title: `${r.rail} window for this machine: low (V)`, number: true, max: 8});
      const hi = lo ? await keypad({title: `${r.rail} window for this machine: high (V)`, number: true, max: 8}) : null;
      if (!lo || !hi) return;
      win = [parseFloat(lo), parseFloat(hi)];
    }
    const note = await keypad({title: "Note (optional): why, what was done", max: 200, value: cur ? cur.note : ""});
    if (note == null) return;
    specCall("PUT", `/api/actuals/${encodeURIComponent(slug)}`, {rail: r.rail, value: v, window: win, note, from},
             `${r.rail} actual: ${fmtV(v)}${win ? " · own window " + fmtW(win) : ""}`);
  }
  async function setFactActual(slug, what, cur) {
    if (!what) { what = await keypad({title: "What is it? (e.g. MONITOR, FUSE F2, POWER SUPPLY)", max: 60}); if (!what) return; }
    const v = await keypad({title: `${what}: what this machine actually has`, max: 120, value: cur ? cur.value : ""});
    if (!v) return;
    const note = await keypad({title: "Note (optional)", max: 200, value: cur ? cur.note : ""});
    if (note == null) return;
    specCall("PUT", `/api/actuals/${encodeURIComponent(slug)}`, {fact: what, value: v, note}, `${what}: ${v}`);
  }
  function docsCard(slug, d) {
    const c = el("div", "card");
    c.appendChild(el("h2", null, `Documents (${d.docs.length})`));
    if (d.note) c.appendChild(el("div", "warn", d.note));
    if (!d.docs.length) c.appendChild(el("div", "mut", d.why ? `None here: ${d.why}` : "None here yet."));
    for (const x of d.docs) {
      const b = el("button", "row doc");
      add(b, el("b", null, x.title + " "), el("span", "tag cy", x.kind.toUpperCase()),
          el("div", "mut", `${x.pages} page${x.pages === 1 ? "" : "s"} · ${(x.bytes / 1e6).toFixed(1)} MB` +
                           (x.added === "upload" ? " · uploaded" : x.added === "folder" ? " · added by hand" : "")));
      b.addEventListener("click", () => openViewer(slug, x, 1));
      c.appendChild(b);
    }
    if (d.not_fetched.length)
      c.appendChild(el("div", "mut", `On the list, not on the Pi yet: ${d.not_fetched.map(x => x.title).join(" · ")} (gatbox-manuals fetch)`));
    const row = el("div", "btnrow");
    row.style.cssText = "align-items:center;margin-top:8px";
    if (S.local) row.appendChild(el("span", "mut", `ADD PDF: from your phone (this tab, same button), or copy it into /srv/gatbox/manuals/${slug}/ on the Pi.`));
    else {
      const inp = el("input"), b = el("button", null, "ADD PDF");
      inp.type = "file"; inp.accept = "application/pdf,.pdf"; inp.className = "hide";
      b.addEventListener("click", () => inp.click());
      inp.addEventListener("change", () => { if (inp.files[0]) uploadPdf(slug, inp.files[0]); });
      add(row, b, inp, el("span", "mut", `a PDF, up to ${Math.round(d.upload_max / 1048576)} MB`));
    }
    c.appendChild(row);
    return c;
  }
  async function uploadPdf(slug, file) {
    const title = await keypad({title: "What is it? (e.g. OPERATORS MANUAL, CPU BOARD SCHEMATIC)", max: 120, value: file.name.replace(/\.pdf$/i, "")});
    if (title == null) return;
    const kind = await pickFrom("What kind of document?", (M.docs.kinds || ["yours"]).map(k => ({key: k, title: k.toUpperCase(), sub: "", find: [k]})), "manual");
    if (!kind) return;
    toast(`Uploading ${file.name}…`, "", 120000);
    try {
      const r = await fetch(`/api/manuals/${encodeURIComponent(slug)}?title=${encodeURIComponent(title)}&kind=${encodeURIComponent(kind)}`,
                            {method: "POST", headers: {"Content-Type": "application/pdf"}, body: file});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
      toast(`Added ${d.title} (${d.pages} pages)`);
      loadManuals();
    } catch (e) { fail(e); }
  }

  // the viewer: one page as an image drawn by the Pi (no PDF plugin on the kiosk), fit to width; drag pans, pinch or
  // +/− zoom (the kiosk runs --disable-pinch, so the page does its own), a sideways swipe at fit turns the page,
  // double tap zooms in / back to fit. The next page is fetched ahead; a sharper image comes when zoomed in.
  const V = {slug: null, doc: null, page: 1, z: 1, tx: 0, ty: 0, ptrs: new Map(), pinch: null, drag: null, w: 0, lastTap: 0};
  const snapW = px => [800, 1200, 1600, 2400].find(w => w >= px) || 2400;
  function openViewer(slug, doc, page) {
    if (!doc) return toast("That document isn't on the Pi yet", "warn");
    Object.assign(V, {slug, doc, page: Math.max(1, Math.min(page || 1, doc.pages || 1)), z: 1, tx: 0, ty: 0, w: 0});
    $("#vw-title").textContent = doc.title;
    const a = $("#vw-pdf");
    a.href = `/manual/${encodeURIComponent(slug)}/${encodeURIComponent(doc.file)}`;
    a.classList.toggle("hide", S.local);
    $("#vw-img").removeAttribute("src"); $("#vw-img").dataset.url = "";
    $("#viewer").classList.remove("hide");
    vwLoad();
  }
  function vwLoad() {
    const st = $("#vw-stage"), img = $("#vw-img");
    const w = snapW(st.clientWidth * (window.devicePixelRatio || 1) * V.z);
    $("#vw-page").textContent = `${V.page} / ${V.doc.pages}`;
    $("#vw-prev").disabled = V.page <= 1; $("#vw-next").disabled = V.page >= V.doc.pages;
    if (w > V.w || img.dataset.page !== String(V.page)) {
      V.w = Math.max(w, img.dataset.page === String(V.page) ? V.w : 0);
      const url = pageUrl(V.slug, V.doc.file, V.page, V.w);
      $("#vw-wait").textContent = "drawing the page…";
      $("#vw-wait").classList.remove("hide");
      img.onload = () => { $("#vw-wait").classList.add("hide"); vwClamp(); vwApply(); };
      img.onerror = () => { $("#vw-wait").textContent = "couldn't draw this page"; };
      img.dataset.url = url; img.dataset.page = String(V.page); img.src = url;
      if (V.page < V.doc.pages) new Image().src = pageUrl(V.slug, V.doc.file, V.page + 1, V.w);
    }
    vwApply();
  }
  function vwClamp() {
    const st = $("#vw-stage"), img = $("#vw-img"), W = st.clientWidth, H = st.clientHeight;
    const h = (img.naturalWidth ? img.naturalHeight / img.naturalWidth * W : H) * V.z;
    V.tx = Math.min(0, Math.max(W - W * V.z, V.tx));
    V.ty = Math.min(0, Math.max(Math.min(0, H - h), V.ty));
  }
  const vwApply = () => { $("#vw-img").style.transform = `translate(${V.tx}px,${V.ty}px) scale(${V.z})`; };
  function vwGo(p) {
    if (!V.doc || p < 1 || p > V.doc.pages) return;
    Object.assign(V, {page: p, z: 1, tx: 0, ty: 0, w: 0});
    vwLoad();
  }
  function vwZoom(f, cx, cy) {                       // about a point on the stage
    const z = Math.max(1, Math.min(6, V.z * f)), k = z / V.z;
    V.tx = cx - (cx - V.tx) * k; V.ty = cy - (cy - V.ty) * k; V.z = z;
    vwClamp(); vwApply();
    clearTimeout(vwZoom.t); vwZoom.t = setTimeout(vwLoad, 250);    // a sharper image once the zooming stops
  }
  (function () {
    const st = $("#vw-stage"), mid = () => { const p = [...V.ptrs.values()]; return [(p[0].x + p[1].x) / 2, (p[0].y + p[1].y) / 2, Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y)]; };
    const at = e => { const r = st.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; };
    st.addEventListener("pointerdown", e => {
      st.setPointerCapture(e.pointerId); V.ptrs.set(e.pointerId, at(e));
      if (V.ptrs.size === 2) { const [cx, cy, d] = mid(); V.pinch = {cx, cy, d: d || 1, z: V.z}; V.drag = null; }
      else if (V.ptrs.size === 1) { const p = at(e); V.drag = {x: p.x, y: p.y, tx: V.tx, ty: V.ty, t: Date.now()}; }
    });
    st.addEventListener("pointermove", e => {
      if (!V.ptrs.has(e.pointerId)) return;
      V.ptrs.set(e.pointerId, at(e));
      if (V.pinch && V.ptrs.size >= 2) { const [cx, cy, d] = mid(); vwZoom(V.pinch.z * d / V.pinch.d / V.z, cx, cy); }
      else if (V.drag) { const p = at(e); V.tx = V.drag.tx + (V.z > 1 ? p.x - V.drag.x : 0); V.ty = V.drag.ty + p.y - V.drag.y; vwClamp(); vwApply(); }
    });
    const up = e => {
      if (!V.ptrs.has(e.pointerId)) return;
      const p = at(e), dr = V.drag;
      V.ptrs.delete(e.pointerId);
      if (V.ptrs.size < 2) V.pinch = null;
      if (dr && V.ptrs.size === 0) {
        const dx = p.x - dr.x, dy = p.y - dr.y;
        if (V.z === 1 && Math.abs(dx) > 80 && Math.abs(dx) > 2 * Math.abs(dy) && Date.now() - dr.t < 700) vwGo(V.page + (dx < 0 ? 1 : -1));
        else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
          if (Date.now() - V.lastTap < 350) { if (V.z > 1) { V.z = 1; V.tx = V.ty = 0; vwApply(); } else vwZoom(2.5, p.x, p.y); V.lastTap = 0; }
          else V.lastTap = Date.now();
        }
      }
      if (V.ptrs.size === 0) V.drag = null;
    };
    st.addEventListener("pointerup", up); st.addEventListener("pointercancel", up);
    st.addEventListener("wheel", e => {
      e.preventDefault();
      const p = at(e);
      if (e.ctrlKey) vwZoom(e.deltaY > 0 ? 0.8 : 1.25, p.x, p.y);
      else { V.ty -= e.deltaY; V.tx -= e.deltaX; vwClamp(); vwApply(); }
    }, {passive: false});
    $("#vw-prev").addEventListener("click", () => vwGo(V.page - 1));
    $("#vw-next").addEventListener("click", () => vwGo(V.page + 1));
    $("#vw-in").addEventListener("click", () => vwZoom(1.5, st.clientWidth / 2, st.clientHeight / 2));
    $("#vw-out").addEventListener("click", () => vwZoom(1 / 1.5, st.clientWidth / 2, st.clientHeight / 2));
    $("#vw-fit").addEventListener("click", () => { V.z = 1; V.tx = V.ty = 0; vwApply(); });
    $("#vw-close").addEventListener("click", () => $("#viewer").classList.add("hide"));
    $("#vw-page").addEventListener("click", async () => {
      const n = await keypad({title: `Go to page (1 to ${V.doc.pages})`, number: true, max: 5});
      if (n) vwGo(parseInt(n, 10));
    });
    $("#vw-search").addEventListener("click", async () => {
      const q = await keypad({title: `Search ${V.doc.title} (e.g. VOLT, FUSE, U12)`, max: 60});
      if (!q) return;
      let r;
      try { r = await api("GET", `/api/manuals/${encodeURIComponent(V.slug)}/search?file=${encodeURIComponent(V.doc.file)}&q=${encodeURIComponent(q)}`); }
      catch (e) { return fail(e); }
      if (!r.hits.length) return toast(r.text ? `"${q}": not in this document` : "This document has no text to search (a scan without OCR)", "warn", 6000);
      const p = await pickFrom(`"${q}": ${r.hits.length} page${r.hits.length > 1 ? "s" : ""}`,
                               r.hits.map(h => ({key: String(h.page), title: `p.${h.page}${h.count > 1 ? ` (${h.count}×)` : ""}`, sub: h.snippet, find: [String(h.page)]})), String(V.page));
      if (p) vwGo(parseInt(p, 10));
    });
    document.addEventListener("keydown", e => {
      if ($("#viewer").classList.contains("hide") || !$("#sheet").classList.contains("hide")) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") vwGo(V.page + 1);
      else if (e.key === "ArrowLeft" || e.key === "PageUp") vwGo(V.page - 1);
      else if (e.key === "Escape") $("#viewer").classList.add("hide");
    });
    window.addEventListener("resize", () => { if (!$("#viewer").classList.contains("hide")) { vwClamp(); vwApply(); } });
  })();

  // --- ROM CHIPS (MAME): the machine's MAME set, every chip, ticked when the archive has a dump with its SHA-1 ------
  // (built at install from the Pi's own MAME: hashes only). A version switch when MAME knows several (revisions,
  // regions); it starts on the version the dumps match best. The pick is kept in this page's memory only.
  const KB = n => n >= 1048576 ? `${n / 1048576} MB` : `${n / 1024} KB`;
  const famsFor = n => (D.fams || []).filter(f => (f.bytes || []).includes(n));
  async function families() {
    if (!D.fams) { try { D.fams = (await api("GET", "/api/dump/parts")).families; } catch (e) { return []; } }
    return D.fams;
  }
  async function mameList(slug) {
    const j = await api("GET", "/api/mame/" + encodeURIComponent(slug));
    S.mameVer = S.mameVer || {};
    return [j, j.versions.find(x => x.name === (S.mameVer[slug] || j.default)) || j.versions[0]];
  }
  async function mameSection(c, slug) {
    c.appendChild(el("h3", null, "ROM chips (MAME)"));
    const box = el("div");
    c.appendChild(box);
    let j, v;
    try { [j, v] = await mameList(slug); await families(); } catch (e) { box.appendChild(el("div", "mut", e.message)); return; }
    if (!v) {
      box.appendChild(el("div", j.status === "missing" ? "warn" : "mut", (j.status === "none" ? "Not in MAME: " : "") + (j.why || "No MAME set.")));
      return;
    }
    const head = el("div", "btnrow"), vb = el("button", null, `${v.name} ▸`);
    head.style.alignItems = "center";
    vb.disabled = j.versions.length < 2;
    vb.addEventListener("click", async () => {
      const pick = await pickFrom(`Which version? (${j.versions.length} in MAME ${j.mame || ""})`, j.versions.map(x => ({
        key: x.name, title: x.desc, find: [x.desc.toLowerCase(), x.name],
        sub: `${x.name}${x.parent ? " · parent" : ""} · ${x.mfr} ${x.year} · ${x.dumped} of ${x.roms.length} dumped`})), v.name);
      if (pick) { S.mameVer[slug] = pick; loadMachine(); }
    });
    add(head, vb, el("span", null, `${v.desc} · ${v.mfr} ${v.year}`),
        el("span", "badge " + (v.dumped ? "ok" : "mut"), `${v.dumped} OF ${v.roms.length} DUMPED`),
        j.status === "check" ? el("span", "badge warn", "CHECK") : null);
    box.appendChild(head);
    if (j.status === "check" && j.why) box.appendChild(el("div", "warn", "Match not confirmed: " + j.why + " A dump from the board settles it."));
    if (j.versions.length > 1) box.appendChild(el("div", "mut", `MAME knows ${j.versions.length} versions: tap ${v.name} ▸ to switch.`));
    const wrap = el("div");
    wrap.style.overflowX = "auto";
    const t = el("table", "t chips-t"), hr = el("tr");
    for (const h of ["", "CHIP", "SIZE", "CRC", "SHA-1", "ON THE BOARD"]) hr.appendChild(el("th", null, h));
    t.appendChild(hr);
    for (const r of v.roms) {
      const tr = el("tr"), d = r.dumped[0], fam = famsFor(r.size).map(f => f.label).join(" or ");
      const name = el("td");
      add(name, el("b", null, r.name), d ? el("div", "ok", `← ${d.label}${d.machine !== slug ? ` (archived under ${d.machine})` : ""}`) : null);
      add(tr, el("td", d ? "ok" : "mut", d ? "✓" : "·"), name,
          el("td", null, KB(r.size) + (fam ? ` · ${fam}` : "")), el("td", null, r.crc || "—"),
          el("td", r.sha1 ? null : "warn", r.sha1 ? r.sha1.slice(0, 8) : (r.status === "nodump" ? "no known dump" : "no hash")),
          el("td", "mut", (r.region || "") + (r.bios ? ` · BIOS ${r.bios}` : "")));
      t.appendChild(tr);
    }
    for (const dk of v.disks) {
      const tr = el("tr");
      add(tr, el("td", "mut", "▣"), el("td", null, dk.name), el("td", "mut", "hard disk / CD image: not a T48 job"),
          el("td"), el("td", null, (dk.sha1 || "").slice(0, 8)), el("td", "mut", dk.region || ""));
      t.appendChild(tr);
    }
    wrap.appendChild(t);
    box.appendChild(wrap);
  }

  // --- DUMP (M7): part → label → DUMP → result. gatbox-web queues it; gatbox-dump.service does the reading. -----
  // Reads only: nothing on this screen writes to a chip (hard rule 6).
  const D = {part: null, label: null, family: null, parts: null, fams: null, st: null, chip: null};
  async function loadDump() {
    try {
      const [st, pl] = await Promise.all([api("GET", "/api/dump"), D.fams ? null : api("GET", "/api/dump/parts")]);
      D.st = st;
      if (pl) D.fams = pl.families;
    } catch (e) { return fail(e); }
    renderDump();
  }
  function onDump(st) { D.st = st; if (S.view === "dump") renderDump(); }
  function dumpReq(extra) {
    const body = Object.assign({part: D.part, label: D.label}, D.machine ? {machine: D.machine} : {}, extra || {});
    return api("POST", "/api/dump", body).then(() => toast(`DUMP queued: ${D.part} · ${D.label}`)).catch(fail);
  }
  async function pickDumpMachine() {
    let R;
    try { R = await api("GET", "/api/roster"); } catch (e) { return fail(e); }
    const pick = await sheet("Which machine is this chip from?", (pane, done) => {
      let q = "";
      const field = el("div", "kp-field"), list = el("div", "picklist");
      const draw = () => {
        field.textContent = q || "type to filter"; field.appendChild(el("span", "cur"));
        clear(list);
        const Q = q.toLowerCase();
        for (const m of R.machines.filter(m => !Q || m.name.toLowerCase().includes(Q) || m.slug.includes(Q)).slice(0, 60)) {
          const b = el("button", "row");
          add(b, el("b", null, m.name), el("div", "mut", m.slug));
          b.addEventListener("click", () => done(m));
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
    if (pick) { D.machine = pick.slug; D.machineName = pick.name; D.chip = null; renderDump(); }
  }
  // WHICH CHIP?: the machine's MAME chips, not yet dumped first. Picking one fills the label with its printed name
  // (MAME's name before the board location: epr-15781c.ic18 -> EPR-15781C) and marks the family tiles of its size.
  // The exact part is still picked off the chip: nothing here guesses it (hard rule 6).
  const chipLabel = r => r.name.split(".")[0].toUpperCase().replace(/[^A-Z0-9._-]/g, "-").slice(0, 40) || null;
  async function pickChip(slug) {
    let j, v;
    try { [j, v] = await mameList(slug); } catch (e) { return fail(e); }
    if (!v) return toast(j.status === "none" ? `Not in MAME: ${j.why}` : (j.why || "No MAME chip list for this machine"), "warn", 6000);
    const roms = v.roms.filter(r => !r.dumped.length).concat(v.roms.filter(r => r.dumped.length));
    const pick = await pickFrom(`Which chip? ${v.desc} (${v.dumped} of ${v.roms.length} dumped)`, roms.map(r => ({
      key: r.name, title: (r.dumped.length ? "✓ " : "") + r.name, find: [r.name],
      sub: `${KB(r.size)}${famsFor(r.size).length ? " · " + famsFor(r.size).map(f => f.label).join(" or ") : ""}`
           + (r.dumped.length ? ` · dumped as ${r.dumped[0].label}` : "") + (r.sha1 ? "" : " · MAME has no hash")})), D.chip && D.chip.name);
    if (!pick) return;
    D.chip = v.roms.find(r => r.name === pick); D.chipFor = slug;
    D.label = chipLabel(D.chip);
    renderDump();
  }
  async function pickFamily(f) {
    let r;
    try { r = await api("GET", "/api/dump/parts?family=" + encodeURIComponent(f.id)); } catch (e) { return fail(e); }
    const pick = await sheet(`${f.label}: which one is printed on the chip?`, (pane, done) => {
      pane.appendChild(el("div", "mut", "minipro's own names. The maker's prefix matters (TMS, AM, M, MBM…): the chip-ID check " +
                                        "stops a wrong pick and names the right one."));
      const box = el("div", "scroll");
      box.style.cssText = "overflow:auto;min-height:0;display:flex;flex-direction:column;gap:6px";
      partLists(box, r.parts, done);
      pane.appendChild(box);
    });
    if (pick) { D.part = pick; renderDump(); }
  }
  async function searchParts() {
    const q = await keypad({title: "Search minipro's part names (e.g. 27C020, MBM27, 2764)", max: 30});
    if (!q) return;
    let r;
    try { r = await api("GET", "/api/dump/parts?q=" + encodeURIComponent(q)); } catch (e) { return fail(e); }
    const pick = await sheet(`"${q}": ${r.parts.length}${r.more ? "+" : ""} parts`, (pane, done) => {
      if (!r.parts.length) pane.appendChild(el("div", "mut", "Nothing matches. Try fewer characters."));
      const box = el("div", "scroll");
      box.style.cssText = "overflow:auto;min-height:0;display:flex;flex-direction:column;gap:6px";
      partLists(box, r.parts, done);
      pane.appendChild(box);
    });
    if (pick) { D.part = pick; renderDump(); }
  }
  // DIP parts go straight in the T48's socket; TSOP / PLCC / SOP need an adapter (2026-09-29: a DIP chip picked as
  // TSOP32 by one tap answered 0xFEFF). So DIP first, and the rest apart, under a warning.
  const isDip = n => !n.includes("@") || /@DIP/i.test(n);
  function partLists(box, names, done) {
    const dip = names.filter(isDip), other = names.filter(n => !isDip(n));
    const list = items => {
      const l = el("div", "picklist");
      for (const n of items) { const b = el("button", "row", n); b.addEventListener("click", () => done(n)); l.appendChild(b); }
      return l;
    };
    if (dip.length) add(box, el("h3", null, "DIP: goes straight in the socket (names without @ are DIP too)"), list(dip));
    if (other.length) {
      const h = el("h3", "warn", "Other packages: only with a socket adapter (TSOP, PLCC, SOP…)");
      add(box, h, list(other));
    }
  }
  function renderDump() {
    if (D.holding) { D.pending = true; return; }       // a hold to burn is under a finger: redraw when it ends
    const B = clear($("#du-body")), st = D.st || {}, t48 = (st.t48 || {}).present;
    const head = el("div", "card");
    add(head, el("div", "big " + (t48 ? "ok" : "mut"), t48 ? "T48 READY" : "T48 NOT PLUGGED IN"),
        el("div", "mut", (t48 ? `USB ${st.t48.usb_id} · ` : "Plug the XGecu T48 into the Pi (USB a466:0a53). ") +
                         "READ a chip, or BURN one (a blank chip, armed by a hold on the Pi's own screen)"));
    if (!st.spool) head.appendChild(el("div", "warn", "The dump service isn't installed yet (no spool)."));
    B.appendChild(head);
    const s = st.status;
    if (st.busy || (s && s.state === "running")) {
      const c = el("div", "card");
      const r = st.request || s || {};                 // the job queued or running, else the last one's status
      add(c, el("h2", null, `${{blank: "BLANK CHECK", burn: "BURNING"}[r.op] || "DUMPING"} ${r.part || D.part || ""}`));
      const bar = el("div"); bar.style.cssText = "height:10px;border-radius:5px;background:var(--line);overflow:hidden;margin:6px 0";
      const fill = el("div"); fill.style.cssText = `height:100%;width:${Math.round(((s && s.progress) || 0.02) * 100)}%;background:var(--cyan)`;
      bar.appendChild(fill); c.appendChild(bar);
      for (const x of (s && s.steps) || []) c.appendChild(el("div", "mut", `${x.step}${x.detail ? ": " + x.detail : ""}`));
      c.appendChild(el("div", "mut", st.queued ? "queued: starting…" : "keep the chip in the socket until it's done"));
      B.appendChild(c);
    } else if (s && s.state) {
      B.appendChild(resultCard(s));
    }
    // the picker
    const p = el("div", "card");
    // the machine this dump is archived under: the current one unless picked here (just for dumps: picking one here
    // doesn't change the logger's machine, so a bench dump never starts a new log file)
    const mrow = el("div", "btnrow");
    mrow.style.alignItems = "center";
    const cur_m = D.machine ? D.machineName : ((S.st && (S.st.machine_name || S.st.machine)) || null);
    const mchip = el("span", "chip", cur_m || "unassigned");
    const other = el("button", null, "OTHER MACHINE");
    other.addEventListener("click", pickDumpMachine);
    add(mrow, el("b", null, "FOR"), mchip, other);
    if (D.machine) {
      const back = el("button", null, "CURRENT");
      back.addEventListener("click", () => { D.machine = null; D.chip = null; renderDump(); });
      mrow.appendChild(back);
    }
    p.appendChild(mrow);
    const seg = el("div", "seg");
    seg.style.margin = "8px 0";
    for (const [k, t] of [["read", "READ"], ["burn", "BURN"]]) {
      const b = el("button", (D.mode || "read") === k ? "on" : null, t);
      b.addEventListener("click", () => { D.mode = k; renderDump(); });
      seg.appendChild(b);
    }
    p.appendChild(seg);
    if (D.mode === "burn") { burnPicker(p, st, t48); B.appendChild(p); return; }
    const forSlug = D.machine || (S.st && S.st.machine);
    if (D.chip && D.chipFor !== forSlug) {                    // another machine: that chip (and its label) isn't on it
      if (D.label === chipLabel(D.chip)) D.label = null;
      D.chip = null;
    }
    if (forSlug) {
      const crow = el("div", "btnrow"), cb = el("button", null, D.chip ? `CHIP: ${D.chip.name}` : "WHICH CHIP? (MAME's list)");
      crow.style.cssText = "align-items:center;margin-top:6px";
      cb.addEventListener("click", () => pickChip(forSlug));
      crow.appendChild(cb);
      if (D.chip) crow.appendChild(el("span", "mut", `${KB(D.chip.size)}${famsFor(D.chip.size).length ? " · fits " + famsFor(D.chip.size).map(f => f.label).join(" or ") : ""}`));
      p.appendChild(crow);
    }
    add(p, el("h2", null, "1 · PART"));
    partSection(p, D.chip && D.chip.size);
    add(p, el("h2", null, "2 · LABEL"));
    const lb = el("button", null, D.label ? "LABEL: " + D.label : "TYPE THE CHIP'S LABEL");
    lb.addEventListener("click", async () => {
      const v = await keypad({title: "The chip's label (e.g. EPR-15781C, LG-U12)", max: 40, value: D.label || ""});
      if (v != null) { D.label = v.replace(/ /g, "-") || null; renderDump(); }
    });
    p.appendChild(lb);
    add(p, el("h2", null, "3 · DUMP"));
    const go = el("button", null, "DUMP");
    go.style.cssText = "min-height:72px;min-width:220px;font-size:24px;border-color:var(--mag)";
    go.disabled = !(t48 && D.part && D.label && !st.busy && st.spool);
    go.addEventListener("click", () => dumpReq());
    add(p, go, el("span", "mut", "  reads the chip twice, compares, asks MAME, archives it"));
    B.appendChild(p);
    const L = el("div", "card");
    L.appendChild(el("h2", null, "Dumps for this machine"));
    B.appendChild(L);
    api("GET", "/api/dumps" + (D.machine ? "?machine=" + encodeURIComponent(D.machine) : ""))
      .then(r => dumpList(L, r.dumps)).catch(() => {});
  }
  // the part: family tiles (lit for a size: the MAME chip's, or the image's) → minipro's exact names; or SEARCH
  function partSection(p, size) {
    const row = el("div", "btnrow");
    row.style.alignItems = "center";
    if (D.part) {                                  // picked: the tiles fold away so the next steps stay on screen
      const cur = el("span", "chip", D.part);
      cur.style.fontSize = "18px";
      const change = el("button", null, "CHANGE");
      change.addEventListener("click", () => { D.part = null; renderDump(); });
      add(row, cur, change);
      if (!isDip(D.part)) row.appendChild(el("span", "warn", "not DIP: only with a socket adapter"));
    }
    const srch = el("button", null, "SEARCH");
    srch.addEventListener("click", searchParts);
    row.appendChild(srch);
    p.appendChild(row);
    if (!D.part) {
      const tiles = el("div", "tiles");
      tiles.style.gridTemplateColumns = "repeat(auto-fill,minmax(150px,1fr))";
      for (const f of D.fams || []) {
        const b = el("button", "tile" + (size && (f.bytes || []).includes(size) ? " on" : ""));
        b.style.minHeight = "64px";
        add(b, el("b", null, f.label), el("span", "mut", f.size));
        b.addEventListener("click", () => pickFamily(f));
        tiles.appendChild(b);
      }
      p.appendChild(tiles);
    }
  }

  // BURN (2026-09-29, the owner's decision): an image from the archive into a blank chip. BLANK CHECK first; then, on
  // the Pi's own screen only, a 3 s hold arms the burn (it stands in for the physical ARM button to come: the server
  // refuses a burn from anywhere else). gatbox-dump.service writes, minipro verifies, and two read-backs are compared
  // with the image. A phone sees everything but the hold.
  function burnPicker(p, st, t48) {
    add(p, el("h2", null, "1 · IMAGE"));
    const ir = el("div", "btnrow");
    ir.style.alignItems = "center";
    if (D.bimage) add(ir, el("span", "chip", "IMAGE: " + D.bimage.name),
                      el("span", "mut", `${KB(D.bimage.size)} · ${D.bimage.matches[0] || (D.bimage.match === false ? "NO MATCH" : "not identified")} · SHA-1 ${D.bimage.sha1.slice(0, 8)}`));
    const pick = el("button", null, D.bimage ? "OTHER IMAGE" : "PICK AN IMAGE (the archive, _images/)");
    pick.addEventListener("click", pickBurnImage);
    ir.appendChild(pick);
    p.appendChild(ir);
    add(p, el("h2", null, "2 · PART"));
    partSection(p, D.bimage && D.bimage.size);
    add(p, el("h2", null, "3 · BLANK CHECK"));
    const bc = el("button", null, "BLANK CHECK");
    bc.disabled = !(t48 && D.bimage && D.part && !st.busy && st.spool);
    bc.addEventListener("click", () => burnReq("/api/burn/blank"));
    add(p, bc, el("span", "mut", "  the part's size against the image, the pin check, minipro's blank check"));
    add(p, el("h2", null, "4 · BURN"));
    const s = st.status || {}, now = (Date.now() + S.offset) / 1000;
    const ready = D.bimage && D.part && !st.busy && s.op === "blank" && s.state === "done" && s.blank_ok === true
                  && s.part === D.part && (s.image || "").endsWith("/" + D.bimage.image) && now - (s.finished_at || 0) < 300;
    if (!ready) p.appendChild(el("div", "mut", "after a BLANK CHECK of this image and part that passed (good for 5 minutes)"));
    else if (!S.local) p.appendChild(el("div", "warn", "BLANK · arm it at the Pi: the hold to burn is on the Pi's own screen"));
    else p.appendChild(holdButton(`HOLD 3 S TO BURN ${D.bimage.name} → ${D.part}`, 3000, () => burnReq("/api/burn")));
  }
  async function pickBurnImage() {
    let r;
    try { r = await api("GET", "/api/burn/images"); } catch (e) { return fail(e); }
    if (!r.images.length) return toast("No images yet: dump a chip first, or copy a .bin into /srv/gatbox/roms/_images", "warn", 6000);
    const pick = await pickFrom("Which image goes into the chip?", r.images.map(x => ({
      key: x.image, title: x.label || x.name, find: [x.image.toLowerCase(), (x.label || "").toLowerCase()],
      sub: `${x.folder} · ${x.name} · ${KB(x.size)} · ${x.matches[0] || (x.match === false ? "NO MATCH" : "not identified")}`})),
      D.bimage && D.bimage.image);
    if (!pick) return;
    D.bimage = r.images.find(x => x.image === pick); D.part = null; D.byes = null;    // a new image: pick its part next
    renderDump();
  }
  function burnReq(path) {
    // D.byes: the part the owner confirmed a non-JEDEC pinout for; it covers that part only, never a later CHANGE
    const body = Object.assign({image: D.bimage.image, part: D.part}, D.machine ? {machine: D.machine} : {},
                               D.byes && D.byes === D.part ? {yes: true} : {});
    return api("POST", path, body)
      .then(() => toast(path.endsWith("/blank") ? `BLANK CHECK queued: ${D.part}` : `BURN armed: ${D.bimage.name} → ${D.part}`))
      .catch(e => { fail(e); renderDump(); });       // a refused burn: a fresh hold button replaces the spent one
  }
  // hold to act (like SHUT DOWN): letting go early does nothing; while it's held, the tab doesn't redraw under it.
  // One pointer holds it (a second finger is ignored, and so is its release), and once it has fired it's spent
  function holdButton(label, ms, fire) {
    const b = el("button", "hold bad", label);
    b.style.cssText = "min-height:72px;font-size:20px;width:100%";
    b.style.setProperty("--ms", ms + "ms");
    let t = null, who = null;
    const end = () => { D.holding = false; if (D.pending) { D.pending = false; setTimeout(renderDump, 0); } };
    const stop = e => {
      if (t === null || e.pointerId !== who) return;
      clearTimeout(t); t = who = null; b.classList.remove("arm"); b.textContent = label;
      end();
    };
    b.addEventListener("pointerdown", e => {
      e.preventDefault();
      if (t !== null || b.dataset.done) return;
      who = e.pointerId; D.holding = true; b.classList.add("arm"); b.textContent = "KEEP HOLDING…";
      t = setTimeout(() => { t = who = null; b.dataset.done = 1; b.textContent = "ARMED: STARTING…"; end(); fire(); }, ms);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach(k => b.addEventListener(k, stop));
    return b;
  }
  function burnResult(s) {
    const c = el("div", "card"), img = (s.image || "").split("/").pop();
    if (s.op === "blank" && s.state === "done") {
      add(c, el("div", "big ok", "BLANK"), el("div", "mut", `${s.part} · ${KB(s.size || 0)} · pin check ${s.pin_check || "?"} · ready for ${img}`));
    } else if (s.op === "burn" && s.state === "done" && s.verified) {
      add(c, el("div", "big ok", "VERIFIED"), el("div", null, `${s.part} now holds ${img}`),
          el("div", "mut", `SHA-1 ${s.sha1} · ${KB(s.size || 0)} · VPP ${s.vpp || "?"}`),
          el("div", "mut", "minipro's verify OK · 2 read-backs identical to the image"));
    } else {
      const written = s.op === "burn" && (s.written || /write failed|read-backs/.test(s.error || ""));
      const big = s.op === "blank" ? (s.blank_ok === false ? "NOT BLANK" : "STOPPED") : written ? "FAILED" : "NOT BURNED";
      add(c, el("div", "big bad", big), el("div", null, s.error || ""), s.hint ? el("div", "mut", "→ " + s.hint) : null,
          written ? el("div", "warn", "the chip was written, so it may be partly programmed: BLANK CHECK it before using it again") : null);
      const again = el("div", "btnrow");
      const btn = (t, f) => { const b = el("button", null, t); b.addEventListener("click", f); again.appendChild(b); };
      const m = /again with -p (\S+)/.exec(s.hint || "");
      if (m) btn("USE " + m[1], () => { D.part = m[1]; renderDump(); });
      if (/--yes/.test(s.hint || "") && D.bimage) btn("I CHECKED: BLANK CHECK AGAIN", () => { D.byes = D.part; burnReq("/api/burn/blank"); });
      if (again.childNodes.length) c.appendChild(again);
    }
    return c;
  }
  function resultCard(s) {
    if (s.op === "blank" || s.op === "burn") return burnResult(s);
    const c = el("div", "card");
    if (s.state === "done") {
      const ri = s.romident || {}, a = s.archived || {};
      if (ri.match) {
        add(c, el("div", "big ok", "MATCH"));
        for (const m of ri.matches) c.appendChild(el("div", null, `${m.set} / ${m.rom} · ${m.description}`));
      } else if (ri.match === false) {
        add(c, el("div", "big warn", "NO MATCH"),
            el("div", "mut", "MAME doesn't know this ROM: a revision, a modified dump, or a bad read. Compare with the chip's sticker."));
      } else add(c, el("div", "big warn", "NOT IDENTIFIED"), el("div", "mut", "MAME couldn't run"));
      add(c, el("div", "mut", `${s.part} · ${s.label} · ${(s.size || 0).toLocaleString("en-US")} bytes · 2 reads identical`),
          el("div", "mut", `SHA-1 ${s.sha1} · CRC32 ${s.crc32}`),
          el("div", "mut", (a.new ? "archived: " : "already archived: ") + (a.path || "")));
      if (s.blank) c.appendChild(el("div", "warn", "archived although it reads " + s.blank));
    } else {
      add(c, el("div", "big bad", "STOPPED"), el("div", null, s.error || ""), s.hint ? el("div", "mut", "→ " + s.hint) : null);
      const again = el("div", "btnrow"), same = {part: s.part, label: s.label};
      const m = /dump again with -p (\S+)/.exec(s.hint || "");
      const btn = (t, f) => { const b = el("button", null, t); b.addEventListener("click", f); again.appendChild(b); };
      if (m) btn("USE " + m[1], () => { D.part = m[1]; D.label = s.label; dumpReq(); });
      if (/--ignore-id/.test(s.hint || "")) btn("READ ANYWAY (ignore the ID)", () => { Object.assign(D, same); dumpReq({ignore_id: true}); });
      if (/--keep-blank/.test(s.hint || "")) btn("ARCHIVE ANYWAY", () => { Object.assign(D, same); dumpReq({keep_blank: true}); });
      if (/--yes/.test(s.hint || "")) btn("I CHECKED: DUMP", () => { Object.assign(D, same); dumpReq({yes: true}); });
      if (/reseat|differ|seated|lever/.test((s.hint || "") + (s.error || ""))) btn("TRY AGAIN", () => { Object.assign(D, same); dumpReq(); });
      if (again.childNodes.length) c.appendChild(again);
    }
    return c;
  }
  function dumpList(box, list) {
    if (!list.length) { box.appendChild(el("div", "mut", "None yet.")); return; }
    for (const d of list.slice(0, 20)) {
      const r = el("div");
      r.style.margin = "4px 0";
      add(r, el("b", null, d.label + " "), el("span", d.match ? "tag ok" : "tag warn", d.match ? "MATCH" : "NO MATCH"),
          el("div", "mut", `${(d.matches[0] && d.matches[0].set + "/" + d.matches[0].rom) || ""} ${d.part} · ${(d.date || "").slice(0, 16).replace("T", " ")} · ${d.sha1.slice(0, 8)}`));
      box.appendChild(r);
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
