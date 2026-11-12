# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/unreal/07_Unreal_Fidelity.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 07 Unreal Fidelity: X3D scenes as Unreal Engine stills, on a Colab GPU, with no screen
#
# **Goal:** bring impressive X3D scenes into **Unreal Engine 5** conclusively and robustly, render the highest-fidelity stills this GPU allows, and *measure* what arrived and how it looks next to X_ITE and Blender Cycles. Streaming is not part of this notebook.
#
# 1. **knows its GPU**: model, VRAM, driver, CUDA, vCPUs, RAM, disk; looks up what the GPU can do (RT cores, video encoder) and then asks the **Vulkan device itself** whether it can ray trace;
# 2. **gives Unreal real Vulkan**: Colab ships NVIDIA's compute driver only; we unpack the graphics half at exactly the driver's version (the route confirmed on a Colab A100, 10 Nov 2026);
# 3. **gets the engine from *you***: Epic's licence means you download Unreal with your own Epic account (one time, see below); the notebook copies it from your Drive, trims it, and can cache it there for next time;
# 4. **proves from Unreal's own log** that it rendered with Vulkan on the NVIDIA GPU, not on NullRHI or a software rasterizer;
# 5. **imports** each scene: X3D → glTF by the lab's Blender leg (`x3dlab.formats`, owned by notebook 06 Foundry), glTF → Unreal by Interchange in a one-shot editor task, then **accounts** for it: meshes, triangles, materials, textures, lights, cameras, instancing, axes, units, light units, field of view;
# 6. **renders** every X3D viewpoint through a ladder (*preview / high / ultra*) with Movie Render Queue: Lumen everywhere, hardware ray tracing and the **path tracer** where the GPU has RT cores, big resolutions and resident texture pools where it has VRAM;
# 7. **compares** Unreal with X_ITE (and Cycles) on the same views with the lab's own referee (`formats.match`, `compare_pngs`), plus SSIM, in a contact sheet;
# 8. ends with a **scorecard** (PASS / WARN / FAIL / SKIP, a reason on every row), a JSON **run card**, and everything saved to your Drive.
#
# | Colab GPU | What Unreal gets | Use it for |
# |---|---|---|
# | **G4** (RTX PRO 6000 Blackwell, 96 GB) | RT cores **and** the most VRAM: hardware ray tracing, the path tracer, 8K stills | **the best Colab GPU for Unreal fidelity** |
# | **A100** (40 / 80 GB) | **no RT cores, no NVENC**: software Lumen only, no path tracer; but huge VRAM: 8K stills without tiling, resident texture pools | big stills, big scenes |
# | **L4** (24 GB) | RT cores (Ada), path tracer, 4K | good value |
# | **T4** (16 GB) | RT cores (Turing), but small RAM and few vCPUs on the standard runtime: the first shader compile can take hours | a smoke test; use *High-RAM* |
#
# **Runtime:** *Runtime › Change runtime type* › **G4** (best), A100 or L4. **Time:** the first run pays a large one-time cost (copying ~40 GB, compiling Unreal's shaders); later runs reuse what this notebook caches in your Drive. Everything was tested in a sandbox *without* a GPU or Unreal: this notebook's first Colab run is the live test, so it explains every failure in detail.
#
# <details><summary><b>📖 Words used here</b> (click)</summary>
#
# | Word | Meaning |
# |---|---|
# | **RHI** | Unreal's *Rendering Hardware Interface*: the layer that talks to Vulkan, DirectX or Metal. **NullRHI** draws nothing |
# | **Vulkan ICD** | the small JSON file that tells Vulkan which GPU driver library to load |
# | **Lumen** | Unreal's dynamic global illumination and reflections; *software* Lumen runs on any GPU, *hardware* Lumen uses RT cores |
# | **path tracer** | Unreal's offline-quality renderer: needs hardware ray tracing |
# | **Movie Render Queue (MRQ)** | Unreal's tool for final-quality frames: many samples per pixel, EXR/PNG output |
# | **Interchange** | Unreal's import framework (glTF, FBX, USD) |
# | **DDC** | *Derived Data Cache*: compiled shaders and cooked data, cached so the second run is fast |
# | **EV100**, **lux**, **candela** | exposure, illuminance (directional lights) and luminous intensity (point and spot lights): Unreal's physical units |
# | **NVENC** | NVIDIA's video encoder (Pixel Streaming needs it; the A100 has none) |
# </details>
#
# Status words: **PASS** worked as expected · **WARN** worked, worth a look · **FAIL** did not work · **SKIP** not run (the reason is shown).

# %% [markdown]
# ## Your one-time step: get Unreal Engine with your Epic account
#
# This notebook cannot download Unreal for you (Epic's EULA: each user downloads it with their own account). Do **one** of these once; the notebook finds it by itself.
#
# **A · The Linux zip in your Google Drive (recommended).**
# 1. Sign in at [unrealengine.com/linux](https://www.unrealengine.com/linux) with your Epic account and accept the EULA.
# 2. Download **`Linux_Unreal_Engine_5.8.4.zip`** (the current release in Nov 2026; 5.8.1's zip was 39 GB, ~70 GB unpacked). 5.9 is a *preview*: use it only on purpose.
# 3. Upload the zip to your Drive folder **`MyDrive/x3dlab/unreal/`** (any name `Linux_Unreal_Engine_*.zip`). Drive needs the room (a Google One plan); the browser upload of 40 GB takes a while: let it finish.
#
# **A2 · Straight from Epic's download link into your Drive (fastest: ~70 MB/s, no upload from your computer).** On unrealengine.com/linux, right-click the download button and copy the link, then run step **5a** below and paste it when asked. The link is typed into a hidden prompt: it is never saved in the notebook, so saving the notebook to GitHub cannot publish it (Epic's links expire after an hour anyway).
#
# **B · Epic's container image, without Docker.**
# 1. Link your GitHub account to your Epic account ([epicgames.com › Account › Apps and accounts](https://www.epicgames.com/account/connections)) and accept the e-mail invitation to GitHub's **EpicGames** organisation.
# 2. Create a GitHub *classic* personal access token with only **`read:packages`**.
# 3. In Colab's 🔑 Secrets add **`GHCR_USER`** (your GitHub user name) and **`GHCR_TOKEN`** (the token), with *Notebook access* on.
# 4. Set `ENGINE_SOURCE = ghcr` and `GHCR_TAG = dev-slim-5.8.4` below. (The 5.8.1 dev-slim image held a 1 TB build-cache file; this notebook skips it, and Epic fixed it by 5.8.3.)
#
# After the first successful run, `CACHE_ENGINE` writes a trimmed engine archive (no debug symbols, templates or samples: what Epic's own *dev-slim* image also leaves out) to `MyDrive/x3dlab/unreal/cache/`, and later runs start from that. It is your own copy in your own Drive; do not share it.

# %%
#@title 1 · Options { display-mode: "form" }
ENGINE_SOURCE = "auto"  #@param ["auto", "cache", "zip", "ghcr"]
ENGINE_VERSION = "5.8"  #@param ["5.8", "5.4", "5.9", "5.2", "newest"] {allow-input: true}
#@markdown **ENGINE_VERSION** picks among the engines in your Drive (cache archives and `Linux_Unreal_Engine_*.zip`): `5.8` = any 5.8.x, `5.8.4` = exactly that, `newest` = the newest that is not a preview. Changing it switches engines (the one on the local disk is replaced). 5.8 is the default since 12 Nov 2026 (the current release; step 5a fetches it into your Drive once); 5.4 still works; 5.9 is a preview.
SUBSTRATE = "auto"  #@param ["auto", "on", "off"]
MEGALIGHTS = "auto"  #@param ["auto", "on", "off"]
#@markdown **SUBSTRATE** `auto` = Substrate materials from 5.7 (production-ready, Epic's default for new projects). **MEGALIGHTS** `auto` = on from 5.8 where hardware ray tracing is on. Both decide which shaders compile: set them before the first start.
PRIMITIVE_DIVISIONS = 64  #@param {type:"slider", min:16, max:256, step:16}
BLENDER_IO_REF = "main"  #@param {type:"string"}
LINES_FOR_UNREAL = True  #@param {type:"boolean"}
LINE_PX = 1.5  #@param {type:"number"}
X3D_COLOURS = "x_ite"  #@param ["x_ite", "as_is"]
BACKGROUND_GRADIENT = True  #@param {type:"boolean"}
#@markdown **PRIMITIVE_DIVISIONS** segments for X3D Sphere, Cylinder and Cone (X3D leaves them to the browser; 64 holds up at 4K). Read by blender_IO from the *import-fidelity* change on, which also shades them smooth, maps X3D's Blinn-Phong specular and draws lines unlit. **BLENDER_IO_REF** `main` (default) = the add-on's newest main, so those fixes reach Unreal as soon as they merge; the run card records the exact commit. `pin` = the commit in x3d.lab's pins.toml (what the other notebooks use); or any branch or commit of hagopjay/x3d-blender-io. **LINES_FOR_UNREAL** gives Unreal its own copy of each glb with X3D lines and points as tubes and spheres `LINE_PX` pixels wide in a 1080-line frame, sized from the scene's viewpoints (the median one) (Unreal drops line and point primitives; X_ITE and Babylon.js keep drawing the originals).
#@markdown **X3D_COLOURS** `x_ite` (default) reads classic X3D colours (Material, UnlitMaterial, Background) as sRGB values, as X_ITE and Castle do, and lights classic and PhysicalMaterial scenes the way X_ITE does: measured, a Material emissiveColor of 0.5 is 128 in X_ITE, 186 in Babylon.js and Unreal when the glb carries it as is, 127 when converted. Needs the add-on's *x-ite-colour* change (else the run falls back to `as_is` and says so). `as_is` = colours as they are (the behaviour before 12 Nov). **BACKGROUND_GRADIENT** draws an X3D Background's sky and ground gradient as a dome in Unreal (one sky colour stays the flat backdrop). Every emissive X3D colour gets K, as the backdrop does.
DRIVE_DIR = "/content/drive/MyDrive/x3dlab/unreal"  #@param {type:"string"}
GHCR_TAG = "dev-slim-5.8.4"  #@param {type:"string"}
SCENES = "calib, stilllife, toycar-x3d, toycar-gltf, odyssey, gobbler3"  #@param {type:"string"}
#@markdown also available: `gobbler-gyro`, `hall` (14 MB, 23 viewpoints), `pbr-tests`, `lantern-gltf`, `waterbottle-gltf`
RUNG = "auto"  #@param ["auto", "preview", "high", "ultra", "ultra_lumen"]
LOOK = "cinematic"  #@param ["cinematic", "match"]
MAX_VIEWPOINTS = 4  #@param {type:"slider", min:1, max:30, step:1}
COMPARE = True  #@param {type:"boolean"}
USD = "auto"  #@param ["auto", "off"]
CACHE_ENGINE = True  #@param {type:"boolean"}
CACHE_DDC = True  #@param {type:"boolean"}
FORCE_HWRT = False  #@param {type:"boolean"}
VERBOSE = True  #@param {type:"boolean"}
VISUAL = True  #@param {type:"boolean"}
HEARTBEAT_S = 20  #@param {type:"slider", min:5, max:120, step:5}
DRIVE_HEARTBEAT = True  #@param {type:"boolean"}
GENTLE = True  #@param {type:"boolean"}
#@markdown **DRIVE_HEARTBEAT** writes `DRIVE_DIR/heartbeat/<run>/heartbeat.json` every minute and mirrors logs and stills there every 5 minutes, so the run can be watched from anywhere, also after the browser disconnects (data out only; nothing is read back). **GENTLE** runs Unreal at low CPU and disk priority and leaves 4 cores free: a VM with every core busy can be recycled by Colab.
#@markdown **VERBOSE** streams Unreal's important log lines while it runs, plus a heartbeat every `HEARTBEAT_S` seconds (shader jobs and their rate, workers, Unreal's memory, RAM, VRAM, GPU %, free disk), and warns when Unreal's memory grows with nothing to show for it. **VISUAL** shows every still the moment it is written and a VRAM/GPU chart after each Unreal job.
LAB_REPO = "hagopjay/x3d.lab"
LAB_REF = "main"
#@markdown **auto** rung: *high* on a T4 or L4, *ultra* on A100 / G4. **match** look = X3D-like light (no tone curve, no global illumination): use it to compare engines; **cinematic** = Lumen GI, filmic tone curve.
print("✅ options saved: engine from %s · scenes %s · rung %s · look %s" % (ENGINE_SOURCE, SCENES, RUNG, LOOK))

