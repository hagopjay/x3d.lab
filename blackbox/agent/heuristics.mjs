/**
 * heuristics.mjs — pure functions that turn raw MCP tool output into structure.
 *
 * Every pattern here was written against output captured from a real
 * chrome-devtools-mcp 1.13.0 / Chrome 159 session (see docs/FORMATS.md).
 * Parsers are permissive on purpose: a malformed response should degrade to
 * "unknown", never crash the mission.
 */

export const num = (v, fallback = null) => {
  if (v === null || v === undefined) return fallback;
  const n = Number(String(v).replace(/[,\s]/g, '').replace(/[^\d.\-]/g, ''));
  return Number.isFinite(n) ? n : fallback;
};

/** "1.3 MB" | "304.1 kB" | "0 B" -> bytes */
export function bytes(str) {
  if (!str) return 0;
  const m = String(str).match(/([\d.,]+)\s*(B|kB|KB|MB|GB)/i);
  if (!m) return 0;
  const n = Number(m[1].replace(/,/g, ''));
  const unit = m[2].toUpperCase();
  return Math.round(n * (unit === 'B' ? 1 : unit === 'KB' ? 1024 : unit === 'GB' ? 1024 ** 3 : 1024 ** 2));
}

export const kb = (b) => (b >= 1024 * 1024 ? (b / 1024 / 1024).toFixed(1) + ' MB' : Math.round(b / 1024) + ' kB');

/* ─────────────────────────── performance trace ─────────────────────────── */

/**
 * The trace summary is markdown-ish:
 *   ## insight set id: NAVIGATION_0
 *   Metrics (lab / observed):
 *     - LCP: 9554 ms, event: ...
 *     - LCP breakdown:
 *       - TTFB: 566 ms, bounds: ...
 *   Available insights:
 *     - insight name: ImageDelivery
 *       description: ...
 *       estimated wasted bytes: 1.3 MB
 */
export function parseTrace(text = '') {
  const out = {
    url: text.match(/^URL: (.+)$/m)?.[1]?.trim() ?? null,
    cpuThrottling: text.match(/CPU throttling: (.+)$/m)?.[1]?.trim() ?? null,
    networkThrottling: text.match(/Network throttling: (.+)$/m)?.[1]?.trim() ?? null,
    insightSets: [],
    insights: [],
    metrics: {},
    breakdown: {},
    raw: text,
  };

  for (const m of text.matchAll(/^## insight set id: (\S+)/gm)) {
    out.insightSets.push({ id: m[1] });
  }

  // top-level metrics ("- LCP: 9554 ms", "- CLS: 0.04")
  for (const m of text.matchAll(/^\s{2}- ([A-Z][A-Za-z ()]*):\s*([\d,.]+)\s*(ms|s)?(?:,|$)/gm)) {
    const key = m[1].trim();
    if (/breakdown/i.test(key)) continue;
    out.metrics[key] = m[3] === 's' ? num(m[2]) * 1000 : num(m[2]);
  }
  // LCP sub-parts ("    - Load duration: 8,897 ms")
  const bd = text.match(/LCP breakdown:([\s\S]*?)(?:\n\s{2}- |\nMetrics)/);
  if (bd) {
    for (const m of bd[1].matchAll(/-\s*([A-Za-z ]+):\s*([\d,.]+)\s*(ms|s)/g)) {
      out.breakdown[m[1].trim()] = m[3] === 's' ? num(m[2]) * 1000 : num(m[2]);
    }
  }

  // Insight list, with the savings/wasted-bytes attributes that follow each name.
  const insightBlocks = text.split(/^\s{2}- insight name: /m).slice(1);
  for (const block of insightBlocks) {
    const name = block.match(/^(\w+)/)?.[1];
    if (!name) continue;
    const descBlock = block.split(/\n\s{2}- insight name:/)[0];
    out.insights.push({
      name,
      description: descBlock.match(/description: (.+)/)?.[1]?.trim() ?? '',
      wastedBytes: bytes(descBlock.match(/estimated wasted bytes: (.+)/)?.[1]),
      savings: descBlock.match(/estimated metric savings: (.+)/)?.[1]?.trim() ?? null,
    });
  }
  return out;
}

/** performance_analyze_insight returns a titled markdown dossier. */
export function parseInsight(text = '') {
  const title = text.match(/## Insight Title: (.+)/)?.[1]?.trim() ?? null;
  const summary = text.split('## Insight Summary:')[1]?.split('##')[0]?.trim() ?? '';
  const analysis = text.split('## Detailed analysis:')[1]?.split('## Estimated savings')[0]?.trim() ?? '';
  const savings = text.match(/## Estimated savings: (.+)/)?.[1]?.trim() ?? null;
  const urls = [...new Set([...analysis.matchAll(/https?:\/\/[^\s,)\]]+/g)].map((m) => m[0]))]
    .filter((u) => !u.includes('developer.chrome.com'))
    .slice(0, 12);
  const savingsBytes = Math.max(
    bytes(analysis.match(/Total potential savings: (.+)/i)?.[1]),
    bytes(analysis.match(/Potential savings: (.+)/i)?.[1]),
  );
  return { title, summary, analysis: analysis.slice(0, 1800), savings, urls, savingsBytes };
}

/* ─────────────────────────────── console ────────────────────────────────── */

export function parseConsole(text = '') {
  const messages = [];
  let current = null;
  for (const line of text.split('\n')) {
    const m = line.match(/^msgid=(\d+) \[(\w+)\] ([\s\S]*?)(?: \(\d+ args\))?(?: \[(\d+) times\])?$/);
    if (m) {
      current = { id: num(m[1]), type: m[2], text: m[3].trim(), count: m[4] ? num(m[4]) : 1, stack: [] };
      messages.push(current);
    } else if (current && /^\s{2,}\S/.test(line) && !/^Note:/.test(line.trim())) {
      current.stack.push(line.trim());
    }
  }
  // de-duplicate "Failed to load resource" repeats but keep the count
  return messages;
}

/* ─────────────────────────────── network ────────────────────────────────── */

export function parseNetwork(text = '') {
  const requests = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^reqid=(\d+) (\w+) (\S+) \[(\d+)\]/);
    if (m) requests.push({ id: num(m[1]), method: m[2], url: m[3], status: num(m[4]) });
  }
  return requests;
}

