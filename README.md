# x3d.lab

Colab notebooks and a shared toolkit for authoring and testing [X3D](https://www.web3d.org/x3d/what-x3d) with AI: the **x3d_mcp** validator and MCP server, the **X_ITE** browser, **Blender**, and models from OpenAI, Anthropic (Claude) or Google (Gemini). A live dashboard follows every run in your browser, on your phone, and in Chrome DevTools.

## Start here: two tracks

| Track | Notebooks | Needs the `x3dlab` package? | Use it for |
|---|---|---|---|
| **A · Plain MCP** (start here) | 00 MCP Starter, 00 Basic MCP, 00 GPU Browser Playground | No. All code is in the cells; `python tools/run_basic.py` checks there is no `x3dlab` reference | Install an X3D MCP server, test it on 127.0.0.1, optionally expose it through a Cloudflare quick tunnel or ngrok behind a bearer token |
| **B · x3dlab kit** | 01 Quickstart, 02 Lab, 03 Research, 04 Model Duel, 05 GPU Hog | Yes: they clone this repo (`LAB_REPO` in each source) and import `src/x3dlab/` | Dashboard, Blender round trips, fuzzing, the regression gate |

Local runbook (outside Colab): [docs/HANDOFF.md](docs/HANDOFF.md).

| Notebook | For | Runtime | Status |
|---|---|---|---|
| [00 MCP Starter](notebooks/00_MCP_Starter.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/00_MCP_Starter.ipynb) | start here (undergrads): code hidden behind forms, either server, none / Cloudflare / ngrok behind a password, then a guided test: hello, tool tour, 16 broken scenes with "what we broke", a load test, a scorecard, live 3D views and a fix-3-mistakes challenge. No package | CPU | ready |
| [00 Basic MCP](notebooks/00_Basic_MCP.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/00_Basic_MCP.ipynb) | the shortest path: install an X3D MCP server (the fork, or upstream with one word), expose it through Cloudflare or ngrok behind a token, and smoke-test it thoroughly. No package, all code in the cells | CPU | ready |
| [00 GPU Browser Playground](notebooks/00_GPU_Browser_Playground.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/00_GPU_Browser_Playground.ipynb) | for the curious: give headless Chrome the real GPU (install the graphics half of the NVIDIA driver at the exact version), then run everything twice, SwiftShader vs GPU: chrome://gpu and the WebGPU adapter's features and limits, an editable WGSL matrix multiply checked against NumPy, an instanced-triangle ladder chart, X_ITE frames per second. Built on Google's headless-Chrome WebGPU notebook. No package | any GPU (T4 free) | new |
| [01 Quickstart](notebooks/01_Quickstart.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/01_Quickstart.ipynb) | newcomers: hosting, keys, a first scene, MCP tools, connecting Claude Code, Break it | CPU | ready |
| [02 Lab](notebooks/02_Lab.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/02_Lab.ipynb) | intermediate: X3D ↔ Blender round trips measured object by object (3.3 / 4.0 / 4.1), multi-generation drift, Cycles render ladders on the GPU with peak VRAM, MCP load, a control panel with JSON configs and seeds, run cards you can compare | L4 or G4 (CPU works, smaller) | ready |
| [03 Research](notebooks/03_Research.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/03_Research.ipynb) | advanced: differential fuzzing from the X3D object model with minimised reproducers, a six-oracle matrix (fork, consortium, a second XSD engine, X_ITE, Copilot, Blender), X_ITE vs Cycles silhouettes, scale to millions of vertices, Chrome traces, one model driving x3d_mcp *and* chrome-devtools-mcp, and the regression gate CI runs | L4 or G4 (CPU works, smaller) | ready |
| [04 Model Duel](notebooks/04_Model_Duel.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/04_Model_Duel.ipynb) | the same prompts to several models (NVIDIA build.nvidia.com, Claude, OpenAI, Gemini, or the free `sample`), x3d_mcp as referee: valid as written / after the server's exact fixes / after the model's repair, an error profile by kind, a prompt-by-prompt grid, a JSON duel card. Shows the worst-case request count before spending | CPU | new |
| [05 GPU Hog](notebooks/05_GPU_Hog.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/05_GPU_Hog.ipynb) | everything at once on a GPU: a corpus of ~230 scenes (one per X3D node from x3d.loom's fixtures, the 7 node types new in 4.1, the lab's scenes; each as 4.0 and 4.1) through x3d_mcp, lxml + the version's XSD, X_ITE, Blender import / round trip / export validity, Cycles and loom's Babylon bridge; the heavy stress test; warm traces; findings sorted by project (x3d_mcp, X_ITE, x3d-blender-io, x3d.loom, Copilot) with issue drafts | A100 / G4 | new |

| [06 Foundry](notebooks/06_Foundry.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/06_Foundry.ipynb) | the flywheel, in rounds: pick nodes nobody has covered (new in 4.1, not yet bridged, failed by models, disputed last time), NVIDIA models write scenes around them, three judges (x3d_mcp, lxml + XSD, the object model) and four engines (X_ITE, the x3d.loom bridge, Blender, glTF via Blender + the Khronos validator + X_ITE) look at each; every disagreement is shrunk to its smallest scene and written up as a bridge fixture pinned to X_ITE's pixels, an x3d_mcp strict-xfail test, or an issue draft; each model's mistakes become rules in its next prompt. `corpus` runs it offline for free | CPU / T4 (no A100 needed) | new |
| [07 Unreal Fidelity](notebooks/unreal/07_Unreal_Fidelity.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/unreal/07_Unreal_Fidelity.ipynb) | X3D scenes as Unreal Engine 5 stills, headless: detects the GPU tier (RT cores, NVENC, VRAM; then the Vulkan device's own ray-tracing extensions), gives Unreal real Vulkan, takes the engine from *your* Epic download in Drive (or Epic's container image, no Docker), proves Vulkan-on-NVIDIA from Unreal's log, imports the Foundry's glTF with Interchange and accounts for it, renders every viewpoint through a preview / high / ultra ladder (Lumen; the path tracer where there are RT cores), compares with X_ITE and Cycles. Everything in [notebooks/unreal](notebooks/unreal/README.md) | **G4** (best), A100, L4 | new, untested on a GPU |

01–06 import the same `x3dlab` package (00 is self-contained on purpose) from this repo, so a fix lands once.

## Which x3d_mcp

The official project is [Web3DConsortium/x3d_mcp](https://github.com/Web3DConsortium/x3d_mcp). The notebooks run the fork [hagopjay/x3d_mcp](https://github.com/hagopjay/x3d_mcp) at the commit in [`pins.toml`](src/x3dlab/pins.toml). The fork is ahead of upstream (exact-fix hints; ROUTE-order, colour-range and H-Anim checks; a blob store for scenes over the 4 MiB request limit) and pins `mcp<2`. Upstream asks for `mcp>=1.7` with no upper bound and imports `mcp.server.fastmcp`, which `mcp` 2.x removed, so a fresh install of upstream fails at start-up while `mcp` 2.x is current on PyPI.

## Cell 1: how the pieces connect

```
                  ┌──────────────── Colab VM ────────────────┐
your browser ◄──┐ │  gateway :8765  ── /mcp ──►  x3d_mcp :8000 │
(Colab proxy)   ├─┤  dashboard, API, event stream   (localhost) │
your phone   ◄──┤ │  jobs (model calls, validation)             │
Claude Code  ◄──┘ │  Blender workers, headless Chrome (02, 03)  │
(tunnel)          └──────────────────────────────────────────────┘
```

* **Colab proxy** is always on and private to your Google login. **cloudflared** (no account) or **ngrok** (token, optional fixed domain) add a public URL for your phone and for remote MCP clients.
* The dashboard lives under `/k/<key>/`: the key is in the path, so every relative URL carries it (this works inside Colab's output iframe, where cookies are third-party). `/mcp` needs a separate bearer token, so giving a client MCP access does not give it your model budget. Both change on every run.
* x3d_mcp listens on loopback only. The gateway rewrites `Host` so the MCP SDK's DNS-rebinding guard stays on.
* Model work runs as background jobs: requests answer at once and progress streams over SSE, which keeps every request well under Cloudflare's ~100 s limit.
* X_ITE renders on the device that opens the dashboard, not on Colab's GPU. Colab's GPU is for Blender (02) and the headless browser measurements (03).

## Chrome DevTools

Open the dashboard and press F12.

* **Performance**: record a trace while a scene runs. Validator, MCP, LLM and X_ITE work appear as custom tracks in an *x3d.lab* group (via `performance.measure` with `detail.devtools`). Enable *Capture settings › Show custom tracks* if needed.
* **Console**: a styled line per event (level and density follow the page's selectors), one collapsed group per scene with `console.table` of issues, a custom formatter for issue objects (*Settings › Console › Custom formatters*), and `x3dlab.help()`.
* **Network**: `events` is the live stream (EventStream tab). Every API response carries `Server-Timing` (gateway, validate, mcp).
* **Copilot tab**: `window.__x3dcopilot` exposes the Copilot's viewer, editor and validator.

## Research and the regression gate

The 03 notebook's machinery is in the package, so it also runs from a shell:

```bash
python -m x3dlab ci                                   # the gate: every verdict of the ci preset vs src/x3dlab/baselines/ci.json
python -m x3dlab ci --update                          # accept new behaviour (after a deliberate pin change), in its own commit
python -m x3dlab fuzz --n 300 --seed 11 --oracles fork,consortium,lxml-xsd   # a standalone fuzz run with reproducers
```

* `fuzz.py`: scenes generated from the X3DUOM (public Web3D data shipped with x3d_mcp), 27 operators (20 break a rule, 7 keep the meaning), findings bucketed by (kind, oracle, operator, complaint) and minimised by hierarchical delta debugging.
* `research.py`: the matrix, cross-renderer, scale and trace tiers; they plug into the same run cards as the 02 tiers.
* `browser.py`: Playwright Chromium with SwiftShader WebGL, the pinned X_ITE served offline, load timings as DevTools user-timing bars, Chrome traces and metrics.
* `agent.py`: one model with x3d_mcp and [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) (pinned, `--isolated --no-usage-statistics --no-performance-crux`); the model only sees `lab://` addresses, never the dashboard key.
* `regress.py`: snapshot, gates (worse verdicts, new buckets, recall/specificity drops fail; improvements are reported), markdown for the GitHub job summary.

What the fuzzer found on the pinned versions is listed in [docs/ROADMAP.md](docs/ROADMAP.md#phase-3-03-research-this-commit).

## Private material

Some inputs are private: the Copilot Validator Demo build and other private modules of the x3d family. Notebooks load them only with your credentials (Drive, a private release, or `GITHUB_TOKEN`) into the Colab VM, never into this repo, and every notebook runs without them.

## Develop

```bash
pip install -e ".[server,notebook,tunnels,research,dev]"
python -m playwright install --with-deps chromium       # for the browser, agent and research tests
X3DLAB_X3D_MCP_DIR=/path/to/x3d_mcp pytest           # uses your checkout; otherwise the pinned fork is cloned
                                                      # X3DLAB_XITE_DIR=<x_ite dist> enables the browser and agent tests
python tools/build_notebooks.py                       # notebooks/src/*.py -> notebooks/*.ipynb
```

Edit notebooks in `notebooks/src/` (percent format, diff-friendly); the `.ipynb` files are generated. CI runs the tests and fails if a notebook is out of date. In a notebook, `X3DLAB_DEV=1` skips the pip install so a local checkout is used.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next.
