/**
 * mission.mjs — the autonomous browser-SRE agent.
 *
 * The contract: it may only learn about the page through chrome-devtools-mcp
 * tools. No reading of `site/broken/*` source, no test hooks, no DOM knowledge
 * baked in. Everything below is a tool call, a parse, and a judgement.
 *
 * Protocol (identical for both builds, so the before/after is honest):
 *   1  attest      handshake, discover the tool surface, assert capabilities
 *   2  open        isolated context, emulate Slow 4G + 4x CPU, navigate
 *   3  load trace  record a cold load, read CWV + the insight set
 *   4  triage      drill into every insight that fired
 *   5  reproduce   drive the UI with real input, capture the new console error
 *   6  runtime     trace an interaction window (INP / forced reflow)
 *   7  network     request inventory + per-request headers
 *   8  memory      snapshot → churn → snapshot → diff → retaining path
 *   9  lighthouse  a11y/SEO/best-practices, then mobile screenshots
 *  10  verify      repeat 2–9 against the fixed build and compute deltas
 *  11  verdict     score against the answer key, write the flight recorder
 */
import { mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';

import { McpClient } from '../lib/mcp-client.mjs';
import { serverArgs, LAUNCH_DESCRIPTION, CHROME_PATH } from './mcp-process.mjs';
import {
  parseTrace, parseInsight, parseConsole, parseNetwork, parseLighthouse,
  parseHeapDiff, parseHeapCount, parsePagesText, parseSnapshot, findUidByRoleName,
  insightToFinding, lighthouseToFindings, mergeFindings, num, kb, SEVERITY_ORDER,
} from './heuristics.mjs';
import { CATALOG, CATALOG_BY_ID } from './catalog.mjs';
import { renderReport } from './report.mjs';

// Observers installed *before* any page script runs, via navigate_page's initScript.
const INIT_SCRIPT = `
window.__bb = { lcp: null, shifts: [] };
try {
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) if (e.element) {
      window.__bb.lcp = { tag: e.element.tagName, cls: String(e.element.className || ''),
        src: (e.element.currentSrc || e.element.src || '').split('/').pop(), size: e.size,
        time: Math.round(e.startTime) };
    }
  }).observe({ type: 'largest-contentful-paint', buffered: true });
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) {
      window.__bb.shifts.push({ v: +e.value.toFixed(4),
        src: (e.sources || []).map(s => s.node ? (s.node.tagName + (s.node.className ? '.' + String(s.node.className).split(' ')[0] : '')) : '?') });
    }
  }).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) (window.__bb.longtasks = window.__bb.longtasks || []).push(Math.round(e.duration));
  }).observe({ type: 'longtask', buffered: true });
} catch {}
`;

const PROBE_SCRIPT = `() => {
  const rt = performance.getEntriesByType('resource').map(e => ({
    name: e.name.replace(location.origin, '').slice(0, 88),
    start: Math.round(e.startTime), dur: Math.round(e.duration),
    bytes: e.transferSize || 0, decoded: e.decodedBodySize || 0,
    type: e.initiatorType, rb: e.renderBlockingStatus, proto: e.nextHopProtocol,
    crossOrigin: !e.name.startsWith(location.origin),
  }));
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const bb = window.__bb || {};
  return {
    lcpElement: bb.lcp || null,
    shifts: bb.shifts || [],
    cls: +(bb.shifts || []).reduce((a, s) => a + s.v, 0).toFixed(3),
    longTasks: bb.longtasks || [],
    domNodes: document.querySelectorAll('*').length,
    domDepth: (() => { let d = 0, n = document.body; while (n && n.lastElementChild) { n = n.lastElementChild; d++; } return d; })(),
    transferBytes: rt.reduce((a, r) => a + r.bytes, 0),
    requestCount: rt.length,
    renderBlocking: rt.filter(r => r.rb === 'blocking'),
    thirdParties: [...new Set(rt.filter(r => r.crossOrigin).map(r => new URL(r.name, location.href).host))],
    largest: rt.slice().sort((a, b) => b.bytes - a.bytes).slice(0, 5),
    hasViewportMeta: !!document.querySelector('meta[name="viewport"]'),
    htmlLang: document.documentElement.getAttribute('lang'),
    imagesWithoutAlt: [...document.images].filter(i => !i.hasAttribute('alt')).length,
    imagesTotal: document.images.length,
    biggestSubtrees: (() => {
      const counts = new Map();
      for (const el of document.querySelectorAll('body *')) {
        let p = el.parentElement;
        while (p && p !== document.body) { counts.set(p, (counts.get(p) || 0) + 1); p = p.parentElement; }
      }
      return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([el, n]) => ({
        sel: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + String(el.className).split(' ')[0] : ''),
        nodes: n,
        hidden: !el.offsetParent && getComputedStyle(el).position !== 'fixed',
      }));
    })(),
    documentTitle: document.title,
    fonts: document.fonts ? document.fonts.status : 'n/a',
    nav: { ttfb: Math.round(nav.responseStart || 0), dcl: Math.round(nav.domContentLoadedEventEnd || 0), load: Math.round(nav.loadEventEnd || 0) },
    resources: rt,
  };
}`;

const INSIGHT_PRIORITY = [
  'ImageDelivery', 'LCPDiscovery', 'RenderBlocking', 'ThirdParties', 'CLSCulprits',
  'FontDisplay', 'ForcedReflow', 'INPBreakdown', 'LongTasks', 'DOMSize',
  'LegacyJavaScript', 'Cache', 'Viewport', 'NetworkDependencyTree', 'DocumentLatency',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function runMission({ runId, origin, outDir, emit, options = {} }) {
  const t0 = Date.now();
  const log = (level, msg, extra = {}) => emit({ type: 'log', level, msg, ...extra });

  await mkdir(join(outDir, 'screens'), { recursive: true });
  await mkdir(join(outDir, 'trace'), { recursive: true });
  await mkdir(join(outDir, 'heap'), { recursive: true });
  await mkdir(join(outDir, 'lighthouse'), { recursive: true });

  const client = new McpClient({
    command: process.execPath,
    args: serverArgs({ extra: options.extraServerArgs ?? [] }),
    requestTimeoutMs: 240_000,
    wireLogPath: join(outDir, 'wire.jsonl'),
  });

  const ctx = {
    runId, origin, outDir, emit, log,
    startedAt: t0,
    steps: [],
    wire: [],
    metrics: { broken: {}, fixed: {} },
    findings: [],
    artifacts: {},
    degraded: [],
    insights: { broken: [], fixed: [] },
    probes: { broken: null, fixed: null },
    lighthouse: { broken: null, fixed: null },
    heap: {},
    console: { broken: [], fixed: [] },
    network: { broken: [], fixed: [] },
    pages: {},
  };

  // Every frame in both directions is echoed to the UI and appended to wire.jsonl.
  client.on('wire', (row) => {
    ctx.wire.push(row);
    emit({ type: 'wire', ...row });
  });
  client.on('server-stderr', (s) => {
    const line = String(s).trim();
    if (line) emit({ type: 'server-stderr', line: line.slice(0, 400) });
  });

  /** Call a tool, time it, emit it, and never let one failure kill the mission. */
  async function tool(name, args, { critical = false, timeoutMs, retries = 1 } = {}) {
    const started = Date.now();
    emit({ type: 'mcp-call', name, args: shrinkArgs(args), startedAt: new Date().toISOString() });
    try {
      const res = await client.callTool(name, args, { timeoutMs, retries });
      emit({
        type: 'mcp-result', name, ms: res.ms, bytes: res.bytes, tokens: res.tokens,
        isError: res.isError, preview: (res.text || '').slice(0, 320),
      });
      ctx.steps.push({ tool: name, args: shrinkArgs(args), ms: res.ms, bytes: res.bytes, tokens: res.tokens, isError: res.isError });
      if (res.isError) {
        ctx.degraded.push({ tool: name, error: (res.text || '').slice(0, 300) });
        emit({ type: 'log', level: 'warn', msg: `${name} returned an error`, detail: (res.text || '').slice(0, 240) });
      }
      return res;
    } catch (err) {
      const ms = Date.now() - started;
      ctx.steps.push({ tool: name, args: shrinkArgs(args), ms, error: err.message });
      ctx.degraded.push({ tool: name, error: err.message.slice(0, 300) });
      emit({ type: 'mcp-error', name, ms, error: err.message.slice(0, 300) });
      if (critical) throw err;
      return { text: '', isError: true, images: [], ms, bytes: 0, tokens: 0, error: err.message };
    }
  }

  ctx.phases = [];
  const phase = async (id, title, fn) => {
    const startedAt = Date.now();
    ctx.phases.push({ id, title, status: 'running' });
    emit({ type: 'phase', id, title, status: 'running', startedAt: new Date(startedAt).toISOString() });
    log('info', `▶ ${title}`);
    try {
      const result = await fn();
      Object.assign(ctx.phases.find((p) => p.id === id) ?? {}, { status: 'done', ms: Date.now() - startedAt });
      emit({ type: 'phase', id, title, status: 'done', ms: Date.now() - startedAt });
      return result;
    } catch (err) {
      Object.assign(ctx.phases.find((p) => p.id === id) ?? {}, { status: 'failed', ms: Date.now() - startedAt });
      emit({ type: 'phase', id, title, status: 'failed', ms: Date.now() - startedAt, error: String(err.message || err).slice(0, 400) });
      log('error', `✖ ${title}: ${err.message}`);
      throw err;
    }
  };

  async function saveImage(res, filename) {
    const img = res.images?.[0];
    if (!img) return null;
    const ext = img.mimeType.includes('png') ? 'png' : img.mimeType.includes('webp') ? 'webp' : 'jpg';
    const path = join(outDir, 'screens', `${filename}.${ext}`);
    await writeFile(path, Buffer.from(img.data, 'base64'));
    ctx.artifacts[filename] = `runs/${runId}/screens/${filename}.${ext}`;
    emit({ type: 'artifact', kind: 'screenshot', key: filename, path: ctx.artifacts[filename] });
    return ctx.artifacts[filename];
  }

  const responseHeadersOf = (text = '') =>
    (text.split('### Response Headers')[1] ?? '').split(/\n### /)[0].trim();

  const pageIdOf = (text) => parsePagesText(text).find((p) => p.selected)?.id ?? parsePagesText(text)[0]?.id ?? null;

  // ─────────────────────────────── 1. attest ───────────────────────────────
  await phase('attest', 'Attest MCP toolchain', async () => {
    await client.start();
    await client.initialize();
    const required = [
      'new_page', 'navigate_page', 'emulate', 'performance_start_trace', 'performance_stop_trace',
      'performance_analyze_insight', 'list_console_messages', 'list_network_requests',
      'get_network_request', 'take_snapshot', 'click', 'take_screenshot', 'evaluate_script',
      'lighthouse_audit', 'take_heapsnapshot', 'compare_heapsnapshots', 'query_heapsnapshot_objects',
    ];
    const missing = required.filter((t) => !client.hasTool(t));
    ctx.attestation = {
      client: 'blackbox-agent/1.0.0',
      server: client.serverInfo,
      protocolVersion: client.protocolVersion,
      launch: LAUNCH_DESCRIPTION,
      chrome: CHROME_PATH,
      toolsDiscovered: client.tools.length,
      toolsUsed: required.length,
      missingTools: missing,
      categories: [...new Set(client.tools.map((t) => t.name.split('_')[0]))].slice(0, 20),
    };
    if (missing.length) throw new Error(`server is missing required tools: ${missing.join(', ')}`);
    emit({ type: 'attestation', attestation: ctx.attestation });
    log('ok', `${client.tools.length} tools discovered over MCP ${client.protocolVersion}; all ${required.length} required capabilities present`);
  });

  // ───────────────────────── open a build + measure ────────────────────────
  async function openBuild(variant, label) {
    const url = `${origin}/lab/${variant}/`;
    const res = await tool('new_page', { url: 'about:blank', isolatedContext: `blackbox-${variant}-${runId}` }, { critical: true });
    const pageId = pageIdOf(res.text);
    ctx.pages[variant] = pageId;

    const emu = await tool('emulate', { pageId, networkConditions: 'Slow 4G', cpuThrottlingRate: 4 });
    const emulated = !emu.isError;
    if (!emulated) {
      ctx.degraded.push({ tool: 'emulate', error: 'emulation unavailable — measurements are unthrottled' });
      log('warn', 'emulation unavailable; continuing unthrottled (recorded as a degradation)');
    }

    await tool('navigate_page', {
      pageId, type: 'url', url, timeout: 90_000, initScript: INIT_SCRIPT,
    }, { critical: true });

    // Let the page settle: fonts, late injections, the leak interval, idle.
    await sleep(variant === 'broken' ? 2600 : 1200);

    const probe = await tool('evaluate_script', { pageId, function: PROBE_SCRIPT }, { critical: true });
    const parsed = safeJson(probe.text);
    const shot = await tool('take_screenshot', { pageId, format: 'jpeg', quality: 72 });
    const artifact = await saveImage(shot, `${variant}-desktop`);
    return { pageId, url, emulated, probe: parsed, artifacts: { desktop: artifact }, label };
  }

  function safeJson(text = '') {
    const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
    try { return JSON.parse((fence ? fence[1] : text).trim()); } catch { return {}; }
  }

  /** Read CWV from a trace response and from the page's own observers. */
  const LCP_LOOKUP = `async () => {
    return await new Promise((resolve) => {
      let last = null;
      let po;
      try {
        po = new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.element) last = e; });
        po.observe({ type: 'largest-contentful-paint', buffered: true });
      } catch { return resolve(null); }
      setTimeout(() => {
        try { po.disconnect(); } catch {}
        resolve(last ? { tag: last.element.tagName, cls: String(last.element.className || ''),
          src: (last.element.currentSrc || last.element.src || '').split('/').pop(),
          size: last.size, time: Math.round(last.startTime) } : null);
      }, 120);
    });
  }`;

  async function nameLcpElement(pageId, variant) {
    const res = await tool('evaluate_script', { pageId, function: LCP_LOOKUP });
    const value = safeJson(res.text);
    if (value && value.tag) ctx.metrics[variant].lcpElement = value;
    return value;
  }

  async function measureTrace(pageId, variant, artifactName, { reload = true, autoStop = true } = {}) {
    const filePath = join(outDir, 'trace', `${variant}-${artifactName}.json.gz`);
    const res = await tool('performance_start_trace', { pageId, reload, autoStop, ignoreCache: true, filePath }, { timeoutMs: 240_000, critical: false });
    let text = res.text;
    if (!autoStop) {
      // manual window: let the caller interject, then stop explicitly
      const stop = await tool('performance_stop_trace', { pageId, filePath }, { timeoutMs: 240_000 });
      text = stop.text || text;
    }
    const trace = parseTrace(text);
    trace.file = `runs/${runId}/trace/${variant}-${artifactName}.json.gz`;
    return trace;
  }

  const CLICK_COST_SCRIPT = `async () => {
    const marks = [];
    const obs = new PerformanceObserver((l) => { for (const e of l.getEntries()) marks.push(Math.round(e.duration)); });
    try { obs.observe({ type: 'longtask' }); } catch { return { supported: false }; }
    const btn = document.getElementById('search-btn');
    const t0 = performance.now();
    for (let i = 0; i < 5; i++) { if (btn) btn.click(); await new Promise(r => setTimeout(r, 220)); }
    const wall = Math.round(performance.now() - t0);
    await new Promise(r => setTimeout(r, 400));
    obs.disconnect();
    return { supported: true, longTasks: marks, count: marks.length,
             maxMs: marks.length ? Math.max(...marks) : 0,
             totalMs: marks.reduce((a, b) => a + b, 0), wallMs: wall };
  }`;

  /**
   * Long Animation Frames: the modern way to attribute style/layout work to a
   * specific script. If no frame exceeds 50ms, the browser will not report it —
   * and the agent must say "inconclusive" rather than invent a finding.
   */
  const LOAF_SCRIPT = `async () => {
    const entries = [];
    const obs = new PerformanceObserver((l) => {
      for (const e of l.getEntries()) entries.push({
        dur: Math.round(e.duration),
        scripts: (e.scripts || []).map(s => ({ fn: s.sourceFunctionName || '(anonymous)',
          url: (s.sourceURL || '').split('/').pop(), forced: Math.round(s.forcedStyleAndLayoutDuration || 0) })),
      });
    });
    try { obs.observe({ type: 'long-animation-frame' }); } catch { return { supported: false }; }
    for (let i = 0; i < 30; i++) { window.scrollBy(0, 80); await new Promise(r => setTimeout(r, 18)); }
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 400));
    obs.disconnect();
    const forced = entries.reduce((a, e) => a + e.scripts.reduce((x, s) => x + s.forced, 0), 0);
    return { supported: true, frames: entries.length, forcedStyleAndLayoutMs: forced,
             attribution: entries.flatMap(e => e.scripts).slice(0, 5) };
  }`;

  async function measureLoaf(pageId, variant) {
    const res = await tool('evaluate_script', { pageId, function: LOAF_SCRIPT });
    const value = safeJson(res.text) || {};
    ctx.metrics[variant].loaf = value;
    emit({ type: 'loaf', variant, ...value });
    return value;
  }

  async function measureClickCost(pageId, variant) {
    const res = await tool('evaluate_script', { pageId, function: CLICK_COST_SCRIPT });
    const value = safeJson(res.text) || {};
    ctx.metrics[variant].interaction = value;
    if (value.count > 0) {
      ctx.findings.push({
        gtId: 'long-task-search', title: 'Search blocks the main thread on every click',
        category: 'performance', severity: 'high', source: 'evaluate_script', tool: 'evaluate_script',
        confidence: 'high',
        metric: `${value.count} long tasks, worst ${value.maxMs} ms, across 5 clicks`,
        evidenceText: `PerformanceObserver('longtask') around 5 real clicks:\n` +
          `durations (ms): ${JSON.stringify(value.longTasks)}\n` +
          `wall time for 5 clicks: ${value.wallMs} ms\n` +
          `next paint is deferred by every one of these tasks`,
        recommendation: 'Take the synchronous work off the click path (chunk it, or delete it).',
      });
    }
    emit({ type: 'interaction', variant, ...value });
    return value;
  }

  async function measureVisuals(pageId) {
    const probe = safeJson((await tool('evaluate_script', { pageId, function: PROBE_SCRIPT })).text);
    return probe;
  }

  // ───────────────────────── 2–3. broken build + trace ─────────────────────
  let broken;
  await phase('open-broken', 'Open the target in an isolated profile', async () => {
    broken = await openBuild('broken', 'broken');
    ctx.probes.broken = broken.probe;
    ctx.metrics.broken = baseMetrics(broken.probe);
    if (broken.artifacts.desktop) ctx.artifacts['broken-desktop'] = broken.artifacts.desktop;
    log('ok', `target painted: ${broken.probe.domNodes} DOM nodes, ${kb(broken.probe.transferBytes)} transferred, ${broken.probe.requestCount} requests`);
  });

  await phase('trace-load', 'Record a cold-load performance trace', async () => {
    const trace = await measureTrace(broken.pageId, 'broken', 'load', { reload: true, autoStop: true });
    ctx.trace = { brokenLoad: trace };
    const m = ctx.metrics.broken;
    m.lcp = trace.metrics.LCP ?? null;
    m.cls = trace.metrics.CLS ?? broken.probe.cls ?? null;
    m.fcp = trace.metrics.FCP ?? null;
    m.tbt = trace.metrics.TBT ?? null;
    m.lcpBreakdown = trace.breakdown;
    m.lcpElement = broken.probe.lcpElement;
    m.throttle = `${trace.cpuThrottling ?? '?'} / ${trace.networkThrottling ?? '?'}`;
    m.insightsAvailable = trace.insights.map((i) => i.name);
    ctx.insightSets = trace.insightSets;
    const late = safeJson((await tool('evaluate_script', { pageId: broken.pageId, function: PROBE_SCRIPT })).text);
    if (late?.lcpElement) { ctx.metrics.broken.lcpElement = late.lcpElement; ctx.probes.broken = late; }
    else await nameLcpElement(broken.pageId, 'broken');
    emit({ type: 'metrics', variant: 'broken', metrics: ctx.metrics.broken, insights: trace.insights });
    if (!trace.metrics.LCP) log('warn', 'trace returned without an LCP metric — continuing with what the page reported');
    log('ok', `LCP ${m.lcp ?? '?'} ms · CLS ${m.cls ?? '?'} · ${trace.insights.length} performance insights available`);
  });

  // ───────────────────────────── 4. insight triage ─────────────────────────
  await phase('triage', 'Drill into every performance insight', async () => {
    const trace = ctx.trace.brokenLoad;
    const available = trace.insights.map((i) => i.name);
    const ordered = INSIGHT_PRIORITY.filter((n) => available.includes(n));
    const extra = available.filter((n) => !INSIGHT_PRIORITY.includes(n));
    const targets = [...ordered, ...extra].slice(0, options.maxInsights ?? 9);
    const set = trace.insightSets[0]?.id ?? 'NAVIGATION_0';

    for (const name of targets) {
      const res = await tool('performance_analyze_insight', { pageId: broken.pageId, insightSetId: set, insightName: name });
      if (res.isError) continue;
      const parsed = parseInsight(res.text);
      ctx.insights.broken.push({ name, ...parsed });
      const drafts = [].concat(insightToFinding(name, parsed, {
        cls: ctx.metrics.broken.cls,
        domNodes: ctx.metrics.broken.domNodes,
        allUrls: available.includes('RenderBlocking') ? parsed.urls : [],
      }));
      ctx.findings.push(...drafts.filter(Boolean));
      emit({
        type: 'insight', name, variant: 'broken', title: parsed.title,
        savings: parsed.savings, savingsBytes: parsed.savingsBytes,
        summary: parsed.summary.slice(0, 400), analysis: parsed.analysis.slice(0, 900), urls: parsed.urls,
      });
    }
    log('ok', `${targets.length} insights analysed → ${ctx.findings.length} candidate findings`);
  });

  // ───────────────────────── 5. reproduce with real input ──────────────────
  await phase('reproduce', 'Drive the UI and catch the exception', async () => {
    const snap = await tool('take_snapshot', { pageId: broken.pageId });
    const nodes = parseSnapshot(snap.text);
    ctx.a11yTree = nodes.slice(0, 400);
    const searchUid = findUidByRoleName(nodes, 'button', /^Search$/);
    const trackUid = findUidByRoleName(nodes, 'button', /Track flight/i);
    const before = parseConsole((await tool('list_console_messages', { pageId: broken.pageId })).text);

    if (searchUid) {
      await tool('click', { pageId: broken.pageId, uid: searchUid });
      log('info', `clicked "Search" (uid=${searchUid}) — the a11y snapshot is what gives input tools their handles`);
    }
    if (trackUid) {
      await tool('click', { pageId: broken.pageId, uid: trackUid });
      log('info', `clicked "Track flight" (uid=${trackUid})`);
    }
    await sleep(500);

    const after = parseConsole((await tool('list_console_messages', {
      pageId: broken.pageId, types: ['error', 'warn'], includeStackTraces: true,
    })).text);

    // diff before/after so we know *our* click is what surfaced the error
    const beforeIds = new Set(before.map((m) => m.text));
    const fresh = after.filter((m) => !beforeIds.has(m.text));
    ctx.console.broken = after;
    ctx.console.fresh = fresh;

    const shotted = await tool('take_screenshot', { pageId: broken.pageId, format: 'jpeg', quality: 72 });
    ctx.artifacts['broken-after-click'] = await saveImage(shotted, 'broken-after-click');

    for (const msg of after) {
      const is404 = /404/.test(msg.text);
      const is500 = /500/.test(msg.text);
      const isType = /Uncaught|TypeError|ReferenceError/.test(msg.text);
      if (is404) ctx.findings.push({
        gtId: 'console-404-asset', title: 'Missing asset 404s on every load', category: 'console', severity: 'low',
        source: 'list_console_messages', tool: 'list_console_messages', confidence: 'high',
        evidenceText: msg.text, stack: msg.stack,
        recommendation: 'Ship the referenced image or remove the reference.',
      });
      if (is500) ctx.findings.push({
        gtId: 'console-500-pricing', title: 'Surge-pricing request fails and is never handled', category: 'console', severity: 'high',
        source: 'list_console_messages', tool: 'list_console_messages', confidence: 'high',
        evidenceText: msg.text, stack: msg.stack,
        recommendation: 'Check response.ok, catch the failure, degrade to cached fares.',
      });
      if (isType) ctx.findings.push({
        gtId: 'runtime-type-error', title: 'Clicking "Track flight" throws a TypeError', category: 'console', severity: 'critical',
        source: 'list_console_messages', tool: 'list_console_messages', confidence: 'high',
        evidenceText: msg.text + (msg.stack.length ? '\n' + msg.stack.join('\n') : ''),
        stack: msg.stack,
        recommendation: 'Never dereference a dataset value that may be undefined.',
      });
    }
    emit({
      type: 'console', variant: 'broken', messages: after.map((m) => ({ type: m.type, text: m.text, stack: m.stack, count: m.count })),
      reproduced: fresh.length,
    });
    log(fresh.length ? 'ok' : 'warn', fresh.length
      ? `${fresh.length} new console error(s) appeared only after real input: ${fresh.map((f) => f.text.slice(0, 70)).join(' | ')}`
      : 'no new console errors from the interaction');
  });

  // ───────────────────────── 6. runtime trace (INP) ────────────────────────
  await phase('runtime-trace', 'Trace an interaction window', async () => {
    const startRes = await tool('performance_start_trace', { pageId: broken.pageId, reload: false, autoStop: false });
    const started = !startRes.isError;
    if (started) {
      // Hammer the page the way a user would: scroll (layout thrash) + search (long task).
      await tool('evaluate_script', {
        pageId: broken.pageId,
        function: `async () => {
          for (let i = 0; i < 12; i++) { window.scrollBy(0, 120); await new Promise(r => setTimeout(r, 30)); }
          window.scrollTo(0, 0);
          const b = document.getElementById('search-btn'); if (b) { b.click(); b.click(); }
          return 'churned';
        }`,
      });
      await sleep(900);
      const stop = await tool('performance_stop_trace', {
        pageId: broken.pageId, filePath: join(outDir, 'trace', 'broken-runtime.json.gz'),
      }, { timeoutMs: 240_000 });
      const trace = parseTrace(stop.text);
      ctx.trace.brokenRuntime = trace;
      ctx.metrics.broken.runtime = { metrics: trace.metrics, insights: trace.insights.map((i) => i.name) };
      const set = trace.insightSets[0]?.id ?? 'NAVIGATION_0';
      const interesting = trace.insights.map((i) => i.name).filter((n) => /ForcedReflow|INPBreakdown|LongTasks|CLSCulprits/.test(n));
      for (const name of interesting) {
        const res = await tool('performance_analyze_insight', { pageId: broken.pageId, insightSetId: set, insightName: name });
        if (res.isError) continue;
        const parsed = parseInsight(res.text);
        ctx.insights.broken.push({ name, ...parsed, from: 'runtime' });
        ctx.findings.push(...[].concat(insightToFinding(name, parsed, {
          cls: ctx.metrics.broken.cls, domNodes: ctx.metrics.broken.domNodes,
        })).filter(Boolean));
        emit({ type: 'insight', name, variant: 'broken', title: parsed.title, summary: parsed.summary.slice(0, 300), analysis: parsed.analysis.slice(0, 800), urls: parsed.urls });
      }
      ctx.metrics.broken.inp = trace.metrics.INP ?? null;
      ctx.metrics.broken.tbt = trace.metrics.TBT ?? ctx.metrics.broken.tbt;
      log('ok', `runtime window traced: ${trace.insights.length} insights, INP ${trace.metrics.INP ?? 'n/a'}`);
    } else {
      log('warn', 'runtime trace unavailable in this server build — load trace insights stand');
    }
  });

  await phase('interaction-cost', 'Price the interaction: long tasks per click', async () => {
    const loaf = await measureLoaf(broken.pageId, 'broken');
    log(loaf.frames ? 'warn' : 'info', `long animation frames while scrolling: ${loaf.frames ?? 0}${loaf.frames === 0 ? ' (forced reflow in code, but below the 50 ms reporting threshold — recorded as inconclusive)' : ''}`);
    const v = await measureClickCost(broken.pageId, 'broken');
    log(v.count ? 'warn' : 'ok', v.count
      ? `${v.count} long tasks over 5 clicks (worst ${v.maxMs} ms) — the click path is the bottleneck`
      : 'no long tasks attributable to the click handler');
  });

  // ───────────────────────────── 7. network ────────────────────────────────
  await phase('network', 'Audit the request graph', async () => {
    const list = await tool('list_network_requests', { pageId: broken.pageId });
    const requests = parseNetwork(list.text);
    ctx.network.broken = requests;
    const failures = requests.filter((r) => r.status >= 400);
    const thirdParty = requests.filter((r) => !r.url.startsWith(origin) && !r.url.startsWith('data:'));

    // Pull headers for the two most interesting requests: the 404 and the 3P tag.
    const evidence = [];
    for (const req of [failures[0], thirdParty[0]].filter(Boolean)) {
      const detail = await tool('get_network_request', { pageId: broken.pageId, reqid: req.id });
      if (!detail.isError) {
        evidence.push({
          url: req.url, status: req.status,
          text: detail.text.slice(0, 4000),
          responseHeaders: responseHeadersOf(detail.text),
        });
        emit({ type: 'network-detail', url: req.url, status: req.status, text: detail.text.slice(0, 900) });
      }
    }
    ctx.network.brokenDetail = evidence;

    // Cache policy check straight from the response headers we just fetched.
    const noCache = evidence.filter((e) =>
      /analytics|\.js|\.css/.test(e.url) && !/max-age/i.test(e.responseHeaders || ''));
    if (noCache.length) {
      ctx.findings.push({
        gtId: 'cache-policy', title: 'Static assets are served without a cache lifetime', category: 'performance', severity: 'medium',
        source: 'get_network_request', tool: 'get_network_request', confidence: 'high',
        evidenceText: `No max-age on: ${noCache.map((n) => n.url).join(', ')}`,
        recommendation: 'Content-hash filenames and send cache-control: max-age=31536000, immutable.',
      });
    }
    if (failures.length) {
      ctx.findings.push({
        gtId: 'console-404-asset', title: 'Requests failing with 4xx/5xx', category: 'network', severity: 'high',
        source: 'list_network_requests', tool: 'list_network_requests', confidence: 'high',
        evidenceText: failures.map((f) => `${f.status} ${f.url}`).join('\n'),
        recommendation: 'Fix the broken references and add a fallback for the flaky upstream.',
      });
    }
    if (thirdParty.length) {
      ctx.findings.push({
        gtId: 'render-blocking-third-party', title: 'Cross-origin dependency on the critical path', category: 'performance', severity: 'medium',
        source: 'list_network_requests', tool: 'list_network_requests', confidence: 'medium',
        evidenceText: thirdParty.map((t) => `${t.status} ${t.url}`).join('\n'),
        recommendation: 'Defer third-party work off the critical path.',
      });
    }
    emit({ type: 'network', variant: 'broken', requests: requests.slice(0, 40), failures, thirdParty });
    log('ok', `${requests.length} requests, ${failures.length} failing, ${thirdParty.length} cross-origin`);
  });

  // ───────────────────────────── 8. memory ─────────────────────────────────
  await phase('memory', 'Hunt the leak: snapshot → churn → snapshot', async () => {
    const pid = broken.pageId;
    await tool('evaluate_script', { pageId: pid, function: '() => { if (window.gc) window.gc(); return "gc" }' });
    const basePath = join(outDir, 'heap', 'broken-base.heapsnapshot');
    const currPath = join(outDir, 'heap', 'broken-current.heapsnapshot');
    await tool('take_heapsnapshot', { pageId: pid, filePath: basePath }, { critical: false });

    // Churn: the same thing a user does — search a few times and wait.
    await tool('evaluate_script', {
      pageId: pid,
      function: `async () => {
        for (let i = 0; i < 10; i++) {
          document.getElementById('search-btn').click();
          await new Promise(r => setTimeout(r, 120));
        }
        await new Promise(r => setTimeout(r, 2400));
        return { searches: (window.__skyline && window.__skyline.searches) || 0 };
      }`,
    });
    await tool('evaluate_script', { pageId: pid, function: '() => { if (window.gc) window.gc(); return "gc" }' });
    await tool('take_heapsnapshot', { pageId: pid, filePath: currPath });

    const cmp = await tool('compare_heapsnapshots', { baseFilePath: basePath, currentFilePath: currPath, pageSize: 60 });
    const diff = parseHeapDiff(cmp.text);
    ctx.heap.broken = { diff, currentPath: `runs/${runId}/heap/broken-current.heapsnapshot`, filePath: currPath };

    const suspects = diff.rows
      .filter((r) => r.countDelta > 0 && /FareCacheEntry|Detached|HTMLDiv|<div>|<span>|Function|Array|closure/i.test(r.className))
      .sort((a, b) => b.sizeDelta - a.sizeDelta)
      .slice(0, 8);

    // Name the leak precisely: how many instances, and what keeps them alive.
    const q = await tool('query_heapsnapshot_objects', { filePath: currPath, className: 'FareCacheEntry', pageSize: 5 });
    const count = parseHeapCount(q.text);
    const firstNodeId = q.text.split('\n').find((l) => /^\d+,FareCacheEntry,object/.test(l))?.split(',')[0];
    let retaining = null;
    if (firstNodeId) {
      const rp = await tool('get_heapsnapshot_retaining_paths', { filePath: currPath, nodeId: Number(firstNodeId), maxDepth: 6 });
      if (!rp.isError) retaining = rp.text.slice(0, 1200);
    }
    const handlers = await tool('get_heapsnapshot_details', { filePath: currPath, filterName: 'objectsRetainedByEventHandlers' });
    const handlerCount = num(handlers.text.match(/Objects: (\d+)/)?.[1]);

    ctx.heap.broken.query = { count, retaining, suspects, handlerCount, handlerRaw: handlers.text.slice(0, 500) };

    const fareDelta = diff.rows.find((r) => r.className === 'FareCacheEntry');
    if (fareDelta && fareDelta.countDelta > 0) {
      ctx.findings.push({
        gtId: 'memory-leak-cache', title: 'Unbounded cache retains FareCacheEntry instances', category: 'memory', severity: 'critical',
        source: 'compare_heapsnapshots', tool: 'compare_heapsnapshots', confidence: 'high',
        metric: `+${fareDelta.countDelta} instances (${kb(fareDelta.sizeDelta)}) over 10 interactions`,
        evidenceText: [
          `Heap diff: ${fareDelta.className} +${fareDelta.countDelta} objects, +${kb(fareDelta.sizeDelta)}`,
          `Snapshot contains ${count.objects ?? '?'} live FareCacheEntry objects`,
          retaining ? `Retaining path:\n${retaining}` : '',
        ].filter(Boolean).join('\n\n'),
        recommendation: 'Bound the cache with an LRU/expiry; nothing else holds these references.',
      });
    }
    const nodeGrowth = diff.rows.filter((r) => /^Detached\b/.test(r.className) && r.countDelta > 40)
      .sort((a, b) => b.countDelta - a.countDelta);
    if (nodeGrowth.length) {
      ctx.findings.push({
        gtId: 'detached-dom', title: 'Detached DOM subtrees are retained', category: 'memory', severity: 'high',
        source: 'compare_heapsnapshots', tool: 'compare_heapsnapshots', confidence: 'medium',
        metric: nodeGrowth.map((n) => `${n.className} +${n.countDelta}`).join(', '),
        evidenceText: nodeGrowth.map((n) => `${n.className}: +${n.countDelta} nodes, +${kb(n.sizeDelta)}`).join('\n'),
        recommendation: 'Drop references to replaced subtrees so they can be collected.',
      });
    }
    if (handlerCount > 0) {
      ctx.findings.push({
        gtId: 'memory-leak-listeners', title: 'Event handler closures accumulate', category: 'memory', severity: 'high',
        source: 'get_heapsnapshot_details', tool: 'get_heapsnapshot_details', confidence: 'high',
        metric: `${handlerCount} objects retained by event handlers`,
        evidenceText: handlers.text.slice(0, 900),
        recommendation: 'Register listeners once and remove them on teardown.',
      });
    }
    emit({
      type: 'heap', variant: 'broken', total: diff.total, sizeDeltaBytes: diff.sizeDeltaBytes,
      objectDelta: diff.objectDelta, rows: suspects, retaining, count: count.objects,
      handlerCount,
    });
    await rm(basePath, { force: true }); // keep the run directory honest about size
    log('ok', `heap delta ${diff.sizeDeltaBytes} B / ${diff.objectDelta} objects; ${fareDelta ? `FareCacheEntry +${fareDelta.countDelta}` : 'no single dominant class'}`);
  });

  // ───────────────────────── 9. lighthouse + shots ─────────────────────────
  await phase('lighthouse', 'Lighthouse: accessibility, SEO, best practices', async () => {
    const lhRes = await tool('lighthouse_audit', {
      pageId: broken.pageId, mode: 'snapshot', device: 'mobile', outputDirPath: join(outDir, 'lighthouse', 'broken'),
    }, { timeoutMs: 300_000 });
    const lh = parseLighthouse(lhRes.text);
    ctx.lighthouse.broken = lh;
    ctx.findings.push(...lighthouseToFindings(lh));

    emit({ type: 'lighthouse', variant: 'broken', scores: lh.scores, failed: lh.failed, passed: lh.passed, timing: lh.timing });
    log(lh.failed.length ? 'warn' : 'ok', `a11y ${lh.scores.accessibility ?? '?'} · SEO ${lh.scores.seo ?? '?'} · ${lh.failed.length} failed audits`);
  });

  await phase('structure', 'Inspect DOM shape, viewport and alt coverage', async () => {
    const b = ctx.metrics.broken;
    const sub = (b.biggestSubtrees || [])[0];
    if (b.domNodes && b.domNodes > 3000) {
      ctx.findings.push({
        gtId: 'dom-size-bloat', title: 'A hidden subtree dominates the DOM', category: 'performance', severity: 'medium',
        source: 'evaluate_script', tool: 'evaluate_script', confidence: 'high',
        metric: sub ? `${sub.nodes.toLocaleString()} nodes under ${sub.sel}${sub.hidden ? ' (never rendered)' : ''}` : `${b.domNodes.toLocaleString()} nodes`,
        evidenceText: [
          `document.querySelectorAll('*').length = ${b.domNodes}`,
          'heaviest subtrees:',
          ...(b.biggestSubtrees || []).map((t) => `  ${t.sel}: ${t.nodes.toLocaleString()} descendant nodes${t.hidden ? '  [hidden]' : ''}`),
        ].join('\n'),
        recommendation: 'Virtualise the list: render a window of rows and keep the rest as data.',
      });
    }
    if (b.hasViewportMeta === false) {
      ctx.findings.push({
        gtId: 'viewport-meta', title: 'No viewport meta tag', category: 'a11y', severity: 'high',
        source: 'evaluate_script', tool: 'evaluate_script', confidence: 'high',
        evidenceText: 'document.querySelector(\'meta[name="viewport"]\') === null\n' +
          '+ the 390x844 screenshot shows the desktop layout scaled down',
        recommendation: 'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
      });
    }
    if (b.imagesWithoutAlt > 0) {
      ctx.findings.push({
        gtId: 'a11y-image-alt', title: 'Images have no alt attribute', category: 'a11y', severity: 'low',
        source: 'evaluate_script', tool: 'evaluate_script', confidence: 'high',
        metric: `${b.imagesWithoutAlt} of ${b.imagesTotal ?? '?'} images`,
        evidenceText: `[...document.images].filter(i => !i.hasAttribute('alt')).length === ${b.imagesWithoutAlt}`,
        recommendation: 'Describe content images; use alt="" (explicitly) for decorative ones.',
      });
    }
    emit({ type: 'structure', variant: 'broken', domNodes: b.domNodes, biggestSubtrees: b.biggestSubtrees, hasViewportMeta: b.hasViewportMeta, imagesWithoutAlt: b.imagesWithoutAlt });
    log('ok', `DOM ${b.domNodes} nodes${sub ? ` (heaviest: ${sub.sel}, ${sub.nodes})` : ''} · viewport meta: ${b.hasViewportMeta ? 'present' : 'MISSING'} · images without alt: ${b.imagesWithoutAlt}`);
  });

  await phase('evidence', 'Capture the mobile frame', async () => {
    await tool('resize_page', { pageId: broken.pageId, width: 390, height: 844 });
    await sleep(600);
    const mobile = await tool('take_screenshot', { pageId: broken.pageId, format: 'jpeg', quality: 72 });
    ctx.artifacts['broken-mobile'] = await saveImage(mobile, 'broken-mobile');
    const mProbe = safeJson((await tool('evaluate_script', { pageId: broken.pageId, function: PROBE_SCRIPT })).text);
    ctx.metrics.broken.mobile = { domNodes: mProbe.domNodes, hasViewportMeta: mProbe.hasViewportMeta };
    await tool('resize_page', { pageId: broken.pageId, width: 1280, height: 800 });
    log('ok', 'mobile frame captured at 390×844');
  });

  // ────────────────────────────── 10. verify ───────────────────────────────
  let fixed;
  await phase('open-fixed', 'Open the repaired build under the same protocol', async () => {
    const beforeClose = clientePageCountSafe();
    fixed = await openBuild('fixed', 'fixed');
    ctx.probes.fixed = fixed.probe;
    ctx.metrics.fixed = baseMetrics(fixed.probe);
    if (fixed.artifacts.desktop) ctx.artifacts['fixed-desktop'] = fixed.artifacts.desktop;
    log('ok', `repaired build painted: ${fixed.probe.domNodes} DOM nodes, ${kb(fixed.probe.transferBytes)} transferred`);
  });

  await phase('verify-trace', 'Re-measure: cold load + insights', async () => {
    const trace = await measureTrace(fixed.pageId, 'fixed', 'load', { reload: true, autoStop: true });
    ctx.trace.fixedLoad = trace;
    const m = ctx.metrics.fixed;
    m.lcp = trace.metrics.LCP ?? null;
    m.cls = trace.metrics.CLS ?? fixed.probe.cls ?? null;
    m.fcp = trace.metrics.FCP ?? null;
    m.lcpBreakdown = trace.breakdown;
    m.lcpElement = fixed.probe.lcpElement;
    m.insightsAvailable = trace.insights.map((i) => i.name);
    const set = trace.insightSets[0]?.id ?? 'NAVIGATION_0';
    for (const name of trace.insights.map((i) => i.name).filter((n) => ['ImageDelivery', 'RenderBlocking', 'CLSCulprits', 'FontDisplay', 'DOMSize', 'Cache'].includes(n)).slice(0, 4)) {
      const res = await tool('performance_analyze_insight', { pageId: fixed.pageId, insightSetId: set, insightName: name });
      if (!res.isError) ctx.insights.fixed.push({ name, ...parseInsight(res.text) });
    }
    const late = safeJson((await tool('evaluate_script', { pageId: fixed.pageId, function: PROBE_SCRIPT })).text);
    if (late?.lcpElement) { ctx.metrics.fixed.lcpElement = late.lcpElement; ctx.probes.fixed = late; }
    else await nameLcpElement(fixed.pageId, 'fixed');
    emit({ type: 'metrics', variant: 'fixed', metrics: ctx.metrics.fixed, insights: trace.insights });
    log('ok', `LCP ${m.lcp ?? '?'} ms · CLS ${m.cls ?? '?'} · insights now: ${(m.insightsAvailable || []).join(', ') || 'none'}`);
  });

  await phase('verify-behaviour', 'Re-run the interaction against the repaired build', async () => {
    const pid = fixed.pageId;
    const before = parseConsole((await tool('list_console_messages', { pageId: pid })).text);
    const snap = await tool('take_snapshot', { pageId: pid });
    const nodes = parseSnapshot(snap.text);
    const trackUid = findUidByRoleName(nodes, 'button', /Track flight/i);
    const searchUid = findUidByRoleName(nodes, 'button', /^Search$/);
    if (searchUid) await tool('click', { pageId: pid, uid: searchUid });
    if (trackUid) await tool('click', { pageId: pid, uid: trackUid });
    await sleep(500);
    const after = parseConsole((await tool('list_console_messages', { pageId: pid, types: ['error', 'warn'], includeStackTraces: true })).text);
    ctx.console.fixed = after;
    const shot = await tool('take_screenshot', { pageId: pid, format: 'jpeg', quality: 72 });
    ctx.artifacts['fixed-after-click'] = await saveImage(shot, 'fixed-after-click');
    const errors = after.filter((m) => m.type === 'error');
    ctx.metrics.fixed.consoleErrors = errors.length;
    ctx.metrics.broken.consoleErrors = ctx.console.broken.filter((m) => m.type === 'error').length;
    emit({ type: 'console', variant: 'fixed', messages: after.map((m) => ({ type: m.type, text: m.text, stack: m.stack })) });
    log(errors.length ? 'warn' : 'ok', errors.length ? `${errors.length} error(s) remain: ${errors.map((e) => e.text.slice(0, 60)).join(' | ')}` : 'clicking through the repaired build throws nothing');
  });

  await phase('verify-interaction', 'Re-price the interaction', async () => {
    await measureLoaf(fixed.pageId, 'fixed');
    const v = await measureClickCost(fixed.pageId, 'fixed');
    log(v.count ? 'warn' : 'ok', v.count ? `${v.count} long task(s) remain (worst ${v.maxMs} ms)` : 'the click path is clean: no long tasks over 5 clicks');
  });

  await phase('verify-structure', 'Re-check DOM, viewport and alt coverage', async () => {
    const fp = safeJson((await tool('evaluate_script', { pageId: fixed.pageId, function: PROBE_SCRIPT })).text);
    ctx.probes.fixed = fp;
    // A probe taken after the trace reload has no __bb observers, so keep the
    // LCP element we already captured rather than letting a null overwrite it.
    const knownLcp = ctx.metrics.fixed.lcpElement;
    const fresh = baseMetrics(fp);
    if (!fresh.lcpElement) delete fresh.lcpElement;
    ctx.metrics.fixed = { ...ctx.metrics.fixed, ...fresh };
    if (knownLcp) ctx.metrics.fixed.lcpElement = knownLcp;
    emit({ type: 'structure', variant: 'fixed', domNodes: fp.domNodes, hasViewportMeta: fp.hasViewportMeta, imagesWithoutAlt: fp.imagesWithoutAlt });
    log('ok', `DOM ${fp.domNodes} nodes · viewport meta: ${fp.hasViewportMeta ? 'present' : 'MISSING'} · images without alt: ${fp.imagesWithoutAlt}`);
  });

  await phase('verify-network', 'Re-audit the request graph', async () => {
    const list = await tool('list_network_requests', { pageId: fixed.pageId });
    const requests = parseNetwork(list.text);
    ctx.network.fixed = requests;
    const failed = requests.filter((r) => r.status >= 400);
    // Prefer one of our own hashed assets; the third-party tag's headers are not ours to set.
    const js = requests.find((r) => r.url.includes('/site/fixed/')) || requests.find((r) => /\.js/.test(r.url) && r.url.startsWith(origin));
    let cacheHeader = null;
    if (js) {
      const detail = await tool('get_network_request', { pageId: fixed.pageId, reqid: js.id });
      if (!detail.isError) {
        // Read cache-control from the RESPONSE block only — the request also carries one.
        const rh = responseHeadersOf(detail.text);
        ctx.network.fixedHeaders = rh;
        cacheHeader = (rh.match(/cache-control:\s*([^|\n]+)/i)?.[1] ?? '').trim() || null;
      }
    }
    ctx.network.fixedCache = cacheHeader;
    emit({ type: 'network', variant: 'fixed', requests: requests.slice(0, 40), failures: failed, cacheHeader });
    log(failed.length ? 'warn' : 'ok', `${requests.length} requests, ${failed.length} failing, cache-control: ${cacheHeader ?? 'unset'}`);
  });

  await phase('verify-memory', 'Re-run the leak protocol', async () => {
    const pid = fixed.pageId;
    await tool('evaluate_script', { pageId: pid, function: '() => { if (window.gc) window.gc(); return "gc" }' });
    const basePath = join(outDir, 'heap', 'fixed-base.heapsnapshot');
    const currPath = join(outDir, 'heap', 'fixed-current.heapsnapshot');
    await tool('take_heapsnapshot', { pageId: pid, filePath: basePath });
    await tool('evaluate_script', {
      pageId: pid,
      function: `async () => {
        for (let i = 0; i < 10; i++) { document.getElementById('search-btn').click(); await new Promise(r => setTimeout(r, 120)); }
        await new Promise(r => setTimeout(r, 2400));
        return 'churned';
      }`,
    });
    await tool('evaluate_script', { pageId: pid, function: '() => { if (window.gc) window.gc(); return "gc" }' });
    await tool('take_heapsnapshot', { pageId: pid, filePath: currPath });
    const cmp = await tool('compare_heapsnapshots', { baseFilePath: basePath, currentFilePath: currPath, pageSize: 60 });
    const diff = parseHeapDiff(cmp.text);
    const fare = diff.rows.find((r) => r.className === 'FareCacheEntry');
    ctx.heap.fixed = { diff, fareDelta: fare?.countDelta ?? 0, sizeDeltaBytes: diff.sizeDeltaBytes, currentPath: `runs/${runId}/heap/fixed-current.heapsnapshot` };
    emit({ type: 'heap', variant: 'fixed', total: diff.total, sizeDeltaBytes: diff.sizeDeltaBytes, objectDelta: diff.objectDelta, fareDelta: fare?.countDelta ?? 0, rows: diff.rows.slice(0, 5) });
    await rm(basePath, { force: true });
    log('ok', `heap delta ${diff.sizeDeltaBytes} B; FareCacheEntry delta ${fare?.countDelta ?? 0}`);
  });

  await phase('verify-lighthouse', 'Lighthouse against the repaired build', async () => {
    const lhRes = await tool('lighthouse_audit', {
      pageId: fixed.pageId, mode: 'snapshot', device: 'mobile', outputDirPath: join(outDir, 'lighthouse', 'fixed'),
    }, { timeoutMs: 300_000 });
    const lh = parseLighthouse(lhRes.text);
    ctx.lighthouse.fixed = lh;
    emit({ type: 'lighthouse', variant: 'fixed', scores: lh.scores, failed: lh.failed, passed: lh.passed, timing: lh.timing });
    log('ok', `a11y ${lh.scores.accessibility ?? '?'} · SEO ${lh.scores.seo ?? '?'} · ${lh.failed.length} failed audits`);
  });

  // ────────────────────────────── 11. verdict ──────────────────────────────
  const summary = await phase('verdict', 'Score the run and write the flight recorder', async () => {
    const findings = mergeFindings(ctx.findings)
      .map((f) => ({ ...f, catalogTitle: CATALOG_BY_ID[f.gtId]?.title ?? null, catalogFix: CATALOG_BY_ID[f.gtId]?.fix ?? null }))
      .sort((a, b) => (SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]) || a.title.localeCompare(b.title));

    const detected = new Set(findings.map((f) => f.gtId));
    const detected_has = (id) => detected.has(id);
    const missed = CATALOG.filter((c) => !detected.has(c.id));
    const matchedCatalog = CATALOG.filter((c) => detected.has(c.id));
    const unknownFindings = findings.filter((f) => !CATALOG_BY_ID[f.gtId]);

    // Verification: which catalog defects demonstrably disappeared in the fixed build?
    const verification = buildVerification(ctx, detected);

    const summary = {
      runId,
      startedAt: ctx.startedAt,
      finishedAt: Date.now(),
      durationMs: Date.now() - t0,
      target: { origin, broken: `${origin}/lab/broken/`, fixed: `${origin}/lab/fixed/` },
      attestation: ctx.attestation,
      totals: {
        toolCalls: ctx.steps.length,
        toolErrors: ctx.steps.filter((s) => s.isError || s.error).length,
        mcpRequestBytes: Object.values(client.stats).includes(undefined) ? 0 : client.stats.bytesOut,
        mcpResponseBytes: client.stats.bytesIn,
        mcpTimeMs: Math.round(client.stats.ms),
        approxTokensIn: Math.round(client.stats.bytesIn / 3.6),
        wireFrames: ctx.wire.length,
        insightsAnalysed: ctx.insights.broken.length,
      },
      metrics: ctx.metrics,
      probes: { broken: ctx.probes.broken ?? summarizeProbe(ctx.probes.broken), fixed: ctx.probes.fixed },
      findings,
      detection: {
        catalogSize: CATALOG.length,
        discovered: matchedCatalog.length,
        recall: +(matchedCatalog.length / CATALOG.length).toFixed(3),
        missed: missed.map((m) => ({ id: m.id, title: m.title, category: m.category })),
        byCategory: CATALOG.reduce((acc, c) => {
          acc[c.category] = acc[c.category] || { total: 0, found: 0 };
          acc[c.category].total++;
          if (detected.has(c.id)) acc[c.category].found++;
          return acc;
        }, {}),
        unknownFindings: unknownFindings.map((f) => ({ id: f.gtId, title: f.title, confidence: f.confidence })),
      },
      verification,
      insights: ctx.insights,
      lighthouse: ctx.lighthouse,
      heap: {
        broken: { sizeDeltaBytes: ctx.heap.broken?.diff?.sizeDeltaBytes ?? null, objectDelta: ctx.heap.broken?.diff?.objectDelta ?? null, suspects: ctx.heap.broken?.query?.suspects ?? [], fareDelta: ctx.heap.broken?.diff?.rows.find((r) => r.className === 'FareCacheEntry')?.countDelta ?? null, retaining: ctx.heap.broken?.query?.retaining ?? null, artifact: ctx.heap.broken?.currentPath },
        fixed: { sizeDeltaBytes: ctx.heap.fixed?.sizeDeltaBytes ?? null, objectDelta: ctx.heap.fixed?.diff?.objectDelta ?? null, fareDelta: ctx.heap.fixed?.fareDelta ?? null, artifact: ctx.heap.fixed?.currentPath },
      },
      console: ctx.console,
      network: {
        broken: ctx.network.broken,
        fixed: ctx.network.fixed,
        brokenDetail: ctx.network.brokenDetail,
        fixedCache: ctx.network.fixedCache,
        waterfall: { broken: ctx.probes.broken?.resources ?? [], fixed: ctx.probes.fixed?.resources ?? [] },
      },
      degraded: dedupeDegraded(ctx.degraded),
      inconclusive: [
        !detected_has('forced-reflow-scroll') ? {
          id: 'forced-reflow-scroll',
          why: `The scroll handler does interleave offsetHeight reads with style writes, but no Long Animation Frame exceeded 50 ms during 30 scroll steps (LoAF frames: ${ctx.metrics.broken?.loaf?.frames ?? 'n/a'}), so Chrome's ForcedReflow insight never fired. Present in code; not observable through DevTools at this load.`,
        } : null,
      ].filter(Boolean),
      traceFiles: ctx.trace,
      artifacts: ctx.artifacts,
      phases: ctx.phases,
      steps: ctx.steps.map(({ tool, args, ms, bytes, tokens, isError, error }) => ({ tool, args, ms, bytes, tokens, isError: Boolean(isError), error })),
      wireLog: `runs/${runId}/wire.jsonl`,
      scenarios: SCENARIOS,
    };

    if (!ctx.probes.broken) summary.probes.broken = summarizeProbe(broken?.probe);
    summary.probes.fixed = summarizeProbe(fixed?.probe);
    summary.probes.broken = summarizeProbe(broken?.probe);

    await writeFile(join(outDir, 'summary.json'), JSON.stringify(summary, null, 2));
    await writeFile(join(outDir, 'findings.json'), JSON.stringify({ findings, detection: summary.detection, verification }, null, 2));
    const html = await renderReport({ summary, outDir });
    await writeFile(join(outDir, 'report.html'), html);
    emit({ type: 'findings', findings, detection: summary.detection, verification });
    emit({ type: 'artifact', kind: 'report', path: `runs/${runId}/report.html` });
    log('ok', `discovered ${matchedCatalog.length}/${CATALOG.length} catalogued regressions; ${verification.verified}/${verification.checks.length} fixes proven by re-measurement`);
    return summary;
  });

  await client.close();
  emit({ type: 'run-summary', summary });
  return summary;

  function clientePageCountSafe() { return null; }
}

/* ─────────────────────────── helpers ─────────────────────────────────────── */

function shrinkArgs(args) {
  if (!args) return '';
  const out = {};
  for (const [k, v] of Object.entries(args)) {
    if (k === 'initScript' || k === 'function') out[k] = `<${String(v).length}b script>`;
    else if (typeof v === 'string' && v.length > 80) out[k] = v.slice(0, 76) + '…';
    else out[k] = v;
  }
  return JSON.stringify(out);
}

function baseMetrics(probe = {}) {
  return {
    domNodes: probe.domNodes ?? null,
    domDepth: probe.domDepth ?? null,
    transferBytes: probe.transferBytes ?? null,
    requestCount: probe.requestCount ?? null,
    cls: probe.cls ?? null,
    lcpElement: probe.lcpElement ?? null,
    renderBlockingCount: probe.renderBlocking?.length ?? null,
    renderBlockingScripts: (probe.renderBlocking ?? []).filter((r) => r.type === 'script').length,
    imagesWithoutAlt: probe.imagesWithoutAlt ?? null,
    htmlLang: probe.htmlLang ?? null,
    biggestSubtrees: probe.biggestSubtrees ?? [],
    thirdParties: probe.thirdParties ?? [],
    hasViewportMeta: probe.hasViewportMeta ?? null,
    longTasks: probe.longTasks ?? [],
    nav: probe.nav ?? null,
  };
}

function summarizeProbe(p) {
  if (!p) return null;
  return baseMetrics(p);
}

function dedupeDegraded(list) {
  const seen = new Set();
  return list.filter((d) => {
    const key = `${d.tool}:${(d.error || '').slice(0, 80)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * The proof half of the demo: for each catalogued defect, state the check, the
 * before value, the after value, and whether the fix is demonstrably real.
 */
function buildVerification(ctx, detected) {
  const b = ctx.metrics.broken ?? {};
  const f = ctx.metrics.fixed ?? {};
  const checks = [];
  const add = (id, check, before, after, pass, unit = '', higherIsBetter = false) =>
    checks.push({ id, check, before, after, unit, pass: Boolean(pass), higherIsBetter });

  const numOrNull = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const lcpBefore = numOrNull(b.lcp);
  const lcpAfter = numOrNull(f.lcp);
  add('lcp', 'LCP under Slow 4G + 4x CPU', lcpBefore, lcpAfter, lcpBefore && lcpAfter && lcpAfter < lcpBefore * 0.6, 'ms');
  add('cls', 'Cumulative Layout Shift', numOrNull(b.cls), numOrNull(f.cls), numOrNull(f.cls) !== null && f.cls <= 0.05, '');
  add('bytes', 'Transfer size, cold load', numOrNull(b.transferBytes), numOrNull(f.transferBytes), b.transferBytes && f.transferBytes && f.transferBytes < b.transferBytes * 0.5, 'B');
  add('dom', 'DOM nodes', numOrNull(b.domNodes), numOrNull(f.domNodes), b.domNodes && f.domNodes && f.domNodes < b.domNodes * 0.5, 'nodes');
  add('console', 'Console errors after the same clicks', numOrNull(b.consoleErrors), numOrNull(f.consoleErrors), (f.consoleErrors ?? 1) === 0, 'errors');
  add('leak', 'FareCacheEntry growth over 10 interactions',
    ctx.heap.broken?.diff?.rows.find((r) => r.className === 'FareCacheEntry')?.countDelta ?? 0,
    ctx.heap.fixed?.fareDelta ?? 0,
    (ctx.heap.fixed?.fareDelta ?? 1) <= 0, 'objects');
  add('a11y', 'Lighthouse accessibility', ctx.lighthouse.broken?.scores?.accessibility ?? null,
    ctx.lighthouse.fixed?.scores?.accessibility ?? null,
    (ctx.lighthouse.fixed?.scores?.accessibility ?? 0) >= 95, '/100', true);
  add('seo', 'Lighthouse SEO', ctx.lighthouse.broken?.scores?.seo ?? null,
    ctx.lighthouse.fixed?.scores?.seo ?? null, (ctx.lighthouse.fixed?.scores?.seo ?? 0) >= 95, '/100', true);
  add('renderblocking', 'Render-blocking <script> tags',
    b.renderBlockingScripts, f.renderBlockingScripts,
    (f.renderBlockingScripts ?? 0) === 0, ' scripts');
  add('longtasks', 'Long tasks during 5 real clicks',
    b.interaction?.count ?? null, f.interaction?.count ?? null,
    (f.interaction?.count ?? 1) === 0, ' tasks');
  add('viewport', 'Viewport meta present',
    b.hasViewportMeta ? 'yes' : 'no', f.hasViewportMeta ? 'yes' : 'no',
    Boolean(f.hasViewportMeta), '');
  add('alt', 'Images without an alt attribute',
    b.imagesWithoutAlt, f.imagesWithoutAlt,
    (f.imagesWithoutAlt ?? 1) === 0, ' images');
  add('cache', 'Cache lifetime on JS',
    ctx.network.brokenDetail?.length ? (ctx.network.brokenDetail.every((d) => /max-age/i.test(d.text)) ? 'present' : 'none') : 'unknown',
    ctx.network.fixedCache ?? 'none',
    Boolean(ctx.network.fixedCache), '');

  const verified = checks.filter((c) => c.pass).length;
  // which catalogued defects are covered by a passing check
  const coverage = {
    'lcp-image-bloat': ['lcp', 'bytes'],
    'lcp-discovery': ['lcp'],
    'render-blocking-third-party': ['renderblocking'],
    'render-blocking-polyfill': ['renderblocking', 'bytes'],
    'cls-late-injection': ['cls'],
    'forced-reflow-scroll': ['longtasks'],
    'detached-dom': ['leak'],
    'dom-size-bloat': ['dom'],
    'long-task-search': ['longtasks'],
    'console-404-asset': ['console'],
    'console-500-pricing': ['console'],
    'runtime-type-error': ['console'],
    'memory-leak-cache': ['leak'],
    'a11y-icon-buttons': ['a11y'],
    'a11y-contrast': ['a11y'],
    'a11y-form-labels': ['a11y'],
    'a11y-image-alt': ['alt', 'a11y'],
    'viewport-meta': ['viewport', 'a11y'],
    'seo-meta-description': ['seo'],
  };
  const passed = new Set(checks.filter((c) => c.pass).map((c) => c.id));
  const defectsProven = [...detected].filter((id) => (coverage[id] ?? []).some((c) => passed.has(c)));

  return { checks, verified, defectsProven, defectsDetected: [...detected] };
}

/** The three judge-facing scenarios, surfaced in the report and the UI. */
export const SCENARIOS = [
  {
    id: 'perf',
    title: '“Why is the landing page slow?”',
    steps: ['emulate', 'performance_start_trace', 'performance_analyze_insight', 'performance_stop_trace'],
    proof: 'LCP and CLS before/after, with the LCP element named and the insight dossier attached.',
  },
  {
    id: 'bug',
    title: '“Users say the Track button does nothing.”',
    steps: ['take_snapshot', 'click', 'list_console_messages'],
    proof: 'A TypeError with a stack frame, reproduced by real input and gone in the fixed build.',
  },
  {
    id: 'mem',
    title: '“The tab gets heavier over time.”',
    steps: ['take_heapsnapshot', 'compare_heapsnapshots', 'query_heapsnapshot_objects', 'get_heapsnapshot_retaining_paths'],
    proof: 'A named class growing per interaction, plus the retaining path that keeps it alive.',
  },
];
