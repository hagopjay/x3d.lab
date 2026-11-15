# Leg E on the Colab G4 (15 Nov 2026): known physics scenes (scenes/e1-pbr-grid.x3d, e2-cornell-box.x3d, e3-light-ladder.x3d)
# through notebook 07, against X_ITE and Cycles. Paste each block as its own cell:
#   pass 1 settings -> steps 7, 8, 9, 10 -> 10c (FLIP over object pixels) ; pass 2 (cinematic bracket, 4K keepers, Cornell path-traced).
# Results and the gaps they named: README.md in this folder.

# %% Leg E · E1 PBR grid, E2 Cornell box, E3 light ladder
#@title Leg E · E1 PBR grid, E2 Cornell box, E3 light ladder
import glob, os
from x3due import lines, showcase
hits = sorted(glob.glob("/content/drive/MyDrive/x3dlab/**/e1-pbr-grid.x3d", recursive=True))
assert hits, "e1-pbr-grid.x3d not found under MyDrive/x3dlab"
SD = "file://" + os.path.dirname(hits[0]) + "/"
OWN = "generated for x3d.lab by HagopJay and Claude, 15 Nov 2026"
showcase.CATALOGUE.update({
  "e1-pbr-grid": {"title": "E1 PBR grid", "kind": "x3d", "url": SD + "e1-pbr-grid.x3d", "licence": OWN, "why": "metallic x roughness 7x7"},
  "e2-cornell": {"title": "E2 Cornell box", "kind": "x3d", "url": SD + "e2-cornell-box.x3d", "licence": OWN, "why": "bounce light"},
  "e3-lights": {"title": "E3 light ladder", "kind": "x3d", "url": SD + "e3-light-ladder.x3d", "licence": OWN, "why": "each X3D light, PBR vs classic"}})
SCENES = "e1-pbr-grid, e2-cornell, e3-lights"
RUNG, LOOK, MAX_VIEWPOINTS, COMPARE = "preview", "match", 1, True
print("leg E pass 1:", SCENES, "from", SD)

# %% Leg E · pass 2: cinematic bracket, 4K keepers, Cornell path-traced
#@title Leg E · pass 2: cinematic bracket, 4K keepers, Cornell path-traced
import json, numpy as np
from PIL import Image
from IPython.display import display
LOOK = "cinematic"; EV0 = float(getattr(LAB.mapping, "ev100", 0.0)); NAN = {"r.RayTracing.Nanite.Mode": 1}
def _bad(png):
    a = np.asarray(Image.open(png).convert("RGB"), np.float32) / 255.0
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    return abs(float(np.median(y)) - 0.38) + 2.5 * max(0, float((y > 0.98).mean()) - 0.02) + max(0, float((y < 0.02).mean()) - 0.25)
def _rung(base, extra=None):
    r = json.loads(json.dumps(PLAN["ladder"][base])); r["cvars"] = dict(r["cvars"], **NAN, **(extra or {})); return r
EBEST, EKEEP = {}, {}
for n in ["e1-pbr-grid", "e2-cornell", "e3-lights"]:
    res = IMP.get(n)
    if not res or not res["report"].get("ok"):
        print(n, "not imported"); continue
    cams = staging.pick(sorted(res["report"]["sequences"]), SCN.get(n, {}).get("framing", {}), 1)
    offs = [4, 2, 0, -2, -4, -6, -8]
    grades = {"eb_%s%d" % ("p" if o > 0 else "m", abs(o)): {"post": {"auto_exposure_bias": EV0 + o}} for o in offs}
    out = pipeline.render_grades(LAB, n, cams, "preview", _rung("preview"), grades, look=LOOK, res_override=(1280, 720), label="ebracket")
    scored = [(_bad(out["stills"][(g, cams[0])]), o) for g, o in zip(grades, offs) if out["stills"].get((g, cams[0]))]
    if not scored:
        print(n, "no bracket stills"); continue
    EBEST[n] = min(scored)[1]
    print(n, cams[0], " | ".join("EV%+d %.2f" % (o, b) for b, o in sorted(scored, key=lambda t: -t[1])), "-> EV%+d" % EBEST[n])
    for base, extra in [("ultra_lumen", {})] + ([("ultra", {"r.PathTracing.Denoiser": 1})] if n == "e2-cornell" else []):
        g = {"ek_" + base: {"post": {"auto_exposure_bias": EV0 + EBEST[n], "bloom_intensity": 0.4, "bloom_threshold": 1.0, "vignette_intensity": 0.2}}}
        k = pipeline.render_grades(LAB, n, cams, base, _rung(base, extra), g, look=LOOK, res_override=(3840, 2160), label="ekeeper_" + base)
        for (gn, cam), png in k["stills"].items():
            if png:
                EKEEP[(n, base)] = png; print("  ", base, png); display(Image.open(png).convert("RGB").resize((1280, 720)))
CARD["legE_beauty"] = {"best_ev": EBEST, "stills": {"%s|%s" % k: v for k, v in EKEEP.items()}}

# %% 10c · FLIP over the object pixels only (keyed background left out)
#@title 10c · FLIP over the object pixels only (keyed background left out)
import io, numpy as np, flip_evaluator as flip
from PIL import Image
def _arr(b): return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32) / 255.0
def _key(a): return (np.abs(a - np.array([1.0, 0.0, 1.0])) < 60 / 255).all(-1)
labels = [(k, n) for k, v in CMP.items() for n in (("X_ITE",) if v.get("xite") else ()) + (("Cycles",) if v.get("cycles") else ())]
FLIP_OBJ = {}
for (key, ref_name), (ref, test) in zip(labels, FLIP_PAIRS[-len(labels):]):
    A, B = _arr(ref), _arr(test)
    if A.shape != B.shape:
        B = np.asarray(Image.fromarray((B * 255).astype(np.uint8)).resize(A.shape[1::-1]), np.float32) / 255
    emap, mean, _ = flip.evaluate(A, B, "LDR", applyMagma=False)
    emap = np.squeeze(emap); m = ~(_key(A) & _key(B))
    obj = float(emap[m].mean()) if m.any() else float("nan")
    FLIP_OBJ["%s vs %s" % (key, ref_name)] = {"frame": round(float(mean), 4), "object": round(obj, 4), "object_share": round(float(m.mean()), 3)}
    print("%-22s vs %-6s frame %.4f · object %.4f over %4.1f%% of pixels" % (key, ref_name, mean, obj, 100 * m.mean()))
CARD["flip_object"] = FLIP_OBJ