# %% [markdown]
# ## 2 · The helpers
#
# Two pieces of code, both from this repository: the Unreal leg (`notebooks/unreal/x3due`, plus the Unreal project and the Python scripts that run *inside* Unreal), and the merged `x3dlab` package for everything that is not Unreal (the X3D → glTF trip and its referee, X_ITE in headless Chrome, Blender).

# %%
#@title 2 · Get the helpers { display-mode: "form" }
import importlib, json, os, subprocess, sys, time
from pathlib import Path
from IPython.display import HTML, Image as IPImage, display

def sh(cmd, timeout=1800):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()

def git(args, timeout=1800):
    """git with a read-only token from Colab Secrets (GITHUB_TOKEN) sent as a header for this repo only, the same way as
    notebooks 01-06: works when the repo is private; the token never goes into a URL, a file or the output."""
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
    LAB_DIR = Path(os.environ["X3DLAB_DEV"])                     # a local checkout
elif not (LAB_DIR / ".git").exists():
    rc, msg = git(["clone", "-q", "--depth", "1", "--branch", LAB_REF, "https://github.com/%s" % LAB_REPO, str(LAB_DIR)])
    if rc:
        raise SystemExit("%s\ncould not clone github.com/%s. If that repo is private, add a read-only GitHub token as the Colab "
                         "secret GITHUB_TOKEN (key icon, left bar) and allow this notebook to read it." % (msg, LAB_REPO))
else:
    rc, msg = git(["-C", str(LAB_DIR), "pull", "-q", "--ff-only"])
    print(msg or "helpers up to date")
UNREAL_DIR = LAB_DIR / "notebooks" / "unreal"
sys.path.insert(0, str(UNREAL_DIR))
if importlib.util.find_spec("x3dlab") is None:
    print("installing the x3dlab package (formats, Blender, X_ITE)…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "%s[research]" % LAB_DIR], check=True)
# An editable install only becomes importable after a kernel restart (its .pth file is read at start-up), so put the
# package's source folder on the path as well: `import x3dlab` works in this same session.
if str(LAB_DIR / "src") not in sys.path:
    sys.path.insert(1, str(LAB_DIR / "src"))
importlib.invalidate_caches()
from x3due import (compare, convert, engine, engines, gltfstat, lines, live, pipeline, project, report, showcase, tier, staging, ucopy,
                   uerun, vulkan, x3dscene)
SC = report.Scorecard()
CARD = {"notebook": "07_Unreal_Fidelity", "lab_commit": sh("git -C %s rev-parse --short HEAD" % LAB_DIR), "options": {
    k: globals()[k] for k in ("ENGINE_SOURCE", "ENGINE_VERSION", "SUBSTRATE", "MEGALIGHTS", "GHCR_TAG", "SCENES", "RUNG", "LOOK",
                              "MAX_VIEWPOINTS", "COMPARE", "USD", "FORCE_HWRT", "PRIMITIVE_DIVISIONS", "BLENDER_IO_REF",
                              "LINES_FOR_UNREAL", "LINE_PX", "X3D_COLOURS", "BACKGROUND_GRADIENT")}}
WORK = Path("/content/x3due") if Path("/content").is_dir() else Path.home() / "x3due"
print("✅ helpers from %s @ %s · work folder %s" % (LAB_REPO, CARD["lab_commit"], WORK))

# %% [markdown]
# ## 3 · Which GPU is this, and what can it do for Unreal?
#
# The table is from NVIDIA's own documents (sources in `x3due/tier.py` and `FINDINGS.md`). It is only a *prior*: step 4 asks the Vulkan device for its ray-tracing extensions, and that answer decides.

# %%
#@title 3 · Detect the tier { display-mode: "form" }
HW = tier.detect()
g = HW["gpu"]
print("GPU      :", ("%s · %.0f GB · driver %s · CUDA %s · compute %s" % (g["name"], HW["vram_gb"], HW["driver"], HW["cuda"], g["compute_cap"])) if g else "none")
print("machine  : %d vCPUs · %.0f GB RAM (%.0f free) · %.0f GB free disk in %s" % (HW["cpus"], HW.get("ram_gb", 0), HW.get("ram_available_gb", 0), HW["disk_free_gb"], HW["disk_path"]))
print("container: NVIDIA_DRIVER_CAPABILITIES=%s" % (HW["driver_capabilities"] or "(unset)"))
display(HTML("<div style='font-size:12.5px'>%s</div>" % tier.capability_table_markdown().replace("\n", "<br>")))
CARD["hardware"] = HW
if not g:
    SC.add("3 tier", "NVIDIA GPU present", "FAIL", "no nvidia-smi output: pick a GPU runtime (G4, A100, L4)")
else:
    SC.add("3 tier", "NVIDIA GPU present", "PASS", "%s, %.0f GB, driver %s" % (g["name"], HW["vram_gb"], HW["driver"]))
    SC.add("3 tier", "GPU in the capability table", "PASS" if HW["kind"] in tier.CAPS else "WARN",
           "%s: %s" % (HW["kind"], tier.CAPS.get(HW["kind"], {}).get("rt_note", "unknown GPU, ray tracing decided by Vulkan only")))
need = 90
SC.add("3 tier", "free disk for the engine", "PASS" if HW["disk_free_gb"] >= need else "WARN",
       "%.0f GB free; zip + trimmed engine need about %d GB (less when extracting straight from Drive)" % (HW["disk_free_gb"], need))
SC.add("3 tier", "RAM", "PASS" if HW.get("ram_gb", 0) >= 32 else "WARN",
       "%.0f GB (Unreal recommends 32 GB; shader workers are capped by RAM)" % HW.get("ram_gb", 0))

# %% [markdown]
# ## 4 · Give Unreal real Vulkan
#
# Colab starts the container with `NVIDIA_DRIVER_CAPABILITIES=compute,utility`: CUDA works, Vulkan does not. `apt-get install libnvidia-gl-<branch>` fails here (*held broken packages*: it would replace Colab's CUDA userspace). What works, and what this cell does: **download** `libnvidia-gl`, `libnvidia-gpucomp` and `libnvidia-common` at *exactly* the driver's version, **unpack** their files (`dpkg -x`, apt's database untouched), `ldconfig`, install `vulkan-tools`. It never installs a different driver version and never hand-writes an ICD file. Then it asks Vulkan, *as the user Unreal will run as*, what the device can do.

# %%
#@title 4 · Vulkan driver, device and ray-tracing extensions { display-mode: "form" }
VK_ENABLE = vulkan.enable()
print("driver %s · %s" % (VK_ENABLE.get("driver"), VK_ENABLE.get("skipped") or "unpacked: " + ", ".join("%s=%s" % kv for kv in VK_ENABLE.get("plan", {}).get("packages", {}).items())))
for d in VK_ENABLE.get("done", []):
    print("  %s → rc %s %s" % (d["cmd"], d["rc"], d["err"][-120:] if d["rc"] else ""))
if HW["gpu"]:
    uerun.ensure_user("ue", [])        # Unreal refuses root; Vulkan is asked as the same user Unreal will run as
VK = vulkan.probe(user="ue" if HW["gpu"] else None)
for d in VK["devices"]:
    print("Vulkan device: %s · %s · API %s%s" % (d.get("deviceName"), d.get("driverName"), d.get("apiVersion"), " (software!)" if d["software"] else ""))
PLAN = tier.plan(HW, VK, force_hwrt=FORCE_HWRT)
rt = PLAN["ray_tracing"]
print("ray tracing:", rt["why"])
print("extensions :", ", ".join("%s %s" % (k, "✓" if v else "✗") for k, v in rt["extensions"].items()))
for n in PLAN["notes"]:
    print("→", n)
st = vulkan.status()
SC.add("4 Vulkan", "NVIDIA Vulkan ICD present", "PASS" if st["nvidia_icd"] else "FAIL",
       st["nvidia_icd"][0] if st["nvidia_icd"] else (VK_ENABLE.get("skipped") or VK_ENABLE.get("error") or "no nvidia_icd.json"))
nv = VK["nvidia_device"]
SC.add("4 Vulkan", "user ue sees the NVIDIA device", "PASS" if nv and not nv["software"] else "FAIL",
       "%s (Vulkan %s)" % (nv.get("deviceName"), nv.get("apiVersion")) if nv else "vulkaninfo as ue lists %s" % ([d.get("deviceName") for d in VK["devices"]] or "nothing"))
SC.add("4 Vulkan", "ray tracing decision", rt["status"], rt["why"])
SC.add("4 Vulkan", "nvoptix.bin (OptiX denoiser)", "PASS" if st["nvoptix_bin"] else "WARN", st["nvoptix_bin"][0] if st["nvoptix_bin"] else "missing: Cycles falls back to OpenImageDenoise")
CARD.update(vulkan={"enable": {k: VK_ENABLE.get(k) for k in ("driver", "skipped", "plan", "error")}, "devices": [
    {k: v for k, v in d.items() if k != "extensions"} for d in VK["devices"]]}, plan={k: v for k, v in PLAN.items() if k != "ladder"},
    ladder=PLAN["ladder"])

# %% [markdown]
# ### 4b · Only if step 4 says user `ue` does not see the NVIDIA device
#
# Checks first and changes nothing when Vulkan already works. Otherwise it installs the Vulkan/GL userspace of *exactly* the running driver from NVIDIA's own driver package (`--extract-only`, no kernel module).

# %%
#@title 4b · Vulkan for THIS GPU: diagnose, and repair from NVIDIA's own driver package { display-mode: "form" }
import glob, json, os, re, shutil, subprocess, urllib.request
def sh(c, t=900):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t)
    return (r.stdout + r.stderr).strip()
