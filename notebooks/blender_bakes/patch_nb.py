"""Patch giza_bakes_g4.ipynb (build 18751c82) -> v2: Blender 5.3.0 or 5.1.2 switch, current add-on, any NVIDIA GPU.
Embedded build sources (cell 4) are not touched, so their hashes still hold."""
import json, sys, hashlib
src, dst, addon = sys.argv[1], sys.argv[2], sys.argv[3]
nb = json.load(open(src))
def get(i):
    s = nb['cells'][i]['source']; return s if isinstance(s, str) else ''.join(s)
def put(i, s): nb['cells'][i]['source'] = s
c4 = hashlib.sha256(get(4).encode()).hexdigest()

md = get(0)
md = md.replace("# Blenderizer bakes on the G4  (build 18751c82)", "# Blenderizer bakes on the G4 or A100  (build 18751c82, notebook v2)")
md = md.replace("1. Runtime ▸ Change runtime type ▸ **G4 GPU**.", "1. Runtime ▸ Change runtime type ▸ **G4 GPU** if you can get it, otherwise **A100** (any NVIDIA GPU works for these bakes).")
a = md[md.index("**Download.**"):md.index("**Cost.**")]
md = md.replace(a, "**Download.** Cell 3 fetches the Linux build of Blender from download.blender.org (5.3.0 is 407 MB, 5.1.2 is 396 MB) "
  "and clones x3d-blender-io at `%s`. Pick the Blender version in cell 3: **5.3.0** is the default, **5.1.2** is the fallback "
  "if anything misbehaves.\n\n**Device.** Cell 5 `DEVICE = \"AUTO\"` asks Blender what the GPU offers and takes OptiX if it is there, "
  "otherwise CUDA. Cell 6 stops with ❌ if a GPU was asked for and the bake fell back to the CPU.\n\n" % addon)
put(0, md)

put(1, '''#@title 1. What machine did we get? { display-mode: "form" }
import os, subprocess, multiprocessing
GPU = subprocess.run("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader", shell=True, capture_output=True, text=True).stdout.strip()
print(GPU or "❌ no NVIDIA GPU: Runtime ▸ Change runtime type ▸ G4 or A100")
print("CPUs:", multiprocessing.cpu_count())
if GPU:
    print("✅ good for the bakes." + ("" if "RTX" in GPU else " This card has no ray-tracing cores, so OptiX may be missing; cell 5 AUTO then uses CUDA."))
''')

c3 = get(3)
c3 = c3.replace('#@title 3. Blender 5.1.2 and x3d-blender-io { display-mode: "form" }',
  '#@title 3. Blender and x3d-blender-io { display-mode: "form" }\nBLENDER_VERSION = "5.3.0"  #@param ["5.3.0", "5.1.2"]\nADDON_COMMIT = "%s"  #@param {type:"string"}' % addon)
c3 = c3.replace('BL = ROOT / "blender" / "blender"', 'BLDIR = ROOT / ("blender-" + BLENDER_VERSION)\nBL = BLDIR / "blender"')
c3 = c3.replace('(ROOT / "blender").mkdir(parents=True, exist_ok=True)', 'BLDIR.mkdir(parents=True, exist_ok=True)')
old = 'sh("curl -sSL https://download.blender.org/release/Blender5.1/blender-5.1.2-linux-x64.tar.xz | tar -xJ -C %s --strip-components=1" % (ROOT / "blender"))'
assert old in c3
c3 = c3.replace(old, 'sh("set -o pipefail; curl -fsSL https://download.blender.org/release/Blender%s/blender-%s-linux-x64.tar.xz | tar -xJ -C %s --strip-components=1" % (BLENDER_VERSION.rsplit(".", 1)[0], BLENDER_VERSION, BLDIR))')
old = 'sh(["git", "-C", str(ADDON), "checkout", "-q", "47ed742"])'
assert old in c3
c3 = c3.replace(old, 'sh(["git", "-C", str(ADDON), "fetch", "-q", "origin"], check=False)\nsh(["git", "-C", str(ADDON), "checkout", "-q", ADDON_COMMIT])')
c3 = c3.replace('print("✅ Blender and the add-on are ready")', 'print("✅ Blender", BLENDER_VERSION, "and the add-on at", ADDON_COMMIT, "are ready")')
c3 = c3.replace('r = subprocess.run(cmd, shell=isinstance(cmd, str), text=True)', 'r = subprocess.run(cmd, shell=isinstance(cmd, str), text=True, executable="/bin/bash" if isinstance(cmd, str) else None)')
put(3, c3)

put(5, '''#@title 5. Choose the job and the profile { display-mode: "form" }
JOB = "crawl"  #@param ["crawl", "masonic", "fall", "twins"]
PROFILE = "quick"  #@param ["quick", "g4"]
DEVICE = "AUTO"  #@param ["AUTO", "OPTIX", "CUDA", "CPU"]
if DEVICE == "AUTO":
    probe = ("import bpy\\np = bpy.context.preferences.addons['cycles'].preferences\\nfor t in ('OPTIX', 'CUDA'):\\n"
             "    try:\\n        p.compute_device_type = t\\n        p.refresh_devices() if hasattr(p, 'refresh_devices') else p.get_devices()\\n"
             "        print('PROBE', t, [d.name for d in p.devices if d.type == t])\\n    except Exception as e:\\n        print('PROBE', t, [], e)\\n")
    r = subprocess.run([str(BL), "-b", "--python-expr", probe], capture_output=True, text=True).stdout
    found = {l.split()[1]: "[]" not in l for l in r.splitlines() if l.startswith("PROBE")}
    print("\\n".join(l for l in r.splitlines() if l.startswith("PROBE")))
    DEVICE = "OPTIX" if found.get("OPTIX") else "CUDA" if found.get("CUDA") else "CPU"
print(("✅" if DEVICE != "CPU" else "❌ no GPU backend found, this would bake on the CPU:"), "device", DEVICE, "| job", JOB, "| profile", PROFILE, "| Blender", BLENDER_VERSION)
''')

c6 = get(6)
old = 'out = ROOT / "out" / ("%s-%s" % (JOB, PROFILE))'
assert old in c6
c6 = c6.replace(old, 'out = ROOT / "out" / ("%s-%s-b%s" % (JOB, PROFILE, BLENDER_VERSION.replace(".", "")))')
old = '''            print(line.rstrip()[:200])
    p.wait()'''
assert old in c6
c6 = c6.replace(old, '''            print(line.rstrip()[:200])
        if line.startswith("BAKE DEVICE CPU") and DEVICE != "CPU":
            p.kill()
            print("❌ asked for", DEVICE, "but Blender fell back to the CPU. Stopped. Set DEVICE to CUDA in cell 5 and run cells 5 and 6 again.")
    p.wait()''')
put(6, c6)

c7 = get(7)
old = '''if dst.exists():
    shutil.rmtree(dst)
'''
assert old in c7
c7 = c7.replace(old, '''if dst.exists():   # keep the earlier run: move it aside instead of deleting it
    dst.rename(dst.with_name(dst.name + time.strftime("-prev-%Y%m%d-%H%M%S")))
''')
put(7, c7)
nb['metadata']['colab']['gpuType'] = 'G4'
assert hashlib.sha256(get(4).encode()).hexdigest() == c4
json.dump(nb, open(dst, 'w'), ensure_ascii=False, indent=1)
for i in (1, 3, 5, 6, 7):
    compile(''.join(l for l in get(i).splitlines(True)), 'cell%d' % i, 'exec')
print('ok', dst)
