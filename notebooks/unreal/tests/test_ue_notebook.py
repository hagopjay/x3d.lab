import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UNREAL = REPO / "notebooks" / "unreal"


def test_notebook_is_built_and_current():
    r = subprocess.run([sys.executable, str(REPO / "tools" / "build_notebooks.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    nb = json.loads((UNREAL / "07_Unreal_Fidelity.ipynb").read_text())
    assert nb["metadata"]["accelerator"] == "GPU"
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert code and all(c["metadata"].get("cellView") == "form" for c in code)
    for c in code:
        ast.parse("".join(c["source"]))


def test_notebook_uses_the_foundry_leg_and_never_bundles_the_engine():
    src = (UNREAL / "src" / "07_unreal.py").read_text()
    assert "from x3dlab import blender as xblender, formats" in src and "engines.to_gltf" in src
    assert "formats" in (UNREAL / "x3due" / "engines.py").read_text()
    assert "unrealengine.com/linux" in src and "read:packages" in src
    me = Path(__file__).resolve()
    every = "\n".join(p.read_text(errors="replace") for p in UNREAL.rglob("*")
                      if p.is_file() and p.resolve() != me and p.suffix in (".py", ".md", ".ini", ".uproject", ".ipynb"))
    assert not re.search(r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail)\.com", every)       # no personal e-mail anywhere
    assert not re.search(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{10,}", every)          # no GitHub token


def test_every_scorecard_row_in_the_notebook_has_a_reason():
    src = (UNREAL / "src" / "07_unreal.py").read_text()
    for m in re.finditer(r"SC\.add\(([^\n]+)", src):
        args = m.group(1)
        assert args.count(",") >= 3, args
