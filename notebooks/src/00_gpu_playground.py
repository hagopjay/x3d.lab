# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/00_GPU_Browser_Playground.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # GPU Browser Playground: the same web page on the CPU and on the GPU
#
# A browser can draw 3D (**WebGL**, **WebGPU**) and even compute (WebGPU *compute shaders*). In a cloud notebook there is no screen, so we run Chrome **headless**, and by default it draws with **SwiftShader**: a software GPU that runs on the CPU. This notebook gives headless Chrome the **real GPU**, then runs every experiment **twice, side by side**: once on SwiftShader (CPU) and once on the GPU.
#
# 1. **makes the GPU reachable**: Colab ships NVIDIA's *compute* driver but not its *graphics* half; we install it (Vulkan) at exactly the driver's version;
# 2. **launches two Chromes**: `cpu` (SwiftShader) and `gpu` (ANGLE on Vulkan), and asks each what it really got (`chrome://gpu`, the WebGPU adapter, its features and limits);
# 3. **computes**: a matrix multiply in **WGSL** (WebGPU's shader language) that *you* can edit, checked against NumPy, timed on both;
# 4. **draws**: thousands of instanced triangles per frame, timed on both, for a chart;
# 5. **renders X3D** with X_ITE in both, the engine the rest of x3d.lab tests.
#
# **Runtime:** *Runtime › Change runtime type* › any GPU (**T4** is free; L4, A100, G4 work). On a CPU runtime everything still runs; both columns are then SwiftShader, which is a fine lesson too.
#
# **Credit:** the driver fix and Chrome flags come from Google's [Supercharge Web AI model testing](https://developer.chrome.com/blog/supercharge-web-ai-testing) notebook by François Beaufort. This version uses Python + Playwright, compares CPU and GPU in every step, and adds tinkering.
#
# <details><summary><b>📖 Words used here</b> (click)</summary>
#
# | Word | Meaning |
# |---|---|
# | **headless** | the browser runs with no window; we read pages, screenshots and timings through code |
# | **SwiftShader** | Google's software implementation of Vulkan: correct everywhere, slow, CPU only |
# | **Vulkan** | a low-level GPU API; Chrome's **ANGLE** layer can run WebGL on it, and Dawn runs WebGPU on it |
# | **ICD** | *installable client driver*: the file that tells Vulkan which GPU driver library to load |
# | **WGSL** | WebGPU Shading Language: the code that runs on the GPU |
# | **workgroup** | a block of GPU threads that run together and can share fast memory |
# | **GFLOP/s** | billions of floating-point operations per second: a matrix multiply of size N does 2·N³ of them |
# </details>

# %%
#@title 1 · Is there a GPU, and can a browser use it? { display-mode: "form" }
import os, re, shutil, subprocess

def sh(cmd, timeout=600):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout

driver = sh("nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null").strip()
print("GPU:", driver or "none (CPU runtime): both columns will be SwiftShader")
icds = sh("ls /usr/share/vulkan/icd.d /etc/vulkan/icd.d 2>/dev/null")
print("Vulkan drivers (ICDs):", " ".join(icds.split()) or "none")
print("NVIDIA graphics libraries:", "present" if "libGLX_nvidia" in sh("ldconfig -p") else "missing (this is why a browser cannot use the GPU yet)")

# %% [markdown]
# ## 2 · Install the graphics half of the NVIDIA driver
#
# The compute driver (`nvidia-smi`, CUDA) is there; the graphics userspace (Vulkan ICD, GL libraries) is not. The package `libnvidia-gl-<branch>` adds it, but **only at exactly the same version as the driver**: a different version would break the GPU for everything, so this cell looks for an exact match and installs nothing otherwise.

# %%
#@title 2 · Install libnvidia-gl (exact driver version) and vulkan-tools { display-mode: "form" }
INSTALL = True  #@param {type:"boolean"}
version = driver.split(",")[-1].strip() if driver else ""
if not version:
    print("No NVIDIA GPU: nothing to install.")
elif not INSTALL:
    print("Skipped (INSTALL is off).")
