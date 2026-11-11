# %% [markdown]
# # 08 · The Gobbler, cinematic: Unreal rounds toward the most photoreal Gobbler
#
# The Gobbler's look lives in GLSL `ComposedShader`s, which glTF and Unreal cannot carry. This notebook gets it there in four moves, each measured:
#
# 1. **Bake** (`x3due/gobbler.py`): the shader-encoded geometry decoded at the frozen moment (phase 1, time 0) into real geometry. Ribbons become tubes, points become spheres, facets stay faces, all with emissive/PBR materials. The bake also keeps the shader's data channels in TEXCOORDs for later custom materials. A **CPU reference** draws the same decode the way the GLSL does; it matches X_ITE at SSIM 0.91–1.0, so it is the yardstick.
# 2. **Import** one or more bake variants (`studio`, `neon`, `gilded`) into Unreal. The shader's four point lights become real lights.
# 3. **Rounds.** Every *grade* (bloom, exposure, lens, depth of field, light scale) × every viewpoint renders in **one** Unreal start. Each still is scored against the reference (**SSIM**, **lit F** = which pixels light up, **Δ** colour) and laid out on a contact sheet. The next round tries the neighbours of the look you pick: brighter or darker, more or less bloom, stronger or weaker lights. The *fidelity* grade gets its exposure fitted to the reference automatically.
# 4. **Hero:** the chosen look at full resolution and samples, saved to Drive with its exact settings.
# 5. **Everywhere** (section 9): X_ITE's look baked into one glb and rendered by Unreal (extreme: 8K, 256 samples, Lumen at full quality), Blender Cycles and Babylon.js (WebGPU), side by side with the reference, with every engine's GPU use charted. Sections 5–7 can be skipped to go straight there.
#
# Scores go to `DRIVE_DIR/gobbler/scoreboard.jsonl` (one line per still: round, bake, grade, viewpoint, scores, GPU, settings), so rounds on different runtimes add up. **Fidelity** asks how close Unreal gets to the shader; **cinematic** is your eye on the contact sheet.
#
# Needs the Unreal engine in your Drive, the same as notebook 07 (its section 5 explains the one manual download). If Unreal will not start, run 07's step **6c**.

# %%
#@title 1 · Options { display-mode: "form" }
DRIVE_DIR = "/content/drive/MyDrive/x3dlab/unreal"  #@param {type:"string"}
ENGINE_SOURCE = "auto"  #@param ["auto", "cache", "zip"]
ENGINE_VERSION = "5.4"  #@param ["5.4", "5.8", "newest"] {allow-input: true}
BAKES = "studio, neon"  #@param {type:"string"}
#@markdown bake variants (re-import each): `studio` (as the shader), `neon` (thinner, hotter ribbons; darker, shinier gold), `gilded` (mirror gold)
ROUND_VIEWPOINTS = "EyeToEye, Flywheel"  #@param {type:"string"}
#@markdown the Gobbler's viewpoints: Wide, Colossus, EyeToEye, Inside, Flywheel, Crown, Probe
ROUND_GRADES = "fidelity, glow, film, macro, warm"  #@param {type:"string"}
ROUND_RES = "1280x720"  #@param ["960x540", "1280x720", "1920x1080"]
ROUND_SAMPLES = 16  #@param {type:"integer"}
HERO_VIEWPOINTS = "EyeToEye, Flywheel, Colossus, Crown"  #@param {type:"string"}
HERO_RES = "3840x2160"  #@param ["1920x1080", "3840x2160", "7680x4320"]
HERO_SAMPLES = 64  #@param {type:"integer"}
VERBOSE = True  #@param {type:"boolean"}
VISUAL = True  #@param {type:"boolean"}
HEARTBEAT_S = 20  #@param {type:"slider", min:5, max:120, step:5}
DRIVE_HEARTBEAT = True  #@param {type:"boolean"}
GENTLE = True  #@param {type:"boolean"}
#@markdown **VERBOSE** streams Unreal's important log lines and a heartbeat while it runs; **VISUAL** shows every still as it lands and a VRAM/GPU chart per Unreal job. **DRIVE_HEARTBEAT** writes the run's status to `DRIVE_DIR/heartbeat/` every minute (data out only). **GENTLE** keeps 4 cores free and Unreal at low priority, so Colab does not recycle the VM.
LAB_REPO = "hagopjay/x3d.lab"
LAB_REF = "main"
def _res(s):
    w, h = (int(x) for x in s.lower().split("x"))
    return w, h
def _names(s):
    return [x.strip() for x in s.split(",") if x.strip()]
