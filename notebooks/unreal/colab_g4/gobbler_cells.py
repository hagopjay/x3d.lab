# Gobbler at 4K on the Colab G4, run INSIDE the notebook-07 session (no new runtime, no notebook 08 bring-up).
# Rig: Colab G4 RTX PRO 6000 Blackwell SE 95.6 GB · UE 5.8.4-58902052 Vulkan SM6+RT · x3d.lab@70551e8 (the 07 kernel).
# Load once: GBC = open("/content/drive/MyDrive/x3dlab/x3d_gobbler_g4_cells_v2.py").read().split("\n# %% ")
#            def gob(step): exec("#" + next(c for c in GBC[1:] if c.startswith(step)), globals())
# then gob("G1") ... gob("G6"), one per Colab cell, in order (or paste each "# %%" block as its own cell). Every still gets a unique grade name (nothing is overwritten).

# %% G1 · setup (5 s)
import json, time, shutil
import numpy as np
from pathlib import Path
from PIL import Image
from IPython.display import Image as IPImage, display
from x3due import gobbler, showcase, x3dscene, pipeline
assert globals().get("LAB") is not None, "run this in the notebook-07 session (LAB missing)"
def _names(s): return [x.strip() for x in s.split(",") if x.strip()]
# X_ITE-look bakes (gobbler.write_glb_xite): per-facet FacetShader colours, the EyeShader iris, ribbon and star colours as textures.
# (v1 of these cells used gobbler.write_glb, whose flat placeholder materials drew the cream bird in your first G4 sheet.)
GB_LOOKBAKES = {"xite": {},                                                                       # as X_ITE draws it
                "xite_hot": {"ribbons": {"strength": 5.0, "whiten": 0.5}, "points": {"strength": 6.0}, "eye": {"strength": 3.0}},
                "xite_gold": {"facets": {"metal": 0.85, "rough": 0.2, "base_k": 0.6, "emissive_k": 0.45}}}
GB_BAKES = list(GB_LOOKBAKES)
GB_VIEWS = _names("EyeToEye, Flywheel, Colossus, Crown")
GB_ROUND_VIEWS = _names("EyeToEye, Flywheel")        # the looks round uses two views to save time
GB_LOOKS = _names("glow, film, macro, warm, extreme")
NAN = {"r.RayTracing.Nanite.Mode": 1}                 # round spheres in ray tracing (your 9N test)
GB = WORK / "out" / "gobbler"; GB.mkdir(parents=True, exist_ok=True)
GOB_DRIVE = Path(DRIVE_DIR) / "gobbler"; GOB_DRIVE.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d-%H%M%S")
def _rung(base, w, h, samples, warm=32, extra=None):
    r = json.loads(json.dumps(PLAN["ladder"][base]))
    r.update(resolution=[w, h], spatial_samples=int(samples), warmup_frames=warm, tiles=1)
    r["cvars"] = dict(r["cvars"], **NAN, **(extra or {}))
    return r
def _luma(png):
    a = np.asarray(Image.open(png).convert("RGB"), np.float32) / 255.0
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    return {"p50": float(np.median(y)), "p99": float(np.percentile(y, 99)), "clip": float((y > 0.98).mean())}
print("✅ G1 · bakes %s · views %s · looks %s · Drive %s" % (GB_BAKES, GB_VIEWS, GB_LOOKS, GOB_DRIVE))
print("   rig: %s %s GB · UE %s" % (HW.get("kind"), HW.get("vram_gb"), (globals().get("ENGINE") or {}).get("version")))

# %% G2 · decode the Gobbler and draw the CPU references (1-3 min, CPU only)
SRC_GOB = showcase.fetch("gobbler3", WORK / "scenes")
GOB_X3D = Path(SRC_GOB["main"])
BAKE = gobbler.decode(GOB_X3D)
print("\n".join(gobbler.summary(BAKE)))
X3D_MANIFEST = x3dscene.manifest(GOB_X3D.read_text(encoding="utf-8", errors="replace"))
REFS, REF_PNG = {}, {}
(GB / "ref").mkdir(parents=True, exist_ok=True)
for vp in GB_VIEWS:
    if vp not in BAKE["viewpoints"]:
        print("⚠️ no viewpoint %s (have %s)" % (vp, ", ".join(BAKE["viewpoints"]))); continue
    t0 = time.time()
    REFS[vp] = gobbler.preview(BAKE, vp, 1280, 720)
    REF_PNG[vp] = gobbler.to_png(REFS[vp], GB / "ref" / ("ref_%s_1280x720.png" % vp))
    print("reference %-9s %.1f s" % (vp, time.time() - t0))
