"""One glb, every engine: the X_ITE-look Gobbler (gobbler.write_glb_xite) in Blender Cycles and Babylon.js, from the
X3D viewpoints, with the GPU sampled while each renders (Unreal goes through pipeline.render_grades as before).

    V = everywhere.views(bake, ["EyeToEye", "Flywheel"])
    cy = everywhere.cycles(BL.cmd, glb, V, out_dir, res=(3840, 2160), samples=512)       # Blender, GPU
    bj = everywhere.babylon(glb, V, out_dir, res=(3840, 2160))                            # Chrome: WebGPU, else WebGL2
    everywhere.gpu_chart({"Cycles": cy["gpu_series"], "Babylon": bj["gpu_series"]}, "gpu.png")

Babylon runs in headless Chrome on the GPU (ANGLE on Vulkan, Dawn for WebGPU; flags from x3dlab.browser) and serves its
page from 127.0.0.1, a secure context, which WebGPU needs.
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import math
import os
import shutil
import socketserver
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import numpy as np

from . import gobbler, uerun

HERE = Path(__file__).resolve().parent
CYCLES_SCRIPT = HERE.parent / "bl_scripts" / "gob_cycles.py"
BABYLON_VERSION = "9.32.0"            # the version x3dlab pins for x3d.loom (pins.toml [x3d_loom] babylon)


# ---------------------------------------------------------------------- shared
def views(bake, names=None) -> dict:
    """{name: {position, orientation, fov}} in X3D coordinates, for the scripts and the page."""
    out = {}
    for k, v in bake["viewpoints"].items():
        if names and k not in names:
            continue
        out[k] = {"position": [float(x) for x in v["position"]], "orientation": [float(x) for x in v["orientation"]], "fov": float(v["fov"])}
    return out


def studio_lights(bake, watts=300.0, intensity=1.0) -> list:
    """The FacetShader's four lights for engines that need real lights (Cycles watts, Babylon intensity)."""
    return [{"name": L["DEF"], "location": L["location"], "color": L["color"], "watts": watts, "intensity": intensity}
            for L in gobbler.studio_lights(bake["uniforms"])]


def bloom(png_in, png_out=None, threshold=0.72, intensity=0.9, radii=(0.004, 0.012, 0.035)) -> str:
    """A filmic bloom in post (for Cycles, which has no bloom of its own in a headless render): the parts over
    `threshold`, blurred at three radii (fractions of the image height), added back."""
    from PIL import Image, ImageFilter
    im = Image.open(png_in).convert("RGB")
    a = np.asarray(im, float) / 255.0
    bright = np.clip((a - threshold) / max(1e-3, 1 - threshold), 0, 1)
    b8 = Image.fromarray(np.round(bright * 255).astype(np.uint8))
    acc = np.zeros_like(a)
    for r, w in zip(radii, (0.5, 0.3, 0.2)):
        acc += w * np.asarray(b8.filter(ImageFilter.GaussianBlur(max(1.0, r * im.size[1]))), float) / 255.0
    out = np.clip(a + intensity * acc, 0, 1)
    png_out = png_out or png_in
    Image.fromarray(np.round(out * 255).astype(np.uint8)).save(png_out)
    return str(png_out)


def _Sampler():
    return uerun.GPUSampler(0.5)


