/**
 * Host-side GPU preflight.
 *
 * This deliberately does not infer physical GPU execution from browser JS. It
 * records independent host evidence (nvidia-smi, Vulkan summary, device nodes)
 * so the report can distinguish "WebGPU worked" from "a T4 was present".
 */
import { execFileSync } from 'node:child_process';
import { readdirSync, existsSync } from 'node:fs';
import { homedir, platform, arch } from 'node:os';
import { join } from 'node:path';

function command(name, args, timeout = 12_000) {
  try {
    const stdout = execFileSync(name, args, {
      encoding: 'utf8',
      timeout,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, LC_ALL: 'C' },
      maxBuffer: 1024 * 1024,
    });
    return { available: true, exitCode: 0, output: stdout.trim().slice(0, 12_000) };
  } catch (err) {
    const stderr = String(err.stderr || '').trim();
    return {
      available: false,
      exitCode: Number.isInteger(err.status) ? err.status : null,
      output: [String(err.message || '').split('\n')[0], stderr].filter(Boolean).join('\n').slice(0, 2_000),
    };
  }
}

function deviceNodes() {
  const dirs = ['/dev/dri', '/dev'];
  const found = [];
  for (const dir of dirs) {
    try {
      for (const name of readdirSync(dir)) {
        if (dir === '/dev/dri' && /^(card|renderD)\d+$/.test(name)) found.push(join(dir, name));
        if (dir === '/dev' && /^nvidia(?:\d+|ctl|-uvm|-modeset)$/.test(name)) found.push(join(dir, name));
      }
    } catch {}
  }
  return [...new Set(found)].sort();
}

function parseNvidia(output) {
  return String(output || '').split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
    const [name = '', driver = '', memory = ''] = line.split(',').map((x) => x.trim());
    return { name, driver, memory };
  });
}

export function collectGpuPreflight() {
  const nvidia = command('nvidia-smi', ['--query-gpu=name,driver_version,memory.total', '--format=csv,noheader,nounits']);
  const vulkan = command('vulkaninfo', ['--summary']);
  const nodes = deviceNodes();
  const gpus = nvidia.available ? parseNvidia(nvidia.output) : [];
  const chromePath = process.env.CHROME_PATH || join(homedir(), '.cache', 'chrome-159', 'chrome-linux64', 'chrome');
  const chromeVersion = existsSync(chromePath) ? command(chromePath, ['--version'], 8_000) : { available: false, output: 'Chrome path not found' };
  const gpuPresent = gpus.length > 0 || nodes.some((p) => /nvidia|\/dev\/dri\//.test(p));
  const vulkanDevices = (vulkan.output || '').split('\n').filter((line) => /deviceName|device name|GPU[0-9]|driverName|deviceType/i.test(line)).slice(0, 24);
  const vulkanSoftwareOnly = /llvmpipe|lavapipe|swiftshader/i.test(vulkanDevices.join('\n')) || /PHYSICAL_DEVICE_TYPE_CPU/i.test(vulkan.output || '');
  const hostStatus = gpuPresent ? 'detected' : vulkanSoftwareOnly ? 'software-only' : 'not-detected';
  return {
    schemaVersion: 1,
    capturedAt: new Date().toISOString(),
    runtime: { platform: platform(), arch: arch(), node: process.version, cwd: process.cwd() },
    chrome: { path: chromePath, version: chromeVersion.available ? chromeVersion.output : null, available: chromeVersion.available },
    hostGpu: {
      status: hostStatus,
      confidence: gpus.length ? 'nvidia-smi' : nodes.length ? 'device-node-only' : vulkanSoftwareOnly ? 'vulkan-software-device' : 'no-host-evidence',
      nvidiaSmi: { available: nvidia.available, gpus, raw: nvidia.available ? nvidia.output : null, diagnostic: nvidia.available ? null : nvidia.output },
      deviceNodes: nodes,
      vulkan: { available: vulkan.available, softwareOnly: vulkanSoftwareOnly, deviceLines: vulkanDevices, rawSummary: vulkan.available ? vulkan.output : null, diagnostic: vulkan.available ? null : vulkan.output },
    },
    interpretation: gpuPresent
      ? 'Host GPU evidence exists. This does not by itself prove Chrome selected that adapter; compare browser adapter metadata and WebGPU compute results.'
      : vulkanSoftwareOnly
        ? 'Vulkan reports a software/CPU device and no physical GPU evidence was found. WebGPU may still run through software; do not report a hardware-GPU pass.'
        : 'No host GPU evidence was found. WebGPU may be unavailable or software-backed; do not report a hardware-GPU pass.',
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  console.log(JSON.stringify(collectGpuPreflight(), null, 2));
}