print("✅ options: bakes %s · round %s at %s, %d samples · hero %s at %s, %d samples" % (
    _names(BAKES), _names(ROUND_GRADES), ROUND_RES, ROUND_SAMPLES, _names(HERO_VIEWPOINTS), HERO_RES, HERO_SAMPLES))

# %%
#@title 2 · Get the helpers, mount Drive, start the heartbeat { display-mode: "form" }
import importlib, json, os, subprocess, sys, time
from pathlib import Path
from IPython.display import HTML, Image as IPImage, display

def sh(cmd, timeout=1800):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()

def git(args, timeout=1800):
    """git with a read-only token from Colab Secrets (GITHUB_TOKEN) sent as a header for this repo only; the token never
    goes into a URL, a file or the output."""
    env = dict(os.environ)
    try:
        from google.colab import userdata
        token = userdata.get("GITHUB_TOKEN")
    except Exception:
        token = os.environ.get("GITHUB_TOKEN")
    if token:
        import base64
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/%s.extraheader" % LAB_REPO,
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode())
    r = subprocess.run(["git"] + args, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

LAB_DIR = Path("/content/x3d.lab") if Path("/content").is_dir() else Path.home() / "x3d.lab"
if os.environ.get("X3DLAB_DEV") and Path(os.environ["X3DLAB_DEV"]).is_dir():
    LAB_DIR = Path(os.environ["X3DLAB_DEV"])
elif not (LAB_DIR / ".git").exists():
    rc, msg = git(["clone", "-q", "--depth", "1", "--branch", LAB_REF, "https://github.com/%s" % LAB_REPO, str(LAB_DIR)])
    if rc:
        raise SystemExit("%s\ncould not clone github.com/%s (private? add a read-only GITHUB_TOKEN Colab secret)" % (msg, LAB_REPO))
else:
    rc, msg = git(["-C", str(LAB_DIR), "pull", "-q", "--ff-only"])
    print(msg or "helpers up to date")
UNREAL_DIR = LAB_DIR / "notebooks" / "unreal"
sys.path.insert(0, str(UNREAL_DIR))
importlib.invalidate_caches()
from x3due import bringup, gobbler, heartbeat, pipeline, project, showcase, x3dscene
for _m in (gobbler, project, pipeline, bringup):
    importlib.reload(_m)
WORK = Path("/content/x3due") if Path("/content").is_dir() else Path.home() / "x3due"
STAGE = "setup"
if Path("/content").is_dir():                      # Colab: Drive holds the engine, the caches and the scoreboard
    from google.colab import drive
    drive.mount("/content/drive")
    if not pipeline.drive_mounted(DRIVE_DIR):
        raise SystemExit("❌ Google Drive did not mount (or %s is missing): run this cell again and sign in with the account "
                         "that owns the engine files" % Path(DRIVE_DIR).parent)
GOB_DRIVE = Path(DRIVE_DIR) / "gobbler"
if Path(DRIVE_DIR).parent.exists():
    GOB_DRIVE.mkdir(parents=True, exist_ok=True)
if DRIVE_HEARTBEAT and Path(DRIVE_DIR).parent.exists() and not (globals().get("HB_DRIVE") and HB_DRIVE.alive()):
    HB_DRIVE = heartbeat.Heartbeat(DRIVE_DIR, WORK / "out", context=lambda: globals(), beat_s=60).start()
    print("💓 Drive heartbeat →", HB_DRIVE.dir)
CARD = {"notebook": "08_Gobbler_Cinematic", "lab_commit": sh("git -C %s rev-parse --short HEAD" % LAB_DIR), "rounds": []}
print("✅ helpers %s @ %s · work %s · Drive %s" % (LAB_REPO, CARD["lab_commit"], WORK, GOB_DRIVE))

# %% [markdown]
# ## 3 · Unreal up: GPU, Vulkan, engine, shader cache, first start
#
# Notebook 07's steps 3–6 in one call. The first start on a new runtime compiles Unreal's global shaders on the CPU (about 15–20 minutes; the GPU sits idle meanwhile). It restores the compiled shaders from Drive first and saves them back after a long start, so the next runtime skips most of it.

# %%
#@title 3 · Bring Unreal up { display-mode: "form" }
STAGE = "bring-up"
UP = bringup.up(WORK, DRIVE_DIR, ENGINE_SOURCE, ENGINE_VERSION, verbose=VERBOSE, visual=VISUAL, every=HEARTBEAT_S, gentle_mode=GENTLE)
LAB, PLAN, ENGINE, HW = UP.get("lab"), UP.get("plan"), UP.get("engine") or {}, UP.get("hw") or {}
SMOKE = {"cold": UP["smoke"]} if UP.get("smoke") else {}
CARD.update(hardware=HW, engine={k: v for k, v in ENGINE.items() if k != "extract"}, bringup={k: UP.get(k) for k in ("ok", "why", "steps", "seconds")})
if UP["ok"]:
    print("\n✅ Unreal %s draws on the %s · %s" % (ENGINE.get("version"), HW.get("kind"), UP["smoke"]["proof"].get("why")))
else:
    print("\n❌ " + str(UP["why"]))
    if UP.get("crash_stack"):
        print("crash stack:\n" + UP["crash_stack"][:1500])

# %% [markdown]
# ## 4 · Bake the Gobbler and draw the references
#
# The scene comes from x3d.demo at the pinned commit. The **reference** stills are the CPU port of the GLSL at the round resolution: what each Unreal still is scored against.

# %%
#@title 4 · Decode, bake, reference stills { display-mode: "form" }
STAGE = "bake"
SRC = showcase.fetch("gobbler3", WORK / "scenes")
GOB_X3D = Path(SRC["main"])
BAKE = gobbler.decode(GOB_X3D)
print("\n".join(gobbler.summary(BAKE)))
X3D_MANIFEST = x3dscene.manifest(GOB_X3D.read_text(encoding="utf-8", errors="replace"))
RW, RH = _res(ROUND_RES)
REFS, REF_PNG = {}, {}
(WORK / "out" / "gobbler" / "ref").mkdir(parents=True, exist_ok=True)
for vp in dict.fromkeys(_names(ROUND_VIEWPOINTS) + _names(HERO_VIEWPOINTS)):
    if vp not in BAKE["viewpoints"]:
        print("⚠️ no viewpoint %s (have %s)" % (vp, ", ".join(BAKE["viewpoints"])))
        continue
    t0 = time.time()
    REFS[vp] = gobbler.preview(BAKE, vp, RW, RH)
    REF_PNG[vp] = gobbler.to_png(REFS[vp], WORK / "out" / "gobbler" / "ref" / ("ref_%s_%dx%d.png" % (vp, RW, RH)))
    print("reference %-10s %.1f s" % (vp, time.time() - t0))
if VISUAL:
    for vp in _names(ROUND_VIEWPOINTS):
        if vp in REF_PNG:
            display(IPImage(filename=str(REF_PNG[vp]), width=640))

# %% [markdown]
# ## 5 · Into Unreal: one level per bake variant
#
# Each variant is written as a glb (Khronos-valid) and imported by Interchange into its own level. The Gobbler's box (without the sky sphere and the parked pick marker), one centre per baked part for the axis check, and the shader's four point lights go in with it.

# %%
#@title 5 · Bake variants → glb → Unreal { display-mode: "form" }
STAGE = "import"
IMPORTED, GLB = {}, {}
for b in _names(BAKES):
    v = gobbler.BAKES.get(b)
    if v is None:
        print("⚠️ unknown bake %s (have %s)" % (b, ", ".join(gobbler.BAKES)))
        continue
    d = WORK / "out" / "gobbler" / "bakes"
    d.mkdir(parents=True, exist_ok=True)
    GLB[b] = gobbler.write_glb(BAKE, d / ("gobbler3_%s.glb" % b), core_scale=v["core_scale"], gain=v["gain"], tube_sides=v["tube_sides"],
                               style=v["style"])
    print("%-7s glb %.1f MB · %d vertices · %d triangles" % (b, GLB[b]["bytes"] / 2**20, GLB[b]["vertices"], GLB[b]["triangles"]))
    if not (LAB and UP["ok"]):
        continue
    man = gobbler.manifest_for(BAKE, X3D_MANIFEST)
    res = pipeline.import_scene(LAB, "gob_" + b, GLB[b]["path"], "gltf", man, shape_centres=man["shape_centres"])
    rep = res["report"]
    IMPORTED[b] = rep
    print("        import %s · %d cameras · %d lights · axes %s · %.0f s" % ("ok" if rep.get("ok") else "FAILED", len(rep.get("cameras_placed", [])),
          len(rep.get("lights_placed", [])), (rep.get("axes") or {}).get("axes"), res["seconds"]))
    if not rep.get("ok"):
        print(res.get("excerpt", "")[-2500:])
CARD["bakes"] = {b: {"glb": {k: g[k] for k in ("bytes", "vertices", "triangles")}, "import_ok": (IMPORTED.get(b) or {}).get("ok")} for b, g in GLB.items()}

# %% [markdown]
# ## 6 · A round: every grade × every viewpoint, scored
#
# Run this cell again for the next round. Round 1 renders the preset grades (`gobbler.GRADES`). After it, **PICK** a look from the contact sheet (or leave it empty: the best by `PICK_BY`), and the next round renders it with its six neighbours. The *fidelity* grade's exposure is fitted to the reference after every round.

# %%
#@title 6 · Round { display-mode: "form" }
PICK = ""  #@param {type:"string"}
#@markdown e.g. `glow` or `glow_r2_bright`; `neon:glow` picks the bake too. Empty = the cinematic look with the best PICK_BY (fidelity always rides along).
PICK_BY = "ssim"  #@param ["ssim", "lit_f", "colour_delta"]
STAGE = "round"
if "ROUND" not in globals():
    ROUND, EV, SCORES, HISTORY = 0, {}, {}, []
    CANDIDATES = {g: gobbler.GRADES[g] for g in _names(ROUND_GRADES) if g in gobbler.GRADES}
    BAKES_ROUND = [b for b in _names(BAKES) if (IMPORTED.get(b) or {}).get("ok")]
elif any(k[0] == ROUND for k in SCORES):
    # choose from the last round, then make its neighbours the next round's candidates
    last = {k: v for k, v in SCORES.items() if k[0] == ROUND}
    def mean(bake, grade, key):
        xs = [s[key] for (r, b, g, vp), s in last.items() if b == bake and g == grade]
        return sum(xs) / len(xs) if xs else None
    combos = sorted({(b, g) for (r, b, g, vp) in last})
    if PICK:
        pb, _, pg = PICK.rpartition(":")
        pick = (pb or next((b for b, g in combos if g == pg), combos[0][0]), pg)
    else:                                   # the cinematic look closest to the shader; fidelity rides along anyway
        sign = -1 if PICK_BY == "colour_delta" else 1
        looks = [c for c in combos if c[1] != "fidelity"] or combos
        pick = max(looks, key=lambda c: sign * (mean(*c, PICK_BY) or -1e9))
    print("picked %s:%s (%s %.3f)" % (pick[0], pick[1], PICK_BY, mean(*pick, PICK_BY) or 0))
    CARD.setdefault("picks", []).append({"round": ROUND, "bake": pick[0], "grade": pick[1]})
    base = CANDIDATES.get(pick[1]) or gobbler.GRADES[pick[1].split("_")[0]]
    CANDIDATES = {pick[1]: base, "fidelity": gobbler.GRADES["fidelity"]}
    for n, (g, e) in gobbler.neighbours(pick[1].split("_")[0] + "_r%d" % (ROUND + 1), base, EV.get(pick[1], 0.0)).items():
        CANDIDATES[n], EV[n] = g, e
    BAKES_ROUND = [pick[0]]
ROUND += 1
cams = [vp for vp in _names(ROUND_VIEWPOINTS) if vp in REFS]
rung = {"resolution": [RW, RH], "spatial_samples": int(ROUND_SAMPLES), "temporal_samples": 1, "renderer": "lumen", "tiles": 1,
        "warmup_frames": 16, "texture_streaming": "fully_load", "cvars": dict(PLAN["ladder"]["high"]["cvars"])}
grades = {n: gobbler.with_exposure(g, EV.get(n, 0.0)) for n, g in CANDIDATES.items()}
sb = GOB_DRIVE / "scoreboard.jsonl"
rows_sheet, scores_sheet = {}, {}
for b in BAKES_ROUND:
    print("round %d · bake %s · %d grades × %d viewpoints in one Unreal start" % (ROUND, b, len(grades), len(cams)))
    out = pipeline.render_grades(LAB, "gob_" + b, cams, "round", rung, grades, label="r%d" % ROUND)
    if not out["stills"] or not any(out["stills"].values()):
        print("❌ no stills: %s\n%s" % (out["proof"].get("why"), out.get("excerpt", "")[-2000:]))
        continue
    for (gname, vp), png in out["stills"].items():
        if not png:
            continue
        test = gobbler.resize_like(png, REFS[vp].shape)
        s = gobbler.score(test, REFS[vp])
        s["ev_fit"] = round(gobbler.exposure_fit(test, REFS[vp]), 2)
        SCORES[(ROUND, b, gname, vp)] = s
        rows_sheet.setdefault("%s · %s" % (vp, b), {"reference": str(REF_PNG[vp])})[gname] = png
        scores_sheet[("%s · %s" % (vp, b), gname)] = s
        line = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "round": ROUND, "bake": b, "grade": gname, "viewpoint": vp,
                "scores": s, "gpu": HW.get("kind"), "engine": ENGINE.get("version"), "res": [RW, RH], "samples": ROUND_SAMPLES,
                "grade_settings": grades[gname], "bake_settings": gobbler.BAKES[b], "png": png}
        HISTORY.append(line)
        if GOB_DRIVE.exists():
            with open(sb, "a") as f:
                f.write(json.dumps(line, default=str) + "\n")

