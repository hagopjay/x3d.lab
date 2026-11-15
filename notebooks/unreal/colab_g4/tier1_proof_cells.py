# Tier-1 proof run for Unreal 5.8.4 as an X3D endpoint, on a FRESH runtime of x3d.lab main (PRs #29-#31: auto exposure,
# Nanite ray-tracing mode, grade-name guard, X3D shadows field). Load with:
#   TBC = open("/content/drive/MyDrive/x3dlab/x3d_tier1_proof_cells.py").read().split("\n# %% ")
#   def proof(step): exec("#" + next(c for c in TBC[1:] if c.startswith(step)), globals())
# Order: Runtime > Restart session, then notebook 07 steps 1 and 2, then proof("F0"), then steps 3 -> 12 as normal
# (step 5 finds the engine already on the local disk), then proof("F1").
# The bounds in F1 are fixed here, before the numbers exist, so the result can't be tuned to pass.
# (Do not name the loader `tier`: notebook 07 already has a module called tier, used by steps 3 and 4.)

# %% F0 · the proof run's settings (after step 2, before step 3; 2 s)
import glob, os
from x3due import showcase, project as _proj
hits = sorted(glob.glob("/content/drive/MyDrive/x3dlab/**/e1-pbr-grid.x3d", recursive=True))
assert hits, "e1-pbr-grid.x3d not found under MyDrive/x3dlab"
SD = "file://" + os.path.dirname(hits[0]) + "/"
OWN = "generated for x3d.lab by HagopJay and Claude, 15 Nov 2026"
showcase.CATALOGUE.update({
  "e1-pbr-grid": {"title": "E1 PBR grid", "kind": "x3d", "url": SD + "e1-pbr-grid.x3d", "licence": OWN, "why": "metallic x roughness 7x7"},
  "e2-cornell": {"title": "E2 Cornell box", "kind": "x3d", "url": SD + "e2-cornell-box.x3d", "licence": OWN, "why": "bounce light"},
  "e3-lights": {"title": "E3 light ladder", "kind": "x3d", "url": SD + "e3-light-ladder.x3d", "licence": OWN, "why": "each X3D light, PBR vs classic"}})
SCENES = "calib, stilllife, odyssey, e1-pbr-grid, e2-cornell, e3-lights"
RUNG, LOOK, EXPOSURE, MAX_VIEWPOINTS, COMPARE = "high", "cinematic", "auto", 2, True
_rc, LAB_HEAD = git(["-C", str(LAB_DIR), "rev-parse", "--short", "HEAD"])
_main = all(hasattr(_proj, a) for a in ("SHADOW_MODES", "shadow_mode"))
print("x3d.lab at %s · %s" % (LAB_HEAD, "includes PR #31 (X3D shadows field)" if _main else "OLD CODE: PR #31 missing, restart the session and rerun step 2"))
print("proof run: %s · rung %s · look %s · exposure %s · %d viewpoints per scene" % (SCENES, RUNG, LOOK, EXPOSURE, MAX_VIEWPOINTS))
print("now run steps 3 -> 12 (about 30-45 min on the G4, odyssey is the slow one), then proof(\"F1\")")

# %% F1 · Tier-1 proof: the FLIP triangle, the repeat, the bounds (after step 10; about 2-4 min per scene)
import io, json, sys, subprocess, time
import numpy as np
from pathlib import Path
from PIL import Image
from IPython.display import display
try:
    import flip_evaluator as flip
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "-q", "install", "flip-evaluator"], check=True)
    import flip_evaluator as flip
from x3dlab import browser as xbrowser
# Bounds, fixed before the run. FLIP is over object pixels only (the magenta key left out), 0 = identical, 1 = opposite.
BOUNDS = {"silhouette_iou": 0.95,       # Unreal's outline vs X_ITE's
          "spread_margin": 0.05,        # Unreal may differ from X_ITE and Cycles by no more than they differ from each other, + 0.05
          "repeat": 0.02}               # the same still rendered twice by Unreal: the noise floor
