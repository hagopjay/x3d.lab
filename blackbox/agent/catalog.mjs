/**
 * catalog.mjs — the ANSWER KEY.
 *
 * These are the defects that exist in `site/broken/`, written down once, from the
 * source, before the agent ever runs. The mission never reads this file at
 * investigation time; it exists so the run can be *scored* afterwards:
 *
 *   recall    = defects the agent found on its own / defects that exist
 *   precision = findings backed by tool evidence / all findings
 *
 * If a detector stops firing, recall drops and the run fails loudly. That is the
 * point: this doubles as a regression test for the MCP toolchain itself.
 */

export const CATALOG = [
  {
    id: 'lcp-image-bloat',
    title: 'LCP hero image is a 1.4 MB PNG served at display resolution',
    category: 'performance',
    severity: 'critical',
    sourceFile: 'site/broken/index.html',
    symptom: 'Largest Contentful Paint is dominated by an oversized, unoptimised PNG.',
    fix: 'Serve a right-sized WebP/AVIF, add width/height, preload with fetchpriority=high.',
  },
  {
    id: 'lcp-discovery',
    title: 'LCP image is not discoverable early',
    category: 'performance',
    severity: 'high',
    sourceFile: 'site/broken/index.html',
    symptom: 'No preload hint; the hero is found only after CSS and parser-blocking script.',
    fix: '<link rel="preload" as="image"> in the head, before stylesheets.',
  },
  {
    id: 'render-blocking-third-party',
    title: 'Third-party analytics tag blocks the parser',
    category: 'performance',
    severity: 'critical',
    sourceFile: 'site/broken/index.html',
    symptom: 'Synchronous cross-origin <script> in <head> burns ~450 ms of main-thread time before first paint.',
    fix: 'Load async (or defer + consent-gated injection).',
  },
  {
    id: 'render-blocking-polyfill',
    title: '26 KB of ES5 polyfills shipped parser-blocking',
    category: 'performance',
    severity: 'high',
    sourceFile: 'site/broken/index.html',
    symptom: 'Legacy bundle is render-blocking and unreachable in an evergreen browser.',
    fix: 'Delete the polyfill bundle, or serve it module/noModule-scoped with defer.',
  },
  {
    id: 'cls-late-injection',
    title: 'Promo and advisory blocks injected above the fold after paint',
    category: 'performance',
    severity: 'high',
    sourceFile: 'site/broken/app.js',
    symptom: 'Two blocks are inserted at 1.2 s and 2.6 s, pushing the hero and search panel down.',
    fix: 'Reserve height in markup (.promo-slot) and swap content in place.',
  },
  {
    id: 'font-display-missing',
    title: '@font-face blocks text rendering (no font-display)',
    category: 'performance',
    severity: 'medium',
    sourceFile: 'site/broken/styles.css',
    symptom: 'Three webfonts load without font-display, so text is invisible until they arrive.',
    fix: 'font-display: swap plus preload of the critical weight.',
  },
  {
    id: 'dom-size-bloat',
    title: '1,200 hidden table rows add ~8,400 DOM nodes',
    category: 'performance',
    severity: 'medium',
    sourceFile: 'site/broken/app.js',
    symptom: 'Style, layout and memory costs scale with a DOM the user never sees.',
    fix: 'Virtualise the list (render a page of rows, keep the rest as data).',
  },
  {
    id: 'forced-reflow-scroll',
    title: 'Scroll handler interleaves layout reads and writes',
    category: 'performance',
    severity: 'medium',
    sourceFile: 'site/broken/app.js',
    symptom: 'offsetHeight is read per card, then style is written, forcing synchronous layout.',
    fix: 'Batch reads, write only transforms, and gate on requestAnimationFrame.',
  },
  {
    id: 'long-task-search',
    title: 'Search handler blocks the main thread for ~260 ms',
    category: 'performance',
    severity: 'high',
    sourceFile: 'site/broken/app.js',
    symptom: 'A synchronous busy-loop runs on every search, delaying the next paint.',
    fix: 'Move work off the click path (chunk, worker, or just remove it).',
  },
  {
    id: 'console-404-asset',
    title: 'Missing asset: /shared/img/fleet-map.png 404s',
    category: 'console',
    severity: 'low',
    sourceFile: 'site/broken/app.js',
    symptom: 'A console error on every load; the fleet map is blank.',
    fix: 'Ship the asset (or point at one that exists).',
  },
  {
    id: 'console-500-pricing',
    title: 'Surge-pricing fetch 500s and the promise chain never handles it',
    category: 'console',
    severity: 'high',
    sourceFile: 'site/broken/app.js',
    symptom: 'Failed request plus an unhandled rejection; prices silently stay stale.',
    fix: 'Check response.ok, catch, and degrade to cached fares with a visible notice.',
  },
  {
    id: 'runtime-type-error',
    title: 'Clicking "Track flight" throws a TypeError',
    category: 'console',
    severity: 'critical',
    sourceFile: 'site/broken/app.js',
    symptom: "Cannot read properties of undefined (reading 'length') at trackFlight.",
    fix: 'Default the dataset value and render a real status.',
  },
  {
    id: 'memory-leak-cache',
    title: 'Unbounded FareCacheEntry cache grows forever',
    category: 'memory',
    severity: 'critical',
    sourceFile: 'site/broken/app.js',
    symptom: 'A module-level array keeps every entry alive; nothing evicts.',
    fix: 'Bound the cache (LRU/expiry) — 40 entries in the fixed build.',
  },
  {
    id: 'memory-leak-listeners',
    title: 'A new resize listener is registered every second',
    category: 'memory',
    severity: 'high',
    sourceFile: 'site/broken/app.js',
    symptom: 'Listener closures accumulate; each retains a ~24 KB payload.',
    fix: 'Register once; remove on unload.',
  },
  {
    id: 'detached-dom',
    title: 'Cloned fare cards are retained after being replaced',
    category: 'memory',
    severity: 'medium',
    sourceFile: 'site/broken/app.js',
    symptom: 'Each search leaves a detached subtree referenced from __skyline.orphans.',
    fix: 'Do not retain clones; let the old subtree be collected.',
  },
  {
    id: 'cache-policy',
    title: 'Static assets ship with no cache lifetime',
    category: 'performance',
    severity: 'medium',
    sourceFile: 'server.mjs (lab fixture)',
    symptom: 'JS/CSS/image responses have no max-age, so every visit revalidates.',
    fix: 'Immutable, content-hashed asset URLs with a one-year max-age.',
  },
  {
    id: 'viewport-meta',
    title: 'No viewport meta tag',
    category: 'a11y',
    severity: 'high',
    sourceFile: 'site/broken/index.html',
    symptom: 'Mobile browsers render a shrunken desktop layout at 980 px.',
    fix: '<meta name="viewport" content="width=device-width, initial-scale=1">',
  },
  {
    id: 'a11y-html-lang',
    title: 'The document has no lang attribute',
    category: 'a11y',
    severity: 'medium',
    sourceFile: 'site/broken/index.html',
    symptom: '<html> has no lang, so screen readers guess the pronunciation language.',
    fix: '<html lang="en">',
  },
  {
    id: 'a11y-icon-buttons',
    title: 'Icon buttons have no accessible name',
    category: 'a11y',
    severity: 'high',
    sourceFile: 'site/broken/index.html',
    symptom: 'Two 18×18 buttons announce as "button" with no label.',
    fix: 'aria-label plus a 44×44 hit target.',
  },
  {
    id: 'a11y-contrast',
    title: 'Muted price metadata fails contrast (3.1:1)',
    category: 'a11y',
    severity: 'medium',
    sourceFile: 'site/broken/styles.css',
    symptom: '.price .meta uses #5b6472 on #0e1626.',
    fix: 'Raise to #a3b0c7 (7.4:1).',
  },
  {
    id: 'a11y-form-labels',
    title: 'Search inputs are placeholder-only',
    category: 'a11y',
    severity: 'high',
    sourceFile: 'site/broken/index.html',
    symptom: 'Four controls have no <label> or aria-label.',
    fix: 'Add visible labels bound with for/id.',
  },
  {
    id: 'a11y-image-alt',
    title: 'The hero image has no alt attribute at all',
    category: 'a11y',
    severity: 'low',
    sourceFile: 'site/broken/index.html',
    symptom: 'The LCP image is unlabelled, so screen readers announce nothing for it.',
    fix: 'Descriptive alt text on content images.',
  },
  {
    id: 'seo-meta-description',
    title: 'No meta description',
    category: 'seo',
    severity: 'low',
    sourceFile: 'site/broken/index.html',
    symptom: 'Lighthouse SEO score penalised; search snippets are generated from body text.',
    fix: 'Add a 120–155 character meta description.',
  },
];

export const CATALOG_BY_ID = Object.fromEntries(CATALOG.map((c) => [c.id, c]));

/** Defects we expect DevTools to be able to see from the outside, per category. */
export const EXPECTED_BY_CATEGORY = CATALOG.reduce((acc, c) => {
  acc[c.category] = (acc[c.category] || 0) + 1;
  return acc;
}, {});
