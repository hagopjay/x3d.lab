# x3d.lab

Colab notebooks and a shared toolkit for authoring and testing [X3D](https://www.web3d.org/x3d/what-x3d) with AI: the **x3d_mcp** validator and MCP server, the **X_ITE** browser, **Blender**, and models from OpenAI, Anthropic (Claude) or Google (Gemini). A live dashboard follows every run in your browser, on your phone, and in Chrome DevTools.

| Notebook | For | Runtime | Status |
|---|---|---|---|
| [01 Quickstart](notebooks/01_Quickstart.ipynb) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/jasonthurman-sys/x3d.lab/blob/main/notebooks/01_Quickstart.ipynb) | newcomers: hosting, keys, a first scene, MCP tools, connecting Claude Code, Break it | CPU | ready |
| 02 Lab | intermediate: Blender round trip and GPU renders, a configurable stress test, DevTools profiling, run cards, provenance | L4 or G4 | next |
| 03 Stress & Research | advanced: grammar fuzzing, cross-validator and cross-renderer comparison, scale on a G4, regression baselines | G4 | planned |

All three notebooks import the same `x3dlab` package from this repo, so a fix lands once.

## Which x3d_mcp

The official project is [Web3DConsortium/x3d_mcp](https://github.com/Web3DConsortium/x3d_mcp). The notebooks run the fork [hagopjay/x3d_mcp](https://github.com/hagopjay/x3d_mcp) at the commit in [`pins.toml`](src/x3dlab/pins.toml). The fork is ahead of upstream (exact-fix hints; ROUTE-order, colour-range and H-Anim checks; a blob store for scenes over the 4 MiB request limit) and pins `mcp<2`. Upstream asks for `mcp>=1.7` with no upper bound and imports `mcp.server.fastmcp`, which `mcp` 2.x removed, so a fresh install of upstream fails at start-up while `mcp` 2.x is current on PyPI.

## Cell 1: how the pieces connect

```
                  ┌──────────────── Colab VM ────────────────┐
your browser ◄──┐ │  gateway :8080  ── /mcp ──►  x3d_mcp :8000 │
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

## Private material

Some inputs are private: the Copilot Validator Demo build and other private modules of the x3d family. Notebooks load them only with your credentials (Drive, a private release, or `GITHUB_TOKEN`) into the Colab VM, never into this repo, and every notebook runs without them.

## Develop

```bash
pip install -e ".[server,notebook,tunnels,dev]"
X3DLAB_X3D_MCP_DIR=/path/to/x3d_mcp pytest           # uses your checkout; otherwise the pinned fork is cloned
python tools/build_notebooks.py                       # notebooks/src/*.py -> notebooks/*.ipynb
```

Edit notebooks in `notebooks/src/` (percent format, diff-friendly); the `.ipynb` files are generated. CI runs the tests and fails if a notebook is out of date. In a notebook, `X3DLAB_DEV=1` skips the pip install so a local checkout is used.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next.
