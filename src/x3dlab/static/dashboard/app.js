/* x3d.lab dashboard. Plain JS, no build step. Every URL is relative, so the /k/<key>/ prefix travels with it.
 *
 * Chrome DevTools integration:
 *   Performance  server spans (validator, MCP, model calls) -> custom tracks in the "x3d.lab" group
 *                via performance.measure(..., {detail: {devtools: {...}}})
 *   Console      styled event lines, one collapsed group per finished scene, console.table of issues,
 *                custom formatter for issue objects, window.x3dlab helpers
 *   Network      the event stream (EventStream tab) and Server-Timing on every API response
 */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const LEVELS = { DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40 };
  const ROUTE = /trycloudflare\.com|ngrok/.test(location.host) ? "tunnel" : /googleusercontent|colab/.test(location.host) ? "colab" : "local";
  const COLORS = { Validator: "primary", MCP: "primary-light", LLM: "secondary", Blender: "tertiary", X_ITE: "tertiary-light", Gateway: "secondary-light", Lab: "primary-dark" };
  const state = { health: null, browser: null, events: [], paused: false, lastSeq: 0, job: null, loaf: 0, measures: 0, sceneXml: null };

  // ------------------------------------------------------------------ helpers
  async function api(path, opts = {}) {
    const t0 = performance.now();
    const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
    let body = null;
    try { body = await r.json(); } catch (_) { /* 204 */ }
    const st = timingOf(r.url);
    if (consoleOn()) console.debug("%c%s %s%c %d in %d ms%s", "color:#888", opts.method || "GET", path, "", r.status,
      Math.round(performance.now() - t0), st ? " · server-timing " + st : "");
    if (!r.ok) throw new Error((body && body.error) || ("HTTP " + r.status));
    return body;
  }
  function timingOf(url) {
    const e = performance.getEntriesByName(url).pop();
    return e && e.serverTiming && e.serverTiming.length ? e.serverTiming.map((s) => s.name + "=" + Math.round(s.duration) + "ms").join(" ") : "";
  }
  function beacon(type, data) {
    fetch("api/beacon", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ type, data }), keepalive: true }).catch(() => {});
  }
  function el(tag, attrs = {}, text) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const consoleOn = () => $("#log-console").checked;
  function webglRenderer() {
    try {
      const gl = document.createElement("canvas").getContext("webgl2") || document.createElement("canvas").getContext("webgl");
      const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl ? gl.getParameter(gl.RENDERER) : "no WebGL";
    } catch (_) { return "unknown"; }
  }

  // ------------------------------------------------------------------ health and controls
  async function refreshHealth() {
    try {
      const h = await api("api/health");
      state.health = h;
      const pills = $("#pills");
      pills.replaceChildren();
      const add = (text, cls) => pills.appendChild(el("span", { class: "pill " + (cls || "") }, text));
      add("MCP " + h.mcp, h.mcp === "up" ? "ok" : "bad");
      const withKeys = Object.entries(h.llm).filter(([, v]) => v.has_key).map(([k]) => k);
      add(withKeys.length ? "models: " + withKeys.join(", ") : "no API key (sample mode)", withKeys.length ? "ok" : "warn");
      add("route: " + ROUTE);
      add("X_ITE " + h.pins.x_ite);
      add((h.runtime.colab ? "Colab " : "") + (h.runtime.gpu_kind === "CPU" ? "CPU only" : "GPU " + h.runtime.gpu_kind));
      add("model runs " + h.jobs.llm_runs + "/" + h.jobs.llm_limit);
      $("#copilot-link").hidden = !h.copilot;
      fillProviders(h.llm);
      if (!$("#defect").options.length) {
        for (const d of h.breakit) $("#defect").appendChild(el("option", { value: d.id }, d.title));
      }
    } catch (e) {
      $("#pills").replaceChildren(el("span", { class: "pill bad" }, "gateway: " + e.message));
    }
  }
  function fillProviders(llm) {
    const sel = $("#provider"), cur = sel.value;
    sel.replaceChildren(el("option", { value: "auto" }, "auto"));
    for (const [p, v] of Object.entries(llm)) if (v.has_key) sel.appendChild(el("option", { value: p }, v.label));
    sel.appendChild(el("option", { value: "sample" }, "no model (sample scene)"));
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    fillModels();
  }
  function fillModels() {
    const p = $("#provider").value, m = $("#model"), llm = (state.health || {}).llm || {};
    m.replaceChildren();
    const info = llm[p];
    if (!info) { m.appendChild(el("option", { value: "" }, p === "sample" ? "—" : "default")); return; }
    for (const id of info.models.length ? info.models : [info.model || ""]) m.appendChild(el("option", { value: id }, id || "default"));
    if (info.model) m.value = info.model;
  }

  // ------------------------------------------------------------------ X_ITE viewer
  function initViewer() {
    if (!window.X3D) { $("#view-info").textContent = "X_ITE did not load (offline CDN?)"; return; }
    customElements.whenDefined("x3d-canvas").then(() => {
      state.browser = X3D.getBrowser($("#view"));
      $("#view-info").textContent = "X_ITE " + state.browser.version + " ready";
      beacon("hello", { route: ROUTE, ua: navigator.userAgent, gl: webglRenderer(), dpr: devicePixelRatio, xite: state.browser.version });
    });
  }
  async function view(source, label) {
    const b = state.browser;
    if (!b) return;
    const isXml = /^\s*</.test(source);
    const errors = [];
    const origErr = console.error, origWarn = console.warn;
    console.error = (...a) => { errors.push(a.map(String).join(" ").slice(0, 300)); origErr.apply(console, a); };
    console.warn = (...a) => { errors.push("warn: " + a.map(String).join(" ").slice(0, 300)); origWarn.apply(console, a); };
    performance.mark("xite-load-start");
    const t0 = performance.now();
    try {
      if (isXml) { const s = await b.createX3DFromString(source); await b.replaceWorld(s); }
      else await b.loadURL(new X3D.MFString(new URL(source, location.href).href));
      const ms = performance.now() - t0;
      performance.measure("X_ITE load " + (label || ""), { start: t0, end: t0 + ms,
        detail: { devtools: { dataType: "track-entry", track: "X_ITE", trackGroup: "x3d.lab", color: errors.length ? "error" : "tertiary",
          properties: [["source", label || (isXml ? "inline" : source)], ["console messages", String(errors.length)]], tooltipText: "X_ITE load" } } });
      let nodes = 0;
      try { nodes = b.currentScene.rootNodes.length; } catch (_) {}
      $("#view-info").textContent = "X_ITE " + b.version + " · " + Math.round(ms) + " ms · " + nodes + " root nodes" + (errors.length ? " · " + errors.length + " console message(s)" : "");
      beacon("xite-load", { ms: Math.round(ms), root_nodes: nodes, errors: errors.slice(0, 10), label: label || "", route: ROUTE });
      return { ok: true, ms, errors };
    } catch (e) {
      $("#view-info").textContent = "X_ITE could not load it: " + e;
      beacon("xite-error", { error: String(e).slice(0, 300), label: label || "", errors: errors.slice(0, 10) });
      return { ok: false, error: String(e), errors };
    } finally {
      console.error = origErr; console.warn = origWarn;
    }
  }

  // ------------------------------------------------------------------ verdicts
  function showVerdict(v, title) {
    const box = $("#verdict");
    box.replaceChildren();
    if (!v) return;
    const ok = v.ok;
    box.appendChild(el("div", { class: "verdict-head " + (ok ? "ok" : "bad") },
      (title ? title + ": " : "") + (ok ? "VALID" : "NOT VALID") + " · XSD " + (v.xsd_valid ? "ok" : "failed") + " · " + v.errors + " error(s), " + v.warnings + " warning(s)"));
    const issues = v.issues || [];
    if (!issues.length) return;
    const t = el("table", { class: "issues" });
    t.appendChild(el("tr")).append(el("th", {}, "severity"), el("th", {}, "layer"), el("th", {}, "rule"), el("th", {}, "line"), el("th", {}, "message"));
    for (const i of issues.slice(0, 40)) {
      const tr = el("tr");
      tr.append(el("td", {}, i.severity), el("td", {}, i.layer), el("td", {}, i.rule), el("td", {}, i.line || ""), el("td", {}, i.message));
      t.appendChild(tr);
    }
    box.appendChild(t);
  }
  function issueObjects(issues) {
    return (issues || []).map((i) => ({ __x3dIssue: true, ...i }));
  }

  // ------------------------------------------------------------------ the event stream
  function onEvent(ev) {
    state.events.push(ev);
    if (state.events.length > 3000) state.events.splice(0, 1000);
    state.lastSeq = ev.seq;
    toTimeline(ev);
    toLog(ev);
    toConsole(ev);
    const d = ev.data || {};
    if (ev.kind === "step" && ev.job === state.job) addStep(ev.msg, d, ev);
    if (ev.kind === "scene") onScene(ev);
    if (ev.kind === "job_end" && ev.job === state.job) $("#run").disabled = false;
  }
  function toTimeline(ev) {
    const sp = ev.span;
    if (!sp) return;
    const start = sp.start * 1000 - performance.timeOrigin, end = sp.end * 1000 - performance.timeOrigin;
    if (start < 0 || end < start) return;               // happened before this page opened
    const props = Object.entries(ev.data || {}).slice(0, 8).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]);
    if (ev.job) props.unshift(["job", ev.job]);
    try {
      performance.measure(sp.name, { start, end, detail: { devtools: { dataType: "track-entry", track: sp.track || "Lab", trackGroup: "x3d.lab",
        color: (ev.data && ev.data.status === "error") ? "error" : (COLORS[sp.track] || "primary"), properties: props, tooltipText: ev.msg } } });
      state.measures++;
    } catch (_) { /* older browsers */ }
  }
  function passes(ev) {
    if (LEVELS[ev.level] < LEVELS[$("#log-level").value]) return false;
    const f = $("#log-filter").value.trim().toLowerCase();
    return !f || ev.src.toLowerCase().includes(f) || ev.msg.toLowerCase().includes(f);
  }
  function line(ev) {
    const t = new Date(ev.ts * 1000).toTimeString().slice(0, 8);
    let s = t + " " + ev.level.padEnd(5) + " " + ev.src.slice(0, 9).padEnd(9) + " " + ev.msg;
    const dens = $("#log-density").value;
    if (dens !== "compact" && ev.data) {
      const pairs = Object.entries(ev.data).slice(0, dens === "rich" ? 6 : 50).map(([k, v]) => k + "=" + (typeof v === "string" ? v : JSON.stringify(v)).slice(0, dens === "rich" ? 60 : 2000));
      s += dens === "rich" ? "  | " + pairs.join(" ") : "\n    " + pairs.join("\n    ");
    }
    return s;
  }
  function toLog(ev) {
    if (state.paused || !passes(ev)) return;
    const log = $("#log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.appendChild(el("div", { class: ev.level }, line(ev)));
    while (log.childNodes.length > 800) log.removeChild(log.firstChild);
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  const BADGE = { ERROR: "background:#b3261e;color:#fff", WARN: "background:#9a5b00;color:#fff", INFO: "background:#1f7a5c;color:#fff", DEBUG: "background:#777;color:#fff" };
  function toConsole(ev) {
    if (!consoleOn() || !passes(ev) || ev.kind === "scene") return;
    const fn = ev.level === "ERROR" ? console.error : ev.level === "WARN" ? console.warn : ev.level === "DEBUG" ? console.debug : console.log;
    const dens = $("#log-density").value;
    const args = ["%c" + ev.src + "%c " + ev.msg, BADGE[ev.level] + ";border-radius:3px;padding:0 4px", ""];
    if (dens !== "compact" && ev.data) args.push(ev.data);
    fn.apply(console, args);
  }
  function addStep(msg, d, ev) {
    const li = el("li", {}, msg);
    li.appendChild(el("span", { class: "t" }, (d.ok === false ? "✗ " : d.ok === true ? "✓ " : "") + (d.errors !== undefined ? d.errors + " err" : "")));
    $("#steps").appendChild(li);
  }
  async function onScene(ev) {
    const d = ev.data || {};
    console.count("x3d.lab scene " + (d.valid ? "VALID" : "not valid"));
    if (consoleOn()) {
      console.groupCollapsed("%cscene " + ev.job + ": " + (d.valid ? "VALID" : "NOT VALID") + "%c  " + (d.provider || "") + " " + (d.model || ""),
        "font-weight:600;color:" + (d.valid ? "#1d6b3a" : "#b3261e"), "color:#888");
      console.log("first pass valid:", d.first_pass_valid, "· repair rounds:", d.rounds, "· usage:", d.usage);
      if (d.issues && d.issues.length) { console.table(d.issues.map(({ severity, layer, rule, line, message }) => ({ severity, layer, rule, line, message }))); console.log(...issueObjects(d.issues)); }
      console.log("files:", d.files);
      console.groupEnd();
    }
    if (ev.job !== state.job && state.job) return;
    if (d.files && d.files["scene.x3d"]) {
      $("#dl").href = d.files["scene.x3d"]; $("#dl").hidden = false;
      await view(d.files["scene.x3d"], "scene " + ev.job);
    }
    try { const j = await api("api/jobs/" + ev.job); if (j.result) showVerdict(j.result.verdict, "scene " + ev.job); } catch (_) {}
  }
  function connect() {
    const es = new EventSource("events?since=" + state.lastSeq);
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.warn("bad event", e); } };
    es.onopen = () => { $("#footer").textContent = "live · route " + ROUTE; };
    es.onerror = () => { $("#footer").textContent = "event stream reconnecting…"; };
    state.es = es;
  }

  // ------------------------------------------------------------------ actions
  async function run() {
    $("#run").disabled = true; $("#steps").replaceChildren(); showVerdict(null);
    const params = { prompt: $("#prompt").value, provider: $("#provider").value, model: $("#model").value || null,
      mode: $("#mode").value, rounds: +$("#rounds").value, version: $("#version").value };
    try {
      const r = await api("api/jobs", { method: "POST", body: JSON.stringify({ kind: "scene", params }) });
      state.job = r.job;
      $("#steps").appendChild(el("li", {}, "job " + r.job + " started"));
      for (const ev of state.events) if (ev.job === r.job && ev.kind === "step") addStep(ev.msg, ev.data || {}, ev);   // arrived before the reply
      if (state.events.some((ev) => ev.job === r.job && ev.kind === "job_end")) $("#run").disabled = false;
    } catch (e) {
      $("#steps").appendChild(el("li", {}, "refused: " + e.message)); $("#run").disabled = false;
    }
  }
  async function inject() {
    const id = $("#defect").value, guess = $("#guess").value;
    $("#inject").disabled = true;
    try {
      const o = await api("api/breakit", { method: "POST", body: JSON.stringify({ defect: id }) });
      const names = { xml: "the XML parser", xsd: "the XSD schema", semantic: "the semantic check", nobody: "nobody" };
      const verdictText = guess ? (guess === o.caught_by ? "You were right. " : "Not quite. ") : "";
      $("#breakit-out").textContent = verdictText + "Caught by " + names[o.caught_by] + (o.rules.length ? " (" + o.rules.join(", ") + ")" : "") +
        ". " + o.lesson + (o.repaired ? " repair_x3d fixed it on its own." : "");
      $("#paste").value = o.xml;
      showVerdict(await api("api/validate", { method: "POST", body: JSON.stringify({ xml: o.xml }) }), "break-it " + id);
      const x = await view(o.xml, "break-it " + id);
      if (x) {
        const said = (x.errors || []).filter((m) => !/long animation frame/.test(m));
        const runtime = !x.ok ? " X_ITE refused to load it: " + x.error.slice(0, 160)
          : said.length ? " X_ITE loaded it but complained: " + said[0].replace(/^warn: /, "").slice(0, 180)
          : " X_ITE loaded it without a word" + (o.caught_by === "nobody" ? "." : ", so only the validator saw the problem.");
        $("#breakit-out").textContent += runtime;
      }
    } catch (e) { $("#breakit-out").textContent = "error: " + e.message; }
    $("#inject").disabled = false;
  }
  async function validatePaste() {
    const xml = $("#paste").value;
    if (!xml.trim()) return;
    try { showVerdict(await api("api/validate", { method: "POST", body: JSON.stringify({ xml }) }), "pasted"); }
    catch (e) { showVerdict(null); $("#verdict").textContent = "error: " + e.message; }
  }

  // ------------------------------------------------------------------ DevTools extras
  window.devtoolsFormatters = (window.devtoolsFormatters || []).concat([{
    header(o) {
      if (!o || !o.__x3dIssue) return null;
      const c = o.severity === "error" ? "#b3261e" : o.severity === "warning" ? "#9a5b00" : "#1f7a5c";
      return ["span", {}, ["span", { style: "background:" + c + ";color:#fff;border-radius:3px;padding:0 4px" }, o.layer + "/" + o.rule],
        ["span", { style: "margin-left:6px" }, (o.line ? "line " + o.line + ": " : "") + o.message]];
    },
    hasBody() { return false; },
  }]);
  if ("PerformanceObserver" in window && PerformanceObserver.supportedEntryTypes.includes("long-animation-frame")) {
    new PerformanceObserver((list) => { for (const e of list.getEntries()) if (e.duration > 150) { state.loaf++; if (consoleOn() && state.loaf <= 5) console.warn("long animation frame %d ms (later ones are only counted: x3dlab.stats)", Math.round(e.duration), e); } })
      .observe({ type: "long-animation-frame", buffered: false });
  }
  window.x3dlab = {
    get events() { return state.events; },
    get health() { return state.health; },
    get browser() { return state.browser; },
    get stats() { return { events: state.events.length, measures: state.measures, longFrames: state.loaf, route: ROUTE }; },
    view: (src, label) => view(src, label),
    validate: (xml) => api("api/validate", { method: "POST", body: JSON.stringify({ xml }) }),
    run: (prompt, opts = {}) => api("api/jobs", { method: "POST", body: JSON.stringify({ kind: "scene", params: { prompt, ...opts } }) }),
    breakit: (id) => api("api/breakit", { method: "POST", body: JSON.stringify({ defect: id }) }),
    jobs: () => api("api/jobs"),
    spans: () => performance.getEntriesByType("measure").filter((m) => m.detail && m.detail.devtools),
    help() {
      console.log("%cx3d.lab console helpers", "font-weight:600;font-size:13px");
      console.table({
        "x3dlab.run(prompt, {provider, mode})": "start a scene job (watch the Performance panel while it runs)",
        "x3dlab.validate(xml)": "validate a string with x3d_mcp",
        "x3dlab.breakit('route_types')": "inject a defect",
        "x3dlab.view(urlOrXml)": "load into the X_ITE viewer",
        "x3dlab.events / x3dlab.spans()": "everything received / the DevTools track entries",
        "copy(x3dlab.events)": "copy all events as JSON (DevTools utility)",
      });
    },
  };

  // ------------------------------------------------------------------ wiring
  $("#provider").addEventListener("change", fillModels);
  $("#run").addEventListener("click", run);
  $("#prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run(); });
  $("#inject").addEventListener("click", inject);
  $("#validate").addEventListener("click", validatePaste);
  $("#view-paste").addEventListener("click", () => view($("#paste").value, "pasted"));
  $("#log-pause").addEventListener("click", (e) => { state.paused = !state.paused; e.target.textContent = state.paused ? "resume" : "pause"; });
  $("#log-clear").addEventListener("click", () => $("#log").replaceChildren());
  $("#devtools-help").addEventListener("click", () => $("#devtools-dialog").showModal());
  console.log("%cx3d.lab%c dashboard · type x3dlab.help() · route %s", "background:#1f7a5c;color:#fff;padding:2px 6px;border-radius:3px;font-weight:600", "", ROUTE);
  initViewer();
  refreshHealth();
  setInterval(refreshHealth, 15000);
  connect();
})();
