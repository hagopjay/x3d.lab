#!/usr/bin/env node
/**
 * run-cli.mjs — run a mission headless, e.g. from CI.
 *
 *   node agent/run-cli.mjs --origin http://127.0.0.1:8080
 *
 * Exits non-zero if recall drops below the threshold, which is what makes this
 * usable as a regression gate for the *agent itself*.
 */
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { runMission } from './mission.mjs';

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};

const origin = flag('origin', 'http://127.0.0.1:8080');
const threshold = Number(flag('min-recall', '0.8'));
const runId = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
const outDir = join(process.cwd(), 'runs', runId);
await mkdir(outDir, { recursive: true });

const colour = (s, c) => `\x1b[${c}m${s}\x1b[0m`;
const emit = (e) => {
  if (e.type === 'log') {
    const tag = { ok: colour(' ok ', 32), warn: colour('warn', 33), error: colour(' err', 31), info: colour('info', 36) }[e.level] ?? '    ';
    console.log(`${tag} ${e.msg}`);
  } else if (e.type === 'phase' && e.status === 'running') {
    console.log(colour(`\n▶ ${e.title}`, 1));
  } else if (e.type === 'mcp-call') {
    process.stdout.write(colour(`   · ${e.name} `, 90));
  } else if (e.type === 'mcp-result') {
    process.stdout.write(colour(`${e.ms}ms${e.isError ? ' ERR' : ''}\n`, e.isError ? 31 : 90));
  }
};

const summary = await runMission({ runId, origin, outDir, emit, options: {} });

console.log(`\n${colour('══ VERDICT ══', 1)}`);
console.log(`  regressions discovered : ${summary.detection.discovered}/${summary.detection.catalogSize} (recall ${(summary.detection.recall * 100).toFixed(0)}%)`);
console.log(`  findings               : ${summary.findings.length}`);
console.log(`  fixes proven           : ${summary.verification.verified}/${summary.verification.checks.length}`);
console.log(`  tool calls             : ${summary.totals.toolCalls} (${summary.totals.toolErrors} errors)`);
console.log(`  wall clock             : ${Math.round(summary.durationMs / 1000)}s`);
console.log(`  report                 : runs/${runId}/report.html`);

process.exit(summary.detection.recall >= threshold ? 0 : 1);
