# chrome-devtools-mcp — field notes

Everything here was verified against **chrome-devtools-mcp 1.13.0**, **Chrome for Testing 159**, **MCP protocol 2025-06-18**, **Node 22**.
Where the server behaves differently from the docs, the difference is recorded — those are the notes that save you an afternoon.

---

## 1. The wire

stdio transport, newline-delimited JSON-RPC 2.0. One message per line. No framing headers.

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","serverInfo":{"name":"chrome_devtools","version":"1.13.0"},"capabilities":{...}}}
→ {"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
→ {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[ ...46 tools... ]}}
```

**The server calls the client back.** During the handshake it issues `roots/list` as a *request* to you:

```json
{"jsonrpc":"2.0","id":1,"method":"roots/list"}
```

If your client ignores it, filesystem-scoped tools fall back to the OS temp directory. Answer it:

```json
{"jsonrpc":"2.0","id":1,"result":{"roots":[{"uri":"file:///home/user/project","name":"workspace"}]}}
```

`--workspace=/path` is the flag-level equivalent for clients that do not implement roots.

---

## 2. Launch flags that matter

```bash
chrome-devtools-mcp \
  --headless --isolated \                 # ephemeral profile: no state bleed between runs
  --memoryDebugging \                     # unlocks the 13 heap-snapshot tools
  --executablePath /path/to/chrome \
  --viewport 1280x800 \                   # deterministic layout for screenshots
  --no-usage-statistics --no-performance-crux \
  --chromeArg=--no-sandbox --chromeArg=--disable-dev-shm-usage \
  --chromeArg=--js-flags=--expose-gc      # lets evaluate_script call gc() before a heap snapshot
