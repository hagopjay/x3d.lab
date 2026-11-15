# Two "banger" cells for the notebook-07 G4 session (after the Gobbler v2 cells and Leg E). Each is self-contained.
# Rig: Colab G4 RTX PRO 6000 Blackwell SE · UE 5.8.4 Vulkan SM6+RT · HW Lumen · x3d.lab@70551e8 kernel.
# Every still has a stamped grade name, so nothing earlier is overwritten. Results go to MyDrive/x3dlab/unreal/joy/<stamp>/.

# %% J1 · Eight moods of the Gobbler (one poster, 3 views x 8 moods at 1080p; about 10-12 min)
import json, time, base64, shutil, subprocess, math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from IPython.display import display, HTML
from x3due import pipeline
J_STAMP = time.strftime("%H%M")
JOY = Path(DRIVE_DIR) / "joy" / time.strftime("%Y%m%d-%H%M%S"); JOY.mkdir(parents=True, exist_ok=True)
J_NAN = {"r.RayTracing.Nanite.Mode": 1}
def _jrung(base, w, h, spp, warm=24, extra=None):
    r = json.loads(json.dumps(PLAN["ladder"][base])); r.update(resolution=[w, h], spatial_samples=spp, warmup_frames=warm, tiles=1)
    r["cvars"] = dict(r["cvars"], **J_NAN, **(extra or {})); return r
def _font(px):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try: return ImageFont.truetype(f, px)
        except Exception: pass
    return ImageFont.load_default()
J_BAKE = "gob_" + ("xite_hot" if "xite_hot" in globals().get("GB_OK", []) else (globals().get("GB_OK") or ["xite"])[0])
J_VIEWS = ["EyeToEye", "Flywheel", "Colossus"]
J_EV = {v: (globals().get("GB_EV") or {}).get(v, 0.0) for v in J_VIEWS}
FX = {"r.SceneColorFringeQuality": 1, "r.DepthOfFieldQuality": 2}          # the rung turns fringe and DOF off; moods turn them back on
MOODS = {
  "ember":      ("Ember",       {"post": {"white_temp": 3300.0, "bloom_intensity": 1.4, "bloom_threshold": 0.6, "vignette_intensity": 0.45}, "light_scale": 1.3}),
  "glacier":    ("Glacier",     {"post": {"white_temp": 11000.0, "bloom_intensity": 1.0, "bloom_threshold": 0.8, "vignette_intensity": 0.3}}),
  "neon":       ("Neon dream",  {"post": {"bloom_intensity": 2.4, "bloom_threshold": 0.35, "scene_fringe_intensity": 1.6, "vignette_intensity": 0.35}, "light_scale": 1.6}),
  "anamorphic": ("Lens flare",  {"post": {"lens_flare_intensity": 2.5, "lens_flare_threshold": 0.5, "bloom_intensity": 1.2, "bloom_threshold": 0.7}}),
  "macro":      ("Macro f/1.2", {"post": {"bloom_intensity": 1.0, "bloom_threshold": 0.8, "vignette_intensity": 0.4}, "aperture": 1.2, "focus": "auto"}),
  "noir":       ("Film noir",   {"post": {"film_grain_intensity": 0.35, "vignette_intensity": 0.7, "bloom_intensity": 0.5, "white_temp": 6200.0}, "light_scale": 0.8}),
  "supernova":  ("Supernova",   {"post": {"bloom_intensity": 3.0, "bloom_threshold": 0.3, "vignette_intensity": 0.25}, "light_scale": 3.0, "ev": -1.0}),
  "velvet":     ("Velvet",      {"post": {"white_temp": 5200.0, "white_tint": 0.15, "bloom_intensity": 0.9, "scene_fringe_intensity": 0.5, "vignette_intensity": 0.4}}),
}
rung = _jrung("high", 1920, 1080, 24)
J1 = {}
for ev in sorted(set(J_EV.values())):
    views = [v for v in J_VIEWS if J_EV[v] == ev]
    grades = {}
    for k, (title, g) in MOODS.items():
        g = json.loads(json.dumps(g)); e = g.pop("ev", 0.0)
        g["post"]["auto_exposure_bias"] = round(ev + e, 2); g["headlight"] = False; g["cvars"] = dict(FX)
        grades["m%s_%s" % (J_STAMP, k)] = g
    t0 = time.time()
    out = pipeline.render_grades(LAB, J_BAKE, views, "joy", rung, grades, label="joy_moods_" + J_STAMP)
    print("EV %+.1f · %s · %d/%d stills · %.0f s" % (ev, ", ".join(views), sum(1 for p in out["stills"].values() if p), len(grades) * len(views), time.time() - t0))
    for (gname, vp), png in out["stills"].items():
        if png:
            k = gname.split("_", 1)[1]; J1[(vp, k)] = png
            shutil.copy(png, JOY / ("gobbler_%s_%s_1080p.png" % (vp, k)))
