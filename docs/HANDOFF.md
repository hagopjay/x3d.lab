# x3d.lab runbook (integration handoff, 2026-11-08)

Every statement below was either run during the integration (marked **ran**), read from the code (marked **code**), or is labelled **unverified**.
Sandbox used: Linux, Python 3.13.18, uv 0.11.32, Node 22, github.com and PyPI reachable, tunnels not allowed (see "Tunnels").

## 1. Two tracks

| | Track A: plain MCP (start here) | Track B: x3dlab kit |
|---|---|---|
| Notebooks | `00_MCP_Starter`, `00_Basic_MCP` | `01_Quickstart`, `02_Lab`, `03_Research` |
| Needs `x3dlab` | No. `grep -c x3dlab` on both sources is 0 (**ran**) | Yes: `src/x3dlab/` (39 files tracked; present in this repo since phase 1, `b1f0c49`) |
| Needs a GPU | No | 02 and 03 prefer L4/G4; CPU works smaller (README) |
| Automated check | `tools/run_basic.py`, workflow `basic-mcp` | `pytest`, `python -m x3dlab ci`, workflows `tests`, `regression-gate`, `blender` |
| Exposure | 127.0.0.1 + token proxy, optional tunnel | gateway + dashboard, optional tunnel |

The "x3dlab package is missing" worry applies to the owner's *local* copy of the package, not to this repo: it is in the tree and its tests pass.
01 Quickstart is blocked only if `LAB_REPO` (in 01/02/03) points at a repo that does not exist yet: it now says `hagopjay/x3d.lab`, which must exist and be public for Colab to clone it.

## 2. Fresh-machine setup, outside Colab

```bash
git clone https://github.com/hagopjay/x3d.lab && cd x3d.lab
python -m venv .venv && . .venv/bin/activate          # Python >= 3.10 (CI uses 3.12; Blender tests 3.13)
pip install -e ".[server,notebook,research,dev]"      # Track B + tests. Track A alone needs only: pip install nbformat nbclient ipykernel
python tools/build_notebooks.py --check               # notebooks match notebooks/src
python tools/run_basic.py                             # Track A smoke test, ~15 s warm
pytest -q                                             # needs X3DLAB_XITE_DIR for offline browser tests, see below
```

Offline X_ITE for the browser tests (what CI does): `npm pack x_ite@$(python -c "import x3dlab.pins as p; print(p.get('x_ite','version'))")`, untar, export `X3DLAB_XITE_DIR=<dir>/package/dist`. If Chromium is not where Playwright expects it: `export CHROMIUM_EXECUTABLE=/path/to/chrome` (**ran** with `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`).
On a system Python that refuses pip installs add `--break-system-packages` (**ran**; root-user warning is harmless).

