# Vulkan burn-in for the Colab G4, from Python: wgpu-py forced onto its Vulkan backend, WGSL compute shaders.
# Three acts, about 5 min in all (tune with the knobs below):
#   1. the adapter card: what Vulkan device and driver wgpu found;
#   2. FMA furnace: a pure FP32 fused-multiply-add compute shader, timed, reported in TFLOPS;
#   3. Mandelbulb at 4K: a ray-marched fractal per frame (orbit camera, orbit-trap colour, soft shadow, AO, glow), timed in fps,
#      with keyframes saved as PNG, GIF and MP4.
# An nvidia-smi sampler runs alongside and ends with a chart of GPU utilisation, power, SM clock and memory.
# Run as one Colab cell:  exec(open("/content/drive/MyDrive/x3dlab/x3d_vulkan_burn.py").read(), {"__name__": "__main__"})
import os, sys, subprocess, threading, time, math, struct, base64, json
os.environ.setdefault("WGPU_BACKEND_TYPE", "Vulkan")                  # wgpu-native: Vulkan only, no GL fallback
QUICK = os.environ.get("BURN_QUICK") == "1"                            # tiny sizes for a CPU (lavapipe) smoke test
FURNACE_S = 4 if QUICK else 60                                         # seconds of FMA furnace
BULB_W, BULB_H = (320, 180) if QUICK else (3840, 2160)
BULB_FRAMES = 6 if QUICK else 240                                      # rendered frames (every 10th is kept)
BULB_SS = 1 if QUICK else 2                                            # supersampling per axis (2 = 4 rays per pixel)
OUT = os.environ.get("BURN_OUT") or ("/content/drive/MyDrive/x3dlab/unreal/joy/vulkan_" + time.strftime("%Y%m%d-%H%M%S"))
os.makedirs(OUT, exist_ok=True)
try:
    import wgpu
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "-q", "install", "wgpu"], check=True)
    import wgpu
import numpy as np
from PIL import Image
try:
    from IPython.display import display, HTML
except Exception:
    display = HTML = None


# ---------------------------------------------------------------- GPU sampler (nvidia-smi, every 0.5 s)
class Sampler(threading.Thread):
    Q = "utilization.gpu,power.draw,clocks.sm,memory.used,temperature.gpu"

    def __init__(self):
        super().__init__(daemon=True)
        self.rows, self.stop, self.marks = [], threading.Event(), []
        self.ok = subprocess.run("nvidia-smi -L", shell=True, capture_output=True).returncode == 0

    def run(self):
        t0 = time.time()
        while self.ok and not self.stop.is_set():
            r = subprocess.run("nvidia-smi --query-gpu=%s --format=csv,noheader,nounits" % self.Q, shell=True, capture_output=True, text=True)
            try:
                vals = [float(x) for x in r.stdout.strip().splitlines()[0].split(",")]
                self.rows.append([time.time() - t0] + vals)
            except Exception:
                pass
            time.sleep(0.5)


SAMPLER = Sampler(); T_START = time.time(); SAMPLER.start()
def mark(label):
    SAMPLER.marks.append((time.time() - T_START, label))


# ---------------------------------------------------------------- act 1: the adapter
req = getattr(wgpu.gpu, "request_adapter_sync", None)
adapter = req(power_preference="high-performance") if req else wgpu.gpu.request_adapter(power_preference="high-performance")
info = dict(getattr(adapter, "info", None) or getattr(adapter, "properties", {}) or {})
print("🔥 Vulkan burn · wgpu-py %s" % wgpu.__version__)
for k in ("backend_type", "adapter_type", "device", "vendor", "architecture", "description"):
    if info.get(k):
        print("   %-13s %s" % (k, info[k]))
if str(info.get("backend_type", "")).lower() != "vulkan":
    print("⚠️  backend is %s, not Vulkan" % info.get("backend_type"))
lim = adapter.limits
want = {k: lim[k] for k in ("max-storage-buffer-binding-size", "max-buffer-size") if k in lim}
rd = getattr(adapter, "request_device_sync", None)
device = rd(required_limits=want) if rd else adapter.request_device(required_limits=want)
mark("adapter")

def sync():
    """Wait for the queue to drain (a tiny read-back)."""
    device.queue.read_buffer(SYNC_BUF)
SYNC_BUF = device.create_buffer(size=16, usage=wgpu.BufferUsage.COPY_SRC | wgpu.BufferUsage.STORAGE)