fid = [SCORES[k]["ev_fit"] for k in SCORES if k[0] == ROUND and k[2] == "fidelity"]   # one fit per round: all bakes averaged
if fid:
    EV["fidelity"] = round(EV.get("fidelity", 0.0) + sum(fid) / len(fid), 2)
    print("fidelity exposure fitted to the reference: %+.2f EV" % EV["fidelity"])
cols = ["reference"] + list(grades)
sheet = gobbler.contact_sheet(rows_sheet, cols, WORK / "out" / "gobbler" / ("round_%02d.png" % ROUND), cell_w=400, scores=scores_sheet)
display(IPImage(filename=sheet))
print("%-28s %-8s %-10s %6s %6s %6s %6s" % ("grade", "bake", "viewpoint", "SSIM", "litF", "Δ", "EVfit"))
for (r, b, g, vp), s in sorted(SCORES.items()):
    if r == ROUND:
        print("%-28s %-8s %-10s %6.3f %6.3f %6.1f %+6.2f" % (g, b, vp, s["ssim"], s["lit_f"], s["colour_delta"], s["ev_fit"]))
CARD["rounds"].append({"round": ROUND, "bakes": BAKES_ROUND, "grades": grades, "sheet": sheet,
                       "scores": {"%s|%s|%s" % k[1:]: v for k, v in SCORES.items() if k[0] == ROUND}})
