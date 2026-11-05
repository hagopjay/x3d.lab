# %% [markdown]
# <a href="https://colab.research.google.com/github/jasonthurman-sys/x3d.lab/blob/main/notebooks/03_Research.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 03 Research: break the validators, compare the renderers, gate the regressions
#
# **Which X3D mistakes does each validator catch, which good files does it wrongly reject, does Blender draw the same picture as X_ITE, where do things fall over as scenes get big, and did anything get worse since last time?** This notebook answers each question with a measurement, and ends with a regression gate you can run in CI.
#
# | | |
# |---|---|
# | **Runtime** | **L4** or **G4** for the render and scale sections; everything else runs on a CPU runtime |
# | **Time** | about 10 minutes with the defaults on an L4; each section can be run on its own |
# | **Needs** | nothing private. Optional: the private Copilot build (a 5th validator), an API key for the agent in section 8 |
#
# | § | Question | How |
# |---|---|---|
# | 3 | Which defects does each validator miss, and which good files does it reject? | **differential fuzzing** from the X3D Unified Object Model: 27 mutation operators (20 break a rule, 7 keep the meaning), up to six oracles, findings grouped into buckets, each bucket **minimised** to a small reproducer |
# | 4 | Where do the validators disagree on known cases? | the **oracle matrix**: 17 break-it defects and 6 scene families through every validator |
# | 5 | Does Blender see the same scene as the browser? | **cross-renderer**: X_ITE and Cycles render the same Viewpoint; silhouette IoU and an overlay picture |
# | 6 | Where does each stage fall over? | **scale**: meshes up to millions of vertices through the validator (blob store), X_ITE, the Blender importer and Cycles |
# | 7 | What does the browser actually spend its time on? | **Chrome traces** with the lab's own bars on DevTools tracks, plus Chrome's performance metrics |
# | 8 | Can a model fix a scene *and prove it renders*? | **one model, two MCP servers**: x3d_mcp and Google's chrome-devtools-mcp |
# | 9 | Did anything get worse? | the **regression gate**: a committed baseline of every verdict, the same check GitHub Actions runs |
#
# The oracles: **fork** (hagopjay/x3d_mcp, the lab's server) · **consortium** (Web3DConsortium/x3d_mcp at a pinned commit) · **lxml-xsd** (the X3D 4.0 XSD in a second schema engine) · **x_ite** (the browser) · **copilot** (optional, private) · **blender** (the importer). A *finding* is a broken document an oracle accepted (**missed**), a good one it rejected (**false alarm**), or a crash. Browsers and importers are lenient by design, so their misses are recorded but not flagged.

# %%
#@title 1 · Start the lab { display-mode: "form" }
TUNNEL = "none"  #@param ["none", "cloudflared", "ngrok"]
LOG_LEVEL = "INFO"  #@param ["DEBUG", "INFO", "WARN", "ERROR"]
LOG_DENSITY = "compact"  #@param ["compact", "rich", "firehose"]
LAB_REPO = "jasonthurman-sys/x3d.lab"
LAB_REF = "main"

import importlib.util, os, subprocess, sys
if not os.environ.get("X3DLAB_DEV") and (importlib.util.find_spec("x3dlab") is None or os.environ.get("X3DLAB_REINSTALL")):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "x3dlab[notebook,tunnels,research] @ git+https://github.com/%s@%s" % (LAB_REPO, LAB_REF)], check=True)
import x3dlab
from x3dlab import agent, fuzz, regress, research, researchui as rui, stress

# X_ITE from the pinned npm tarball (not the CDN): the headless browser and the agent's Chrome load it from the lab.
lab = x3dlab.up(tunnel=TUNNEL, log_level=LOG_LEVEL, log_density=LOG_DENSITY, xite="npm")

# %% [markdown]
# ## 2 · The oracles
#
# Each switch starts one more oracle. The **consortium** server runs on 127.0.0.1:8001 next to the fork (never exposed through the tunnel). The **headless browser** is Playwright's Chromium with WebGL in software (SwiftShader), loading the pinned X_ITE; it is the `x_ite` oracle, the cross-renderer's left eye and the trace recorder. **Blender** is the importer oracle and the cross-renderer's right eye. **Copilot** needs the private build (cell 6 of the 01 notebook explains the sources); it runs inside the same headless browser.