# ---------------------------------------------------------------- act 2: FMA furnace
ITERS = 64 if QUICK else 2048
WG_X, WG_Y = (64, 1) if QUICK else (4096, 16)                           # 256 threads per workgroup
FURNACE = """
@group(0) @binding(0) var<storage, read_write> sink: array<vec4<f32>>;
@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) gid: vec3<u32>, @builtin(num_workgroups) nwg: vec3<u32>) {
  let id = gid.x + gid.y * nwg.x * 256u;
  let s = f32(id & 1023u) * 1e-6;
  let b = vec4<f32>(0.99991, 0.99992, 0.99993, 0.99994);
  let c = vec4<f32>(1e-5, 2e-5, 3e-5, 4e-5);
  var x = vec4<f32>(s, 0.1, 0.2, 0.3); var y = x.yzwx; var z = x.zwxy; var w = x.wxyz;
  var p = x * 0.5; var q = y * 0.5; var r = z * 0.5; var t = w * 0.5;
  for (var i = 0u; i < %du; i = i + 1u) {
    x = fma(x, b, c); y = fma(y, b, c); z = fma(z, b, c); w = fma(w, b, c);
    p = fma(p, b, c); q = fma(q, b, c); r = fma(r, b, c); t = fma(t, b, c);
    x = fma(x, b, c); y = fma(y, b, c); z = fma(z, b, c); w = fma(w, b, c);
    p = fma(p, b, c); q = fma(q, b, c); r = fma(r, b, c); t = fma(t, b, c);
  }
  sink[id & 1048575u] = x + y + z + w + p + q + r + t;
}
""" % ITERS
sink = device.create_buffer(size=1048576 * 16, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC)
fp = device.create_compute_pipeline(layout="auto", compute={"module": device.create_shader_module(code=FURNACE), "entry_point": "main"})
fbg = device.create_bind_group(layout=fp.get_bind_group_layout(0), entries=[{"binding": 0, "resource": {"buffer": sink, "offset": 0, "size": sink.size}}])
flops_per_dispatch = WG_X * WG_Y * 256 * ITERS * 16 * 4 * 2            # threads x iters x 16 vec4 FMA x 4 lanes x 2 flops

def furnace(n):
    enc = device.create_command_encoder()
    for _ in range(n):
        cp = enc.begin_compute_pass(); cp.set_pipeline(fp); cp.set_bind_group(0, fbg); cp.dispatch_workgroups(WG_X, WG_Y, 1); cp.end()
    device.queue.submit([enc.finish()])

furnace(2); sync()                                                      # warm-up (pipeline compile, clocks up)
print("\n🔥 act 2 · FMA furnace: %.2f TFLOP per dispatch, %d s" % (flops_per_dispatch / 1e12, FURNACE_S))
mark("furnace")
t0, done, best = time.time(), 0, 0.0
while time.time() - t0 < FURNACE_S:
    tb = time.time(); furnace(8); sync(); dt = time.time() - tb
    done += 8; best = max(best, 8 * flops_per_dispatch / dt / 1e12)
    print("   %5.1f s · %4d dispatches · %6.1f TFLOPS now · best %6.1f" % (time.time() - t0, done, 8 * flops_per_dispatch / dt / 1e12, best), end="\r")
furnace_tflops = done * flops_per_dispatch / (time.time() - t0) / 1e12
print("\n   furnace average %.1f TFLOPS FP32 (best batch %.1f) over %d dispatches" % (furnace_tflops, best, done))


