/**
 * server.mjs — three things on one port:
 *
 *  1. The target under test:  /lab/broken/  and  /lab/fixed/
 *     (plus a deliberate cross-origin "third party" at /thirdparty/*)
 *  2. The BLACKBOX control plane: POST /api/mission, GET /events (SSE), run artifacts
 *  3. The Mission Control UI at /
 *
 * Node built-ins only. No framework, no build step.
 */
import { createServer } from 'node:http';
import { readFile, stat, readdir } from 'node:fs/promises';
import { createReadStream, existsSync } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

import { runMission } from './agent/mission.mjs';
import { runWebGpuSmoke } from './agent/webgpu-smoke.mjs';
import { collectGpuPreflight } from './agent/gpu-preflight.mjs';
import { probeTool, listTools } from './agent/explorer.mjs';
import { runX3dMission, x3dEnvironment, runPython, buildX3dScenes } from './agent/x3d-mission.mjs';

const ROOT = resolve(import.meta.dirname);
const PORT = Number(process.env.PORT || 8080);
const HOST = '0.0.0.0';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.jsonl': 'application/x-ndjson; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.map': 'application/json',
  '.gz': 'application/gzip',
  '.heapsnapshot': 'application/json',
  '.mp4': 'video/mp4',
  '.md': 'text/markdown; charset=utf-8',
  '.x3d': 'model/x3d+xml',
  '.x3dv': 'model/x3d-vrml',
  '.x3dj': 'model/x3d+json',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.woff': 'font/woff',
  '.wasm': 'application/wasm',
  '.xml': 'application/xml',
};
const X_ITE_DIST = join(resolve(import.meta.dirname), 'node_modules', 'x_ite', 'dist');

// ── live event bus (SSE) ────────────────────────────────────────────────────
const clients = new Set();
const history = [];

function broadcast(event) {
  const payload = { ...event, seq: history.length, t: Date.now() };
  history.push(payload);
  if (history.length > 6000) history.shift();
  const frame = `data: ${JSON.stringify(payload)}\n\n`;
  for (const res of clients) {
    try {
      res.write(frame);
    } catch {
      clients.delete(res);
    }
  }
}

let activeRun = null;

// ── helpers ─────────────────────────────────────────────────────────────────
function json(res, code, body) {
  const s = JSON.stringify(body);
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(s);
}

async function serveFile(res, filePath, { cache } = {}) {
  try {
    const st = await stat(filePath);
    if (!st.isFile()) throw new Error('not a file');
    const type = MIME[extname(filePath)] || 'application/octet-stream';
    const headers = { 'content-type': type, 'content-length': st.size };
    // Cache-policy asymmetry is deliberate: the broken build ships no cache
    // metadata at all (a finding), the fixed build ships immutable assets.
    if (cache === 'immutable') headers['cache-control'] = 'public, max-age=31536000, immutable';
    if (cache === 'none') headers['cache-control'] = 'no-store, must-revalidate';
    res.writeHead(200, headers);
    createReadStream(filePath).pipe(res);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
  }
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    return {};
  }
}

function altOrigin(req) {
  // The target page is served on 127.0.0.1; assets on localhost are a genuinely
  // different origin, which is how we get a real third-party without the internet.
  const host = req.headers.host || `127.0.0.1:${PORT}`;
  const port = host.split(':')[1] || String(PORT);
  return `http://localhost:${port}`;
}

async function serveLab(req, res, variant) {
  const file = join(ROOT, 'site', variant, 'index.html');
  let html = await readFile(file, 'utf8');
  html = html.replaceAll('__ALT_ORIGIN__', altOrigin(req));
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
  res.end(html);
}

async function listRuns() {
  const dir = join(ROOT, 'runs');
  if (!existsSync(dir)) return [];
  const entries = await readdir(dir, { withFileTypes: true });
  const runs = [];
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    try {
      const summary = JSON.parse(await readFile(join(dir, e.name, 'summary.json'), 'utf8'));
      runs.push(summary);
    } catch {}
  }
  return runs.sort((a, b) => b.startedAt - a.startedAt);
}

