# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/06_Foundry.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 06 Foundry
#
# **Model-written scenes in, tests for the engines that disagree about them out.** The hog (05) runs one fixed corpus through every engine once. The Foundry runs in **rounds**, and every round starts from what the earlier ones learned:
#
# | Step | What happens | Who it helps |
# |---|---|---|
# | **target** | picks nodes nobody knows much about yet: never tried, new in 4.1, not yet bridged in x3d.loom, failed by the models, or disputed last round | all |
# | **generate** | NVIDIA models (or Claude, OpenAI, Gemini) write one scene per target node; x3d_mcp's exact fixes, then one repair turn | the models |
# | **referee** | three judges that share no code: **x3d_mcp** (the fork), **lxml + the version's XSD**, and the **X3D object model** (UOM) where the XSD is known to be stricter | x3d_mcp, the XSDs |
# | **engines** | **X_ITE** (the reference), the **x3d.loom** Babylon bridge (picture against X_ITE's picture), **Blender** (import, X3D round trip, is the export valid?) and **glTF** (Blender's export, the Khronos validator, X_ITE draws the glTF under the original camera) | X_ITE, x3d.loom, x3d-blender-io |
# | **claims** | every disagreement becomes a claim that can be checked again: *"x3d_mcp rejects a scene the XSD accepts"*, *"the bridge draws the right shapes in the wrong colours"* | |
# | **shrink** | each new claim is cut down to the smallest scene for which it is still true (delta debugging, re-checking with the engine involved) | whoever fixes it |
# | **write** | **x3d.loom fixtures** pinned to X_ITE's own pixels, **x3d_mcp tests** (strict xfail), **issue drafts**, all in one kit zip | |
# | **learn** | each model's mistakes become rules in its next system prompt; the scoreboard shows whether they helped | the next round |
#
# **Runtime: economy first.** Nothing here needs an A100: the models run at NVIDIA, the validators and Blender's import/export run on the CPU, Chrome uses whatever GPU there is. A **CPU runtime** or a **T4** is enough; keep the A100 for the Cycles numbers in 02/05. A round of 8 nodes × 2 models takes about 10–20 minutes, most of it the bridge runner and shrinking.
#
# **Keys:** in Colab **Secrets** (the key icon on the left) as `NVIDIA_API_KEY` (and optionally `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`). Never paste a key into a cell. The `corpus` contender needs no key: it answers with x3d.loom's own fixtures (some with a planted defect), so you can watch a whole round for free.

# %% [markdown]
# ## 1 · Start the lab

# %%
#@title 1 · Start x3d_mcp and the dashboard { display-mode: "form" }
VERBOSE = True  #@param {type:"boolean"}
VISUAL = True  #@param {type:"boolean"}
LOG_LEVEL = "INFO" if VERBOSE else "WARN"   # VERBOSE streams every stage as it runs; off: warnings only
LAB_REPO = "hagopjay/x3d.lab"
LAB_REF = "main"

import importlib.util, os, subprocess, sys
if not os.environ.get("X3DLAB_DEV") and (importlib.util.find_spec("x3dlab") is None or os.environ.get("X3DLAB_REINSTALL")):
    env = dict(os.environ)
    try:                       # a private repo: a read-only token from Colab Secrets, sent as a git header (never printed)
        from google.colab import userdata
        token = userdata.get("GITHUB_TOKEN")
    except Exception:
        token = os.environ.get("GITHUB_TOKEN")
    if token:
        import base64
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/%s.extraheader" % LAB_REPO,
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode())
    if subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                       "x3dlab[notebook,tunnels,research] @ git+https://github.com/%s@%s" % (LAB_REPO, LAB_REF)], env=env).returncode:
        raise SystemExit("x3dlab did not install from github.com/%s. If that repo is private, add a read-only GitHub token as the "
                         "Colab secret GITHUB_TOKEN (key icon, left bar) and allow this notebook to read it." % LAB_REPO)
import random
import x3dlab
from x3dlab import foundry, hog, keys

lab = x3dlab.up(tunnel="none", log_level=LOG_LEVEL)
print(lab.runtime_info["line"])

