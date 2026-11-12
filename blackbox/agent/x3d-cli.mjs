#!/usr/bin/env node
/**
 * x3d-cli.mjs: run the X3D Lab mission headless (CI-friendly). Needs `npm start` running.
 *
 *   node agent/x3d-cli.mjs --origin http://127.0.0.1:8080 [--no-blender] [--skip-build]
 *
 * Blender leg: set X3DLAB_BPY_PYTHON (a Python with bpy) and X3DLAB_ADDON (x3d-blender-io/source).
 * Exits non-zero if any mission check fails.
 */
import { join } from 'node:path';
import { runX3dMission } from './x3d-mission.mjs';

const args = process.argv.slice(2);
const flag = (name, fallback) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : fallback; };
const origin = flag('origin', 'http://127.0.0.1:8080');
const runId = 'x3d-' + new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
const c = (s, n) => `\x1b[${n}m${s}\x1b[0m`;
const emit = (e) => {
  if (e.type === 'x3d-phase') console.log(c(`\n▶ ${e.phase}`, 1));
  else if (e.type === 'x3d-log') console.log(/^PASS/.test(e.line) ? c(e.line, 32) : /^FAIL/.test(e.line) ? c(e.line, 31) : '  ' + e.line);
};
const summary = await runX3dMission({ runId, origin, outDir: join(process.cwd(), 'runs', runId), emit,
  options: { blender: !args.includes('--no-blender'), skipBuild: args.includes('--skip-build') } });
console.log(`\n${c('══ VERDICT ══', 1)}\n  ${summary.verdict}\n  report: runs/${runId}/report.html`);
process.exit(summary.kpis.checksPassed === summary.kpis.checks ? 0 : 1);