// ── routes ──────────────────────────────────────────────────────────────────
const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const path = decodeURIComponent(url.pathname);

  // CORS for the SSE/API surface (the UI is same-origin, but keep it usable from curl)
  res.setHeader('access-control-allow-origin', '*');
  res.setHeader('access-control-allow-headers', 'content-type');
  if (req.method === 'OPTIONS') return res.writeHead(204).end();

  try {
    // ── app shell + UI
    if (path === '/' || path === '/index.html') return serveFile(res, join(ROOT, 'ui', 'index.html'), { cache: 'none' });
    if (path.startsWith('/ui/')) return serveFile(res, join(ROOT, 'ui', path.slice(4)), { cache: 'none' });
    if (path.startsWith('/docs/')) return serveFile(res, join(ROOT, 'docs', path.slice(6)), { cache: 'none' });

    // ── target under test
    if (path === '/lab' || path === '/lab/') return res.writeHead(302, { location: '/lab/broken/' }).end();
    if (path === '/lab/webgpu' || path === '/lab/webgpu/') {
      return serveFile(res, join(ROOT, 'site', 'webgpu', 'index.html'), { cache: 'none' });
    }
    if (path.startsWith('/lab/webgpu/')) {
      return serveFile(res, join(ROOT, 'site', 'webgpu', path.slice('/lab/webgpu/'.length)), { cache: 'none' });
    }
    // ── X3D Lab: the X_ITE dev console, its scenes, and X_ITE itself (vendored from npm, served locally)
    if (path === '/lab/x3d' || path === '/lab/x3d/') {
      return serveFile(res, join(ROOT, 'site', 'x3d', 'index.html'), { cache: 'none' });
    }
    if (path.startsWith('/lab/x3d/vendor/x_ite/')) {
      const rel = normalize(path.slice('/lab/x3d/vendor/x_ite/'.length));
      if (rel.startsWith('..')) return json(res, 400, { error: 'bad path' });
      return serveFile(res, join(X_ITE_DIST, rel), { cache: 'immutable' });
    }
    if (path.startsWith('/lab/x3d/')) {
      const rel = normalize(path.slice('/lab/x3d/'.length));
      if (rel.startsWith('..')) return json(res, 400, { error: 'bad path' });
      return serveFile(res, join(ROOT, 'site', 'x3d', rel), { cache: 'none' });
    }
    if (path === '/lab/broken/' || path === '/lab/broken' || path === '/lab/fixed/' || path === '/lab/fixed') {
      return serveLab(req, res, path.includes('fixed') ? 'fixed' : 'broken');
    }
    if (path.startsWith('/lab/broken/')) {
      return serveFile(res, join(ROOT, 'site', path.slice(1)), { cache: 'none' });
    }
    if (path.startsWith('/lab/fixed/')) {
      return serveFile(res, join(ROOT, 'site', path.slice(1)), { cache: 'immutable' });
    }
    if (path.startsWith('/site/broken/')) {
      const ext = extname(path);
      return serveFile(res, join(ROOT, path), { cache: ext === '.js' || ext === '.css' ? 'none' : undefined });
    }
    if (path.startsWith('/site/fixed/')) {
      return serveFile(res, join(ROOT, path), { cache: 'immutable' });
    }
    if (path.startsWith('/shared/')) {
      // hero-legacy.png is the bloated original; everything else is fine to cache.
      return serveFile(res, join(ROOT, 'site', path), { cache: path.includes('legacy') ? 'none' : 'immutable' });
    }
    // The "third party": same file, different origin, deliberately slow to cache.
    if (path === '/thirdparty/analytics.js') {
      return serveFile(res, join(ROOT, 'site', 'broken', 'vendor', 'analytics.js'), { cache: 'none' });
    }

    // ── app APIs the target calls
    if (path === '/api/health') return json(res, 200, { ok: true, uptime: process.uptime(), activeRun: activeRun?.id ?? null });
    if (path === '/api/fares') {
      const delay = Math.min(Number(url.searchParams.get('delay') || 0), 5000);
      await new Promise((r) => setTimeout(r, delay));
      return json(res, 200, {
        fares: [
          { from: 'ATL', to: 'LHR', price: 612, badge: 'Nonstop', departs: '21:40', duration: '8h 05m' },
          { from: 'ATL', to: 'CDG', price: 548, badge: '1 stop', departs: '19:10', duration: '10h 25m' },
          { from: 'ATL', to: 'AMS', price: 587, badge: 'Nonstop', departs: '22:55', duration: '8h 40m' },
        ],
      });
    }
    if (path === '/api/flaky') {
      // Chaos endpoint: 500s exactly as a flaky pricing service would.
      res.writeHead(500, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ error: 'pricing upstream unavailable', traceId: randomUUID() }));
    }

    // ── control plane
    if (path === '/events') {
      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-store',
        connection: 'keep-alive',
        'x-accel-buffering': 'no',
      });
      res.write(`retry: 2000\n\n`);
      for (const e of history) res.write(`data: ${JSON.stringify(e)}\n\n`);
      clients.add(res);
      const ping = setInterval(() => {
        try { res.write(': ping\n\n'); } catch { clearInterval(ping); }
      }, 15000);
      req.on('close', () => { clearInterval(ping); clients.delete(res); });
      return;
    }

    if (path === '/api/gpu-preflight' && req.method === 'GET') {
      return json(res, 200, collectGpuPreflight());
    }

    if (path === '/api/gpu-smoke' && req.method === 'POST') {
      if (activeRun) return json(res, 409, { error: 'a browser mission is already running', id: activeRun.id });
      const runId = 'gpu-' + new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14) + '-' + Math.random().toString(36).slice(2, 6);
      const origin = `http://127.0.0.1:${PORT}`;
      activeRun = { id: runId, startedAt: Date.now(), type: 'gpu-smoke' };
      broadcast({ type: 'gpu-smoke-start', runId, target: origin + '/lab/webgpu/' });
      try {
        const summary = await runWebGpuSmoke({ runId, origin, outDir: join(ROOT, 'runs', runId), emit: broadcast });
        broadcast({ type: 'gpu-smoke-end', runId, ok: true, summary });
        return json(res, 200, { ok: true, summary });
      } catch (err) {
        const error = String(err?.message || err);
        broadcast({ type: 'gpu-smoke-end', runId, ok: false, error });
        return json(res, 500, { ok: false, runId, error });
      } finally {
        activeRun = null;
      }
    }

    // ── X3D Lab control plane
    if (path === '/api/x3d/env' && req.method === 'GET') {
      return json(res, 200, await x3dEnvironment());
    }
    if (path === '/api/x3d/validate' && req.method === 'POST') {
      const body = await readBody(req);
      if (typeof body.text !== 'string' || body.text.length > 20_000_000) return json(res, 400, { error: 'text (X3D XML) required' });
      const name = String(body.name || 'upload.x3d').replace(/[^\w.\-]/g, '_').slice(0, 80);
      const out = await runPython(['-m', 'x3dlab', 'analyze', '-', '--name', name], { input: body.text });
      if (out.code !== 0) return json(res, 500, { error: out.stderr.slice(-800) });
      return json(res, 200, JSON.parse(out.stdout));
    }
    if (path === '/api/x3d/build' && req.method === 'POST') {
      if (activeRun) return json(res, 409, { error: 'a browser mission is already running', id: activeRun.id });
      const body = await readBody(req);
      activeRun = { id: 'x3d-build', startedAt: Date.now(), type: 'x3d-build' };
      broadcast({ type: 'x3d-start', runId: 'build', kind: 'build' });
      try {
        const index = await buildX3dScenes({ blender: body.blender !== false, emit: broadcast });
        broadcast({ type: 'x3d-end', runId: 'build', ok: true, kind: 'build', scenes: index.scenes.length, blender: index.blender });
        return json(res, 200, { ok: true, scenes: index.scenes.length, blender: index.blender });
      } catch (err) {
        broadcast({ type: 'x3d-end', runId: 'build', ok: false, kind: 'build', error: String(err?.message || err) });
        return json(res, 500, { ok: false, error: String(err?.message || err) });
      } finally {
        activeRun = null;
      }
    }
    if (path === '/api/x3d/mission' && req.method === 'POST') {
      if (activeRun) return json(res, 409, { error: 'a browser mission is already running', id: activeRun.id });
      const body = await readBody(req);
      const runId = 'x3d-' + new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14) + '-' + Math.random().toString(36).slice(2, 6);
      const origin = `http://127.0.0.1:${PORT}`;
      activeRun = { id: runId, startedAt: Date.now(), type: 'x3d-mission' };
      broadcast({ type: 'x3d-start', runId, kind: 'mission', target: origin + '/lab/x3d/' });
      runX3dMission({ runId, origin, outDir: join(ROOT, 'runs', runId), emit: broadcast, options: body?.options || {} })
        .then((summary) => broadcast({ type: 'x3d-end', runId, kind: 'mission', ok: true, summary: { runId, verdict: summary.verdict, kpis: summary.kpis } }))
        .catch((err) => broadcast({ type: 'x3d-end', runId, kind: 'mission', ok: false, error: String(err?.message || err), stack: String(err?.stack || '') }))
        .finally(() => { activeRun = null; });
      return json(res, 202, { runId });
    }

    if (path === '/api/runs' && req.method === 'GET') {
      return json(res, 200, { runs: await listRuns() });
    }

    if (path === '/api/mission' && req.method === 'POST') {
      if (activeRun) return json(res, 409, { error: 'a mission is already running', id: activeRun.id });
      const body = await readBody(req);
      const origin = `http://127.0.0.1:${PORT}`;
      const runId = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14) + '-' + Math.random().toString(36).slice(2, 6);
      activeRun = { id: runId, startedAt: Date.now() };
      broadcast({ type: 'run-start', runId, target: origin + '/lab/broken/' });

      runMission({
        runId,
        origin,
        outDir: join(ROOT, 'runs', runId),
        emit: broadcast,
        options: body?.options || {},
      })
        .then((summary) => {
          broadcast({ type: 'run-end', runId, ok: true, summary });
        })
        .catch((err) => {
          broadcast({ type: 'run-end', runId, ok: false, error: String(err?.message || err), stack: String(err?.stack || '') });
        })
        .finally(() => {
          activeRun = null;
        });

      return json(res, 202, { runId });
    }

    if (path === '/api/explorer/tools' && req.method === 'GET') {
      try {
        return json(res, 200, await listTools());
      } catch (err) {
        return json(res, 500, { error: String(err?.message || err) });
      }
    }

    if (path === '/api/explorer/tool' && req.method === 'POST') {
      const body = await readBody(req);
      try {
        const result = await probeTool({ origin: `http://127.0.0.1:${PORT}`, ...body });
        return json(res, 200, result);
      } catch (err) {
        return json(res, 500, { error: String(err?.message || err) });
      }
    }

    if (path.startsWith('/runs/')) {
      const rel = normalize(path.slice('/runs/'.length));
      if (rel.startsWith('..')) return json(res, 400, { error: 'bad path' });
      return serveFile(res, join(ROOT, 'runs', rel), { cache: 'none' });
    }

    if (path === '/api/session' && req.method === 'GET') {
      return json(res, 200, {
        activeRun,
        events: history.length,
        history,
        serverTime: new Date().toISOString(),
        target: { broken: `http://127.0.0.1:${PORT}/lab/broken/`, fixed: `http://127.0.0.1:${PORT}/lab/fixed/` },
      });
    }

    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('404');
  } catch (err) {
    console.error('[server]', err);
    if (!res.headersSent) json(res, 500, { error: String(err?.message || err) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`BLACKBOX control plane  http://${HOST}:${PORT}`);
  console.log(`  target (broken)        http://127.0.0.1:${PORT}/lab/broken/`);
  console.log(`  target (fixed)         http://127.0.0.1:${PORT}/lab/fixed/`);
  console.log(`  x3d dev console        http://127.0.0.1:${PORT}/lab/x3d/`);
  console.log(`  mission control        http://localhost:${PORT}/`);
});
