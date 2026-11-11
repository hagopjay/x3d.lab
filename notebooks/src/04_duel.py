# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/04_Model_Duel.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 04 Model Duel
#
# **Which model writes the best X3D, and what does each one get wrong?** Every contender gets the same prompts. The **x3d_mcp** validator is the referee: it checks every scene, gives every error a *kind*, tries its own exact fixes, and then lets the model repair from the validator's report. The result is a scorecard and an **error profile** per model.
#
# | | You will |
# |---|---|
# | **1** | start the x3d_mcp server and the dashboard |
# | **2** | add API keys: **NVIDIA** ([build.nvidia.com](https://build.nvidia.com), free credits), Claude, OpenAI or Gemini. The `sample` contender needs no key |
# | **3** | pick contenders and prompts, and see the worst-case request count **before** anything is spent |
# | **4** | run the duel and read the scorecard, the error profile and the prompt-by-prompt grid |
# | **5** | open any scene: the model's draft, what the validator said, and the final version in 3D |
#
# **Runtime:** a CPU runtime is enough; the models run on their providers' servers. **Cost:** each scene is one model request, plus one per repair round it needs. Step 3 prints the worst case, and the run refuses to start above `MAX_REQUESTS`.
#
# **Keys:** store them once in Colab **Secrets** (the key icon in the left bar) as `NVIDIA_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY`, and allow this notebook to read them. Never paste a key into a code cell: cells get saved to GitHub.

# %% [markdown]
# ## 1 · Start the lab
#
# The same server and dashboard as the other notebooks. `TUNNEL = none` is right here: the duel talks to the server inside this machine, and only the models' providers are reached over the internet.

# %%
#@title 1 · Start x3d_mcp and the dashboard { display-mode: "form" }
TUNNEL = "none"  #@param ["none", "cloudflared", "ngrok"]
LOG_LEVEL = "WARN"  #@param ["DEBUG", "INFO", "WARN", "ERROR"]
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
                       "x3dlab[notebook,tunnels] @ git+https://github.com/%s@%s" % (LAB_REPO, LAB_REF)], env=env).returncode:
        raise SystemExit("x3dlab did not install from github.com/%s. If that repo is private, add a read-only GitHub token as the "
                         "Colab secret GITHUB_TOKEN (key icon, left bar) and allow this notebook to read it." % LAB_REPO)
import x3dlab
from x3dlab import duel, keys, llm

lab = x3dlab.up(tunnel=TUNNEL, log_level=LOG_LEVEL)

# %% [markdown]
# ## 2 · API keys
#
# **Load from Colab Secrets**, then **Test keys & list models**. A green tick shows how many chat models the key can use; the dropdown is the model a bare provider name (like `nvidia`) will use.
#
# **NVIDIA** serves many open models (Llama, Nemotron, Qwen, Mistral, ...) through one OpenAI-compatible endpoint. Its model names change over time, so the list comes live from NVIDIA. The free tier allows about 40 requests a minute; the lab paces NVIDIA requests to stay under that (set the environment variable `X3DLAB_NVIDIA_RPM` to change it, or `0` for no limit on a paid endpoint).

# %%
#@title 2 · API keys (NVIDIA, Claude, OpenAI, Gemini)
keys.widget()

# %% [markdown]
# Optional: the NVIDIA chat models your key can use, to copy an exact id into step 3 (`nvidia:<id>`).

# %%
#@title 2b · List NVIDIA models { display-mode: "form" }
FILTER = "instruct"  #@param {type:"string"}
if keys.get_key("nvidia") and not keys.models("nvidia"):
    keys.test("nvidia")
nv = keys.models("nvidia")
ids = [m.id for m in nv if FILTER.lower() in m.id.lower()]
print("%d NVIDIA chat models%s; default: %s" % (len(ids), (" matching %r" % FILTER) if FILTER else "", keys.model("nvidia")) if nv else
      "No NVIDIA key yet: add NVIDIA_API_KEY in step 2.")
print("\n".join(ids[:80]))

# %% [markdown]
# ## 3 · Pick the contenders
#
# A contender is a provider name (`nvidia`, `anthropic`, `openai`, `gemini`: the model chosen in step 2) or `provider:model` for an exact model, for example `nvidia:meta/llama-3.3-70b-instruct`. Add `sample` to see the pipeline without spending anything. Leave a box empty to skip it.
#
# | Setting | What it changes |
# |---|---|
# | `PROMPTS` | how many of the 12 built-in prompts to use. Each leans on one known trap (ROUTE types, DEF/USE, PhysicalMaterial, IndexedFaceSet, ...) |
# | `SYSTEM` | **coached** gives every model the Quickstart's rule list (lowercase booleans, colour ranges, ...); **bare** gives one line. Run both to see how much the rules help each model |
# | `ROUNDS` | model repair turns after the server's own fixes. 0 measures first drafts and server fixes only |
# | `PARALLEL` | scenes in flight at once. NVIDIA is paced anyway, so more mostly helps the other providers |
# | `MAX_REQUESTS` | the run refuses to start if the worst case is above this |

