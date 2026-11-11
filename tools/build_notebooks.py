"""Build the .ipynb notebooks from their percent-format sources in notebooks/src/.

The sources are the files to edit: they diff cleanly in review. Run after any change:

    python tools/build_notebooks.py          # writes notebooks/*.ipynb
    python tools/build_notebooks.py --check  # fails if a notebook is out of date (CI)
"""

import argparse
import re
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "notebooks" / "src"
OUT = ROOT / "notebooks"
NAMES = {"00_starter.py": ("00_MCP_Starter.ipynb", None), "00_basic_mcp.py": ("00_Basic_MCP.ipynb", None), "01_quickstart.py": ("01_Quickstart.ipynb", None),
         "02_lab.py": ("02_Lab.ipynb", "GPU"), "03_research.py": ("03_Research.ipynb", "GPU"),
         "04_duel.py": ("04_Model_Duel.ipynb", None), "05_hog.py": ("05_GPU_Hog.ipynb", "GPU"),
         "00_gpu_playground.py": ("00_GPU_Browser_Playground.ipynb", "GPU"),
         "06_foundry.py": ("06_Foundry.ipynb", None),
         "07_unreal.py": ("unreal/07_Unreal_Fidelity.ipynb", "GPU")}   # name -> (output under notebooks/, accelerator)
UNREAL_SRC = ROOT / "notebooks" / "unreal" / "src"   # the Unreal leg keeps its sources next to its own files


COLLAPSED = "<!-- collapsed -->"   # put this line in a markdown cell under its heading to fold that section


def parse(text: str) -> list:
    cells, kind, buf = [], None, []

    def flush():
        if kind is None:
            return
        body = "\n".join(buf).strip("\n")
        if kind == "markdown":
            body = "\n".join(re.sub(r"^# ?", "", ln) for ln in body.splitlines())
            cells.append(nbformat.v4.new_markdown_cell(body))
        else:
            cells.append(nbformat.v4.new_code_cell(body))

    for line in text.splitlines():
        if line.startswith("# %%"):
            flush()
            kind, buf = ("markdown" if "[markdown]" in line else "code"), []
        else:
            buf.append(line)
    flush()
    return cells


def build(src: Path) -> tuple[Path, str]:
    out_name, accel = NAMES[src.name]
    nb = nbformat.v4.new_notebook()
    nb.cells = parse(src.read_text(encoding="utf-8"))
    collapsed = []
    for i, c in enumerate(nb.cells):
        c["id"] = "%s-%02d" % (src.stem.replace("_", "-"), i)
        if c.cell_type == "code":
            c.metadata["cellView"] = "form"           # Colab shows the title and form, the code stays hidden
        elif COLLAPSED in c.source:                   # a heading whose section starts folded in Colab
            c.source = "\n".join(ln for ln in c.source.splitlines() if ln.strip() != COLLAPSED)
            collapsed.append(c["id"])
    nb.metadata = {"colab": {"provenance": [], "include_colab_link": True, "toc_visible": True,
                             **({"collapsed_sections": collapsed} if collapsed else {})},
                   "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}}
    if accel:
        nb.metadata["accelerator"] = accel
    nbformat.validate(nb)
    return OUT / out_name, nbformat.writes(nb) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    stale = []
    for src in sorted(SRC.glob("*.py")) + sorted(UNREAL_SRC.glob("*.py")):
        path, text = build(src)
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
            print("wrote", path.relative_to(ROOT))
    if stale:
        print("out of date (run python tools/build_notebooks.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
