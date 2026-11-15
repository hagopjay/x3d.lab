# Vulkan encore for the Colab G4: two self-contained cells, wgpu-py on its Vulkan backend, WGSL compute shaders.
# Load with:
#   VEC = open("/content/drive/MyDrive/x3dlab/x3d_vulkan_encore.py").read().split("\n# %% ")
#   def encore(step): exec("#" + next(c for c in VEC[1:] if c.startswith(step)), globals())
# then encore("V1") and encore("V2"). Each makes its own Vulkan device; run them while no Unreal cell is running.
# BURN_QUICK=1 in the environment shrinks everything for a CPU (lavapipe) smoke test.

# %% V1 · Two galaxies collide: an N-body simulation on the GPU, about 4-7 min
import os, sys, subprocess, time, math, struct, base64, json
os.environ.setdefault("WGPU_BACKEND_TYPE", "Vulkan")
try:
    import wgpu
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "-q", "install", "wgpu"], check=True); import wgpu
import numpy as np
from PIL import Image
try:
    from IPython.display import display, HTML
except Exception:
    display = HTML = None
QUICK = os.environ.get("BURN_QUICK") == "1"
GX_N = 2048 if QUICK else 262144                  # stars (two galaxies, half each); every star pulls on every other: N^2 per step
GX_STEPS = 12 if QUICK else 2400                  # time steps (closest pass near step 1300, then the tidal tails)
GX_PER_FRAME = 4 if QUICK else 6                  # steps between frames (2400 / 6 = 400 frames, 16 s at 25 fps)
GX_W, GX_H = (320, 180) if QUICK else (1920, 1080)
GX_DT, GX_EPS = 0.006, 0.05                       # step and softening length (simulation units, G = 1)
GX_OUT = os.environ.get("BURN_OUT") or ("/content/drive/MyDrive/x3dlab/unreal/joy/galaxies_" + time.strftime("%Y%m%d-%H%M%S"))
os.makedirs(GX_OUT, exist_ok=True)
_rq = getattr(wgpu.gpu, "request_adapter_sync", None)
gx_adapter = _rq(power_preference="high-performance") if _rq else wgpu.gpu.request_adapter(power_preference="high-performance")
_lim = gx_adapter.limits
_rd = getattr(gx_adapter, "request_device_sync", None)
_want = {k: _lim[k] for k in ("max-storage-buffer-binding-size", "max-buffer-size") if k in _lim}
gx_dev = _rd(required_limits=_want) if _rd else gx_adapter.request_device(required_limits=_want)
print("🌌 galaxies · %s · %s · %d stars · %d steps" % (dict(gx_adapter.info).get("device", "?"), dict(gx_adapter.info).get("backend_type", "?"), GX_N, GX_STEPS))

# initial conditions: two discs with a central black hole each, the second tilted 60 degrees, on a close fly-by
rng = np.random.default_rng(7)
def _disc(n, centre, vel, tilt, spin, m_bh=1.0, m_disc=0.5, r_max=1.6):
    r = r_max * np.sqrt(rng.uniform(0.004, 1.0, n)); th = rng.uniform(0, 2 * np.pi, n)
    p = np.stack([r * np.cos(th), rng.normal(0, 0.02, n) * (1 + r), r * np.sin(th)], 1)
    vc = np.sqrt((m_bh + m_disc * (r / r_max) ** 2) / np.sqrt(r ** 2 + GX_EPS ** 2))
    v = np.stack([-np.sin(th) * vc, np.zeros(n), np.cos(th) * vc], 1) * spin
    c, s = np.cos(tilt), np.sin(tilt); R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    p, v = p @ R.T + centre, v @ R.T + vel
    m = np.full(n, m_disc / n); p[0], v[0], m[0] = centre, vel, m_bh   # star 0 of each galaxy is its black hole
    return p, v, m
h = GX_N // 2
pa, va, ma = _disc(h, np.array([-3.2, 0.0, -1.0]), np.array([0.32, 0.0, -0.08]), 0.0, 1.0)
pb, vb, mb = _disc(h, np.array([3.2, 0.0, 1.0]), np.array([-0.32, 0.0, 0.08]), math.radians(60), -1.0)
P0 = np.concatenate([np.concatenate([pa, pb]), np.concatenate([ma, mb])[:, None]], 1).astype(np.float32)
V0 = np.concatenate([np.concatenate([va, vb]), np.zeros((GX_N, 1))], 1).astype(np.float32)
V0[h:, 3] = 1.0                                                          # w of velocity = which galaxy (colour)