def gpu_chart(series_by_engine: dict, path, title="GPU while each engine renders") -> str | None:
    """One chart: GPU utilisation (%) and VRAM (GB) over time, one line per engine."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    fig, ax = plt.subplots(1, 2, figsize=(12, 3.4))
    for name, s in series_by_engine.items():           # uerun.GPUSampler.series(): (seconds, VRAM GB, GPU %)
        if not s:
            continue
        ax[0].plot([x[0] for x in s], [x[2] for x in s], label=name)
        ax[1].plot([x[0] for x in s], [x[1] for x in s], label=name)
    ax[0].set(title="GPU utilisation", xlabel="s", ylabel="%", ylim=(0, 105))
    ax[1].set(title="GPU memory", xlabel="s", ylabel="GB")
    for a in ax:
        a.grid(alpha=0.3)
        if a.get_legend_handles_labels()[0]:
            a.legend(loc="upper right", fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return str(path)


# ---------------------------------------------------------------------- Blender Cycles
def cycles(blender_cmd, glb, view_map, out_dir, res=(1920, 1080), samples=256, exposure=0.0, look=None, device="GPU",
           backend=None, lights=None, view_transform="AgX", post_bloom=True, timeout=7200, log=print) -> dict:
    """Cycles stills of `glb` from every view; blender_cmd as x3dlab.blender.Blender.cmd ([python] for the bpy wheel, or
    [blender, -b, --factory-startup, --python])."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {"glb": str(glb), "out_dir": str(out_dir), "res": list(res), "samples": int(samples), "exposure": float(exposure),
           "look": look, "device": device, "backend": backend, "views": view_map, "lights": lights or [], "view_transform": view_transform}
    jp = out_dir / "cycles_job.json"
    jp.write_text(json.dumps(job))
    cmd = list(blender_cmd) + ([str(CYCLES_SCRIPT), str(jp)] if len(blender_cmd) == 1 else [str(CYCLES_SCRIPT), "--", str(jp)])
    t0 = time.time()
    lines = []
    with _Sampler() as smp:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        killer = threading.Timer(timeout, p.kill)
        killer.start()
        try:
            for line in p.stdout:
                lines.append(line.rstrip())
                if line.startswith("X3DLAB"):
                    log("  " + line.strip())
            p.wait()
        finally:
            killer.cancel()
    rep_path = out_dir / "cycles_report.json"
    rep = json.loads(rep_path.read_text()) if rep_path.exists() else {"errors": ["no report"], "views": {}}
    stills = {}
    for k, v in rep.get("views", {}).items():
        png = v.get("png")
        if png and Path(png).exists() and post_bloom:
            bloom(png, str(out_dir / ("cycles_%s_bloom.png" % k)))
            png = str(out_dir / ("cycles_%s_bloom.png" % k))
        stills[k] = png
    return {"engine": "Blender Cycles", "rc": p.returncode, "seconds": round(time.time() - t0, 1), "stills": stills, "report": rep,
            "gpu": smp.summary(), "gpu_series": smp.series(), "log_tail": lines[-25:]}