# %% [markdown]
# ## 2 · API keys
#
# **Load from Colab Secrets**, then **Test keys & list models**. A bare `nvidia` contender uses the model picked in the dropdown; `nvidia:<org/model>` names one exactly. The free NVIDIA tier allows about 40 requests a minute and the lab paces itself under that.

# %%
#@title 2 · API keys (NVIDIA, Claude, OpenAI, Gemini)
keys.widget()

# %% [markdown]
# ## 3 · Blender (for the Blender and glTF engines)
#
# Only import, export and conversion run here: no Cycles unless you tick `CYCLES` in step 4, so this costs CPU seconds, not GPU minutes. Skip this cell and untick `BLENDER` and `GLTF` to leave Blender out.

# %%
#@title 3 · Set up Blender { display-mode: "form" }
BLENDER_SOURCE = "auto"  #@param ["auto", "wheel", "tarball"]
bl = lab.setup_blender(BLENDER_SOURCE)
print("Blender %s · Cycles backend if asked for: %s" % (bl.version, bl.best_backend))
from x3dlab import toolchain
print("toolchain:", toolchain.line(toolchain.report(lab)))   # what will really run, and where each piece came from

# %% [markdown]
# ## 4 · The round
#
# | Setting | What it changes |
# |---|---|
# | `CONTENDER_*` | `nvidia`, `nvidia:<org/model>`, `anthropic`, `openai`, `gemini`, or `corpus` (free, offline). Empty = skip |
# | `TARGETS` / `NODES` | how many nodes to aim at, or a comma list of exact nodes (then `TARGETS` is ignored) |
# | `REPAIR_ROUNDS` | model repair turns after x3d_mcp's exact fixes. The referee judges the **draft** too, so 0 still finds validator disagreements |
# | engines | `XITE` is the reference the others are compared with; `LOOM` runs the bridge's own test runner; `BLENDER` and `GLTF` need step 3 |
# | `MAX_SHRINKS`, `SHRINK_BUDGET` | how many new claims to shrink, and how many re-checks each may use. Validator claims re-check in milliseconds; bridge and glTF claims in seconds |
# | `MAX_REQUESTS` | the round refuses to start if the worst case needs more model requests than this |
# | `STATE` | where the memory between rounds lives. Point it at Google Drive to carry rules and known claims across sessions |

# %%
#@title 4 · Settings { display-mode: "form" }
CONTENDER_1 = "nvidia"  #@param {type:"string"}
CONTENDER_2 = "nvidia:meta/llama-3.3-70b-instruct"  #@param {type:"string"}
CONTENDER_3 = ""  #@param {type:"string"}
TARGETS = 8  #@param {type:"slider", min:1, max:24, step:1}
NODES = ""  #@param {type:"string"}
X3D_VERSION = "4.0"  #@param ["4.0", "4.1"]
REPAIR_ROUNDS = 1  #@param {type:"slider", min:0, max:3, step:1}
XITE = True  #@param {type:"boolean"}
LOOM = True  #@param {type:"boolean"}
BLENDER = True  #@param {type:"boolean"}
GLTF = True  #@param {type:"boolean"}
CYCLES = False  #@param {type:"boolean"}
MAX_SHRINKS = 6  #@param {type:"slider", min:0, max:20, step:1}
SHRINK_BUDGET = 40  #@param {type:"slider", min:10, max:150, step:10}
MAX_REQUESTS = 80  #@param {type:"integer"}
CHROME_GPU = True  #@param {type:"boolean"}
STATE = ""  #@param {type:"string"}

engines = [e for e, on in (("xite", XITE), ("loom", LOOM), ("blender", BLENDER), ("gltf", GLTF)) if on]
cfg = foundry.FoundryConfig(contenders=[c.strip() for c in (CONTENDER_1, CONTENDER_2, CONTENDER_3) if c.strip()],
                            targets=TARGETS, nodes=[n.strip() for n in NODES.split(",") if n.strip()], version=X3D_VERSION,
                            repair_rounds=REPAIR_ROUNDS, engines=engines, cycles=CYCLES, max_shrinks=MAX_SHRINKS,
                            shrink_budget=SHRINK_BUDGET, max_requests=MAX_REQUESTS, chrome_gpu=CHROME_GPU, state=STATE)
