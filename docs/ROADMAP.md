# Roadmap

Teaching first, then a regression harness, with enough rigour that the numbers can be cited.

## Phase 1: foundation and 01 Quickstart (this commit)

* `x3dlab` package: pins, runtime and GPU detection, process manager, event bus (levels, densities, redaction; notebook panel, JSONL, SSE), x3d_mcp client and verdict, provider adapters for OpenAI, Anthropic and Gemini over REST with tool calling, background jobs with spend guards, gateway, tunnels, private Copilot loader.
* Fixes carried over from `x3d_duo.ipynb`:
  * an older server holding port 8000 made the health check pass while the new one had exited with code 3: start now frees the port and health only counts the process it started;
  * the coordinator answered 500 to a non-ASCII key, bad JSON, a list body, `rounds='x'`, `writer='none'` and a NUL byte in a path: all 4xx now, with tests;
  * scenes over 4 MiB made `/api/run` fail with 413 → 500: large documents go through the fork's `/blob` store;
  * `/pulse` and `/mcp` were not reachable on the app port: the gateway proxies `/mcp` and `/blob` behind a bearer token;
  * the MCP server listened on 0.0.0.0: loopback only, with the SDK's host and origin checks on;
  * cloudflared's stderr pipe was left unread after the URL was found (it can stall): its log goes to a file;
  * the ngrok secret name differed between the docs and the code: `NGROK_AUTHTOKEN`, with `NGROK` still accepted;
  * Claude was not supported and the OpenAI default model was hard-coded: all three providers, models listed live;
  * pins: fork `13121b6`, add-on `d9bbf91` (2.6.4), X_ITE 16.7.0 everywhere, `bpy` 5.2.3.

## 00 Basic MCP

* Self-contained: install (uv or pip), start on loopback, a token-checking proxy, cloudflared or ngrok, then a smoke test of the protocol, every tool (stateful ones as chains), validator verdicts on 2 good and 16 broken scenes, concurrent load, 0.5–20 MB documents and hostile input, and a JSON report.
* `SERVER = "consortium"` runs upstream with `mcp<2` pinned. On upstream `main` (Oct 2026) two defects slip through: a ROUTE before its DEF, and PBR outside the declared profile.
* Found while testing: in the fork, `render_current_scene` returns a blank frame for a scene that `render_image` draws when given the same XML (from `get_scene`). The smoke test cross-checks this automatically.
* The fork's render tools load X_ITE from jsdelivr. `LOCAL_XITE` serves it from the VM instead; the local server needs CORS headers because X_ITE fetches its CSS and WebAssembly cross-origin.

## Phase 2: 02 Lab (L4 / G4)

* One Blender worker (round trip, render, stress ladder) with GPU probing OptiX → CUDA → CPU, a GPU render queue, VRAM sampling, temp-dir cleanup, and the importer's own error messages instead of "import/export error: ?".
* The stress test as a control panel: presets (smoke, heavy, 50k, custom) scaled to the detected GPU; every tier on/off with its own settings; a JSON config with a seed; a run card per run.
* Fidelity beyond counts (bounding boxes, vertex hashes, materials, transforms) and multi-generation drift; the light-energy drift shown as a strip until a decision is made.
* Optional provenance stamping on each step, checked after each Blender round trip (private module, loaded with your token).

## Phase 3: 03 Stress & Research

* A grammar-aware differential fuzz tier with a minimiser (private module, loaded with your token).
* Copilot validator vs x3d_mcp vs X_ITE on one corpus, through `window.__x3dcopilot` in headless Chrome.
* X_ITE vs Cycles silhouette overlap from the same Viewpoint.
* Very large scenes as scale fixtures on a G4.
* Chrome DevTools traces captured by CDP into the report; chrome-devtools-mcp next to x3d_mcp for an agent that profiles what it writes.

## Phase 4: regression harness

* `compare(baseline, run)`: status flips, new fuzz signatures, p95 latency and drift changes; pass/fail gates.
* A CPU-only `ci` preset for GitHub Actions in x3d_mcp, x3d-blender-io and the other x3d repos; GPU tiers stay in Colab.
