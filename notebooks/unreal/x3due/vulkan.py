"""Give the container NVIDIA's Vulkan driver without touching its CUDA userspace (the route confirmed on a Colab A100,
10 Nov 2026).

Colab starts containers with NVIDIA_DRIVER_CAPABILITIES=compute,utility: CUDA works, but there is no Vulkan ICD,
no GLX/EGL libraries and no nvoptix.bin. `apt-get install libnvidia-gl-<branch>` fails ("held broken packages"):
it would replace Colab's CUDA userspace. What works: `apt-get download` exactly libnvidia-gl-<branch>,
libnvidia-gpucomp-<branch> and libnvidia-common-<branch> at the driver's exact version, `dpkg -x` each into /,
`ldconfig`, then `apt-get install vulkan-tools`. Never hand-write an ICD JSON, never install another driver version.

    cmds = vulkan.unpack_plan("580.82.07", madison)     # pure: the commands, for a dry run or a test
    rep = vulkan.enable()                               # runs them when needed (root + apt + NVIDIA GPU)
    vk = vulkan.probe(user="ue")                        # devices and extensions, as the user Unreal will run as
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile

ICD_DIRS = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d")
OPTIX_BIN = ("/usr/share/nvidia/nvoptix.bin",)
PACKAGES = ("libnvidia-gl-{b}", "libnvidia-gpucomp-{b}", "libnvidia-common-{b}")
REQUIRED = ("libnvidia-gl-{b}", "libnvidia-common-{b}")   # gpucomp does not exist for older branches
SOFTWARE = re.compile(r"llvmpipe|lavapipe|swiftshader|softpipe|software", re.I)


def _run(cmd, timeout=600, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def parse_madison(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 2 and p[0] and p[1]:
            out.append((p[0], p[1]))
    return out


def exact_version(driver: str, versions: list[str]) -> str | None:
    """The package version that is exactly the driver's (580.82.07 -> 580.82.07-0ubuntu1; never 580.82.09 or 580.196.07)."""
    pat = re.compile(r"^(\d+:)?%s([-~+].*)?$" % re.escape(driver))
    hits = sorted(v for v in versions if pat.match(v))
    return hits[0] if hits else None


def unpack_plan(driver: str, madison: dict[str, str], workdir: str = "/tmp/x3due_nvgl") -> dict:
    """Commands for the unpack route. `madison` maps package name -> `apt-cache madison <pkg>` output.
    Returns {"ok", "commands", "packages", "why"}; never picks a version that is not the driver's exact one."""
    branch = driver.split(".")[0]
    picks, missing = {}, []
    for flavour in ("", "-server"):
        picks, missing = {}, []
        for tmpl in PACKAGES:
            name = tmpl.format(b=branch) + flavour
            if flavour and tmpl == "libnvidia-common-{b}":
                name = tmpl.format(b=branch) + "-server"
            vers = [v for p, v in parse_madison(madison.get(name, "")) if p == name]
            v = exact_version(driver, vers)
            if v:
                picks[name] = v
            elif tmpl in REQUIRED:
                missing.append(name)
        if not missing:
            break
    if missing:
        return {"ok": False, "commands": [], "packages": picks,
                "why": "no %s at exactly %s in the apt sources; installing another version would break the driver, so "
                       "nothing is installed" % (", ".join(missing), driver)}
    cmds = [["mkdir", "-p", workdir],
            ["apt-get", "download"] + ["%s=%s" % kv for kv in picks.items()]]
    cmds += [["dpkg", "-x", "{deb}", "/"]]           # one per downloaded .deb, expanded at run time
    cmds += [["ldconfig"], ["apt-get", "install", "-y", "-qq", "--no-install-recommends", "vulkan-tools"]]
    return {"ok": True, "commands": cmds, "packages": picks, "workdir": workdir, "why": "exact match"}


def status() -> dict:
    icds = []
    for d in ICD_DIRS:
        if os.path.isdir(d):
            icds += [os.path.join(d, f) for f in sorted(os.listdir(d))]
    return {"nvidia_icd": [p for p in icds if "nvidia" in p.lower()], "icds": icds,
            "nvoptix_bin": [p for p in OPTIX_BIN if os.path.exists(p)],
            "vulkaninfo": shutil.which("vulkaninfo"),
            "driver_capabilities": os.environ.get("NVIDIA_DRIVER_CAPABILITIES", "")}


def driver_version() -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], 30).stdout.split()
        return out[0] if out else None
    except Exception:
        return None


