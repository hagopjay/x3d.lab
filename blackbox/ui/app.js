/* Mission Control — vanilla JS, driven entirely by the server's SSE stream. */
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const kb = (b) => (b == null ? '—' : b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.round(b / 1024) + ' kB');
  const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? '—' : typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(d)) : v);

  const state = {
    runId: null,
    running: false,
    startedAt: null,
    timer: null,
    phases: new Map(),
    tools: new Map(),      // name -> {calls, ms, bytes, tokens, errors}
    wireRows: [],
    findings: [],
    metrics: { broken: null, fixed: null },
    summary: null,
    logCount: 0,
    gpuPreflight: null,
    gpuWireRows: [],
    gpuRunning: false,
    lastGpuSummary: null,
  };

  /* ── tabs ───────────────────────────────────────────────────────────── */
  $$('.tabs button').forEach((b) => b.addEventListener('click', () => {
    $$('.tabs button').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
    $$('.view').forEach((v) => v.setAttribute('data-active', String(v.dataset.tab === b.dataset.tab)));
  }));

  /* ── status / timer ─────────────────────────────────────────────────── */
  function setStatus(kind, text) {
    const el = $('#status');
    el.className = `status ${kind}`;
    $('#status-text').textContent = text;
    $('#run').disabled = kind === 'running';
  }
  function tick() {
    if (!state.startedAt) return;
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    $('#elapsed').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  /* ── mission view ───────────────────────────────────────────────────── */
  function upsertPhase(p) {
    state.phases.set(p.id, { ...(state.phases.get(p.id) || {}), ...p });
    const done = [...state.phases.values()].filter((x) => x.status === 'done').length;
    $('#phase-progress').textContent = `${done}/${state.phases.size}`;
    const host = $('#phases');
    host.innerHTML = [...state.phases.values()].map((ph) => `
      <div class="phase ${ph.status}">
        <span class="tick"></span>
        <span>${esc(ph.title)}</span>
        <span class="ms">${ph.ms ? ph.ms + ' ms' : ph.status === 'running' ? '…' : ''}</span>
      </div>`).join('');
  }

  function pushStream(row) {
    const host = $('#stream');
    const el = document.createElement('div');
    el.className = `mcp-row ${row.isError ? 'err' : row.pending ? '' : 'ok'}`;
    el.dataset.key = row.key;
    el.innerHTML = `<span class="spinner">${row.pending ? '◐' : row.isError ? '✖' : '✓'}</span>
      <span class="tool">${esc(row.name)}<span class="meta"> ${esc(row.args || '')}</span></span>
      <span class="meta">${row.ms != null ? row.ms + ' ms · ' + kb(row.bytes) : '…'}</span>`;
    if (row.pending) {
      const pending = host.querySelector(`[data-key="${row.key}"]`);
      if (pending) pending.replaceWith(el); else host.appendChild(el);
    } else {
      const pending = host.querySelector(`[data-key="${row.key}"]`);
      if (pending) pending.replaceWith(el); else host.appendChild(el);
    }
    host.scrollTop = host.scrollHeight;
  }

  function log(level, msg, detail) {
    const host = $('#log');
    if (state.logCount === 0) host.innerHTML = '';
    state.logCount++;
    const el = document.createElement('div');
    el.className = `log-line ${level}`;
    el.innerHTML = `<span class="t">${new Date().toISOString().slice(11, 19)}</span>
      <span>${esc(msg)}${detail ? `<span class="detail">${esc(detail)}</span>` : ''}</span>`;
    host.appendChild(el);
    host.scrollTop = host.scrollHeight;
    $('#log-count').textContent = `${state.logCount} lines`;
  }

  function renderKpis() {
    const b = state.metrics.broken || {};
    const f = state.metrics.fixed || {};
    const defs = [
      ['LCP', b.lcp, f.lcp, (v) => (v == null ? '—' : v + ' ms')],
      ['CLS', b.cls, f.cls, (v) => (v == null ? '—' : v)],
      ['Transfer', b.transferBytes, f.transferBytes, kb],
      ['DOM nodes', b.domNodes, f.domNodes, (v) => (v == null ? '—' : v.toLocaleString())],
      ['Console errors', b.consoleErrors, f.consoleErrors, fmt],
      ['a11y score', b.a11y, f.a11y, (v) => (v == null ? '—' : v + '/100')],
    ];
    $('#kpis').innerHTML = defs.map(([label, before, after, f2]) => {
      const empty = before == null && after == null;
      let delta = '';
      if (before && after) delta = `${after < before ? '▼' : '▲'} ${Math.abs(Math.round(((after - before) / before) * 100))}%`;
      return `<div class="kpi ${empty ? 'empty' : ''}">
        <div class="label">${label}</div>
        <div class="vals"><span class="before">${empty ? '—' : f2(before)}</span>
          <span class="arrow">→</span><span class="after">${empty ? '—' : f2(after)}</span>
          <span class="d">${delta}</span></div></div>`;
    }).join('');
  }

  /* ── findings ───────────────────────────────────────────────────────── */
  function renderFindings(payload) {
    const { findings = [], detection = {}, verification = null } = payload;
    state.findings = findings;
    $('#finding-count').textContent = findings.length;

    const counts = findings.reduce((a, x) => { a[x.severity] = (a[x.severity] || 0) + 1; return a; }, {});
    $('#verdict').innerHTML = `
      <div class="sev-summary">
        ${['critical', 'high', 'medium', 'low'].filter((s) => counts[s]).map((s) => `<span class="chip ${s}">${counts[s]} ${s}</span>`).join('')}
        ${detection.catalogSize ? `<span class="chip">${detection.discovered}/${detection.catalogSize} catalogued regressions</span>` : ''}
        ${detection.recall != null ? `<span class="chip">recall ${Math.round(detection.recall * 100)}%</span>` : ''}
        ${verification ? `<span class="chip">${verification.verified}/${verification.checks.length} fixes proven</span>` : ''}
      </div>
      <div class="hint">These defects were never described to the agent. It found them through the tool surface only.</div>`;
    $('#verdict-meta').textContent = detection.catalogSize ? `${detection.discovered}/${detection.catalogSize}` : '';

    $('#findings-list').innerHTML = findings.map((f) => `
      <article class="finding ${esc(f.severity)}">
        <header>
          <span class="chip ${esc(f.severity)}">${esc(f.severity)}</span>
          <span class="chip">${esc(f.category)}</span>
          <span class="chip">${esc(f.confidence)} confidence</span>
        </header>
        <h4>${esc(f.title)}</h4>
        ${f.metric ? `<div class="metric">${esc(f.metric)}</div>` : ''}
        <div class="tools">${(f.evidence || []).map((e) => `<code>${esc(e.tool)}</code>`).join('')}</div>
        <pre>${esc((f.evidence || []).map((e) => e.text).filter(Boolean).join('\n───\n').slice(0, 1600))}</pre>
        ${f.catalogFix ? `<p class="fix"><strong>Fix:</strong> ${esc(f.catalogFix)}</p>` : ''}
      </article>`).join('') || '<div class="empty-state">No findings.</div>';

    if (verification) {
      $('#proof').innerHTML = `<table class="data">
        <thead><tr><th></th><th>check</th><th>before</th><th>after</th></tr></thead>
        <tbody>${verification.checks.map((c) => {
          const u = c.unit && String(c.unit).trim() ? ' ' + esc(String(c.unit).trim()) : '';
          return `<tr>
          <td><span class="dot ${c.pass ? 'ok' : 'bad'}"></span></td>
          <td class="name">${esc(c.check)}</td>
          <td class="out">${esc(String(c.before))}${u}</td>
          <td class="in">${esc(String(c.after))}${u}</td>
        </tr>`; }).join('')}</tbody></table>`;
    }

    const missed = detection.missed || [];
    $('#limits').innerHTML = missed.length
      ? `<div class="hint">Not observed in this environment (reported, not hidden):</div>
         <ul class="hint" style="margin:6px 0 0; padding-left:18px">${missed.map((m) => `<li>${esc(m.id)} — ${esc(m.title)}</li>`).join('')}</ul>`
      : '<div class="hint">Every catalogued defect was observed.</div>';
  }

  function renderEvidence(summary) {
    const art = summary.artifacts || {};
    const shots = ['broken-desktop', 'fixed-desktop'].filter((k) => art[k]);
    if (!shots.length) return;
    $('#evidence').innerHTML = shots.map((k) => `
      <div style="margin-bottom:10px"><img class="shot" src="/${esc(art[k])}" alt="${esc(k)}">
      <div class="cap">${esc(k)} — 1280×800</div></div>`).join('');
  }

  /* ── wire view ──────────────────────────────────────────────────────── */
  function renderWire() {
    const tbody = $('#wire-table tbody');
    tbody.innerHTML = state.wireRows.slice(-600).map((r, i) => `<tr>
      <td>${i + 1}</td>
      <td class="${r.dir === 'in' ? 'in' : 'out'}">${r.dir}</td>
      <td class="name">${esc(r.method || '')}${r.notification ? ' (notify)' : ''}${r.serverRequest ? ' (server→client)' : ''}</td>
      <td>${r.ms != null ? r.ms : ''}</td>
      <td>${r.bytes != null ? r.bytes : ''}</td></tr>`).join('');
    const rows = [...state.tools.values()].sort((a, b) => b.ms - a.ms);
    $('#tool-table tbody').innerHTML = rows.map((t) => `<tr>
      <td class="name">${esc(t.name)}</td><td>${t.calls}</td><td>${t.ms}</td>
      <td>${kb(t.bytes)}</td><td>${t.tokens}</td></tr>`).join('');
  }

  /* ── optional WebGPU smoke lane ─────────────────────────────────────── */
  async function loadGpuPreflight() {
    $('#gpu-preflight').textContent = 'Checking host devices, nvidia-smi, Vulkan, and Chrome…';
    try {
      const res = await fetch('/api/gpu-preflight');
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || 'preflight failed');
      state.gpuPreflight = body;
      $('#gpu-host-status').textContent = `${body.hostGpu?.status || 'unknown'} · Vulkan ${body.hostGpu?.vulkan?.available ? 'yes' : 'no'}`;
      $('#gpu-preflight').textContent = JSON.stringify(body, null, 2);
    } catch (err) {
      $('#gpu-preflight').textContent = `Preflight error: ${err.message}`;
      $('#gpu-host-status').textContent = 'error';
    }
  }

  async function loadLatestGpuRun() {
    if (state.lastGpuSummary || state.gpuRunning) return;
    try {
      const runsRes = await fetch('/api/runs');
      const { runs = [] } = await runsRes.json();
      const latest = runs.find((run) => run.kind === 'webgpu-smoke');
      if (!latest) return;
      const summaryRes = await fetch(`/runs/${encodeURIComponent(latest.runId)}/summary.json`);
      if (!summaryRes.ok) return;
      renderGpuEnd({ ok: true, summary: await summaryRes.json() });
    } catch { /* no saved GPU run yet */ }
  }

  function renderGpuEnd(e) {
    state.gpuRunning = false;
    $('#gpu-run').disabled = false;
    $('#gpu-run-state').textContent = e.ok ? (e.summary?.verdict?.compute || 'complete') : 'failed';
    if (!e.ok || !e.summary) {
      $('#gpu-result').textContent = e.error || 'GPU smoke failed without a summary.';
      $('#gpu-progress').textContent = 'Run failed; inspect the server output or MCP wire log.';
      return;
    }
    const summary = e.summary;
    state.lastGpuSummary = summary;
    $('#gpu-result').textContent = JSON.stringify({
      runId: summary.runId,
      verdict: summary.verdict,
      hardwareAssessment: summary.hardwareAssessment,
      webgpu: summary.browserResult?.webgpu,
      webgl2: summary.browserResult?.webgl2,
      mcp: summary.mcp,
      console: summary.console,
      failure: summary.failure,
    }, null, 2);
    $('#gpu-progress').textContent = `${summary.verdict?.compute || 'unknown'} · host GPU ${summary.verdict?.hostGpu || 'unknown'} · ${summary.mcp?.toolCalls || 0} MCP tool calls`;
    $('#gpu-report-host').innerHTML = `<iframe class="report" src="/runs/${esc(summary.runId)}/report.html" title="WebGPU smoke report"></iframe>`;
    loadGpuPreflight();
  }

  $('#gpu-refresh').addEventListener('click', loadGpuPreflight);
  $('#gpu-run').addEventListener('click', async () => {
    if (state.gpuRunning) return;
    state.gpuRunning = true;
    state.gpuWireRows = [];
    $('#gpu-wire').textContent = '';
    $('#gpu-result').textContent = 'Waiting for the MCP browser session…';
    $('#gpu-progress').textContent = 'Starting host preflight, MCP handshake, WebGPU compute, console capture, and screenshot…';
    $('#gpu-run-state').textContent = 'running';
    $('#gpu-run').disabled = true;
    try {
      const res = await fetch('/api/gpu-smoke', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || 'GPU smoke request failed');
      if (!body.ok) throw new Error(body.error || 'GPU smoke did not complete');
      renderGpuEnd({ ok: true, summary: body.summary });
    } catch (err) {
      if (state.gpuRunning) renderGpuEnd({ ok: false, error: String(err.message || err) });
    }
  });

  /* ── X3D lab ────────────────────────────────────────────────────────── */
  const x3dLog = (line) => {
    const host = $('#x3d-log');
    if (host.textContent === 'No run yet.') host.textContent = '';
    host.textContent += line + '\n';
    host.scrollTop = host.scrollHeight;
  };
  async function loadX3dEnv() {
    try {
      const env = await (await fetch('/api/x3d/env')).json();
      $('#x3d-env').textContent = JSON.stringify(env, null, 2);
      $('#x3d-env-state').textContent = `X_ITE ${env.xite || '—'} · Blender leg ${env.blenderLeg ? 'on' : 'off'}`;
      const idx = await fetch('/lab/x3d/scenes/index.json').then((r) => (r.ok ? r.json() : null)).catch(() => null);
      $('#x3d-scenes').innerHTML = idx
        ? idx.scenes.map((sc) => `<div><a style="color:var(--amber)" href="/lab/x3d/?scene=${esc(sc.id)}" target="_blank" rel="noopener">${esc(sc.title)}</a> · ${sc.stats.nodes} nodes · ${sc.fidelity.length} fidelity rules${sc.blender ? (sc.blender.ok ? ' · glb ✓' : ' · <span style="color:var(--red)">Blender import failed</span>') : ''}</div>`).join('')
        : 'Not built yet: press Build scenes.';
      if (!state.x3dShown) {
        const { runs = [] } = await (await fetch('/api/runs')).json();
        const latest = runs.filter((r) => String(r.runId || r.id || '').startsWith('x3d-'))
          .sort((a, b) => String(b.runId || b.id).localeCompare(String(a.runId || a.id)))[0];
        const sum = latest && await fetch(`/runs/${encodeURIComponent(latest.runId || latest.id)}/summary.json`).then((r) => (r.ok ? r.json() : null));
        if (sum) handleX3d({ type: 'x3d-end', kind: 'mission', ok: true, summary: sum });
      }
    } catch (err) {
      $('#x3d-env').textContent = 'env error: ' + err.message;
    }
  }
  function handleX3d(e) {
    if (e.type === 'x3d-start') {
      $('#x3d-log').textContent = '';
      $('#x3d-wire').textContent = '';
      $('#x3d-run-state').textContent = e.kind === 'build' ? 'building' : 'running';
      x3dLog(`▶ ${e.kind} ${e.runId}`);
    } else if (e.type === 'x3d-phase') {
      x3dLog(`\n▶ ${e.phase}`);
      $('#x3d-progress').textContent = 'phase: ' + e.phase;
    } else if (e.type === 'x3d-log') {
      x3dLog(e.line);
    } else if (e.type === 'x3d-wire' && e.dir === 'in' && !e.serverRequest) {
      const host = $('#x3d-wire');
      if (host.textContent === 'No calls yet.') host.textContent = '';
      host.textContent += `${e.server.padEnd(7)} ${String(e.method || '').padEnd(16)} ${String(e.ms ?? '').padStart(6)} ms ${e.ok ? '' : 'ERR'}\n`;
      host.scrollTop = host.scrollHeight;
    } else if (e.type === 'x3d-end') {
      $('#x3d-build').disabled = false;
      $('#x3d-run').disabled = false;
      $('#x3d-run-state').textContent = e.ok ? 'done' : 'failed';
      if (!e.ok) { x3dLog('FAILED: ' + e.error); $('#x3d-progress').textContent = e.error; }
      if (e.kind === 'build') loadX3dEnv();
      if (e.ok && e.summary) {
        state.x3dShown = true;
        $('#x3d-progress').textContent = e.summary.verdict;
        $('#x3d-report-host').innerHTML = `<iframe class="report" src="/runs/${esc(e.summary.runId)}/report.html" title="X3D Lab mission report"></iframe>`;
      }
    }
  }
  $('#x3d-build').addEventListener('click', async () => {
    $('#x3d-build').disabled = true;
    const r = await fetch('/api/x3d/build', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    if (!r.ok) { x3dLog('build: ' + ((await r.json()).error || r.status)); $('#x3d-build').disabled = false; }
  });
  $('#x3d-run').addEventListener('click', async () => {
    $('#x3d-run').disabled = true;
    const r = await fetch('/api/x3d/mission', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    if (!r.ok) { x3dLog('mission: ' + ((await r.json()).error || r.status)); $('#x3d-run').disabled = false; }
  });
  document.querySelector('[role=tab][data-tab="x3d"]')?.addEventListener('click', loadX3dEnv);

  /* ── SSE ────────────────────────────────────────────────────────────── */
  function connect() {
    // ?static=1 renders from the last run's artifacts without opening SSE
    // (used for docs screenshots and for embedding in a static page).
    if (new URLSearchParams(location.search).has('static')) return;
    const es = new EventSource('/events');
    es.onmessage = (ev) => handle(JSON.parse(ev.data));
    es.onerror = () => { /* EventSource retries on its own */ };
  }

  function handle(e) {
    if (typeof e.type === 'string' && e.type.startsWith('x3d-')) return handleX3d(e);
    switch (e.type) {
      case 'gpu-smoke-start':
        state.gpuRunning = true;
        state.gpuWireRows = [];
        $('#gpu-run').disabled = true;
        $('#gpu-run-state').textContent = 'running';
        $('#gpu-progress').textContent = `Run ${e.runId} · target ${e.target}`;
        $('#gpu-wire').textContent = '';
        break;

      case 'gpu-smoke': {
        const line = `${e.stage || 'event'} · ${e.message || ''}`;
        const host = $('#gpu-wire');
        host.textContent = (host.textContent === 'No calls yet.' ? '' : host.textContent) + `${line}\n`;
        host.scrollTop = host.scrollHeight;
        break;
      }

      case 'gpu-wire': {
        const row = e;
        state.gpuWireRows.push(row);
        const host = $('#gpu-wire');
        const line = `${row.dir || '?'} ${row.method || 'notification'}${row.ms != null ? ` · ${row.ms} ms` : ''}${row.bytes != null ? ` · ${row.bytes} B` : ''}`;
        host.textContent = (host.textContent === 'No calls yet.' ? '' : host.textContent) + `${line}\n`;
        host.scrollTop = host.scrollHeight;
        break;
      }

      case 'gpu-smoke-end':
        renderGpuEnd(e);
        break;

      case 'run-start':
        state.runId = e.runId;
        state.running = true;
        state.startedAt = Date.now();
        state.phases.clear();
        state.tools.clear();
        state.wireRows = [];
        state.logCount = 0;
        state.metrics = { broken: null, fixed: null };
        $('#stream').innerHTML = '';
        $('#phases').innerHTML = '';
        $('#log').innerHTML = '';
        $('#findings-list').innerHTML = '';
        $('#verdict').className = 'empty-state';
        $('#verdict').textContent = 'Investigating…';
        $('#finding-count').textContent = '0';
        $('#wire-count').textContent = '';
        clearInterval(state.timer);
        state.timer = setInterval(tick, 250);
        setStatus('running', 'running');
        log('info', `mission ${e.runId} started against ${e.target}`);
        break;

      case 'phase': upsertPhase(e); break;

      case 'log': log(e.level === 'ok' ? 'ok' : e.level === 'warn' ? 'warn' : e.level === 'error' ? 'error' : '', e.msg, e.detail); break;

      case 'mcp-call':
        pushStream({ key: e.name + ':' + e.startedAt, name: e.name, args: e.args, pending: true });
        break;

      case 'mcp-result': {
        const t = state.tools.get(e.name) || { name: e.name, calls: 0, ms: 0, bytes: 0, tokens: 0 };
        t.calls++; t.ms += e.ms; t.bytes += e.bytes || 0; t.tokens += e.tokens || 0;
        state.tools.set(e.name, t);
        const host = $('#stream');
        const key = e.name + ':' + e.startedAt;
        const el = host.querySelector(`[data-key="${key}"]`);
        if (el) {
          el.className = `mcp-row ${e.isError ? 'err' : 'ok'}`;
          el.querySelector('.spinner').textContent = e.isError ? '✖' : '✓';
          el.querySelector('.meta:last-child').textContent = `${e.ms} ms · ${kb(e.bytes)}`;
        } else {
          pushStream({ key, name: e.name, args: '', ms: e.ms, bytes: e.bytes, isError: e.isError });
        }
        const total = [...state.tools.values()].reduce((a, x) => ({ calls: a.calls + x.calls, bytes: a.bytes + x.bytes }), { calls: 0, bytes: 0 });
        $('#stream-stats').textContent = `${total.calls} calls · ${kb(total.bytes)}`;
        break;
      }

      case 'mcp-error':
        pushStream({ key: e.name + ':' + Date.now(), name: e.name, args: e.error, ms: e.ms, isError: true });
        break;

      case 'wire':
        state.wireRows.push(e);
        $('#wire-count').textContent = `${state.wireRows.length} frames`;
        if (document.querySelector('.view[data-tab="wire"]').dataset.active === 'true' && state.wireRows.length % 4 === 0) renderWire();
        break;

      case 'metrics': {
        state.metrics[e.variant] = { ...(state.metrics[e.variant] || {}), ...e.metrics };
        renderKpis();
        break;
      }

      case 'attestation':
        $('#attestation').innerHTML = `
          server <strong>${esc(e.attestation.server?.name)}</strong> v${esc(e.attestation.server?.version)}<br>
          protocol <code>${esc(e.attestation.protocolVersion)}</code><br>
          ${esc(e.attestation.toolsDiscovered)} tools discovered<br>
          <span style="color:var(--faint)">${esc(e.attestation.launch)}</span>`;
        break;

      case 'findings':
        renderFindings(e);
        break;

      case 'lighthouse': {
        if (e.scores?.accessibility != null) {
          state.metrics[e.variant] = { ...(state.metrics[e.variant] || {}), a11y: e.scores.accessibility, seo: e.scores.seo };
          renderKpis();
        }
        log('info', `lighthouse ${e.variant}: a11y ${e.scores?.accessibility ?? '?'} · seo ${e.scores?.seo ?? '?'} · ${e.failed?.length ?? 0} failed audits`);
        break;
      }

      case 'run-end': {
        state.running = false;
        clearInterval(state.timer);
        if (e.ok && e.summary) {
          state.summary = e.summary;
          state.metrics.broken = { ...state.metrics.broken, ...e.summary.metrics?.broken };
          state.metrics.fixed = { ...state.metrics.fixed, ...e.summary.metrics?.fixed };
          renderKpis();
          renderEvidence(e.summary);
          setStatus('done', 'complete');
          log('ok', `mission complete in ${Math.round(e.summary.durationMs / 1000)}s · ${e.summary.findings.length} findings · ${e.summary.detection.discovered}/${e.summary.detection.catalogSize} catalogued`);
          $('#report-host').innerHTML = `<iframe class="report" src="/runs/${esc(e.summary.runId)}/report.html" title="flight recorder"></iframe>`;
          document.querySelector('.tabs button[data-tab="findings"]').click();
        } else {
          setStatus('error', 'failed');
          log('error', `mission failed: ${e.error}`);
        }
        break;
      }

      case 'insight':
        log('info', `insight ${e.name}: ${(e.title || '').slice(0, 80)}${e.savings ? ' · ' + e.savings : ''}`);
        break;

      case 'heap':
        log('info', `heap ${e.variant}: Δ ${kb(e.sizeDeltaBytes)} / ${e.objectDelta} objects${e.fareDelta != null ? ` · FareCacheEntry ${e.fareDelta > 0 ? '+' : ''}${e.fareDelta}` : ''}`);
        break;

      case 'console':
        if (e.messages?.length) log('warn', `console ${e.variant}: ${e.messages.length} message(s)`);
        break;

      default:
        break;
    }
  }

  /* ── run button ─────────────────────────────────────────────────────── */
  $('#run').addEventListener('click', async () => {
    $('#run').disabled = true;
    try {
      const res = await fetch('/api/mission', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      const body = await res.json();
      if (!res.ok) {
        setStatus('error', body.error || 'busy');
        setTimeout(() => setStatus('idle', 'idle'), 4000);
      }
    } catch (err) {
      setStatus('error', 'request failed');
    }
  });

  /* ── explorer ───────────────────────────────────────────────────────── */
  const presets = [
    { label: 'take_snapshot', tool: 'take_snapshot', args: {} },
    { label: 'evaluate_script — DOM size', tool: 'evaluate_script', args: { function: '() => document.querySelectorAll("*").length' } },
    { label: 'list_console_messages (errors + stacks)', tool: 'list_console_messages', args: { types: ['error'], includeStackTraces: true } },
    { label: 'list_network_requests', tool: 'list_network_requests', args: {} },
    { label: 'performance_start_trace + stop', tool: 'performance_start_trace', args: { reload: true, autoStop: true } },
    { label: 'navigate_page (broken build)', tool: 'navigate_page', args: { type: 'url', url: '/lab/broken/' } },
    { label: 'emulate Slow 4G + 4x CPU', tool: 'emulate', args: { networkConditions: 'Slow 4G', cpuThrottlingRate: 4 } },
    { label: 'lighthouse_audit', tool: 'lighthouse_audit', args: { mode: 'snapshot', device: 'mobile' } },
    { label: 'take_heapsnapshot', tool: 'take_heapsnapshot', args: { filePath: '/tmp/explorer.heapsnapshot' } },
  ];
  let toolsLoaded = false;
  let selectedTool = null;

  async function loadTools() {
    if (toolsLoaded) return;
    try {
      const res = await fetch('/api/explorer/tools');
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || 'failed');
      toolsLoaded = true;
      $('#tool-count').textContent = `${body.tools.length} tools`;
      $('#explorer-page').textContent = `page ${body.pageId} · ${body.server?.name} v${body.server?.version}`;
      $('#tools').innerHTML = body.tools.map((t) => `
        <div class="tool-item" data-tool="${esc(t.name)}" role="option" aria-selected="false">
          ${esc(t.name)}<span class="cat">${esc(t.name.split('_')[0])}</span></div>`).join('');
      $$('.tool-list .tool-item').forEach((el) => el.addEventListener('click', () => pickTool(el.dataset.tool)));
      $('#preset').innerHTML = '<option value="">— load a scenario —</option>' +
        presets.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join('');
      window.__tools = new Map(body.tools.map((t) => [t.name, t]));
    } catch (err) {
      $('#tools').innerHTML = `<div class="hint">could not start MCP session: ${esc(err.message)}</div>`;
      toolsLoaded = false;
    }
  }

  function pickTool(name) {
    selectedTool = name;
    $$('.tool-list .tool-item').forEach((el) => el.setAttribute('aria-selected', String(el.dataset.tool === name)));
    const tool = window.__tools?.get(name);
    $('#tool-doc').textContent = tool ? tool.description || '' : '';
    const props = tool?.schema?.properties || {};
    const skeleton = {};
    for (const [k, v] of Object.entries(props)) {
      if (k === 'pageId') continue;
      skeleton[k] = v.default ?? (v.type === 'boolean' ? false : v.type === 'number' ? 0 : v.type === 'array' ? [] : '');
    }
    $('#args').value = JSON.stringify(skeleton, null, 2);
  }

  $('#preset').addEventListener('change', (e) => {
    const p = presets[Number(e.target.value)];
    if (!p) return;
    pickTool(p.tool);
    $('#args').value = JSON.stringify(p.args, null, 2);
  });

  $('#invoke').addEventListener('click', async () => {
    if (!selectedTool) return;
    let args;
    try { args = JSON.parse($('#args').value || '{}'); } catch { $('#resp').textContent = 'args are not valid JSON'; return; }
    $('#invoke').disabled = true;
    $('#resp-meta').textContent = 'calling…';
    try {
      const res = await fetch('/api/explorer/tool', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ tool: selectedTool, args }),
      });
      const body = await res.json();
      $('#resp-meta').textContent = `${body.ms ?? '?'} ms · ${kb(body.bytes)} · ~${body.tokens ?? '?'} tok`;
      $('#resp').innerHTML = (body.image ? `<img src="${body.image}" alt="tool image output">` : '') +
        `<pre style="margin:0;white-space:pre-wrap">${esc(body.text || body.error || '')}</pre>`;
    } catch (err) {
      $('#resp').textContent = String(err.message || err);
    } finally {
      $('#invoke').disabled = false;
    }
  });

  // allow deep links for screenshots: /?static=1&tab=findings
  const wanted = new URLSearchParams(location.search).get('tab');
  if (wanted) setTimeout(() => document.querySelector(`.tabs button[data-tab="${wanted}"]`)?.click(), 400);

  $$('.tabs button').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.tab === 'explorer') loadTools();
    if (b.dataset.tab === 'wire') renderWire();
    if (b.dataset.tab === 'gpu') {
      if (!state.gpuPreflight) loadGpuPreflight();
      if (!state.lastGpuSummary) loadLatestGpuRun();
    }
  }));

  /* ── boot: replay the most recent run so the UI is never empty ──────── */
  (async function boot() {
    connect();
    try {
      const res = await fetch('/api/runs');
      const { runs } = await res.json();
      const latest = runs?.find((run) => run.kind !== 'webgpu-smoke');
      if (!latest) return;
      const summaryRes = await fetch(`/runs/${latest.runId}/summary.json`);
      if (!summaryRes.ok) return;
      const summary = await summaryRes.json();
      state.metrics = { broken: summary.metrics?.broken, fixed: summary.metrics?.fixed };
      renderKpis();
      const findingsRes = await fetch(`/runs/${latest.runId}/findings.json`);
      if (findingsRes.ok) {
        const f = await findingsRes.json();
        renderFindings({ findings: f.findings, detection: f.detection, verification: f.verification });
      }
      // Replay the finished run into the mission view so the screen is never blank.
      (summary.phases || []).forEach((ph) => upsertPhase({ ...ph, status: ph.status === 'running' ? 'done' : ph.status }));
      const stream = $('#stream');
      stream.innerHTML = '';
      (summary.steps || []).forEach((st, i) => {
        const el = document.createElement('div');
        el.className = `mcp-row ${st.isError ? 'err' : 'ok'}`;
        el.innerHTML = `<span class="spinner">${st.isError ? '✖' : '✓'}</span>
          <span class="tool">${esc(st.tool)}<span class="meta"> ${esc(String(st.args || '').slice(0, 70))}</span></span>
          <span class="meta">${st.ms} ms · ${kb(st.bytes)}</span>`;
        stream.appendChild(el);
      });
      const lt = summary.lighthouse || {};
      state.metrics.broken = { ...state.metrics.broken, a11y: lt.broken?.scores?.accessibility, seo: lt.broken?.scores?.seo };
      state.metrics.fixed = { ...state.metrics.fixed, a11y: lt.fixed?.scores?.accessibility, seo: lt.fixed?.scores?.seo };
      renderKpis();
      renderEvidence(summary);
      $('#attestation').innerHTML = `previous run <code>${esc(summary.runId)}</code><br>${esc(summary.attestation?.toolsDiscovered)} tools · MCP ${esc(summary.attestation?.protocolVersion)}`;
      $('#report-host').innerHTML = `<iframe class="report" src="/runs/${esc(summary.runId)}/report.html" title="flight recorder"></iframe>`;
      log('info', `loaded previous run ${summary.runId} (${Math.round(summary.durationMs / 1000)}s, ${summary.findings.length} findings)`);
      setStatus('done', 'previous run loaded');
    } catch { /* first boot, no runs yet */ }
  })();
})();
