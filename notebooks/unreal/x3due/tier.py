"""Which GPU is this, what can it do for Unreal, and which settings ladder follows from that.

    hw = tier.detect()                 # nvidia-smi + /proc + disk: GPU, VRAM, driver, CUDA, vCPUs, RAM, free disk
    plan = tier.plan(hw, vk)           # vk = vulkan.probe(): the device's real extensions decide ray tracing
    plan["ladder"]["ultra"]            # resolution, samples, renderer, console variables, pools

The capability table is a prior; the Vulkan device's extensions at run time are the decision. A GPU whose table row
says "no RT cores" but whose driver still lists ray-tracing extensions gets software Lumen only (emulated ray tracing
would be very slow), unless force_hwrt is set.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

# Sources (checked 10 Nov 2026):
#  A100: NVIDIA A100 Tensor Core GPU Architecture whitepaper, Table 4 note: "it does not include display connectors,
#        NVIDIA RT Cores for ray tracing acceleration, or an NVENC encoder"
#  NVENC counts: NVIDIA Video Encode and Decode GPU Support Matrix (A100 0, T4 1, L4 2, RTX PRO 6000 Blackwell SE 4, H100 0)
#  T4: NVIDIA T4 datasheet ("RT Cores 40", 16 GB GDDR6)
#  L4: NVIDIA L4 datasheet / product page (third-generation RT cores, 24 GB, 2 NVENC)
#  RTX PRO 6000 Blackwell Server Edition: NVIDIA datasheet (96 GB GDDR7, 188 fourth-generation RT cores, 4 NVENC)
#  Colab "G4" = RTX PRO 6000 (nvidia-smi on Colab, reported by mccormickml.com, Mar 2026)
CAPS = {
    "T4": {"arch": "Turing", "vram_gb": 16, "rt_cores": True, "rt_note": "40 RT cores (1st gen)", "nvenc": 1,
           "source": "NVIDIA T4 datasheet; NVENC/NVDEC support matrix"},
    "L4": {"arch": "Ada Lovelace", "vram_gb": 24, "rt_cores": True, "rt_note": "3rd-gen RT cores", "nvenc": 2,
           "source": "NVIDIA L4 datasheet; NVENC/NVDEC support matrix"},
    "A100": {"arch": "Ampere (GA100)", "vram_gb": None, "rt_cores": False, "rt_note": "no RT cores", "nvenc": 0,
             "source": "NVIDIA A100 architecture whitepaper (Table 4 note); NVENC/NVDEC support matrix"},
    "H100": {"arch": "Hopper", "vram_gb": 80, "rt_cores": False, "rt_note": "no RT cores (unverified here)", "nvenc": 0,
             "source": "NVENC/NVDEC support matrix (0 NVENC); RT cores: unverified"},
    "G4": {"arch": "Blackwell", "vram_gb": 96, "rt_cores": True, "rt_note": "188 4th-gen RT cores", "nvenc": 4,
           "source": "NVIDIA RTX PRO 6000 Blackwell Server Edition datasheet; NVENC/NVDEC support matrix"},
}
RT_EXTENSIONS = ("VK_KHR_ray_tracing_pipeline", "VK_KHR_acceleration_structure")
RT_HELPFUL = ("VK_KHR_ray_query", "VK_KHR_ray_tracing_position_fetch", "VK_KHR_deferred_host_operations")


def kind_of(name: str) -> str:
    n = (name or "").upper()
    if "RTX PRO 6000" in n or "RTX PRO6000" in n:
        return "G4"
    for k in ("H100", "A100", "L4", "T4"):
        if re.search(r"\b%s\b" % k, n) or k in n.replace("-", " ").split():
            return k
    for k in ("H100", "A100", "L4", "T4"):
        if k in n:
            return k
    return "other"


def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def parse_smi_query(text: str) -> list[dict]:
    """`nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap,pci.device_id --format=csv,noheader,nounits`"""
    out = []
    for line in text.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 3 or not p[0]:
            continue
        try:
            mem = int(float(p[1]))
        except ValueError:
            mem = 0
        out.append({"name": p[0], "memory_mib": mem, "driver": p[2], "compute_cap": p[3] if len(p) > 3 else "",
                    "pci_device_id": p[4] if len(p) > 4 else "", "kind": kind_of(p[0])})
    return out


def parse_smi_header(text: str) -> dict:
    m = re.search(r"Driver Version:\s*([\d.]+)", text)
    c = re.search(r"CUDA Version:\s*([\d.]+)", text)
    return {"driver": m.group(1) if m else None, "cuda": c.group(1) if c else None}


def meminfo(text: str | None = None) -> dict:
    if text is None:
        try:
            text = open("/proc/meminfo").read()
        except OSError:
            return {}
    d = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            d[m.group(1)] = int(m.group(2)) / 2**20
    return {"ram_gb": round(d.get("MemTotal", 0), 1), "ram_available_gb": round(d.get("MemAvailable", 0), 1)}


def detect(smi_query: str | None = None, smi_header: str | None = None, meminfo_text: str | None = None,
           cpus: int | None = None, disk_path: str = "/content") -> dict:
    """Everything the plan needs. Pass recorded texts to test without a GPU."""
    if smi_query is None and shutil.which("nvidia-smi"):
        smi_query = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap,pci.device_id",
                          "--format=csv,noheader,nounits"])
        smi_header = _run(["nvidia-smi"])
    gpus = parse_smi_query(smi_query or "")
    hdr = parse_smi_header(smi_header or "")
    path = disk_path if os.path.isdir(disk_path) else os.path.expanduser("~")
    du = shutil.disk_usage(path)
    hw = {"gpus": gpus, "gpu": gpus[0] if gpus else None, "driver": (gpus[0]["driver"] if gpus else None) or hdr["driver"],
          "cuda": hdr["cuda"], "cpus": cpus or os.cpu_count() or 1, **meminfo(meminfo_text),
          "disk_path": path, "disk_free_gb": round(du.free / 2**30, 1), "disk_total_gb": round(du.total / 2**30, 1),
          "driver_capabilities": os.environ.get("NVIDIA_DRIVER_CAPABILITIES", "")}
    hw["kind"] = hw["gpu"]["kind"] if hw["gpu"] else "CPU"
    hw["vram_gb"] = round(hw["gpu"]["memory_mib"] / 1024, 1) if hw["gpu"] else 0
    return hw


# ---------------------------------------------------------------------- ladders
def shader_workers(cpus: int, ram_gb: float) -> int:
    """Shader-compile workers that fit: the editor keeps ~10 GB, each ShaderCompileWorker takes ~2 GB at peak
    (rule of thumb, unverified). Never more than cpus - 1."""
    by_ram = int(max(0.0, ram_gb - 10) // 2)
    return max(1, min(max(1, cpus - 1), by_ram if by_ram else 1))


BASE_CVARS = {                     # stills of a static scene: nothing that smears or depends on motion
    "r.MotionBlurQuality": 0, "r.DepthOfFieldQuality": 0, "r.SceneColorFringeQuality": 0, "r.Tonemapper.GrainQuantization": 0,
    "r.ScreenPercentage": 100,
}


def _preset(res, spatial, temporal, renderer, **kw):
    d = {"resolution": list(res), "spatial_samples": spatial, "temporal_samples": temporal, "renderer": renderer,
         "tiles": 1, "warmup_frames": 32, "cvars": dict(BASE_CVARS)}
    d.update(kw)
    return d


def ladder(kind: str, vram_gb: float, hw_rt: bool) -> dict:
    """preview / high / ultra for this GPU. Big VRAM buys resolution and resident pools, not tiling (tiling in Movie
    Render Queue disables temporal anti-aliasing and screen-space effects, per Epic's MRQ image-quality page)."""
    v = vram_gb or 0
    if v >= 90:                       # G4 (96 GB)
        res = [(1920, 1080), (3840, 2160), (7680, 4320)]
    elif v >= 70:                     # A100 80 GB, H100
        res = [(1920, 1080), (3840, 2160), (7680, 4320)]
    elif v >= 36:                     # A100 40 GB
        res = [(1920, 1080), (3840, 2160), (5120, 2880)]
    elif v >= 20:                     # L4
        res = [(1600, 900), (2560, 1440), (3840, 2160)]
    else:                             # T4 or smaller
        res = [(1280, 720), (1920, 1080), (2560, 1440)]
    pools = {"r.Streaming.PoolSize": int(min(0.35 * v * 1024, 32768)) if v else 1000,
             "r.Nanite.Streaming.StreamingPoolSize": int(min(max(512, 0.04 * v * 1024), 4096)) if v else 512,
             "r.VT.PoolSizeScale": 8 if v >= 70 else 4 if v >= 36 else 2 if v >= 20 else 1}
    resident = v >= 36                # load every texture at full resolution instead of streaming
    lumen = {"sg.GlobalIlluminationQuality": 4, "sg.ReflectionQuality": 4, "sg.ShadowQuality": 4, "sg.PostProcessQuality": 4,
             "r.Lumen.HardwareRayTracing": 1 if hw_rt else 0}
    out = {
        "preview": _preset(res[0], 1, 8, "lumen", cvars={**BASE_CVARS, **lumen}),
        "high": _preset(res[1], 1, 32, "lumen", cvars={**BASE_CVARS, **lumen, **pools}),
        "ultra": _preset(res[2], 2, 64, "lumen", cvars={**BASE_CVARS, **lumen, **pools}),
    }
    if hw_rt:                         # the path tracer needs hardware ray tracing (Epic's path tracer page)
        spp = 1024 if v >= 70 else 512 if v >= 20 else 256
        out["ultra"] = _preset(res[2], spp, 1, "pathtracer",
                               cvars={**BASE_CVARS, **pools, "r.PathTracing.MaxBounces": 16, "r.PathTracing.Denoiser": 1})
        out["ultra_lumen"] = _preset(res[2], 2, 64, "lumen", cvars={**BASE_CVARS, **lumen, **pools,
                                                                       "r.Lumen.HardwareRayTracing.LightingMode": 2})
    for p in out.values():
        p["texture_streaming"] = "fully_load" if resident else "default"
    return out


def vulkan_decision(kind: str, extensions: list | None, force_hwrt: bool = False) -> dict:
    caps = CAPS.get(kind, {"rt_cores": None})
    ext = set(extensions or [])
    has_ext = all(e in ext for e in RT_EXTENSIONS)
    table = caps.get("rt_cores")
    if extensions is None:
        return {"hw_rt": False, "status": "SKIP", "why": "no Vulkan device seen, so no ray tracing (software Lumen only)",
                "extensions": {}}
    if has_ext and (table or force_hwrt):
        st, why, on = "PASS", "device lists %s%s" % (" + ".join(RT_EXTENSIONS), " (forced)" if force_hwrt and not table else ""), True
    elif has_ext and table is False:
        st, why, on = "WARN", ("driver lists the ray-tracing extensions but the %s has no RT cores (emulated, slow): "
                               "software Lumen only; set FORCE_HWRT to try anyway" % kind), False
    elif has_ext:
        st, why, on = "WARN", "unknown GPU %s lists the ray-tracing extensions: hardware ray tracing on" % kind, True
    elif table:
        st, why, on = "FAIL", ("the %s has RT cores but its Vulkan device lacks %s: the unpacked libnvidia-gl may not "
                               "match the driver" % (kind, ", ".join(e for e in RT_EXTENSIONS if e not in ext))), False
    else:
        st, why, on = "PASS", "no RT cores and no ray-tracing extensions: software Lumen, as expected for %s" % kind, False
    return {"hw_rt": on, "status": st, "why": why,
            "extensions": {e: e in ext for e in RT_EXTENSIONS + RT_HELPFUL}}


def plan(hw: dict, vk: dict | None = None, force_hwrt: bool = False) -> dict:
    kind = hw.get("kind", "CPU")
    exts = None
    if vk and vk.get("nvidia_device"):
        exts = vk["nvidia_device"].get("extensions")
    dec = vulkan_decision(kind, exts, force_hwrt)
    caps = CAPS.get(kind, {})
    v = hw.get("vram_gb", 0)
    notes = []
    if kind == "A100":
        notes.append("A100: no RT cores and no NVENC, so no hardware ray tracing, no path tracer and no Pixel Streaming "
                     "encoder; it wins on VRAM: 8K stills without tiling and resident texture/Nanite/VT pools.")
    if kind == "G4":
        notes.append("G4 (RTX PRO 6000 Blackwell, 96 GB): the most VRAM and RT cores of the Colab GPUs: hardware "
                     "ray tracing, the path tracer and the biggest stills. The best Colab GPU for Unreal fidelity.")
    if kind == "T4":
        notes.append("T4: RT cores, but 16 GB and (on the standard runtime) few vCPUs and ~13 GB RAM: the first shader "
                     "compile can take hours; prefer a High-RAM runtime or L4/A100/G4.")
    if hw.get("ram_gb", 0) and hw["ram_gb"] < 24:
        notes.append("Only %.0f GB RAM: shader workers capped at %d; Unreal recommends 32 GB." %
                     (hw["ram_gb"], shader_workers(hw.get("cpus", 1), hw["ram_gb"])))
    return {"kind": kind, "caps": caps, "vram_gb": v, "ray_tracing": dec,
            "shader_workers": shader_workers(hw.get("cpus", 1), hw.get("ram_gb", 0) or 8),
            "ladder": ladder(kind, v, dec["hw_rt"]), "notes": notes}


def capability_table_markdown() -> str:
    rows = ["| Colab GPU | Architecture | VRAM | RT cores | NVENC | Unreal hardware RT / path tracer | Source |",
            "|---|---|---|---|---|---|---|"]
    for k, c in CAPS.items():
        vram = "40 / 80 GB" if k == "A100" else "%d GB" % c["vram_gb"]
        rows.append("| %s | %s | %s | %s | %d | %s | %s |" % (k, c["arch"], vram, c["rt_note"], c["nvenc"],
                                                             "yes, if Vulkan lists the extensions" if c["rt_cores"] else "no (software Lumen)",
                                                             c["source"]))
    return "\n".join(rows)