if GOB_DRIVE.exists():
    import shutil
    shutil.copy(sheet, GOB_DRIVE / Path(sheet).name)
print("\nnext: set PICK (or leave empty for the best %s) and run this cell again, or go to the hero render" % PICK_BY)

# %% [markdown]
# ## 7 · Hero render
#
# The chosen look (the last PICK, or the best of the last round) at `HERO_RES` with `HERO_SAMPLES` samples, every hero viewpoint, saved to `DRIVE_DIR/gobbler/hero/` with the exact grade and bake.

# %%
#@title 7 · Hero { display-mode: "form" }
HERO_GRADE = ""  #@param {type:"string"}
#@markdown empty = the look the next round would start from (PICK, or the best of the last round)
STAGE = "hero"
last = {k: v for k, v in SCORES.items() if k[0] == globals().get("ROUND")} if "SCORES" in globals() else {}
CANDIDATES, EV = globals().get("CANDIDATES") or {}, globals().get("EV") or {}
BAKES_ROUND = globals().get("BAKES_ROUND") or _names(BAKES)
if HERO_GRADE:
    hb, _, hg = HERO_GRADE.rpartition(":")
    hero = (hb or BAKES_ROUND[0], hg)
elif PICK:
    pb, _, pg = PICK.rpartition(":")
    hero = (pb or BAKES_ROUND[0], pg)