else:
    sh("apt-get update -qq")
    major, pick = version.split(".")[0], None
    for pkg in (f"libnvidia-gl-{major}", f"libnvidia-gl-{major}-server"):
        for line in sh(f"apt-cache madison {pkg}").splitlines():
            v = line.split("|")[1].strip()
            if re.match(r"^(\d+:)?%s([-~+].*)?$" % re.escape(version), v):
                pick = pick or (pkg, v)
    if pick:
        print("installing %s=%s ..." % pick)
        sh("apt-get install -y -qq --no-install-recommends %s=%s vulkan-tools" % pick, 900)
    else:
        print("No package at exactly %s in the apt sources: skipping (a mismatched version would break the driver)." % version)
devices = re.findall(r"deviceName\s*=\s*(.+)", sh("vulkaninfo --summary 2>/dev/null"))
print("Vulkan sees:", devices or "nothing")
print("→ the gpu column below will be the real GPU" if any(not re.search("llvmpipe|swiftshader", d, re.I) for d in devices)
      else "→ the gpu column below will fall back to SwiftShader")

# %% [markdown]
# ## 3 · Two Chromes: `cpu` and `gpu`
#
# Same browser, different flags. `cpu` forces SwiftShader. `gpu` asks ANGLE to use Vulkan, and `--disable-vulkan-surface` matters: headless Chrome has no window, so there is no *surface* to give Vulkan.
#
# WebGPU only works in a **secure context**: `https://` or `http://localhost`. So pages are served by a tiny web server on `127.0.0.1`, started below.

# %%
#@title 3 · Install Playwright, start a local web server, define the two browser modes { display-mode: "form" }
import sys, threading, http.server, functools, tempfile, json, asyncio, time
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright", "nest_asyncio", "numpy", "matplotlib"], check=True)
CHROMIUM = os.environ.get("CHROMIUM_EXECUTABLE")          # use an existing Chromium if you have one
if not CHROMIUM:
    subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
import nest_asyncio; nest_asyncio.apply()
from playwright.async_api import async_playwright

COMMON = ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-gpu-blocklist", "--enable-unsafe-webgpu",
          "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
          "--disable-frame-rate-limit", "--disable-gpu-vsync"]   # no 60 frames/s cap: step 7 measures the engine, not the clock
MODES = {
    "cpu": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] + COMMON,
    "gpu": ["--use-angle=vulkan", "--enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan", "--disable-vulkan-surface",
            "--enable-unsafe-swiftshader"] + COMMON,
}

WWW = tempfile.mkdtemp(prefix="www_")
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=WWW))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]

def page_file(name, body):
    """Write an HTML page into the local web server's folder and return its URL."""
    open(os.path.join(WWW, name), "w").write("<!doctype html><meta charset=utf-8><body style='margin:0'>" + body)
    return "%s/%s" % (BASE, name)

async def in_browser(mode, url, js, viewport=(640, 360), timeout=120_000):
    """Open `url` in a fresh headless Chrome of the given mode, run `js` (an async function body), return its result."""
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=True, args=MODES[mode], **({"executable_path": CHROMIUM} if CHROMIUM else {}))
        try:
            page = await br.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            logs = []
            page.on("console", lambda m: logs.append(m.text))
            await page.goto(url)
            out = await page.evaluate("async () => { %s }" % js) if js else None
            return {"out": out, "logs": logs, "page": None}
        finally:
            await br.close()

def both(url, js, **kw):
    """Run the same thing in the cpu and the gpu Chrome; returns {'cpu': ..., 'gpu': ...}."""
    return {m: asyncio.get_event_loop().run_until_complete(in_browser(m, url, js, **kw)) for m in MODES}

print("web server:", BASE, "| Chromium:", CHROMIUM or "Playwright's")

# %% [markdown]
# ## 4 · What did each Chrome really get?
#
# The WebGL renderer string names the device ANGLE draws with. The WebGPU **adapter** is the device WebGPU computes and draws with; its `info` says vendor and architecture, its **features** are optional abilities (16-bit floats, timestamp queries, ...), its **limits** say how big things may get.

