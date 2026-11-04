"""The pinned versions every notebook runs against (src/x3dlab/pins.toml)."""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    import tomli as tomllib

PINS_FILE = Path(__file__).with_name("pins.toml")


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(PINS_FILE, "rb") as fh:
        return tomllib.load(fh)


def pins() -> dict:
    """A copy of pins.toml as a dict (safe to modify)."""
    return copy.deepcopy(_load())


def get(section: str, key: str, default=None):
    return _load().get(section, {}).get(key, default)


def xite_url(version: str | None = None) -> str:
    v = version or get("x_ite", "version")
    return get("x_ite", "cdn").format(version=v)


def summary() -> dict:
    """Short form for run cards and the dashboard header."""
    p = _load()
    return {
        "x3d_mcp": "%s@%s" % (p["x3d_mcp"]["repo"], p["x3d_mcp"]["ref"][:7]),
        "x3d_blender_io": "%s@%s" % (p["x3d_blender_io"]["repo"], p["x3d_blender_io"]["ref"][:7]),
        "x_ite": p["x_ite"]["version"],
        "mcp": p["python"]["mcp"],
    }
