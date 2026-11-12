# BLACKBOX WebGPU Lab: Colab T4 / local GPU smoke test

The GPU lane is an **opt-in add-on**. The existing performance-forensics mission remains the CPU-oriented baseline and still launches Chrome with `--disable-gpu`. The WebGPU lane launches a separate isolated Chrome profile, runs a local deterministic WGSL compute test, and collects browser evidence through the official `chrome-devtools-mcp` server.

## What this smoke test does

1. **Host preflight** records Node/Chrome, `/dev/dri` and NVIDIA device nodes, `nvidia-smi`, and `vulkaninfo --summary` when available.
2. **MCP handshake** discovers the server's tools and records every JSON-RPC frame in `runs/<id>/wire.jsonl`.
3. **MCP browser flow** creates an isolated page, navigates to `/lab/webgpu/`, evaluates the browser result, reads console warnings/errors, and captures a screenshot.
4. The local page requests a WebGPU adapter/device, dispatches a WGSL kernel for 1,024 deterministic floats (`out[i] = in[i] * 2 + 1`), reads the output back, and checks numerical correctness.
5. It also records a WebGL2 availability/renderer hint, adapter metadata/features/selected limits, secure-context state, and submit-plus-readback wall time.
6. A self-contained `report.html`, `summary.json`, `host-preflight.json`, screenshot, and MCP wire log are saved under `runs/<id>/`.

**A passing compute result is not, by itself, proof of hardware acceleration.** Browser metadata is privacy-limited and can describe a software adapter. The report keeps host-side GPU evidence separate from browser evidence and labels the join conservatively. Submit/readback time includes buffer readback and is not kernel-only timing. This is a capability/correctness smoke test, not a model benchmark.

## Run locally or in a GPU VM

```bash
# Optional: install Vulkan diagnostic CLI, not an NVIDIA driver.
BLACKBOX_GPU_TOOLS=1 bash scripts/setup.sh

CHROME_PATH="$HOME/.cache/chrome-159/chrome-linux64/chrome" \
  "$HOME/.cache/rt/node-v22.20.0-linux-x64/bin/node" server.mjs
```

Then use **WebGPU Lab → Refresh preflight → Run MCP WebGPU smoke**. Headless CLI alternative:

```bash
CHROME_PATH="$HOME/.cache/chrome-159/chrome-linux64/chrome" \
  "$HOME/.cache/rt/node-v22.20.0-linux-x64/bin/node" \
  agent/webgpu-smoke.mjs --origin http://127.0.0.1:8080
```

For a GPU-required gate (nonzero exit if host GPU evidence or a correct compute result is missing):

```bash
CHROME_PATH="$HOME/.cache/chrome-159/chrome-linux64/chrome" \
  "$HOME/.cache/rt/node-v22.20.0-linux-x64/bin/node" \
  agent/webgpu-smoke.mjs --origin http://127.0.0.1:8080 --require-gpu
```

The gate checks for host-side GPU evidence plus a passing compute check. It does not prove that a particular GPU was selected by Chrome; inspect the adapter metadata and report caveat. For a strict T4-only gate, also inspect `hostPreflight.hostGpu.nvidiaSmi.gpus[].name` for `T4`.

## Colab notebook

Open `notebooks/BLACKBOX_WebGPU_Colab.ipynb` in Colab. Select **Runtime → Change runtime type → T4 GPU** (availability depends on the account/runtime), upload the project ZIP when prompted, run the cells, and download the run-artifact bundle. The notebook:

- installs Vulkan diagnostic utilities only; it does **not** replace Colab's NVIDIA driver;
- runs `nvidia-smi` and `vulkaninfo` as separate host diagnostics;
- runs the shared `scripts/setup.sh` for pinned Node 22, Chrome for Testing 159, and MCP dependencies;
- starts the control plane and invokes the same MCP-first smoke runner as the UI/CLI.

A CPU-only/unsupported runtime still produces an honest report rather than a fabricated GPU pass. Colab GPU entitlement, preinstalled driver compatibility, and physical-GPU access vary by runtime; the notebook must be run in Colab to validate T4 execution.

## Chrome profile / safety notes

`agent/mcp-process.mjs` has two named profiles:

- `cpu` (default): preserves the existing mission behavior and includes `--disable-gpu`.
- `webgpu` (opt-in): uses the notebook-inspired Vulkan/WebGPU switches and does not pass `--disable-gpu`.

The GPU profile includes experimental/unsafe WebGPU switches because it is a lab test. Use an isolated browser/profile and trusted local test content; do not copy these switches into a general-purpose or production browser. Flags are version-sensitive. The notebook and runner record their profile, and the browser version is pinned to make failures reproducible.

## Operative heuristics

- **Secure origin first:** WebGPU requires a secure context; localhost is treated as trustworthy. If testing a remote page, use HTTPS and report origin/permissions.
- **Presence is not use:** `navigator.gpu`, adapter acquisition, and a device are necessary but not sufficient. The known-answer compute/readback check is the actual correctness assertion.
- **Hardware identity is a separate axis:** `nvidia-smi` proves a host GPU is visible to the runtime; it does not prove Chrome selected it. Browser adapter metadata may be blank, generic, or privacy-redacted.
- **Treat software signatures as hints, not perfect classification:** SwiftShader/Lavapipe/llvmpipe names are strong software hints; absence of those strings does not prove physical acceleration.
- **Do not compare cold startup to warm throughput:** this smoke captures one submit-plus-readback interval only. For a model benchmark, separately report model fetch/decode, compilation, first inference, warm inference, repeated p50/p95, numerical parity, and selected backend.
- **GPU memory is not JS heap:** the existing heap-snapshot tools do not measure VRAM. Use driver/runtime telemetry for GPU memory and keep its provenance separate.
- **Change one variable at a time:** same GPU/driver/browser/model/input/precision; compare backend only after correctness checks pass.

## Scope and known limits

- Latest local result: compute **pass**, 0/1,024 mismatches on Chrome's SwiftShader adapter; host Vulkan reports CPU-only llvmpipe. This verifies the smoke harness and its software fallback, not physical GPU acceleration.
- This workspace has no `/dev/dri` device or `nvidia-smi`, so a physical T4 run could not be validated here.
- No TensorFlow.js model is loaded yet. The first implemented workload is the smaller WebGPU compute-correctness proof; model benchmarking is a follow-on.
- There is no cross-browser GPU matrix yet. Start with pinned Chrome and the target Colab T4, then add browser/runtime variants only after the baseline is stable.