# %%
#@title 4 · Renderer, adapter, features, limits { display-mode: "form" }
probe_js = """
  const c = document.createElement('canvas').getContext('webgl2');
  const d = c && c.getExtension('WEBGL_debug_renderer_info');
  const webgl = c ? (d ? c.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'webgl2') : 'none';
  if (!navigator.gpu) return {webgl, webgpu: 'navigator.gpu missing'};
  const a = await navigator.gpu.requestAdapter({powerPreference: 'high-performance'});
  if (!a) return {webgl, webgpu: 'no adapter'};
  const info = a.info || (a.requestAdapterInfo ? await a.requestAdapterInfo() : {});
  const L = a.limits, keys = ['maxComputeWorkgroupSizeX','maxComputeInvocationsPerWorkgroup','maxComputeWorkgroupStorageSize',
    'maxStorageBufferBindingSize','maxBufferSize','maxTextureDimension2D'];
  return {webgl, webgpu: [info.vendor, info.architecture, info.device, info.description].filter(Boolean).join(' · ') || 'adapter (no info)',
          fallback: !!(info.isFallbackAdapter || a.isFallbackAdapter), features: [...a.features].sort(),
          limits: Object.fromEntries(keys.map(k => [k, L[k]]))};
"""
rep = both(page_file("probe.html", "probe"), probe_js)
for m in MODES:
    o = rep[m]["out"]
    print("── %s ──" % m)
    print("  WebGL :", o["webgl"])
    print("  WebGPU:", o["webgpu"], "(fallback adapter)" if o.get("fallback") else "")
    if o.get("limits"):
        print("  limits:", ", ".join("%s=%s" % kv for kv in o["limits"].items()))
        print("  features (%d): %s" % (len(o["features"]), ", ".join(o["features"])))
only_gpu = sorted(set(rep["gpu"]["out"].get("features", [])) - set(rep["cpu"]["out"].get("features", [])))
print("\nfeatures only the gpu Chrome has:", only_gpu or "none (is the gpu column really a GPU?)")

# %% [markdown]
# ## 5 · Compute: a matrix multiply you can edit
#
# `C = A × B` for two N×N matrices, written in WGSL. Each GPU thread computes one element of C. The page uploads A and B (the same random numbers in both Chromes), runs the shader `RUNS` times, reads C back, and Python checks it against NumPy.
#
# **Tinker:** change `N` and `WORKGROUP`, or edit the kernel. Ideas: a *tiled* version that loads blocks of A and B into `var<workgroup>` memory first (often several times faster on a real GPU), `f16` (needs the `shader-f16` feature), or a deliberate bug to see the NumPy check catch it.

# %%
#@title 5 · WGSL matrix multiply on cpu and gpu { display-mode: "form" }
import numpy as np
N = 512  #@param {type:"slider", min:64, max:2048, step:64}
WORKGROUP = 8  #@param [4, 8, 16]
RUNS = 5  #@param {type:"slider", min:1, max:20, step:1}

KERNEL = """
@group(0) @binding(0) var<storage, read> A : array<f32>;
@group(0) @binding(1) var<storage, read> B : array<f32>;
@group(0) @binding(2) var<storage, read_write> C : array<f32>;
override N : u32;

@compute @workgroup_size(WG, WG)
fn main(@builtin(global_invocation_id) id : vec3u) {
  if (id.x >= N || id.y >= N) { return; }
  var s = 0.0;
  for (var k = 0u; k < N; k++) {
    s += A[id.y * N + k] * B[k * N + id.x];
  }
  C[id.y * N + id.x] = s;
}
"""