# %%
#@title 2 · Start the oracles { display-mode: "form" }
CONSORTIUM = True  #@param {type:"boolean"}
BROWSER = True  #@param {type:"boolean"}
BLENDER = True  #@param {type:"boolean"}
COPILOT = False  #@param {type:"boolean"}
COPILOT_SOURCE = "drive"  #@param ["drive", "github", "upload", "path"]
COPILOT_PATH = ""  #@param {type:"string"}

if CONSORTIUM:
    lab.start_upstream(8001)
if COPILOT:
    lab.load_copilot(COPILOT_SOURCE, path=COPILOT_PATH or None)
if BROWSER:
    hb = lab.start_browser()
    print("browser:", hb.info)
bl = lab.setup_blender() if BLENDER else None
rui.oracle_table(lab, bl)

# %% [markdown]
# ## 3 · Differential fuzzing
#
# Every case is generated from the **X3DUOM** (the machine-readable X3D 4.0 object model): real node names, typed field values inside each field's range and enumeration, nodes only where a parent field accepts them, DEF before USE. Then one operator is applied:
#
# * **20 that break a rule** (expected verdict: reject): out-of-range numbers (from the UOM, and from 19775-1 bounds the UOM leaves out), bad enumerations, wrong tuple sizes, NaN/INF, unknown attributes and nodes, a node in a field that cannot hold it, wrong `containerField`, duplicate DEF, dangling USE, USE with extra fields, ROUTEs to missing fields or between mismatched types, event fields written as attributes, a node outside the declared profile, a bad version, an out-of-range face index, a truncated file.
# * **7 that keep the meaning** (expected: accept): shuffled attributes, comments, every default written out, other spellings of the same numbers (`1.0`, `5e-1`, `+0.25`), an extra Group, consistently renamed DEFs, line breaks inside tags.
#
# A case where an oracle's verdict differs from the expectation is a finding. Findings with the same (kind, oracle, operator, complaint) form a **bucket**; the first case of each bucket is **minimised** by hierarchical delta debugging: whole subtrees first, then attributes, keeping the defect and anything it refers to, until nothing more can go. Everything is deterministic for a seed.

# %%
#@title 3 · Fuzz { display-mode: "form" }
CASES = 200  #@param {type:"integer"}
SEED = 7  #@param {type:"integer"}
ORACLES = "fork, consortium, lxml-xsd, x_ite, copilot"  #@param {type:"string"}
MINIMIZE = True  #@param {type:"boolean"}
MIN_BUDGET = 60  #@param {type:"integer"}
CLEAN_SHARE = 0.2  #@param {type:"number"}
METAMORPHIC_SHARE = 0.25  #@param {type:"number"}
WILD = 0.25  #@param {type:"number"}

from x3dlab import scenegen, scenes
names = [o.strip() for o in ORACLES.split(",") if o.strip()]
orcs, skipped = research.oracles(lab, names, bl)
seeds = {"lantern": scenes.LANTERN, "pagoda": scenes.PAGODA, **{f: scenegen.make(f, 4, SEED) for f in ("primitives", "lights", "hierarchy")}}
cfg = fuzz.FuzzConfig(n=CASES, seed=SEED, seeds=seeds, minimize=MINIMIZE, min_budget=MIN_BUDGET, clean=CLEAN_SHARE,
                      metamorphic=METAMORPHIC_SHARE, wild=WILD)
fr = fuzz.run(cfg, orcs)
fuzz_dir = lab.out_dir / ("fuzz-%d" % SEED)
fr.write(fuzz_dir)
rui.show_fuzz(fr)
rui.operator_heatmap(fr)
print("cases, reproducers and report.html:", fuzz_dir, "| dashboard:", lab.dashboard_url() + "out/fuzz-%d/report.html" % SEED)

# %% [markdown]
# **Reading it.** *Recall* is the share of broken documents an oracle rejected; *specificity* the share of good ones it accepted; κ is Cohen's agreement between two oracles beyond chance. Open a bucket to see its minimised reproducer: paste it into the dashboard's *Validate* box, or ask the agent in section 8 to fix it. Findings this fuzzer made against the pinned versions are listed in `docs/ROADMAP.md`.

# %% [markdown]
# ## 4 · The oracle matrix
#
# Fuzzing samples the space; the matrix pins down known cases: the 17 break-it defects from the 01 notebook (each with the layer that should catch it) and one scene of every generator family. A red cell is an oracle doing something other than expected; the grey label says at which layer it complained. The XSD engine is not blamed for semantic defects (it cannot see them).

