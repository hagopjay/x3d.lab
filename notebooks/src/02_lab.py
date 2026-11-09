# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/02_Lab.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 02 Lab: X3D ↔ Blender on a GPU, stress-tested
#
# **What survives when an X3D scene goes into Blender and back out again, how fast Cycles renders it on an L4 or a G4, and how does the validator hold up under load?** This notebook measures all three and writes a run card you can compare later.
#
# | | |
# |---|---|
# | **Runtime** | *Runtime › Change runtime type* › **L4** or **G4** (RTX PRO 6000 Blackwell, 96 GB). A CPU runtime works too, with the render ladders cut down automatically. |
# | **Blender** | the `bpy` 5.2 LTS wheel when Colab's Python is 3.13 (no download of the app), otherwise the Blender 5.2 LTS tarball; plus the [x3d-blender-io](https://github.com/hagopjay/x3d-blender-io) add-on at a pinned commit |
# | **Time** | `smoke` about 1 minute · `standard` 3–6 minutes on an L4 · `heavy` 20 minutes or more |
#
# The tiers, each one switchable with its own settings:
#
# | Tier | What it does | A row is a warning when |
# |---|---|---|
# | **validate** | every generated scene (6 families × sizes) through x3d_mcp | — (failure if a generated scene is invalid) |
# | **roundtrip** | X3D → Blender → X3D 3.3 / 4.0 / 4.1 → Blender, compared object by object: transforms, bounding boxes, vertex positions, material colours, light energy and direction, camera field of view, mesh sharing. The exported file is validated by x3d_mcp too | anything changed beyond tolerance, or Blender wrote invalid X3D |
# | **drift** | the same round trip N generations deep | a quantity keeps moving from generation to generation |
# | **render** | Cycles ladders over samples, resolution and geometry, on the best GPU backend (OptiX › CUDA › CPU), optionally also on CPU for a speed-up figure; peak VRAM from `nvidia-smi` | a frame comes out blank, or a GPU render silently fell back to CPU |
# | **load** | concurrent MCP sessions validating at once; latency percentiles | — (failure on any error) |
#
# Everything streams to the live panel under cell 1 and to the dashboard (which has a **Lab run** panel with a GPU graph and a render gallery). In Chrome DevTools' Performance panel, Blender work appears on its own **Blender** track.

# %%
#@title 1 · Start the lab (server, dashboard, optional tunnel) { display-mode: "form" }
TUNNEL = "none"  #@param ["none", "cloudflared", "ngrok"]
LOG_LEVEL = "INFO"  #@param ["DEBUG", "INFO", "WARN", "ERROR"]
LOG_DENSITY = "compact"  #@param ["compact", "rich", "firehose"]
LAB_REPO = "hagopjay/x3d.lab"
LAB_REF = "main"

import importlib.util, os, subprocess, sys
if not os.environ.get("X3DLAB_DEV") and (importlib.util.find_spec("x3dlab") is None or os.environ.get("X3DLAB_REINSTALL")):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "x3dlab[notebook,tunnels] @ git+https://github.com/%s@%s" % (LAB_REPO, LAB_REF)], check=True)
import x3dlab
from x3dlab import labui, stress

lab = x3dlab.up(tunnel=TUNNEL, log_level=LOG_LEVEL, log_density=LOG_DENSITY)

# %% [markdown]
# ## 2 · Blender and the GPU
#
# Installs Blender (about 400 MB, once per session), checks out the add-on at its pinned commit, and asks Cycles which GPU backends it can see. `best` is what the render tier uses unless you pick one. On an NVIDIA runtime expect **OPTIX** (RT cores) or **CUDA**.

# %%
#@title 2 · Set up Blender { display-mode: "form" }
BLENDER_SOURCE = "auto"  #@param ["auto", "wheel", "tarball"]
bl = lab.setup_blender(BLENDER_SOURCE)

rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (b, ", ".join(v) if isinstance(v, list) and v else (v or "none"))
               for b, v in bl.info["backends"].items())
x3dlab.ui.show("<div style='font:14px system-ui'><b>Blender %s</b> (Python %s) · add-on %s · best Cycles backend: <b>%s</b> · "
               "%d GPU render slot(s)</div><table style='font-size:12px'><tr><th align=left>backend</th><th align=left>devices</th></tr>%s</table>"
               % (bl.version, bl.info["python"], x3dlab.pins.summary()["x3d_blender_io"], bl.best_backend, bl.gpu._value, rows))
print(lab.runtime_info["line"])