def _arr(b): return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), np.float32) / 255.0
def _key(a): return (np.abs(a - np.array([1.0, 0.0, 1.0], np.float32)) < 60 / 255).all(-1)
def _oflip(ref, test, keep=False):
    if not (ref and test):
        return None
    A, B = _arr(ref), _arr(test)
    if A.shape != B.shape:
        B = np.asarray(Image.fromarray((B * 255).astype(np.uint8)).resize(A.shape[1::-1], Image.LANCZOS), np.float32) / 255.0
    emap, mean, _ = flip.evaluate(A, B, "LDR", applyMagma=False)
    emap = np.squeeze(emap); m = ~(_key(A) & _key(B))
    r = {"frame": round(float(mean), 4), "object": round(float(emap[m].mean()), 4) if m.any() else None, "share": round(float(m.mean()), 3)}
    if keep:
        r["_map"] = emap
    return r
def _o(row, k): return (row.get(k) or {}).get("object")
def _f(v): return "  –  " if v is None else "%.3f" % v
HB = xbrowser.start(gpu=True, viewport=(960, 540))
XI_INFO = {k: str(v) for k, v in (getattr(HB, "info", {}) or {}).items()}
print("X_ITE renderer:", XI_INFO.get("webgl", "?"))
TIER, STRIPS = {}, []
for n, out in RND.items():
    r = SCN[n]; t0 = time.time()
    cams = [s["viewpoint"] for s in out["stills"]]
    kA = pipeline.render(LAB, n, cams, "preview", PLAN["ladder"]["preview"], look="match", key=True, res_override=(960, 540), grade_name="tierA")
    kB = pipeline.render(LAB, n, cams, "preview", PLAN["ladder"]["preview"], look="match", key=True, res_override=(960, 540), grade_name="tierB")
    scene_dir = Path(r["fetch"]["main"]).parent
    main_name = Path(r["wrapper"]).name if r["kind"] == "gltf" else Path(r["fetch"]["main"]).name
    xi = engines.xite_views(HB, scene_dir, main_name, r["manifest"], size=(960, 540))
    cy = engines.cycles_first_view(BL, r["fetch"]["main"], WORK / "out" / ("tier_cycles_%s.png" % n)) if (BL and r["kind"] == "x3d") else {}
    vps = {project.ue_name(x3dscene.viewpoint_name(v)): x3dscene.viewpoint_name(v) for v in r["manifest"]["viewpoints"]}
    for i, cam in enumerate(cams):
        def _png(k):
            s = next((s for s in k["stills"] if s["viewpoint"] == cam and s.get("png")), None)
            return Path(s["png"]).read_bytes() if s else None
        A, B = _png(kA), _png(kB)
        xv = xi.get(vps.get(cam, cam)) or {}
        X = xv.get("png"); C = cy.get("png_key") if i == 0 else None
        j = compare.judge(X, A, formats) if (X and A) else {}
        row = {"ue_x": _oflip(X, A, keep=True), "ue_c": _oflip(C, A), "x_c": _oflip(C, X), "repeat": _oflip(A, B),
               "iou": j.get("iou"), "ssim": j.get("ssim"), "colour_delta": j.get("colour_delta"),
               "xite_ok": xv.get("ok"), "xite_errors": xv.get("errors"),
               "still_s": next((s.get("seconds") for s in out["stills"] if s["viewpoint"] == cam), None)}
        checks = {}
        if row["iou"] is not None:
            checks["silhouette"] = row["iou"] >= BOUNDS["silhouette_iou"]
        if _o(row, "repeat") is not None:
            checks["repeatable"] = _o(row, "repeat") <= BOUNDS["repeat"]
        ue = [v for v in (_o(row, "ue_x"), _o(row, "ue_c")) if v is not None]
        if _o(row, "x_c") is not None and ue:
            checks["within reference spread"] = max(ue) <= _o(row, "x_c") + BOUNDS["spread_margin"]
        row["checks"] = checks
        row["verdict"] = ("SKIP" if "silhouette" not in checks else "PASS" if all(checks.values()) else "FAIL")   # no X_ITE still, no verdict
        if row["ue_x"] and "_map" in row["ue_x"] and i == 0:
            STRIPS.append((n, cam, A, X, C, row["ue_x"].pop("_map")))
        elif row["ue_x"]:
            row["ue_x"].pop("_map", None)
        TIER["%s/%s" % (n, cam)] = row
        if not X:
            print("   %s/%s: X_ITE drew nothing · ok=%s · errors %s" % (n, cam, xv.get("ok"), xv.get("errors")))
        SC.add("F1 tier-1", "%s/%s" % (n, cam), row["verdict"],
               "IoU %s · FLIP UE-X %s · UE-C %s · X-C %s · repeat %s" % (_f(row["iou"]), _f(_o(row, "ue_x")), _f(_o(row, "ue_c")),
                                                                     _f(_o(row, "x_c")), _f(_o(row, "repeat"))))
    print("%-12s done in %.0f s" % (n, time.time() - t0))