# ---------------------------------------------------------------------- vulkaninfo parsing
def parse_summary(text: str) -> list[dict]:
    """`vulkaninfo --summary` -> [{deviceName, vendorID, deviceType, driverName, apiVersion, driverVersion}]."""
    devs, cur = [], None
    for line in text.splitlines():
        if re.match(r"\s*GPU\d+:", line):
            cur = {}
            devs.append(cur)
            continue
        m = re.match(r"\s*(\w+)\s*=\s*(.+?)\s*$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    return devs


def parse_extensions(text: str) -> list[list[str]]:
    """Full `vulkaninfo` output -> device extension lists, one per device, in device order."""
    out, cur, in_dev = [], None, False
    for line in text.splitlines():
        if re.match(r"\s*Device Extensions\s*:?\s*count\s*=\s*\d+", line):
            cur, in_dev = [], True
            out.append(cur)
            continue
        if in_dev:
            m = re.match(r"\s*(VK_\w+)\s*:\s*extension revision", line)
            if m:
                cur.append(m.group(1))
            elif line.strip() and not line.strip().startswith(("-", "=")) and not m:
                if not re.match(r"\s*VK_", line):
                    in_dev = False
    return out


def probe(user: str | None = None, summary_text: str | None = None, full_text: str | None = None) -> dict:
    """Devices Vulkan sees (as `user` if given: the account Unreal runs under) and the NVIDIA device's extensions."""
    if summary_text is None:
        if not shutil.which("vulkaninfo"):
            return {"devices": [], "nvidia_device": None, "software_only": False, "error": "vulkaninfo not installed"}
        pre = ["runuser", "-u", user, "--"] if user and os.geteuid() == 0 else []
        summary_text = _run(pre + ["vulkaninfo", "--summary"], 120).stdout
        full_text = _run(pre + ["vulkaninfo"], 300).stdout
    devs = parse_summary(summary_text)
    exts = parse_extensions(full_text or "")
    for i, d in enumerate(devs):
        d["extensions"] = exts[i] if i < len(exts) else []
        d["software"] = bool(SOFTWARE.search(d.get("deviceName", "") + d.get("driverName", ""))) or d.get("vendorID", "").lower() == "0x10005"
    nv = next((d for d in devs if d.get("vendorID", "").lower() == "0x10de" or "nvidia" in d.get("deviceName", "").lower()), None)
    return {"devices": devs, "nvidia_device": nv, "software_only": bool(devs) and all(d["software"] for d in devs)}


def enable(dry_run: bool = False, update: bool = True) -> dict:
    """Unpack the graphics half of the driver when Vulkan cannot see the NVIDIA GPU yet. Safe anywhere: without an
    NVIDIA GPU, root or apt it changes nothing; it never installs a version other than the driver's."""
    st = status()
    rep = {"before": st, "driver": driver_version(), "done": [], "skipped": None}
    if not rep["driver"]:
        rep["skipped"] = "no NVIDIA GPU (nvidia-smi missing or silent)"
        return rep
    if st["nvidia_icd"] and st["vulkaninfo"] and probe()["nvidia_device"]:
        rep["skipped"] = "already enabled: Vulkan sees the NVIDIA GPU"
        rep["after"] = st
        return rep
    if not shutil.which("apt-get") or os.geteuid() != 0:
        rep["skipped"] = "needs apt-get as root (Colab has both)"
        return rep
    if update and not dry_run:
        _run(["apt-get", "update", "-qq"], 900)
    branch = rep["driver"].split(".")[0]
    names = [t.format(b=branch) + s for t in PACKAGES for s in ("", "-server")]
    madison = {n: _run(["apt-cache", "madison", n], 120).stdout for n in names}
    plan = unpack_plan(rep["driver"], madison)
    rep["plan"] = plan
    if not plan["ok"] or dry_run:
        rep["skipped"] = plan["why"] if not plan["ok"] else "dry run"
        return rep
    work = tempfile.mkdtemp(prefix="x3due_nvgl_")
    os.chmod(work, 0o777)                         # apt's _apt user downloads into it (else a harmless warning)
    r = _run(plan["commands"][1], 900, cwd=work)
    rep["done"].append({"cmd": " ".join(plan["commands"][1]), "rc": r.returncode, "err": r.stderr[-400:]})
    debs = sorted(glob.glob(os.path.join(work, "*.deb")))
    if r.returncode or not debs:
        rep["error"] = "apt-get download failed: " + (r.stderr or r.stdout)[-600:]
        return rep
    for deb in debs:                              # files only: apt's database is not changed, CUDA stays as it is
        x = _run(["dpkg", "-x", deb, "/"], 300)
        rep["done"].append({"cmd": "dpkg -x %s /" % os.path.basename(deb), "rc": x.returncode, "err": x.stderr[-300:]})
    _run(["ldconfig"], 120)
    v = _run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", "vulkan-tools"], 900)
    rep["done"].append({"cmd": "apt-get install vulkan-tools", "rc": v.returncode, "err": v.stderr[-300:]})
    rep["after"] = status()
    return rep