/* ────────────────────────────── lighthouse ──────────────────────────────── */

export function parseLighthouse(text = '') {
  const scores = {};
  for (const m of text.matchAll(/^- ([A-Za-z ]+): (\d+) \(([\w-]+)\)$/gm)) scores[m[3]] = num(m[2]);

  const failed = [];
  const section = text.split('### Failed audits')[1] ?? '';
  // Each audit is a bullet followed by its own indented block; keep the blocks
  // separate so a selector is never attributed to the wrong audit.
  const blocks = section.split(/\n(?=- [\w-]+ \[)/).filter((b) => /^- [\w-]+ \[/.test(b.trim()));
  for (const block of blocks) {
    const m = block.match(/^- ([\w-]+) \[([\w-]+)\]: ([\s\S]*?)(?: \(score: ([\d.]+)\))?(?:\n|$)/);
    if (!m) continue;
    const selector = block.match(/selector="([^"]+)"/)?.[1] ?? null;
    const nodes = num(block.match(/Nodes \((\d+)\)/)?.[1]);
    failed.push({ id: m[1], category: m[2], title: m[3].replace(/`/g, '').trim(), score: num(m[4]), selector, nodes });
  }
  return {
    scores,
    passed: num(text.match(/Passed: (\d+)/)?.[1]),
    failedCount: num(text.match(/Failed: (\d+)/)?.[1]),
    failed,
    timing: num(text.match(/Total timing: ([\d.]+)/)?.[1]),
    reportPaths: [...text.matchAll(/^- (\/\S+)$/gm)].map((m) => m[1]),
  };
}

/* ───────────────────────────────── heap ─────────────────────────────────── */

/** compare_heapsnapshots returns a CSV of per-class deltas. */
export function parseHeapDiff(text = '') {
  const rows = [];
  const totalLine = text.match(/Total: (.+)/)?.[1] ?? '';
  for (const line of text.split('\n')) {
    const m = line.match(/^(\d+),(.+),(\d+),(\d+),([+-]?\d+),([\d.,]+\s*\w+),([\d.,]+\s*\w+),([+-]?[\d.,]+\s*\w+)$/);
    if (m) {
      rows.push({
        index: num(m[1]),
        className: m[2],
        added: num(m[3]),
        removed: num(m[4]),
        countDelta: num(m[5]),
        addedBytes: bytes(m[6]),
        removedBytes: bytes(m[7]),
        sizeDelta: bytes(m[8]),
      });
    }
  }
  return {
    total: totalLine,
    rows,
    sizeDeltaBytes: bytes(totalLine.match(/size delta: ([+-]?[\d.,]+\s*\w+)/)?.[1]),
    objectDelta: num(totalLine.match(/# delta: ([+-]?\d+)/)?.[1]),
  };
}

export function parseHeapTable(text = '') {
  const lines = text.split('\n').filter((l) => /^\d+,/.test(l));
  const header = text.split('\n').find((l) => /^nodeId,|^id,/.test(l))?.split(',') ?? [];
  return lines.map((l) => {
    const cells = l.split(',');
    const row = {};
    header.forEach((h, i) => { row[h] = cells[i]; });
    return row;
  });
}

export function parseHeapCount(text = '') {
  const objects = num(text.match(/Objects: (\d+)/)?.[1]);
  const classes = num(text.match(/Showing \d+-\d+ of (\d+)/)?.[1]);
  return { objects: objects ?? classes, raw: text.slice(0, 400) };
}

/* ───────────────────────────── misc parsers ─────────────────────────────── */

export const parsePagesText = (text = '') =>
  text.split('\n').filter((l) => /^\s*\d+:/.test(l)).map((l) => {
    const m = l.match(/^\s*(\d+):\s*(.*?)(?:\s*\((.*?)\))?\s*(\[selected\])?/);
    return { id: num(m[1]), title: m[2], url: m[3] ?? '', selected: Boolean(m[4]) };
  });

/** take_snapshot is an indented a11y tree: `uid=1_49 button "Track flight SK118"` */
export function parseSnapshot(text = '') {
  const nodes = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^(\s*)uid=(\S+) ([\w-]+)(.*)$/);
    if (!m) continue;
    nodes.push({
      depth: Math.round(m[1].length / 2),
      uid: m[2],
      role: m[3],
      name: m[4].match(/"([^"]*)"/)?.[1] ?? '',
      raw: line.trim(),
    });
  }
  return nodes;
}

export function findUidByRoleName(nodes, role, nameRe) {
  const hit = nodes.find((n) => n.role === role && nameRe.test(n.name));
  return hit?.uid ?? null;
}

/* ───────────────────────── findings derivation ──────────────────────────── */

export const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

/**
 * Map (insight | console | network | heap | lighthouse) signals onto catalog ids.
 * Each detector returns null or a finding draft.
 */
export function insightToFinding(name, parsed, ctx = {}) {
  const base = {
    source: 'performance_analyze_insight',
    tool: 'performance_analyze_insight',
    insight: name,
    evidenceText: (parsed.analysis || parsed.summary || '').slice(0, 1400),
    urls: parsed.urls ?? [],
  };
  const mk = (o) => ({ category: 'performance', confidence: 'high', ...base, ...o });

  switch (name) {
    case 'ImageDelivery':
      return mk({
        gtId: 'lcp-image-bloat',
        title: 'LCP image is grossly oversized',
        severity: 'critical',
        metric: parsed.savingsBytes ? `${kb(parsed.savingsBytes)} of image bytes are avoidable` : null,
        recommendation: 'Ship a right-sized WebP/AVIF at display resolution and preload it.',
      });
    case 'LCPDiscovery':
      return mk({
        gtId: 'lcp-discovery',
        title: 'LCP image is not discoverable from the HTML',
        severity: 'high',
        recommendation: 'Add <link rel="preload" as="image" fetchpriority="high"> before render-blocking CSS.',
      });
    case 'RenderBlocking': {
      const urls = (ctx.allUrls?.length ? ctx.allUrls : parsed.urls) || [];
      const thirdParty = urls.find((u) => /thirdparty|analytics/.test(u));
      const polyfill = urls.find((u) => /polyfill/.test(u));
      const picks = [];
      if (thirdParty) picks.push(mk({
        gtId: 'render-blocking-third-party',
        title: 'Third-party analytics blocks first paint',
        severity: 'critical',
        urls: [thirdParty],
        recommendation: 'Load the tag async; it is not needed to paint the page.',
      }));
      if (polyfill) picks.push(mk({
        gtId: 'render-blocking-polyfill',
        title: 'Legacy polyfill bundle blocks the parser',
        severity: 'high',
        urls: [polyfill],
        recommendation: 'Delete the ES5 polyfills or ship them with defer and noModule.',
      }));
      if (!picks.length) picks.push(mk({ gtId: 'render-blocking-generic', title: 'Render-blocking requests on the critical path', severity: 'medium' }));
      return picks;
    }
    case 'CLSCulprits':
      return mk({
        gtId: 'cls-late-injection',
        title: 'Layout shifts from late content injection',
        severity: 'high',
        metric: ctx.cls != null ? `CLS ${ctx.cls}` : null,
        recommendation: 'Reserve space for the promo/advisory slot and inject inside it.',
      });
    case 'FontDisplay':
      return mk({
        gtId: 'font-display-missing',
        title: 'Webfonts block text rendering',
        severity: 'medium',
        recommendation: 'font-display: swap + preload the critical weight.',
      });
    case 'DOMSize':
      return mk({
        gtId: 'dom-size-bloat',
        title: 'DOM is far larger than the UI needs',
        severity: 'medium',
        metric: ctx.domNodes ? `${ctx.domNodes} nodes` : null,
        recommendation: 'Virtualise the fare matrix instead of rendering 1,200 rows.',
      });
    case 'ForcedReflow':
      return mk({
        gtId: 'forced-reflow-scroll',
        title: 'Forced synchronous layout during scroll',
        severity: 'medium',
        recommendation: 'Read layout once, write only transforms inside requestAnimationFrame.',
      });
    case 'INPBreakdown':
    case 'LongTasks':
      return mk({
        gtId: 'long-task-search',
        title: 'Long main-thread task on interaction',
        severity: 'high',
        recommendation: 'Remove the synchronous busy loop from the click path.',
      });
    case 'Cache':
      return mk({
        gtId: 'cache-policy',
        title: 'Static assets have no cache lifetime',
        severity: 'medium',
        recommendation: 'Immutable, content-hashed assets with max-age=31536000.',
      });
    case 'Viewport':
      return mk({
        gtId: 'viewport-meta',
        category: 'a11y',
        title: 'No viewport meta tag',
        severity: 'high',
        recommendation: 'Add width=device-width, initial-scale=1.',
      });
    case 'DocumentLatency':
      return mk({ gtId: 'ttfb-document', title: 'Document request latency', severity: 'low', confidence: 'low' });
    default:
      return mk({ gtId: `insight:${name}`, title: parsed.title || name, severity: 'low', confidence: 'low' });
  }
}

/** Lighthouse failed audits -> catalog ids. */
export function lighthouseToFindings(lh) {
  const out = [];
  for (const audit of lh.failed ?? []) {
    let gtId = null;
    let severity = 'medium';
    let category = audit.category === 'seo' ? 'seo' : 'a11y';
    if (audit.id === 'meta-viewport' || audit.id === 'viewport') { gtId = 'viewport-meta'; severity = 'high'; }
    else if (audit.id === 'color-contrast') { gtId = 'a11y-contrast'; severity = 'medium'; }
    else if (audit.id === 'label' || audit.id === 'form-field-multiple-labels') { gtId = 'a11y-form-labels'; severity = 'high'; }
    else if (audit.id === 'image-alt' || audit.id === 'input-image-alt') { gtId = 'a11y-image-alt'; severity = 'low'; }
    else if (audit.id === 'button-name' || audit.id === 'link-name') { gtId = 'a11y-icon-buttons'; severity = 'high'; }
    else if (audit.id === 'image-alt') { gtId = 'a11y-image-alt'; severity = 'low'; }
    else if (audit.id === 'meta-description') { gtId = 'seo-meta-description'; severity = 'low'; }
    else if (audit.id === 'select-name') { gtId = 'a11y-form-labels'; severity = 'high'; }
    else if (audit.id === 'html-has-lang') { gtId = 'a11y-html-lang'; severity = 'medium'; }
    else if (audit.id === 'td-has-header') { continue; }
    else if (audit.id === 'tap-targets' || audit.id === 'target-size') { gtId = 'a11y-icon-buttons'; severity = 'medium'; }
    else if (audit.id === 'heading-order' || audit.id === 'html-has-lang' || audit.id === 'document-title') { gtId = null; }
    if (!gtId) continue;
    out.push({
      gtId,
      title: audit.title,
      category,
      severity,
      source: 'lighthouse_audit',
      tool: 'lighthouse_audit',
      confidence: 'high',
      evidenceText: `${audit.id} (${audit.category}) — ${audit.title}${audit.selector ? `\nselector: ${audit.selector}` : ''}`,
      auditId: audit.id,
    });
  }
  return out;
}

/** Merge duplicate findings (same gtId) keeping the strongest evidence list. */
export function mergeFindings(findings) {
  const byId = new Map();
  for (const f of findings) {
    if (!f) continue;
    const key = f.gtId;
    if (!byId.has(key)) {
      byId.set(key, { ...f, evidence: [{ tool: f.tool, text: f.evidenceText, urls: f.urls ?? [] }] });
    } else {
      const cur = byId.get(key);
      cur.evidence.push({ tool: f.tool, text: f.evidenceText, urls: f.urls ?? [] });
      if (SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[cur.severity]) cur.severity = f.severity;
      cur.confidence = cur.confidence === 'high' || f.confidence === 'high' ? 'high' : cur.confidence;
      cur.metric = cur.metric || f.metric;
    }
  }
  return [...byId.values()].map((f) => ({ ...f, id: f.gtId }));
}
