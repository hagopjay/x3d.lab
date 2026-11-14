/**
 * report.mjs — writes a single self-contained HTML "flight recorder".
 *
 * No external requests: CSS is inline, screenshots are base64 data URIs, charts
 * are hand-built SVG. Open it from disk, email it, archive it in CI.
 */
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SEV = { critical: 0, high: 1, medium: 2, low: 3 };
const fmt = (n, unit = '') => (n === null || n === undefined || Number.isNaN(n) ? '—' : `${n}${unit}`);
const kb = (b) => (b == null ? '—' : b >= 1048576 ? (b / 1048576).toFixed(2) + ' MB' : Math.round(b / 1024) + ' kB');
const pct = (a, b) => (typeof a === 'number' && typeof b === 'number' && a ? Math.round(((a - b) / a) * 100) : null);
const dot = (ok) => `<span class="dot ${ok ? 'ok' : 'bad'}" aria-label="${ok ? 'pass' : 'fail'}"></span>`;

async function dataUri(outDir, relPath) {
  if (!relPath) return null;
  try {
    // relPath looks like "runs/<runId>/screens/foo.jpg"; outDir already is runs/<runId>
    const rel = relPath.split('/').slice(2).join('/');
    const buf = await readFile(join(outDir, rel));
    const ext = relPath.endsWith('.png') ? 'png' : 'jpeg';
    return `data:image/${ext};base64,${buf.toString('base64')}`;
  } catch {
    return null;
  }
}

