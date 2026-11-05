"""Fetch the pinned repos and install what the x3d_mcp server needs, idempotently."""

from __future__ import annotations

import importlib.metadata as md
import os
import subprocess
import sys
from pathlib import Path

from . import pins, runtime
from .events import bus

SERVER_DEPS = {
    "mcp": "mcp>=1.29,<2",
    "x3d": "x3d>=4.0.65.5,<5",
    "lxml": "lxml>=6.0",
    "xmlschema": "xmlschema>=4.3",
    "jsonschema": "jsonschema>=4.26",
    "rdflib": "rdflib>=7.0",
    "numpy": "numpy>=1.26",
    "zstandard": "zstandard>=0.23",
    "brotli": "brotli>=1.2.0",
}


def sh(cmd: list, cwd: str | Path | None = None, check: bool = True, timeout: float = 900) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError("command failed (%d): %s\n%s" % (r.returncode, " ".join(map(str, cmd)), (r.stdout + r.stderr)[-1500:]))
    return r


def github_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        try:
            from google.colab import userdata
            tok = userdata.get("GITHUB_TOKEN")
        except Exception:
            tok = None
    if tok:
        bus.add_secret(tok)
    return tok


def clone(repo: str, ref: str, dest: Path, token: str | None = None) -> str:
    """Clone (or update) `repo` into `dest` and check out `ref`. Returns the short commit id."""
    url = "https://%sgithub.com/%s.git" % (("x-access-token:%s@" % token) if token else "", repo)
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with bus.span("git clone %s" % repo, src="install"):
            sh(["git", "clone", "-q", "--filter=blob:none", url, str(dest)])
    have = sh(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    if not have.startswith(ref) and ref not in ("main", "master", "HEAD"):
        sh(["git", "fetch", "-q", "origin", ref], cwd=dest, check=False)
    sh(["git", "checkout", "-q", "-f", ref], cwd=dest)
    sh(["git", "clean", "-fdq"], cwd=dest)
    short = sh(["git", "rev-parse", "--short", "HEAD"], cwd=dest).stdout.strip()
    bus.info("%s at %s" % (repo, short), src="install")
    return short


def _installed(dist: str) -> str | None:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return None


def missing_server_deps() -> list[str]:
    need = []
    for dist, spec in SERVER_DEPS.items():
        v = _installed(dist)
        if v is None:
            need.append(spec)
        elif dist == "mcp" and int(v.split(".")[0]) >= 2:
            need.append(spec)        # 2.x removed mcp.server.fastmcp, which the server imports
    return need


def pip(specs: list[str]) -> None:
    if not specs:
        return
    with bus.span("pip install %s" % " ".join(specs), src="install"):
        sh([sys.executable, "-m", "pip", "install", "-q", *specs])


def x3d_mcp(ref: str | None = None) -> Path:
    """The pinned fork, checked out under the lab's home. Returns its src/ directory."""
    repo = pins.get("x3d_mcp", "repo")
    ref = ref or pins.get("x3d_mcp", "ref")
    dest = runtime.sub("repos") / "x3d_mcp"
    if os.environ.get("X3DLAB_X3D_MCP_DIR"):          # tests and local development: use an existing checkout
        return Path(os.environ["X3DLAB_X3D_MCP_DIR"]) / "src"
    clone(repo, ref, dest, github_token())
    return dest / "src"


def upstream(ref: str | None = None) -> Path:
    """The Web3D Consortium's x3d_mcp at its pinned commit (a second validator for the 03 notebook). Returns src/."""
    if os.environ.get("X3DLAB_UPSTREAM_DIR"):
        return Path(os.environ["X3DLAB_UPSTREAM_DIR"]) / "src"
    dest = runtime.sub("repos") / "x3d_mcp_upstream"
    clone(pins.get("x3d_mcp", "upstream"), ref or pins.get("x3d_mcp", "upstream_ref"), dest)
    return dest / "src"


def server_ready() -> Path:
    pip(missing_server_deps())
    return x3d_mcp()