elif last:
    combos = sorted({(b, g) for (r, b, g, vp) in last if g != "fidelity"}) or sorted({(b, g) for (r, b, g, vp) in last})
    hero = max(combos, key=lambda c: sum(s["ssim"] for (r, b, g, vp), s in last.items() if (b, g) == c))
else:
    hero = (_names(BAKES)[0], "glow")
g = gobbler.with_exposure(CANDIDATES.get(hero[1]) or gobbler.GRADES[hero[1].split("_")[0]], EV.get(hero[1], 0.0))
HW_, HH_ = _res(HERO_RES)
rung = {"resolution": [HW_, HH_], "spatial_samples": int(HERO_SAMPLES), "temporal_samples": 1, "renderer": "lumen", "tiles": 1,
        "warmup_frames": 64, "texture_streaming": "fully_load", "cvars": dict(PLAN["ladder"]["high"]["cvars"], **{"r.ScreenPercentage": 100})}
cams = [vp for vp in _names(HERO_VIEWPOINTS) if vp in BAKE["viewpoints"]]
print("hero: bake %s · grade %s · %dx%d · %d samples · %s" % (hero[0], hero[1], HW_, HH_, HERO_SAMPLES, ", ".join(cams)))
HERO = pipeline.render_grades(LAB, "gob_" + hero[0], cams, "hero", rung, {hero[1]: g}, label="hero")
if GOB_DRIVE.exists():
    import shutil
    dst = GOB_DRIVE / "hero" / time.strftime("%Y%m%d-%H%M%S")
    dst.mkdir(parents=True, exist_ok=True)
    for (gname, vp), png in HERO["stills"].items():
        if png:
            shutil.copy(png, dst / ("gobbler_%s_%s_%s_%dx%d.png" % (vp, hero[0], gname, HW_, HH_)))
    (dst / "look.json").write_text(json.dumps({"bake": hero[0], "bake_settings": gobbler.BAKES[hero[0]], "grade": hero[1],
                                                "grade_settings": g, "res": [HW_, HH_], "samples": HERO_SAMPLES, "gpu": HW.get("kind"),
                                                "engine": ENGINE.get("version")}, indent=1, default=str))
    print("saved to", dst)