/* ── tiny SVG waterfall from PerformanceResourceTiming ───────────────────── */
function waterfall(resources = [], { width = 760 } = {}) {
  if (!resources.length) return '<p class="muted">no resource timings captured</p>';
  const rows = resources
    .slice()
    .sort((a, b) => a.start - b.start)
    .slice(0, 14);
  const maxEnd = Math.max(...rows.map((r) => r.start + r.dur), 1);
  const rowH = 18;
  const left = 300;
  const chartW = width - left - 90;
  const height = rows.length * rowH + 26;
  const scale = (t) => (t / maxEnd) * chartW;
  const bars = rows.map((r, i) => {
    const y = i * rowH + 18;
    const w = Math.max(2, scale(r.dur));
    const x = left + scale(r.start);
    const blocking = r.rb === 'blocking';
    const color = blocking ? '#ff5d5d' : r.type === 'img' ? '#ffb020' : r.crossOrigin ? '#b98bff' : '#3ddc97';
    return `
      <text x="0" y="${y + 11}" class="wl" fill="#93a1bb">${esc(r.name.length > 42 ? r.name.slice(0, 41) + '…' : r.name)}</text>
      <rect x="${left}" y="${y}" width="${chartW}" height="10" fill="#141d31" rx="2"/>
      <rect x="${x}" y="${y}" width="${w}" height="10" fill="${color}" rx="2"/>
      <text x="${left + chartW + 6}" y="${y + 9}" class="wl" fill="#6d7994">${r.bytes ? kb(r.bytes) : ''}${blocking ? ' RB' : ''}</text>`;
  }).join('');
  return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="Resource waterfall">
    ${bars}
    <line x1="${left}" y1="10" x2="${left}" y2="${height - 8}" stroke="#22304d"/>
    <text x="${left}" y="${height - 1}" class="wl" fill="#52607a">0</text>
    <text x="${left + chartW - 30}" y="${height - 1}" class="wl" fill="#52607a">${Math.round(maxEnd)} ms</text>
  </svg>`;
}

/* ── before/after KPI ────────────────────────────────────────────────────── */
function kpi(label, before, after, { unit = '', invert = false, digits = 0 } = {}) {
  const b = typeof before === 'number' ? Number(before.toFixed(digits)) : before;
  const a = typeof after === 'number' ? Number(after.toFixed(digits)) : after;
  const bNum = typeof b === 'number';
  const aNum = typeof a === 'number';
  const max = bNum && aNum ? Math.max(b, a) || 1 : 1;
  const good = bNum && aNum ? (invert ? a > b : a < b) : null;
  const delta = bNum && aNum && b ? Math.round(((a - b) / b) * 100) : null;
  const bar = (v, cls) => `<div class="bar ${cls}"><span style="width:${bNum && aNum ? Math.max(3, (v / max) * 100) : 0}%"></span></div>`;
  return `<div class="kpi">
    <div class="kpi-label">${esc(label)}</div>
    <div class="kpi-row"><span class="kpi-tag">before</span><span class="kpi-val">${fmt(b, unit)}</span>${bNum ? bar(b, 'bad') : ''}</div>
    <div class="kpi-row"><span class="kpi-tag good">after</span><span class="kpi-val">${fmt(a, unit)}</span>${aNum ? bar(a, 'ok') : ''}</div>
    <div class="kpi-delta ${good === null ? '' : good ? 'down' : 'up'}">${delta === null ? '' : `${delta > 0 ? '+' : ''}${delta}%`}</div>
  </div>`;
}

export async function renderReport({ summary, outDir }) {
  const m = summary.metrics || {};
  const b = m.broken || {};
  const f = m.fixed || {};
  const det = summary.detection || {};
  const ver = summary.verification || { checks: [] };
  const findings = summary.findings || [];
  const heap = summary.heap || {};
  const lh = summary.lighthouse || {};
  const totals = summary.totals || {};

  const screens = {};
  for (const [k, v] of Object.entries(summary.artifacts || {})) screens[k] = await dataUri(outDir, v);

  const severityCount = findings.reduce((acc, x) => { acc[x.severity] = (acc[x.severity] || 0) + 1; return acc; }, {});
  const passes = ver.checks.filter((c) => c.pass).length;

  const findingCards = findings.map((fd, i) => `
    <article class="finding sev-${esc(fd.severity)}">
      <header>
        <span class="pill sev">${esc(fd.severity)}</span>
        <span class="pill cat">${esc(fd.category)}</span>
        <span class="pill conf">${esc(fd.confidence)} confidence</span>
        <h3>${esc(fd.title)}</h3>
      </header>
      ${fd.metric ? `<p class="metric">${esc(fd.metric)}</p>` : ''}
      <div class="ev">
        <div class="ev-tools">${(fd.evidence || []).map((e) => `<code>${esc(e.tool)}</code>`).join(' ')}</div>
        <pre>${esc((fd.evidence || []).map((e) => e.text).filter(Boolean).join('\n\n───\n\n').slice(0, 2200))}</pre>
      </div>
      ${fd.catalogFix ? `<p class="fix"><strong>Fix:</strong> ${esc(fd.catalogFix)}</p>` : ''}
    </article>`).join('');

  const matrixRows = [
    ...findings.filter((x) => x.catalogTitle)
      .sort((a2, b2) => (SEV[a2.severity] ?? 9) - (SEV[b2.severity] ?? 9))
      .map((x) => ({
        id: x.gtId, title: x.catalogTitle, category: x.category, found: true,
        severity: x.severity, tools: [...new Set((x.evidence || []).map((e) => e.tool))].join(', '),
      })),
    ...(det.missed || []).map((x) => ({ ...x, found: false })),
  ].map((r) => `<tr class="${r.found ? 'found' : 'missed'}">
      <td>${dot(r.found)}</td>
      <td><code>${esc(r.id)}</code></td>
      <td>${esc(r.title)}</td>
      <td>${esc(r.category)}</td>
      <td>${esc(r.severity || '—')}</td>
      <td class="muted">${esc(r.tools || '—')}</td>
    </tr>`).join('');

  const unit = (u) => (u && String(u).trim() ? ' ' + esc(String(u).trim()) : '');
  const checkRows = ver.checks.map((c) => {
    let d = pct(c.before, c.after);
    if (d != null && c.higherIsBetter) d = -d;
    const deltaTxt = d == null ? '—' : `${d > 0 ? '+' : ''}${d}%`;
    return `<tr class="${c.pass ? 'pass' : 'fail'}">
      <td>${dot(c.pass)}</td>
      <td>${esc(c.check)}</td>
      <td class="num">${esc(String(c.before))}${unit(c.unit)}</td>
      <td class="num">${esc(String(c.after))}${unit(c.unit)}</td>
      <td class="num">${deltaTxt}</td>
    </tr>`;
  }).join('');

  const slowest = (summary.steps || [])
    .slice()
    .sort((x, y) => (y.ms || 0) - (x.ms || 0))
    .slice(0, 10);

  const toolTable = slowest.map((s) => `<tr>
      <td><code>${esc(s.tool)}</code></td>
      <td class="muted">${esc(String(s.args).slice(0, 90))}</td>
      <td class="num">${s.ms} ms</td>
      <td class="num">${Math.round((s.bytes || 0) / 1024)} kB</td>
      <td class="num">~${s.tokens || 0} tok</td>
    </tr>`).join('');

  const heapRows = (heap.broken?.suspects || []).map((r) => `<tr>
      <td><code>${esc(r.className)}</code></td>
      <td class="num">+${r.countDelta}</td>
      <td class="num">+${kb(r.sizeDelta)}</td>
    </tr>`).join('');

  const consoleRows = (summary.console?.broken || []).map((c) => `<li class="${esc(c.type)}">
      <span class="pill sev">${esc(c.type)}</span> <code>${esc(c.text)}</code>
      ${c.stack?.length ? `<pre>${esc(c.stack.join('\n'))}</pre>` : ''}
    </li>`).join('');

  const dur = Math.round((summary.durationMs || 0) / 1000);
  const i2b = kb(b.transferBytes);
  const totalTokens = totals.approxTokensIn || 0;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BLACKBOX flight recorder — ${esc(summary.runId)}</title>
<style>
  :root {
    --bg: #05080f; --panel: #0b1220; --panel2: #0e1626; --line: #1b2438;
    --ink: #e8eefc; --mid: #a3b0c7; --dim: #7d8aa5; --amber: #ffb020; --red: #ff5d5d;
    --green: #3ddc97; --violet: #b98bff; --blue: #6ea8ff;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.6 Inter, system-ui, -apple-system, sans-serif; }
  a { color: var(--amber); }
  code, pre, .mono { font-family: var(--mono); }
  .wrap { max-width: 1120px; margin: 0 auto; padding: 34px 22px 90px; }
  header.mast { border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 26px; }
  .brandline { display: flex; align-items: center; gap: 12px; font-family: var(--mono); letter-spacing: 0.26em; font-size: 12px; color: var(--amber); }
  h1 { font-size: 32px; letter-spacing: -0.02em; margin: 10px 0 6px; }
  h2 { font-size: 15px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--dim); margin: 40px 0 14px; }
  h3 { font-size: 16px; margin: 6px 0; }
  .sub { color: var(--mid); }
  .grid { display: grid; gap: 12px; }
  .g2 { grid-template-columns: 1fr 1fr; }
  .g3 { grid-template-columns: repeat(3, 1fr); }
  .g4 { grid-template-columns: repeat(4, 1fr); }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
  .panel h4 { margin: 0 0 10px; font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--dim); }
  .stat { font-family: var(--mono); font-size: 22px; }
  .verdict { background: linear-gradient(135deg, #10233a, #0b1220 60%); border: 1px solid #23405f; }
  .verdict .big { font-size: 26px; font-weight: 700; letter-spacing: -0.01em; }
  .kpi { background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
  .kpi-label { font-size: 12px; color: var(--mid); margin-bottom: 8px; min-height: 32px; }
  .kpi-row { display: grid; grid-template-columns: 44px 1fr; gap: 6px; align-items: center; font-family: var(--mono); font-size: 13px; }
  .kpi-row .bar { grid-column: 1 / -1; height: 6px; background: #101a2b; border-radius: 4px; overflow: hidden; }
  .kpi-row .bar span { display: block; height: 100%; }
  .bar.bad span { background: var(--red); }
  .bar.ok span { background: var(--green); }
  .kpi-tag { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--red); }
  .kpi-tag.good { color: var(--green); }
  .kpi-val { text-align: right; }
  .kpi-delta { font-family: var(--mono); font-size: 12px; text-align: right; color: var(--dim); }
  .kpi-delta.down { color: var(--green); } .kpi-delta.up { color: var(--red); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--dim); font-weight: 500; font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; }
  td.num, th.num { text-align: right; font-family: var(--mono); }
  tr.missed td { color: var(--dim); }
  .pill { display: inline-block; font-family: var(--mono); font-size: 10px; letter-spacing: 0.1em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--mid); }
  .pill.sev { border-color: #3a2a0a; color: var(--amber); }
  .sev-critical .pill.sev { border-color: #5b1a1a; color: #ff8080; }
  .sev-high .pill.sev { border-color: #4a3410; color: var(--amber); }
  .pill.cat { border-color: #1d2942; color: var(--blue); }
  .finding { background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--amber);
    border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
  .sev-critical { border-left-color: var(--red); }
  .sev-medium { border-left-color: var(--blue); }
  .sev-low { border-left-color: var(--dim); }
  .metric { font-family: var(--mono); color: var(--amber); font-size: 13px; margin: 4px 0 10px; }
  .ev pre { background: #070d18; border: 1px solid var(--line); border-radius: 8px; padding: 12px;
    overflow-x: auto; font-size: 11.5px; line-height: 1.5; color: #c7d3e8; max-height: 320px; margin: 8px 0 0; white-space: pre-wrap; }
  .ev-tools code { background: #101a2b; border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; font-size: 11px; color: var(--blue); }
  .fix { color: var(--mid); font-size: 13px; margin: 10px 0 0; }
  ul.console { list-style: none; padding: 0; margin: 0; }
  ul.console li { padding: 8px 0; border-bottom: 1px solid var(--line); }
  ul.console pre { margin: 6px 0 0; color: var(--dim); font-size: 11.5px; white-space: pre-wrap; }
  .muted { color: var(--dim); }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
  .dot.ok { background: var(--green); box-shadow: 0 0 8px rgba(61,220,151,0.5); }
  .dot.bad { background: #2a3346; border: 1px solid var(--faint); }
  .mono-sm { font-family: var(--mono); font-size: 12px; color: var(--mid); }
  .shot { width: 100%; border-radius: 10px; border: 1px solid var(--line); display: block; }
  .wl { font-family: var(--mono); font-size: 9px; }
  .foot { margin-top: 46px; border-top: 1px solid var(--line); padding-top: 16px; color: var(--dim); font-size: 12px; }
  .degraded { border-left: 3px solid var(--amber); background: #171105; padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; font-size: 13px; }
  .cap { font-size: 12px; color: var(--dim); margin-top: 6px; }
  @media (max-width: 820px) { .g2, .g3, .g4 { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div class="brandline">◆ BLACKBOX · FLIGHT RECORDER</div>
    <h1>Autonomous browser forensics over chrome-devtools-mcp</h1>
    <p class="sub">
      Run <code>${esc(summary.runId)}</code> · ${dur}s · ${esc(summary.attestation?.server?.name)} v${esc(summary.attestation?.server?.version)}
      · MCP ${esc(summary.attestation?.protocolVersion)} · ${esc(totals.toolCalls)} tool calls · ${esc(totals.wireFrames)} wire frames
    </p>
  </header>

  <div class="grid g3">
    <div class="panel verdict" style="grid-column: span 2">
      <h4>Verdict</h4>
      <div class="big">${det.discovered}/${det.catalogSize} catalogued regressions found without being told about them</div>
      <p class="sub" style="margin:6px 0 0">
        ${passes}/${ver.checks.length} fixes proven by re-measurement under an identical protocol.
        ${det.recall != null ? `Recall ${(det.recall * 100).toFixed(0)}%.` : ''}
        ${severityCount.critical ? `${severityCount.critical} critical, ` : ''}${severityCount.high || 0} high severity findings.
      </p>
    </div>
    <div class="panel">
      <h4>Telemetry</h4>
      <div class="mono-sm">
        ${esc(totals.toolCalls)} tool calls<br>
        ${kb(totals.mcpResponseBytes)} of responses<br>
        ~${totalTokens.toLocaleString()} tokens in<br>
        ${esc(Math.round((totals.mcpTimeMs || 0) / 1000))}s inside MCP
      </div>
    </div>
  </div>

  <h2>Before / after — same protocol, same throttling</h2>
  <div class="grid g4">
    ${kpi('LCP (ms)', b.lcp, f.lcp)}
    ${kpi('CLS', b.cls, f.cls, { digits: 3 })}
    ${kpi('Transfer size', b.transferBytes, f.transferBytes, { unit: ' B' })}
    ${kpi('DOM nodes', b.domNodes, f.domNodes)}
    ${kpi('Console errors', b.consoleErrors, f.consoleErrors)}
    ${kpi('Heap growth (kB)', heap.broken?.sizeDeltaBytes, heap.fixed?.sizeDeltaBytes)}
    ${kpi('Lighthouse a11y', lh.broken?.scores?.accessibility, lh.fixed?.scores?.accessibility, { invert: true, unit: '/100' })}
    ${kpi('Lighthouse SEO', lh.broken?.scores?.seo, lh.fixed?.scores?.seo, { invert: true, unit: '/100' })}
    ${kpi('Long tasks / 5 clicks', b.interaction?.count, f.interaction?.count)}
    ${kpi('Agentic Browsing', lh.broken?.scores?.['agentic-browsing'], lh.fixed?.scores?.['agentic-browsing'], { invert: true, unit: '/100' })}
  </div>
  <p class="cap">Throttling: ${esc(b.throttle || 'n/a')} · LCP element (broken): <code>${esc(b.lcpElement?.tag || 'n/a')}.${esc(b.lcpElement?.cls || '')}</code> ${b.lcpElement?.size ? `(${b.lcpElement.size} px²)` : ''}
    · LCP element (fixed): <code>${esc(f.lcpElement?.tag || 'n/a')}.${esc(f.lcpElement?.cls || '')}</code>
    · long tasks per 5 clicks: ${esc(b.interaction?.count ?? '—')} → ${esc(f.interaction?.count ?? '—')}
    · LoAF frames while scrolling: ${esc(b.loaf?.frames ?? 'n/a')} → ${esc(f.loaf?.frames ?? 'n/a')}</p>

  <h2>Proof table — every fix, re-measured</h2>
  <div class="panel">
    <table>
      <thead><tr><th></th><th>Check</th><th class="num">Broken</th><th class="num">Fixed</th><th class="num">Δ</th></tr></thead>
      <tbody>${checkRows}</tbody>
    </table>
  </div>

  <h2>Detection matrix — what the agent found on its own</h2>
  <div class="panel">
    <table>
      <thead><tr><th></th><th>Defect id</th><th>Description</th><th>Category</th><th>Severity</th><th>Evidence from</th></tr></thead>
      <tbody>${matrixRows}</tbody>
    </table>
  </div>

  <h2>Findings</h2>
  ${findingCards || '<p class="muted">No findings.</p>'}

  <h2>Network waterfall — broken vs fixed</h2>
  <div class="grid g2">
    <div class="panel"><h4>Broken build (${esc(b.requestCount)} requests, ${esc(i2b)})</h4>${waterfall(summary.network?.waterfall?.broken)}</div>
    <div class="panel"><h4>Fixed build (${esc(f.requestCount)} requests, ${esc(kb(f.transferBytes))})</h4>${waterfall(summary.network?.waterfall?.fixed)}</div>
  </div>
  <p class="cap">Red = render-blocking. Amber = image. Violet = cross-origin. Green = script/css.</p>

  <h2>Memory — the leak, named</h2>
  <div class="grid g2">
    <div class="panel">
      <h4>Class growth over 10 interactions</h4>
      <table><thead><tr><th>Class</th><th class="num">Δ objects</th><th class="num">Δ size</th></tr></thead>
      <tbody>${heapRows || '<tr><td colspan="3" class="muted">no growth detected</td></tr>'}</tbody></table>
      <p class="cap">Fixed build: FareCacheEntry Δ <strong>${esc(heap.fixed?.fareDelta ?? '—')}</strong> · overall ${esc(kb(heap.fixed?.sizeDeltaBytes))}</p>
    </div>
    <div class="panel">
      <h4>Retaining path</h4>
      <pre style="background:#070d18;border:1px solid var(--line);border-radius:8px;padding:10px;font-size:11px;max-height:260px;overflow:auto;white-space:pre-wrap">${esc(heap.broken?.retaining || 'not captured')}</pre>
    </div>
  </div>

  <h2>Console forensics</h2>
  <div class="panel"><ul class="console">${consoleRows || '<li class="muted">no console errors</li>'}</ul></div>

  <h2>Evidence</h2>
  <div class="grid g2">
    ${screens['broken-desktop'] ? `<div><img class="shot" src="${screens['broken-desktop']}" alt="broken build, desktop"><p class="cap">Broken build — 1280×800</p></div>` : ''}
    ${screens['fixed-desktop'] ? `<div><img class="shot" src="${screens['fixed-desktop']}" alt="fixed build, desktop"><p class="cap">Fixed build — 1280×800</p></div>` : ''}
  </div>
  ${screens['broken-mobile'] ? `<div style="max-width:390px;margin-top:16px"><img class="shot" src="${screens['broken-mobile']}" alt="broken build, mobile"><p class="cap">Broken build at 390×844 — no viewport tag, so the layout is scaled down</p></div>` : ''}

  <h2>MCP telemetry — slowest tools</h2>
  <div class="panel">
    <table><thead><tr><th>Tool</th><th>Arguments</th><th class="num">Latency</th><th class="num">Response</th><th class="num">Context</th></tr></thead>
    <tbody>${toolTable}</tbody></table>
  </div>

  <h2>Honest limits</h2>
  ${(summary.degraded || []).length
    ? (summary.degraded || []).map((d) => `<div class="degraded"><strong>${esc(d.tool)}</strong> — ${esc(d.error)}</div>`).join('')
    : '<p class="muted">No degradations: every tool the mission needed was available and behaved.</p>'}
  ${(summary.inconclusive || []).length
    ? (summary.inconclusive || []).map((x) => `<div class="degraded"><strong>Inconclusive — ${esc(x.id)}</strong><br>${esc(x.why)}</div>`).join('')
    : ''}
  ${(det.missed || []).length
    ? `<div class="degraded"><strong>Not observed in this environment:</strong> ${esc(det.missed.map((x) => x.id).join(', '))}. These defects exist in the source but produced no external signal in a snapshot-mode Lighthouse + headless trace. They are reported rather than hidden.</div>`
    : ''}

  <h2>How this run was produced</h2>
  <div class="panel mono-sm">
    server: <code>${esc(summary.attestation?.launch)}</code><br>
    client: <code>${esc(summary.attestation?.client)}</code> · protocol <code>${esc(summary.attestation?.protocolVersion)}</code> · ${esc(summary.attestation?.toolsDiscovered)} tools discovered<br>
    artifacts: <code>wire.jsonl</code> (every JSON-RPC frame), <code>summary.json</code>, <code>findings.json</code>, <code>trace/*.json.gz</code>, <code>heap/*.heapsnapshot</code>
  </div>

  <div class="foot">
    BLACKBOX · generated ${new Date().toISOString()} · target ${esc(summary.target?.broken)} · this document is self-contained (no external requests).
  </div>
</div>
</body>
</html>`;
}
