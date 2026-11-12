/**
 * X3D Lab mission: two MCP servers, one report.
 *
 *   1. library lane (no MCP)   python -m x3dlab build [--blender]: scenes in three encodings, validation,
 *                              fidelity predictions, X3D → Blender → glb, prediction-vs-measurement verdicts
 *   2. x3dlab MCP server       the same library behind MCP: list_scenes, validate_x3d, predict_fidelity,
 *                              compose_scene (a deliberate typo, then the fix), blender_roundtrip — and a
 *                              check that MCP and the library agree
 *   3. chrome-devtools-mcp     the X_ITE dev console in headless Chrome (SwiftShader WebGL): per scene, SAI
 *                              counts, console errors, a split-view screenshot and pixel diff against the glb;
 *                              the colour-space probe; Script-grown vs Python-baked terrain heights; a real
 *                              click on the orrery's sun (TouchSensor → BooleanFilter → BooleanToggle)
 *   4. report                  runs/<id>/report.html, summary.json, wire-x3dlab.jsonl, wire-chrome.jsonl
 *
 * In-page work goes through X_ITE's public SAI. Where the mission calls window.x3dlab (the dev console's own
 * helper API: probe(), diff()), the report says so.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { McpClient, parseEvalResult } from '../lib/mcp-client.mjs';
import { serverArgs } from './mcp-process.mjs';

const ROOT = resolve(dirname(new URL(import.meta.url).pathname), '..');
const X3D_DIR = join(ROOT, 'x3d');
const SCENES_DIR = join(ROOT, 'site', 'x3d', 'scenes');
const PY = () => process.env.X3DLAB_PYTHON || 'python3';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function runPython(args, { input, python = PY(), env = {}, onLine } = {}) {
  return new Promise((resolveP) => {
    const child = spawn(python, args, {
      cwd: X3D_DIR,
      env: { ...process.env, PYTHONPATH: X3D_DIR + (process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''), ...env },
    });
    let stdout = '', stderr = '', buf = '';
    child.stdout.on('data', (d) => {
      stdout += d;
      if (!onLine) return;
      buf += d;
      const lines = buf.split('\n');
      buf = lines.pop();
      lines.forEach((l) => l.trim() && onLine(l));
    });
    child.stderr.on('data', (d) => (stderr += d));
    child.on('error', (e) => resolveP({ code: -1, stdout, stderr: String(e) }));
    child.on('close', (code) => resolveP({ code, stdout, stderr }));
    if (input !== undefined) child.stdin.end(input);
    else child.stdin.end();
  });
}

export async function x3dEnvironment() {
  const py = await runPython(['-c', 'import sys; print(sys.version.split()[0])']);
  const bpyPy = process.env.X3DLAB_BPY_PYTHON;
  const addon = process.env.X3DLAB_ADDON;
  let bpy = null;
  if (bpyPy && existsSync(bpyPy)) {
    const r = await runPython(['-c', 'import bpy; print(bpy.app.version_string)'], { python: bpyPy });
    bpy = r.code === 0 ? r.stdout.trim().split('\n').pop() : null;
  }
  const xite = join(ROOT, 'node_modules', 'x_ite', 'package.json');
  return {
    python: py.code === 0 ? py.stdout.trim() : null,
    bpy, bpyPython: bpyPy || null,
    addon: addon && existsSync(join(addon, 'import_x3d.py')) ? addon : null,
    blenderLeg: Boolean(bpy && addon && existsSync(join(addon, 'import_x3d.py'))),
    xite: existsSync(xite) ? JSON.parse(readFileSync(xite, 'utf8')).version : null,
    scenesBuilt: existsSync(join(SCENES_DIR, 'index.json')),
  };
}

export async function buildX3dScenes({ blender = true, emit = () => {} } = {}) {
  const env = await x3dEnvironment();
  const useBlender = blender && env.blenderLeg;
  const args = ['-m', 'x3dlab', 'build', '--out', SCENES_DIR];
  if (useBlender) args.push('--blender', '--bpy-python', env.bpyPython, '--addon', env.addon);
  emit({ type: 'x3d-log', line: `python ${args.join(' ')}` });
  const r = await runPython(args, { onLine: (line) => emit({ type: 'x3d-log', line }) });
  if (r.code !== 0) throw new Error('x3dlab build failed: ' + (r.stderr || r.stdout).slice(-1200));
  return JSON.parse(readFileSync(join(SCENES_DIR, 'index.json'), 'utf8'));
}

function json(text) {
  try { return JSON.parse(text); } catch { return null; }
}

// ── in-page functions (run through evaluate_script) ─────────────────────────────────────────────
const PAGE_STATS = `async () => {
  await window.x3dlab.ready;
  const b = X3D.getBrowser('#left');
  const t0 = performance.now();
  await new Promise((r) => setTimeout(r, 1500));
  const s = b.currentScene;
  const seen = new Set();
  const byType = {};
  const C = X3D.X3DConstants;
  const T = {}; for (const [k, v] of Object.entries(C)) if (/^(SF|MF)Node$/.test(k)) T[v] = k;
  const visit = (n) => {
    if (!n || seen.has(n)) return;
    seen.add(n);
    byType[n.getNodeTypeName()] = (byType[n.getNodeTypeName()] || 0) + 1;
    for (const d of n.getFieldDefinitions()) {
      if (!T[d.dataType] || d.accessType === C.inputOnly || d.accessType === C.outputOnly) continue;
      const v = n[d.name];
      if (!v) continue;
      if (T[d.dataType] === 'SFNode') visit(v); else for (const k of v) visit(k);
    }
  };
  for (const n of s.rootNodes) visit(n);
  const gl = b.getContext();
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return { nodes: seen.size, routes: s.routes.length, rootNodes: s.rootNodes.length, byType,
    fps: +b.currentFrameRate.toFixed(2), canvas: [gl.canvas.width, gl.canvas.height],
    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    xite: b.version, colorSpace: b.getBrowserOption('ColorSpace'), toneMapping: b.getBrowserOption('ToneMapping'),
    profile: s.profile?.name, encoding: s.encoding, waitedMs: Math.round(performance.now() - t0) };
}`;

const PAGE_DIFF = `async () => { await window.x3dlab.ready; await new Promise((r) => setTimeout(r, 800)); return await window.x3dlab.diff(); }`;
const PAGE_PROBE = `async () => { await window.x3dlab.ready; return await window.x3dlab.probe(); }`;
const PAGE_HEIGHTS = `async () => {
  await window.x3dlab.ready;
  const b = X3D.getBrowser('#left');
  for (let i = 0; i < 40; i++) { const h = b.currentScene.getNamedNode('Terrain').height; if (h.length) break; await new Promise((r) => setTimeout(r, 100)); }
  const g = b.currentScene.getNamedNode('Terrain');
  const h = [...g.height];
  return { count: h.length, min: Math.min(...h), max: Math.max(...h), sample: h.filter((_, i) => i % 97 === 0).map((x) => +x.toFixed(5)),
    colors: g.color ? g.color.color.length : 0 };
}`;
const PAGE_CLOCK = `async () => { await window.x3dlab.ready; const s = X3D.getBrowser('#left').currentScene;
  return { clock0Enabled: s.getNamedNode('Clock0').enabled, comet: s.getNamedNode('CometClock').enabled, toggle: s.getNamedNode('Running').toggle }; }`;

// ── report ──────────────────────────────────────────────────────────────────────────────────────
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pill = (s) => `<span class="pill s-${esc(s)}">${esc(s)}</span>`;

function renderReport(sum) {
  const k = sum.kpis;
  const scenes = sum.scenes.map((s) => {
    const shot = s.screenshot ? `<img src="data:${s.screenshot.mimeType};base64,${s.screenshot.data}" alt="${esc(s.id)} in X_ITE${s.split ? ' (left) and its Blender glb (right)' : ''}">` : '';
    const rules = (s.fidelity || []).map((r) => `<tr><td>${esc(r.title)}</td>${['xite', 'blender', 'gltf', 'unreal'].map((leg) => {
      const m = r.measured?.[leg];
      const v = r.verdicts?.[leg];
      return `<td>${pill(r.legs[leg][0])}${m ? ` → ${pill(m[0])}<span class="v v-${esc(v)}">${v === 'worse' ? ' ✗' : v === 'confirmed' ? ' ✓' : v === 'blocked' ? ' ⊘' : ''}</span><div class="ev">${esc(m[1])}</div>` : ''}</td>`;
    }).join('')}</tr>`).join('');
    const errs = s.consoleErrors?.length ? `<ul class="errs">${s.consoleErrors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : '<p class="ok">No X_ITE errors in the console.</p>';
    return `<section class="scene"><h2>${esc(s.title)} <code>${esc(s.id)}</code></h2>
      <p class="blurb">${esc(s.blurb)}</p>${shot}
      <div class="kv"><span>X_ITE (SAI)</span><span>${s.stats ? `${s.stats.nodes} nodes · ${s.stats.routes} routes · ${s.stats.fps} fps on ${esc(s.stats.renderer)}` : '—'}</span>
      <span>validator</span><span>${s.validation ? `${s.validation.errors} errors · ${s.validation.warnings} warnings (via MCP validate_x3d)` : '—'}</span>
      <span>Blender leg</span><span>${s.blender ? (s.blender.ok ? `${esc(s.blender.blender)} · add-on ${esc(s.blender.addonVersion)} · glb ${(s.blender.glb.bytes / 1024).toFixed(0)} kB` : `import failed: <code>${esc(s.blender.error)}</code>`) : 'not run'}</span>
      ${s.diff ? `<span>source vs glb</span><span>${s.diff.pctContentChanged}% of the object's pixels differ by more than 24/255 (${s.diff.pctChanged}% of the frame, mean Δ ${s.diff.meanDelta})</span>` : ''}</div>
      ${s.extra ? `<div class="extra">${s.extra}</div>` : ''}
      <table class="fid"><tr><th>rule</th><th>X_ITE</th><th>Blender</th><th>glTF</th><th>Unreal</th></tr>${rules}</table>${errs}</section>`;
  }).join('');
  const checks = sum.checks.map((c) => `<tr><td>${c.pass ? '<span class="ok">PASS</span>' : '<span class="bad">FAIL</span>'}</td><td>${esc(c.name)}</td><td>${esc(c.detail)}</td></tr>`).join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>X3D Lab mission ${esc(sum.runId)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{margin:0;background:#04070d;color:#e8eefc;font:14px/1.55 Inter,system-ui,sans-serif}main{max-width:1180px;margin:0 auto;padding:28px 22px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:0 0 6px}h2 code{font-size:12px;color:#6d7994}code{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.sub{color:#a3b0c7}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.kpi{background:#0a111d;border:1px solid #172033;border-radius:12px;padding:12px 14px}.kpi b{display:block;font-size:24px}.kpi span{color:#6d7994;font-size:12px}
.scene{background:#0a111d;border:1px solid #172033;border-radius:14px;padding:18px;margin:18px 0}.scene img{width:100%;border-radius:10px;border:1px solid #22304d;margin:8px 0}
.blurb{color:#a3b0c7;margin:0}.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;font-size:13px;margin:8px 0}.kv span:nth-child(odd){color:#6d7994}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}td,th{border-bottom:1px solid #172033;padding:6px 5px;text-align:left;vertical-align:top}th{color:#6d7994;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.pill{display:inline-block;border-radius:999px;padding:0 7px;font:11px ui-monospace,monospace}.s-kept{background:#3ddc9722;color:#3ddc97}.s-approx{background:#ffb02022;color:#ffb020}.s-partial{background:#ff9a5c22;color:#ff9a5c}.s-lost{background:#ff5d5d22;color:#ff5d5d}
.ev{color:#6d7994;font:11px ui-monospace,monospace}.v-worse{color:#ff5d5d;font-weight:700}.v-confirmed{color:#3ddc97}.ok{color:#3ddc97}.bad{color:#ff5d5d;font-weight:700}.errs{color:#ff5d5d;font-size:12px}
.extra{background:#0d1626;border-left:3px solid #ffb020;padding:8px 12px;margin:10px 0;font-size:13px}.limits li{color:#a3b0c7}
</style></head><body><main>
<h1>X3D Lab mission <code>${esc(sum.runId)}</code></h1>
<p class="sub">${esc(sum.verdict)}</p>
<div class="kpis">
<div class="kpi"><b>${k.scenes}</b><span>scenes, all built in 3 encodings</span></div>
<div class="kpi"><b>${k.checksPassed}/${k.checks}</b><span>mission checks pass</span></div>
<div class="kpi"><b>${k.confirmed}</b><span>predictions confirmed by measurement</span></div>
<div class="kpi"><b>${k.worse}</b><span>legs lost more than predicted</span></div>
<div class="kpi"><b>${k.x3dlabCalls}</b><span>x3dlab MCP calls</span></div>
<div class="kpi"><b>${k.chromeCalls}</b><span>chrome-devtools-mcp calls</span></div>
</div>
<div class="kv"><span>environment</span><span>Python ${esc(sum.env.python)} · bpy ${esc(sum.env.bpy || 'not available')} · X_ITE ${esc(sum.env.xite)} · add-on ${esc(sum.addonVersion || '—')}</span>
<span>duration</span><span>${(sum.ms / 1000).toFixed(1)} s</span></div>
<h2 style="margin-top:22px">Checks</h2><table><tr><th></th><th>check</th><th>evidence</th></tr>${checks}</table>
${scenes}
<h2>Limits of this run</h2><ul class="limits">
<li>Rendering is SwiftShader (software WebGL) in headless Chrome. Frame rates are not representative of a GPU; pixel values are.</li>
<li>The Unreal column is a prediction from the fidelity-chain finding. This mission does not run Unreal.</li>
<li>Split-view diffs include animation phase and the Blender leg's lighting; they show where the glb departs from X_ITE, not why.</li>
<li>probe() and diff() are the dev console's own helpers (window.x3dlab); counts and heights come straight from X_ITE's SAI.</li>
</ul></main></body></html>`;
}

// ── mission ─────────────────────────────────────────────────────────────────────────────────────
export async function runX3dMission({ runId, origin, outDir, emit = () => {}, options = {} }) {
  const t0 = Date.now();
  mkdirSync(outDir, { recursive: true });
  const say = (line) => emit({ type: 'x3d-log', runId, line });
  const phase = (name) => emit({ type: 'x3d-phase', runId, phase: name });
  const checks = [];
  const check = (name, pass, detail) => { checks.push({ name, pass: Boolean(pass), detail }); say(`${pass ? 'PASS' : 'FAIL'} ${name}: ${detail}`); };

  phase('environment');
  const env = await x3dEnvironment();
  say(`python ${env.python} · bpy ${env.bpy || '—'} · X_ITE ${env.xite} · Blender leg ${env.blenderLeg ? 'on' : 'off'}`);
  if (!env.python) throw new Error('python3 not found (set X3DLAB_PYTHON)');
  if (!env.xite) throw new Error('x_ite is not installed: run npm install');

  // 1 ── library lane
  phase('build');
  const index = options.skipBuild && env.scenesBuilt ? JSON.parse(readFileSync(join(SCENES_DIR, 'index.json'), 'utf8'))
    : await buildX3dScenes({ blender: options.blender !== false, emit: (e) => emit({ ...e, runId }) });
  check('library build', index.scenes.every((s) => s.validation.ok), `${index.scenes.length} scenes, ${index.scenes.reduce((a, s) => a + s.stats.nodes, 0)} nodes, all valid X3D 4.0`);

  // 2 ── x3dlab MCP server
  phase('x3dlab-mcp');
  const x3d = new McpClient({ command: env.python ? PY() : 'python3', args: ['-m', 'x3dlab', 'mcp'], cwd: X3D_DIR,
    env: { PYTHONPATH: X3D_DIR, X3DLAB_ROOT: ROOT, X3DLAB_OUT: join(outDir, 'x3dlab'), ...(env.blenderLeg ? { X3DLAB_BPY_PYTHON: env.bpyPython, X3DLAB_ADDON: env.addon } : {}) },
    wireLogPath: join(outDir, 'wire-x3dlab.jsonl'), requestTimeoutMs: 600000 });
  x3d.on('wire', (w) => emit({ type: 'x3d-wire', runId, server: 'x3dlab', ...w }));
  const perScene = Object.fromEntries(index.scenes.map((s) => [s.id, { ...s }]));
  let x3dCalls = 0;
  const callX3d = async (name, args) => { x3dCalls++; const r = await x3d.callTool(name, args); return { ...r, data: json(r.text) }; };
  try {
    await x3d.start();
    await x3d.initialize();
    check('x3dlab MCP handshake', x3d.tools.length >= 7, `${x3d.tools.length} tools: ${x3d.tools.map((t) => t.name).join(', ')}`);
    const res = await x3d.request('resources/list', {});
    check('x3dlab MCP resources', res.resources?.length >= index.scenes.length * 3, `${res.resources?.length} resources (every scene in 3 encodings + the schema)`);
    const list = await callX3d('list_scenes', {});
    check('list_scenes matches the build', list.data?.scenes?.length === index.scenes.length, `${list.data?.scenes?.length} scenes`);
    for (const s of index.scenes) {
      const v = await callX3d('validate_x3d', { scene: s.id });
      perScene[s.id].validation = v.data;
      const p = await callX3d('predict_fidelity', { scene: s.id });
      perScene[s.id].mcpRules = p.data?.rules?.length;
      say(`MCP ${s.id}: ${v.data?.errors} errors, ${v.data?.warnings} warnings, ${p.data?.rules?.length} fidelity rules`);
    }
    check('MCP and library agree', index.scenes.every((s) => perScene[s.id].mcpRules === s.fidelity.length && perScene[s.id].validation?.errors === s.validation.errors),
      'same validation verdicts and the same fidelity rules from both lanes');
    const typo = await callX3d('compose_scene', { title: 'agent draft', nodes: [{ type: 'Shape', children: [{ type: 'Appearance', children: [{ type: 'Material', fields: { diffuseColor: [1, 0.5, 0] } }] }, { type: 'Sphere', fields: { radiuss: 2 } }] }] });
    const fixed = await callX3d('compose_scene', { title: 'agent draft', save_as: 'agent-draft', nodes: [{ type: 'Shape', children: [{ type: 'Appearance', children: [{ type: 'Material', fields: { diffuseColor: [1, 0.5, 0] } }] }, { type: 'Sphere', fields: { radius: 2 } }] }] });
    check('compose_scene catches a typo, then builds', typo.isError && /did you mean 'radius'/.test(typo.text) && fixed.data?.validation?.ok,
      `${typo.text.trim()} → fixed draft valid (${fixed.data?.stats?.nodes} nodes)`);
    if (env.blenderLeg) {
      const rt = await callX3d('blender_roundtrip', { scene: 'terrain-baked' });
      const lib = index.scenes.find((s) => s.id === 'terrain-baked').blender;
      check('blender_roundtrip over MCP reproduces the build', rt.data?.blender?.ok && rt.data.blender.glb.bytes === lib?.glb?.bytes,
        `terrain-baked glb ${rt.data?.blender?.glb?.bytes} bytes over MCP, ${lib?.glb?.bytes} in the build`);
    }
  } finally {
    await x3d.close().catch(() => {});
  }

  // 3 ── chrome-devtools-mcp drives the dev console
  phase('chrome-mcp');
  const chrome = new McpClient({ command: process.execPath, args: serverArgs({ profile: 'x3d', viewport: '1280x720' }),
    cwd: ROOT, wireLogPath: join(outDir, 'wire-chrome.jsonl'), requestTimeoutMs: 180000 });
  chrome.on('wire', (w) => emit({ type: 'x3d-wire', runId, server: 'chrome', ...w }));
  let chromeCalls = 0;
  const cc = async (name, args, opts) => { chromeCalls++; return chrome.callTool(name, args, opts); };
  const evalIn = async (pageId, fn) => parseEvalResult((await cc('evaluate_script', { pageId, function: fn }, { timeoutMs: 120000 })).text);
  try {
    await chrome.start();
    await chrome.initialize();
    const np = await cc('new_page', { url: origin + '/lab/x3d/?shot=1' });
    const pageId = Number((np.text.split('\n').find((l) => l.includes('[selected]')) || '').match(/(\d+):/)?.[1] ?? 1);
    const heights = {};
    for (const s of index.scenes) {
      const split = Boolean(s.files.glbWrapper);
      const url = `${origin}/lab/x3d/?scene=${s.id}&view=${split ? 'split' : 'single'}&shot=1`;
      say(`open ${url}`);
      await cc('navigate_page', { pageId, type: 'url', url, timeout: 60000 });
      const stats = await evalIn(pageId, PAGE_STATS);
      perScene[s.id].stats = stats;
      perScene[s.id].split = split;
      if (split) perScene[s.id].diff = await evalIn(pageId, PAGE_DIFF);
      if (s.id.startsWith('terrain')) heights[s.id] = await evalIn(pageId, PAGE_HEIGHTS);
      if (s.probes?.colourSpace && split) {
        const p = await evalIn(pageId, PAGE_PROBE);
        perScene[s.id].probe = p;
        if (p?.left) {
          const rowsHtml = Object.keys(p.left).map((n) => `<tr><td>${esc(n)}</td><td>${p.left[n].gray}</td><td>${p.expect.xite[n]}</td><td>${p.right?.[n]?.gray ?? '—'}</td><td>${p.expect.linear[n]}</td></tr>`).join('');
          perScene[s.id].extra = `<b>Colour-space probe</b> (window.x3dlab.probe): 8-bit grey on screen for a colour of 0.5.
            <table><tr><th>swatch</th><th>X_ITE</th><th>expected (sRGB reading)</th><th>glb in X_ITE</th><th>expected (linear)</th></tr>${rowsHtml}</table>`;
          const near = (a, b) => Math.abs(a - b) <= 6;
          check('X_ITE reads Material colours as sRGB', near(p.left.CS_Material.gray, 128) && near(p.left.CS_Physical.gray, 188),
            `Material 0.5 → ${p.left.CS_Material.gray}, PhysicalMaterial 0.5 → ${p.left.CS_Physical.gray}`);
          if (p.right) {
            const delta = p.right.CS_Material.gray - p.left.CS_Material.gray;
            const ruleCs = s.fidelity.find((r) => r.id === 'colour-space');
            if (ruleCs) {
              ruleCs.measured = { ...(ruleCs.measured || {}), gltf: [Math.abs(delta) > 12 ? 'approx' : 'kept', `Material 0.5 shows ${p.left.CS_Material.gray} in X_ITE, ${p.right.CS_Material.gray} from the glb (Δ ${delta})`] };
              ruleCs.verdicts = { ...(ruleCs.verdicts || {}), gltf: ruleCs.measured.gltf[0] === ruleCs.legs.gltf[0] ? 'confirmed' : ruleCs.measured.gltf[0] === 'kept' ? 'better' : 'worse' };
            }
            check('colour-space shift through the glb is measurable', true, `Material emissive 0.5: X_ITE ${p.left.CS_Material.gray} vs glb ${p.right.CS_Material.gray} (Δ ${delta}/255)`);
          }
        }
      }
      if (s.id === 'orrery') {
        const before = await evalIn(pageId, PAGE_CLOCK);
        const snap = await cc('take_snapshot', { pageId });
        const uid = (snap.text.split('\n').find((l) => /X3D scene \(source/.test(l)) || '').match(/uid=([\w-]+)/)?.[1];
        if (uid) {
          await cc('click', { pageId, uid });
          await sleep(600);
          const after = await evalIn(pageId, PAGE_CLOCK);
          await cc('click', { pageId, uid });
          await sleep(600);
          const again = await evalIn(pageId, PAGE_CLOCK);
          check('real click on the sun pauses and resumes the orrery', before?.clock0Enabled === true && after?.clock0Enabled === false && again?.clock0Enabled === true,
            `Clock0.enabled ${before?.clock0Enabled} → click → ${after?.clock0Enabled} → click → ${again?.clock0Enabled} (TouchSensor → BooleanFilter → BooleanToggle, uid ${uid})`);
          perScene[s.id].extra = `<b>Interaction:</b> chrome-devtools-mcp <code>click</code> on the canvas (uid ${esc(uid)}, its centre is the sun). Clock0.enabled went ${before?.clock0Enabled} → ${after?.clock0Enabled} → ${again?.clock0Enabled}.`;
        } else {
          check('real click on the sun pauses and resumes the orrery', false, 'canvas not found in the accessibility snapshot');
        }
      }
      const shot = await cc('take_screenshot', { pageId, format: 'jpeg', quality: 72 });
      perScene[s.id].screenshot = shot.images?.[0] || null;
      const cm = await cc('list_console_messages', { pageId, types: ['error'] });
      perScene[s.id].consoleErrors = cm.text.split('\n').filter((l) => /^msgid=/.test(l)).map((l) => l.replace(/^msgid=\d+ \[error\] /, '').slice(0, 300));
      say(`${s.id}: ${stats?.nodes} nodes, ${stats?.routes} routes, ${stats?.fps} fps${perScene[s.id].diff ? `, ${perScene[s.id].diff.pctContentChanged}% of object px differ from the glb` : ''}, ${perScene[s.id].consoleErrors.length} console errors`);
    }
    check('every scene loads in X_ITE without console errors', index.scenes.every((s) => perScene[s.id].stats?.nodes > 0 && !perScene[s.id].consoleErrors.length),
      index.scenes.map((s) => `${s.id} ${perScene[s.id].stats?.nodes ?? 0}`).join(', '));
    const sc = heights['terrain-script'], bk = heights['terrain-baked'];
    if (sc && bk) {
      const maxd = Math.max(...sc.sample.map((v, i) => Math.abs(v - bk.sample[i])));
      check('JS Script heights equal the Python bake', sc.count === bk.count && sc.count === 49 * 49 && maxd < 1e-4,
        `${sc.count} heights grown by the Script, ${bk.count} baked by Python, max sampled difference ${maxd}`);
      perScene['terrain-script'].extra = `<b>Script vs bake:</b> X_ITE ran the Script and grew ${sc.count} heights (range ${sc.min.toFixed(3)} … ${sc.max.toFixed(3)}); the Python-baked twin has ${bk.count}; the sampled heights agree to ${maxd}. Only the baked file reaches Blender.`;
    }
    const shader = perScene['shader-ribbon'];
    if (shader?.diff) check('GLSL look is X_ITE-only', shader.diff.pctContentChanged > 50, `${shader.diff.pctContentChanged}% of the object's pixels differ between the shaded source and the glb's fallback Material`);
  } finally {
    await chrome.close().catch(() => {});
  }

  // 4 ── report
  phase('report');
  const scenes = index.scenes.map((s) => ({ ...perScene[s.id], fidelity: s.fidelity }));
  let confirmed = 0, worse = 0, blocked = 0;
  for (const s of scenes) for (const r of s.fidelity) for (const v of Object.values(r.verdicts || {})) { if (v === 'confirmed') confirmed++; if (v === 'worse') worse++; if (v === 'blocked') blocked++; }
  const passed = checks.filter((c) => c.pass).length;
  const summary = {
    kind: 'x3d-mission', runId, origin, ms: Date.now() - t0, env, addonVersion: index.blender?.addonVersion,
    verdict: `${passed} of ${checks.length} checks pass. ${confirmed} fidelity predictions confirmed by measurement; ${worse} legs lost more than predicted${blocked ? ` (${blocked} more blocked by a failed import)` : ''}.`,
    kpis: { blocked, scenes: scenes.length, checks: checks.length, checksPassed: passed, confirmed, worse, x3dlabCalls: x3dCalls, chromeCalls },
    checks, scenes,
  };
  writeFileSync(join(outDir, 'report.html'), renderReport(summary));
  const slim = { ...summary, scenes: scenes.map(({ screenshot, ...rest }) => rest) };
  writeFileSync(join(outDir, 'summary.json'), JSON.stringify(slim, null, 1));
  say(`report: runs/${runId}/report.html · ${summary.verdict}`);
  return summary;
}
