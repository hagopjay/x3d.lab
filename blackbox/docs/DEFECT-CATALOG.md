# The answer key

`site/broken/` is a realistic dispatch console with **23 deliberate defects**. They were written
first and catalogued in `agent/catalog.mjs` *before* any agent logic existed, so the run can be
scored honestly afterwards.

The agent never reads this file, the catalog, or the source. It only gets the MCP tool surface and
a URL.

| # | id | where | what |
|---|---|---|---|
| 1 | `lcp-image-bloat` | index.html | LCP hero is a 1.4 MB PNG at 1440×810 displayed at 1280×720 |
| 2 | `lcp-discovery` | index.html | no `<link rel=preload>`, no `fetchpriority`, discovered after CSS + blocking JS |
| 3 | `render-blocking-third-party` | index.html | cross-origin `analytics.js` in `<head>`, busy-waits 450 ms |
| 4 | `render-blocking-polyfill` | index.html | 26 KB ES5 polyfill bundle, parser-blocking, unreachable in Chrome |
| 5 | `cls-late-injection` | app.js | promo inserted at 1.2 s, advisory at 2.6 s, above the fold |
| 6 | `font-display-missing` | styles.css | three `@font-face` blocks, no `font-display` |
| 7 | `dom-size-bloat` | app.js | 1,200 hidden `<tr>` → ~8,400 nodes |
| 8 | `forced-reflow-scroll` | app.js | `offsetHeight` read + style write per card, per scroll event |
| 9 | `long-task-search` | app.js | 260 ms synchronous busy-loop on every search click |
| 10 | `console-404-asset` | app.js | `/shared/img/fleet-map.png` does not exist |
| 11 | `console-500-pricing` | app.js + server | `/api/flaky` returns 500; `.then()` chain never checks `response.ok` |
| 12 | `runtime-type-error` | app.js | `box.dataset.flight.length` on the Track button |
| 13 | `memory-leak-cache` | app.js | module-level `ledger` array, `FareCacheEntry` per tick, never evicted |
| 14 | `memory-leak-listeners` | app.js | `addEventListener('resize')` inside `setInterval` |
| 15 | `detached-dom` | app.js | `cards.cloneNode(true)` retained in `__skyline.orphans` |
| 16 | `cache-policy` | server | `/site/broken/*.js` served with `no-store` |
| 17 | `viewport-meta` | index.html | no viewport meta |
| 18 | `a11y-html-lang` | index.html | `<html>` without `lang` |
| 19 | `a11y-icon-buttons` | index.html | two 18×18 icon buttons, `::before` glyph only, no name |
| 20 | `a11y-contrast` | styles.css | `#5b6472` on `#0e1626` = 3.1:1 |
| 21 | `a11y-form-labels` | index.html | four placeholder-only inputs, plus an unlabelled `<select>` |
| 22 | `a11y-image-alt` | index.html | hero `<img>` with no `alt` attribute at all |
| 23 | `seo-meta-description` | index.html | no meta description |

## The repair

`site/fixed/` is the same product, repaired. Deliberately *not* a rewrite: the DOM structure,
copy and component boundaries are identical, so every delta in the report is attributable to one
of the fixes above rather than to a redesign. The only structural changes are the ones the fixes
require (a `.promo-slot` with reserved height, a virtualised list, a hidden `<ul>` instead of a
hidden `<table>`).

Serving rules are part of the fixture: `/site/broken/*` is `no-store`, `/site/fixed/*` is
`max-age=31536000, immutable`, and `/shared/img/hero-legacy.png` is never cached.

## Scoring

```
recall    = catalogued defects with a matching finding / 23
precision = findings whose evidence names a tool / all findings
proof     = checks that flipped in the fixed build / all checks
```

A missed defect lowers recall and is printed in the report under **Honest limits** — it is never
silently dropped. `forced-reflow-scroll` is the current known miss: it exists in the code, but no
Long Animation Frame exceeded Chrome's 50 ms reporting threshold, so no DevTools insight fires.
The report says exactly that.