for (gname, vp), png in HERO["stills"].items():
    if png and not (LAB.live and png in LAB.live.shown):
        display(IPImage(filename=png, width=960))
CARD["hero"] = {"bake": hero[0], "grade": hero[1], "settings": g, "stills": {vp: p for (gn, vp), p in HERO["stills"].items()}}

# %% [markdown]
# ## 9 · The Gobbler everywhere: X_ITE's look in Unreal, Blender and Babylon.js
#
# The rounds above use a bake that keeps the shaders' *data* (for custom materials later), so every engine draws flat placeholder materials. This section bakes what X_ITE's shaders actually *draw* into ordinary textures (`gobbler.write_glb_xite`):
#
# - the FacetShader's colour for every facet;
# - the EyeShader's iris and rings;
# - the LineShader and PointShader core colours;
# - a near-black sky and faint membranes.
#
# One glb, no custom shader, in three renderers, side by side with the reference:
#
# - **Unreal**, *extreme*: 8K by default, 256 samples, Lumen at its highest quality, bloom. A quick 720p still first fits the exposure to the reference.
# - **Blender Cycles** on the GPU, AgX colour, with bloom added in post.
# - **Babylon.js** in headless Chrome on the GPU: WebGPU when Chrome offers it, WebGL2 otherwise. Glow layer on the ribbons and stars, bloom, ACES, 8× MSAA, and the exposure fitted to the reference. It ends with a stress loop that shows the GPU at full frame rate.
#
# Every engine's GPU use is sampled while it renders: one chart and one table at the end.
#
# Sandbox check at 480×270, CPU only, against the reference: Cycles scored SSIM 0.75–0.77 and lit F 0.93–0.98; Babylon (WebGL2 on SwiftShader) scored SSIM 0.70–0.73 and lit F 0.94–0.98. The flat placeholder bake in Unreal had scored SSIM 0.26–0.46.

# %%
#@title 9a · Bake X_ITE's look into one glb { display-mode: "form" }
EVERY_VIEWS = "EyeToEye, Flywheel, Colossus, Crown"  #@param {type:"string"}
STAGE = "everywhere-bake"
EV_DIR = WORK / "out" / "gobbler" / "everywhere"
EV_DIR.mkdir(parents=True, exist_ok=True)
from x3due import everywhere
importlib.reload(everywhere)
XGLB = gobbler.write_glb_xite(BAKE, EV_DIR / "gobbler3_xite.glb")
print("glb %.1f MB · %d triangles · textures %s · facets %d, ribbons %d, points %d" % (
    XGLB["bytes"] / 2**20, XGLB["triangles"], XGLB["textures"], XGLB["facets"], XGLB["ribbons"], XGLB["points"]))
EVIEWS = [v for v in _names(EVERY_VIEWS) if v in BAKE["viewpoints"]]
for vp in EVIEWS:
    if vp not in REFS:
        REFS[vp] = gobbler.preview(BAKE, vp, RW, RH)
        REF_PNG[vp] = gobbler.to_png(REFS[vp], WORK / "out" / "gobbler" / "ref" / ("ref_%s_%dx%d.png" % (vp, RW, RH)))
VMAP = everywhere.views(BAKE, EVIEWS)
EVERY = {"views": EVIEWS, "engines": {}}