def ue_sees_gpu():
    out = sh("runuser -u ue -- env -i HOME=/home/ue PATH=/usr/bin:/bin vulkaninfo --summary 2>&1")
    return ("NVIDIA" in out and "llvmpipe" not in out.split("deviceName")[-1]), out
ver, name = [x.strip() for x in sh("nvidia-smi --query-gpu=driver_version,name --format=csv,noheader").split(",")[:2]]
print("GPU %s · driver %s" % (name, ver))
if "VK_ENABLE" in globals():
    print("step 4 said:", {k: VK_ENABLE.get(k) for k in ("driver", "skipped", "error")})
sh("id ue || useradd -m ue")
if not shutil.which("vulkaninfo"):
    sh("apt-get install -y -qq libvulkan1 vulkan-tools || (apt-get update -qq && apt-get install -y -qq libvulkan1 vulkan-tools)")
print("ICD files:", glob.glob("/usr/share/vulkan/icd.d/*nvidia*") + glob.glob("/etc/vulkan/icd.d/*nvidia*") or "none")
ok, out = ue_sees_gpu()
if ok:
    print("✅ user ue already sees the GPU through Vulkan: nothing to repair")
else:
    print("user ue does not see the GPU (%s): installing the Vulkan/GL userspace of driver %s from NVIDIA" % (
        (re.findall(r"ERROR[^\n]*", out) or ["no NVIDIA device"])[0][:120], ver))
    run = "/content/NVIDIA-Linux-x86_64-%s.run" % ver
    for url in ("https://us.download.nvidia.com/tesla/%s/NVIDIA-Linux-x86_64-%s.run" % (ver, ver),
                "https://download.nvidia.com/XFree86/Linux-x86_64/%s/NVIDIA-Linux-x86_64-%s.run" % (ver, ver)):
        if os.path.exists(run) and os.path.getsize(run) > 50e6:
            break
        try:
            print("  downloading", url.split("/")[2], "…")
            urllib.request.urlretrieve(url, run)
        except Exception as e:
            print("  ", e)
    assert os.path.exists(run) and os.path.getsize(run) > 50e6, "could not download driver %s from NVIDIA" % ver
    d = "/content/nvdrv-%s" % ver
    if not os.path.isdir(d):
        print("  extracting…", sh("sh %s --extract-only --target %s 2>&1 | tail -1" % (run, d)))
    lib = "/usr/lib/x86_64-linux-gnu"
    copied = []
    for f in sorted(glob.glob(d + "/*.so.%s" % ver)):
        b = os.path.basename(f)
        if re.match(r"lib(GLX_nvidia|EGL_nvidia|nvidia-(glcore|glvkspirv|tls|gpucomp|eglcore|glsi|rtcore|vksc-core)|nvoptix)\.so\.", b):
            if not os.path.exists(os.path.join(lib, b)):          # never replace what Colab ships (CUDA libs untouched)
                shutil.copy2(f, lib); copied.append(b)
    for b in ("libGLX_nvidia", "libEGL_nvidia"):
        src = os.path.join(lib, "%s.so.%s" % (b, ver))
        if os.path.exists(src) and not os.path.exists(os.path.join(lib, b + ".so.0")):
            os.symlink(src, os.path.join(lib, b + ".so.0"))
    os.makedirs("/usr/share/nvidia", exist_ok=True)
    if os.path.exists(d + "/nvoptix.bin") and not os.path.exists("/usr/share/nvidia/nvoptix.bin"):
        shutil.copy2(d + "/nvoptix.bin", "/usr/share/nvidia/nvoptix.bin")
    tmpl = (glob.glob(d + "/nvidia_icd.json*") or [None])[0]
    icd = json.loads(open(tmpl).read().replace("__NV_VK_ICD__", "libGLX_nvidia.so.0")) if tmpl else \
        {"file_format_version": "1.0.0", "ICD": {"library_path": "libGLX_nvidia.so.0", "api_version": "1.3.0"}}
    os.makedirs("/usr/share/vulkan/icd.d", exist_ok=True)
    json.dump(icd, open("/usr/share/vulkan/icd.d/nvidia_icd.json", "w"), indent=1)
    sh("ldconfig")
    print("  copied %d libraries: %s" % (len(copied), ", ".join(copied)))
    ok, out = ue_sees_gpu()
    print(("✅ user ue now sees: " if ok else "❌ still no GPU for ue: ") +
          "; ".join(l.strip() for l in out.splitlines() if "deviceName" in l or "ERROR" in l)[:300])
print("\nray-tracing extensions:", {e: e in sh("runuser -u ue -- env -i HOME=/home/ue vulkaninfo 2>/dev/null | grep -o 'VK_KHR_[a-z_]*'")
                                   for e in ("VK_KHR_ray_tracing_pipeline", "VK_KHR_acceleration_structure", "VK_KHR_ray_query")})

# %% [markdown]
# ## 5a · (optional) Download Unreal from your Epic link into Drive
#
# Skip this when the zip is already in your Drive. The file keeps Epic's own name (for example `Linux_Unreal_Engine_5.4.4.zip`), so the version is known.

# %%
#@title 5a · Paste your Epic download link (hidden; never saved) { display-mode: "form" }
DOWNLOAD = False  #@param {type:"boolean"}
if DOWNLOAD:
    from getpass import getpass
    try:
        from google.colab import drive as _drive
        _drive.mount("/content/drive")
    except Exception:
        pass
    _have = pipeline.drive_engines(DRIVE_DIR, ENGINE_VERSION) if Path(DRIVE_DIR).parent.exists() else []
    if _have:
        print("already in your Drive for %s: %s" % (ENGINE_VERSION, ", ".join(Path(f["path"]).name for f in _have)))
    _url = getpass("Epic download link (Enter to skip): ").strip()
    if _url:
        try:
            _got = engine.download_link(_url, DRIVE_DIR, log=print)          # the link itself is never printed
        finally:
            del _url
        print("✅ %s · %.1f GB in %.0f s (%.0f MB/s)" % (_got["name"], _got["bytes"] / 2**30, _got["seconds"], _got["mb_s"]))
    else:
        del _url
else:
    print("skipped (tick DOWNLOAD to paste an Epic link)")

# %% [markdown]
# ## 5 · Get the engine
#
# From your Drive (a cache archive from an earlier run, or your `Linux_Unreal_Engine_*.zip`), or from Epic's container image. The zip's index is read first: the notebook knows the exact size before it extracts anything, and decides whether to copy the zip to the local SSD (faster) or extract straight from Drive (when the disk is too small for both).

# %%
#@title 5 · Engine onto the local disk { display-mode: "form" }
try:
    from google.colab import drive, userdata
    drive.mount("/content/drive")
    def secret(k):
        try:
            return userdata.get(k)
        except Exception:
            return None
except Exception:
    def secret(k):
        return os.environ.get(k)
if Path("/content").is_dir() and not pipeline.drive_mounted(DRIVE_DIR):
    raise SystemExit("❌ Google Drive is not mounted, so the engine in %s cannot be seen. Run `from google.colab import drive; "
                     "drive.mount('/content/drive', force_remount=True)`, sign in with the account that owns the engine files, "
                     "then run this step again." % DRIVE_DIR)
LAB = pipeline.Lab(work=WORK, hw=HW, plan=PLAN)
LAB.live = live.Live(verbose=VERBOSE, visual=VISUAL, every=HEARTBEAT_S, disk_path=str(WORK)) if (VERBOSE or VISUAL) else None
if GENTLE and HW["gpu"]:
    os.nice(max(0, 10 - os.nice(0)))                  # this kernel and everything it starts yield to Colab's own services
    sh("ionice -c 3 -p %d" % os.getpid())              # lowest disk priority, inherited the same way
    if HW["cpus"] >= 8:
        PLAN["shader_workers"] = min(PLAN.get("shader_workers") or HW["cpus"], HW["cpus"] - 4)
    print("gentle: nice %d · %s shader workers" % (os.nice(0), PLAN.get("shader_workers")))
if DRIVE_HEARTBEAT and Path("/content/drive/MyDrive").exists():
    from x3due import heartbeat
    if not (globals().get("HB_DRIVE") and HB_DRIVE.alive()):
        Path(DRIVE_DIR).mkdir(parents=True, exist_ok=True)
        HB_DRIVE = heartbeat.Heartbeat(DRIVE_DIR, WORK / "out", context=lambda: globals()).start()
    print("💓 Drive heartbeat →", HB_DRIVE.dir)
if not engine.tool("zstd") and HW["gpu"]:
    sh("apt-get install -y -qq zstd")      # multi-threaded compression for the Drive cache
Path(DRIVE_DIR).mkdir(parents=True, exist_ok=True) if Path(DRIVE_DIR).parent.exists() else None
t0 = time.time()
try:
    ENGINE = pipeline.get_engine(LAB, ENGINE_SOURCE, DRIVE_DIR, GHCR_TAG, secret("GHCR_USER"), secret("GHCR_TOKEN"),
                                 want=ENGINE_VERSION)
    print("✅ Unreal %s from %s in %.0f s → %s" % (ENGINE.get("version"), ENGINE["source"], ENGINE["seconds"], LAB.engine_dir))
    SC.add("5 engine", "engine on local disk", "PASS", "Unreal %s (changelist %s) from %s in %.0f s" % (
        ENGINE.get("version"), ENGINE.get("changelist"), ENGINE["source"], ENGINE["seconds"]))
    ver = ENGINE.get("version") or ""
    SC.add("5 engine", "engine version", "PASS" if engine.vkey(ver)[:2] >= (5, 4) else "WARN",
           "%s (asked for %s; 5.8.x is the current release, 5.9 a preview; before 5.4 Unreal on Linux has no Vulkan SM6, so no ray tracing)"
           % (ver, ENGINE_VERSION))
    LAB.settings["sm6"] = engine.default_sm6(ver)
