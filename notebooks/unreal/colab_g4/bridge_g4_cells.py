# x3d.loom Babylon WebGPU bridge on the Colab G4: the bridge's own fixture suite, X_ITE as oracle, on the real GPU.
# Paste each cell into the notebook 07 session after its Vulkan step (step 8 there set up the NVIDIA Vulkan ICD).
# Nothing here touches Unreal, the engine cache or the DDC. Disk: about 0.5 GB under /content/bridge. No paid API.

#@title B0 · Bridge setup: Node 22, x3d.loom 403afa0 (sparse), Playwright 1.62.1 Chromium, Babylon 9.32.0, X_ITE 16.6.0, GPU patch, GPU gate
# Weight: about 3 minutes, CPU and network only (about 250 MB download).
import subprocess, os, json, glob, pathlib, shutil, textwrap
def sh(c, check=True):
    print("$", c[:160]); r = subprocess.run(c, shell=True, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out: print(textwrap.indent(out[-1500:], "  "))
    if check and r.returncode: raise SystemExit(f"rc {r.returncode}")
    return r.stdout
BR_ROOT = "/content/bridge"; os.makedirs(BR_ROOT, exist_ok=True)
# Node 20 or newer (the bridge's package.json says >=20)
nv = subprocess.run("node -v", shell=True, capture_output=True, text=True).stdout.strip()
if not nv.startswith(("v2", "v3")) or int(nv[1:].split(".")[0]) < 20:
    sh(f"cd {BR_ROOT} && curl -fsSL https://nodejs.org/dist/v22.12.0/node-v22.12.0-linux-x64.tar.xz | tar xJ")
    os.environ["PATH"] = f"{BR_ROOT}/node-v22.12.0-linux-x64/bin:" + os.environ["PATH"]
print("node", sh("node -v").strip())
# x3d.loom at the pinned commit, only the x3d.webgpu folder (about 20 MB)
LOOM = f"{BR_ROOT}/x3d.loom"; PIN = "403afa0"
if not os.path.isdir(LOOM):
    sh(f"git clone -q --filter=blob:none --no-checkout https://github.com/hagopjay/x3d.loom {LOOM}")
    sh(f"cd {LOOM} && git sparse-checkout set x3d.webgpu && git checkout -q {PIN}")
BRIDGE = f"{LOOM}/x3d.webgpu/bridge"
sh(f"cd {BRIDGE} && npm ci --no-audit --no-fund --loglevel=error")
sh(f"cd {BRIDGE} && npx playwright install --with-deps chromium", check=False)
# engines: the versions the bridge's receipts name
for pkg, d in (("babylonjs@9.32.0", "babylon"), ("x_ite@16.6.0", "xite")):
    if not os.path.isdir(f"{BR_ROOT}/{d}/package"):
        os.makedirs(f"{BR_ROOT}/{d}", exist_ok=True); sh(f"cd {BR_ROOT}/{d} && npm pack {pkg} --silent && tar xzf *.tgz")
os.environ.update(XITE_DIR=f"{BR_ROOT}/xite/package/dist", BABYLON_JS=f"{BR_ROOT}/babylon/package/babylon.js")
# the GPU patch: BRIDGE_GPU=1 swaps the SwiftShader launch flags for ANGLE Vulkan + the hardware WebGPU adapter, records adapter names and ms per fixture
PATCH = r'''
import sys, pathlib
B = pathlib.Path(sys.argv[1]) / "tools"
SW = "'--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-webgpu-adapter=swiftshader'"
GPU = ("...(process.env.BRIDGE_GPU === '1' ? ['--use-angle=vulkan', '--enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan', "
       "'--disable-vulkan-surface', '--ignore-gpu-blocklist', '--disable-dev-shm-usage'] : [" + SW + "])")
WEBGPU = ("env.webgpu = await pos.evaluate(async () => { const a = await navigator.gpu?.requestAdapter(); "
          "const i = a && (a.info || (a.requestAdapterInfo && await a.requestAdapterInfo())); "
          "return i ? [i.vendor, i.architecture, i.device, i.description].filter(Boolean).join(' / ') : 'no adapter'; }).catch((e) => 'probe error ' + e); ")
WEBGL = ("env.webgl = await px.evaluate(() => { const g = document.createElement('canvas').getContext('webgl2'); "
         "const e = g && g.getExtension('WEBGL_debug_renderer_info'); "
         "return g ? g.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : g.RENDERER) : 'no webgl2'; }).catch((e) => 'probe error ' + e); ")
EDITS = {
    "run-tests.js": [
        (SW, GPU),
        ("' (WebGL2, SwiftShader)'; }", "' (WebGL2, ' + (process.env.BRIDGE_GPU === '1' ? 'GPU requested, see env.webgl' : 'SwiftShader') + ')'; " + WEBGL + "}"),
        ("env.babylon = await pos.evaluate(() => window.sceneReady.then(() => BABYLON.Engine.Version)); }",
         "env.babylon = await pos.evaluate(() => window.sceneReady.then(() => BABYLON.Engine.Version)); " + WEBGPU + "}"),
        ("  for (const n of names) {\n", "  for (const n of names) {\n    const __t0 = Date.now();\n"),
        ("    results[n] = r;\n", "    r.ms = Date.now() - __t0; results[n] = r;\n"),
    ],
    "smoke.js": [(SW, GPU)],
}
for f, edits in EDITS.items():
    p = B / f; s = p.read_text()
    if "BRIDGE_GPU" in s:
        print("already patched", f); continue
    for old, new in edits:
        assert s.count(old) == 1, f"{f}: anchor found {s.count(old)} times: {old[:70]}"
        s = s.replace(old, new)
    p.write_text(s); print("patched", f, len(edits), "edits")
'''
pathlib.Path(f"{BR_ROOT}/gpu_patch.py").write_text(PATCH)
sh(f"python3 {BR_ROOT}/gpu_patch.py {BRIDGE} && node --check {BRIDGE}/tools/run-tests.js && node --check {BRIDGE}/tools/smoke.js")
# GPU gate: one fixture through smoke.js; the WebGPU adapter must be NVIDIA, not SwiftShader. Tries Playwright's headless shell, then full Chromium.
PWB = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
full = sorted(glob.glob(f"{PWB}/chromium-*/chrome-linux*/chrome"))
CHROME = None
for exe in [None] + full[-1:]:
    env = dict(os.environ, BRIDGE_GPU="1"); env.pop("CHROMIUM_EXECUTABLE", None)
    if exe: env["CHROMIUM_EXECUTABLE"] = exe
    r = subprocess.run(f"cd {BRIDGE} && node tools/smoke.js fixtures/Circle2D.x3d 2", shell=True, capture_output=True, text=True, env=env, timeout=180)
    try: res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: res = {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
    print("gate", exe or "playwright headless shell", "->", json.dumps({k: res.get(k) for k in ("ok", "adapter", "babylon", "frames", "pageErrors", "error")})[:400])
    if res.get("ok") and "swiftshader" not in json.dumps(res.get("adapter", "")).lower():
        CHROME = exe or ""; break
if CHROME is None:
    raise SystemExit("GPU gate FAILED: WebGPU still lands on SwiftShader (or no adapter). Paste the two gate lines above.")
if CHROME: os.environ["CHROMIUM_EXECUTABLE"] = CHROME
else: os.environ.pop("CHROMIUM_EXECUTABLE", None)
print("✓ GPU gate passed:", CHROME or "playwright headless shell")
print(sh("nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader").strip())

#@title B1 · Every bridge fixture on the real GPU, then a sample on SwiftShader on the same G4 (the receipts' rig), side by side
# Weight: Likely 10 to 20 minutes. Runs in chunks of 12 fixtures per Chromium, because one long-lived page crashed
# ("Target crashed") after 87 fixtures in a SwiftShader dry run of this suite, and every later fixture then failed.
# Pictures: every scene fixture's ours | X_ITE shot from the GPU pass.
import time, datetime, statistics, random
SW_SAMPLE = 40   #@param {type:"integer"}   how many fixtures also run on SwiftShader (0 = none, 999 = all)
CHUNK = 12
STAMP = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
OUT = f"/content/drive/MyDrive/x3dlab/bridge-g4/{STAMP}"; os.makedirs(OUT, exist_ok=True)
NAMES = sorted(p.name[:-10] for p in pathlib.Path(BRIDGE, "fixtures").glob("*.test.json"))
def run(gpu, names, shots):
    env = dict(os.environ, BRIDGE_GPU="1" if gpu else "0")
    if not gpu: env.pop("CHROMIUM_EXECUTABLE", None)
    merged = {"env": None, "results": {}, "wall_s": 0.0, "crashed_chunks": []}
    for i in range(0, len(names), CHUNK):
        part = names[i:i + CHUNK]; t = time.time()
        r = subprocess.run(["node", "tools/run-tests.js"] + (["--shots", shots] if shots else []) + part, cwd=BRIDGE, capture_output=True, text=True, env=env, timeout=1800)
        merged["wall_s"] += time.time() - t
        try: d = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception: merged["crashed_chunks"].append([part[0], (r.stderr or r.stdout)[-300:]]); continue
        merged["env"] = merged["env"] or d.get("env"); merged["results"].update(d.get("results", {}))
        if any("crashed" in str(v.get("ours", "")) for v in d.get("results", {}).values()): merged["crashed_chunks"].append([part[0], "page crashed"])
        print(f"  {'GPU' if gpu else 'SwiftShader'} {min(i + CHUNK, len(names))}/{len(names)}  {time.time() - t:.0f} s", end="\r")
    merged["wall_s"] = round(merged["wall_s"], 1); print(); return merged
print(f"▶ GPU pass, {len(NAMES)} fixtures"); G = run(True, NAMES, f"{OUT}/shots"); json.dump(G, open(f"{OUT}/bridge_gpu.json", "w"))
SWN = NAMES if SW_SAMPLE >= len(NAMES) else sorted(random.Random(7).sample(NAMES, SW_SAMPLE))
W = {"env": {}, "results": {}, "wall_s": 0, "crashed_chunks": []}
if SWN:
    print(f"▶ SwiftShader pass on the same G4, {len(SWN)} fixtures"); W = run(False, SWN, None); json.dump(W, open(f"{OUT}/bridge_swiftshader.json", "w"))
for lab, d in (("GPU", G), ("SwiftShader", W)):
    rs = d["results"]
    if not rs: continue
    e = d["env"] or {}; ms = [v.get("ms", 0) for v in rs.values()]
    print(f"\n{lab}: {sum(v.get('ok', False) for v in rs.values())}/{len(rs)} pass, wall {d['wall_s']} s, median {statistics.median(ms):.0f} ms per fixture, crashed chunks {len(d['crashed_chunks'])}")
    print("  chromium", e.get("chromium"), "| babylon", e.get("babylon"), "|", e.get("xite"))
    print("  webgpu:", e.get("webgpu"), "\n  webgl2:", e.get("webgl"))
    if e.get("pageErrors"): print("  page errors:", e["pageErrors"][:3])
both = [n for n in SWN if n in G["results"] and n in W["results"]]
flip = [n for n in both if G["results"][n].get("ok") != W["results"][n].get("ok")]
print(f"\nverdict differs GPU vs SwiftShader on {len(flip)} of {len(both)} shared fixture(s):")
for n in flip:
    print(f"  {n}: GPU {'pass' if G['results'][n].get('ok') else 'FAIL'} | SwiftShader {'pass' if W['results'][n].get('ok') else 'FAIL'}")
    print("     GPU ours:", str(G["results"][n].get("ours"))[:220]); print("     GPU xite:", str(G["results"][n].get("xite"))[:160])
fails = [n for n in NAMES if not G["results"].get(n, {}).get("ok")]
print(f"\nGPU failures ({len(fails)}):")
for n in fails: print(f"  {n}: ours {str(G['results'].get(n, {}).get('ours'))[:160]} | xite {str(G['results'].get(n, {}).get('xite'))[:80]}")
sp = sorted(((W["results"][n].get("ms", 0) / max(1, G["results"][n].get("ms", 1)), n) for n in both), reverse=True)
if sp: print("speed, SwiftShader ms / GPU ms: median %.1fx; biggest:" % statistics.median(x for x, _ in sp), ", ".join(f"{n} {x:.1f}x" for x, n in sp[:8]))
dv = [(m.get("iou"), m.get("mad"), n, s) for n, v in G["results"].items() for s, m in (v.get("visual") or {}).items()]
print(f"\nscene shots with metrics: {len(dv)}; lowest IoU ours vs X_ITE on the GPU:")
for iou, mad, n, s in sorted(dv, key=lambda x: (x[0] is None, x[0] or 0))[:10]: print(f"  {n}.{s}: IoU {iou}, MAD {mad}")
print("\nrig: Colab G4", sh("nvidia-smi --query-gpu=name,driver_version --format=csv,noheader").strip(), "| x3d.loom", PIN, "| saved", OUT)

#@title B2 · Contact sheet: every scene fixture, ours (Babylon WebGPU bridge) | X_ITE, on the real GPU
# Weight: seconds. Writes contact_sheet.png beside the run.
from PIL import Image, ImageDraw
from IPython.display import display
pairs = sorted(glob.glob(f"{OUT}/shots/*.png")); pairs = [p for p in pairs if not p.endswith((".ours.png", ".xite.png"))]
cols, cw = 4, 640; tiles = []
for p in pairs:
    im = Image.open(p).convert("RGB"); im = im.resize((cw, int(im.height * cw / im.width)))
    lab = Image.new("RGB", (cw, im.height + 18), (20, 20, 24)); lab.paste(im, (0, 18))
    ImageDraw.Draw(lab).text((4, 3), os.path.basename(p)[:-4] + "   (ours | X_ITE)", fill=(230, 230, 230)); tiles.append(lab)
if tiles:
    th = max(t.height for t in tiles); rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, rows * th), (10, 10, 12))
    for i, t in enumerate(tiles): sheet.paste(t, ((i % cols) * cw, (i // cols) * th))
    sheet.save(f"{OUT}/contact_sheet.png"); print(len(tiles), "shots →", f"{OUT}/contact_sheet.png"); display(sheet.resize((sheet.width // 2, sheet.height // 2)))
else: print("no shots written; paste B1's output")