# ---------------------------------------------------------------- act 3: Mandelbulb at 4K
BULB = """
struct U { res: vec2<u32>, t: f32, ss: f32 };
@group(0) @binding(0) var<uniform> u: U;
@group(0) @binding(1) var<storage, read_write> img: array<u32>;

fn de(p0: vec3<f32>, power: f32) -> vec2<f32> {
  var z = p0; var dr = 1.0; var r = 0.0; var trap = 1e9;
  for (var i = 0; i < 10; i = i + 1) {
    r = length(z);
    if (r > 2.0) { break; }
    let theta = acos(clamp(z.y / max(r, 1e-6), -1.0, 1.0)) * power;
    let phi = atan2(z.z, z.x) * power;
    dr = pow(r, power - 1.0) * power * dr + 1.0;
    let zr = pow(r, power);
    z = zr * vec3<f32>(sin(theta) * cos(phi), cos(theta), sin(theta) * sin(phi)) + p0;
    trap = min(trap, dot(z, z));
  }
  return vec2<f32>(0.5 * log(max(r, 1e-6)) * r / dr, trap);
}

fn palette(t: f32) -> vec3<f32> {
  return vec3<f32>(0.55, 0.4, 0.35) + vec3<f32>(0.45, 0.4, 0.4) * cos(6.28318 * (vec3<f32>(1.0, 0.8, 0.6) * t + vec3<f32>(0.0, 0.15, 0.35)));
}

fn shade(ro: vec3<f32>, rd: vec3<f32>, power: f32) -> vec3<f32> {
  var t = 0.0; var glow = 0.0; var hit = false; var trap = 0.0; var steps = 0.0;
  for (var i = 0; i < 180; i = i + 1) {
    let h = de(ro + rd * t, power);
    glow = glow + exp(-80.0 * h.x) * 0.004;
    if (h.x < 0.00035 * t) { hit = true; trap = h.y; steps = f32(i); break; }
    t = t + h.x * 0.9;
    if (t > 8.0) { break; }
  }
  let sky = mix(vec3<f32>(0.003, 0.002, 0.008), vec3<f32>(0.02, 0.012, 0.04), clamp(rd.y * 0.5 + 0.5, 0.0, 1.0));
  if (!hit) { return sky + glow * vec3<f32>(1.0, 0.55, 0.2); }
  let p = ro + rd * t;
  let e = vec2<f32>(0.0005 * t, 0.0);
  let n = normalize(vec3<f32>(de(p + e.xyy, power).x - de(p - e.xyy, power).x,
                              de(p + e.yxy, power).x - de(p - e.yxy, power).x,
                              de(p + e.yyx, power).x - de(p - e.yyx, power).x));
  let L = normalize(vec3<f32>(0.6, 0.8, -0.4));
  var sh = 1.0; var st = 0.002;                                          // soft shadow toward the key light
  for (var j = 0; j < 48; j = j + 1) {
    let h = de(p + L * st, power).x;
    sh = min(sh, 12.0 * h / st);
    st = st + clamp(h, 0.002, 0.08);
    if (sh < 0.001 || st > 2.5) { break; }
  }
  sh = clamp(sh, 0.0, 1.0);
  let ao = clamp(1.0 - steps / 120.0, 0.15, 1.0);
  let base = palette(sqrt(trap) * 0.8 + u.t * 0.03) * vec3<f32>(1.15, 0.85, 0.55);
  let diff = max(dot(n, L), 0.0) * sh;
  let rim = pow(1.0 - max(dot(n, -rd), 0.0), 3.0);
  let spec = pow(max(dot(reflect(-L, n), -rd), 0.0), 40.0) * sh;
  return base * (0.06 + 1.25 * diff * vec3<f32>(1.0, 0.86, 0.7)) * ao + rim * vec3<f32>(0.15, 0.3, 0.8) * ao + spec * 0.6 + glow * 0.4;
}

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  if (gid.x >= u.res.x || gid.y >= u.res.y) { return; }
  let res = vec2<f32>(u.res);
  let power = 8.0 + 2.0 * sin(u.t * 0.35);
  let a = u.t * 0.25;
  let ro = vec3<f32>(3.1 * sin(a), 0.9 * sin(u.t * 0.17) + 0.35, 3.1 * cos(a));
  let fw = normalize(-ro); let rt = normalize(cross(vec3<f32>(0.0, 1.0, 0.0), fw)); let up = cross(fw, rt);
  var col = vec3<f32>(0.0);
  let n = i32(u.ss);
  for (var sy = 0; sy < n; sy = sy + 1) {
    for (var sx = 0; sx < n; sx = sx + 1) {
      let off = (vec2<f32>(f32(sx), f32(sy)) + 0.5) / u.ss;
      let uv = (vec2<f32>(f32(gid.x), res.y - 1.0 - f32(gid.y)) + off - 0.5 * res) / res.y;
      col = col + shade(ro, normalize(fw * 1.6 + rt * uv.x + up * uv.y), power);
    }
  }
  col = col / (u.ss * u.ss);
  col = col / (col + vec3<f32>(0.85)) * 1.6;                             // soft tone curve
  col = pow(clamp(col, vec3<f32>(0.0), vec3<f32>(1.0)), vec3<f32>(1.0 / 2.2));
  let q = vec3<u32>(col * 255.0 + 0.5);
  img[gid.x + gid.y * u.res.x] = q.x | (q.y << 8u) | (q.z << 16u) | (255u << 24u);
}
"""
ubuf = device.create_buffer(size=16, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
ibuf = device.create_buffer(size=BULB_W * BULB_H * 4, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC)
bp = device.create_compute_pipeline(layout="auto", compute={"module": device.create_shader_module(code=BULB), "entry_point": "main"})
bbg = device.create_bind_group(layout=bp.get_bind_group_layout(0), entries=[
    {"binding": 0, "resource": {"buffer": ubuf, "offset": 0, "size": 16}},
    {"binding": 1, "resource": {"buffer": ibuf, "offset": 0, "size": ibuf.size}}])

def bulb(t):
    device.queue.write_buffer(ubuf, 0, struct.pack("<IIff", BULB_W, BULB_H, t, float(BULB_SS)))
    enc = device.create_command_encoder()
    cp = enc.begin_compute_pass(); cp.set_pipeline(bp); cp.set_bind_group(0, bbg)
    cp.dispatch_workgroups((BULB_W + 7) // 8, (BULB_H + 7) // 8, 1); cp.end()
    device.queue.submit([enc.finish()])

print("\n🌀 act 3 · Mandelbulb %dx%d, %d rays per pixel, %d frames" % (BULB_W, BULB_H, BULB_SS ** 2, BULB_FRAMES))
mark("mandelbulb")
keep, times = [], []
for f in range(BULB_FRAMES):
    t = f * 0.12
    tb = time.time(); bulb(t)
    if f % 10 == 0 or QUICK:
        raw = np.frombuffer(device.queue.read_buffer(ibuf), dtype=np.uint8).reshape(BULB_H, BULB_W, 4)[..., :3]
        p = os.path.join(OUT, "bulb_%03d.png" % f); Image.fromarray(raw.copy()).save(p); keep.append(p)
    else:
        sync()
    times.append(time.time() - tb)
    print("   frame %3d/%d · %.3f s · %.1f fps · %.2f Grays/s" % (f + 1, BULB_FRAMES, times[-1], 1 / times[-1],
          BULB_W * BULB_H * BULB_SS ** 2 / times[-1] / 1e9), end="\r")
fps = len(times) / sum(times)
print("\n   Mandelbulb average %.2f fps at %dx%d x%d rays (%.2f Grays/s)" % (fps, BULB_W, BULB_H, BULB_SS ** 2, BULB_W * BULB_H * BULB_SS ** 2 * fps / 1e9))
mark("end")
SAMPLER.stop.set(); time.sleep(0.6)

# ---------------------------------------------------------------- the reel and the chart
gif = os.path.join(OUT, "vulkan_mandelbulb.gif")
ims = [Image.open(p).convert("RGB").resize((960, 540), Image.LANCZOS) for p in keep]
if ims:
    ims[0].save(gif, save_all=True, append_images=ims[1:], duration=160, loop=0)
    subprocess.run("ffmpeg -y -loglevel error -framerate 6 -pattern_type glob -i '%s/bulb_*.png' -vf scale=1920:-2 -c:v libx264 -pix_fmt yuv420p -crf 16 %s"
                   % (OUT, os.path.join(OUT, "vulkan_mandelbulb.mp4")), shell=True)
chart = None
if SAMPLER.rows:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a = np.array(SAMPLER.rows)
    fig, ax = plt.subplots(4, 1, figsize=(12, 8), sharex=True, facecolor="#0b0b10")
    for axi, col, lab, c in zip(ax, (1, 2, 3, 4), ("GPU util %", "power W", "SM clock MHz", "memory MiB"), ("#ffb347", "#ff5e7e", "#7ec8ff", "#9be38f")):
        axi.plot(a[:, 0], a[:, col], color=c, lw=1.6); axi.set_ylabel(lab, color="#ddd"); axi.set_facecolor("#111118")
        axi.tick_params(colors="#aaa")
        for tm, lb in SAMPLER.marks:
            axi.axvline(tm, color="#555", lw=0.8)
    for tm, lb in SAMPLER.marks:
        ax[0].text(tm, ax[0].get_ylim()[1] * 0.92, " " + lb, color="#ccc", fontsize=9)
    ax[-1].set_xlabel("seconds", color="#ddd")
    fig.suptitle("Vulkan burn · %s · %s" % (info.get("device", "?"), info.get("backend_type", "?")), color="#ffd28c")
    chart = os.path.join(OUT, "gpu_chart.png"); fig.savefig(chart, dpi=110, facecolor=fig.get_facecolor()); plt.close(fig)
    print("   GPU peak util %.0f%% · peak power %.0f W · peak SM clock %.0f MHz · peak memory %.1f GB"
          % (a[:, 1].max(), a[:, 2].max(), a[:, 3].max(), a[:, 4].max() / 1024))
card = {"wgpu": wgpu.__version__, "adapter": {k: str(v) for k, v in info.items()}, "furnace_tflops_fp32": round(furnace_tflops, 2),
        "furnace_best_tflops": round(best, 2), "mandelbulb": {"res": [BULB_W, BULB_H], "rays_per_pixel": BULB_SS ** 2, "fps": round(fps, 3)},
        "gpu_samples": len(SAMPLER.rows)}
open(os.path.join(OUT, "burn_card.json"), "w").write(json.dumps(card, indent=1))
if display:
    if chart:
        display(Image.open(chart))
    if ims:
        display(HTML('<img src="data:image/gif;base64,%s" width="960">' % base64.b64encode(open(gif, "rb").read()).decode()))
        display(Image.open(keep[-1]).resize((1280, 720), Image.LANCZOS))
print("✅ Vulkan burn done · %s" % OUT)
print(json.dumps(card))