```

The default `cpu` profile intentionally includes `--disable-gpu`. The optional WebGPU smoke runner selects a separate `webgpu` profile: it omits that flag and adds the Colab-inspired Vulkan/WebGPU switches (`--use-angle=vulkan`, `--enable-features=Vulkan`, `--enable-unsafe-webgpu`, etc.). This is an isolated lab-only profile; see [WEBGPU-LAB.md](WEBGPU-LAB.md) for the evidence boundary and Colab workflow.

`--pageIdRouting` is **on by default** in 1.13.0: every page-scoped tool now *requires* a `pageId`.
Get it from `new_page` / `list_pages` — parse the line marked `[selected]`, not the first line
(page 1 is usually `about:blank`):

```
## Pages
1: about:blank
2: SKYLINE — Dispatch (http://127.0.0.1:8080/lab/broken/) [selected] isolatedContext=probe
```

---

## 3. Version compatibility (the sharp edges)

| Symptom | Cause | Fix |
|---|---|---|
| `Protocol error (Network.emulateNetworkConditionsByRule): … mandatory field missing: offline` | Server 1.13.0 vs **Chrome 148** CDP drift. Breaks `emulate` *and* `lighthouse_audit`. | Use a current Chrome (159 works). |
| `TypeError: Promise.withResolvers is not a function` from `compare_heapsnapshots`, `get_heapsnapshot_summary`, `query_heapsnapshot_objects` | Heap tools require **Node ≥ 22** | Run the *server* on Node 22+. |
| `invalid arguments … pageId: expected number, received undefined` | page-id routing on | pass `pageId` |
| `isolatedContext: expected string, received boolean` | schema is a *name*, not a flag | `isolatedContext: "run-42"` |
| `No snapshot found for page N. Use take_snapshot…` | `get_css_styles` needs a current a11y snapshot | `take_snapshot` first, use its `uid`s |

The demo records these as first-class data: `summary.json → degraded[]` and `literal` evidence in the report.

---

## 4. Tool families actually used

### Performance
```
performance_start_trace {pageId, reload, autoStop, ignoreCache, filePath}
performance_stop_trace  {pageId, filePath}
performance_analyze_insight {pageId, insightSetId, insightName}
lighthouse_audit {pageId, mode: navigation|snapshot, device, outputDirPath}
```

* `autoStop: true` returns the whole summary — metrics **and** the insight catalogue — in one response.
* `autoStop: false` returns immediately; you then drive the page and call `performance_stop_trace`
  to close the window. **Insights are only available between a start and a stop** — once you navigate,
  or start a new trace, the old ones are gone ("No recorded traces found"). Analyse *before* moving on.
* `insightSetId` comes from `## insight set id: NAVIGATION_0` in the summary. Don't invent it.
* The summary is markdown, not JSON. Parse lines like `  - LCP: 9554 ms` and
  `  - insight name: RenderBlocking`.

The 18 insight names that can appear:
`Cache, CLSCulprits, DocumentLatency, DOMSize, DuplicatedJavaScript, FontDisplay, ForcedReflow,
ImageDelivery, INPBreakdown, LCPBreakdown, LCPDiscovery, LegacyJavaScript, ModernHTTP,
NetworkDependencyTree, RenderBlocking, SlowCSSSelector, ThirdParties, Viewport`

### Input (needs a11y `uid`s)
```
take_snapshot {pageId}                     → uid=1_49 button "Track flight SK118"
click {pageId, uid}
```
This is the correct way to drive a page: uids come from the accessibility tree, so a click is
equivalent to "the thing a screen reader would call the Track flight button".

### Console
```
list_console_messages {pageId, types:['error','warn'], includeStackTraces:true}
```
```
msgid=4 [error] Uncaught TypeError: Cannot read properties of undefined (reading 'length') (0 args)
  at trackFlight (app.js:144:34)
```
`msgid=… [type] text (N args)` — the stack follows on indented lines.

### Network
```
list_network_requests {pageId}          → reqid=22 GET http://…/fleet-map.png [404]
get_network_request {pageId, reqid}     → status, request headers, response headers
```
`reqid`s are per-page and reset on navigation. The response-header block can be long — split on
`### Response Headers` before looking for `cache-control`, or you will read request headers and
conclude "no cache" about a perfectly cached asset.

### Memory (file-based, not page-based)
```
take_heapsnapshot {pageId, filePath}
compare_heapsnapshots {baseFilePath, currentFilePath, pageSize}
query_heapsnapshot_objects {filePath, className}
get_heapsnapshot_retaining_paths {filePath, nodeId, maxDepth}
get_heapsnapshot_details {filePath, filterName:'objectsRetainedByDetachedDomNodes'|'objectsRetainedByEventHandlers'|…}
```
No `pageId` on the analysis calls — they read the `.heapsnapshot` file. `compare_heapsnapshots`
returns CSV: `index,className,addedCount,removedCount,countDelta,addedSize,removedSize,sizeDelta`.
Detached nodes appear as class names like `Detached <div>`.

### Screenshots / emulation
```
take_screenshot {pageId, format: png|jpeg|webp, quality, fullPage, uid, filePath}
emulate {pageId, networkConditions:'Slow 4G'|'Fast 3G'|…, cpuThrottlingRate: 1-20, colorScheme, geolocation, userAgent}
resize_page {pageId, width, height}
```

---

## 5. What the agent still has to do itself

MCP gives you DevTools primitives; it does not give you judgement. In this project the following
are *page-level* measurements taken with `evaluate_script`, because no single tool exposes them:

* LCP **element** identity — `PerformanceObserver('largest-contentful-paint').element`
  (installed via `navigate_page`'s `initScript`, before any page script runs).
* Long-task attribution to a click path — `PerformanceObserver('longtask')` around real clicks.
* Long Animation Frames — `PerformanceObserver('long-animation-frame')` for
  `forcedStyleAndLayoutDuration` per script. Frames < 50 ms are never reported; say "inconclusive"
  rather than guessing.
* Node-heavy subtrees — the Elements panel equivalent, computed in one upward walk.
* Per-resource waterfall — `performance.getEntriesByType('resource')`, including
  `renderBlockingStatus` and `transferSize`.

The rule the demo follows: **one tool call per fact, and every claim cites the tool that produced it.**

---

## 6. Failure modes worth handling in your client

1. **Transient protocol errors.** `Protocol error (Target closed)` / `Target detached` happen when
   the page is mid-navigation. Retry once or twice with backoff; do not fail the whole run.
2. **A failing tool is data.** `emulate` returning an error is a finding about the environment.
   Record it, degrade, and keep measuring.
3. **Schema validation before sending.** The server publishes JSON Schema for all 46 tools. Validate
   `required`, types and `enum` locally — a malformed call costs a round trip and a confusing error.
4. **Budget the context.** One `performance_stop_trace` response is ~10 kB of markdown; a heap diff is
   bigger. Trim before it reaches a model: the mission keeps 1.8 kB per insight and ~175 k tokens total
   in the reference run across 75 MCP tool calls.
5. **Close the browser.** `--isolated` profiles are cleaned on exit; a killed process leaves Chrome
   children behind. Send SIGTERM, then SIGKILL after a grace period.