for vp in GB_ROUND_VIEWS:
    if vp in REF_PNG: display(IPImage(filename=str(REF_PNG[vp]), width=480))

# %% G3 · bake three X_ITE-look variants to glb and import each into Unreal (about 2-4 min per bake)
GLB, IMPORTED = {}, {}
MAN = gobbler.manifest_for(BAKE, X3D_MANIFEST)
for b in GB_BAKES:
    (GB / "bakes").mkdir(exist_ok=True)
    GLB[b] = gobbler.write_glb_xite(BAKE, GB / "bakes" / ("gobbler3_%s.glb" % b), look=GB_LOOKBAKES[b])
    print("%-7s glb %.1f MB · %d vertices · %d triangles" % (b, GLB[b]["bytes"] / 2**20, GLB[b]["vertices"], GLB[b]["triangles"]))
    res = pipeline.import_scene(LAB, "gob_" + b, GLB[b]["path"], "gltf", MAN, shape_centres=MAN["shape_centres"])
    rep = res["report"]; IMPORTED[b] = rep
    print("        import %s · %d cameras · %d lights · %.0f s" % ("ok" if rep.get("ok") else "FAILED", len(rep.get("cameras_placed", [])),
          len(rep.get("lights_placed", [])), res["seconds"]))
    if not rep.get("ok"): print(res.get("excerpt", "")[-2500:])
GB_OK = [b for b in GB_BAKES if (IMPORTED.get(b) or {}).get("ok")]
print("✅ G3 · imported:", GB_OK)

# %% G4 · exposure per viewpoint, fitted to the reference (one Unreal start, 20 small stills, about 3-6 min)
# Grade names avoid "+" and "-": Unreal job names turn both into "_", so +3 and -3 collided in v1 (the empty +3 column).
# Your odyssey f50 run showed why: one EV for every camera blows out the brighter one (GiftFrame). So each view gets its own EV.
BRACKET = (2, 0, -2, -4, -6)
base = GB_OK[0]
grades = {"xg%s%d" % ("p" if e > 0 else "m", abs(e)): gobbler.with_exposure(gobbler.GRADES["fidelity"], e) for e in BRACKET}
rung = _rung("high", 960, 540, 8, warm=16)
t0 = time.time()
out = pipeline.render_grades(LAB, "gob_" + base, GB_VIEWS, "gbx", rung, grades, label="gbx_" + STAMP)
print("bracket: %d/%d stills · %.0f s" % (sum(1 for p in out["stills"].values() if p), len(grades) * len(GB_VIEWS), time.time() - t0))
GB_EV, rows = {}, {}
for vp in GB_VIEWS:
    est = []
    for e in BRACKET:
        png = out["stills"].get(("xg%s%d" % ("p" if e > 0 else "m", abs(e)), vp))
        if not png or vp not in REFS: continue
        test = gobbler.resize_like(png, REFS[vp].shape)
        fit = gobbler.exposure_fit(test, REFS[vp]); L = _luma(png)
        if fit != 0.0:                               # exposure_fit says exactly 0 when no pixel is lit in both: no evidence
            est.append((abs(fit) + 4 * L["clip"], e + fit))
        else:
            est.append((9 + abs(L["p99"] - 0.85), e))     # fallback: the render whose highlights sit near 0.85 unclipped
        rows.setdefault(vp, {"reference": str(REF_PNG[vp])})["%+d EV" % e] = png
        print("%-9s EV %+3d · fit %+5.2f · median %.2f · clip %4.1f%%" % (vp, e, fit, L["p50"], 100 * L["clip"]))
    if est:
        GB_EV[vp] = round(min(est)[1] * 2) / 2      # the render that needed the least correction, to the nearest half stop
sheet = gobbler.contact_sheet(rows, ["reference"] + ["%+d EV" % e for e in BRACKET], GB / ("exposure_%s.png" % STAMP), cell_w=320)
display(IPImage(filename=sheet)); shutil.copy(sheet, GOB_DRIVE / Path(sheet).name)
print("✅ G4 · EV per view:", GB_EV)