# %%
#@title 9b · Unreal, extreme { display-mode: "form" }
UNREAL_RES = "7680x4320"  #@param ["1920x1080", "3840x2160", "7680x4320"]
UNREAL_SAMPLES = 256  #@param {type:"integer"}
STAGE = "everywhere-unreal"
if LAB and UP["ok"]:
    man = gobbler.manifest_for(BAKE, X3D_MANIFEST)
    imp = pipeline.import_scene(LAB, "gob_xite", XGLB["path"], "gltf", man, shape_centres=man["shape_centres"])
    print("import %s · %.0f s" % ("ok" if imp["report"].get("ok") else "FAILED", imp["seconds"]))
    base_cvars = dict(PLAN["ladder"]["high"]["cvars"])
    calib_rung = {"resolution": [RW, RH], "spatial_samples": 8, "temporal_samples": 1, "renderer": "lumen", "tiles": 1, "warmup_frames": 16,
                  "texture_streaming": "fully_load", "cvars": base_cvars}
    c = pipeline.render_grades(LAB, "gob_xite", EVIEWS[:2], "calib", calib_rung, {"extreme": gobbler.with_exposure(gobbler.GRADES["extreme"], 0.0)},
                               label="calib")
    fits = [gobbler.exposure_fit(gobbler.resize_like(png, REFS[vp].shape), REFS[vp]) for (g, vp), png in c["stills"].items() if png]
    UE_EV = round(sum(fits) / len(fits), 2) if fits else 0.0
    print("Unreal exposure fitted to the reference: %+.2f EV" % UE_EV)
    UW, UH = _res(UNREAL_RES)
    xrung = {"resolution": [UW, UH], "spatial_samples": int(UNREAL_SAMPLES), "temporal_samples": 1, "renderer": "lumen", "tiles": 1,
             "warmup_frames": 64, "texture_streaming": "fully_load", "cvars": base_cvars}
    UE_X = pipeline.render_grades(LAB, "gob_xite", EVIEWS, "extreme", xrung, {"extreme": gobbler.with_exposure(gobbler.GRADES["extreme"], UE_EV)},
                                  label="extreme")
    EVERY["engines"]["Unreal"] = {"stills": {vp: png for (g, vp), png in UE_X["stills"].items()}, "seconds": UE_X["seconds"],
                                  "gpu": UE_X.get("gpu") or {}, "gpu_series": UE_X.get("gpu_series") or [],
                                  "detail": "UE %s · Lumen · %dx%d · %d spp · EV %+.2f" % (ENGINE.get("version"), UW, UH, UNREAL_SAMPLES, UE_EV)}
    g = UE_X.get("gpu") or {}
    print("Unreal extreme: %d stills in %.0f s · GPU peak %s%%, mean %s%% · VRAM peak %.1f GB" % (
        sum(1 for p in UE_X["stills"].values() if p), UE_X["seconds"], g.get("peak_util", "?"), g.get("mean_util", "?"), (g.get("peak_mem_mb") or 0) / 1024))
else:
    print("Unreal is not up (step 3): skipped")

# %%
#@title 9c · Blender Cycles { display-mode: "form" }
CYCLES_RES = "3840x2160"  #@param ["1920x1080", "3840x2160"]
CYCLES_SAMPLES = 512  #@param {type:"integer"}
STAGE = "everywhere-cycles"
try:
    from x3dlab import blender as xblender
    if "BL" not in globals() or BL is None:
        BL = xblender.setup()
    print("Blender %s · Cycles backend %s" % (BL.version, BL.best_backend))
    CW, CH = _res(CYCLES_RES)
    CY = everywhere.cycles(BL.cmd, XGLB["path"], VMAP, EV_DIR / "cycles", res=(CW, CH), samples=int(CYCLES_SAMPLES),
                           device="CPU" if BL.best_backend == "CPU" else "GPU", backend=BL.best_backend if BL.best_backend != "CPU" else None,
                           lights=everywhere.studio_lights(BAKE))
    print("Cycles on %s: %d stills in %.0f s · GPU peak %s%%, mean %s%%" % (CY["report"].get("device"), sum(1 for p in CY["stills"].values() if p),
          CY["seconds"], CY["gpu"].get("peak_util", "?"), CY["gpu"].get("mean_util", "?")))
    for e in CY["report"].get("errors", [])[:3]:
        print("  note:", e)
    EVERY["engines"]["Cycles"] = {"stills": CY["stills"], "seconds": CY["seconds"], "gpu": CY["gpu"], "gpu_series": CY["gpu_series"],
                                  "detail": "Blender %s · %s · %dx%d · %d spp · AgX + bloom" % (CY["report"].get("blender"), CY["report"].get("device"),
                                                                                             CW, CH, CYCLES_SAMPLES)}
    if VISUAL:
        for vp, png in list(CY["stills"].items())[:2]:
            if png:
                display(IPImage(filename=png, width=720))
except Exception as exc:
    print("❌ Cycles: %s" % exc)