NBODY = """
struct S { n: u32, dt: f32, eps2: f32, pad: f32 };
@group(0) @binding(0) var<uniform> s: S;
@group(0) @binding(1) var<storage, read> pin: array<vec4<f32>>;
@group(0) @binding(2) var<storage, read_write> pout: array<vec4<f32>>;
@group(0) @binding(3) var<storage, read_write> vel: array<vec4<f32>>;
var<workgroup> tile: array<vec4<f32>, 256>;
@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) gid: vec3<u32>, @builtin(local_invocation_id) lid: vec3<u32>) {
  let i = gid.x;
  var me = vec4<f32>(0.0);
  if (i < s.n) { me = pin[i]; }
  var acc = vec3<f32>(0.0);
  let tiles = (s.n + 255u) / 256u;
  for (var t = 0u; t < tiles; t = t + 1u) {
    let j = t * 256u + lid.x;
    if (j < s.n) { tile[lid.x] = pin[j]; } else { tile[lid.x] = vec4<f32>(0.0); }
    workgroupBarrier();
    for (var k = 0u; k < 256u; k = k + 1u) {
      let o = tile[k];
      let d = o.xyz - me.xyz;
      let r2 = dot(d, d) + s.eps2;
      let inv = inverseSqrt(r2);
      acc = acc + d * (o.w * inv * inv * inv);
    }
    workgroupBarrier();
  }
  if (i < s.n) {
    var v = vel[i];
    v = vec4<f32>(v.xyz + acc * s.dt, v.w);                              // kick
    vel[i] = v;
    pout[i] = vec4<f32>(me.xyz + v.xyz * s.dt, me.w);                    // drift
  }
}
"""
SPLAT = """
struct C { res: vec2<u32>, n: u32, ang: f32, dist: f32, h: f32, pad: vec2<f32> };
@group(0) @binding(0) var<uniform> c: C;
@group(0) @binding(1) var<storage, read> pos: array<vec4<f32>>;
@group(0) @binding(2) var<storage, read> vel: array<vec4<f32>>;
@group(0) @binding(3) var<storage, read_write> acc: array<atomic<u32>>;
@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let i = gid.x;
  if (i >= c.n) { return; }
  let p = pos[i].xyz;
  let ro = vec3<f32>(c.dist * sin(c.ang), c.h, c.dist * cos(c.ang));
  let fw = normalize(-ro); let rt = normalize(cross(vec3<f32>(0.0, 1.0, 0.0), fw)); let up = cross(fw, rt);
  let q = p - ro; let z = dot(q, fw);
  if (z < 0.1) { return; }
  let f = 1.4 * f32(c.res.y) / z;
  let sx = i32(f32(c.res.x) * 0.5 + dot(q, rt) * f);
  let sy = i32(f32(c.res.y) * 0.5 - dot(q, up) * f);
  if (sx < 0 || sy < 0 || sx >= i32(c.res.x) || sy >= i32(c.res.y)) { return; }
  let v = vel[i];
  let spd = clamp(length(v.xyz) * 0.9, 0.0, 1.0);
  var col = mix(vec3<f32>(1.0, 0.62, 0.25), vec3<f32>(1.0, 0.92, 0.75), spd);     // galaxy A: ember to gold
  if (v.w > 0.5) { col = mix(vec3<f32>(0.3, 0.55, 1.0), vec3<f32>(0.8, 0.95, 1.0), spd); }   // galaxy B: blue to ice
  var w = 64.0;
  if (pos[i].w > 0.1) { w = 60000.0; col = vec3<f32>(1.0, 1.0, 1.0); }    // the black holes burn white
  let k = (u32(sy) * c.res.x + u32(sx)) * 3u;
  atomicAdd(&acc[k], u32(col.x * w)); atomicAdd(&acc[k + 1u], u32(col.y * w)); atomicAdd(&acc[k + 2u], u32(col.z * w));
}
"""
TONE = """
struct T { res: vec2<u32>, gain: f32, pad: f32 };
@group(0) @binding(0) var<uniform> t: T;
@group(0) @binding(1) var<storage, read> acc: array<u32>;
@group(0) @binding(2) var<storage, read_write> img: array<u32>;
fn at(x: i32, y: i32) -> vec3<f32> {
  let xx = clamp(x, 0, i32(t.res.x) - 1); let yy = clamp(y, 0, i32(t.res.y) - 1);
  let k = (u32(yy) * t.res.x + u32(xx)) * 3u;
  return vec3<f32>(f32(acc[k]), f32(acc[k + 1u]), f32(acc[k + 2u]));
}
@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  if (gid.x >= t.res.x || gid.y >= t.res.y) { return; }
  let x = i32(gid.x); let y = i32(gid.y);
  var glow = vec3<f32>(0.0);
  for (var dy = -3; dy <= 3; dy = dy + 1) {
    for (var dx = -3; dx <= 3; dx = dx + 1) {
      glow = glow + at(x + dx, y + dy) * exp(-0.35 * f32(dx * dx + dy * dy));
    }
  }
  let e = at(x, y) * 1.0 + glow * 0.18;
  var col = vec3<f32>(1.0) - exp(-e * t.gain);
  col = col + vec3<f32>(0.0008, 0.0006, 0.0025);                         // a little night sky
  col = pow(clamp(col, vec3<f32>(0.0), vec3<f32>(1.0)), vec3<f32>(1.0 / 2.2));
  let q = vec3<u32>(col * 255.0 + 0.5);
  img[gid.y * t.res.x + gid.x] = q.x | (q.y << 8u) | (q.z << 16u) | (255u << 24u);
}
"""
BU = wgpu.BufferUsage
pbuf = [gx_dev.create_buffer_with_data(data=P0.tobytes(), usage=BU.STORAGE | BU.COPY_SRC | BU.COPY_DST) for _ in range(2)]
vbuf = gx_dev.create_buffer_with_data(data=V0.tobytes(), usage=BU.STORAGE | BU.COPY_SRC)
sbuf = gx_dev.create_buffer_with_data(data=struct.pack("<Ifff", GX_N, GX_DT, GX_EPS ** 2, 0.0), usage=BU.UNIFORM)
cbuf = gx_dev.create_buffer(size=32, usage=BU.UNIFORM | BU.COPY_DST)
tbuf = gx_dev.create_buffer_with_data(data=struct.pack("<IIff", GX_W, GX_H, 0.0009 if not QUICK else 0.004, 0.0), usage=BU.UNIFORM)
abuf = gx_dev.create_buffer(size=GX_W * GX_H * 12, usage=BU.STORAGE | BU.COPY_DST)
ibuf = gx_dev.create_buffer(size=GX_W * GX_H * 4, usage=BU.STORAGE | BU.COPY_SRC)
def _pipe(code):
    return gx_dev.create_compute_pipeline(layout="auto", compute={"module": gx_dev.create_shader_module(code=code), "entry_point": "main"})
