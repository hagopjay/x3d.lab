# x3d -> Unreal 5.8.4 on Colab G4: extra cells for x3d.lab notebook 07 (07_Unreal_Fidelity.ipynb, x3d.lab main 70551e8)
# Paste each "# %%" block as its own cell at the place its title says. Weights are for a Colab G4 (RTX PRO 6000 Blackwell, 96 GB).
# Written 14 Nov 2026. Nothing here prints or stores a token or an Epic link.

# %%
#@title 0 · G4 hardware card (FIRST cell; seconds)
import subprocess, glob, os, shutil, json
from google.colab import drive; drive.mount('/content/drive')
HW = {"gpu": subprocess.run("nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader",
                            shell=True, capture_output=True, text=True).stdout.strip(),
      "cpus": os.cpu_count(),
      "ram_gb": round(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1e9, 1),
      "disk_free_gb": round(shutil.disk_usage('/content').free / 1e9, 1)}
print(json.dumps(HW, indent=1), "\n(unpacking 5.8.4 needs ~75 GB local disk)")
for z in glob.glob('/content/drive/MyDrive/x3dlab/unreal/*Unreal*Engine*.zip'):
    print("engine zip:", os.path.basename(z), round(os.path.getsize(z) / 1e9, 1), "GB")
for f in sorted(glob.glob('/content/drive/MyDrive/x3dlab/unreal/scenes/*')):
    print("scene:", os.path.basename(f), os.path.getsize(f), "B")

# %%
#@title 2b · Add THALASSA, PRISMATICA and the GPU-instancing glb (after step 2, before step 7; seconds)
# Put the three files from the thread into MyDrive/x3dlab/unreal/scenes/ first. They stay private: they are read from your
# Drive (file:// URL) and never go to a repo. Converted to X3D 4.0 from your THALASSA-UE58 / PRISMATICA-UE58 uploads (13 Nov).
SCENE_DIR = "/content/drive/MyDrive/x3dlab/unreal/scenes"
OWN = "the owner's own project (THALASSA-UE58 / PRISMATICA-UE58 uploads, 13 Nov 2026); private, not published"
showcase.CATALOGUE.update({
    "thalassa": {"title": "THALASSA exhibition (1,719 instances)", "kind": "x3d", "url": "file://%s/thalassa-exhibition.x3d" % SCENE_DIR,
                 "licence": OWN, "why": "8 DEF shapes USEd 1,719 times; 2 viewpoints (hero_camera, detail_camera)"},
    "prismatica": {"title": "PRISMATICA aurora cathedral (static)", "kind": "x3d",
                   "url": "file://%s/prismatica-aurora-cathedral-static.x3d" % SCENE_DIR, "licence": OWN,
                   "why": "24,576 unlit ribbon triangles with per-vertex colour: KHR_materials_unlit + COLOR_0 into Unreal"},
    "thalassa-inst-gltf": {"title": "THALASSA, EXT_mesh_gpu_instancing glb", "kind": "gltf",
                           "url": "file://%s/thalassa-exhibition-instanced.glb" % SCENE_DIR, "licence": OWN,
                           "why": "add-on main (PR #34) Group instances: 10 nodes instead of 1,721; does Unreal's Interchange keep the instances?"},
})
EXTRA = "thalassa, prismatica, thalassa-inst-gltf"
SCENES = SCENES + ", " + EXTRA if EXTRA not in SCENES else SCENES
print("scenes for step 7:", SCENES)

# %%
#@title 9b · Keep the compare pairs for FLIP (just before step 10; instant)
FLIP_PAIRS = []
_judge_orig = getattr(compare, "_judge_orig", compare.judge)
compare._judge_orig = _judge_orig
def _judge_keep(a, b, *k, **kw):
    FLIP_PAIRS.append((a, b))
    return _judge_orig(a, b, *k, **kw)
compare.judge = _judge_keep
print("step 10 will keep its X_ITE / Cycles / Unreal pairs for FLIP")

# %%
#@title 10b · NVIDIA FLIP on every pair (after step 10; ~1 min, CPU)
subprocess.run("pip -q install flip-evaluator", shell=True)
import io, numpy as np, flip_evaluator as flip
from PIL import Image
def _arr(b):
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32) / 255.0
labels = []
for key, v in CMP.items():                     # step 10 judges X_ITE first, then Cycles (first viewpoint only)
    if v.get("xite"): labels.append((key, "X_ITE"))
    if v.get("cycles"): labels.append((key, "Cycles"))
eng = (CARD.get("engine") or {}).get("version") or ENGINE_VERSION
print("hardware: %s · Unreal %s · rung preview key, look match · FLIP (LDR, flip-evaluator)" % (HW["gpu"], eng))
FLIP = {}
for (key, ref_name), (ref, test) in zip(labels, FLIP_PAIRS):
    try:
        _, mean, _ = flip.evaluate(_arr(ref), _arr(test), "LDR")
        FLIP["%s vs %s" % (key, ref_name)] = round(float(mean), 4)
        print("%-40s vs %-6s  FLIP %.4f  (0 = same, 1 = opposite)" % (key, ref_name, mean))
    except Exception as exc:
        print("%-40s vs %-6s  FLIP failed: %s" % (key, ref_name, exc))
CARD["flip"] = {"pairs": FLIP, "hardware": HW, "unreal": eng}
