"""Give headless Chrome (and Cycles' OptiX denoiser) the GPU on Colab: install the graphics half of the NVIDIA driver.

Colab's containers carry NVIDIA's *compute* userspace (CUDA, NVML, OptiX ray tracing) but not the *graphics* one: no
Vulkan ICD, no GLX/EGL libraries, no OptiX denoiser model (nvoptix.bin). Chrome then falls back to SwiftShader (WebGL
and WebGPU on the CPU) and Cycles' OptiX denoiser cannot start. Installing `libnvidia-gl-<branch>` at exactly the
driver's version adds them; Chrome then runs ANGLE on Vulkan on the real GPU when started with GPU_ARGS (which include
--disable-vulkan-surface: headless Chrome has no window surface to give Vulkan).

    from x3dlab import graphics
    print(graphics.enable())        # installs only an exact version match; never a different driver version

Idea and flags from Google's "Supercharge Web AI model testing" notebook (developer.chrome.com/blog/supercharge-web-ai-testing).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from .events import bus


def _run(cmd: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def driver_version() -> str | None:
    """The kernel driver's version as nvidia-smi reports it (e.g. 580.82.07), or None without an NVIDIA GPU."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], 30).stdout.split()
        return out[0] if out else None
    except Exception:
        return None


def parse_madison(text: str) -> list[tuple[str, str]]:
    """`apt-cache madison` lines -> [(package, version)]."""
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append((parts[0], parts[1]))
    return out


def pick_package(driver: str, candidates: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The package whose version is exactly the driver's (580.82.07 matches 580.82.07-0ubuntu1, not 580.82.09-...).
    Userspace and kernel driver must match exactly; a near miss breaks CUDA too, so there is no fallback."""
    pat = re.compile(r"^(\d+:)?%s([-~+].*)?$" % re.escape(driver))
    hits = [(p, v) for p, v in candidates if pat.match(v)]
    # prefer the desktop branch package over -server when both match (either works; same libraries)
    hits.sort(key=lambda pv: ("-server" in pv[0], pv[1]))
    return hits[0] if hits else None


def candidates(driver: str) -> list[tuple[str, str]]:
    major = driver.split(".")[0]
    out = []
    for pkg in ("libnvidia-gl-%s" % major, "libnvidia-gl-%s-server" % major):
        r = _run(["apt-cache", "madison", pkg], 120)
        out += parse_madison(r.stdout)
    return out


def vulkan_devices() -> list[str]:
    """Device names vulkaninfo sees ([] if vulkaninfo is missing or finds none)."""
    if not shutil.which("vulkaninfo"):
        return []
    r = _run(["vulkaninfo", "--summary"], 120)
    return re.findall(r"deviceName\s*=\s*(.+)", r.stdout)


def status() -> dict:
    icds = []
    for d in ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d"):
        if os.path.isdir(d):
            icds += [os.path.join(d, f) for f in sorted(os.listdir(d))]
    optix = [p for p in ("/usr/share/nvidia/nvoptix.bin", "/usr/lib/x86_64-linux-gnu/nvoptix.bin") if os.path.exists(p)]
    return {"driver": driver_version(), "nvidia_vulkan_icd": [p for p in icds if "nvidia" in p.lower()],
            "nvoptix_bin": optix, "vulkan_devices": vulkan_devices()}


def enable(install: bool = True, update: bool = True) -> dict:
    """Install the matching libnvidia-gl package (and vulkan-tools) if the GPU's graphics userspace is missing.
    Safe to call anywhere: without an NVIDIA GPU, without apt, or without an exact match it changes nothing."""
    st = status()
    rep = {"before": st, "installed": None, "skipped": None}
    if not st["driver"]:
        rep["skipped"] = "no NVIDIA GPU"
        return rep
    if st["nvidia_vulkan_icd"] and any(not re.search(r"llvmpipe|swiftshader|software", d, re.I) for d in st["vulkan_devices"]):
        rep["skipped"] = "already enabled"
        rep["after"] = st
        return rep
    if not shutil.which("apt-get") or os.geteuid() != 0:
        rep["skipped"] = "needs apt-get as root (Colab has both)"
        return rep
    if update:
        _run(["apt-get", "update", "-qq"], 600)
    pick = pick_package(st["driver"], candidates(st["driver"]))
    if not pick:
        rep["skipped"] = ("no libnvidia-gl package at exactly %s in the apt sources; installing another version would "
                          "break the driver, so nothing was installed" % st["driver"])
        bus.warn(rep["skipped"], src="graphics")
        return rep
    if install:
        with bus.span("install %s=%s + vulkan-tools" % pick, src="graphics"):
            r = _run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", "%s=%s" % pick, "vulkan-tools"], 900)
        rep["installed"] = "%s=%s" % pick
        if r.returncode:
            rep["error"] = (r.stderr or r.stdout)[-600:]
    rep["after"] = status()
    ok = bool(rep["after"]["nvidia_vulkan_icd"])
    bus.info("NVIDIA graphics userspace %s: Vulkan devices %s, nvoptix.bin %s" % (
        "enabled" if ok else "NOT enabled", rep["after"]["vulkan_devices"] or "none", "yes" if rep["after"]["nvoptix_bin"] else "no"),
        src="graphics", data=rep)
    return rep
