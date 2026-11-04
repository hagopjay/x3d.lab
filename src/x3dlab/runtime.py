"""Where are we running, and on what hardware? Used to pick defaults and recorded on every run card."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def in_colab() -> bool:
    try:
        import google.colab  # noqa: F401
        return True
    except Exception:
        return False


def home() -> Path:
    """Root for everything the lab writes: /content/x3dlab on Colab, ~/.x3dlab elsewhere (or $X3DLAB_HOME)."""
    root = os.environ.get("X3DLAB_HOME")
    if root:
        p = Path(root)
    elif Path("/content").is_dir() and in_colab():
        p = Path("/content/x3dlab")
    else:
        p = Path.home() / ".x3dlab"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(*parts: str) -> Path:
    p = home().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class GPU:
    name: str
    memory_mb: int
    driver: str
    kind: str  # T4, L4, A100, H100, G4 (RTX PRO 6000), or other


@dataclass
class Runtime:
    python: str
    platform: str
    colab: bool
    cpus: int
    ram_gb: float
    disk_free_gb: float
    gpus: list = field(default_factory=list)

    @property
    def gpu_kind(self) -> str:
        return self.gpus[0].kind if self.gpus else "CPU"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["gpu_kind"] = self.gpu_kind
        return d

    def line(self) -> str:
        g = ", ".join("%s %d GB" % (x.name, round(x.memory_mb / 1024)) for x in self.gpus) or "no GPU"
        return "Python %s | %s CPUs | %.0f GB RAM | %s | %s" % (
            self.python, self.cpus, self.ram_gb, g, "Colab" if self.colab else "not Colab")


def _kind(name: str) -> str:
    n = name.upper()
    if "RTX PRO 6000" in n or "RTX PRO6000" in n:
        return "G4"
    for k in ("H100", "A100", "L4", "T4", "V100", "A10G", "L40"):
        if k in n:
            return k
    return "other"


def gpus() -> list[GPU]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    found = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                mem = int(float(parts[1]))
            except ValueError:
                mem = 0
            found.append(GPU(parts[0], mem, parts[2], _kind(parts[0])))
    return found


def detect() -> Runtime:
    try:
        import psutil
        ram = psutil.virtual_memory().total / 2**30
    except Exception:
        ram = 0.0
    return Runtime(
        python=sys.version.split()[0],
        platform=platform.platform(terse=True),
        colab=in_colab(),
        cpus=os.cpu_count() or 1,
        ram_gb=round(ram, 1),
        disk_free_gb=round(shutil.disk_usage(home()).free / 2**30, 1),
        gpus=gpus(),
    )