if ("blender" in engines or "gltf" in engines) and lab.blender is None:
    print("⚠️  BLENDER/GLTF need step 3; they are left out for now.")
    cfg.engines = [e for e in engines if e not in ("blender", "gltf")]
from x3dlab import duel
ready, skipped = duel.resolve(duel.DuelConfig(contenders=cfg.contenders))
cfg.contenders = [c.spec for c in ready]
for c in ready:
    print("  ✓ %s" % c.label)
for spec, why in skipped.items():
    print("  ✗ %s: %s" % (spec, why))
est = foundry.estimate(cfg)
print("model requests: up to %d (limit %d)%s" % (est["requests_worst"], cfg.max_requests, "" if est["within_budget"] else "  ← above the limit"))

st = foundry.load_state(foundry.state_path(cfg))
n_next = len(st["rounds"]) + 1
loom_dir = hog.loom_checkout(cfg.loom_ref, cfg.loom_dir)
targets = foundry.choose_targets(cfg, st, foundry.loom_status(loom_dir), random.Random(cfg.seed or n_next))
print("\nround %d will aim at:" % n_next)
for t in targets:
    print("  %-28s %s  %-5s %s" % (t["node"], t.get("component", ""), t["version"], t["why"]))
if st["rules"]:
    print("\nrules carried over:", {k: len(v) for k, v in st["rules"].items()})

# %% [markdown]
# ## 5 · Run rounds
#
# Each round writes its report, card and kit as it goes (a stopped round still leaves files). Run this cell again for the next round: the targets move on, the rules carry over, and claims already reported with their smallest scene are only counted.

# %%
#@title 5 · Run { display-mode: "form" }
ROUNDS = 1  #@param {type:"slider", min:1, max:5, step:1}
for i in range(ROUNDS):
    rnd = foundry.run(lab, cfg)
    if VISUAL:
        foundry.show(rnd, lab)
    else:
        print(foundry.card(rnd))

# %% [markdown]
# **Reading a round.**
#
# - **Scoreboard:** per contender, how many drafts were valid as written, after fixes, by the XSD; whether the model actually **used the node** it was asked for (a valid scene without it tests nothing); **sound** scenes are the ones every engine is expected to handle. **Δ vs last** is the draft-valid rate against the previous round of the same contender: the rules at work.
# - **Claims:** **NEW** ones are shrunk and written up; **known** ones were reported in an earlier round (only counted now); **expected** ones are explained by a known limit (glTF has no text; the bridge has not started a node; Blender's importer skips a node) and are listed so you can see they were noticed; **same as above** shrank to the same scene as another claim: one problem seen from several nodes.
# - **Gallery:** X_ITE · the bridge · the glTF trip drawn by X_ITE, all on the magenta key background, so a missing shape or a wrong colour is plain at a glance. The verdicts use silhouettes that forgive 2 px (lines and edges are never pixel-identical), and colour only when the shapes agree.
# - **Rules for the next round:** taken only from real mistakes. When the XSD and the object model accept a draft the fork rejected, the fork was wrong and nothing is learned from it.

# %% [markdown]
# ## 6 · The kit
#
# Everything written for the projects, laid out like the repositories:
#
# - `x3d.loom/x3d.webgpu/bridge/fixtures/foundry/*.x3d|.test.json`: bridge fixtures. Their pixels are X_ITE's, taken at flat spots (no edges) and kept only after a second run showed X_ITE reproduces them. `foundry.status` is **agrees** (a new regression test) or **ours-fails** (a target for the bridge). The bridge's default run reads `fixtures/*.test.json` only, so these never turn its CI red; run one with `node tools/run-tests.js --babylon <babylon.js> foundry/<Name>`.
# - `x3d_mcp/tests/test_foundry_r<N>_*.py`: one strict-xfail test per validator claim, checked here against the fork's own `validate_xml` (a case that does not reproduce there is dropped). It passes while the bug is there; the day it is fixed it fails as XPASS: drop the marker and keep the test.
# - `issues/*.md`: one draft per new claim with the smallest scene, every judge's word, and how it was found. Read before filing: a claim says engines disagree; the one in the title is the one the others point at.

