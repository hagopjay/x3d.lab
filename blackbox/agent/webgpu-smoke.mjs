/**
 * MCP-first WebGPU smoke mission.
 *
 * Host preflight is separate, explicitly labelled evidence. All browser actions
 * (page creation, navigation, JS evaluation, console collection, screenshot)
 * go through the official chrome-devtools-mcp server and are recorded on wire.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { readFile } from 'node:fs/promises';
import { McpClient, parseEvalResult } from '../lib/mcp-client.mjs';
import { serverArgs, CHROME_PATH, describeLaunch } from './mcp-process.mjs';
import { collectGpuPreflight } from './gpu-preflight.mjs';

const SOFTWARE_HINT = /swiftshader|lavapipe|llvmpipe|software|microsoft basic render/i;
const NVIDIA_HINT = /nvidia|geforce|tesla|quadro|rtx|gtx/i;
const ESC = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function selectedPageId(text = '') {
  const lines = String(text).split('\n');
  const line = lines.find((x) => x.includes('[selected]')) || lines.find((x) => /^\s*\d+:/.test(x));
  const id = Number(line?.match(/^\s*(\d+):/)?.[1]);
  if (!Number.isFinite(id)) throw new Error(`Could not parse selected MCP page id from: ${text.slice(0, 300)}`);
  return id;
}

function hostAdapterAssessment(host, browserResult) {
  const adapter = browserResult?.webgpu?.adapter;
  const info = adapter?.info || {};
  const metadata = [info.vendor, info.device, info.description, info.architecture].filter(Boolean).join(' ');
  const gpuNames = (host?.hostGpu?.nvidiaSmi?.gpus || []).map((g) => g.name).join(' ');
  const hostHasNvidia = /nvidia/i.test(gpuNames);
  if (SOFTWARE_HINT.test(metadata)) {
    return { classification: 'software-likely', hostGpuDetected: host?.hostGpu?.status === 'detected', metadata, basis: 'software renderer signature in browser adapter metadata; heuristic only' };
  }
  if (hostHasNvidia && NVIDIA_HINT.test(metadata)) {
    return { classification: 'hardware-metadata-consistent', hostGpuDetected: true, metadata, basis: 'NVIDIA host GPU and NVIDIA-like adapter metadata agree; not cryptographic proof of adapter selection' };
  }
  if (host?.hostGpu?.status === 'detected') {
    return { classification: 'host-gpu-present-adapter-unverified', hostGpuDetected: true, metadata, basis: 'host GPU is present, but browser adapter metadata does not identify the selected physical adapter' };
  }
  if (browserResult?.webgpu?.status === 'pass') {
    return { classification: 'webgpu-passed-hardware-unattested', hostGpuDetected: false, metadata, basis: 'compute passed but no host-side GPU evidence was found' };
  }
  return { classification: 'unavailable-or-unverified', hostGpuDetected: false, metadata, basis: 'no matching host/browser hardware evidence' };
}

function htmlReport(summary, screenshotData = null) {
  const result = summary.browserResult || {};
  const webgpu = result.webgpu || {};
  const host = summary.hostPreflight?.hostGpu || {};
  const details = JSON.stringify({
    launch: summary.launch,
    mcpServerArgs: summary.mcpServerArgs,
    chromePath: summary.chromePath,
    hostPreflight: summary.hostPreflight,
    browserResult: result,
    console: summary.console,
    mcp: summary.mcp,
  }, null, 2);
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BLACKBOX WebGPU smoke · ${ESC(summary.runId)}</title>
<style>
:root{color-scheme:dark;--bg:#070d16;--p:#0e1928;--line:#22314a;--ink:#edf3ff;--muted:#9caec7;--a:#ffb020;--g:#3ddc97;--r:#ff6868}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#101c2d,var(--bg) 55%);color:var(--ink);font:15px/1.55 system-ui,sans-serif}main{max-width:1080px;margin:40px auto;padding:0 20px}h1{font-size:38px;margin:6px 0}.tag{font:12px ui-monospace,monospace;color:var(--a);text-transform:uppercase;letter-spacing:.14em}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:24px 0}.card{border:1px solid var(--line);background:var(--p);padding:16px;border-radius:12px}.k{font:11px ui-monospace,monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}.v{font:15px ui-monospace,monospace;margin-top:8px;overflow-wrap:anywhere}.pass{color:var(--g)}.fail{color:var(--r)}.warn{color:var(--a)}p{color:var(--muted)}pre{white-space:pre-wrap;overflow:auto;background:#040911;border:1px solid var(--line);padding:14px;border-radius:10px;font:12px/1.5 ui-monospace,monospace}img{max-width:100%;border:1px solid var(--line);border-radius:10px}.callout{padding:12px 14px;border-left:3px solid var(--a);background:#161b20;color:var(--muted)}a{color:#85b6ff}
</style><main><div class="tag">MCP-first browser GPU experiment · ${ESC(summary.runId)}</div><h1>WebGPU compute smoke</h1>
<p>Generated ${ESC(summary.finishedAt || summary.startedAt)} · Chrome profile: <code>webgpu</code> · MCP calls: ${summary.mcp?.toolCalls ?? 0}</p>
<div class="grid">
<div class="card"><div class="k">WebGPU compute</div><div class="v ${webgpu.status === 'pass' ? 'pass' : webgpu.status === 'error' || webgpu.status === 'fail' ? 'fail' : 'warn'}">${ESC(webgpu.status || 'unknown')}</div></div>
<div class="card"><div class="k">Host GPU</div><div class="v ${host.status === 'detected' ? 'pass' : 'warn'}">${ESC(host.status || 'unknown')}</div></div>
<div class="card"><div class="k">Hardware interpretation</div><div class="v">${ESC(summary.hardwareAssessment?.classification || 'unknown')}</div></div>
<div class="card"><div class="k">Output check</div><div class="v">${webgpu.compute ? `${webgpu.compute.mismatchCount} mismatches / ${webgpu.compute.elementCount} values` : ESC(webgpu.reason || 'not run')}</div></div>
<div class="card"><div class="k">Submit + readback</div><div class="v">${webgpu.compute?.submitReadbackMs ?? '—'} ms</div></div>
<div class="card"><div class="k">Chrome</div><div class="v">${ESC(summary.hostPreflight?.chrome?.version || summary.hostPreflight?.chrome?.path || 'unknown')}</div></div>
</div>
<div class="callout"><strong>Evidence boundary:</strong> WebGPU passing proves the deterministic compute result was produced. Host GPU detection and browser adapter metadata are separate signals. This smoke test is not a model benchmark, a kernel-only timer, or proof of a T4 unless the runtime and adapter evidence support that claim.</div>
${screenshotData ? `<h2>MCP screenshot</h2><img alt="WebGPU smoke page captured through chrome-devtools-mcp" src="${screenshotData}">` : ''}
<h2>Full evidence</h2><p><a href="./summary.json">summary.json</a> · <a href="./host-preflight.json">host-preflight.json</a> · <a href="./wire.jsonl">wire.jsonl</a>${summary.screenshot ? ` · <a href="./${ESC(summary.screenshot)}">screenshot</a>` : ''}</p><pre>${ESC(details)}</pre>
</main></html>`;
}

export async function runWebGpuSmoke({ origin = 'http://127.0.0.1:8080', outDir, runId = `gpu-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}-${randomUUID().slice(0, 6)}`, emit = () => {} } = {}) {
  const startedAt = new Date().toISOString();
  outDir ||= join(process.cwd(), 'runs', runId);
  const wireLogPath = join(outDir, 'wire.jsonl');
  await mkdir(join(outDir, 'screens'), { recursive: true });
  const hostPreflight = collectGpuPreflight();
  await writeFile(join(outDir, 'host-preflight.json'), JSON.stringify(hostPreflight, null, 2));
  const steps = [];
  const log = (stage, message, extra = {}) => emit({ type: 'gpu-smoke', runId, stage, message, ...extra });
  log('preflight', `host GPU ${hostPreflight.hostGpu.status}; Vulkan ${hostPreflight.hostGpu.vulkan.available ? 'available' : 'not detected'}`, { hostGpu: hostPreflight.hostGpu.status });

  const client = new McpClient({
    command: process.execPath,
    args: serverArgs({ profile: 'webgpu' }),
    requestTimeoutMs: 90_000,
    wireLogPath,
  });
  client.on('wire', (row) => emit({ type: 'gpu-wire', runId, ...row }));
  client.on('server-stderr', (s) => {
    const line = String(s).trim();
    if (line) emit({ type: 'gpu-smoke', runId, stage: 'browser', message: line.slice(0, 300), level: 'debug' });
  });

  let browserResult = null;
  let consoleResult = null;
  let screenshot = null;
  let pageId = null;
  let serverInfo = null;
  let protocolVersion = null;
  let toolCalls = 0;
  let toolErrors = 0;
  let failure = null;
  const call = async (name, args, { timeoutMs = 60_000, critical = true } = {}) => {
    log('mcp', `call ${name}`, { tool: name });
    try {
      const res = await client.callTool(name, args, { timeoutMs, retries: 1 });
      toolCalls++;
      if (res.isError) {
        toolErrors++;
        steps.push({ tool: name, args, ms: res.ms, bytes: res.bytes, isError: true, text: (res.text || '').slice(0, 500) });
        if (critical) throw new Error(`${name} returned MCP error: ${(res.text || '').slice(0, 350)}`);
      } else {
        steps.push({ tool: name, args, ms: res.ms, bytes: res.bytes, tokens: res.tokens, isError: false });
      }
      log('mcp', `${name} ${res.isError ? 'error' : 'done'} · ${res.ms} ms · ${res.bytes} bytes`, { tool: name, isError: res.isError, ms: res.ms, bytes: res.bytes });
      return res;
    } catch (error) {
      if (!steps.some((s) => s.tool === name && s.error === error.message)) {
        toolErrors++;
        steps.push({ tool: name, args, error: String(error.message || error), isError: true });
      }
      if (critical) throw error;
      return { text: '', images: [], isError: true, error: String(error.message || error) };
    }
  };

  try {
    log('handshake', 'starting chrome-devtools-mcp with the opt-in WebGPU profile');
    await client.start();
    await client.initialize();
    serverInfo = client.serverInfo;
    protocolVersion = client.protocolVersion;
    const required = ['new_page', 'navigate_page', 'evaluate_script', 'list_console_messages', 'take_screenshot'];
    const missing = required.filter((name) => !client.hasTool(name));
    if (missing.length) throw new Error(`MCP server missing required tools: ${missing.join(', ')}`);
    log('handshake', `connected to ${serverInfo?.name || 'MCP server'} v${serverInfo?.version || '?'} · ${client.tools.length} tools discovered`);

    const created = await call('new_page', { url: 'about:blank', isolatedContext: `blackbox-webgpu-${runId}` });
    pageId = selectedPageId(created.text);
    await call('navigate_page', { pageId, type: 'url', url: `${origin}/lab/webgpu/`, timeout: 60_000 });
    log('page', 'loaded local WebGPU test fixture through MCP navigation');

    const evaluation = await call('evaluate_script', {
      pageId,
      function: `async () => {
        if (!window.__BB_GPU_SMOKE_PROMISE__) {
          if (!window.__BB_GPU_SMOKE__) return { schemaVersion: 1, webgpu: { status: 'error', reason: 'test fixture did not initialize' } };
          return await window.__BB_GPU_SMOKE__.run();
        }
        return await window.__BB_GPU_SMOKE_PROMISE__;
      }`,
    }, { timeoutMs: 60_000 });
    browserResult = parseEvalResult(evaluation.text);
    if (typeof browserResult === 'string') {
      try { browserResult = JSON.parse(browserResult); } catch {}
    }
    if (!browserResult || typeof browserResult !== 'object') {
      browserResult = { schemaVersion: 1, webgpu: { status: 'error', reason: 'MCP evaluate_script returned no parseable result', raw: String(evaluation.text || '').slice(0, 500) } };
    }
    log('compute', `WebGPU ${browserResult.webgpu?.status || 'unknown'} · ${browserResult.webgpu?.compute?.mismatchCount ?? 'n/a'} mismatches`, { result: browserResult.webgpu });

    consoleResult = await call('list_console_messages', { pageId, types: ['error', 'warn'], includeStackTraces: true }, { critical: false });
    const shot = await call('take_screenshot', { pageId, format: 'png' }, { critical: false });
    const image = shot.images?.[0];
    if (image?.data) {
      const ext = image.mimeType?.includes('png') ? 'png' : 'jpg';
      screenshot = `screens/webgpu.${ext}`;
      await writeFile(join(outDir, screenshot), Buffer.from(image.data, 'base64'));
    }
  } catch (error) {
    failure = String(error?.message || error);
    log('error', failure, { level: 'error' });
  } finally {
    await client.close();
  }

  const hardwareAssessment = hostAdapterAssessment(hostPreflight, browserResult);
  const computeStatus = browserResult?.webgpu?.status || 'error';
  const result = {
    schemaVersion: 1,
    kind: 'webgpu-smoke',
    runId,
    startedAt,
    finishedAt: new Date().toISOString(),
    origin: `${origin}/lab/webgpu/`,
    profile: 'webgpu',
    launch: describeLaunch('webgpu'),
    mcpServerArgs: serverArgs({ profile: 'webgpu' }).slice(1),
    chromePath: CHROME_PATH,
    hostPreflight,
    hardwareAssessment,
    browserResult,
    console: {
      isError: Boolean(consoleResult?.isError),
      text: (consoleResult?.text || consoleResult?.error || '').slice(0, 12_000),
    },
    mcp: {
      server: serverInfo,
      protocolVersion,
      discoveredTools: client.tools.length,
      pageId,
      toolCalls,
      toolErrors,
      bytesOut: client.stats.bytesOut,
      bytesIn: client.stats.bytesIn,
      totalMs: client.stats.ms,
    },
    steps,
    screenshot,
    failure,
    verdict: {
      compute: computeStatus,
      hostGpu: hostPreflight.hostGpu.status,
      hardware: hardwareAssessment.classification,
      note: computeStatus === 'pass'
        ? 'Compute correctness passed. Physical GPU execution is a separate claim; consult host and browser adapter evidence.'
        : 'No WebGPU compute pass was established. Do not treat this run as GPU-accelerated model evidence.',
    },
  };
  await writeFile(join(outDir, 'summary.json'), JSON.stringify(result, null, 2));
  let screenshotData = null;
  if (screenshot) {
    const bytes = await readFile(join(outDir, screenshot));
    screenshotData = `data:image/${screenshot.endsWith('.png') ? 'png' : 'jpeg'};base64,${bytes.toString('base64')}`;
  }
  await writeFile(join(outDir, 'report.html'), htmlReport(result, screenshotData));
  log('complete', `WebGPU ${computeStatus}; host GPU ${hostPreflight.hostGpu.status}; report runs/${runId}/report.html`, { status: computeStatus, resultPath: `runs/${runId}/summary.json`, reportPath: `runs/${runId}/report.html` });
  return result;
}

// Standalone use: node agent/webgpu-smoke.mjs --origin http://127.0.0.1:8080
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const flag = (name, fallback) => {
    const i = args.indexOf(`--${name}`);
    return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
  };
  const origin = flag('origin', 'http://127.0.0.1:8080');
  const outDir = flag('output-dir', undefined);
  const requireGpu = args.includes('--require-gpu');
  const result = await runWebGpuSmoke({ origin, ...(outDir ? { outDir } : {}) });
  console.log(JSON.stringify({
    runId: result.runId,
    verdict: result.verdict,
    report: `runs/${result.runId}/report.html`,
    summary: `runs/${result.runId}/summary.json`,
    wire: `runs/${result.runId}/wire.jsonl`,
  }, null, 2));
  if (requireGpu && (result.hostPreflight.hostGpu.status !== 'detected' || result.verdict.compute !== 'pass')) process.exitCode = 2;
  else if (result.verdict.compute === 'error' || result.failure) process.exitCode = 1;
}