# %% G5 · the looks round: 3 bakes × 5 looks × 2 views at 1280x720, HW Lumen (about 10-15 min)
SCORES, LOOK_STILLS = {}, {}
rung = _rung("high", 1280, 720, 16, warm=16)
for b in GB_OK:
    for ev in sorted({GB_EV.get(vp, 0.0) for vp in GB_ROUND_VIEWS}):
        views = [vp for vp in GB_ROUND_VIEWS if GB_EV.get(vp, 0.0) == ev]
        grades = {"L%s_%s" % (STAMP[-4:], g): gobbler.with_exposure(gobbler.GRADES[g], ev) for g in GB_LOOKS}
        t0 = time.time()
        out = pipeline.render_grades(LAB, "gob_" + b, views, "gbl", rung, grades, label="gbl_%s_%s" % (b, STAMP))
        print("%-7s EV %+.1f · %s · %d stills · %.0f s" % (b, ev, views, sum(1 for p in out["stills"].values() if p), time.time() - t0))
        for (gname, vp), png in out["stills"].items():
            if not png: continue
            look = gname.split("_", 1)[1]
            s = gobbler.score(gobbler.resize_like(png, REFS[vp].shape), REFS[vp]); s.update(_luma(png))
            SCORES[(b, look, vp)] = s; LOOK_STILLS[(b, look, vp)] = png
rows, sc = {}, {}
for (b, look, vp), png in LOOK_STILLS.items():
    r = "%s · %s" % (vp, b)
    rows.setdefault(r, {"reference": str(REF_PNG[vp])})[look] = png
    sc[(r, look)] = SCORES[(b, look, vp)]
sheet = gobbler.contact_sheet(rows, ["reference"] + GB_LOOKS, GB / ("looks_%s.png" % STAMP), cell_w=360, scores=sc)
display(IPImage(filename=sheet)); shutil.copy(sheet, GOB_DRIVE / Path(sheet).name)
print("%-8s %-8s %-9s %6s %6s %6s %6s" % ("bake", "look", "view", "SSIM", "litF", "Δ", "clip%"))
for (b, look, vp), s in sorted(SCORES.items()):
    print("%-8s %-8s %-9s %6.3f %6.3f %6.1f %6.1f" % (b, look, vp, s["ssim"], s["lit_f"], s["colour_delta"], 100 * s["clip"]))
print("✅ G5 · now pick by EYE: set HERO_BAKE and HERO_LOOK in G6")

# %% G6 · the 4K heroes: your pick, all 4 views, HW Lumen at 64 samples, then the path tracer (about 10-20 min)
HERO_BAKE = globals().get("PICK_BAKE", "xite")    # xite | xite_hot | xite_gold
HERO_LOOK = globals().get("PICK_LOOK", "glow")    # glow | film | macro | warm | extreme
HERO_PT = globals().get("PICK_PT", True)          # also render the path-traced version (slower, truest light)
HERO_DIR = GOB_DRIVE / "hero" / STAMP; HERO_DIR.mkdir(parents=True, exist_ok=True)
HERO_SET = {}
passes = [("lumen", _rung("ultra_lumen", 3840, 2160, 64, warm=64))]
if HERO_PT:
    passes.append(("pt", _rung("ultra", 3840, 2160, 512, warm=32, extra={"r.PathTracing.Denoiser": 1})))
for kind, rung in passes:
    for ev in sorted({GB_EV.get(vp, 0.0) for vp in GB_VIEWS}):
        views = [vp for vp in GB_VIEWS if GB_EV.get(vp, 0.0) == ev]
        g = gobbler.with_exposure(gobbler.GRADES[HERO_LOOK], ev)
        name = "H%s_%s_%s" % (STAMP[-4:], kind, HERO_LOOK)
        t0 = time.time()
        out = pipeline.render_grades(LAB, "gob_" + HERO_BAKE, views, "gbh", rung, {name: g}, label="gbh_%s_%s_%s" % (kind, HERO_BAKE, STAMP))
        print("%-5s EV %+.1f · %s · %d stills · %.0f s" % (kind, ev, views, sum(1 for p in out["stills"].values() if p), time.time() - t0))
        for (gname, vp), png in out["stills"].items():
            if not png: continue
            dst = HERO_DIR / ("gobbler_%s_%s_%s_%s_3840x2160.png" % (vp, HERO_BAKE, HERO_LOOK, kind))
            shutil.copy(png, dst); HERO_SET[(kind, vp)] = str(dst)
            display(IPImage(filename=png, width=960))
(HERO_DIR / "look.json").write_text(json.dumps({"bake": HERO_BAKE, "bake_look": GB_LOOKBAKES[HERO_BAKE], "look": HERO_LOOK,
    "grade": gobbler.GRADES[HERO_LOOK], "ev_per_view": GB_EV, "passes": [k for k, _ in passes], "res": [3840, 2160],
    "rig": {"gpu": HW.get("kind"), "vram_gb": HW.get("vram_gb"), "engine": (globals().get("ENGINE") or {}).get("version")}},
    indent=1, default=str))
print("✅ G6 · %d heroes saved to %s" % (len(HERO_SET), HERO_DIR))