except Exception as exc:
    ENGINE = {"error": str(exc)}
    print("❌", exc)
    SC.add("5 engine", "engine on local disk", "FAIL", str(exc)[:350])
CARD["engine"] = {k: v for k, v in ENGINE.items() if k not in ("extract",)}
if ENGINE.get("version") and not LAB.settings["sm6"]:
    LAB.settings["ray_tracing"] = False
    engine.disable_vulkan_rt(LAB.engine_dir)
    print("Unreal %s: Shader Model 5, no ray tracing (no Vulkan SM6 shader format before 5.4)" % ENGINE["version"])
elif ENGINE.get("version") and PLAN["ray_tracing"]["hw_rt"]:
    LAB.settings["ray_tracing"] = True
    CARD["engine"]["vulkan_rt_config"] = engine.enable_vulkan_rt(LAB.engine_dir, LAB.settings["bindless"])
    print("Vulkan ray tracing switched on in the engine config (Epic's documented edit; originals kept as *.x3due.orig)")
_parts_next = bool(ENGINE.get("version") and CACHE_ENGINE and Path(DRIVE_DIR).exists() and not ENGINE.get("parts"))
if ENGINE.get("version") and CACHE_ENGINE and ENGINE["source"] != "drive cache" and Path(DRIVE_DIR).exists() and not _parts_next:
    CARD["engine_cache"] = pipeline.cache_engine(LAB, DRIVE_DIR, ENGINE["version"])     # only when no parts follow (Drive room)
    print("Drive cache:", CARD["engine_cache"])
if _parts_next:
    print("one-time: splitting the engine cache into %d parts on Drive, so the next runtime restores it in parallel…" % engine.PARTS)
    CARD["engine_parts"] = pipeline.cache_engine_parts(LAB, DRIVE_DIR, ENGINE["version"])
    print("Drive cache (parts):", CARD["engine_parts"])

# %% [markdown]
# ## 6 · First start: prove the RHI, pay the shader bill
#
# Unreal refuses to run as root (*"Refusing to run with the root privileges"*), so it runs as a normal user `ue`. It starts on Vulkan with **no window** (`-RenderOffscreen`), opens an empty map and quits. The first start compiles Unreal's global shaders (Blender's first OptiX render took 325 s on the A100; Unreal's is usually far longer): it is timed separately, then a second start shows the warm cost. The log decides PASS: Vulkan on the NVIDIA device, not NullRHI, not llvmpipe.

# %%
#@title 6 · Cold and warm start { display-mode: "form" }
if CACHE_DDC and Path(DRIVE_DIR).exists():
    CARD["ddc_restore"] = pipeline.ddc_restore(LAB, DRIVE_DIR, ENGINE.get("version"))
    print("DDC:", CARD["ddc_restore"])
LAB.settings["usd"] = USD == "auto"          # step 8 imports .usdc files: the USD Importer plugin must be on
LAB.settings["engine_version"] = ENGINE.get("version")
LAB.settings["features"] = {"substrate": SUBSTRATE, "megalights": MEGALIGHTS, "async_compute": False}
PROJ = LAB.write_project()
print("project %s · plugins %s · %d shader workers" % (PROJ["project"], ", ".join(p["Name"] for p in PROJ["plugins"] if p["Enabled"]), PROJ["workers"]))
_f = PROJ.get("features") or {}
print("features: Substrate %s (%s) · MegaLights %s (%s) · async compute off (device-lost guard)"
      % ("on" if _f.get("substrate") else "off", _f.get("why", {}).get("substrate"), "on" if _f.get("megalights") else "off",
         _f.get("why", {}).get("megalights")))
CARD["features"] = _f
if PROJ.get("mcp_plugins"):
    print("MCP: this engine ships %s (not used by these batch runs)" % ", ".join(PROJ["mcp_plugins"]))
if PROJ["missing_required"] and ENGINE.get("version"):
    SC.add("6 start", "required plugins in this engine", "FAIL", "missing: %s" % PROJ["missing_required"])
SMOKE = {}
if ENGINE.get("version"):
    for label in ("cold", "warm"):
        SMOKE[label] = pipeline.smoke(LAB, "smoke_" + label)
        p = SMOKE[label]["proof"]
        print("%s start: rc %s in %.0f s · %s · %s" % (label, SMOKE[label]["rc"], SMOKE[label]["seconds"], p.get("verdict"), p.get("why")))
    p = SMOKE["cold"]["proof"]
    SC.add("6 start", "RHI is Vulkan on the NVIDIA GPU", p["verdict"], "%s · device %s · vendor %s · %s · driver %s" % (
        p.get("why"), p.get("device"), p.get("vendor_id"), p.get("shader_platform"), p.get("driver")))
    SC.add("6 start", "one-time shader compile (cold start)", "PASS" if SMOKE["cold"]["rc"] == 0 else "WARN",
           "%.0f s cold, %.0f s warm (difference ≈ global shader compile + DDC fill)" % (SMOKE["cold"]["seconds"], SMOKE["warm"]["seconds"]))
    if p["verdict"] != "PASS":
        print("\n--- the log lines that matter ---\n" + SMOKE["cold"]["excerpt"])
        stack = pipeline.crash_stack(LAB)
        if stack:
            print("\n--- Unreal's crash call stack (newest crash) ---\n" + stack)
            CARD.setdefault("crashes", []).append(stack[:1500])
    for k, v in SMOKE.items():
        for a in v.get("attempts") or []:
            if a.get("change"):
                print("%s start: retried with %s" % (k, a["change"]))
    CARD["smoke"] = {k: {x: v.get(x) for x in ("rc", "seconds", "killed", "proof", "gpu", "attempts", "timing")} for k, v in SMOKE.items()}
    if CACHE_DDC and Path(DRIVE_DIR).exists() and SMOKE["cold"]["rc"] == 0 and SMOKE["cold"]["seconds"] > 120:
        # The global shaders are the expensive part (17 min on an A100, hours on a small runtime): keep them now,
        # not only at step 11, so a run that stops later still leaves a warm cache for the next one.
        CARD["ddc_save_after_start"] = pipeline.ddc_save(LAB, DRIVE_DIR, ENGINE.get("version"))
        print("shader cache saved to Drive:", CARD["ddc_save_after_start"])
else:
    SC.add("6 start", "RHI is Vulkan on the NVIDIA GPU", "SKIP", "no engine (step 5)")

# %% [markdown]
# ### 6c · Only if step 6 fails: find out why, and find a start that works
#
# Reads Vulkan as user `ue`, the engine's config edits, step 6's log and crash report (with the nearest function names for the crash stack), then tries a few ways of starting Unreal for at most `PROBE_SECONDS` each and keeps the first that works. Its report goes to `DRIVE_DIR/probe/`. (It found the 11 Nov 2026 A100 crash: the Nanite pool above Vulkan's 2 GB allocation, now capped in `tier.py`.)

# %%
#@title 6c · Unreal dies at start on this GPU: find out why, and find a start that works (≈ 3–6 min) { display-mode: "form" }
# Run after step 6 fails. Read-only on your Drive except one report folder (probe/<time>_<gpu>/), which Claude can read.
# It tries a few ways of starting Unreal, each for at most PROBE_SECONDS: a start that is still alive by then got past
# the point where step 6 died (the renderer's start-up, ~10 s in), and is stopped there. With APPLY_WINNER it then keeps
# the first start that worked, so step 6 can simply be run again.
PROBE_SECONDS = 75     #@param {type:"integer"}
APPLY_WINNER = True    #@param {type:"boolean"}

import bisect, glob, json, os, re, shutil, subprocess, time
from pathlib import Path
from x3due import engine as E, pipeline, uerun

assert "LAB" in globals() and (Path(LAB.engine_dir) / "Engine/Binaries/Linux/UnrealEditor").exists(), "run steps 1–5 first (no engine on disk)"
LOGS = Path(LAB.out) / "logs"
GPU = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip() \
    if shutil.which("nvidia-smi") else "no nvidia-smi"
STAMP = time.strftime("%Y-%m-%dT%H-%MZ", time.gmtime())
SAVED = json.loads(json.dumps(LAB.settings, default=str))
REPORT = {"stamp": STAMP, "gpu": GPU, "settings_before": SAVED, "variants": []}
CRASH_RX = re.compile(r"Caught signal|Segmentation fault|Unhandled Exception|Assertion failed|appError called|Fatal error", re.I)
BIN = Path(LAB.engine_dir) / "Engine/Binaries/Linux"
CONF = Path(LAB.engine_dir) / "Engine/Config"


def as_ue(cmd, timeout=180):
    """Run a command as the user Unreal runs as, with Unreal's environment (the same Vulkan driver it sees)."""
    full = ["runuser", "-u", LAB.user, "--", "env", "-i"] + ["%s=%s" % kv for kv in LAB.env().items()] + list(cmd)
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except Exception as exc:
        return -1, "%s: %s" % (type(exc).__name__, exc)


def kill_leftovers():
    """zenserver and the crash reporter outlive Unreal; a stale zenserver can hang the next start."""
    subprocess.run(["pkill", "-TERM", "-u", LAB.user, "-f", "zenserver"], capture_output=True)   # let Zen close its store
    time.sleep(2)
    for pat in ("UnrealEditor", "ShaderCompileWorker", "CrashReportClient", "zenserver", "UnrealTraceServer"):
        subprocess.run(["pkill", "-9", "-u", LAB.user, "-f", pat], capture_output=True)
    time.sleep(1)


def head(title):
    print("\n" + "━" * 100 + "\n" + title + "\n" + "━" * 100)


# ───────────────────────────────────────────── ① what Vulkan shows Unreal's user on this GPU
head("① Vulkan as user %s · %s" % (LAB.user, GPU))
if not shutil.which("vulkaninfo"):
    subprocess.run("apt-get install -y -qq vulkan-tools", shell=True, capture_output=True)