# %%
#@title 4 · Run the matrix { display-mode: "form" }
MATRIX_ORACLES = "fork, consortium, lxml-xsd, x_ite, copilot, blender"  #@param {type:"string"}
mcfg = stress.RunConfig.preset_for("research", seed=SEED)
mcfg.tiers = {"matrix": {"on": True, "oracles": [o.strip() for o in MATRIX_ORACLES.split(",") if o.strip()]}}
mrun = lab.research(mcfg)
rui.show_matrix(mrun)

# %% [markdown]
# ## 5 · Cross-renderer: X_ITE vs Cycles
#
# Both renderers draw the same scene from the scene's own **Viewpoint** at the same resolution. X_ITE gets a flat magenta background, Cycles a transparent film, so each picture reduces to a silhouette: which pixels show geometry. **IoU** (intersection over union) of the two silhouettes is 1.0 when they cover exactly the same pixels. A low score points at the importer: a wrong field of view, a lost transform, a missing or doubled mesh. Animated scenes (the lantern spins) differ a little because X_ITE has already started playing.

# %%
#@title 5 · Compare the renderers { display-mode: "form" }
SCENES = "lantern, pagoda, primitives:9, hierarchy:4, materials:9, lights:4"  #@param {type:"string"}
RESOLUTION = "480x270"  #@param ["320x180", "480x270", "960x540", "1920x1080"]
SAMPLES = 16  #@param {type:"integer"}
w, h = (int(x) for x in RESOLUTION.split("x"))
xcfg = stress.RunConfig.preset_for("research", seed=SEED)
xcfg.tiers = {"crossrender": {"on": True, "scenes": [s.strip() for s in SCENES.split(",") if s.strip()], "resolution": [w, h], "samples": SAMPLES}}
xrun = lab.research(xcfg)
labrows = [r for r in xrun.rows if r.tier == "crossrender"]
print("\n".join("%-4s %-14s %s" % (r.status, r.case, r.detail[:150]) for r in labrows))
rui.show_crossrender(xrun, lab)

# %% [markdown]
# ## 6 · Scale
#
# Tori meshes from 20 thousand to millions of vertices. Each size goes through the validator (above 3.5 MB through the server's blob store; the old notebook got HTTP 500 there), X_ITE (parse, first frames, JS heap), the Blender importer and a small Cycles render with peak GPU memory. The default ladder is cut for CPU runtimes.

# %%
#@title 6 · Scale ladder { display-mode: "form" }
VERTICES = "20000, 200000, 1000000"  #@param {type:"string"}
RENDER_AT_SCALE = True  #@param {type:"boolean"}
verts = [int(v) for v in VERTICES.replace("_", "").split(",") if v.strip()]
if lab.runtime_info.get("gpu_kind") in (None, "CPU"):
    verts = [v for v in verts if v <= 200000]
    print("CPU runtime: ladder cut to", verts)
scfg = stress.RunConfig.preset_for("research", seed=SEED)
scfg.tiers = {"scale": {"on": True, "vertices": verts, "render": RENDER_AT_SCALE},
              "traces": {"on": True, "screenshots": True}}            # section 7 reads these traces
srun = lab.research(scfg)
print("\n".join("%-4s %-36s %s" % (r.status, r.case, r.detail[:120]) for r in srun.rows if r.tier == "scale"))
rui.plot_scale(srun)

# %% [markdown]
# ## 7 · Chrome DevTools traces
#
# Section 6 also recorded a Chrome trace of X_ITE loading the pagoda and the two smallest meshes. Download a `trace.json` and load it in **Chrome DevTools › Performance › Load profile…**: the **x3d.lab** track group has `parse`, `replaceWorld` and `first frames` as named bars over X_ITE's own JavaScript and the GPU process. The dashboard does the same live for everything the lab does (open it, then DevTools › Performance › record).
#
# In the dashboard's DevTools console, `x3dlab.help()` lists the helpers (`x3dlab.events()`, `x3dlab.table()`, `x3dlab.validate(xml)`, …).

# %%
#@title 7 · Traces { display-mode: "form" }
DOWNLOAD_TRACES = False  #@param {type:"boolean"}
rui.show_traces(srun, lab)
if DOWNLOAD_TRACES:
    from google.colab import files
    for p in sorted(srun.dir.glob("trace_*.json")):
        files.download(str(p))

