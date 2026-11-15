"""The Colab G4 cell files (notebooks/unreal/colab_g4) stay loadable: every "# %% " cell parses on its own, the loaders find the
titles the README names, and the Leg E scenes are well-formed X3D with one Viewpoint each."""
import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "colab_g4"
CELL_FILES = sorted(HERE.glob("*.py"))


def cells(path):
    return path.read_text(encoding="utf-8").split("\n# %% ")


@pytest.mark.parametrize("path", CELL_FILES, ids=lambda p: p.name)
def test_every_cell_parses(path):
    ast.parse(path.read_text(encoding="utf-8"))
    for c in cells(path)[1:]:
        ast.parse("#" + c)


@pytest.mark.parametrize("name,steps", [
    ("gobbler_cells.py", ["G1", "G2", "G3", "G4", "G5", "G6"]),
    ("banger_cells.py", ["J1", "J2"]),
    ("tier1_proof_cells.py", ["F0", "F1"]),
    ("vulkan_encore.py", ["V1", "V2"]),
])
def test_loader_finds_steps(name, steps):
    titles = [c.split("\n", 1)[0] for c in cells(HERE / name)[1:]]
    for s in steps:
        assert any(t.startswith(s) for t in titles), (name, s, titles)


def test_tier1_bounds_are_fixed():
    src = (HERE / "tier1_proof_cells.py").read_text(encoding="utf-8")
    assert '"silhouette_iou": 0.95' in src and '"spread_margin": 0.05' in src and '"repeat": 0.02' in src


@pytest.mark.parametrize("path", sorted((HERE / "scenes").glob("*.x3d")), ids=lambda p: p.name)
def test_leg_e_scenes(path):
    root = ET.parse(path).getroot()
    assert root.tag == "X3D" and root.get("version") == "4.0"
    assert len(root.findall(".//Viewpoint")) == 1


def test_loaders_do_not_shadow_notebook_modules():
    # notebook 07 imports x3due modules by these names; a loader called `tier` broke step 3 on 15 Nov 2026
    taken = ("tier", "pipeline", "project", "staging", "engines", "compare", "vulkan", "lines")
    for path in CELL_FILES:
        src = path.read_text(encoding="utf-8")
        for name in taken:
            assert "def %s(" % name not in src, (path.name, name)
