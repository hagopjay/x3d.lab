"""What actually ran: every component of the toolchain, its version and where it came from.

The rule that keeps the lab robust is that each heavy tool runs in its own process on the runtime it was built for
(Blender on its own Python 3.13, the bridge runner and the glTF validator on Node, X_ITE in Playwright's Chromium), so
the notebook's Python barely matters. This module says which of those were found, at which version, and from where
(the bpy wheel or the Blender download; the system Node or the pinned one; Chrome on the GPU or on SwiftShader), and
every run card records it.

    from x3dlab import toolchain
    print(toolchain.line(toolchain.report(lab)))
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from . import __version__, pins


def _run(cmd: list, timeout: float = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def _node() -> dict:
    node = shutil.which("node")
    v = _run([node, "--version"]).lstrip("v") if node else ""
    from . import runtime
    pinned = runtime.home() / "vendor" / ("node-v%s-linux-x64" % pins.get("x3d_loom", "node", "22.11.0")) / "bin" / "node"
    if pinned.exists() and not (v and v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 20):
        return {"version": _run([str(pinned), "--version"]).lstrip("v"), "source": "pinned download"}
    return {"version": v or None, "source": "system" if v else "missing (downloaded when needed)"}


def _gpu() -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    out = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    return out.splitlines()[0] if out else None


def _module_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def report(lab=None) -> dict:
    """{component: {version, source, ...}} for this machine (and the lab's Blender and browser when given)."""
    out = {
        "python": {"version": sys.version.split()[0], "source": sys.executable},
        "x3dlab": {"version": __version__},
        "gpu": {"name": _gpu() or "none (CPU runtime)"},
        "x3d_mcp": {"version": (pins.get("x3d_mcp", "ref") or "")[:12], "source": pins.get("x3d_mcp", "repo")},
        "x3d_blender_io": {"version": (pins.get("x3d_blender_io", "ref") or "")[:12], "source": pins.get("x3d_blender_io", "repo")},
        "x3d_loom": {"version": (pins.get("x3d_loom", "ref") or "")[:12], "source": pins.get("x3d_loom", "repo")},
        "node": _node(),
        "usd_core": {"version": _module_version("usd-core")},
        "playwright": {"version": _module_version("playwright")},
    }
    bl = getattr(lab, "blender", None) if lab is not None else None
    if bl is not None:
        info = getattr(bl, "info", {}) or {}
        out["blender"] = {"version": getattr(bl, "version", "") or info.get("blender"),
                          "source": "bpy wheel in this Python" if len(getattr(bl, "cmd", [])) == 1 else "Blender download (own Python %s)" % (info.get("python") or "3.13"),
                          "cycles": info.get("best")}
    else:
        out["blender"] = {"version": None, "source": "not set up (lab.setup_blender())", "pinned": pins.get("python", "bpy")}
    br = getattr(lab, "browser", None) if lab is not None else None
    if br is not None and getattr(br, "info", None):
        i = br.info
        out["chrome"] = {"version": i.get("chromium"), "x_ite": i.get("xite"), "webgl": i.get("webgl"),
                         "source": "GPU" if i.get("gpu") else "SwiftShader (CPU)"}
    else:
        out["chrome"] = {"version": None, "x_ite": pins.get("x_ite", "version"), "source": "not started"}
    return out


def line(rep: dict) -> str:
    """One line for a notebook cell."""
    def v(k, f="version"):
        return (rep.get(k) or {}).get(f) or "-"
    parts = ["Python %s" % v("python"), "x3dlab %s" % v("x3dlab"), "GPU %s" % v("gpu", "name"),
             "Blender %s (%s)" % (v("blender"), v("blender", "source")),
             "Chrome %s (%s) · X_ITE %s" % (v("chrome"), v("chrome", "source"), v("chrome", "x_ite")),
             "Node %s (%s)" % (v("node"), v("node", "source")), "usd-core %s" % v("usd_core"),
             "x3d_mcp %s · blender-io %s · loom %s" % (v("x3d_mcp"), v("x3d_blender_io"), v("x3d_loom"))]
    return " | ".join(parts)


if __name__ == "__main__":     # python -m x3dlab.toolchain
    print(line(report()))