HB.close()
# the table
print("\n%-26s %6s %7s %7s %7s %7s  %-8s %s" % ("scene/viewpoint", "IoU", "UE-X", "UE-C", "X-C", "repeat", "verdict", "failed checks"))
for k, row in TIER.items():
    print("%-26s %6s %7s %7s %7s %7s  %-8s %s" % (k[:26], _f(row["iou"]), _f(_o(row, "ue_x")), _f(_o(row, "ue_c")), _f(_o(row, "x_c")),
          _f(_o(row, "repeat")), row["verdict"], ", ".join(c for c, ok in row["checks"].items() if not ok)))
cnt = {v: sum(1 for r in TIER.values() if r["verdict"] == v) for v in ("PASS", "FAIL", "SKIP")}
gpu = {n: (o.get("gpu") or {}) for n, o in RND.items()}
peak = max([(g.get("peak_mem_mb") or 0) for g in gpu.values()] or [0]) / 1024
print("\nverdicts: %(PASS)d PASS · %(FAIL)d FAIL · %(SKIP)d SKIP" % cnt, "· peak VRAM across the stills %.1f GB" % peak)
# the strips: Unreal key | X_ITE | Cycles | FLIP map (Unreal vs X_ITE), first viewpoint of each scene
try:
    import matplotlib
    cmap = matplotlib.colormaps["magma"]
except Exception:
    cmap = None
for n, cam, A, X, C, emap in STRIPS:
    tiles = [Image.open(io.BytesIO(b)).convert("RGB").resize((480, 270)) if b else Image.new("RGB", (480, 270), (30, 30, 30)) for b in (A, X, C)]
    em = (cmap(np.clip(emap, 0, 1))[..., :3] * 255).astype(np.uint8) if cmap else (np.clip(emap, 0, 1) * 255).astype(np.uint8)
    tiles.append(Image.fromarray(em).convert("RGB").resize((480, 270)))
    strip = Image.new("RGB", (4 * 484, 274), (10, 10, 12))
    for c, t in enumerate(tiles):
        strip.paste(t, (2 + c * 484, 2))
    print("%s/%s · Unreal key | X_ITE | Cycles | FLIP Unreal vs X_ITE (%s)" % (n, cam, _f(_o(TIER["%s/%s" % (n, cam)], "ue_x"))))
    display(strip)
STAMP = time.strftime("%Y%m%d-%H%M%S")
TD = Path(DRIVE_DIR) / "tier1" / STAMP; TD.mkdir(parents=True, exist_ok=True)
T1 = {"bounds": BOUNDS, "rows": TIER, "counts": cnt, "peak_vram_gb": round(peak, 2), "xite": XI_INFO,
      "rig": {k: CARD.get(k) for k in ("lab_commit", "hardware", "options") if k in CARD}, "lab_head": globals().get("LAB_HEAD"),
      "flip": "flip-evaluator %s, LDR, object pixels only (magenta key left out)" % getattr(flip, "__version__", "?"),
      "renders": {n: {"seconds": o.get("seconds"), "gpu": o.get("gpu")} for n, o in RND.items()}}
(TD / "tier1_card.json").write_text(json.dumps(T1, indent=1, default=str))
CARD["tier1"] = T1
print("✅ F1 · tier-1 card in %s · paste me the table above" % TD)
