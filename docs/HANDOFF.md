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