rc, VI = as_ue(["vulkaninfo"], timeout=120)


def vi_get(rx):
    m = re.search(rx, VI, re.M)
    return m.group(1).strip() if m else None


DEV = {k: vi_get(r"^\s*%s\s*=\s*(.+)$" % k) for k in ("deviceName", "deviceType", "apiVersion", "driverVersion")}
QUEUES = sorted(set(re.findall(r"queueFlags\s*=\s*(.+)", VI)))
EXTS = ["VK_KHR_swapchain", "VK_KHR_dynamic_rendering", "VK_KHR_maintenance4", "VK_EXT_descriptor_buffer", "VK_EXT_mutable_descriptor_type",
        "VK_EXT_mesh_shader", "VK_EXT_shader_image_atomic_int64", "VK_KHR_fragment_shading_rate", "VK_KHR_buffer_device_address",
        "VK_KHR_ray_tracing_pipeline", "VK_KHR_acceleration_structure", "VK_KHR_ray_query"]
FEATS = ["geometryShader", "tessellationShader", "textureCompressionBC", "shaderInt64", "shaderFloat16", "shaderBufferInt64Atomics",
         "shaderImageInt64Atomics", "bufferDeviceAddress", "descriptorBuffer", "meshShader", "taskShader", "descriptorIndexing",
         "runtimeDescriptorArray", "multiDrawIndirect", "fillModeNonSolid", "samplerAnisotropy"]
REPORT["vulkan"] = {"rc": rc, "device": DEV, "queues": QUEUES,
                    "extensions": {x: (x in VI) for x in EXTS},
                    "features": {f: vi_get(r"^\s*%s\s*=\s*(\w+)" % f) for f in FEATS}}
print("device   : %(deviceName)s · %(deviceType)s · API %(apiVersion)s · driver %(driverVersion)s" % {k: v or "?" for k, v in DEV.items()})
print("queues   : " + (" | ".join(QUEUES) if QUEUES else "none listed (vulkaninfo rc %s: %s)" % (rc, VI.strip().splitlines()[-1:] if VI.strip() else "")))
print("graphics : " + ("✅ a queue family has QUEUE_GRAPHICS_BIT" if any("GRAPHICS" in q for q in QUEUES) else "❌ NO graphics queue: this GPU only offers compute through Vulkan"))
print("ext      : " + "  ".join("%s %s" % (x.replace("VK_", ""), "✓" if v else "✗") for x, v in REPORT["vulkan"]["extensions"].items()))
print("features : " + "  ".join("%s=%s" % (f, v) for f, v in REPORT["vulkan"]["features"].items() if v is not None))
lic = subprocess.run("nvidia-smi -q | grep -i -E 'Virtualization Mode|License Status|Product Brand|GPU Operation Mode|MIG Mode' -A1 | head -20",
                     shell=True, capture_output=True, text=True).stdout
REPORT["nvidia_smi_q"] = lic
print(lic.strip())

# ───────────────────────────────────────────── ② the engine's ray-tracing / bindless edits and the project's RHI
head("② Engine config edits (Epic's documented Vulkan ray-tracing edit) and what the project asks for")
ORIGS = sorted(CONF.glob("**/*.x3due.orig"))
CFG_KEYS = re.compile(r"^\s*(bSupportsRayTracingShaders|bSupportsPathTracing|BindlessSupport|BindlessResources|BindlessSamplers)\s*=.*$", re.M)
for f in (CONF / "VulkanPC/DataDrivenPlatformInfo.ini", CONF / "Linux/BaseLinuxEngine.ini"):
    if f.exists():
        print("%-40s %s" % (f.relative_to(CONF), " · ".join(m.group(0).strip() for m in CFG_KEYS.finditer(f.read_text(errors="replace"))) or "(no RT/bindless keys)"))
print("edited by the lab (originals kept): %s" % ([str(o.relative_to(CONF)) for o in ORIGS] or "none"))
de = Path(LAB.project_dir) / "Config/DefaultEngine.ini"
if de.exists():
    print("project: " + " · ".join(l.strip() for l in de.read_text().splitlines() if re.match(r"\s*[+-]?TargetedRHIs|\s*r\.RayTracing=|\s*r\.Lumen\.HardwareRayTracing=", l)))
print("lab settings: sm6 %(sm6)s · ray_tracing %(ray_tracing)s · bindless %(bindless)s · cvars %(cvars)s" % LAB.settings)
SNAP = {p: p.read_text(errors="replace") for p in (CONF / "VulkanPC/DataDrivenPlatformInfo.ini", CONF / "Linux/BaseLinuxEngine.ini") if p.exists()}
REPORT["engine_config"] = {"edited": [str(o) for o in ORIGS], "keys": {str(p): [m.group(0).strip() for m in CFG_KEYS.finditer(t)] for p, t in SNAP.items()}}


def config_as_found():
    for p, t in SNAP.items():
        if p.read_text(errors="replace") != t:
            p.write_text(t)


# ───────────────────────────────────────────── ③ what step 6's log says just before the crash
head("③ Step 6's log: Vulkan start-up and the last lines before the crash")
LOG6 = sorted(LOGS.glob("smoke_*.log"), key=os.path.getmtime)
LOG6 = LOG6[-1] if LOG6 else None
if LOG6:
    lines = LOG6.read_text(errors="replace").splitlines()
    vk = [l for l in lines if re.search(r"LogVulkanRHI|LogRHI|LogInit: .*RHI|Profile|SM6|SM5", l) and not re.search(r"Display:\s+[-+] VK_", l)]
    crash_at = next((i for i, l in enumerate(lines) if CRASH_RX.search(l)), len(lines))
    print("log: %s (%d lines)" % (LOG6, len(lines)))
    for l in vk[-45:]:
        print("  " + l[:240])
    print("  … the 25 lines before the crash:")
    for l in lines[max(0, crash_at - 25): crash_at + 6]:
        print("  " + l[:240])
    REPORT["step6_log"] = {"path": str(LOG6), "vulkan_lines": vk[-80:], "before_crash": lines[max(0, crash_at - 60): crash_at + 40]}
else:
    print("no step 6 log under", LOGS)
CR = sorted(glob.glob(str(Path(LAB.project_dir) / "Saved/Crashes/*")), key=os.path.getmtime)
if CR:
    cl = Path(CR[-1]) / "X3DLab.log"
    if cl.exists():
        tl = cl.read_text(errors="replace").splitlines()
        hit = [l for l in tl if re.search(r"Unhandled Exception|Caught signal|SIGSEGV|invalid attempt|Assertion|Fatal", l)]
        print("\ncrash report %s:\n  " % Path(CR[-1]).name + "\n  ".join(h[:300] for h in hit[:8]))
        REPORT["crash_report_lines"] = hit[:20]

# ───────────────────────────────────────────── ④ name the functions in the crash stack (no debug files needed)
head("④ The crash stack with the nearest exported function names")
STACK = pipeline.crash_stack(LAB, 8000)
_SYMS = {}


def nearest(lib, off):
    so = BIN / lib if (BIN / lib).exists() else None
    if not so:
        return None
    dbg = so.with_suffix(".debug")
    if dbg.exists() and shutil.which("addr2line"):
        out = subprocess.run(["addr2line", "-C", "-f", "-e", str(dbg), hex(off)], capture_output=True, text=True).stdout.split("\n")
        if out and out[0] and out[0] != "??":
            return "%s  [%s]" % (out[0], out[1] if len(out) > 1 else "")
    if lib not in _SYMS:
        nm = subprocess.run(["nm", "-D", "--defined-only", "-C", str(so)], capture_output=True, text=True).stdout
        rows = []
        for l in nm.splitlines():
            parts = l.split(" ", 2)
            if len(parts) == 3 and parts[1] in "TtWw":
                try:
                    rows.append((int(parts[0], 16), parts[2]))
                except ValueError:
                    pass
        rows.sort()
        _SYMS[lib] = ([a for a, _ in rows], [n for _, n in rows])
    addrs, names = _SYMS[lib]
    i = bisect.bisect_right(addrs, off) - 1
    if i < 0:
        return None
    d = off - addrs[i]
    return "%s +0x%x%s" % (names[i][:160], d, "   (far from it: an internal function after it)" if d > 0x4000 else "")


NAMED = []
for l in STACK.splitlines():
    m = re.match(r"\s*(\S+?)!UnknownFunction\(0x([0-9a-fA-F]+)\)", l)
    name = nearest(m.group(1), int(m.group(2), 16)) if m else None
    NAMED.append(l.strip() + ("    ← " + name if name else ""))
print("\n".join(NAMED) or "no crash stack under Saved/Crashes")
REPORT["stack_named"] = NAMED

# ───────────────────────────────────────────── ⑤ try a few starts
head("⑤ Trying starts (each at most %d s; a start still alive then is past the crash point)" % PROBE_SECONDS)
FRESH = Path("/content/x3due_probe") if Path("/content").exists() else Path(LAB.work) / "probe_fresh"
VARIANTS = [("as step 6", {}),
            # the shader cache and home restored from the L4 run (6b) are set aside: an empty HOME and local DDC
            ("as step 6, empty home + shader cache", {"env": {"HOME": str(FRESH / "home"), "XDG_CONFIG_HOME": str(FRESH / "home/.config"),
                                                         "UE-LocalDataCachePath": str(FRESH / "ddc")}})]
if ORIGS:
    VARIANTS.append(("SM6 + Epic's original engine config", {"epic_config": True}))
VARIANTS += [("SM6 + async compute off", {"cvars": {"r.Vulkan.AllowAsyncCompute": 0}}),
             ("SM5", {"sm6": False})]
if ORIGS:
    VARIANTS.append(("SM5 + Epic's original engine config", {"sm6": False, "epic_config": True}))
VARIANTS.append(("no GPU (-nullrhi): control", {"nullrhi": True}))


def setup(ch):
    LAB.settings.clear()
    LAB.settings.update(json.loads(json.dumps(SAVED)))
    config_as_found()
    if ch.get("epic_config"):
        E.disable_vulkan_rt(LAB.engine_dir)
        LAB.settings["ray_tracing"] = False
    if ch.get("sm6") is False:
        LAB.settings["sm6"] = False
        LAB.settings["ray_tracing"] = False
    LAB.settings["cvars"].update(ch.get("cvars", {}))
    for k, v in ch.get("env", {}).items():
        LAB.settings["env"][k] = v
        if v.startswith("/") and not Path(v).exists():
            Path(v).mkdir(parents=True, exist_ok=True)
    if ch.get("env"):
        subprocess.run(["chown", "-R", "%s:%s" % (LAB.user, LAB.user), str(FRESH)], capture_output=True)
    LAB.write_project()