# ---------------------------------------------------------------------- Babylon.js (headless Chrome, WebGPU)
PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#000}canvas{display:block}</style>
<link rel="icon" href="data:,"><script src="babylon.js"></script><script src="babylonjs.loaders.min.js"></script></head><body>
<canvas id="c" width="%(w)d" height="%(h)d" style="width:%(w)dpx;height:%(h)dpx"></canvas><script>
window.gob = (async () => {
  const opts = %(opts)s;
  const canvas = document.getElementById('c');
  let engine, kind = 'WebGL2', adapter = '';
  if (opts.webgpu && navigator.gpu) {
    try {
      const a = await navigator.gpu.requestAdapter({powerPreference: 'high-performance'});
      if (a) {
        const info = a.info || {};
        adapter = [info.vendor, info.architecture, info.device, info.description].filter(Boolean).join(' · ');
        engine = new BABYLON.WebGPUEngine(canvas, {antialias: true, adaptToDeviceRatio: false});
        await engine.initAsync();
        kind = 'WebGPU';
      }
    } catch (e) { engine = null; adapter = 'WebGPU failed: ' + e; }
  }
  if (!engine) engine = new BABYLON.Engine(canvas, true, {preserveDrawingBuffer: true, stencil: true, antialias: true});
  const scene = new BABYLON.Scene(engine);
  scene.useRightHandedSystem = true;                 // glTF and X3D are right-handed, Y up: no root flip, X3D coordinates as is
  scene.clearColor = new BABYLON.Color4(0, 0, 0, 1);
  await BABYLON.SceneLoader.AppendAsync('', 'gob.glb', scene);
  for (const L of opts.lights) {
    const p = new BABYLON.PointLight(L.name, new BABYLON.Vector3(...L.location), scene);
    p.diffuse = new BABYLON.Color3(...L.color); p.specular = p.diffuse; p.intensity = L.intensity;
  }
  const cam = new BABYLON.UniversalCamera('cam', new BABYLON.Vector3(0, 0, 10), scene);
  cam.minZ = 0.02; cam.maxZ = 5000;
  scene.activeCamera = cam;
  const gl = new BABYLON.GlowLayer('glow', scene, {mainTextureSamples: 4, blurKernelSize: opts.glow_kernel});
  gl.intensity = opts.glow;
  for (const m of scene.meshes)                       // only the light-like parts glow: ribbons, points, the eye
    if (/GOB_(ribbons|points|eye)/.test(m.name) || (m.parent && /GOB_(ribbons|points|eye)/.test(m.parent.name))) gl.addIncludedOnlyMesh(m);
  const pipe = new BABYLON.DefaultRenderingPipeline('pipe', true, scene, [cam]);
  pipe.samples = opts.msaa;
  pipe.fxaaEnabled = true;
  pipe.bloomEnabled = opts.bloom > 0; pipe.bloomThreshold = opts.bloom_threshold; pipe.bloomWeight = opts.bloom;
  pipe.bloomKernel = opts.bloom_kernel; pipe.bloomScale = 0.5;
  pipe.imageProcessingEnabled = true;
  const ip = pipe.imageProcessing;
  ip.toneMappingEnabled = opts.tonemap; ip.toneMappingType = BABYLON.ImageProcessingConfiguration.TONEMAPPING_ACES;
  ip.exposure = opts.exposure; ip.contrast = opts.contrast;
  ip.vignetteEnabled = opts.vignette > 0; ip.vignetteWeight = opts.vignette;
  if (opts.grain > 0) { pipe.grainEnabled = true; pipe.grain.intensity = opts.grain; }
  if (opts.chromatic > 0) { pipe.chromaticAberrationEnabled = true; pipe.chromaticAberration.aberrationAmount = opts.chromatic; }
  window.setExposure = (e) => { ip.exposure = e; return ip.exposure; };
  window.view = async (v, frames) => {
    const [x, y, z, ang] = v.orientation;
    const n = Math.hypot(x, y, z) || 1;
    const q = BABYLON.Quaternion.RotationAxis(new BABYLON.Vector3(x / n, y / n, z / n), ang);
    const m = new BABYLON.Matrix(); q.toRotationMatrix(m);
    const fwd = BABYLON.Vector3.TransformNormal(new BABYLON.Vector3(0, 0, -1), m);
    const up = BABYLON.Vector3.TransformNormal(new BABYLON.Vector3(0, 1, 0), m);
    cam.position = new BABYLON.Vector3(...v.position);
    cam.upVector = up;
    cam.setTarget(cam.position.add(fwd));
    cam.fovMode = canvas.width >= canvas.height ? BABYLON.Camera.FOVMODE_VERTICAL_FIXED : BABYLON.Camera.FOVMODE_HORIZONTAL_FIXED;
    cam.fov = v.fov;
    const t0 = performance.now();
    for (let i = 0; i < frames; i++) { scene.render(); await new Promise(r => requestAnimationFrame(r)); }
    if (engine.flushFramebuffer) engine.flushFramebuffer();
    return {ms_per_frame: (performance.now() - t0) / frames};
  };
  return {kind, adapter, meshes: scene.meshes.length, materials: scene.materials.length,
          renderer: engine.getGlInfo ? (engine.getGlInfo().renderer || '') : ''};
})();
</script></body></html>"""

# Tried against the reference (480x270, two views): glow 0.35 / kernel 24 / ACES / contrast 1.25 scored SSIM 0.70-0.73 and
# lit F 0.94-0.98; a stronger glow (0.9, kernel 48) washed the gold out to white (SSIM 0.4).
BABYLON_LOOK = {"webgpu": True, "msaa": 8, "glow": 0.35, "glow_kernel": 24, "bloom": 0.2, "bloom_threshold": 0.85, "bloom_kernel": 64,
                "tonemap": True, "exposure": 1.0, "contrast": 1.25, "vignette": 1.0, "grain": 0.0, "chromatic": 0.0}


def babylon_vendor(dest) -> Path:
    """babylon.js and babylonjs.loaders.min.js at the pinned version, from npm (once)."""
    dest = Path(dest)
    need = {"babylon.js": "babylonjs", "babylonjs.loaders.min.js": "babylonjs-loaders"}
    if all((dest / f).exists() for f in need):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm")
    if not npm:                                   # Colab: the lab's own Node (x3dlab.hog installs it) brings npm along
        try:
            from x3dlab.hog import _node_bin
            cand = Path(_node_bin()).with_name("npm")
            npm = str(cand) if cand.exists() else None
        except Exception as exc:
            raise RuntimeError("npm is needed to fetch Babylon.js, and the lab's Node could not be installed: %s" % exc)
    if not npm:
        raise RuntimeError("npm is needed to fetch Babylon.js")
    for f, pkg in need.items():
        if (dest / f).exists():
            continue
        r = subprocess.run([npm, "pack", "-q", "%s@%s" % (pkg, BABYLON_VERSION)], cwd=dest, capture_output=True, text=True, timeout=600)
        tgz = next(dest.glob("%s-%s.tgz" % (pkg, BABYLON_VERSION)), None)
        if r.returncode or tgz is None:
            raise RuntimeError("npm pack %s failed: %s" % (pkg, (r.stderr or r.stdout)[-300:]))
        with tarfile.open(tgz) as t:
            member = t.getmember("package/%s" % f)
            member.name = f
            t.extract(member, dest, filter="data")
        tgz.unlink()
    return dest


class _Quiet(http.server.SimpleHTTPRequestHandler):
    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map, **{".glb": "model/gltf-binary", ".js": "text/javascript"})

    def log_message(self, *a):
        pass


def _serve(root):
    handler = functools.partial(_Quiet, directory=str(root))
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def babylon(glb, view_map, out_dir, res=(1920, 1080), look=None, lights=None, frames=48, stress_frames=240, gpu=True,
            vendor_dir=None, timeout=600, log=print, references=None) -> dict:
    """Babylon.js stills of `glb` (WebGPU when Chrome gives it an adapter, else WebGL2), then `stress_frames` more frames
    of the first view to show what the GPU does under load. Returns stills, timings, the renderer Chrome really used."""
    from x3dlab import browser as xb
    xb.ensure_playwright()
    box = {}

    def work():
        try:
            box["r"] = _babylon(glb, view_map, out_dir, res, look, lights, frames, stress_frames, gpu, vendor_dir, timeout, log, references)
        except BaseException as exc:                    # handed back to the caller's thread
            box["e"] = exc

    # Playwright's sync API refuses to run on a thread with an asyncio loop (a notebook kernel's): run on a thread of its own.
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout + 600)
    if "e" in box:
        raise box["e"]
    if "r" not in box:
        raise RuntimeError("the Babylon render did not finish within %d s" % (timeout + 600))
    return box["r"]


def _babylon(glb, view_map, out_dir, res, look, lights, frames, stress_frames, gpu, vendor_dir, timeout, log, references):
    from playwright.sync_api import sync_playwright
    from x3dlab import browser as xb
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    site = out_dir / "babylon_site"
    site.mkdir(exist_ok=True)
    vend = babylon_vendor(vendor_dir or out_dir.parent / "vendor" / ("babylon-%s" % BABYLON_VERSION))
    for f in ("babylon.js", "babylonjs.loaders.min.js"):
        if not (site / f).exists():
            shutil.copy(vend / f, site / f)
    shutil.copy(glb, site / "gob.glb")
    opts = dict(BABYLON_LOOK, **(look or {}), lights=lights or [])
    w, h = res
    (site / "index.html").write_text(PAGE % {"w": w, "h": h, "opts": json.dumps(opts)})
    srv, url = _serve(site)
    console, stills, per, info, stress = [], {}, {}, {}, None
    t0 = time.time()
    try:
        with _Sampler() as smp, sync_playwright() as pw:
            exe = xb.chromium_path()
            br = pw.chromium.launch(headless=True, args=xb.GPU_ARGS if gpu else xb.CHROME_ARGS, **({"executable_path": exe} if exe else {}))
            page = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.on("console", lambda m: console.append("%s: %s" % (m.type, m.text)))
            page.on("pageerror", lambda e: console.append("pageerror: %s" % e))
            page.goto(url + "/index.html", timeout=timeout * 1000)
            info = page.evaluate("async () => await window.gob")
            log("  Babylon %s on %s%s · %d meshes" % (BABYLON_VERSION, info["kind"], (" · " + info["adapter"]) if info.get("adapter") else
                                                    (" · " + info.get("renderer", "")), info["meshes"]))
            exposure = opts["exposure"]
            if references:                                  # one measured exposure for all views: X_ITE's brightness
                fits = []
                for name, v in view_map.items():
                    if name in references:
                        page.evaluate("async ([v, f]) => await window.view(v, f)", [v, max(4, frames // 4)])
                        tmp = out_dir / "_calib.png"
                        page.locator("#c").screenshot(path=str(tmp), type="png")
                        fits.append(gobbler.exposure_fit(gobbler.resize_like(tmp, references[name].shape), references[name]))
                if fits:
                    exposure = float(opts["exposure"] * 2 ** (sum(fits) / len(fits)))
                    page.evaluate("(e) => window.setExposure(e)", exposure)
                    log("  babylon exposure fitted to the reference: x%.2f" % (exposure / opts["exposure"]))
            for name, v in view_map.items():
                tv = time.time()
                r = page.evaluate("async ([v, f]) => await window.view(v, f)", [v, frames])
                png = out_dir / ("babylon_%s.png" % name)
                page.locator("#c").screenshot(path=str(png), type="png")
                stills[name] = str(png)
                per[name] = {"ms_per_frame": round(r["ms_per_frame"], 2), "seconds": round(time.time() - tv, 2)}
                log("  babylon %s: %.1f ms/frame" % (name, r["ms_per_frame"]))
            if stress_frames and view_map:
                first = next(iter(view_map.values()))
                r = page.evaluate("async ([v, f]) => await window.view(v, f)", [first, stress_frames])
                stress = {"frames": stress_frames, "ms_per_frame": round(r["ms_per_frame"], 2), "fps": round(1000 / max(r["ms_per_frame"], 1e-3), 1)}
                log("  babylon stress: %d frames at %dx%d, %.1f fps" % (stress_frames, w, h, stress["fps"]))
            br.close()
    finally:
        srv.shutdown()
    return {"engine": "Babylon.js %s" % BABYLON_VERSION, "info": info, "stills": stills, "views": per, "stress": stress,
            "exposure": locals().get("exposure"), "seconds": round(time.time() - t0, 1), "gpu": smp.summary(),
            "gpu_series": smp.series(), "console": [c for c in console if "error" in c.lower()][:10]}
