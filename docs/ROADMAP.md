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

## Phase 2: 02 Lab (this commit)

* `blender.py` + `blender_worker.py`: the `bpy` 5.2.3 wheel when Python is 3.13, else the Blender 5.2 LTS tarball; the add-on at its pin; one worker for probe, round trip and render. Cycles backend probing (OptiX › CUDA › HIP › oneAPI › CPU, counting only real GPU devices), a GPU render semaphore, `nvidia-smi` sampling (utilisation, VRAM, power) with per-render peaks, temp-dir cleanup, and each task's own Blender output attached to it with a one-line `reason` (a broken file now says `SAXParseException: …:1:12: unclosed token`, not "import/export error: ?").
* `scenegen.py`: six deterministic families (primitives, meshes, lights, materials, hierarchy, animated), all valid in x3d_mcp.
* `fidelity.py`: snapshot comparison that survives renaming (match by name, then by type and position), compares light *direction* rather than quaternions, treats seam splits as no loss (unique vertex positions), and detects lost instancing.
* `stress.py`: RunConfig (JSON, seed, run name), presets scaled to the GPU class, tiers validate / roundtrip / drift / render / load, run card + HTML report, `compare_cards`.
* Dashboard: a Lab run panel (start a run, tier counts, findings, render gallery, GPU graph); Blender spans on the DevTools timeline.
* Measured on the pinned add-on (d9bbf91), CPU, `standard` preset (90 checks, 75 s):
  * every Blender export validates in x3d_mcp;
  * the light-energy round-trip drift is fixed on the 4.0 / 4.1 paths (import multiplies by the 1.75 export divides by);
  * the X3D 3.3 path clamps intensity to 1.0: a light at 2.5 keeps 40 % of its energy, permanently, after one round trip;
  * the 4.0 / 4.1 paths lose DEF/USE instancing: 2 shared meshes come back as N separate copies (3.3 keeps them shared).
* Deferred: optional provenance stamping (private module) moves to Phase 3 with the other private integrations.

## Phase 3: 03 Stress & Research

* A grammar-aware differential fuzz tier with a minimiser (private module, loaded with your token).
* Copilot validator vs x3d_mcp vs X_ITE on one corpus, through `window.__x3dcopilot` in headless Chrome.
* X_ITE vs Cycles silhouette overlap from the same Viewpoint.
* Very large scenes as scale fixtures on a G4.
* Chrome DevTools traces captured by CDP into the report; chrome-devtools-mcp next to x3d_mcp for an agent that profiles what it writes.

## Phase 4: regression harness

* `compare(baseline, run)`: status flips, new fuzz signatures, p95 latency and drift changes; pass/fail gates.
* A CPU-only `ci` preset for GitHub Actions in x3d_mcp, x3d-blender-io and the other x3d repos; GPU tiers stay in Colab.