rng = np.random.default_rng(7)
A = rng.random((N, N), dtype=np.float32); B = rng.random((N, N), dtype=np.float32)
open(os.path.join(WWW, "a.bin"), "wb").write(A.tobytes()); open(os.path.join(WWW, "b.bin"), "wb").write(B.tobytes())
matmul_js = """
  const N = %d, RUNS = %d, code = %s;
  const ad = await navigator.gpu.requestAdapter({powerPreference: 'high-performance'});
  const dev = await ad.requestDevice({requiredLimits: {maxStorageBufferBindingSize: ad.limits.maxStorageBufferBindingSize,
                                                     maxBufferSize: ad.limits.maxBufferSize}});
  const errs = []; dev.addEventListener('uncapturederror', e => errs.push(e.error.message));
  const get = async f => new Float32Array(await (await fetch(f)).arrayBuffer());
  const a = await get('a.bin'), b = await get('b.bin'), bytes = N * N * 4;
  const buf = (data, usage) => { const x = dev.createBuffer({size: bytes, usage, mappedAtCreation: !!data});
                                 if (data) { new Float32Array(x.getMappedRange()).set(data); x.unmap(); } return x; };
  const S = GPUBufferUsage.STORAGE;
  const A = buf(a, S), B = buf(b, S), C = buf(null, S | GPUBufferUsage.COPY_SRC);
  const R = dev.createBuffer({size: bytes, usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST});
  const mod = dev.createShaderModule({code});
  const info = await mod.getCompilationInfo();
  const msgs = info.messages.map(m => m.type + ' line ' + m.lineNum + ': ' + m.message);
  if (info.messages.some(m => m.type === 'error')) return {error: msgs.join('\\n')};
  const pipe = await dev.createComputePipelineAsync({layout: 'auto', compute: {module: mod, entryPoint: 'main', constants: {N}}});
  const bg = dev.createBindGroup({layout: pipe.getBindGroupLayout(0), entries: [A, B, C].map((x, i) => ({binding: i, resource: {buffer: x}}))});
  const wg = %d, groups = Math.ceil(N / wg);
  const once = async () => { const e = dev.createCommandEncoder(), p = e.beginComputePass();
    p.setPipeline(pipe); p.setBindGroup(0, bg); p.dispatchWorkgroups(groups, groups); p.end();
    dev.queue.submit([e.finish()]); await dev.queue.onSubmittedWorkDone(); };
  await once();                                     // warm-up: pipeline creation and first dispatch are not timed
  const times = [];
  for (let i = 0; i < RUNS; i++) { const t = performance.now(); await once(); times.push(performance.now() - t); }
  const e = dev.createCommandEncoder(); e.copyBufferToBuffer(C, 0, R, 0, bytes); dev.queue.submit([e.finish()]);
  await R.mapAsync(GPUMapMode.READ); const c = Array.from(new Float32Array(R.getMappedRange()).subarray(0, 64)); R.unmap();
  times.sort((x, y) => x - y);
  return {ms: times[Math.floor(times.length / 2)], all: times, first64: c, errs, msgs};
""" % (N, RUNS, json.dumps(KERNEL.replace("WG", str(WORKGROUP))), WORKGROUP)
res = both(page_file("matmul.html", "matmul"), matmul_js, timeout=600_000)
want = (A @ B).ravel()[:64]
for m in MODES:
    o = res[m]["out"]
    if not o or o.get("error"):
        print("%s: shader error\n%s" % (m, (o or {}).get("error"))); continue
    ok = np.allclose(o["first64"], want, rtol=1e-3, atol=1e-2)
    gflops = 2 * N**3 / (o["ms"] / 1000) / 1e9
    print("%-3s  N=%d  median %.2f ms  →  %.1f GFLOP/s   result %s%s" % (
        m, N, o["ms"], gflops, "matches NumPy ✓" if ok else "DIFFERS from NumPy ✗", ("  errors: %s" % o["errs"]) if o["errs"] else ""))
if all(res[m]["out"] and not res[m]["out"].get("error") for m in MODES):
    print("gpu is %.1f× the cpu column" % (res["cpu"]["out"]["ms"] / res["gpu"]["out"]["ms"]))

# %% [markdown]
# ## 6 · Draw: how many triangles per frame?
#
# WebGPU draws `COUNT` small triangles (one *instanced* draw call) into an offscreen texture, `FRAMES` times, for each count on the ladder. Python charts milliseconds per frame. On SwiftShader the time grows with the number of pixels and triangles almost immediately; on a GPU the line stays flat much longer.
#
# **Tinker:** make the triangles bigger (`SIZE`) to load *fill rate* instead of geometry, or raise the resolution.