for label, ch in VARIANTS:
    kill_leftovers()
    setup(ch)
    cmd = pipeline.smoke_cmd(LAB)
    if ch.get("nullrhi"):
        cmd = [c for c in cmd if c not in ("-vulkan", "-sm6", "-sm5")] + ["-nullrhi"]
    LAB.own()
    log = LOGS / ("probe_%s.log" % re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"))
    res = uerun.run(cmd, log, user=LAB.user, env_vars=LAB.env(), timeout=PROBE_SECONDS)
    text = log.read_text(errors="replace") if log.exists() else ""
    timed_out = str(res.get("killed") or "").startswith("timeout")
    crash = next((l.strip() for l in text.splitlines() if CRASH_RX.search(l)), None) or (
        "exit code %s" % res["rc"] if res["rc"] not in (0, None) and not timed_out else None)
    ok = not crash and (timed_out or res["rc"] == 0)
    platform = (re.findall(r"\b(VULKAN_SM6|VULKAN_SM5)\b", text) or ["?"])[-1]
    compiling = "Using Local Shader Compiler" in text or "shader autogen file written" in text.lower()
    row = {"label": label, "change": ch, "ok": ok, "rc": res["rc"], "seconds": res["seconds"], "stopped_by_probe": timed_out,
           "crash": crash, "platform": platform, "compiling_shaders": compiling,
           "vram_peak_mb": (res.get("gpu") or {}).get("peak_mem_mb"), "log": str(log)}
    REPORT["variants"].append(row)
    print("%s %-38s rc %-5s %5.0f s · %-11s · %s%s" % ("✅" if ok else "❌", label, res["rc"], res["seconds"], platform,
          "alive at %d s, stopped" % PROBE_SECONDS if timed_out and ok else ("quit cleanly" if ok else crash[:110]),
          " · shader compile started" if compiling else ""))
kill_leftovers()

# ───────────────────────────────────────────── ⑥ keep the first start that worked
head("⑥ Verdict")
gpu_wins = [r for r in REPORT["variants"] if r["ok"] and not r["change"].get("nullrhi")]
LAB.settings.clear()
LAB.settings.update(json.loads(json.dumps(SAVED)))
config_as_found()
if not gpu_wins:
    null_ok = any(r["ok"] for r in REPORT["variants"] if r["change"].get("nullrhi"))
    REPORT["verdict"] = "no Vulkan start works on this GPU" + (" (the engine itself starts: -nullrhi is fine)" if null_ok else "")
    print("❌ " + REPORT["verdict"] + ".\n   Settings left as they were. The report below is on your Drive for Claude to read.\n"
          "   If ① shows no graphics queue, this A100 cannot draw with Vulkan at all: an L4 or G4 runtime can.")
    LAB.write_project()
else:
    w = gpu_wins[0]
    REPORT["verdict"] = "works: " + w["label"]
    if w["label"] == "as step 6":
        print("✅ Step 6's own start works now (the crash did not come back: likely a stale zenserver or a half-written cache).\n"
              "   Nothing changed. Run step 6 again.")
        LAB.write_project()
    elif APPLY_WINNER:
        setup(w["change"])
        if w["change"].get("sm6") is False and isinstance(globals().get("PLAN"), dict):
            PLAN.setdefault("notes", []).append("6c: this GPU starts only on Shader Model 5 (VULKAN_SM5)")
        print("✅ Kept: %s. Project and engine config now set that way (lab settings: sm6 %s · ray_tracing %s · cvars %s).\n"
              "   Run step 6 again: the cold start compiles the global shaders for this setting (about 15–20 min on an A100)."
              % (w["label"], LAB.settings["sm6"], LAB.settings["ray_tracing"], LAB.settings["cvars"])
              + ("\n   Unreal now uses an empty home and shader cache under %s (the cache restored from the L4 was the trouble)." % FRESH
                 if w["change"].get("env") else ""))
    else:
        print("✅ %s works. APPLY_WINNER is off, so nothing changed." % w["label"])
        LAB.write_project()
REPORT["settings_after"] = json.loads(json.dumps(LAB.settings, default=str))
globals().setdefault("CARD", {}).setdefault("probe_6c", []).append({k: REPORT[k] for k in ("stamp", "gpu", "verdict")} | {"variants": [
    {k: r[k] for k in ("label", "ok", "rc", "seconds", "platform", "crash")} for r in REPORT["variants"]]})

# ───────────────────────────────────────────── ⑦ the report on Drive (data out only)
try:
    if Path(DRIVE_DIR).exists():
        d = Path(DRIVE_DIR) / "probe" / ("%s_%s" % (STAMP, re.sub(r"[^A-Za-z0-9]+", "-", GPU.split(",")[0]).strip("-")))
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps(REPORT, indent=1, default=str))
        (d / "vulkaninfo.txt").write_text(VI[-400000:])
        for p in [LOG6] + [Path(r["log"]) for r in REPORT["variants"]]:
            if p and Path(p).exists():
                (d / Path(p).name).write_text("\n".join(Path(p).read_text(errors="replace").splitlines()[-3000:]))
        print("\nreport on Drive:", d)
except Exception as exc:
    print("(could not write the Drive report: %s)" % exc)

# %% [markdown]
# ## 7 · Scenes in, X3D → glTF (the Foundry's leg)
#
# Each scene is downloaded from its home (pinned commits; licences in the table). X3D scenes go to glTF through the lab's own Blender leg: `x3d-blender-io` import, Blender's glTF export, the **Khronos glTF validator**, and `x3dlab.formats.losses` (what glTF has no place for). *The `-gltf` scenes are controls*: Khronos samples that go straight into Unreal with no Blender step, so Unreal can be judged on the same file X_ITE draws.
#
# **Expected before Unreal** (found by notebook 06 Foundry with minimal repros; they cap the fidelity of whatever reaches Unreal): x3d-blender-io skips IndexedQuadSet and Arc2D · Sphere and Cylinder import flat-shaded · TwoSidedMaterial is dropped · strips and fans become plain faces, point sets are dropped. GLSL `ComposedShader`s (the Gobbler's whole look) cannot travel in glTF at all; DESIGN.md says what can be done about them.

# %%
#@title 7 · Download, read, convert { display-mode: "form" }
from x3dlab import blender as xblender, formats
names = [s.strip() for s in SCENES.split(",") if s.strip() in showcase.CATALOGUE]
display(HTML("<div style='font-size:12px'>%s</div>" % showcase.licence_table(names).replace("\n", "<br>")))
BL = None
CARD["blender_io"] = engines.use_blender_io(BLENDER_IO_REF, WORK, PRIMITIVE_DIVISIONS, colour_space=X3D_COLOURS)
COLOURS = CARD["blender_io"]["colour_space"]            # what the add-on really does; the Unreal leg follows it
LAB.mapping = convert.LightMapping(ev100=LAB.mapping.ev100, measured_k=LAB.mapping.k, colour_space=COLOURS)   # K kept
try:
    BL = xblender.setup()
    print("Blender %s for the X3D → glTF leg · add-on %s · %d segments per circle · colours %s" % (
        BL.version, CARD["blender_io"].get("why", "pinned in pins.toml"), PRIMITIVE_DIVISIONS, COLOURS))
except Exception as exc:
    print("⚠️ Blender unavailable: %s (X3D scenes are skipped; glTF controls still run)" % exc)
SCN = {}
for n in names:
    e = showcase.CATALOGUE[n]
    rec = {"name": n, "kind": e["kind"]}
    try:
        f = showcase.fetch(n, WORK / "scenes")
        rec.update(fetch=f)
        main = Path(f["main"])
        if e["kind"] == "x3d":
            xml, vp_names = x3dscene.ensure_viewpoint_defs(main.read_text(encoding="utf-8", errors="replace"))
            xml, rec["freeze"] = staging.freeze(xml, e.get("freeze_at", "auto"))       # dark at time 0? freeze it lit
            m0 = x3dscene.manifest(xml)
            rec["framing"] = staging.frame_all(m0)
            if staging.needs_auto(rec["framing"]) and m0.get("bounds"):                 # no authored viewpoint shows it
                xml, _ = staging.add_auto_viewpoint(xml, m0["bounds"])
                rec["framing"] = staging.frame_all(x3dscene.manifest(xml))
            main.write_text(xml, encoding="utf-8")
            rec["manifest"] = x3dscene.manifest(xml)
            if rec["freeze"].get("fraction") is not None:
                print("%s: %s (lights %.3g -> %.3g)" % (n, rec["freeze"]["why"], rec["freeze"]["lights_t0"], rec["freeze"]["lights_frozen"]))
            bad = {k: v["verdict"] for k, v in rec["framing"].items() if v["verdict"] not in ("frames", "inside")}
            if bad:
                print("%s: viewpoints that don't show the model: %s%s" % (n, bad, " · added x3dlab_auto" if "x3dlab_auto" in rec["framing"] else ""))
            if BL is not None:
                conv = engines.to_gltf(BL, main, main.with_suffix(".glb"))
                rec["convert"] = {k: conv.get(k) for k in ("error", "counts", "shared_meshes", "flat_meshes", "losses", "export_s", "bytes")}
                rec["validator"] = {k: (conv.get("validator") or {}).get(k) for k in ("ok", "errors", "warnings")}
                rec["glb"] = conv.get("output") if not conv.get("error") else None
                if USD == "auto" and not conv.get("error"):
                    u = engines.to_gltf(BL, main, main.with_suffix(".usdc"), fmt="usd")
                    rec["usd"] = u.get("output") if not u.get("error") else None
                    rec["usd_error"] = u.get("error")
        else:
            rec["glb"] = str(main)
            gs = gltfstat.stats(main)
            cs = gs["shape_centres"]
            lo = tuple(min(c[i] for c in cs) for i in range(3)) if cs else (-1, -1, -1)
            hi = tuple(max(c[i] for c in cs) for i in range(3)) if cs else (1, 1, 1)
            xml, vp = compare.gltf_control_x3d(main.name, (lo, hi))
            (main.parent / "control.x3d").write_text(xml, encoding="utf-8")
            m = x3dscene.manifest(xml)
            m["bounds"], m["shape_centres"] = (lo, hi), cs
            rec["manifest"], rec["wrapper"] = m, str(main.parent / "control.x3d")
        if rec.get("glb"):
            rec["gltf"] = gltfstat.stats(rec["glb"])
        c = rec["manifest"]["counts"]
        lo_ = (rec.get("convert") or {}).get("losses") or {}
        print("%-14s %5d nodes · %d shapes · %d viewpoints · %d lights · glTF %s%s" % (
            n, c["nodes"], c["shape_instances"], c["viewpoints"], c["lights"], "✓" if rec.get("glb") else "✗",
            ("  · expected losses: %s · unexpected: %s" % (", ".join(lo_.get("expected", [])[:6]) or "none", "; ".join(lo_.get("unexpected", [])) or "none")) if lo_ else ""))
        if rec.get("validator", {}).get("ok") is False:
            SC.add("7 scenes", "%s: glTF valid (Khronos)" % n, "WARN", "%s errors" % rec["validator"].get("errors"))
        SC.add("7 scenes", "%s: in and converted" % n, "PASS" if rec.get("glb") else "FAIL",
               "%d viewpoints, %s missing files%s" % (c["viewpoints"], len(f.get("missing", [])), "; " + (rec.get("convert") or {}).get("error", "") if (rec.get("convert") or {}).get("error") else ""))
    except Exception as exc:
        rec["error"] = str(exc)
        SC.add("7 scenes", "%s: in and converted" % n, "FAIL", str(exc)[:300])
    SCN[n] = rec