def _bg(pipe, bufs):
    return gx_dev.create_bind_group(layout=pipe.get_bind_group_layout(0), entries=[
        {"binding": i, "resource": {"buffer": b, "offset": 0, "size": b.size}} for i, b in enumerate(bufs)])
nb_p, sp_p, tn_p = _pipe(NBODY), _pipe(SPLAT), _pipe(TONE)
nb_bg = [_bg(nb_p, [sbuf, pbuf[0], pbuf[1], vbuf]), _bg(nb_p, [sbuf, pbuf[1], pbuf[0], vbuf])]
sp_bg = [_bg(sp_p, [cbuf, pbuf[k], vbuf, abuf]) for k in (0, 1)]
tn_bg = _bg(tn_p, [tbuf, abuf, ibuf])
WG = (GX_N + 255) // 256
cur, frames, sim_s, t_all = 0, [], 0.0, time.time()
n_frames = GX_STEPS // GX_PER_FRAME
for f in range(n_frames):
    tb = time.time()
    enc = gx_dev.create_command_encoder()
    for _ in range(GX_PER_FRAME):
        cp = enc.begin_compute_pass(); cp.set_pipeline(nb_p); cp.set_bind_group(0, nb_bg[cur]); cp.dispatch_workgroups(WG, 1, 1); cp.end()
        cur ^= 1
    gx_dev.queue.submit([enc.finish()])
    gx_dev.queue.read_buffer(pbuf[cur], 0, 16)                          # wait for the physics only
    sim_s += time.time() - tb
    u = f / max(1, n_frames - 1)
    gx_dev.queue.write_buffer(cbuf, 0, struct.pack("<IIIfffff", GX_W, GX_H, GX_N, 0.4 + 1.1 * u, 9.5 - 2.0 * math.sin(math.pi * u), 3.2 - 1.6 * u, 0.0, 0.0))
    enc = gx_dev.create_command_encoder()
    enc.clear_buffer(abuf)
    cp = enc.begin_compute_pass(); cp.set_pipeline(sp_p); cp.set_bind_group(0, sp_bg[cur]); cp.dispatch_workgroups(WG, 1, 1); cp.end()
    cp = enc.begin_compute_pass(); cp.set_pipeline(tn_p); cp.set_bind_group(0, tn_bg); cp.dispatch_workgroups((GX_W + 7) // 8, (GX_H + 7) // 8, 1); cp.end()
    gx_dev.queue.submit([enc.finish()])
    raw = np.frombuffer(gx_dev.queue.read_buffer(ibuf), np.uint8).reshape(GX_H, GX_W, 4)[..., :3]
    p = os.path.join(GX_OUT, "gx_%04d.png" % f); Image.fromarray(raw.copy()).save(p); frames.append(p)
    steps = (f + 1) * GX_PER_FRAME
    print("   frame %3d/%d · step %4d · physics %.1f s · %.1f G interactions/s" % (f + 1, n_frames, steps, sim_s, steps * float(GX_N) ** 2 / max(sim_s, 1e-9) / 1e9), end="\r")
inter = GX_STEPS * float(GX_N) ** 2
print("\n   %d stars x %d steps = %.2e pair interactions in %.1f s of physics: %.1f G/s, %.1f TFLOPS at the usual 20 flops each"
      % (GX_N, GX_STEPS, inter, sim_s, inter / sim_s / 1e9, inter * 20 / sim_s / 1e12))
mp4 = os.path.join(GX_OUT, "galaxies_collide.mp4")
subprocess.run("ffmpeg -y -loglevel error -framerate 25 -i %s/gx_%%04d.png -c:v libx264 -pix_fmt yuv420p -crf 14 %s" % (GX_OUT, mp4), shell=True)
gif = os.path.join(GX_OUT, "galaxies_collide.gif")
ims = [Image.open(p).convert("RGB").resize((768, 432), Image.LANCZOS) for p in frames[::max(1, len(frames) // 60)]]
ims[0].save(gif, save_all=True, append_images=ims[1:], duration=80, loop=0)
GX_CARD = {"stars": GX_N, "steps": GX_STEPS, "dt": GX_DT, "softening": GX_EPS, "physics_s": round(sim_s, 2),
           "interactions_per_s": round(inter / sim_s, 0), "tflops_at_20": round(inter * 20 / sim_s / 1e12, 2), "frames": len(frames),
           "wall_s": round(time.time() - t_all, 1), "device": dict(gx_adapter.info).get("device")}
open(os.path.join(GX_OUT, "galaxies_card.json"), "w").write(json.dumps(GX_CARD, indent=1))
if display:
    display(HTML('<img src="data:image/gif;base64,%s" width="768">' % base64.b64encode(open(gif, "rb").read()).decode()))
    for k in (len(frames) // 3, 2 * len(frames) // 3, len(frames) - 1):
        display(Image.open(frames[k]).resize((1280, 720), Image.LANCZOS) if not QUICK else Image.open(frames[k]))
print("✅ V1 · %s · %s" % (GX_OUT, json.dumps(GX_CARD)))

# %% V2 · The Mandelbulb poster: one 8K still at 64 rays per pixel, plus the honest 4K frame rate (about 2-4 min)
import os, sys, subprocess, time, math, struct, re, json
os.environ.setdefault("WGPU_BACKEND_TYPE", "Vulkan")
import wgpu
import numpy as np
from PIL import Image
try:
    from IPython.display import display
except Exception:
    display = None
QUICK = os.environ.get("BURN_QUICK") == "1"
BURN_SRC = os.environ.get("BURN_SRC") or "/content/drive/MyDrive/x3dlab/x3d_vulkan_burn.py"
MB_SHADER = re.search(r'BULB = """(.*?)"""', open(BURN_SRC).read(), re.S).group(1)     # the same fractal shader as the burn
MB_OUT = os.environ.get("BURN_OUT") or ("/content/drive/MyDrive/x3dlab/unreal/joy/mandelbulb_poster_" + time.strftime("%Y%m%d-%H%M%S"))
os.makedirs(MB_OUT, exist_ok=True)
_rq = getattr(wgpu.gpu, "request_adapter_sync", None)
mb_adapter = _rq(power_preference="high-performance") if _rq else wgpu.gpu.request_adapter(power_preference="high-performance")
_lim = mb_adapter.limits
_rd = getattr(mb_adapter, "request_device_sync", None)
_want = {k: _lim[k] for k in ("max-storage-buffer-binding-size", "max-buffer-size") if k in _lim}
mb_dev = _rd(required_limits=_want) if _rd else mb_adapter.request_device(required_limits=_want)
mb_pipe = mb_dev.create_compute_pipeline(layout="auto", compute={"module": mb_dev.create_shader_module(code=MB_SHADER), "entry_point": "main"})
BU = wgpu.BufferUsage
MB_BUFS = {}
def mb_render(w, h, ss, t, read=True):
    if (w, h) not in MB_BUFS:                                            # buffers made once per size, so the timer sees only the GPU
        ub = mb_dev.create_buffer(size=16, usage=BU.UNIFORM | BU.COPY_DST | BU.COPY_SRC)
        ib = mb_dev.create_buffer(size=w * h * 4, usage=BU.STORAGE | BU.COPY_SRC)
        MB_BUFS[(w, h)] = (ub, ib, mb_dev.create_bind_group(layout=mb_pipe.get_bind_group_layout(0), entries=[
            {"binding": 0, "resource": {"buffer": ub, "offset": 0, "size": 16}}, {"binding": 1, "resource": {"buffer": ib, "offset": 0, "size": ib.size}}]))
    ub, ib, bg = MB_BUFS[(w, h)]
    mb_dev.queue.write_buffer(ub, 0, struct.pack("<IIff", w, h, t, float(ss)))
    enc = mb_dev.create_command_encoder(); cp = enc.begin_compute_pass(); cp.set_pipeline(mb_pipe); cp.set_bind_group(0, bg)
    cp.dispatch_workgroups((w + 7) // 8, (h + 7) // 8, 1); cp.end(); mb_dev.queue.submit([enc.finish()])
    data = mb_dev.queue.read_buffer(ib if read else ub)                  # reading the uniform back still waits for the frame
    return np.frombuffer(data, np.uint8).reshape(h, w, 4)[..., :3].copy() if read else None
# 1) the honest frame rate: 4K, 4 rays per pixel, nothing saved inside the timer
FW, FH, FN = (320, 180, 6) if QUICK else (3840, 2160, 120)
mb_render(FW, FH, 2, 0.0, read=False)                                    # warm-up: pipeline compile, clocks up
t0 = time.time()
for f in range(FN):
    mb_render(FW, FH, 2, f * 0.12, read=False)
fps = FN / (time.time() - t0)
print("🌀 Mandelbulb %dx%d x4 rays: %.1f fps, %.2f G rays/s over %d frames (render only)" % (FW, FH, fps, FW * FH * 4 * fps / 1e9, FN))
# 2) the poster: 8K at 8x8 = 64 rays per pixel, one dispatch
PW, PH, PSS = (640, 360, 2) if QUICK else (7680, 4320, 8)
T_POSTER = 9.4                                                           # the moment of the orbit (power 8 + 2 sin(0.35 t))
t0 = time.time()
poster = mb_render(PW, PH, PSS, T_POSTER)
ps = time.time() - t0
pp = os.path.join(MB_OUT, "mandelbulb_8k_64spp.png"); Image.fromarray(poster).save(pp)
Image.fromarray(poster).resize((PW // 2, PH // 2), Image.LANCZOS).save(os.path.join(MB_OUT, "mandelbulb_4k_from_8k.png"))
print("   poster %dx%d at %d rays per pixel (%.2f G rays) in %.1f s · %s" % (PW, PH, PSS * PSS, PW * PH * PSS * PSS / 1e9, ps, pp))
card = {"fps_4k_x4": round(fps, 2), "grays_per_s": round(FW * FH * 4 * fps / 1e9, 3), "poster": [PW, PH, PSS * PSS], "poster_s": round(ps, 2),
        "device": dict(mb_adapter.info).get("device"), "backend": dict(mb_adapter.info).get("backend_type"), "wgpu": wgpu.__version__}
open(os.path.join(MB_OUT, "poster_card.json"), "w").write(json.dumps(card, indent=1))
if display:
    display(Image.fromarray(poster).resize((1600, 900), Image.LANCZOS) if not QUICK else Image.fromarray(poster))
    cx, cy = PW // 2, PH // 2
    display(Image.fromarray(poster[cy - PH // 8: cy + PH // 8, cx - PW // 8: cx + PW // 8]))     # a 1:1 crop of the centre
print("✅ V2 · %s · %s" % (MB_OUT, json.dumps(card)))
