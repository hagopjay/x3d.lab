/**
 * mcp-process.mjs — how to launch the official chrome-devtools-mcp server here.
 *
 * In production this would be a line in mcp.json. Here we own the process so the
 * demo can show the exact argv, the handshake, and every frame that follows.
 */
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  join(homedir(), '.cache', 'chrome-159', 'chrome-linux64', 'chrome'),
  '/home/user/.cache/chrome-159/chrome-linux64/chrome',
].filter(Boolean);

function findChrome() {
  for (const c of CHROME_CANDIDATES) if (existsSync(c)) return c;
  throw new Error('No Chrome binary found. Set CHROME_PATH.');
}

export const CHROME_PATH = findChrome();
export const SERVER_ENTRY = resolve(
  import.meta.dirname,
  '..',
  'node_modules',
  'chrome-devtools-mcp',
  'out',
  'Default',
  'gen',
  'src',
  'bin',
  'chrome-devtools-mcp.js',
);

/**
 * Server flags, all deliberate:
 *  --headless --isolated        ephemeral profile, no state bleed between runs
 *  --memoryDebugging            unlocks the heap-snapshot tool family
 *  --viewport                   deterministic layout for screenshots
 *  --no-usage-statistics        no telemetry leaves the box
 *  --no-performance-crux        traces stay local (no CrUX field-data calls)
 *  --chromeArg=--js-flags=…     expose gc() so heap deltas are meaningful
 *  cpu profile                    keeps Chrome GPU-disabled for stable SRE measurements
 *  x3d profile                    SwiftShader WebGL (explicit opt-in) for the X3D Lab's X_ITE pages
 *  webgpu profile                 opt-in Vulkan/WebGPU flags for the compute smoke lab
 *                                 (experimental flags; do not use for general browsing)
 */
export function serverArgs({ extra = [], viewport = '1280x800', profile = 'cpu' } = {}) {
  if (!['cpu', 'webgpu', 'x3d'].includes(profile)) throw new Error(`Unknown Chrome profile "${profile}" (expected cpu, webgpu or x3d)`);
  const profileFlags = profile === 'x3d'
    ? [
        // X3D Lab: X_ITE needs WebGL. Headless Chrome without a GPU falls back to SwiftShader anyway, with a
        // deprecation warning; opting in explicitly keeps the console clean and the renderer known.
        '--chromeArg=--use-angle=swiftshader',
        '--chromeArg=--enable-unsafe-swiftshader',
        '--chromeArg=--ignore-gpu-blocklist',
      ]
    : profile === 'webgpu'
    ? [
        // Optional experiment profile. The normal forensic mission remains GPU-disabled.
        '--chromeArg=--use-angle=vulkan',
        '--chromeArg=--enable-features=Vulkan',
        '--chromeArg=--disable-vulkan-surface',
        '--chromeArg=--enable-unsafe-webgpu',
        '--chromeArg=--enable-webgpu-developer-features',
      ]
    : ['--chromeArg=--disable-gpu'];
  return [
    SERVER_ENTRY,
    '--headless',
    '--isolated',
    '--no-usage-statistics',
    '--no-performance-crux',
    '--memoryDebugging',
    '--executablePath',
    CHROME_PATH,
    `--viewport=${viewport}`,
    '--chromeArg=--no-sandbox',
    '--chromeArg=--disable-setuid-sandbox',
    '--chromeArg=--disable-dev-shm-usage',
    ...profileFlags,
    '--chromeArg=--js-flags=--expose-gc',
    '--chromeArg=--window-size=1280,800',
    ...extra,
  ];
}

export function describeLaunch(profile = 'cpu') {
  return `chrome-devtools-mcp --headless --isolated --memoryDebugging --executablePath <Chrome for Testing> --viewport=1280x800 --profile=${profile}`;
}

/** Human-readable one-liner for the default CPU-only mission. */
export const LAUNCH_DESCRIPTION = describeLaunch('cpu');
