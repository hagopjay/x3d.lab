"""Rebuild the scratch layout the prototype was tested in, then run its tests (and, with --mutants, its mutation check).

    python3 -I docs/traceability/prototype/run.py --lab . --engine /path/to/x3dtrace.py [--mutants]

The engine is the single standard-library file src/x3dtrace.py of hagopjay/x3d.ML, or src/x3d_utils/x3dtrace.py of the x3d_mcp fork
once the pin in src/x3dlab/pins.toml reaches a commit that has it. Nothing under --lab is modified: the patch is applied to a copy."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--lab", default=".")
ap.add_argument("--engine", required=True)
ap.add_argument("--mutants", action="store_true")
a = ap.parse_args()
lab, here = Path(a.lab).resolve(), Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp(prefix="receipt-proto-"))
(tmp / "engine").mkdir()
(tmp / "lab_copy").mkdir()
shutil.copy(a.engine, tmp / "engine" / "x3dtrace.py")
shutil.copy(lab / "notebooks/unreal/x3due/uerun.py", tmp / "lab_copy" / "uerun.py")
shutil.copy(lab / "notebooks/unreal/tests/fixtures/samples.py", tmp / "lab_copy" / "samples.py")
(tmp / "proto").mkdir()
shutil.copytree(here / "tests", tmp / "proto" / "tests")
for f in ("receipt.py", "mutation_check.py"):
    shutil.copy(here / f, tmp / "proto" / f)
work = tmp / "patchwork" / "notebooks/unreal/x3due"
work.mkdir(parents=True)
shutil.copy(lab / "notebooks/unreal/x3due/uerun.py", work / "uerun.py")
subprocess.run(["git", "apply", str(here / "uerun-evidence.diff")], cwd=tmp / "patchwork", check=True)
shutil.copy(work / "uerun.py", tmp / "proto" / "uerun_patched.py")
env = {"LABDEMO": str(tmp), "PATH": "/usr/bin:/bin:/usr/local/bin"}
rc = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "/dev/null", "--rootdir", str(tmp / "proto"), str(tmp / "proto" / "tests")],
                    cwd=tmp / "proto", env=env).returncode
if rc == 0 and a.mutants:
    rc = subprocess.run([sys.executable, "-I", "-B", str(tmp / "proto" / "mutation_check.py"), str(tmp)], cwd=tmp / "proto", env=env).returncode
sys.exit(rc)
