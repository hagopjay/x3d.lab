# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/05_GPU_Hog.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 05 GPU Hog
#
# **One corpus, every engine, and findings sorted by the project that has to fix them.** About 230 scenes (one per X3D node from x3d.loom's bridge fixtures, the seven node types new in X3D 4.1, and the lab's own scenes), each as X3D 4.0 and 4.1, go through:
#
# | Engine | What it answers |
# |---|---|
# | **x3d_mcp** (the fork) and **lxml** with the version's own XSD | valid? which error kinds? do two schema engines agree? |
# | **X_ITE** in Chrome | loads? console errors? draws anything? (on the GPU if the machine allows, see step 3) |
# | **Blender** + x3d-blender-io | imports what? round-trips to 4.0 and 4.1? is the exported file valid X3D? which node types come back? |
# | **Cycles** on the GPU | draws the imported scene? |
# | **x3d.loom** Babylon bridge | its reader + Babylon against X_ITE, node by node |
#
# Then the **heavy** 02 Lab stress test on the GPU (after a warm-up, so first-render kernel loading is measured apart) and Chrome traces of growing meshes. Every finding names its project: **x3d_mcp, X_ITE, x3d-blender-io, x3d.loom, Copilot**, with the scene that shows it, and each project gets an issue draft.
#
# **Runtime:** *Runtime › Change runtime type* › **A100** or **G4** (L4 works, slower). On a CPU runtime the stages still run, but the render and trace numbers are CPU numbers. **Time:** about 30–60 minutes with every stage on an A100.

# %% [markdown]
# ## 1 · Start the lab

# %%
#@title 1 · Start x3d_mcp and the dashboard { display-mode: "form" }
LOG_LEVEL = "WARN"  #@param ["DEBUG", "INFO", "WARN", "ERROR"]
LAB_REPO = "hagopjay/x3d.lab"
LAB_REF = "main"

import importlib.util, os, subprocess, sys
if not os.environ.get("X3DLAB_DEV") and (importlib.util.find_spec("x3dlab") is None or os.environ.get("X3DLAB_REINSTALL")):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "x3dlab[notebook,tunnels,research] @ git+https://github.com/%s@%s" % (LAB_REPO, LAB_REF)], check=True)
import x3dlab
from x3dlab import hog

lab = x3dlab.up(tunnel="none", log_level=LOG_LEVEL)
print(lab.runtime_info["line"])
if "CPU" in lab.runtime_info["line"].split("|")[-1] and "NVIDIA" not in lab.runtime_info["line"]:
    print("⚠️  No GPU on this runtime: everything runs, but render and trace numbers will be CPU numbers.")

# %% [markdown]
# ## 2 · Blender and the GPU
#
# The same setup as 02 Lab. The first GPU render in a fresh runtime loads Cycles' kernels (minutes on an A100); the stress stage does a warm-up render first and reports that time separately.

# %%
#@title 2 · Set up Blender { display-mode: "form" }
BLENDER_SOURCE = "auto"  #@param ["auto", "wheel", "tarball"]
bl = lab.setup_blender(BLENDER_SOURCE)
print("Blender %s · best Cycles backend: %s · backends: %s" % (bl.version, bl.best_backend, bl.info.get("backends")))

# %% [markdown]
# ## 3 · Probe the machine
#
# What the engines will really get. Compute (CUDA, OptiX ray tracing) can work while the **graphics** half of NVIDIA's driver is missing from the container; then Chrome falls back to SwiftShader (WebGL on the CPU) and the OptiX *denoiser* cannot start. This cell says which, from the files that are actually there.

# %%
#@title 3 · Probe { display-mode: "form" }
probe = hog.probe(lab)
g = probe["graphics"]
print("GPU:", g["gpu"] or "none", "| NVIDIA_DRIVER_CAPABILITIES:", g["driver_capabilities"])
print("NVIDIA libraries:", ", ".join("%s %s" % (k, "✓" if v else "✗") for k, v in g["libs"].items()))
print("Vulkan ICDs:", g["vulkan_icds"] or "none", "| nvoptix.bin:", g["nvoptix_bin"] or "missing")
for v in g["verdicts"]:
    print("→", v)