# %%
#@title 9d · Babylon.js (WebGPU) { display-mode: "form" }
BABYLON_RES = "3840x2160"  #@param ["1920x1080", "3840x2160"]
STRESS_FRAMES = 600  #@param {type:"integer"}
STAGE = "everywhere-babylon"
try:
    BW, BH = _res(BABYLON_RES)
    BJ = everywhere.babylon(XGLB["path"], VMAP, EV_DIR / "babylon", res=(BW, BH), lights=everywhere.studio_lights(BAKE),
                            stress_frames=int(STRESS_FRAMES), references={vp: REFS[vp] for vp in EVIEWS[:2]}, vendor_dir=WORK / "vendor" / "babylon")
    info = BJ["info"]
    print("Babylon on %s · %s · %d stills in %.0f s · GPU peak %s%%, mean %s%%%s" % (
        info.get("kind"), info.get("adapter") or info.get("renderer"), len(BJ["stills"]), BJ["seconds"], BJ["gpu"].get("peak_util", "?"),
        BJ["gpu"].get("mean_util", "?"), " · stress %.0f fps at %dx%d" % (BJ["stress"]["fps"], BW, BH) if BJ.get("stress") else ""))
    for c_ in BJ["console"][:3]:
        print("  console:", c_)
    EVERY["engines"]["Babylon"] = {"stills": BJ["stills"], "seconds": BJ["seconds"], "gpu": BJ["gpu"], "gpu_series": BJ["gpu_series"],
                                   "detail": "Babylon.js %s · %s · %dx%d · glow, bloom, ACES, MSAA 8" % (everywhere.BABYLON_VERSION, info.get("kind"), BW, BH)}
    if VISUAL:
        for vp, png in list(BJ["stills"].items())[:2]:
            display(IPImage(filename=png, width=720))
except Exception as exc:
    print("❌ Babylon: %s" % exc)

# %%
#@title 9e · Side by side, scores, GPU { display-mode: "form" }
STAGE = "everywhere-compare"
cols = ["X_ITE look (reference)"] + list(EVERY["engines"])
rows, scores = {}, {}
for vp in EVIEWS:
    rows[vp] = {"X_ITE look (reference)": str(REF_PNG[vp])}
    for eng, e in EVERY["engines"].items():
        png = e["stills"].get(vp)
        rows[vp][eng] = png
        if png:
            scores[(vp, eng)] = gobbler.score(gobbler.resize_like(png, REFS[vp].shape), REFS[vp])
EV_SHEET = gobbler.contact_sheet(rows, cols, EV_DIR / "everywhere.png", cell_w=480, scores=scores)
display(IPImage(filename=EV_SHEET))
chart = everywhere.gpu_chart({k: e["gpu_series"] for k, e in EVERY["engines"].items()}, EV_DIR / "gpu.png")
if chart:
    display(IPImage(filename=chart))
lines = ["| engine | how | time | GPU peak | GPU mean | VRAM peak | SSIM | lit F | Δ |", "|---|---|---|---|---|---|---|---|---|"]
for eng, e in EVERY["engines"].items():
    sc = [scores[(vp, eng)] for vp in EVIEWS if (vp, eng) in scores]
    m = lambda k: sum(x[k] for x in sc) / len(sc) if sc else float("nan")
    g = e["gpu"] or {}
    lines.append("| %s | %s | %.0f s | %s%% | %s%% | %.1f GB | %.3f | %.3f | %.0f |" % (
        eng, e["detail"], e["seconds"], g.get("peak_util", "?"), g.get("mean_util", "?"), (g.get("peak_mem_mb") or 0) / 1024, m("ssim"), m("lit_f"),
        m("colour_delta")))
display(HTML("<div style='font-size:13px'>" + "<br>".join(lines).replace("|", " | ") + "</div>"))
print("\n".join(lines))
CARD["everywhere"] = {"glb": {k: v for k, v in XGLB.items() if k != "eye_box"}, "engines": {k: {x: e[x] for x in ("detail", "seconds", "gpu")}
                      for k, e in EVERY["engines"].items()}, "scores": {"%s|%s" % k: v for k, v in scores.items()}}
if GOB_DRIVE.exists():
    import shutil
    dst = GOB_DRIVE / "everywhere" / time.strftime("%Y%m%d-%H%M%S")
    dst.mkdir(parents=True, exist_ok=True)
    for f in [EV_SHEET, chart, XGLB["path"]] + [p for e in EVERY["engines"].values() for p in e["stills"].values() if p]:
        if f and Path(f).exists():
            shutil.copy(f, dst / Path(f).name)
    (dst / "everywhere.json").write_text(json.dumps(CARD["everywhere"], indent=1, default=str))
    print("saved to", dst)

# %% [markdown]
# ## 10 · Save the run

# %%
#@title 10 · Run card and shader cache to Drive { display-mode: "form" }
STAGE = "save"
RUN_ID = time.strftime("%Y%m%d-%H%M%S")
CARD.update(run_id=RUN_ID, attempts=LAB.attempts if LAB else [], ev=EV)
p = WORK / "out" / ("gobbler_run_%s.json" % RUN_ID)
p.write_text(json.dumps(CARD, indent=1, default=str))
if GOB_DRIVE.exists():
    import shutil
    shutil.copy(p, GOB_DRIVE / p.name)
    if LAB and ENGINE.get("version"):
        print("shader cache:", pipeline.ddc_save(LAB, DRIVE_DIR, ENGINE.get("version")))
print("run card:", p)