CARD["scenes"] = {n: {k: v for k, v in r.items() if k not in ("manifest",)} | {"counts": (r.get("manifest") or {}).get("counts"),
                  "traps": (r.get("manifest") or {}).get("traps")} for n, r in SCN.items()}

# %% [markdown]
# ## 8 · Into Unreal: import, account, calibrate
#
# One editor task per scene (a fresh process: no state leaks from one scene to the next): Interchange imports the glTF into a new level; the script measures what arrived, works out which axis mapping the importer used (from the shapes' centres, not by assumption), places the X3D viewpoints as cameras and the X3D lights in physical units, adds the backdrop (the X3D Background colour) and makes one sequence per viewpoint. Then the accounting: **glTF in vs Unreal out**, row by row.
#
# **Calibration first.** X3D light intensity has no unit; Unreal's are lux and candela, and the picture depends on exposure. The `calib` scene (one white box, one light of intensity 1, head-on) is rendered with no tone curve; its brightness gives the constant every later scene's lights are converted with. **Field of view:** X3D's applies to the *shorter* image side, Unreal's is horizontal; cameras get a filmback with the image's aspect and the matching focal length.

# %%
#@title 8 · Import every scene, calibrate the lights { display-mode: "form" }
IMP = {}
def do_import(n):
    r = SCN[n]
    src, gstats = r["glb"], r["gltf"]
    if n != "calib":
        dst = Path(src).with_name(Path(src).stem + "_unreal.glb")
        try:                                   # after the calibration, so the emission uses the measured K
            uc = ucopy.unreal_copy(src, dst, r["manifest"], k=LAB.mapping.k, colour_space=LAB.mapping.colour_space,
                                   line_radius=lines.radius_for(r["manifest"], px=LINE_PX), thicken=LINES_FOR_UNREAL,
                                   sky=BACKGROUND_GRADIENT)
            r["unreal_copy"] = uc
            lr = uc["lines"]
            if uc["changed"]:
                src, gstats = str(dst), gltfstat.stats(dst)
            if lr.get("changed"):
                SC.add("8 import", "%s: X3D lines and points as geometry" % n, "PASS",
                       "%d segments and %d points → %d triangles in %d colour%s%s (radius %.3g m, emission × K %.3g)%s" % (
                           lr["segments"], lr["points"], lr["triangles_added"], lr["colours"], "" if lr["colours"] == 1 else "s",
                           ", %d translucent" % lr["translucent"] if lr["translucent"] else "", lr["radius"], LAB.mapping.k,
                           "; skipped: %s" % "; ".join(lr["skipped"][:2]) if lr["skipped"] else ""))
            if uc["emission_scaled"]:
                SC.add("8 import", "%s: X3D emissive colours × K" % n, "PASS",
                       "%d emissive materials × K %.3g, so they land on screen at their X3D value (colours: %s)" % (
                           uc["emission_scaled"], LAB.mapping.k, uc["colour_space"]))
            if "radius_m" in uc["sky"]:
                SC.add("8 import", "%s: X3D Background gradient" % n, "PASS",
                       "a dome of %d triangles at %.0f m: zenith %s · horizon %s · nadir %s" % (
                           uc["sky"]["triangles"], uc["sky"]["radius_m"], uc["sky"]["colours"][0], uc["sky"]["colours"][2],
                           uc["sky"]["colours"][-1]))
        except Exception as exc:
            SC.add("8 import", "%s: the glb for Unreal" % n, "WARN", "the Foundry's glb as it is (lines dropped, emission × 1): %s" % str(exc)[:200])
    res = pipeline.import_scene(LAB, n, src, "gltf", r["manifest"], shape_centres=r["manifest"].get("shape_centres"))
    rep = res["report"]
    IMP[n] = res
    rows = report.account(gstats, rep, r["manifest"], rep)
    st = "PASS" if rep.get("ok") else "FAIL"
    SC.add("8 import", "%s: Interchange import" % n, st, "%s · %d sequences · %.0f s%s" % (
        "ok" if rep.get("ok") else "; ".join(rep.get("errors", [])[:2]) or "failed", len(rep.get("sequences", {})), res["seconds"],
        " · %d API names differ in this engine" % len(rep.get("api_misses", [])) if rep.get("api_misses") else ""))
    for row in rows:
        SC.add("8 import", "%s: %s" % (n, row["what"]), row["status"], row["reason"])
    if not rep.get("ok"):
        print("--- %s: the log lines that matter ---\n%s" % (n, res["excerpt"][-3000:]))
    if r.get("usd"):
        u = pipeline.import_scene(LAB, n, r["usd"], "usd", r["manifest"], shape_centres=r["manifest"].get("shape_centres"))
        IMP[n + "_usd"] = u
        SC.add("8 import", "%s: USD import (second path)" % n, "PASS" if u["report"].get("ok") else "WARN",
               "%s via %s" % ("ok" if u["report"].get("ok") else "; ".join(u["report"].get("errors", [])[:2]) or "failed", u["report"].get("usd_path")))
    return rows

CALIB = None
if ENGINE.get("version") and "calib" in SCN and SCN["calib"].get("glb"):
    do_import("calib")
    cams = sorted((IMP["calib"]["report"].get("sequences") or {}))
    rc = pipeline.render(LAB, "calib", cams, "preview", PLAN["ladder"]["preview"], look="match", res_override=(960, 540))
    png = next((s["png"] for s in rc["stills"] if s["png"]), None)
    if png:
        CALIB = pipeline.calibration(png, LAB.mapping.k, colour_space=LAB.mapping.colour_space)
        if CALIB["use"]:
            LAB.mapping = convert.LightMapping(measured_k=CALIB["k_measured"], colour_space=LAB.mapping.colour_space)
        SC.add("8 import", "light calibration (X3D intensity 1 → Unreal)", "PASS" if CALIB["use"] else "WARN",
               "box centre %.3f linear vs %.3f expected → K %s (assumed %.3f)" % (CALIB["centre_linear"], CALIB["expected_linear"],
                                                                                 CALIB["k_measured"], CALIB["k_assumed"]))
    else:
        SC.add("8 import", "light calibration (X3D intensity 1 → Unreal)", "FAIL", "no calibration still: %s" % rc["proof"].get("why"))
    CARD["calibration"] = {"result": CALIB, "mapping": LAB.mapping.describe()}
for n in SCN:
    if n != "calib" and ENGINE.get("version") and SCN[n].get("glb"):
        rows = do_import(n)
        print("%-14s " % n + " · ".join("%s %s→%s" % (r["what"].split(" ")[0], r["gltf"], r["unreal"]) for r in rows[:6]))
CARD["imports"] = {n: {"report": {k: v for k, v in r["report"].items() if k != "steps"}, "seconds": r["seconds"], "attempts": r["attempts"]}
                   for n, r in IMP.items()}

# %% [markdown]
# ## 9 · Stills: the ladder
#
# Every viewpoint (up to `MAX_VIEWPOINTS` per scene) through Movie Render Queue at the chosen rung, EXR + PNG. *preview* / *high* use Lumen; *ultra* uses the **path tracer** where the GPU has RT cores (G4, L4, T4) and Lumen at 8K with resident textures where it has the VRAM but no RT cores (A100). VRAM is sampled with `nvidia-smi` during each render; the time per still, the settings, engine version, GPU and driver go into the run card.

# %%
#@title 9 · Render { display-mode: "form" }
rung_name = RUNG if RUNG != "auto" else ("ultra" if HW["vram_gb"] >= 36 else "high")
if rung_name not in PLAN["ladder"]:
    rung_name = "ultra"
RUNG_USED = PLAN["ladder"][rung_name]
print("rung %s: %dx%d · %s · %s spatial x %s temporal samples · textures %s" % (rung_name, *RUNG_USED["resolution"], RUNG_USED["renderer"],
      RUNG_USED["spatial_samples"], RUNG_USED["temporal_samples"], RUNG_USED["texture_streaming"]))
RND = {}
for n, res in IMP.items():
    if n == "calib" or n.endswith("_usd") or not res["report"].get("ok"):
        continue
    cams = staging.pick(sorted(res["report"]["sequences"]), SCN.get(n, {}).get("framing", {}), MAX_VIEWPOINTS)
    out = pipeline.render(LAB, n, cams, rung_name, RUNG_USED, look=LOOK)
    RND[n] = out
    ok = [s for s in out["stills"] if s["png"] or s["exr"]]
    p = out["proof"]
    gpu = out.get("gpu") or {}
    per = [s["seconds"] for s in out["stills"] if s.get("seconds")]
    SC.add("9 render", "%s: stills" % n, "PASS" if len(ok) == len(cams) and cams else "FAIL" if not ok else "WARN",
           "%d/%d viewpoints · %s · %.0f s per still (first %.0f s) · peak VRAM %.1f GB" % (
               len(ok), len(cams), p.get("why"), (sum(per[1:]) / len(per[1:])) if len(per) > 1 else (per[0] if per else 0),
               per[0] if per else 0, (gpu.get("peak_mem_mb") or 0) / 1024))
    if not ok:
        print("--- %s: the log lines that matter ---\n%s" % (n, out["excerpt"][-3000:]))
    for s in ok[:2]:
        if s["png"] and not (LAB.live and s["png"] in LAB.live.shown):      # VISUAL already showed it live
            display(IPImage(filename=s["png"], width=640))