# %%
#@title 6 · Instanced-triangle ladder on cpu and gpu { display-mode: "form" }
import matplotlib.pyplot as plt
LADDER = "1000, 10000, 100000, 1000000"  #@param {type:"string"}
SIZE = 0.02  #@param {type:"number"}
FRAMES = 20  #@param {type:"slider", min:5, max:120, step:5}
RES = "1280x720"  #@param ["640x360", "1280x720", "1920x1080", "3840x2160"]
counts = [int(x) for x in LADDER.replace(" ", "").split(",") if x]
W, H = (int(v) for v in RES.split("x"))
draw_js = """
  const counts = %s, FRAMES = %d, SIZE = %f, W = %d, H = %d;
  const ad = await navigator.gpu.requestAdapter({powerPreference: 'high-performance'}), dev = await ad.requestDevice();
  const code = `
    struct O { @builtin(position) p : vec4f, @location(0) c : vec3f };
    fn h(n : u32) -> f32 { var x = n * 747796405u + 2891336453u; x = ((x >> ((x >> 28u) + 4u)) ^ x) * 277803737u; return f32((x >> 22u) ^ x) / 4294967295.0; }
    @vertex fn vs(@builtin(vertex_index) v : u32, @builtin(instance_index) i : u32) -> O {
      let corner = array<vec2f, 3>(vec2f(0, 1), vec2f(-0.87, -0.5), vec2f(0.87, -0.5))[v];
      let centre = vec2f(h(i * 2u), h(i * 2u + 1u)) * 2.0 - 1.0;
      var o : O; o.p = vec4f(centre + corner * ${SIZE}, 0, 1); o.c = vec3f(h(i), h(i + 7u), h(i + 13u)); return o; }
    @fragment fn fs(o : O) -> @location(0) vec4f { return vec4f(o.c, 1); }`;
  const mod = dev.createShaderModule({code});
  const pipe = await dev.createRenderPipelineAsync({layout: 'auto', vertex: {module: mod, entryPoint: 'vs'},
    fragment: {module: mod, entryPoint: 'fs', targets: [{format: 'rgba8unorm'}]}, primitive: {topology: 'triangle-list'}});
  const tex = dev.createTexture({size: [W, H], format: 'rgba8unorm', usage: GPUTextureUsage.RENDER_ATTACHMENT});
  const frame = async n => { const e = dev.createCommandEncoder();
    const p = e.beginRenderPass({colorAttachments: [{view: tex.createView(), loadOp: 'clear', storeOp: 'store', clearValue: [0.05, 0.05, 0.1, 1]}]});
    p.setPipeline(pipe); p.draw(3, n); p.end(); dev.queue.submit([e.finish()]); await dev.queue.onSubmittedWorkDone(); };
  const out = [];
  for (const n of counts) {
    await frame(n);
    const t = performance.now(); for (let f = 0; f < FRAMES; f++) await frame(n);
    out.push({n, ms: (performance.now() - t) / FRAMES});
  }
  return out;
""" % (json.dumps(counts), FRAMES, SIZE, W, H)
dr = both(page_file("draw.html", "draw"), draw_js, timeout=900_000)
fig, ax = plt.subplots(figsize=(7, 3.6))
for m, colour in (("cpu", "#8a8f98"), ("gpu", "#2f6db5")):
    pts = dr[m]["out"] or []
    ax.plot([p["n"] for p in pts], [p["ms"] for p in pts], "o-", color=colour, label=m)
    for p in pts:
        print("%-3s %9d triangles  %8.2f ms/frame" % (m, p["n"], p["ms"]))
ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("triangles per frame"); ax.set_ylabel("ms per frame")
ax.set_title("Instanced triangles at %s" % RES); ax.grid(alpha=0.3, which="both"); ax.legend(frameon=False)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
plt.show()

# %% [markdown]
# ## 7 · X3D: one scene, X_ITE, both Chromes
#
# X_ITE is the X3D browser the rest of x3d.lab uses. The page loads it from a CDN, builds a grid of `BOXES` spinning boxes, lets it settle, then measures frames per second for a few seconds. This is the number our trace experiments always measured on SwiftShader, until now.

