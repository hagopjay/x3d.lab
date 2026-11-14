/* X3D Lab dev console: X_ITE + SAI, no framework, no build step, no CDN.
 *
 * Everything here goes through X_ITE's public SAI (X3D.getBrowser, loadURL, currentScene, getNamedNode,
 * getFieldDefinitions, routes, addFieldCallback, createX3DFromString, toXMLString/toVRMLString/toJSONString).
 * window.x3dlab is this console's own helper API (the same one the REPL gets); the MCP mission may call it,
 * and documents when it does.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const params = new URLSearchParams(location.search);
  const SCENES = 'scenes/';
  const C = X3D.X3DConstants;
  const TYPE = {};
  for (const [k, v] of Object.entries(C)) if (/^(SF|MF)[A-Z]/.test(k) && typeof v === 'number') TYPE[v] = k;
  const ACCESS = { [C.initializeOnly]: 'initializeOnly', [C.inputOnly]: 'inputOnly', [C.outputOnly]: 'outputOnly', [C.inputOutput]: 'inputOutput' };
  const NODE_TYPES = new Set(['SFNode', 'MFNode']);
  const LEGS = [['xite', 'X_ITE'], ['blender', 'Blender'], ['gltf', 'glTF'], ['unreal', 'Unreal']];

  const state = {
    index: { scenes: [] }, entry: null, enc: params.get('enc') || 'xml', view: params.get('view') || 'single',
    left: null, right: null, selected: null, watches: new Map(), history: [], hIdx: -1, probe: null, diff: null,
    api: null, loading: null, nodes: [],
  };
  if (params.get('shot')) document.body.classList.add('shot'); // canvas only, for MCP screenshots

  // ── console log ──────────────────────────────────────────────────────────────────────────────
  const logEl = $('log');
  let rowCount = 0;
  function row(cls, text) {
    const d = document.createElement('div');
    d.className = 'row ' + cls;
    d.textContent = text;
    logEl.appendChild(d);
    if (++rowCount > 800) { logEl.firstChild.remove(); rowCount--; }
    logEl.scrollTop = logEl.scrollHeight;
  }
  const native = { log: console.log, warn: console.warn, error: console.error, info: console.info };
  const short = (a) => a.map((x) => (typeof x === 'string' ? x : fmt(x, 1))).join(' ');
  console.log = (...a) => { native.log(...a); row(/x3dlab|print/i.test(String(a[0])) ? 'print' : 'out', short(a)); };
  console.info = (...a) => { native.info(...a); row('sys', short(a)); };
  console.warn = (...a) => { native.warn(...a); if (!/GL Driver|GPU stall/.test(String(a[0]))) row('warn', short(a)); };
  console.error = (...a) => { native.error(...a); row('error', short(a)); };
  addEventListener('error', (e) => row('error', `${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})`));
  addEventListener('unhandledrejection', (e) => {
    // a blocked <link> (strict CSP hosts) rejects with a bare Event: not an error of the scene
    if (e.reason && typeof e.reason === 'object' && 'type' in e.reason && 'target' in e.reason && !('message' in e.reason)) { e.preventDefault(); return; }
    row('error', 'unhandled: ' + (e.reason?.message || e.reason));
  });

  // ── formatting ───────────────────────────────────────────────────────────────────────────────
  function isNode(x) { return x && typeof x === 'object' && typeof x.getNodeTypeName === 'function'; }
  function isField(x) { return x && typeof x === 'object' && typeof x.getTypeName === 'function' && !isNode(x); }
  function nodeLabel(n) { return n ? `<${n.getNodeTypeName()}${n.getNodeName() ? ' DEF=' + n.getNodeName() : ''}>` : 'NULL'; }
  function fmt(x, depth = 2) {
    if (x === undefined) return 'undefined';
    if (x === null) return 'null';
    if (isNode(x)) return nodeLabel(x);
    if (isField(x)) {
      const s = x.toString();
      return s.length > 600 ? s.slice(0, 600) + ` … (${x.length ?? '?'} values)` : s;
    }
    if (typeof x === 'string') return x.length > 4000 ? x.slice(0, 4000) + `\n… (${x.length} chars)` : x;
    if (typeof x !== 'object') return String(x);
    if (depth <= 0) return Array.isArray(x) ? `[${x.length}]` : '{…}';
    try {
      return JSON.stringify(x, (k, v) => (isNode(v) ? nodeLabel(v) : isField(v) ? v.toString() : v), 1).slice(0, 6000);
    } catch { return String(x); }
  }

  // ── SAI traversal ────────────────────────────────────────────────────────────────────────────
  function fieldDefs(node) {
    return [...node.getFieldDefinitions()].map((d) => ({ name: d.name, access: ACCESS[d.accessType] || d.accessType, type: TYPE[d.dataType] || String(d.dataType) }));
  }
  function childFields(node) {
    const out = [];
    for (const d of fieldDefs(node)) {
      if (!NODE_TYPES.has(d.type) || d.access === 'inputOnly' || d.access === 'outputOnly') continue;
      let v;
      try { v = node[d.name]; } catch { continue; }
      if (!v) continue;
      if (d.type === 'SFNode') out.push([d.name, [v]]);
      else if (v.length) out.push([d.name, [...v].filter(Boolean)]);
    }
    return out;
  }
  function walk(scene, visit) {
    const seen = new Set();
    const go = (node, cf, depth, parent) => {
      if (!node) return;
      const again = seen.has(node);
      visit(node, cf, depth, parent, again);
      if (again) return;
      seen.add(node);
      if (depth > 64) return;
      for (const [name, kids] of childFields(node)) for (const k of kids) go(k, name, depth + 1, node);
    };
    for (const n of scene.rootNodes) go(n, 'children', 0, null);
    return seen.size;
  }
  function allNodes(browser = state.left) {
    const list = [];
    if (!browser?.currentScene) return list;
    walk(browser.currentScene, (n, cf, d, p, again) => { if (!again) list.push(n); });
    return list;
  }

  // ── index + scene loading ────────────────────────────────────────────────────────────────────
  async function loadIndex() {
    try {
      const res = await fetch(SCENES + 'index.json', { cache: 'no-store' });
      state.index = await res.json();
    } catch (e) {
      row('warn', 'no scenes/index.json (' + e.message + '); open your own file instead');
    }
    const sel = $('scene');
    sel.innerHTML = state.index.scenes.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join('');
    const want = params.get('scene');
    if (want && state.index.scenes.some((s) => s.id === want)) sel.value = want;
    $('encoding').value = state.enc;
    $('view').value = state.view === 'split' ? 'split' : 'single';
  }

  function syncUrl() {
    const p = new URLSearchParams(location.search);
    if (state.entry?.id && state.entry.id !== 'local') p.set('scene', state.entry.id);
    p.set('enc', state.enc);
    p.set('view', state.view);
    history.replaceState(null, '', '?' + p.toString());
  }

  async function loadScene() {
    const id = $('scene').value;
    const entry = state.index.scenes.find((s) => s.id === id);
    if (!entry) return;
    state.entry = entry;
    state.probe = null;
    state.diff = null;
    const file = entry.files[state.enc] || entry.files.xml;
    $('left-label').textContent = file;
    row('sys', `loading ${file} …`);
    const t0 = performance.now();
    state.loading = (async () => {
      await state.left.loadURL(new X3D.MFString(SCENES + file));
      row('sys', `${file}: ${allNodes().length} nodes, ${state.left.currentScene.routes.length} routes in ${Math.round(performance.now() - t0)} ms`);
      if (state.view === 'split') await loadRight();
      afterLoad();
    })();
    try { await state.loading; } catch (e) { row('error', `load failed: ${e?.message || e}`); }
    syncUrl();
  }

  async function loadRight() {
    const e = state.entry;
    $('vp-right').hidden = false;
    if (!e?.files?.glbWrapper) {
      $('right-label').textContent = 'no glb (Blender leg not run or import failed)';
      await state.right.loadURL(new X3D.MFString()).catch(() => {});
      return;
    }
    $('right-label').textContent = e.files.glb;
    await state.right.loadURL(new X3D.MFString(SCENES + e.files.glbWrapper));
  }

  function afterLoad() {
    state.nodes = allNodes();
    state.selected = null;
    clearWatches();
    renderTree();
    renderInspector();
    renderRoutes();
    renderFidelity();
    renderValidation();
    renderViewpoints();
    renderSnips();
    const vp = params.get('vp');
    if (vp) bind(vp);
  }

  // ── panels ───────────────────────────────────────────────────────────────────────────────────
  function renderTree() {
    const host = $('tree');
    const scene = state.left.currentScene;
    if (!scene) { host.innerHTML = ''; return; }
    const filter = $('graph-filter').value.trim().toLowerCase();
    const root = document.createElement('div');
    const stack = [root];
    let count = 0;
    walk(scene, (node, cf, depth, parent, again) => {
      if (count++ > 3000) return;
      while (stack.length > depth + 1) stack.pop();
      const det = document.createElement('details');
      const type = node.getNodeTypeName();
      const def = node.getNodeName();
      const kids = again ? [] : childFields(node);
      if (!kids.length) det.className = 'leaf';
      if (depth < 2) det.open = true;
      const sum = document.createElement('summary');
      let proto = false;
      try { proto = [...node.getNodeType()].includes(C.X3DPrototypeInstance); } catch {}
      sum.innerHTML = `<span class="${proto ? 't-proto' : 't-type'}">${esc(type)}</span>` +
        (def ? ` <span class="t-def">${esc(def)}</span>` : '') +
        (cf !== 'children' ? ` <span class="t-cf">${esc(cf)}</span>` : '') + (again ? ' <span class="t-cf">(USE)</span>' : '');
      sum.addEventListener('click', (ev) => { if (ev.detail) select(node, sum); });
      det.appendChild(sum);
      if (filter && !(type.toLowerCase().includes(filter) || (def || '').toLowerCase().includes(filter))) det.style.opacity = '.35';
      (stack[depth] || root).appendChild(det);
      stack[depth + 1] = det;
    });
    host.replaceChildren(...root.childNodes);
    $('node-count').textContent = state.nodes.length;
    $('route-count').textContent = scene.routes.length;
  }

  function select(node, summaryEl) {
    state.selected = node;
    document.querySelectorAll('.tree summary.sel').forEach((s) => s.classList.remove('sel'));
    summaryEl?.classList.add('sel');
    renderInspector();
    window.$0 = node;
  }

  function valueText(v) {
    if (v === null || v === undefined) return 'NULL';
    if (isNode(v)) return nodeLabel(v);
    if (typeof v === 'object' && 'length' in v && typeof v !== 'string') {
      const s = v.toString();
      return s.length > 80 ? `[${v.length}] ${s.slice(0, 70)}…` : s;
    }
    return String(v);
  }
  const toHex = (c) => '#' + [c.r, c.g, c.b].map((x) => Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16).padStart(2, '0')).join('');

  function parseValue(type, text) {
    const nums = () => text.trim().split(/[\s,]+/).filter(Boolean).map(Number);
    switch (type) {
      case 'SFBool': return /^(true|1)$/i.test(text.trim());
      case 'SFFloat': case 'SFDouble': case 'SFTime': return Number(text);
      case 'SFInt32': return parseInt(text, 10);
      case 'SFString': return text;
      case 'SFColor': return new X3D.SFColor(...nums());
      case 'SFColorRGBA': return new X3D.SFColorRGBA(...nums());
      case 'SFVec2f': return new X3D.SFVec2f(...nums());
      case 'SFVec3f': return new X3D.SFVec3f(...nums());
      case 'SFVec3d': return new X3D.SFVec3d(...nums());
      case 'SFRotation': return new X3D.SFRotation(...nums());
      case 'MFString': return new X3D.MFString(...(text.match(/"(?:[^"\\]|\\.)*"/g) || [JSON.stringify(text)]).map((s) => JSON.parse(s)));
      default: throw new Error(`editing ${type} is not supported here; use the console`);
    }
  }

  function renderInspector() {
    const host = $('inspector');
    const node = state.selected;
    if (!node) { host.innerHTML = '<p class="dim">Pick a node in the scene graph, or run <code>select("DEF")</code>.</p>'; return; }
    const defs = fieldDefs(node);
    const editable = new Set(['SFBool', 'SFFloat', 'SFDouble', 'SFTime', 'SFInt32', 'SFString', 'SFColor', 'SFColorRGBA', 'SFVec2f', 'SFVec3f', 'SFVec3d', 'SFRotation', 'MFString']);
    const rows = defs.map((d) => {
      let v = '';
      if (d.access !== 'inputOnly') { try { v = node[d.name]; } catch { v = '?'; } }
      const id = 'f-' + d.name;
      let control;
      if (d.access === 'inputOutput' && editable.has(d.type)) {
        if (d.type === 'SFBool') control = `<input type="checkbox" id="${id}" ${v ? 'checked' : ''}>`;
        else {
          const text = d.type === 'MFString' ? v.toString() : String(v);
          control = (d.type === 'SFColor' ? `<input type="color" data-for="${id}" value="${toHex(v)}"> ` : '') +
            `<input type="text" id="${id}" value="${esc(text)}" style="${d.type === 'SFColor' ? 'width:calc(100% - 42px)' : ''}">`;
        }
      } else if (d.type === 'SFNode' && v) {
        control = `<a href="#" class="ro" data-node="${esc(d.name)}">${esc(nodeLabel(v))}</a>`;
      } else {
        control = `<span class="ro" title="${esc(d.access === 'inputOnly' ? '' : valueText(v))}">${esc(d.access === 'inputOnly' ? '—' : valueText(v))}</span>`;
      }
      const watchable = d.access === 'outputOnly' || d.access === 'inputOutput';
      const key = node.getNodeName() + '.' + d.name;
      return `<div class="frow"><div class="fname">${esc(d.name)}${watchable ? `<span class="watch ${state.watches.has(watchKey(node, d.name)) ? 'on' : ''}" data-watch="${esc(d.name)}" title="log every change of ${esc(key)}">◉</span>` : ''}
        <span class="ftype">${esc(d.access)} ${esc(d.type)}</span></div><div>${control}</div></div>`;
    }).join('');
    host.innerHTML = `<h3>${esc(node.getNodeTypeName())} ${node.getNodeName() ? `<span class="t-def">${esc(node.getNodeName())}</span>` : ''}</h3>
      <div class="sub">${defs.length} fields · edit inputOutput fields live · ◉ watches events · <code>$0</code> is this node in the console</div>${rows}`;
    host.querySelectorAll('input[type=text]').forEach((inp) => inp.addEventListener('change', () => {
      const name = inp.id.slice(2);
      const d = defs.find((x) => x.name === name);
      try {
        node[name] = parseValue(d.type, inp.value);
        row('in', `$('${node.getNodeName() || node.getNodeTypeName()}').${name} = ${inp.value}`);
      } catch (e) { row('error', e.message); }
    }));
    host.querySelectorAll('input[type=checkbox]').forEach((inp) => inp.addEventListener('change', () => {
      node[inp.id.slice(2)] = inp.checked;
      row('in', `${nodeLabel(node)}.${inp.id.slice(2)} = ${inp.checked}`);
    }));
    host.querySelectorAll('input[type=color]').forEach((inp) => inp.addEventListener('input', () => {
      const t = $(inp.dataset.for);
      const h = inp.value;
      const c = [1, 3, 5].map((i) => (parseInt(h.slice(i, i + 2), 16) / 255).toFixed(3));
      t.value = c.join(' ');
      node[inp.dataset.for.slice(2)] = new X3D.SFColor(...c.map(Number));
    }));
    host.querySelectorAll('[data-watch]').forEach((el) => el.addEventListener('click', () => { toggleWatch(node, el.dataset.watch); renderInspector(); }));
    host.querySelectorAll('[data-node]').forEach((el) => el.addEventListener('click', (ev) => { ev.preventDefault(); select(node[el.dataset.node]); }));
  }

  const watchKey = (node, field) => `${node.getNodeName() || node.getNodeTypeName()}#${field}`;
  function toggleWatch(node, field) {
    const key = watchKey(node, field);
    if (state.watches.has(key)) {
      const w = state.watches.get(key);
      w.node.removeFieldCallback(w.cb, field);
      state.watches.delete(key);
      row('sys', `unwatched ${key}`);
      return false;
    }
    let n = 0;
    const label = `${node.getNodeName() || node.getNodeTypeName()}.${field}`;
    const cbKey = 'x3dlab-watch-' + key;
    let last = 0;
    node.addFieldCallback(cbKey, field, (value) => {
      n++;
      const now = performance.now();
      if (now - last < 250) return; // rate-limit: a TimeSensor fires every frame
      last = now;
      row('print', `${label} #${n} → ${valueText(value)}`);
    });
    state.watches.set(key, { node, cb: cbKey });
    row('sys', `watching ${label} (rate-limited to 4 lines/s)`);
    return true;
  }
  function clearWatches() {
    for (const [key, w] of state.watches) { try { w.node.removeFieldCallback(w.cb, key.split('#')[1]); } catch {} }
    state.watches.clear();
  }

  function renderRoutes() {
    const scene = state.left.currentScene;
    const list = scene ? [...scene.routes] : [];
    $('routes').innerHTML = list.length
      ? `<table><tr><th>from</th><th>to</th><th></th></tr>${list.map((r, i) => `<tr><td><span class="t-def">${esc(r.sourceNode.getNodeName())}</span>.${esc(r.sourceField)}</td>
        <td><span class="t-def">${esc(r.destinationNode.getNodeName())}</span>.${esc(r.destinationField)}</td>
        <td><button class="btn btn-sm btn-ghost" data-r="${i}" title="watch the source event">◉</button></td></tr>`).join('')}</table>`
      : '<p class="dim">No ROUTEs in this scene.</p>';
    $('routes').querySelectorAll('[data-r]').forEach((b) => b.addEventListener('click', () => {
      const r = list[Number(b.dataset.r)];
      toggleWatch(r.sourceNode, r.sourceField);
    }));
  }

  const pill = (s) => `<span class="pill s-${esc(s)}">${esc(s)}</span>`;
  function renderFidelity() {
    const e = state.entry;
    const host = $('fidelity');
    if (!e?.fidelity) { host.innerHTML = '<p class="dim">Fidelity predictions are built by <code>python -m x3dlab build</code> for the lab scenes.</p>'; return; }
    const b = e.blender;
    const blenderLine = !b ? '<div class="callout">Blender leg not run for this build: predictions only. Run <code>python -m x3dlab build --blender</code> or the X3D mission.</div>'
      : !b.ok ? `<div class="callout"><b>Blender import failed:</b> <code>${esc(b.error)}</code></div>`
        : `<div class="kv"><span>Blender</span><span>${esc(b.blender)} · add-on ${esc(b.addonVersion)} · ${esc(b.seconds)} s</span>
           <span>objects</span><span>${esc(Object.entries(b.objects).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(', '))}</span>
           <span>glb</span><span>${(b.glb.bytes / 1024).toFixed(0)} kB · ${esc(Object.entries(b.glb.primitiveModes).map(([k, v]) => `${v} ${k}`).join(', '))} · ${b.glb.animations} anim · ${b.glb.lights} lights</span>
           <span>extensions</span><span>${esc(b.glb.extensionsUsed.join(', ') || '—')}</span></div>`;
    const rows = e.fidelity.map((r) => {
      const legs = LEGS.map(([leg, label]) => {
        const [p] = r.legs[leg];
        const m = r.measured?.[leg];
        const v = r.verdicts?.[leg];
        return `<div class="leg"><span class="leg-name">${label}</span>${pill(p)}${m ? `<span class="arrow">→</span>${pill(m[0])}<span class="v-${esc(v)}">${v === 'confirmed' ? '✓' : v === 'worse' ? '✗' : v === 'better' ? '↑' : v === 'blocked' ? '⊘' : ''}</span>` : ''}</div>`;
      }).join('');
      const ev = Object.entries(r.measured || {}).map(([leg, m]) => `<span class="meas">${esc(leg)}: ${esc(m[1])}</span>`).join('');
      const worse = Object.values(r.verdicts || {}).includes('worse');
      return `<div class="rule${worse ? ' rule-worse' : ''}"><b>${esc(r.title)}</b><div class="legs">${legs}</div>
        <details><summary>why${ev ? ' · measured' : ''}</summary><p class="why">${esc(r.why)}</p>${ev}</details></div>`;
    }).join('');
    const probe = e.probes?.colourSpace;
    host.innerHTML = `<p>${esc(e.blurb)}</p>${blenderLine}
      ${probe ? `<div class="callout">This scene has a colour-space probe. <button class="btn btn-sm" id="run-probe">Run probe</button>
        <span class="dim">(binds VP_ColourSpace, samples four 0.5-grey swatches; in split view the glb too)</span><div id="probe-out"></div></div>` : ''}
      <p class="dim">Each leg: predicted (from the scene's nodes) → measured (Blender leg, colour probe). ✓ confirmed · ✗ lost more than predicted · ⊘ blocked by an earlier failure.</p>
      ${rows}`;
    $('run-probe')?.addEventListener('click', () => probe_());
    if (state.probe) renderProbe();
  }

  function renderProbe() {
    const out = $('probe-out');
    if (!out || !state.probe) return;
    const p = state.probe;
    const names = Object.keys(p.left);
    out.innerHTML = `<table><tr><th>swatch</th><th>X_ITE</th><th>expect</th>${p.right ? '<th>glb in X_ITE</th><th>expect</th>' : ''}</tr>
      ${names.map((n) => `<tr><td>${esc(n.replace('CS_', ''))}</td><td>${p.left[n].gray}</td><td class="dim">${p.expect.xite[n]}</td>
      ${p.right ? `<td>${p.right[n]?.gray ?? '—'}</td><td class="dim">${p.expect.linear[n]}</td>` : ''}</tr>`).join('')}</table>
      <p class="dim">8-bit grey on screen for a 0.5 colour. 128 = read as sRGB, ~188 = read as linear.</p>`;
  }

  function renderValidation() {
    const e = state.entry;
    const host = $('validation');
    if (!e?.validation) { host.innerHTML = '<p class="dim">No validation for this file.</p>'; return; }
    const v = e.validation;
    const st = e.stats || {};
    const top = Object.entries(st.byType || {}).slice(0, 14);
    host.innerHTML = `<div class="kv"><span>verdict</span><span class="${v.ok ? 'v-confirmed' : 'v-worse'}">${v.ok ? 'valid' : 'invalid'} · ${v.errors} errors · ${v.warnings} warnings</span>
      <span>profile</span><span>${esc(st.profile)} ${esc(st.version)}</span><span>nodes</span><span>${st.nodes} (${st.defs} DEF) · ${st.routes} routes</span>
      ${st.protos?.length ? `<span>PROTOs</span><span>${esc(st.protos.join(', '))} · ${st.protoInstances} instances</span>` : ''}
      <span>encodings</span><span>${Object.entries(e.bytes || {}).map(([k, b]) => `${k} ${(b / 1024).toFixed(1)} kB`).join(' · ')}</span></div>
      <h4>Issues</h4>${v.issues.length ? v.issues.map((i) => `<div class="issue"><span class="lvl ${esc(i.level)}">${esc(i.level)}</span><code>${esc(i.code)}</code> line ${i.line}<br>${esc(i.message)}<br><code>${esc(i.path)}</code></div>`).join('') : '<p class="dim">None.</p>'}
      <h4>Node types</h4><div class="kv">${top.map(([k, n]) => `<span>${esc(k)}</span><span>${n}</span>`).join('')}</div>`;
  }

  function viewpoints(browser = state.left) {
    return allNodes(browser).filter((n) => /Viewpoint$/.test(n.getNodeTypeName()));
  }
  function renderViewpoints() {
    const vps = viewpoints();
    $('viewpoint').innerHTML = vps.map((v, i) => `<option value="${i}">${esc(v.description || v.getNodeName() || 'Viewpoint ' + (i + 1))}</option>`).join('');
  }

  const SNIPS = {
    '*': ['help()', 'stats()', "$$('Shape').length", 'routes().slice(0, 4)', "exportScene('vrml').slice(0, 300)",
      "spawn('<Transform translation=\"0 3.4 0\"><Shape><Appearance><Material diffuseColor=\"1 .8 .1\"/></Appearance><Box size=\".6 .6 .6\"/></Shape></Transform>')"],
    'material-ladder': ['await probe()', "$('Phong4').shininess = 0.02", "$('Pbr2').roughness = 0.9", "bind('VP_ColourSpace')"],
    'shader-ribbon': ["$('Plasma').tint = new X3D.SFColor(1, .35, .1)", "$('Clock').cycleInterval = 1.5", "watch('Clock', 'fraction_changed')"],
    orrery: ["$('Running').toggle = false", "$('Running').toggle = true", "$('Planet3').color = new X3D.SFColor(1, .2, .6)", "watch('Turn0', 'value_changed')"],
    'terrain-script': ["$('Terrain').height.length", "$('Terrain').color.color.length"],
    'terrain-baked': ["$('Terrain').height.length"],
    'lines-and-points': ["$('Galaxy').geometry.coord.point.length", "$('Knot').geometry.color.color.length"],
    primitives: ["$$('Sphere')[0].radius = 1.2", "$$('Text')[0].string = new X3D.MFString('hello', 'X_ITE')"],
  };
  function renderSnips() {
    const list = [...(SNIPS[state.entry?.id] || []), ...SNIPS['*']];
    $('snips').innerHTML = list.map((s) => `<button title="${esc(s)}">${esc(s.length > 42 ? s.slice(0, 40) + '…' : s)}</button>`).join('');
    $('snips').querySelectorAll('button').forEach((b, i) => b.addEventListener('click', () => run(list[i])));
  }

  // ── pixels, probe, diff ──────────────────────────────────────────────────────────────────────
  const frames = (n = 2) => new Promise((r) => { const step = () => (n-- <= 0 ? r() : requestAnimationFrame(step)); step(); });
  function grab(browser = state.left) {
    const src = browser.getContext().canvas;
    const c = document.createElement('canvas');
    c.width = src.width; c.height = src.height;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(src, 0, 0);
    return { g, w: c.width, h: c.height };
  }
  function pixel(x, y, browser = state.left, r = 3) {
    const { g, w, h } = grab(browser);
    x = Math.round(x ?? w / 2); y = Math.round(y ?? h / 2);
    const d = g.getImageData(Math.max(0, x - r), Math.max(0, y - r), 2 * r + 1, 2 * r + 1).data;
    const acc = [0, 0, 0];
    for (let i = 0; i < d.length; i += 4) { acc[0] += d[i]; acc[1] += d[i + 1]; acc[2] += d[i + 2]; }
    const n = d.length / 4;
    return acc.map((v) => Math.round(v / n));
  }
  async function settle(browser) {
    let prev = null;
    for (let i = 0; i < 40; i++) {
      await frames(2);
      await sleep(120);
      const now = pixel(undefined, undefined, browser).join(',');
      if (now === prev) return;
      prev = now;
    }
  }
  async function bindIn(browser, def) {
    const scene = browser?.currentScene;
    if (!scene) return false;
    let vp;
    try { vp = scene.getNamedNode(def); } catch { return false; }
    for (const n of allNodes(browser)) if (n.getNodeTypeName() === 'NavigationInfo') n.transitionType = new X3D.MFString('TELEPORT');
    vp.set_bind = true;
    return true;
  }
  function bind(def) {
    const ok = bindIn(state.left, def);
    if (state.view === 'split') bindIn(state.right, def);
    return ok;
  }
  async function probeOne(browser, spec) {
    if (!(await bindIn(browser, spec.viewpoint))) return null;
    await settle(browser);
    const { w, h } = grab(browser);
    const t = Math.tan(spec.fov / 2) * spec.distance; // world half-extent of the smaller screen dimension
    const sx = w >= h ? (h / w) / t : 1 / t;
    const sy = w >= h ? 1 / t : (w / h) / t;
    const out = {};
    for (const [name, [x, y]] of Object.entries(spec.samples)) {
      const rgb = pixel((1 + x * sx) / 2 * w, (1 - y * sy) / 2 * h, browser, 4);
      out[name] = { rgb, gray: Math.round((rgb[0] + rgb[1] + rgb[2]) / 3) };
    }
    return out;
  }
  async function probe_() {
    const spec = state.entry?.probes?.colourSpace;
    if (!spec) throw new Error('this scene has no colour-space probe (load material-ladder)');
    row('sys', 'probe: binding ' + spec.viewpoint + ' (teleport), sampling four 0.5-grey swatches …');
    const left = await probeOne(state.left, spec);
    const right = state.view === 'split' && state.entry.files?.glbWrapper ? await probeOne(state.right, spec) : null;
    state.probe = { left, right, expect: { xite: spec.expect_xite, linear: spec.expect_linear_pipeline }, at: new Date().toISOString() };
    renderProbe();
    const vps = viewpoints();
    if (vps[0]) { bindIn(state.left, vps[0].getNodeName()); if (right) bindIn(state.right, vps[0].getNodeName()); }
    return state.probe;
  }
  async function diff() {
    if (state.view !== 'split' || !state.entry?.files?.glbWrapper) throw new Error('diff needs split view with a glb');
    await frames(2);
    const a = grab(state.left), b = grab(state.right);
    const w = Math.min(a.w, b.w), h = Math.min(a.h, b.h);
    const da = a.g.getImageData(0, 0, w, h).data, db = b.g.getImageData(0, 0, w, h).data;
    // "content" = pixels where either image departs from its own top-left (background) colour
    const off = (d, i) => Math.max(Math.abs(d[i] - d[0]), Math.abs(d[i + 1] - d[1]), Math.abs(d[i + 2] - d[2])) > 24;
    let sum = 0, changed = 0, content = 0, contentChanged = 0;
    for (let i = 0; i < da.length; i += 4) {
      const d = Math.max(Math.abs(da[i] - db[i]), Math.abs(da[i + 1] - db[i + 1]), Math.abs(da[i + 2] - db[i + 2]));
      sum += d;
      const isContent = off(da, i) || off(db, i);
      if (isContent) content++;
      if (d > 24) { changed++; if (isContent) contentChanged++; }
    }
    const px = da.length / 4;
    state.diff = { meanDelta: +(sum / px).toFixed(2), pctChanged: +(100 * changed / px).toFixed(1),
      pctContentChanged: content ? +(100 * contentChanged / content).toFixed(1) : 0, width: w, height: h };
    $('diff-chip').hidden = false;
    $('diff-value').textContent = `${state.diff.pctContentChanged}% of object px · mean ${state.diff.meanDelta}`;
    return state.diff;
  }

  // ── helpers for the REPL (and window.x3dlab) ─────────────────────────────────────────────────
  function stats(browser = state.left) {
    const nodes = allNodes(browser);
    const byType = {};
    for (const n of nodes) byType[n.getNodeTypeName()] = (byType[n.getNodeTypeName()] || 0) + 1;
    const gl = browser.getContext();
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      scene: state.entry?.id, url: browser.currentScene?.worldURL, nodes: nodes.length,
      routes: browser.currentScene?.routes.length ?? 0, rootNodes: browser.currentScene?.rootNodes.length ?? 0,
      fps: +browser.currentFrameRate.toFixed(1), canvas: [gl.canvas.width, gl.canvas.height],
      renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      xite: browser.version, colorSpace: browser.getBrowserOption('ColorSpace'), toneMapping: browser.getBrowserOption('ToneMapping'),
      byType: Object.fromEntries(Object.entries(byType).sort((a, b) => b[1] - a[1])),
    };
  }
  function routes() {
    return [...state.left.currentScene.routes].map((r) => `${r.sourceNode.getNodeName()}.${r.sourceField} → ${r.destinationNode.getNodeName()}.${r.destinationField}`);
  }
  async function spawn(xml) {
    const s = await state.left.createX3DFromString(`<X3D profile="Full" version="4.0"><Scene>${xml}</Scene></X3D>`);
    const added = [...s.rootNodes];
    for (const n of added) state.left.currentScene.addRootNode(n);
    afterLoadLite();
    return added.length === 1 ? added[0] : added;
  }
  function afterLoadLite() { state.nodes = allNodes(); renderTree(); renderRoutes(); }
  function exportScene(enc = 'xml') {
    const s = state.left.currentScene;
    return { xml: () => s.toXMLString(), vrml: () => s.toVRMLString(), json: () => s.toJSONString() }[enc]();
  }
  function download(text, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(text instanceof Blob ? text : new Blob([text], { type: 'text/plain' }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    return name;
  }
  function named(def, browser = state.left) { return browser.currentScene.getNamedNode(def); }
  function ofType(type, browser = state.left) { return allNodes(browser).filter((n) => n.getNodeTypeName() === type); }
  function help() {
    return [
      'In scope: browser, scene, X3D, glb (the right-hand browser in split view), $0 (inspector node)',
      "$('DEF')             named node            $$('Type')         every node of a type",
      "watch('DEF','field') log an event          unwatch()           stop all watches",
      "bind('VP_DEF')       bind a viewpoint      select('DEF')       open in the inspector",
      "spawn('<Shape>…')    add X3D XML live      routes()            ROUTE list",
      "exportScene('xml'|'vrml'|'json')           download(text, name)",
      'stats()  pixel(x, y)  await probe()  await diff()  reload()',
      "Set fields with X3D types: $('Mat').diffuseColor = new X3D.SFColor(1, 0, 0)",
    ].join('\n');
  }
  const api = {
    get browser() { return state.left; }, get scene() { return state.left.currentScene; }, get glb() { return state.view === 'split' ? state.right : null; },
    X3D, $: named, $$: ofType, routes, spawn, exportScene, download, stats, pixel, probe: probe_, diff, bind, help,
    watch: (def, field) => toggleWatch(named(def), field), unwatch: () => { clearWatches(); return 'cleared'; },
    select: (def) => { select(named(def)); activateTab('inspect'); return named(def); },
    reload: () => loadScene(), allNodes, state, get ready() { return state.loading; }, get entry() { return state.entry; },
  };
  window.x3dlab = api;

  // ── REPL ─────────────────────────────────────────────────────────────────────────────────────
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  async function run(code) {
    code = code.trim();
    if (!code) return;
    state.history.push(code);
    state.hIdx = state.history.length;
    row('in', code);
    const names = ['browser', 'scene', 'glb', 'X3D', '$', '$$', 'routes', 'spawn', 'exportScene', 'download', 'stats', 'pixel', 'probe', 'diff', 'bind', 'help', 'watch', 'unwatch', 'select', 'reload', '$0'];
    const vals = names.map((n) => (n === '$0' ? state.selected : api[n]));
    let fn;
    try { fn = new AsyncFunction(...names, `return (${code}\n);`); } catch { fn = new AsyncFunction(...names, code); }
    try {
      const out = await fn(...vals);
      if (out !== undefined) row('out', fmt(out));
      if (/spawn|addRootNode|removeRootNode/.test(code)) afterLoadLite();
      if (state.selected && /=/.test(code)) renderInspector();
    } catch (e) {
      row('error', `${e.name}: ${e.message}`);
    }
  }
  const input = $('input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const c = input.value; input.value = ''; resize(); run(c); }
    else if (e.key === 'ArrowUp' && input.selectionStart === 0 && state.hIdx > 0) { e.preventDefault(); input.value = state.history[--state.hIdx]; resize(); }
    else if (e.key === 'ArrowDown' && input.selectionEnd === input.value.length && state.hIdx < state.history.length) {
      e.preventDefault(); state.hIdx++; input.value = state.history[state.hIdx] ?? ''; resize();
    }
  });
  const resize = () => { input.style.height = 'auto'; input.style.height = input.scrollHeight + 'px'; };
  input.addEventListener('input', resize);
  $('prompt').addEventListener('submit', (e) => e.preventDefault());
  $('clear').addEventListener('click', () => { logEl.innerHTML = ''; rowCount = 0; });

  // ── chrome ───────────────────────────────────────────────────────────────────────────────────
  function activateTab(name) {
    document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    document.querySelectorAll('.pane').forEach((p) => (p.dataset.active = String(p.dataset.tab === name)));
  }
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => activateTab(b.dataset.tab)));
  $('graph-filter').addEventListener('input', renderTree);
  $('scene').addEventListener('change', loadScene);
  $('encoding').addEventListener('change', () => { state.enc = $('encoding').value; loadScene(); });
  $('view').addEventListener('change', async () => {
    state.view = $('view').value;
    $('vp-right').hidden = state.view !== 'split';
    $('diff-chip').hidden = true;
    if (state.view === 'split') await loadRight().catch((e) => row('error', e.message));
    syncUrl();
  });
  $('viewpoint').addEventListener('change', () => {
    const vp = viewpoints()[Number($('viewpoint').value)];
    if (!vp) return;
    vp.set_bind = true;
    if (state.view === 'split' && vp.getNodeName()) bindIn(state.right, vp.getNodeName());
  });
  $('reload').addEventListener('click', loadScene);
  $('shot').addEventListener('click', () => grab().g.canvas.toBlob((b) => download(b, `${state.entry?.id || 'x3d'}.png`)));
  $('open-file').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', () => { const f = $('file').files[0]; if (f) openFile(f); });
  addEventListener('keydown', (e) => { if (e.key === 'r' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) loadScene(); });

  const stage = $('stage');
  stage.addEventListener('dragover', (e) => { e.preventDefault(); $('drop').hidden = false; });
  stage.addEventListener('dragleave', (e) => { if (e.target === $('drop')) $('drop').hidden = true; });
  stage.addEventListener('drop', (e) => { e.preventDefault(); $('drop').hidden = true; const f = e.dataTransfer.files[0]; if (f) openFile(f); });

  async function openFile(file) {
    row('sys', `opening ${file.name} (${(file.size / 1024).toFixed(1)} kB)`);
    state.entry = { id: 'local', title: file.name, files: {}, local: true };
    $('left-label').textContent = file.name;
    try {
      if (/\.(glb|gltf)$/i.test(file.name)) {
        const url = URL.createObjectURL(file);
        const s = await state.left.createX3DFromString(`<X3D profile="Interchange" version="4.0"><Scene><Inline url='"${url}"'/></Scene></X3D>`);
        state.left.replaceWorld(s);
      } else {
        const text = await file.text();
        const s = await state.left.createX3DFromString(text);
        state.left.replaceWorld(s);
        if (/\.x3d$/i.test(file.name)) validateRemote(text, file.name);
      }
      await frames(4);
      afterLoad();
    } catch (e) {
      row('error', `could not load ${file.name}: ${e.message || e}`);
    }
  }

  /** When served by BLACKBOX, the Python validator and fidelity predictor run on uploaded files too. */
  async function validateRemote(text, name) {
    try {
      const res = await fetch('/api/x3d/validate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text, name }) });
      if (!res.ok) throw new Error(res.status);
      const r = await res.json();
      state.entry = { ...state.entry, validation: r.validation, stats: r.stats, fidelity: r.fidelity, blurb: `Your file, ${name}: predictions only.` };
      renderValidation();
      renderFidelity();
      row('sys', `validator: ${r.validation.errors} errors, ${r.validation.warnings} warnings · ${r.fidelity.length} fidelity rules`);
    } catch {
      row('sys', 'Python validator not reachable from this page (static hosting). Run: python -m x3dlab validate ' + name);
    }
  }

  // ── boot ─────────────────────────────────────────────────────────────────────────────────────
  async function boot() {
    state.left = X3D.getBrowser($('left'));
    state.right = X3D.getBrowser($('right'));
    $('xite-version').textContent = state.left.version;
    const gl = state.left.getContext();
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    $('renderer').textContent = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    row('sys', `X_ITE ${state.left.version} · ${$('renderer').textContent} · colour space ${state.left.getBrowserOption('ColorSpace')} · tone mapping ${state.left.getBrowserOption('ToneMapping')}`);
    row('sys', 'type help() for the console helpers');
    await loadIndex();
    if (state.view === 'split') $('vp-right').hidden = false;
    if (state.index.scenes.length) await loadScene();
    setInterval(() => { $('fps').textContent = state.left.currentFrameRate.toFixed(1); }, 600);
  }
  boot().catch((e) => row('error', 'boot failed: ' + (e.message || e)));
})();
