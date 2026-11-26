#!/usr/bin/env node
// x3d-live/v1 reference server. Zero dependencies. Run:  node live_server.mjs [--port 8787] [--gate tools/gate.py]
//   POST /push   body = X3D XML  -> runs the x3d_mcp gate (XSD + semantic). 200 {etag, sha256} if it passes, 422 {errors} if not. A failing scene is NEVER published.
//   GET  /manifest.json          -> {etag, sha256, bytes, gate, updated}; honours If-None-Match (304)
//   GET  /scene.x3d              -> the last scene that passed the gate
// The Unity client (X3DLiveClient) polls /manifest.json. The gate is the tether: nothing reaches a headset unless a validator passed it.
import http from 'node:http';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const PORT = +arg('--port', 8787), GATE = arg('--gate', path.join(here, 'gate.py')), MAX = 16 * 1024 * 1024;
let cur = null; // {xml, etag, sha256, gate, updated}

function gate(xml) {
  return new Promise((resolve) => {
    const p = spawn(process.env.PYTHON || 'python3', [GATE, '-'], { stdio: ['pipe', 'pipe', 'pipe'] });
    let out = '', err = '';
    p.stdout.on('data', d => out += d); p.stderr.on('data', d => err += d);
    p.on('close', code => { try { resolve({ ok: code === 0, report: JSON.parse(out) }); } catch { resolve({ ok: false, report: { valid: false, errors: ['gate crashed: ' + err.slice(0, 500)] } }); } });
    p.stdin.end(xml);
  });
}

http.createServer(async (req, res) => {
  const send = (code, obj, h = {}) => { const b = typeof obj === 'string' ? obj : JSON.stringify(obj); res.writeHead(code, { 'content-type': typeof obj === 'string' ? 'application/xml' : 'application/json', 'access-control-allow-origin': '*', ...h }); res.end(b); };
  if (req.method === 'OPTIONS') return send(204, '', { 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST' });
  if (req.method === 'POST' && req.url === '/push') {
    const chunks = []; let n = 0;
    for await (const c of req) { n += c.length; if (n > MAX) return send(413, { error: 'too large' }); chunks.push(c); }
    const xml = Buffer.concat(chunks).toString('utf8');
    const g = await gate(xml);
    if (!g.ok) return send(422, { published: false, gate: g.report });
    const sha = crypto.createHash('sha256').update(xml).digest('hex');
    cur = { xml, sha256: sha, etag: '"' + sha.slice(0, 16) + '"', gate: g.report, updated: new Date().toISOString() };
    return send(200, { published: true, etag: cur.etag, sha256: sha, gate: g.report });
  }
  if (req.method === 'GET' && req.url.startsWith('/manifest.json')) {
    if (!cur) return send(404, { error: 'nothing published yet' });
    if (req.headers['if-none-match'] === cur.etag) { res.writeHead(304, { etag: cur.etag }); return res.end(); }
    return send(200, { etag: cur.etag, sha256: cur.sha256, bytes: Buffer.byteLength(cur.xml), gate: { xsd: cur.gate.xsd?.version, valid: cur.gate.valid }, updated: cur.updated }, { etag: cur.etag });
  }
  if (req.method === 'GET' && req.url.startsWith('/scene.x3d')) return cur ? send(200, cur.xml, { etag: cur.etag }) : send(404, { error: 'nothing published yet' });
  send(404, { error: 'not found' });
}).listen(PORT, '0.0.0.0', () => console.log(`x3d-live/v1 on :${PORT}, gate ${GATE}`));