# %%
#@title 7 · X_ITE frames per second on cpu and gpu { display-mode: "form" }
BOXES = 400  #@param {type:"slider", min:25, max:5000, step:25}
SECONDS = 4  #@param {type:"slider", min:2, max:15, step:1}
XITE = "https://cdn.jsdelivr.net/npm/x_ite@16.7.0/dist/x_ite.min.js"
side = max(1, int(round(BOXES ** 0.5)))
cells = "".join('<Transform translation="%g %g 0"><Shape USE="Cube"/></Transform>' % (x * 1.5 - side * 0.75, y * 1.5 - side * 0.75)
                for x in range(side) for y in range(side))
scene = ('<X3D profile="Interchange" version="4.0"><Scene><Viewpoint position="0 0 %g"/>'
         '<Transform DEF="Spin"><Transform><Shape DEF="Cube"><Appearance><Material diffuseColor="0.9 0.5 0.1"/></Appearance><Box size="1 1 1"/></Shape></Transform>%s</Transform>'
         '<TimeSensor DEF="T" cycleInterval="8" loop="true"/><OrientationInterpolator DEF="R" key="0 0.5 1" keyValue="0 1 0 0 0 1 0 3.1416 0 1 0 6.2832"/>'
         '<ROUTE fromNode="T" fromField="fraction_changed" toNode="R" toField="set_fraction"/>'
         '<ROUTE fromNode="R" fromField="value_changed" toNode="Spin" toField="set_rotation"/></Scene></X3D>') % (side * 2.2, cells)
url = page_file("x3d.html", '<script src="%s"></script><x3d-canvas id="c" style="width:640px;height:360px" update="always">%s</x3d-canvas>' % (XITE, scene))
fps_js = """
  if (typeof X3D === 'undefined') return {error: 'X_ITE did not load from the CDN (is this machine online?)'};
  await new Promise(r => { const t0 = performance.now(); (function wait() {
    const b = document.getElementById('c').browser; if (b && b.currentScene && b.currentScene.rootNodes.length) r(); else if (performance.now() - t0 < 60000) setTimeout(wait, 100); else r(); })(); });
  await new Promise(r => setTimeout(r, 1500));
  let frames = 0; const t = performance.now(), dur = %d * 1000;
  await new Promise(r => { (function tick() { frames++; if (performance.now() - t < dur) requestAnimationFrame(tick); else r(); })(); });
  return {fps: frames / ((performance.now() - t) / 1000), version: X3D.getBrowser ? X3D.getBrowser().version : '?'};
""" % SECONDS
fr = both(url, fps_js, timeout=180_000)
for m in MODES:
    o = fr[m]["out"] or {}
    if o.get("error"):
        print("%-3s %s" % (m, o["error"])); continue
    print("%-3s %5d boxes  %6.1f frames/s  (X_ITE %s)" % (m, side * side, o.get("fps", 0), o.get("version", "?")))

# %% [markdown]
# ## 8 · Ideas to try
#
# - **Tile the matrix multiply** (step 5): give each workgroup a `var<workgroup> tileA : array<f32, 256>` and `tileB`, load a 16×16 block of each, `workgroupBarrier()`, multiply, repeat. How much faster on the GPU? On the CPU column?
# - **Find the crossover** (step 6): at what triangle count does the gpu line start to rise? Is it geometry or fill rate? Change `SIZE` to tell.
# - **Precision** (step 5): switch to `f16` (add `enable f16;` and request the `shader-f16` feature). How far from NumPy does it drift as N grows?
# - **Why are some limits different?** Compare `maxComputeInvocationsPerWorkgroup` between the columns in step 4, and set `WORKGROUP = 16` (256 threads). What happens on each?
# - **X3D at scale** (step 7): raise `BOXES` until the gpu column drops below 30 frames/s. Then try `IndexedFaceSet` meshes instead of boxes.
# - **Go further:** the x3d.lab notebook **05 GPU Hog** uses this same fix (`x3dlab.graphics.enable()`) to run X_ITE traces and Cycles' OptiX denoiser on the real GPU.
