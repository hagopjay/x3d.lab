/**
 * mcp-client.mjs — a dependency-free Model Context Protocol client.
 *
 * Speaks JSON-RPC 2.0 over a child process's stdio, using the
 * newline-delimited framing that MCP stdio transports require.
 *
 * Why hand-rolled instead of an SDK: this is the demo's instrument panel.
 * Every frame that crosses the wire is captured (direction, method, byte size,
 * latency) so the agent's reasoning can be audited frame by frame.
 *
 * Implements the parts of the spec a real client needs:
 *   1. initialize / notifications/initialized handshake + protocol negotiation
 *   2. server -> client requests (roots/list, ping) — servers do call back
 *   3. tools/list discovery, with schema validation of every tools/call
 *   4. content-block demux: text, image, and error results
 *   5. cancellation + timeouts + idempotent shutdown
 */

import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

export const PROTOCOL_VERSION = '2025-06-18';

/** Rough token estimate for budgeting context: ~3.6 chars/token for mixed JSON+prose. */
export const estimateTokens = (s) => Math.ceil(String(s ?? '').length / 3.6);

class TimeoutError extends Error {}

export class McpClient extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.command  executable (usually process.execPath)
   * @param {string[]} opts.args   args, first element being the server entry
   * @param {string} [opts.cwd]
   * @param {Record<string,string>} [opts.env]
   * @param {number} [opts.requestTimeoutMs=120000]
   * @param {string} [opts.wireLogPath]  append-only JSONL of every frame
   */
  constructor(opts) {
    super();
    this.opts = { requestTimeoutMs: 120_000, ...opts };
    this._id = 0;
    this._pending = new Map();
    this._buf = '';
    this._closed = false;
    this.tools = [];
    this.toolMap = new Map();
    this.serverInfo = null;
    this.protocolVersion = null;
    this.roots = [{ uri: 'file://' + process.cwd(), name: 'blackbox-workspace' }];
    this.stats = { calls: 0, errors: 0, bytesOut: 0, bytesIn: 0, ms: 0 };
    if (this.opts.wireLogPath) {
      mkdirSync(dirname(this.opts.wireLogPath), { recursive: true });
      this._wireLogPath = this.opts.wireLogPath;
    }
  }

  async start() {
    const { command, args, cwd, env } = this.opts;
    this.child = spawn(command, args, {
      cwd,
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child.stdout.setEncoding('utf8');
    this.child.stderr.setEncoding('utf8');
    this.child.stdout.on('data', (d) => this._onStdout(d));
    // Server diagnostics are useful evidence, not noise — surface them.
    this.child.stderr.on('data', (d) => this.emit('server-stderr', d.toString()));
    this.child.on('exit', (code, signal) => {
      this._closed = true;
      for (const [, p] of this._pending) p.reject(new Error(`server exited (code=${code} signal=${signal})`));
      this._pending.clear();
      this.emit('exit', { code, signal });
    });
    return this;
  }

  _onStdout(chunk) {
    this._buf += chunk;
    let idx;
    while ((idx = this._buf.indexOf('\n')) >= 0) {
      const line = this._buf.slice(0, idx).trim();
      this._buf = this._buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        this.emit('protocol-error', { reason: 'non-json line', line: line.slice(0, 400) });
        continue;
      }
      this._handleMessage(msg);
    }
  }

  _handleMessage(msg) {
    // (a) Response to one of our requests.
    if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined) && !msg.method) {
      const p = this._pending.get(msg.id);
      if (!p) return;
      this._pending.delete(msg.id);
      clearTimeout(p.timer);
      const latency = Number(process.hrtime.bigint() - p.t0) / 1e6;
      const bytes = Buffer.byteLength(JSON.stringify(msg));
      this.stats.bytesIn += bytes;
      this.stats.ms += latency;
      this._wire({ dir: 'in', id: msg.id, method: p.method, ms: Math.round(latency), bytes, ok: !msg.error });
      if (msg.error) {
        this.stats.errors++;
        p.reject(Object.assign(new Error(msg.error.message || 'JSON-RPC error'), { rpc: msg.error }));
      } else {
        p.resolve(msg.result);
      }
      return;
    }

    // (b) Server -> client request. A real client must serve these.
    if (msg.id !== undefined && msg.method) {
      this._wire({ dir: 'in', id: msg.id, method: msg.method, serverRequest: true });
      let result;
      if (msg.method === 'roots/list') result = { roots: this.roots };
      else if (msg.method === 'ping') result = {};
      else {
        this._send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: `Method not found: ${msg.method}` } });
        return;
      }
      this._send({ jsonrpc: '2.0', id: msg.id, result });
      return;
    }

    // (c) Notification.
    if (msg.method) this.emit('notification', msg);
  }

  _wire(entry) {
    const row = { t: new Date().toISOString(), ...entry };
    this.emit('wire', row);
    if (this._wireLogPath) {
      this._wireAppend = this._wireAppend || [];
      this._wireAppend.push(JSON.stringify(row));
      // Flush every frame; runs are short and durability beats throughput here.
      writeFileSync(this._wireLogPath, this._wireAppend.join('\n') + '\n');
    }
  }

  _send(obj) {
    if (this._closed) throw new Error('transport closed');
    const line = JSON.stringify(obj) + '\n';
    this.stats.bytesOut += Buffer.byteLength(line);
    this.child.stdin.write(line);
  }

  request(method, params = {}, { timeoutMs } = {}) {
    const id = ++this._id;
    const t0 = process.hrtime.bigint();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new TimeoutError(`MCP request timed out after ${timeoutMs ?? this.opts.requestTimeoutMs}ms: ${method}`));
      }, timeoutMs ?? this.opts.requestTimeoutMs);
      this._pending.set(id, { resolve, reject, timer, t0, method });
      this._wire({ dir: 'out', id, method, bytes: Buffer.byteLength(JSON.stringify(params)) });
      this._send({ jsonrpc: '2.0', id, method, params });
    });
  }

  notify(method, params = {}) {
    this._wire({ dir: 'out', method, notification: true, bytes: Buffer.byteLength(JSON.stringify(params)) });
    this._send({ jsonrpc: '2.0', method, params });
  }

  /** Handshake, then discover the tool surface and cache schemas. */
  async initialize() {
    const res = await this.request('initialize', {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { roots: { listChanged: false } },
      clientInfo: { name: 'blackbox-agent', version: '1.1.0' },
    });
    this.serverInfo = res.serverInfo ?? null;
    this.protocolVersion = res.protocolVersion ?? null;
    this.capabilities = res.capabilities ?? {};
    this.notify('notifications/initialized');
    const { tools } = await this.request('tools/list', {});
    this.tools = tools ?? [];
    this.toolMap = new Map(this.tools.map((t) => [t.name, t]));
    this.emit('ready', { serverInfo: this.serverInfo, protocolVersion: this.protocolVersion, toolCount: this.tools.length });
    return this;
  }

  hasTool(name) {
    return this.toolMap.has(name);
  }

  /** Validate a tools/call payload against the server's own published JSON Schema. */
  validate(name, args) {
    const tool = this.toolMap.get(name);
    if (!tool) throw new Error(`Unknown tool: ${name}. Discovered: ${[...this.toolMap.keys()].join(', ')}`);
    const schema = tool.inputSchema || {};
    const props = schema.properties || {};
    for (const req of schema.required || []) {
      if (args?.[req] === undefined) throw new Error(`Tool ${name} missing required arg "${req}" (${props[req]?.type ?? 'any'})`);
    }
    for (const [k, v] of Object.entries(args ?? {})) {
      const spec = props[k];
      if (!spec) {
        if (schema.additionalProperties === false) throw new Error(`Tool ${name} rejects unknown arg "${k}"`);
        continue;
      }
      const t = spec.type;
      const actual = Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v;
      const expected = t === 'integer' ? 'number' : t;
      if (expected && actual !== expected) throw new Error(`Tool ${name} arg "${k}" expected ${t}, got ${actual}`);
      if (spec.enum && !spec.enum.includes(v)) throw new Error(`Tool ${name} arg "${k}" must be one of ${spec.enum.join('|')}, got ${v}`);
    }
  }

  /**
   * Call a tool. Returns a normalized result:
   *   { text, images:[{mimeType, data}], isError, raw, bytes, tokens, ms }
   */
  async callTool(name, args = {}, { timeoutMs, retries = 0 } = {}) {
    this.validate(name, args);
    const t0 = Date.now();
    let result;
    for (let attempt = 0; ; attempt++) {
      try {
        result = await this.request('tools/call', { name, arguments: args }, { timeoutMs });
        break;
      } catch (err) {
        // Protocol-level hiccups inside the browser are the most common transient
        // failure; retry a bounded number of times before surfacing it.
        const transient = /Target closed|Protocol error|timed out|detached/i.test(err.message);
        if (attempt >= retries || !transient) throw err;
        await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
      }
    }
    const ms = Date.now() - t0;
    const content = result?.content ?? [];
    const text = content.filter((c) => c.type === 'text').map((c) => c.text).join('\n');
    const images = content.filter((c) => c.type === 'image').map((c) => ({ mimeType: c.mimeType, data: c.data }));
    const out = {
      text,
      images,
      isError: Boolean(result?.isError),
      raw: result,
      ms,
      bytes: Buffer.byteLength(JSON.stringify(result ?? {})),
      tokens: estimateTokens(JSON.stringify(result ?? {})),
    };
    this.stats.calls++;
    if (out.isError) this.stats.errors++;
    return out;
  }

  /** Convenience: call an evaluate_script tool and JSON-parse the fenced result. */
  async evaluate(pageId, fn, { args, format } = {}) {
    const res = await this.callTool('evaluate_script', {
      pageId,
      function: fn,
      ...(args ? { args } : {}),
      ...(format ? { format } : {}),
    });
    return { ...res, value: parseEvalResult(res.text) };
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    try {
      this.child.stdin.end();
    } catch {}
    await new Promise((r) => setTimeout(r, 120));
    try {
      this.child.kill('SIGTERM');
    } catch {}
    setTimeout(() => {
      try {
        this.child.kill('SIGKILL');
      } catch {}
    }, 1500).unref?.();
  }
}

/** MCP returns script output as a fenced json block; parse defensively. */
export function parseEvalResult(text = '') {
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const body = (fence ? fence[1] : text).trim();
  try {
    return JSON.parse(body);
  } catch {
    return body;
  }
}

/** Parse the markdown page list that list_pages returns. */
export function parsePages(text = '') {
  const pages = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^(\d+):\s*(.*?)\s*(?:\((.*?)\))?\s*(\[selected\])?\s*(?:(\S+)=(\S+))?$/);
    if (!m) continue;
    pages.push({ id: Number(m[1]), title: m[2], url: m[3] || '', selected: Boolean(m[4]) });
  }
  return pages;
}

export { TimeoutError };
