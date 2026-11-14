# BLACKBOX

**An autonomous browser-forensics agent whose only browser-control interface is the official
[`chrome-devtools-mcp`](https://github.com/ChromeDevTools/chrome-devtools-mcp) server.** The optional WebGPU lab adds a small, separately labelled host preflight; it never pretends host diagnostics came from MCP.

It is dropped on a real web app it has never seen, in a fresh Chrome profile, throttled to Slow 4G
and a 4× CPU slowdown. In ~115 seconds and 75 MCP tool calls (156 JSON-RPC frames), it:

1. records a cold-load performance trace and drills into **every** insight Chrome raised,
2. drives the UI with real input to reproduce a runtime exception,
3. attributes 260 ms long tasks to a specific click handler,
4. diffs two heap snapshots to name the leaking class and the retaining path that keeps it alive,
5. runs Lighthouse for a11y/SEO,
6. then repeats the identical protocol against the repaired build and **proves** each fix with a
   re-measured delta.

It was never told what was wrong. There are **23 catalogued defects**; it finds **22 of them** and
proves **13/13** repairs, with the one miss printed in the report rather than hidden.

```
LCP            12,716 ms  →  1,813 ms      -86 %   (hero-legacy.png → hero-optimized.webp)
CLS                 0.08  →      0.00     -100 %
Transfer size      2.0 MB →    192 kB      -90 %
DOM nodes          8,515  →       204      -98 %
Console errors         3  →         0     -100 %
Long tasks / 5 clicks  5  →         0     -100 %
Heap growth        +250 FareCacheEntry objects → 0
Lighthouse a11y   70/100  →  100/100
Lighthouse SEO    60/100  →  100/100
Lighthouse agentic 0/100  →  100/100
```

---

## Why this is a demo of *toolchain mastery*, not of a chatbot

The SRE model is not in the loop. `agent/mission.mjs` is a deterministic policy — a written-down
investigation procedure — and every browser-side claim it makes cites the MCP tool call that produced
it. The optional WebGPU smoke mission is also deterministic: host diagnostics are clearly marked as
host evidence; browser interaction and capture still go through MCP. That makes both lanes auditable,
repeatable, and honest:

* **Every JSON-RPC frame is captured** (`runs/<id>/wire.jsonl`) and replayed in the UI's
  *MCP wire* tab, with latency and byte size per frame.
* **Tool schemas are fetched at handshake** and used to validate every call *before* it is sent;
  `pageId` is auto-injected only where the schema allows it.
* **Server → client requests are served** (`roots/list`), not ignored.
* **A failing tool is data, not an exception.** `emulate` returned a CDP error on Chrome 148; the
  run records the degradation, keeps measuring, and reports it — see *Honest limits*.
* **The agent can be wrong, and says so.** `forced-reflow-scroll` exists in the source but produced
  no Long Animation Frame over 50 ms, so Chrome's `ForcedReflow` insight never fired. The report
  marks it *inconclusive* with the LoAF measurement that justifies the claim.

---

## Run it

```bash
bash scripts/setup.sh          # Chrome for Testing 159 + Node 22 + the MCP server
node server.mjs                # needs Node 22 (heap tools use Promise.withResolvers)
# open http://localhost:8080  →  "Run mission"
```

Optional WebGPU lane (Colab/local GPU):

```bash
BLACKBOX_GPU_TOOLS=1 bash scripts/setup.sh  # adds vulkaninfo; does not install a GPU driver
# start server as above, then open WebGPU Lab → Refresh preflight → Run MCP WebGPU smoke
node agent/webgpu-smoke.mjs --origin http://127.0.0.1:8080
```

The default performance mission remains GPU-disabled. The WebGPU lane is opt-in and reports host and browser evidence separately. See [docs/WEBGPU-LAB.md](docs/WEBGPU-LAB.md) and [the Colab notebook](notebooks/BLACKBOX_WebGPU_Colab.ipynb).

X3D Lab (X_ITE dev console, Python generators, the Blender fork through bpy, and an x3dlab MCP server):

```bash
npm run x3d:build              # 7 original X3D 4.0 scenes × XML / ClassicVRML / JSON, validated
node server.mjs                # open http://127.0.0.1:8080/lab/x3d/  (or the X3D Lab tab)
npm run x3d:mission            # x3dlab MCP + chrome-devtools-mcp → runs/x3d-*/report.html
BLACKBOX_X3D_BLENDER=1 bash scripts/setup.sh   # optional: bpy + x3d-blender-io for the Blender leg
```

See [docs/X3D-LAB.md](docs/X3D-LAB.md).

Headless / CI:

```bash
node agent/run-cli.mjs --origin http://127.0.0.1:8080 --min-recall 0.8
# exits non-zero if the agent stops finding what it used to find
```

That last flag is the point: this is a **regression test for the agent**, scored against an answer
key (`agent/catalog.mjs`) written before the agent existed.

---

## What's in the box

```
server.mjs                 one port: target app + control plane + SSE + artifacts
lib/mcp-client.mjs         dependency-free MCP client (handshake, roots, schema validation, wire capture)
agent/mcp-process.mjs      exactly how the server is launched, and why each flag
agent/mission.mjs          the investigation policy — 16 phases, every step a tool call
agent/heuristics.mjs       parsers for real tool output + insight→finding mapping
agent/catalog.mjs          the 23-defect answer key
agent/report.mjs           self-contained HTML flight recorder (inline CSS, base64 evidence, SVG charts)
agent/explorer.mjs         long-lived session behind the UI's Explorer tab
agent/run-cli.mjs          headless runner with a recall gate
agent/gpu-preflight.mjs    host-side device / NVIDIA / Vulkan attestation, kept separate from MCP
agent/webgpu-smoke.mjs     MCP-first WebGPU compute correctness run + report + wire log
site/webgpu/               local, deterministic WGSL and WebGL2 smoke fixture (no model downloads)
notebooks/                 Colab T4 notebook that uploads this ZIP and runs the MCP WebGPU smoke
site/x3d/                  X3D Lab dev console: X_ITE + SAI tree, inspector, REPL, split view vs the Blender glb
x3d/x3dlab/                stdlib Python: X3D builder, 7 scenes, X3DUOM validator, fidelity predictor,
                           glb inspector, bpy Blender leg, and an MCP server over all of it
agent/x3d-mission.mjs      X3D mission: library → x3dlab MCP → chrome-devtools-mcp → report
docs/X3D-LAB.md            the four lanes, the scenes, what the mission measures, sharp edges
site/broken/               the patient: a dispatch console with 23 defects
site/fixed/                the same product, repaired — same structure, so deltas are attributable
ui/                        Mission Control (vanilla JS, SSE, no framework, no CDN), plus WebGPU Lab
docs/MCP-CHEATSHEET.md     field notes: wire format, flags, every sharp edge we hit
docs/WEBGPU-LAB.md         Colab/T4 workflow, evidence boundaries, and GPU-test heuristics
docs/DEFECT-CATALOG.md     the defects and how they are scored
runs/<id>/                 one run: report.html, summary.json, findings.json, wire.jsonl,
                           traces, heapsnapshots, screenshots, and optional GPU evidence
```

## Mission Control screens

| Tab | What it shows |
|---|---|
| **Mission** | phase timeline, live MCP traffic with per-call latency/bytes, agent log, before/after KPIs |
| **Findings** | severity-sorted findings with the raw tool evidence, proof table, screenshots, honest limits |
| **MCP wire** | every JSON-RPC frame (direction, method, ms, bytes) + a per-tool ledger with context cost |
| **Explorer** | the server's own tool surface: pick any of 46 tools, edit its JSON-Schema-shaped args, call it live |
| **WebGPU Lab** | independent host preflight, opt-in MCP-driven WGSL compute check, wire calls, and report |
| **Report** | the generated flight recorder, embedded |

## Deliberate engineering decisions

* **The before/after is an identical protocol.** Same emulation, same `initScript` observers, same
  interaction script, same heap churn (10 searches), same Lighthouse mode. The fixed build is not
  measured on a warm cache or a quieter machine.
* **No test hooks in the app.** The agent cannot read `__skyline` to cheat; where it does touch page
  globals (e.g. `window.gc()`), it is a documented DevTools technique, not a fixture shortcut.
* **Context is budgeted.** A `performance_stop_trace` response is ~10 kB of markdown; the mission
  keeps 1.8 kB per insight and totals ~175 k tokens across 75 tool calls. Every tool's cost is in the
  ledger, so "which tool is eating the context window" is a lookup, not a guess.
* **The report is self-contained.** Inline CSS, base64 images, hand-built SVG waterfall. Email it,
  archive it in CI, open it offline.

## Known limits

* **`forced-reflow-scroll` is not observable here** (documented above). The other 22 defects produce
  external signals in a headless trace.
* **`emulate` / `lighthouse_audit` fail on Chrome 148** with `chrome-devtools-mcp@1.13.0`
  (`Network.emulateNetworkConditionsByRule` deserialisation). Chrome 159 is pinned in
  `agent/mcp-process.mjs`. The failure is reproduced and explained in `docs/MCP-CHEATSHEET.md`.
* **Heap snapshots are 5–9 MB each**; the run keeps one per build and deletes the baselines.
* **The WebGPU lane is implemented but not physically validated on a discrete GPU in this workspace.**
  Its latest smoke run passed the 1,024-element known-answer compute on Chrome's **SwiftShader** adapter
  (0 mismatches); host Vulkan reports CPU-only llvmpipe, with no `/dev/dri` device or `nvidia-smi`.
  That is a software WebGPU pass—not a T4/hardware pass. Run the Colab notebook on a T4 to validate
  physical-GPU execution. No model benchmark or VRAM measurement is claimed.
* **Numbers move between runs** (±5 % on LCP; CLS 0.04–0.08) because this is a real browser under
  real throttling on a 2-vCPU box. Deltas are stable; decimals are not.