CARD["renders"] = {n: {"rung": rung_name, "settings": RUNG_USED, "seconds": r["seconds"], "gpu": r.get("gpu"), "proof": r["proof"],
                       "stills": [{k: v for k, v in s.items() if k != "files"} for s in r["stills"]], "attempts": r["attempts"]}
                   for n, r in RND.items()}

# %% [markdown]
# ## 10 · Unreal vs X_ITE vs Cycles
#
# The same viewpoints, the same size, on the lab's flat magenta key background: Unreal (match look, key backdrop), X_ITE in headless Chrome on the GPU (the scene with its ROUTEs removed, so every engine shows the authored, time-0 state), and Cycles for the first viewpoint. Judged with **`x3dlab.formats.compare_pngs`** (`match`: silhouette agreement forgiving 2 pixels; colour delta and its 64/255 threshold, because Phong vs PBR alone moves colours by 30–50) plus SSIM. The contact sheet puts them side by side.

# %%
#@title 10 · Compare and contact sheet { display-mode: "form" }
CMP, SHEET = {}, None
if COMPARE and RND:
    from x3dlab import browser as xbrowser
    HB = xbrowser.start(gpu=True, viewport=(960, 540))
    print("X_ITE renderer:", HB.info.get("webgl") if hasattr(HB, "info") else "?")
    rows = []
    for n, out in RND.items():
        r = SCN[n]
        cams = [s["viewpoint"] for s in out["stills"]]
        key = pipeline.render(LAB, n, cams, "preview", PLAN["ladder"]["preview"], look="match", key=True, res_override=(960, 540))
        scene_dir = Path(r["fetch"]["main"]).parent
        main_name = Path(r["wrapper"]).name if r["kind"] == "gltf" else Path(r["fetch"]["main"]).name
        xi = engines.xite_views(HB, scene_dir, main_name, r["manifest"], size=(960, 540))
        cy = engines.cycles_first_view(BL, r["fetch"]["main"], WORK / "out" / ("cycles_%s.png" % n)) if (BL and r["kind"] == "x3d") else {}
        vps = {project.ue_name(x3dscene.viewpoint_name(v)): x3dscene.viewpoint_name(v) for v in r["manifest"]["viewpoints"]}
        for i, s in enumerate(out["stills"]):
            ks = next((k for k in key["stills"] if k["viewpoint"] == s["viewpoint"]), {})
            ue_key = Path(ks["png"]).read_bytes() if ks.get("png") else None
            x = (xi.get(vps.get(s["viewpoint"], s["viewpoint"])) or {}).get("png")
            j = compare.judge(x, ue_key, formats) if (x and ue_key) else {}
            jc = compare.judge(cy.get("png_key"), ue_key, formats) if (i == 0 and cy.get("png_key") and ue_key) else {}
            CMP["%s/%s" % (n, s["viewpoint"])] = {"xite": j, "cycles": jc}
            SC.add("10 compare", "%s/%s vs X_ITE" % (n, s["viewpoint"]), compare.verdict(j) if j else "SKIP",
                   ("match %.3f · IoU %.3f · SSIM %s · colour Δ %s%s" % (j["match"], j["iou"], j.get("ssim"), j.get("colour_delta"),
                    " (%s)" % j["colour_kind"] if j.get("colour_kind") else "")) if j.get("match") is not None else "a still is missing")
            rows.append({"label": "%s · %s" % (n, s["viewpoint"]), "cells": [
                ("Unreal %s (%s)" % (rung_name, LOOK), Path(s["png"]).read_bytes() if s.get("png") else None),
                ("Unreal key · match %s" % (j.get("match") if j else "–"), ue_key), ("X_ITE", x),
                ("Cycles (first viewpoint)", cy.get("png_key") if i == 0 else None)]})
    SHEET = compare.contact_sheet(rows, WORK / "out" / "contact_sheet.png")
    display(IPImage(filename=str(SHEET), width=1000))
    HB.close()
else:
    SC.add("10 compare", "engines compared", "SKIP", "COMPARE is off" if not COMPARE else "no Unreal stills")
CARD["compare"] = CMP

# %% [markdown]
# ## 11 · Scorecard, run card, Drive
#
# Everything in one table; the run card (JSON) records the GPU, driver, engine, settings, every attempt and every number above. Stills, the contact sheet, the logs and the run card are copied to `DRIVE_DIR/runs/<time>/`; the shader cache (DDC) goes to `DRIVE_DIR/cache/` so the next run skips most of the compile.

# %%
#@title 11 · Scorecard and save { display-mode: "form" }
RUN_ID = time.strftime("%Y%m%d-%H%M%S")
CARD.update(scorecard=SC.rows, summary=SC.counts(), attempts=LAB.attempts, run_id=RUN_ID, light_mapping=LAB.mapping.describe())
CARD_PATH = report.run_card(WORK / "out" / ("run_card_%s.json" % RUN_ID), CARD)
display(HTML("<div style='font:17px system-ui;margin:6px 0'><b>07 Unreal Fidelity on %s: %s</b></div>" % (HW["kind"], SC.word())))
display(HTML(SC.html()))
if Path(DRIVE_DIR).exists():
    import shutil
    dst = Path(DRIVE_DIR) / "runs" / RUN_ID
    for sub in ("stills", "logs", "scenes"):
        if (WORK / "out" / sub).exists():
            shutil.copytree(WORK / "out" / sub, dst / sub, dirs_exist_ok=True)
    for f in [CARD_PATH, SHEET]:
        if f:
            shutil.copy(f, dst)
    print("saved to", dst)
    if CACHE_DDC and ENGINE.get("version"):
        print("DDC to Drive:", pipeline.ddc_save(LAB, DRIVE_DIR, ENGINE.get("version")))
print("run card:", CARD_PATH)

# %% [markdown]
# ## 12 · What this run proved

# %%
#@title 12 · What this run proved { display-mode: "form" }
def said(part, check):
    r = next((r for r in SC.rows if r["part"] == part and r["check"] == check), None)
    return (r["status"], r["reason"]) if r else ("SKIP", "not run")
lines = []
s, why = said("6 start", "RHI is Vulkan on the NVIDIA GPU")
lines.append("**Unreal on Colab's GPU, headless:** %s: %s." % (s, why))
s, why = said("4 Vulkan", "ray tracing decision")
lines.append("**Ray tracing on this %s:** %s: %s." % (HW["kind"], s, why))
s, why = said("6 start", "one-time shader compile (cold start)")
lines.append("**First-run cost:** %s." % why)
ok_imp = [n for n, r in IMP.items() if r["report"].get("ok")]
lines.append("**Scenes imported:** %d of %d (%s)." % (len(ok_imp), len(IMP), ", ".join(ok_imp) or "none"))
ok_r = [n for n, r in RND.items() if any(s["png"] for s in r["stills"])]
lines.append("**Scenes rendered at %s:** %s." % (RUNG if RUNG != "auto" else "the auto rung", ", ".join(ok_r) or "none"))
if CMP:
    ms = [v["xite"]["match"] for v in CMP.values() if v.get("xite", {}).get("match") is not None]
    if ms:
        lines.append("**Silhouettes vs X_ITE:** median match %.3f over %d views (1.0 = same outline within 2 px)." % (sorted(ms)[len(ms) // 2], len(ms)))
if CALIB:
    lines.append("**Light units:** X3D intensity 1 → %s (calibrated K = %s)." % (LAB.mapping.describe()["rule"], CALIB.get("k_measured")))
c = SC.counts()
lines.append("**Scorecard:** %d PASS · %d WARN · %d FAIL · %d SKIP. Every FAIL row says why; the run card has the logs' key lines." % (c["PASS"], c["WARN"], c["FAIL"], c["SKIP"]))
display(HTML("<div style='font:14px system-ui;line-height:1.5'>" + "<br>".join(
    __import__("re").sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", l) for l in lines) + "</div>"))

# %% [markdown]
# ## Troubleshooting
# <!-- collapsed -->
#
# | You see | Why | Do this |
# |---|---|---|
# | step 4: *no libnvidia-gl at exactly …* | the apt sources lack the driver's exact version | nothing to do safely; a different version would break CUDA. Try another runtime (the driver differs per GPU type) |
# | step 4: user `ue` sees no NVIDIA device | the ICD was not unpacked, or `/dev/nvidia*` is not readable by `ue` | rerun step 4; `ls -l /dev/nvidia*` |
# | step 5: *no engine found* | no zip or cache for `ENGINE_VERSION` in `DRIVE_DIR`, no `GHCR_TOKEN` | the message lists the zips it saw; pick one of those versions, or see *Your one-time step* |
# | step 6: rc 139, call stack in `ShaderFormatVersion` | the project asked for a shader format this engine lacks (UE 5.2 has no Vulkan SM6 on Linux) | automatic: retried with Shader Model 5 and no ray tracing; or use 5.4+ |
# | step 5: ghcr HTTP 401/403 | the token lacks `read:packages`, or the GitHub account is not linked to Epic / the EpicGames invitation is not accepted | fix the token or the link, rerun step 5 |
# | step 5: *not enough local disk* | the trimmed engine does not fit | a runtime with a bigger disk (A100/G4), or remove old files in `/content` |
# | step 6: `FAIL` *NullRHI* or *software Vulkan* | Unreal did not get the NVIDIA ICD | the run already pins `VK_ICD_FILENAMES` to NVIDIA's; check step 4's rows |
# | step 6: `device_lost` | GPU crash; UE 5.8.0 had such reports on Linux | the notebook retries: async compute off → no ray tracing → Shader Model 5. Use 5.8.4 |
# | step 6: *bindless* assertion | ray tracing on Vulkan needs bindless | retried automatically with `BindlessSupport=AllShaderTypes` |
# | step 6 takes very long | the one-time shader compile; few vCPUs or little RAM make it slower | let it run; `CACHE_DDC` makes the next run fast |
# | step 8: `api_misses` in the import report | a Python API name differs in this engine version | the import continues; tell the lab which names (they are in the run card) |
# | step 9: no stills, log says *Movie Render* errors | MRQ did not start | the report says which job; `x3dlab_render.py` also has a `highresshot` mode (set `mode="highresshot"` in `pipeline.render`) |
# | Colab disconnects | RAM exhausted | the watchdog stops Unreal at 94 % RAM; pick High-RAM, or fewer scenes |
