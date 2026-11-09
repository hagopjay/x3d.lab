"""Run notebooks/00_Basic_MCP.ipynb headless and report its smoke-test tally. Track A: no x3dlab import.

    python tools/run_basic.py                         # fork server, no tunnel, uv if installed else pip
    python tools/run_basic.py --installer pip         # hide uv from the kernel to exercise the plain-pip branch
    python tools/run_basic.py --server consortium     # upstream Web3DConsortium/x3d_mcp
    python tools/run_basic.py --tunnel cloudflared    # also try a trycloudflare tunnel (needs outbound network)
    python tools/run_basic.py --tunnel ngrok          # needs NGROK_AUTHTOKEN; skipped cleanly without it

Exit code 0 when the notebook ran to the end with 0 FAIL rows; 1 otherwise; 2 on usage errors.
The tunnel step is skipped (exit 0, "tunnel: SKIPPED") when the token or the network is missing, so CI stays green offline.
The notebook needs network access to clone the server (github.com) and install its dependencies (PyPI).
"""

import argparse
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "00_Basic_MCP.ipynb"


def have_network(host: str = "github.com", port: int = 443) -> bool:
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except OSError:
        return False


def patch(source: str, args, work: str) -> str:
    """Set the settings cell (cell 1) the way a Colab user would with the form fields."""
    for name, value in (("SERVER", repr(args.server)), ("TUNNEL", repr(args.tunnel)), ("RUN_SLOW_TOOLS", repr(args.slow)),
                        ("LOCAL_XITE", repr(args.local_xite)), ("LOAD_SESSIONS", str(args.sessions)),
                        ("LOAD_CALLS_PER_SESSION", str(args.calls))):
        source, n = re.subn(r"^%s = .*?(\s+#@param.*)$" % name, lambda m: "%s = %s%s" % (name, value, m.group(1)), source, count=1, flags=re.M)
        if n != 1:
            raise SystemExit("settings cell: could not find %s" % name)
    source, n = re.subn(r'os\.path\.expanduser\("~/x3d_basic"\)', repr(work), source)
    if n != 1:
        raise SystemExit("settings cell: could not find the work directory")
    return source


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notebook", default=str(NOTEBOOK))
    ap.add_argument("--server", default="hagopjay", choices=["hagopjay", "consortium"])
    ap.add_argument("--tunnel", default="none", choices=["none", "cloudflared", "ngrok"])
    ap.add_argument("--installer", default="auto", choices=["auto", "uv", "pip"], help="pip: remove uv from PATH inside the kernel")
    ap.add_argument("--slow", action="store_true", help="RUN_SLOW_TOOLS: also call render_image/render_current_scene (needs Chromium)")
    ap.add_argument("--local-xite", action="store_true")
    ap.add_argument("--sessions", type=int, default=16)
    ap.add_argument("--calls", type=int, default=10)
    ap.add_argument("--work", default=None, help="work dir (default: a fresh temp dir)")
    ap.add_argument("--cell-timeout", type=int, default=900)
    ap.add_argument("--keep", action="store_true", help="keep the executed notebook next to the report")
    args = ap.parse_args()

    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    nb = nbformat.read(args.notebook, as_version=4)
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    if re.search(r"\bx3dlab\b", code):
        print("FAIL: the Track A notebook imports x3dlab; it must stay self-contained")
        return 1
    print("track A check: no x3dlab reference in any code cell")

    if args.tunnel == "ngrok" and not os.environ.get("NGROK_AUTHTOKEN"):
        print("tunnel: SKIPPED (NGROK_AUTHTOKEN is not set); running with TUNNEL='none'")
        args.tunnel = "none"
    elif args.tunnel == "cloudflared" and not have_network("github.com"):
        print("tunnel: SKIPPED (no outbound network to github.com for the cloudflared download); running with TUNNEL='none'")
        args.tunnel = "none"
    if not have_network("github.com"):
        print("FAIL: no network to github.com; the notebook clones the server there")
        return 1

    work = args.work or tempfile.mkdtemp(prefix="x3d_basic_")
    Path(work).mkdir(parents=True, exist_ok=True)
    nb.cells[[i for i, c in enumerate(nb.cells) if c.cell_type == "code"][0]].source = patch(
        [c for c in nb.cells if c.cell_type == "code"][0].source, args, work)

    # A kernel that is exactly this interpreter, so "sys.executable" inside the notebook is the interpreter running this script.
    kdir = Path(tempfile.mkdtemp(prefix="kernels_")) / "kernels" / "basicrun"
    kdir.mkdir(parents=True)
    (kdir / "kernel.json").write_text(json.dumps({"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                                                  "display_name": "basicrun", "language": "python"}))
    os.environ["JUPYTER_PATH"] = str(kdir.parent.parent)
    if args.installer == "pip":
        uv = shutil.which("uv")
        if uv:
            os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if not (Path(p) / "uv").exists())
    elif args.installer == "uv" and not shutil.which("uv"):
        print("FAIL: --installer uv but uv is not on PATH")
        return 2

    client = NotebookClient(nb, kernel_name="basicrun", timeout=args.cell_timeout, resources={"metadata": {"path": work}},
                            allow_errors=False)
    t0 = time.time()
    error = None
    try:
        client.execute()
    except CellExecutionError as e:
        error = str(e)[-1500:]
    print("notebook run: %.0f s, installer=%s, server=%s, tunnel=%s" % (time.time() - t0, args.installer, args.server, args.tunnel))
    if args.keep:
        nbformat.write(nb, os.path.join(work, "executed.ipynb"))

    reports = sorted(Path(work).glob("x3d_mcp_smoke_*.json"))
    if error or not reports:
        print("FAIL: the notebook did not reach its report" + (":\n" + error if error else ""))
        return 1
    rep = json.loads(reports[-1].read_text())
    s = rep["summary"]
    print("server %s @ %s | python %s | mcp %s" % (rep["server"], rep["commit"].split("·")[0], rep["python"], rep["versions"].get("mcp")))
    print("passed %d / failed %d / warned %d / skipped %d" % (s["PASS"], s["FAIL"], s["WARN"], s["SKIP"]))
    for r in rep["results"]:
        if r["status"] in ("FAIL", "SKIP", "WARN"):
            print("  %-4s %-5s %-34s %s" % (r["part"], r["status"], r["name"], r["detail"][:100]))
    print("report:", reports[-1])
    return 1 if s["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