### Why `uv pip install --system` breaks, and what the notebooks do now
`--system` installs into the Python uv finds first, not the Python running the kernel. In a venv, a Jupyter install or CI the server then dies with `ModuleNotFoundError: No module named 'mcp'` (**ran**: failure reproduced from a venv before the fix; 109 pass / 0 fail / 2 skip after, with both `uv` and `pip` branches).
`00_Basic_MCP` now installs with `uv pip install --python <sys.executable>` or `<sys.executable> -m pip`, and starts the server with `sys.executable`: one interpreter for kernel, install and server. `00_MCP_Starter` already chose `--python` inside a venv and `--system` on Colab (code; `--system` on a non-venv Jupyter install that is not uv's default Python is the same hazard, **unverified** for the Starter).

## 3. Running each notebook

| Notebook | Locally | Result recorded in this integration |
|---|---|---|
| 00 Basic MCP | `python tools/run_basic.py [--installer uv\|pip] [--slow] [--server hagopjay]` | venv: 109 pass / 0 fail / 0 warn / 2 skip (uv) and same (pip); `--slow`: 111 / 0 / 0 / 0 |
| 00 MCP Starter | executed headless with nbclient + a kernel on the venv interpreter | 0 error outputs, 10 HTML displays, "Caught all 15 broken scenes"; PASS/WARN counts not extracted (unverified) |
| 04 Model Duel | executed headless with nbclient, no keys (only `sample` runs; the others are listed as skipped) | 0 error outputs; sample: 1/6 valid as written, 2/6 after server fixes, 6/6 after the stand-in's repair |
| 01 / 02 / 03 | open in Colab (badges in README); locally their code lives in `src/x3dlab` and is covered by `pytest` | not executed end to end here (Colab-specific cells) |

`tools/run_basic.py` exit codes: 0 ok, 1 FAIL rows / notebook error / no network, 2 usage. Tunnel steps are skipped, not failed, when `NGROK_AUTHTOKEN` is unset or github.com is unreachable.
The notebook source of truth is `notebooks/src/*.py`; `.ipynb` files are generated without outputs by `tools/build_notebooks.py`. Never edit an `.ipynb` by hand: CI's `--check` step fails.

## 4. CI layout

| Workflow | Job | What | Local mirror |
|---|---|---|---|
| `tests.yml` | `test` | install extras, Playwright Chromium, pinned X_ITE from npm, `build_notebooks.py --check`, `pytest -q` | section 2 |
| `tests.yml` | `regression-gate` | `python -m x3dlab ci --quiet --out ci-out` vs `src/x3dlab/baselines/ci.json` | same command |
| `tests.yml` | `blender` | Python 3.13 + `bpy==5.2.3`, `pytest tests/test_lab.py tests/test_fidelity.py` | needs the ~400 MB wheel |
| `basic-mcp.yml` | `basic` (uv, pip) | venv runner, `build_notebooks.py --check`, `run_basic.py --installer X` | `python tools/run_basic.py` |

The new `basic-mcp.yml` is valid YAML (parsed locally) but has **not run on GitHub Actions** (unverified). It needs github.com and PyPI egress, and no secrets.

## 5. Tunnels (Track A, from the code in `00_basic_mcp.py` / `00_starter.py`)

Both notebooks run the server with `MCP_TRANSPORT=streamable-http` on 127.0.0.1:8000 and a token-checking proxy on 127.0.0.1:8765. The tunnel always points at the proxy. **Neither notebook uses SSE for MCP**: the only endpoint is `/mcp` (streamable HTTP). SSE appears only in the x3dlab event stream (Track B dashboard). Clients must choose "Streamable HTTP", not "SSE".

### Exposure options compared

| Option | Reachable by | Auth needed | URL | Carries | Limits (source) | Use when |
|---|---|---|---|---|---|---|
| `TUNNEL="none"` (127.0.0.1 only) | code in the kernel | none | `http://127.0.0.1:8765/mcp` | streamable HTTP | none | smoke tests, headless render |
| Colab port proxy | only your signed-in browser | your Google login (notebook text, code) | `google.colab.kernel.proxyPort(port)` (used by Track B `hosting.py`; not by Track A) | HTTP for a browser; **not usable by a remote MCP client** (Starter text: it needs your Google cookie) | Colab only | looking at a served page or directory yourself, a browser-based MCP Inspector (unverified) |
| `files.download(REPORT_PATH)` | your browser | Colab | n/a | the JSON report | Colab only, no-op error is swallowed outside Colab (code) | taking artifacts home |
| `cloudflared` (quick tunnel) | any client | none | random `https://*.trycloudflare.com`, new every run | HTTP incl. streaming | "no uptime promise, about 100 s per request", 429 when starting many in a row (Starter text, not verified by us) | quick remote test, no account |
| `ngrok` | any client | free `NGROK_AUTHTOKEN` (a token, not an API key) | random, or fixed `NGROK_DOMAIN` (one free static domain, per Starter text) | HTTP incl. streaming | one agent at a time on the free plan (`ERR_NGROK_108`) | stable URL for a client config |

`LOCAL_XITE` is a localhost-only helper, not an exposure: it serves the pinned X_ITE `dist/` on 127.0.0.1:8090 with `Access-Control-Allow-Origin: *` so the fork's render tools skip jsdelivr (`X3D_MCP_XITE_JS`). Upstream ignores the variable (the notebook says so).
I did not add a Colab-proxy cell: Track B's `hosting.py` already wraps `eval_js(proxyPort)` and `serve_kernel_port_as_iframe` in a Colab-only try block, and a second copy would be untestable here.

### Pointing a client at the tunnel
Endpoint = `<tunnel URL>/mcp` (no `/sse`). Header `Authorization: Bearer <token printed by cell 4>`.
```bash
claude mcp add --transport http x3d-colab https://<id>.trycloudflare.com/mcp --header "Authorization: Bearer <token>"
```
Cursor: `{"mcpServers": {"x3d-colab": {"url": "<url>/mcp", "headers": {"Authorization": "Bearer <token>"}}}}`. Claude Desktop goes through `npx mcp-remote <url>/mcp --header ...`. Inspector: transport "Streamable HTTP". (Config text from the notebooks; not exercised against a real client here.)

### Common failures
| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: add NGROK_AUTHTOKEN ...` (Basic) / `ERR_NGROK_105` (Starter) | token unset, or an API key was pasted | Colab Secrets key `NGROK_AUTHTOKEN` with notebook access on, or env var locally; use the *authtoken* page |
| `ERR_NGROK_107` / `ERR_NGROK_108` | token reset / another ngrok agent already running | recopy the token; stop the other agent or use cloudflared |
| 502 from the public URL | tunnel is up but nothing answers behind it: it came up before the server/proxy, or the proxy thread died | wait ~30 s (Basic sleeps 3 s, Starter retries ~30 s); rerun cells 3 and 4; check `/healthz` on the public URL (answers without a token) |
| tunnel URL stops working after a kernel restart | the proxy is a thread inside the kernel; the server and cloudflared are started with `start_new_session` and can outlive it (code) | rerun cells 1, 3, 4 (cell 3 frees the leftover port); with cloudflared the URL changes; `KEEP_TOKEN` + `NGROK_DOMAIN` keep client configs valid (Starter) |
| 401 | missing or old token (it changes every run) | copy the new token |
| client says it cannot connect / bad content type | SSE selected, or path `/sse` | choose Streamable HTTP, path `/mcp` |
| cloudflared 429 or "cannot reach Cloudflare" | rate limit or firewall | wait a minute, or use ngrok |

### Security
A public tunnel exposes every tool to anyone holding the URL **and** the token. What the code already does: the server listens on 127.0.0.1 only with host/origin allow-lists; the proxy requires a random 32-byte bearer token (constant-time compare), strips cookie/origin/authorization and swaps in a separate server-side blob token that is never shown; the token is blurred in the output; `Clear this output before sharing the notebook` is stated in the notebook. Easy additions (not implemented): keep `TUNNEL="none"` unless a remote client needs it; run cell 8 (stop) when done; ngrok traffic policies or Cloudflare Access for an identity check (needs a named tunnel/paid features, **unverified**); an IP allow-list in the proxy `forward()`; rotating the token on a timer; never save the notebook with outputs (the builder strips them).

## 6. Gotchas hit during integration

| Symptom | Cause | Fix |
|---|---|---|
| `git bundle verify` / `git fetch` of any upload: "Repository lacks these prerequisite commits 7dcb568" | all four bundles are thin: they assume GitHub's first commit "Add files via upload", which is in none of them | the parent of the first commit (`3c1dee8`) was grafted away and the 6 commits rewritten (SHAs changed, messages/authors/dates kept); one missing delta base (the original README, blob `a0ae117`) was reconstructed byte-exactly from the three READMEs and verified against its SHA-1 |
| Starter bundle looked older than phase 3 | it is newer: starter is 2 commits **on top of** phase 3 | integration branch is based on the starter tip |
| `No module named 'mcp'` from the server in a venv | `uv pip install --system` | section 2 |
| `ScannerError: mapping values are not allowed here` in the new workflow | colon inside an unquoted YAML step name | quoted/reworded; always run `yaml.safe_load` on workflows |
| `git filter-branch`: "Your index contains uncommitted changes" on an empty work tree | HEAD pointed at an unborn branch | `git reset --hard <branch>` first |
| 111 vs 109 passes between system Python and a fresh venv | two extra rows (`splat_inspect`, `splat_chunk`) appear in the system run; cause not investigated (probably an optional package present only there, **unverified**) | compare totals within one environment |
| Public-tunnel and unpinned third-party runs were blocked by the sandbox's permission checks | policy | not run; see PR body |

## 7. Next steps (priority order)

Gate names are the owner's roadmap (Unblock, Fidelity core, One file two engines, Reach); the project document that defines them was not readable from this session, so the mapping below is my reading of the repo and is **unverified**.

1. **Unblock** (hours): create `hagopjay/x3d.lab` public, push, confirm the `tests`, `regression-gate`, `basic-mcp` jobs go green on GitHub (they have not run there); enable Actions.
2. **Unblock** (1-2 h): open each Colab badge from the pushed repo; run 00 Basic with `TUNNEL=cloudflared` and with ngrok on Colab (never run here).
3. **Fidelity core** (1 day): run the `blender` job (needs `bpy==5.2.3`, Python 3.13); the 2 skipped local tests depend on it; X3D 3.3 clamps light intensity and 4.x loses DEF/USE (docs/ROADMAP.md findings).
4. **One file, two engines** (days): X_ITE vs Cycles silhouettes are in 03 Research; bump X_ITE pin 16.7.0 to 16.7.1 only with the browser tests as the gate.
5. **Reach** (days): revisit `mcp<2` (fork imports `mcp.server.fastmcp`, removed in mcp 2.x); add a nightly job that runs `run_basic.py --server consortium` to notice when upstream moves; keep the two known upstream gaps (ROUTE before DEF, PBR outside the profile, per the Starter notebook) as expected FAILs.

## 8. Model providers and the duel

`x3dlab.llm` speaks four providers over plain HTTPS: `anthropic`, `openai`, `gemini` and `nvidia`. Keys come from the keys widget or Colab Secrets (`NVIDIA_API_KEY`, also `NGC_API_KEY` or `NVAPI_KEY`) and are redacted from every log, card and dashboard event.

**NVIDIA (build.nvidia.com)** is the OpenAI chat-completions API at `https://integrate.api.nvidia.com/v1`. What differs, and how the adapter handles it:

| Difference | Handling |
|---|---|
| model ids are `org/model` (`meta/llama-3.3-70b-instruct`) | allowed for `nvidia` only (they go in the JSON body); Gemini ids still may not contain `/`, because they go into a URL path |
| the catalog lists embedding, reranking, safety and reward models too | filtered out of the model list |
| model names rotate | the list comes live from `/v1/models`; the default is the first family present in `llm.NVIDIA_PREFERENCE` (Nemotron 3 Super, Llama 3.3 Nemotron Super, Llama 3.3 70B, ...). Tool calling per model is **unverified** |
| `max_tokens`, not `max_completion_tokens`; hosted models cap it | 8192 by default; a 400 that names `max_tokens` halves it once |
| reasoning models think in `<think>` tags | stripped before the text is used |
| free tier about 40 requests per minute | requests are spaced to 36 per minute per kernel (`X3DLAB_NVIDIA_RPM`, `0` = no limit); 429s are retried 5 times, honouring `retry-after` |

Not exercised against the live endpoint from this sandbox (its network allowlist does not reach nvidia.com); the first Colab run with a real key is the test.

**The duel** (`x3dlab.duel`, notebook 04, `lab.duel(cfg)`): the same prompts to each contender; per scene draft → `validate_x3d` + `validate_semantic` → `repair_x3d` (no model) → up to `rounds` model repairs from the validator's report and `exact_fix_hints`. Every error is given a *kind*: XSD messages by pattern (`boolean-case`, `unknown-field`, `node-not-allowed`, `value-format`, ...), semantic errors by their rule id. Output: `out/duel_<id>/duel_card.json`, `report.html`, and every draft and final scene under `scenes/`.

Finding from the `sample` contender (the lantern with one known defect per prompt, pinned server `13121b6`): `repair_x3d` fixed **1 of 11** defect kinds on its own (the missing PhysicalMaterial component); the other ten, including `solid="True"`, needed a model. That matches the 00 Starter's WARN rows for `autofix_x3d` / `repair_x3d`.

Cost control: `duel.estimate(cfg)` gives best and worst-case request counts before anything is sent, and the run refuses to start above `max_requests` (default 200; the notebook sets 120).

## 9. The GPU hog run (notebook 05, `x3dlab.hog`, `lab.hog(cfg)`)

Stages, each switchable: **probe** (GPU, driver, the graphics libraries Chrome and the OptiX denoiser need, Cycles backends, Chrome's renderer in SwiftShader and GPU mode) · **corpus** (x3d.loom's bridge fixtures at a pinned commit, the 7 node types X3D 4.1 adds, the lab's scenes; every scene as 4.0 and 4.1) · **matrix** (x3d_mcp fork, lxml with the version's own XSD, optional upstream, X_ITE with coverage, Blender import, round trip to X3D40/X3D41 with the export re-validated, Cycles coverage) · **loom** (its `run-tests.js`: bridge reader + Babylon vs X_ITE) · **stress** (heavy preset, GPU only, after a measured warm-up) · **traces** (pagoda cold and warm, meshes of growing size) · **findings** (rules → per-project findings and `issues/<project>.md`).

**Chrome on the GPU.** `browser.py` always forced SwiftShader (`--use-angle=swiftshader`), so every X_ITE timing so far was a CPU timing, on any runtime. `lab.start_browser(gpu=True)` or `X3DLAB_CHROME_GPU=1` now tries ANGLE on Vulkan; `browser.info["gpu"]` says whether it got a hardware renderer. It needs the NVIDIA Vulkan ICD inside the container; the probe reports whether it is there. **Unverified on Colab.**

Blender crashes are contained: tasks run in chunks of `blender_chunk` per process; a chunk that dies is re-run task by task, so the scene that kills Blender is named.

### Findings from the sandbox preview (validators + X_ITE + loom; no Blender, SwiftShader; 233 scenes, 10 Nov 2026)

| Project | Finding | Scenes |
|---|---|---|
| x3d_mcp | `index-out-of-range` on the `-1` separator of IndexedTriangleFanSet / IndexedTriangleStripSet: a false positive (the -1 ends a fan or strip) | 4 |
| x3d_mcp | the `profile-component` message says X_ITE refuses such scenes; X_ITE 16.7.0 loaded all 64 | 64 |
| x3d_mcp / X3D XSD | **ComposedTexture3D**: in both the 4.0 and 4.1 XSD its texture content is an `xs:sequence` (ImageTexture, PixelTexture, MovieTexture) where it should be an `xs:choice`, so ordinary 3D textures are rejected; the UOM accepts any X3DTexture2DNode | 2 |
| x3d_mcp / X3D XSD | **LayoutGroup** inside Group is rejected by both XSDs; the UOM allows it | 2 |
| x3d_mcp / X3D 4.1 XSD | **InlineGeometry** (Shape.geometry) and **RenderedTexture** (Appearance.texture) are missing from the 4.1 XSD's content models though the 4.1 UOM places them there | 2 |
| x3d.loom | 64 fixtures use nodes outside `profile="Immersive"` without a `<component>` declaration | 64 |
| x3d.loom | 8 bridge failures (X_ITE passes all 8 on the same rig, so they are bridge defects, not rig differences): PlaneSensor and SphereSensor drag ~3.7× too short (one shared pointer-to-world scale), QuadSet and StaticGroup not pickable, ScreenGroup / TextureBackground / Viewport not drawn or not clipped, SpatialSound gain and panning slightly off | 8 |
| X_ITE | none: it loaded all 233 scenes, and draws the 4.1 nodes GaussianSplats, EnvironmentLight, RenderedTexture and Tangent | – |

The HAnimPose scene first used `containerField="poses"`: the 4.1 XSD accepted it, the fork's semantic layer rejected it (HAnimHumanoid has no `poses` field in either UOM). That is the semantic layer doing its job; the corpus uses `children`, as `where_can_place` says.

## 10. Giving headless Chrome the GPU on Colab (`x3dlab.graphics`)

Colab's containers carry NVIDIA's compute userspace (CUDA, NVML, OptiX ray tracing) but not the graphics one: no Vulkan ICD, no GLX/EGL libraries, no OptiX denoiser model (`nvoptix.bin`). Chrome then falls back to SwiftShader, and Cycles' OptiX denoiser fails ("Failed to create OptiX denoiser", seen on the A100 on 10 Nov). The fix, from Google's [headless Chrome WebGPU notebook](https://developer.chrome.com/blog/supercharge-web-ai-testing): install `libnvidia-gl-<branch>` (it carries the Vulkan ICD and, in Ubuntu's and NVIDIA's packaging, `libnvoptix`/`nvoptix.bin`) plus `vulkan-tools`, then start Chrome with `--use-angle=vulkan --enable-features=Vulkan --disable-vulkan-surface --enable-unsafe-webgpu`.

- `graphics.enable()` installs the package **only at exactly the driver's version** (`nvidia-smi` → e.g. `580.82.07` → `libnvidia-gl-580=580.82.07-*`, or the `-server` package). A mismatched userspace breaks the GPU for CUDA too, so there is no "closest version" fallback. Without an NVIDIA GPU, apt or root it changes nothing.
- `browser.GPU_ARGS` now include `--disable-vulkan-surface`; without it headless Chrome cannot create a Vulkan surface and stays on SwiftShader.
- `HogConfig.enable_graphics` (default on) runs it before the probe; notebook 05 step 3 has the switch.
- Whether it also fixes the OptiX denoiser depends on the package shipping `nvoptix.bin` for that driver; the probe reports it. **Unverified on Colab** until the first run.

`notebooks/00_GPU_Browser_Playground.ipynb` (Track A, no package) teaches the same thing hands-on: before/after, two Chromes side by side, editable WGSL. In this sandbox (no GPU) every step runs with both columns on SwiftShader; numbers there: WGSL matmul N=256 0.3 GFLOP/s, 100k triangles 1.5–1.6 s/frame, X_ITE 100 boxes ~85 frames/s uncapped.

## 11. The Foundry (notebook 06, `x3dlab.foundry`, `lab.foundry(cfg)`)

The hog (§9) measures one fixed corpus once. The Foundry is the loop that keeps going: **target → generate → referee → engines → claims → shrink → write → learn**, one round at a time, with `state.json` carrying what was learned (per-node attempts and disagreements, per-model rules, the claims already reported) into the next round.

**Pieces, and where they live**

| Piece | Module | Notes |
|---|---|---|
| targeting | `foundry.choose_targets` | score = novelty 3/(1+tried) + new-in-4.1 +2 + x3d.loom `todo`/`gap` +2 (`claimed` +1) + disputed last round +1.5 each (≤3) + models fail it +1.5 + noise; at most 2 nodes per component per round. `nodes=[...]` pins them |
| prompts | `foundry.prompt_for` | the node is the centrepiece ("focus"), 4 of its settable fields away from defaults, a camera framing line, and the `<component>` line when the node is outside Immersive (needed the `uom.profile_nodes` fix below) |
| generation | `duel.run_async` | per-version duels; `DuelConfig.rules` puts each model's lessons in its system prompt. `duel.OFFLINE` now holds the keyless contenders: `sample` and the Foundry's `corpus` (x3d.loom fixtures + the 4.1 examples, a planted defect in 2 of 3, clean on repair) |
| referee | `foundry.judge` | fork verdict (XSD layer + semantic layer) + lxml with the version's XSD + `hog.uom_allows` for placement complaints. `effective` = XSD-valid or UOM-allowed; `sound` = effective and no fork semantic error. Drafts are judged too, so validator disagreements are found even when the repair turn fixes the scene |
| engines | `foundry._engines` (+ `hog._xite_all/_blender_all`) | X_ITE (pictures kept under `shots/`), Blender import + X3D40 round trip (export judged by the fork **and** the XSD), glTF, the bridge |
| glTF engine | `formats.py` + worker task `convert` | Blender imports X3D, exports `.glb` (lights, cameras, loose edges and points on), imports it back into an empty scene (counts before/after, shared meshes, smooth faces); the Khronos `gltf-validator` (npm, 380 kB) validates it; X_ITE draws a small X3D that Inlines the glb under the original's Viewpoint (with its Transform chain) and NavigationInfo. Same renderer, same camera: what changes is what the trip lost. `formats.EXPECTED_LOSS` lists what glTF has no place for (sensors, interpolators, Text, Billboard, Switch, LOD, Background, Fog, …) |
| bridge engine | `foundry.loom_run/loom_shots` | writes fixtures under `fixtures/foundry/` of the checkout, runs the bridge's own `run-tests.js` (names like `foundry/<Name>` work because it uses `path.join`), removes them. A probe is `[{"settle":1},{"shot":1}]`: one whole 320×240 picture per engine |
| claims | `foundry.find_claims` | `fork-rejects-valid`, `fork-accepts-invalid`, `xsd-stricter-than-uom`, `xite-error`, `blender-import`, `blender-unsupported`, `blender-flat`, `blender-export-invalid`, `gltf-invalid`, `gltf-lost`, `gltf-colour`, `loom-error`, `loom-differs`, `loom-colour`. One claim per signature per round; `expected` when a known limit explains it |
| shrinking | `shrink.py` + `foundry.keep_for` | delta debugging: remove nodes, unwrap wrappers (Group/Transform that only carry the culprit), drop attributes, halve long lists keeping whole tuples, drop meta. Every cut must stay *sound* (full referee) and keep the claim true with the engine that made it. Cheap claims first. Claims that shrink to the same scene merge (`same_as`) |
| artifacts | `write_loom_fixtures`, `write_mcp_tests`, `write_issues` | in `kit/` mirroring the repos, zipped |
| learning | `learn_rules` | error kinds of the drafts → rule text (`RULE_TEXT`) + the model's own last message; ≤ 8 per model, newest and most frequent first. Nothing is learned from a draft the XSD/UOM accept (the fork was wrong, not the model) |

**Pictures are compared by silhouette match, not IoU.** `formats.match` is an F-score of "covered here, covered within 2 px there", both ways. IoU said the bridge's Arc2D was 0.45 wrong; it was a one-pixel shift of a one-pixel line (match 1.0). Colour is judged only when the shapes agree (`formats.colour_kind`: mean change > 64 of 255 → darker / brighter / hue), because Phong (X_ITE) and PBR (Babylon, glTF) shade differently by design.

**Loom fixtures** are pinned to X_ITE: `pick_points` takes centres of flat 5×5 patches (no edges, so anti-aliasing cannot move them) spread over a 4×3 grid, 10 covered (tolerance 48) + 4 key background (24); a second runner pass drops any pixel X_ITE does not reproduce itself. The scene keeps a flat magenta Background so coverage reads at a glance. They go to `fixtures/foundry/`, which the default run (`fixtures/*.test.json`) does not read: no red CI, run by name.

**x3d_mcp tests** follow the repo's style (`sys.path` to `src`, `validate_xml(xml, version)`), one strict xfail per validator claim, and are run here against the fork's own `validate_xml` before they are written: a case that does not reproduce through it is dropped.

### First rounds in the sandbox (corpus contender, 8 pinned nodes, SwiftShader, bpy 5.2.3, 10 Nov 2026)

Round time on this CPU box: ~5 min (generate 3 s, referee 5 s, engines 42 s of which the bridge runner 30 s, shrink 2–3 min, write 1.5 min). Rounds 2 and 3 re-found every round-1 claim and treated the reported ones as known.

| For | Claim | Evidence |
|---|---|---|
| x3d.loom | **wrong colours: shapes come out black** (TriangleFanSet, IndexedQuadSet, TwoSidedMaterial merged into one claim) | silhouettes match 1.0, mean colour change 153/255, brightness ×0.00. By hand: an IndexedTriangleSet stays black with no NavigationInfo, with `headlight="true"`, with an explicit DirectionalLight, and with only `emissiveColor`; a Box is lit but off. Shrunk to Group › Shape › Material + TriangleFanSet |
| x3d.loom (runner) | **X_ITE's "Loading 1 file" toast is part of the reference picture**: the runner's X_ITE scene page keeps notifications on, so a scene whose only node is an EnvironmentLight "covers" the top right corner. The shrinker cut an EnvironmentLight claim down to exactly that. Probes and fixtures now start with an X_ITE-only `js` step that turns notifications off and hides the toast; the runner could set `notifications=false` on its `x3d-canvas` instead | with the toast hidden, EnvironmentLight + Box goes from match 0.68 to 1.0 |
| x3d.loom | **Viewpoint position ignored in scene mode**: a scene with `<Viewpoint position="0 0 6"/>` is drawn as from 0 0 10 (0.6× size) | the shrinker cut the Tangent claim to `<Viewpoint position="0 0 6"/>` + a bare IndexedTriangleSet, and (once the toast below was hidden) the EnvironmentLight claim to `<Viewpoint position="0 0 6"/>` + a bare Sphere: neither 4.1 node is the cause. By hand: the same picture at 6 and at 10. Could be the runner's `__bodyShot` camera rather than the bridge |
| x3d-blender-io | **IndexedQuadSet and Arc2D are skipped on import** (`ImportX3D warning: unsupported type`): an empty Blender scene, an empty glTF | worker log; glTF trip match 0.0 marked *expected* because of it |
| x3d-blender-io | **Sphere and Cylinder are imported flat-shaded** (X3D draws them smooth): meshes `Shape_Sphere`, `Shape_Cylinder` have no smooth face; the glb gets per-face normals (sphere 250 → 968 vertices after the trip) | `convert` task `flat_meshes`; X_ITE shows the glTF sphere faceted |
| x3d-blender-io | **TwoSidedMaterial is ignored without a warning**: a red and a green shape come back as one grey default material (Blender has 1 material for 2) | glTF trip keeps the silhouettes (match 1.0) but the colours change by 128/255; the new `gltf-colour` claim. Phong against PBR alone moves colours 30–50/255 (TriangleFanSet 52, Tangent 34), under the 64 threshold |
| X3D XSD / x3d_mcp | ComposedTexture3D (`xs:sequence` for `xs:choice`) and LayoutGroup placement: the hog's findings, now as **verified strict-xfail tests** for x3d_mcp | both XFAIL through `validate_xml` |
| glTF trip | clean otherwise: every Blender export passes the Khronos validator; TriangleFanSet, TwoSidedMaterial, Tangent, EnvironmentLight, ComposedTexture3D survive with match 1.0 | |
| x3dlab | `uom.profile_nodes("Immersive")` was always empty: the profile file pads that entity name with two spaces | fixed (`\s+`), 4.1 falls back to the 4.0 profile file |

**Not yet seen on Colab:** a round with NVIDIA models (the real point: the corpus contender only proves the plumbing), and the bridge runner on a GPU Chrome.

**Next:** USD as a second format engine (same `convert` task, `format="usd"`; check on Colab whether `usd-core` has a Python 3.13 wheel and whether the bpy wheel has `wm.usd_export`); a `--fixtures-dir` option in x3d.loom's runner so probes never touch the checkout; feed `same_as` merges back into targeting (one cause, many nodes).