# %%
#@title 3 · Contenders and settings { display-mode: "form" }
CONTENDER_1 = "nvidia"  #@param {type:"string"}
CONTENDER_2 = "nvidia:meta/llama-3.3-70b-instruct"  #@param {type:"string"}
CONTENDER_3 = "anthropic"  #@param {type:"string"}
CONTENDER_4 = "sample"  #@param {type:"string"}
PROMPTS = 6  #@param {type:"slider", min:1, max:12, step:1}
SYSTEM = "coached"  #@param ["coached", "bare"]
ROUNDS = 1  #@param {type:"slider", min:0, max:3, step:1}
X3D_VERSION = "4.0"  #@param ["4.0", "4.1", "3.3"]
PARALLEL = 4  #@param {type:"slider", min:1, max:16, step:1}
MAX_REQUESTS = 120  #@param {type:"integer"}
NAME = ""  #@param {type:"string"}

specs = [c.strip() for c in (CONTENDER_1, CONTENDER_2, CONTENDER_3, CONTENDER_4) if c.strip()]
cfg = duel.DuelConfig(contenders=specs, prompts=PROMPTS, system=SYSTEM, rounds=ROUNDS, version=X3D_VERSION,
                      parallel=PARALLEL, max_requests=MAX_REQUESTS, name=NAME)
ready, skipped = duel.resolve(cfg)
cfg.contenders = [c.spec for c in ready]
est = duel.estimate(cfg)
print("Contenders that will run:")
for c in ready:
    print("  ✓ %s" % c.label)
for spec, why in skipped.items():
    print("  ✗ %s: %s" % (spec, why))
print("\nPrompts:", ", ".join(p["id"] for p in cfg.prompt_list()))
print("Model requests: %d if every draft is valid, up to %d if every scene uses every repair round (limit %d)%s"
      % (est["requests_best"], est["requests_worst"], MAX_REQUESTS, "" if est["within_budget"] else "  ← above the limit"))
if est["nvidia_requests_worst"]:
    print("NVIDIA: up to %d requests, about %.1f min at the paced rate" % (est["nvidia_requests_worst"], est["nvidia_minutes_at_limit"]))

# %% [markdown]
# ## 4 · Run the duel
#
# Progress streams to the panel under cell 1 and to the dashboard. Each scene goes **draft → check → server fixes → model repair**, and the table records where it first became valid.

# %%
#@title 4 · Run it
run = lab.duel(cfg)
duel.show(run, lab)

# %% [markdown]
# **Reading it.**
#
# - **valid as written** is the model on its own. **+ server fixes** adds x3d_mcp's deterministic `repair_x3d` (no model, no cost). **+ model repair** adds the model fixing its own scene from the validator's report.
# - The **error profile** counts the errors in first drafts by kind. Kinds come from the validator's own messages, so `boolean-case` means `True`/`False` in every column, whichever model wrote it. Hover a kind for its meaning.
# - In the **prompt grid**, ✓ is valid as written, **S** valid after the server's fixes, **M** after the model's repair, ✗ never. Hover a cell for the error kinds of its draft.
# - A model with few draft errors but many ✗ cannot use the validator's feedback; one with many draft errors but mostly **M** can. Both are worth knowing before you wire a model into a pipeline.
# - "Valid" is not "good": a valid scene can still be empty, dark or ugly. Step 5 shows the scenes.

# %% [markdown]
# ## 5 · Look at one scene
#
# Pick a contender and a prompt: the draft's verdict, then the final scene in 3D.

# %%
#@title 5 · Open a scene { display-mode: "form" }
CONTENDER = 1  #@param {type:"integer"}
PROMPT = "orrery"  #@param ["lantern", "orrery", "pbr", "lights", "text", "pyramid", "colour-wheel", "staircase", "traffic-light", "terrain", "touch", "gallery"]

from x3dlab import ui
c = run.contenders[max(1, min(CONTENDER, len(run.contenders))) - 1]
a = next((x for x in run.attempts if x.contender == c.label and x.prompt_id == PROMPT), None)
if a is None:
    print("%s did not run prompt %r: this duel used %s" % (c.label, PROMPT, ", ".join(p["id"] for p in run.prompts)))
else:
    print("%s · %s · valid at: %s · %d request(s) · %.1f s" % (c.label, PROMPT, a.stage, a.requests, a.seconds))
    print("draft errors:", ", ".join("%s (%s)" % (k, duel.kind_title(k)) for k in a.draft_kinds) or "none")
    if a.error:
        print("error:", a.error)
    if a.files.get("draft"):
        draft = (run.dir / a.files["draft"]).read_text()
        ui.show(ui.verdict_html(lab.check(draft).to_dict()) if "<X3D" in draft else "<pre>%s</pre>" % ui.esc(draft[:1500]))
    if a.files.get("final"):
        final = (run.dir / a.files["final"]).read_text()
        if "<X3D" in final:
            ui.show(ui.xite_viewer_html(final, height=320))

# %% [markdown]
# ## 6 · Keep the result
#
# The duel card (JSON) has every scene's verdict, error kinds, requests, tokens and timing; `report.html` is the page from step 4. Both are under `out/` in the lab's folder (on Colab `/content/x3dlab/out`) and linked under the table. Save the notebook to GitHub (*File › Save a copy in GitHub*) to keep the outputs; keys never appear in them.

# %%
#@title 6 · Where the files are { display-mode: "form" }
print("card:  ", run.card_path)
print("report:", run.dir / "report.html")
print("scenes:", run.dir / "scenes")

# %% [markdown]
# ## 7 · Stop
#
# Stops the server, the gateway and any tunnel. The files stay.

# %%
#@title 7 · Stop everything
lab.down()