# %% [markdown]
# ## 3 · Design the run
#
# Pick a preset; it is scaled to the GPU this runtime has (a CPU runtime gets much smaller render ladders). Then change anything: which tiers run, scene families and sizes, X3D versions, drift depth, the render ladders, the load. The **JSON** tab shows the whole configuration: copy it to rerun exactly this test later, or paste one back in. The seed fixes every generated scene, so the same JSON and seed produce the same inputs.

# %%
#@title 3 · Control panel { display-mode: "form" }
PRESET = "standard"  #@param ["smoke", "standard", "heavy"]
SEED = 7  #@param {type:"integer"}
panel = labui.panel(stress.RunConfig.preset_for(PRESET, seed=SEED))

# %% [markdown]
# ## 4 · Run it
#
# Rows arrive live in the panel under cell 1 and on the dashboard. The table below is the full result; the run card (JSON) and an HTML report with every render are linked from it.

# %%
#@title 4 · Run the stress test
cfg = panel.config()
print("run:", cfg.run_name(), "| tiers:", ", ".join(t for t in stress.TIERS if cfg.tiers[t]["on"]))
run = lab.stress(cfg)
labui.show_run(run, lab)

# %% [markdown]
# ## 5 · Read the results
#
# **Drift** plots each quantity relative to generation 0 (dashed lines are the X3D 3.3 path). A flat line at 1.0 means nothing is lost however often the scene goes round. **Render ladders** show where time goes: samples should scale linearly, resolution with pixel count, geometry hardly at all until it no longer fits. The gallery shows the latest frames with their render time and peak VRAM.

# %%
#@title 5 · Drift, render ladders and gallery
labui.plot_drift(run)
labui.plot_render(run)
labui.gallery(run)

# %% [markdown]
# ## 6 · Your scene, rendered in Cycles
#
# The Quickstart loop (a model writes X3D, x3d_mcp checks and repairs it) and then Blender renders the result on the GPU, from the scene's own Viewpoint. Without an API key it uses a built-in scene. Add a key with `x3dlab.keys.widget()`.

# %%
#@title 6 · Prompt → X3D → Cycles { display-mode: "form" }
PROMPT = "A row of five glass-like spheres on a dark reflective floor, lit by a warm spot light and a cool rim light."  #@param {type:"string"}
PROVIDER = "auto"  #@param ["auto", "anthropic", "openai", "gemini", "sample"]
SAMPLES = 256  #@param {type:"integer"}
RESOLUTION = "1920x1080"  #@param ["960x540", "1280x720", "1920x1080", "3840x2160"]

scene = lab.scene(PROMPT, provider=PROVIDER)
print("scene:", "valid" if scene.valid else "not valid", "·", scene.provider, scene.model, "·", "%.1f s" % scene.seconds)
w, h = map(int, RESOLUTION.split("x"))
shot = lab.render(scene.xml, samples=SAMPLES, resolution=(w, h))
if shot["ok"]:
    import base64
    x3dlab.ui.show("<img src='data:image/png;base64,%s' style='max-width:100%%;border-radius:8px'><div style='font:12px system-ui'>"
                   "%s · %d spp · %dx%d · %.1f s%s · camera: %s</div>" % (base64.b64encode(open(shot["path"], "rb").read()).decode(),
                   shot["backend"], shot["samples"], w, h, shot["seconds"],
                   (" · %.0f MB VRAM peak" % shot["gpu"]["peak_mem_mb"]) if shot.get("gpu") else "", shot["camera"]))
else:
    print("render failed:", shot.get("error"), *shot["log"][-5:], sep="\n")

# %% [markdown]
# ## 7 · Compare two runs
#
# Every run writes a run card. Compare the last two (or any two paths): status flips, new or missing cases, and timings that moved by more than half. Change a pin, a preset or the runtime, rerun cell 4, and run this cell.

# %%
#@title 7 · Compare run cards
import glob
cards = sorted(glob.glob(str(x3dlab.runtime.home() / "out" / "*" / "run_card.json")), key=os.path.getmtime)
if len(cards) >= 2:
    labui.compare(cards[-2], cards[-1])
else:
    print("one run card so far:", cards, "\nrun cell 4 again (maybe with another preset or seed) to compare")

# %% [markdown]
# ## 8 · Stop
#
# Stops the server, the gateway and any tunnel. Run cards and renders stay in the lab folder until the Colab session ends: download what you want to keep.

# %%
#@title 8 · Stop everything
print(lab.status()["processes"])
# lab.down()
