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

## Phase 3: 03 Research (this commit)

* `uom.py`: the X3DUOM 4.0 as a grammar (260 concrete nodes; field types, ranges incl. exclusive bounds, enumerations, which node types each SFNode/MFNode field accepts, containerField defaults) plus profile node sets from the X3D-Edit profile file.
* `fuzz.py`: generator, 27 operators (20 reject, 7 metamorphic), oracles (x3d_mcp fork and consortium, lxml + X3D 4.0 XSD, X_ITE, Copilot, Blender importer), recall / specificity / Cohen's κ, buckets, HDD minimiser (subtrees level by level, then attributes; the defect's element, its attributes and any DEF it refers to are protected, so a bucket nobody catches cannot minimise its defect away). Deterministic per seed. Built only on public Web3D data, so it lives in the public package rather than a private module as planned.
* `browser.py`: Playwright Chromium, SwiftShader WebGL, the pinned X_ITE served offline through `page.route`, timings as DevTools track entries, traces and `Performance.getMetrics`, silhouettes / IoU / overlays. Background pages no longer get throttled animation frames (that had turned "first frames" into a constant 5 s).
* `research.py`: tiers matrix, fuzz, crossrender, scale, traces on the 02 run-card machinery; presets `research` and `ci`; the dashboard's Lab run panel shows fuzz progress and links the fuzz report.
* `agent.py`: x3d_mcp + chrome-devtools-mcp 1.12.0 (npx, isolated, no usage statistics, no CrUX) for one model; `lab://` addresses so the dashboard key never reaches a provider; a Playwright stand-in with the same tool names when Node is missing.
* `regress.py` + `python -m x3dlab ci`: committed baseline (`src/x3dlab/baselines/ci.json`: 23 matrix cases, 80 fuzz cases, 3 oracles), gates, a `regression-gate` job in GitHub Actions with the report as job summary and artifact. Two runs on the same pins give identical snapshots.
* Gateway: `/view?src=out/<job>/<file>.x3d` (one scene in X_ITE, for the agent's Chrome) and nested `out/` paths (fuzz reports).

**Findings on the pinned versions** (fork `13121b6`, consortium `ff0c0c9`, add-on `d9bbf91`, X_ITE 16.7.0):

* *The X3D 4.0 XSD rejects in-range colours written in exponent notation.* The SFColor / SFColorRGBA / MFColor patterns bound the mantissa, so `1.5e-1` (= 0.15) is refused while `0.05e1` (= 0.5) passes. Any exporter that prints colours `%e`-style is rejected by both servers and by any XSD engine. Minimal reproducer: `<Material diffuseColor="0.5 3.794e-01 0.2"/>`. SFFloat fields with ranges (e.g. `transparency="5e-1"`) are fine.
* *A USE node that also sets fields is accepted by every validator* (fork, consortium, XSD), e.g. `<Shape USE="A" bboxSize="1 1 1"/>`; 19775-1 allows only `containerField` next to USE.
* *Consortium misses* what the fork catches: nodes outside the declared profile (Interchange + PointLight / Sound / Fog / Billboard), an IndexedFaceSet index past its Coordinate, a ROUTE before its DEF, PBR outside the profile. Fuzz recall on the ci seed: fork 0.976, consortium 0.952, XSD alone 0.833; specificity 0.921 for all three (the colour-exponent quirk is the only false alarm).
* *X3DUOM gaps*: `Box.size`, sphere / cone / cylinder radii and heights (> 0 in 19775-1 and in the fork's semantic check) and `bboxSize` (≥ 0 or the `-1 -1 -1` sentinel, in the XSD) carry no bounds in the UOM. The generator encodes them by hand (`SPEC_POSITIVE`, `NO_GENERATE`), and the `spec_range` operator tests them.
* *X_ITE is lenient by design*: it loads `version="5.0"`, unknown attributes, NaN fields and dangling USEs (recall 0.16) but logs an error for unknown nodes and refuses broken XML.
* *The Blender add-on frames the X3D Viewpoint correctly*: silhouette IoU against X_ITE 0.97–1.00 on six scenes (fieldOfView applied to the short image side). The lantern's 0.97 is its spinning body, which X_ITE has already started to animate.
* *Scale on CPU* (2 cores, SwiftShader): a 200 k-vertex mesh (9.4 MB) validates through the blob store in about 1.1 s; X_ITE parses it in 3.6–4.6 s with a 430–490 MB JS heap; Blender imports it in 1.4 s.

Not verifiable here: chrome-devtools-mcp itself (its npx launch was not permitted in this build environment; the agent was tested end to end through the stand-in, and runs the real server on Colab) and the Copilot oracle (private build; its results are normalised from `validator.validate()` without knowing the exact return shape).

## Phase 4: next

* Run the gate from x3d_mcp's and x3d-blender-io's own CI (a reusable workflow that installs `x3dlab` and points the pins at the commit under test).
* Report the exponent-colour XSD pattern and the USE-with-fields gap upstream (only with an explicit "send").
* Copilot oracle: confirm the shape of `validator.validate()` against the private build and add it to the matrix baseline (kept out of the public baseline).
* A generic plugin hook for private modules (provenance stamping), with the adapter kept outside this repo.
* GPU baselines (render timings per GPU class) kept per runtime in Drive rather than in the repo.