# the poster: rows = views, columns = moods
cw, ch, pad, head = 480, 270, 6, 54
poster = Image.new("RGB", (len(MOODS) * (cw + pad) + pad, head + len(J_VIEWS) * (ch + pad + 26) + 40), (8, 8, 11))
d = ImageDraw.Draw(poster); f1, f2 = _font(30), _font(17)
d.text((pad + 4, 10), "The Gobbler · eight moods · Unreal 5.8.4 HW Lumen · RTX PRO 6000 Blackwell · from X3D", font=f1, fill=(255, 214, 140))
for r, vp in enumerate(J_VIEWS):
    for c, (k, (title, _)) in enumerate(MOODS.items()):
        x, y = pad + c * (cw + pad), head + r * (ch + pad + 26)
        if (vp, k) in J1:
            poster.paste(Image.open(J1[(vp, k)]).convert("RGB").resize((cw, ch), Image.LANCZOS), (x, y))
        d.text((x + 4, y + ch + 3), "%s · %s" % (title, vp), font=f2, fill=(220, 220, 225))
pp = JOY / ("gobbler_eight_moods_%s.png" % J_STAMP); poster.save(pp)
display(poster.resize((poster.width // 2, poster.height // 2), Image.LANCZOS))
for k in ("neon", "ember", "supernova"):
    if ("EyeToEye", k) in J1:
        print(MOODS[k][0]); display(Image.open(J1[("EyeToEye", k)]).convert("RGB").resize((1280, 720), Image.LANCZOS))
print("✅ J1 · %d stills + poster in %s" % (len(J1), JOY))

# %% J2 · Two flipbooks: a rack focus through the Gobbler, and dawn in the Cornell box (2 x 16 frames at 720p; about 10-14 min)
import json, time, base64, shutil, subprocess, math
from pathlib import Path
from PIL import Image
from IPython.display import display, HTML
from x3due import pipeline
J_STAMP2 = time.strftime("%H%M")
JOY = globals().get("JOY") or (Path(DRIVE_DIR) / "joy" / time.strftime("%Y%m%d-%H%M%S")); JOY.mkdir(parents=True, exist_ok=True)
def _jr(base, w, h, spp, extra=None):
    r = json.loads(json.dumps(PLAN["ladder"][base])); r.update(resolution=[w, h], spatial_samples=spp, warmup_frames=16, tiles=1)
    r["cvars"] = dict(r["cvars"], **{"r.RayTracing.Nanite.Mode": 1}, **(extra or {})); return r
def _book(frames, name, ms=110):
    """Ping-pong GIF + MP4 from the frames, shown inline."""
    ims = [Image.open(p).convert("RGB").resize((960, 540), Image.LANCZOS) for p in frames]
    seq = ims + ims[-2:0:-1]
    gif = JOY / (name + ".gif")
    seq[0].save(gif, save_all=True, append_images=seq[1:], duration=ms, loop=0, optimize=True)
    tmp = JOY / (name + "_frames"); tmp.mkdir(exist_ok=True)
    for i, im in enumerate(seq):
        im.save(tmp / ("f%03d.png" % i))
    mp4 = JOY / (name + ".mp4")
    subprocess.run("ffmpeg -y -loglevel error -framerate %d -i %s/f%%03d.png -c:v libx264 -pix_fmt yuv420p -crf 16 %s" % (round(1000 / ms), tmp, mp4), shell=True)
    display(HTML('<img src="data:image/gif;base64,%s" width="960">' % base64.b64encode(gif.read_bytes()).decode()))
    return gif, mp4
N = 16
J2 = {}
# 1) rack focus through the Gobbler: f/1.4, focus sweeps 1.5 m -> 120 m, bloom breathes, white balance drifts warm -> cool
bake = "gob_" + ("xite_hot" if "xite_hot" in globals().get("GB_OK", []) else (globals().get("GB_OK") or ["xite"])[0])
ev = (globals().get("GB_EV") or {}).get("EyeToEye", 0.0)
grades = {}
for i in range(N):
    t = i / (N - 1)
    grades["rf%s_%02d" % (J_STAMP2, i)] = {"post": {"auto_exposure_bias": ev, "bloom_intensity": round(0.9 + 0.8 * math.sin(math.pi * t), 3),
                                                   "bloom_threshold": 0.6, "vignette_intensity": 0.4, "white_temp": round(4200 + 5200 * t, 1)},
                                          "aperture": 1.4, "focus": round(150 * (12000 / 150) ** t, 1), "headlight": False,
                                          "cvars": {"r.DepthOfFieldQuality": 2}}
t0 = time.time()
out = pipeline.render_grades(LAB, bake, ["EyeToEye"], "joy", _jr("high", 1280, 720, 16), grades, label="joy_rack_" + J_STAMP2)
frames = [out["stills"].get((g, "EyeToEye")) for g in grades]
print("rack focus: %d/%d frames · %.0f s" % (sum(1 for f in frames if f), N, time.time() - t0))
if all(frames):
    J2["rack"] = _book(frames, "gobbler_rack_focus_" + J_STAMP2)
# 2) dawn in the Cornell box: the ceiling light rises from an ember (2200 K, 3 %) to daylight (7000 K, 250 %); Lumen bounces the walls' red and green
if "e2-cornell" in IMP and IMP["e2-cornell"]["report"].get("ok"):
    EV0 = float(getattr(LAB.mapping, "ev100", 0.0)) + (globals().get("EBEST") or {}).get("e2-cornell", 0)
    cams = staging.pick(sorted(IMP["e2-cornell"]["report"]["sequences"]), SCN.get("e2-cornell", {}).get("framing", {}), 1)
    grades = {}
    for i in range(N):
        t = i / (N - 1)
        grades["dawn%s_%02d" % (J_STAMP2, i)] = {"post": {"auto_exposure_bias": EV0, "white_temp": round(2200 + 4800 * t, 1), "bloom_intensity": 0.6,
                                                         "bloom_threshold": 1.0, "vignette_intensity": 0.3},
                                                "light_scale": round(0.03 * (2.5 / 0.03) ** t, 4), "headlight": False}
    t0 = time.time()
    out = pipeline.render_grades(LAB, "e2-cornell", cams, "joy", _jr("high", 1280, 720, 32), grades, look="cinematic", label="joy_dawn_" + J_STAMP2)
    frames = [out["stills"].get((g, cams[0])) for g in grades]
    print("Cornell dawn: %d/%d frames · %.0f s" % (sum(1 for f in frames if f), N, time.time() - t0))
    if all(frames):
        J2["dawn"] = _book(frames, "cornell_dawn_" + J_STAMP2, ms=140)
else:
    print("Cornell box not imported in this session (Leg E step 8): skipped")
print("✅ J2 · flipbooks (GIF + MP4) in %s: %s" % (JOY, {k: [p.name for p in v] for k, v in J2.items()}))