# %% [markdown]
# ## 8 · One model, two MCP servers
#
# The model gets the validator's tools (`x3d_*`) and a real Chrome (`devtools_*`, from Google's **chrome-devtools-mcp**, run with `npx` in its own headless Chrome: isolated profile, no usage statistics, no CrUX lookups). Its job: make the scene valid, open it in Chrome, read the console, record a performance trace, and report. It never sees the dashboard key: it works with `lab://view/<name>` addresses that the lab translates.
#
# `DEVTOOLS = "auto"` uses chrome-devtools-mcp when Node ≥ 20.19 is present (Colab has it) and otherwise a stand-in with the same tool names on the lab's own headless Chromium. Needs an API key: run the next cell, or put `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` in Colab Secrets.

# %%
#@title 8a · API keys
x3dlab.keys.widget()

# %%
#@title 8b · Run the agent { display-mode: "form" }
PROVIDER = "auto"  #@param ["auto", "anthropic", "openai", "gemini"]
MODEL = ""  #@param {type:"string"}
SCENE = "fuzz bucket"  #@param ["fuzz bucket", "break-it: route_types", "break-it: key_mismatch", "break-it: pbr_no_component", "lantern (already valid)"]
DEVTOOLS = "auto"  #@param ["auto", "npx", "playwright"]
MAX_CALLS = 24  #@param {type:"integer"}

from x3dlab import breakit
if SCENE == "fuzz bucket":
    pick = next((b for b in sorted(fr.buckets.values(), key=lambda b: -len(b.cases)) if b.repro and b.kind != "crash"), None)
    scene = pick.repro if pick else breakit.BY_ID["route_types"].apply(breakit.LANTERN)
    print("scene from bucket:", pick.key if pick else "(none: using a break-it defect)")
elif SCENE.startswith("break-it"):
    scene = breakit.BY_ID[SCENE.split(": ")[1]].apply(breakit.LANTERN)
else:
    scene = scenes.LANTERN
res = agent.run(lab, scene, provider=PROVIDER, model=MODEL or None, devtools=DEVTOOLS, max_calls=MAX_CALLS)
x3dlab.ui.show(agent.transcript_html(res))
if res.files.get("staged"):
    final = (res.final or {}).get("final_scene", "")
    name = final.rsplit("/", 1)[-1] if final.startswith("lab://view/") else next(iter(res.files["staged"]))
    print("open the final scene:", lab.dashboard_url() + "view?src=out/%s/%s.x3d" % (res.job, name))

# %% [markdown]
# ## 9 · Regression gate
#
# Runs the deterministic **ci** preset: validate, the matrix and 80 seeded fuzz cases with fork, lxml-xsd and consortium. Then it compares every verdict with the baseline committed in the package (`src/x3dlab/baselines/ci.json`). A verdict that got worse, a new finding bucket or a fall in recall/specificity is a regression; improvements are listed separately. GitHub Actions runs the same command on every push: `python -m x3dlab ci`.
#
# After a deliberate change (a pin moved, the fuzzer learned a new operator), review the report and accept the new behaviour with `python -m x3dlab ci --update` in a checkout, in its own commit.

# %%
#@title 9 · Run the gate { display-mode: "form" }
GATE_ORACLES = "fork, lxml-xsd, consortium"  #@param {type:"string"}
CHECK_TIMING = False  #@param {type:"boolean"}
import json as _json
gate_oracles = [o.strip() for o in GATE_ORACLES.split(",") if o.strip()]
if "consortium" in gate_oracles and lab.upstream_client is None:
    lab.start_upstream(8001)
crun, snap = regress.collect(lab, gate_oracles)
base = _json.loads(regress.DEFAULT_BASELINE.read_text())
findings = regress.compare(base, snap, timing=CHECK_TIMING)
rui.show_gate(regress.markdown(findings, base, snap))
(lab.out_dir / "ci_snapshot.json").write_text(_json.dumps(snap, indent=1, sort_keys=True))
print("snapshot:", lab.out_dir / "ci_snapshot.json")

# %% [markdown]
# ## 10 · Stop
#
# Stops the browser, both servers, the gateway and any tunnel. Run cards, fuzz reports, reproducers, renders and traces stay under `out/` in the lab's folder (on Colab: `/content/x3dlab/out`).

# %%
#@title 10 · Stop everything
lab.down()