# %%
#@title 6 · Show the issue drafts and the kit { display-mode: "form" }
from x3dlab import ui
for f in sorted((rnd.dir / "kit" / "issues").glob("*.md")):
    ui.show("<details><summary style='font:14px system-ui'><b>%s</b></summary><pre style='white-space:pre-wrap;font-size:12px'>%s</pre></details>"
            % (ui.esc(f.name), ui.esc(f.read_text())))
for a in rnd.artifacts:
    print("%-14s %s %s" % (a["kind"], a.get("path") or "", a.get("status") or a.get("note") or ""))
print("kit zip:", rnd.dir / rnd.env["kit"] if rnd.env.get("kit") else "nothing written this round")

# %% [markdown]
# ## 7 · The other direction: glTF → X3D, and the reference stills
#
# The rounds above go **X3D → everything else**. This section goes back: Khronos's own **glTF sample models** (32 small ones, each built to test one glTF feature: strips and fans, alpha modes, unlit materials, texture transforms, cameras, lights, instancing, skins…) go into X3D **two independent ways**:
#
# | Converter | How |
# |---|---|
# | **X_ITE** | opens the glTF itself and writes it back as X3D (`createX3DFromURL` + `toXMLString`) |
# | **Blender + x3d-blender-io** | Blender's glTF importer, then the add-on's X3D 4.0 exporter; the same Blender scene also goes out as **USD**, checked by Pixar's compliance checker |
#
# Both X3D files are judged (x3d_mcp, lxml + XSD, the object model), and X_ITE draws them **under the same camera as the original glTF**. That gives **two converters and three pictures**:
# - X_ITE's X3D matches and Blender's doesn't: the loss is in Blender's leg.
# - Neither matches: X3D (or X_ITE) can't hold that feature yet.
#
# The **gap map** lists the node types each converter used: what X3D 4 can already express, and which tool is leaving it out.
#
# The same run takes the **reference stills**: 1280×720, on the magenta key and on grey, with a manifest per model (camera, bounds, X_ITE version, sample commit). The Unreal notebook compares against these instead of rendering its own (coordination doc, item 4). CPU is enough; about 5–10 minutes for all 32.

# %%
#@title 7 · Run the interchange { display-mode: "form" }
SAMPLES = ""  #@param {type:"string"}
BLENDER_TOO = True  #@param {type:"boolean"}
USD_TOO = True  #@param {type:"boolean"}
from x3dlab import interchange
ix = interchange.run(lab, samples=[s.strip() for s in SAMPLES.split(",") if s.strip()], blender=BLENDER_TOO and lab.blender is not None,
                     usd=USD_TOO, chrome_gpu=CHROME_GPU)
if VISUAL:
    interchange.show(ix, lab)                      # pictures side by side, the gap map, the claims
else:
    print(interchange.card(ix))
print("stills for other engines:", ix.dir / "stills" / "index.json")

# %% [markdown]
# ## 8 · Keep everything
#
# Zips the last round's folder (report, card, scenes, pictures, kit) with the state file, and the interchange folder (stills, manifests, converted files) if step 7 ran; then stops the lab. To chain rounds across Colab sessions, keep `state.json` (or set `STATE` in step 4 to a path on Google Drive).

# %%
#@title 8 · Zip and stop { display-mode: "form" }
import shutil
zips = []
if "rnd" in globals():
    shutil.copy(foundry.state_path(cfg), rnd.dir / "state.json")
    zips.append(shutil.make_archive(str(rnd.dir), "zip", rnd.dir))
if "ix" in globals():
    zips.append(shutil.make_archive(str(ix.dir), "zip", ix.dir))
for z in zips:
    print("zip:", z, "(%.1f MB)" % (os.path.getsize(z) / 1e6))
    try:
        from google.colab import files
        files.download(z)
    except Exception:
        pass
lab.down()