for mode, info in probe["chrome"].items():
    print("Chrome %-11s WebGL: %s" % (mode, info.get("webgl") or info.get("error")))

# %% [markdown]
# ## 4 · Choose the stages
#
# Everything is on by default. Turn a stage off to save time; the report covers whatever ran. `MAX_NODES` caps the corpus for a quick look (0 = all of it).

# %%
#@title 4 · Settings { display-mode: "form" }
VALIDATE_AND_RENDER = True  #@param {type:"boolean"}
LOOM_BABYLON = True  #@param {type:"boolean"}
STRESS = True  #@param {type:"boolean"}
STRESS_PRESET = "heavy"  #@param ["standard", "heavy"]
TRACES = True  #@param {type:"boolean"}
X3D_41 = True  #@param {type:"boolean"}
CONSORTIUM = False  #@param {type:"boolean"}
CHROME_GPU = True  #@param {type:"boolean"}
MAX_NODES = 0  #@param {type:"integer"}
TRACE_SIZES = "20000, 200000, 1000000"  #@param {type:"string"}
NAME = ""  #@param {type:"string"}

stages = ["probe", "corpus"] + (["matrix"] if VALIDATE_AND_RENDER else []) + (["loom"] if LOOM_BABYLON else []) + \
         (["stress"] if STRESS else []) + (["traces"] if TRACES else []) + ["findings"]
cfg = hog.HogConfig(stages=stages, versions=["4.0", "4.1"] if X3D_41 else ["4.0"], consortium=CONSORTIUM, chrome_gpu=CHROME_GPU,
                    max_items=MAX_NODES, stress_preset=STRESS_PRESET, name=NAME,
                    trace_sizes=[int(x) for x in TRACE_SIZES.replace(" ", "").split(",") if x])
if CONSORTIUM and lab.upstream_client is None:
    lab.start_upstream(8001)
print("stages:", ", ".join(stages))

# %% [markdown]
# ## 5 · Run the hog
#
# Progress streams to the panel under cell 1 and to the dashboard. Each stage writes its results as it finishes, so a stopped run still leaves a report.

# %%
#@title 5 · Run it
run = lab.hog(cfg)
hog.show(run, lab)

# %% [markdown]
# **Reading it.**
#
# - **Findings** come first, grouped by project. Each says how many scenes show it and quotes the evidence; the scene files are in the run folder under `scenes/`.
# - The **matrix** has one row per scene: ✓/✗ per engine, plus how much of the frame X_ITE and Cycles covered (0 = drew nothing; event and sound nodes draw nothing by design).
# - **export valid** is the new Blender check: is the file Blender wrote valid X3D of the version it claims? A ✗ there is a bug report for x3d-blender-io with the exact file attached.
# - The **probe** lines say whether Chrome and the OptiX denoiser could use the GPU. If not, X_ITE and trace timings are CPU timings, and the stress test's denoiser falls back to OpenImageDenoise.

# %% [markdown]
# ## 6 · Issue drafts
#
# One Markdown file per project in `issues/`, ready to paste into a GitHub issue (review it first: a finding is a measurement, and some will be spec questions rather than bugs).

# %%
#@title 6 · Show the issue drafts { display-mode: "form" }
from x3dlab import ui
for f in sorted((run.dir / "issues").glob("*.md")):
    ui.show("<details><summary style='font:14px system-ui'><b>%s</b></summary><pre style='white-space:pre-wrap;font-size:12px'>%s</pre></details>"
            % (ui.esc(f.name), ui.esc(f.read_text())))
print("run folder:", run.dir)

# %% [markdown]
# ## 7 · Keep everything
#
# Zips the run folder (report, cards, findings, issue drafts, every scene and Blender export, traces) for download, then stops the lab. Save the notebook to GitHub too (*File › Save a copy in GitHub*); no key or token is in it.

# %%
#@title 7 · Zip the run and stop { display-mode: "form" }
import shutil
z = shutil.make_archive(str(run.dir), "zip", run.dir)
print("zip:", z, "(%.1f MB)" % (os.path.getsize(z) / 1e6))
try:
    from google.colab import files
    files.download(z)
except Exception:
    pass
lab.down()
