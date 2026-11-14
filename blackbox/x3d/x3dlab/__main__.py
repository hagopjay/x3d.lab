"""x3dlab command line: the no-MCP Python lane.

    python -m x3dlab list
    python -m x3dlab build --out ../site/x3d/scenes [--blender]
    python -m x3dlab write orrery --encoding vrml > orrery.x3dv
    python -m x3dlab validate my.x3d [--json]
    python -m x3dlab predict my.x3d
    python -m x3dlab glb scene.glb
    python -m x3dlab mcp                       # the same functions as an MCP server on stdio
"""
from __future__ import annotations

import argparse
import json
import sys

from .fidelity import LEGS, predict, summarise
from .scenes import SCENES
from .validate import validate_file

ICON = {"kept": "●", "approx": "◐", "lost": "○", "n/a": "·"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x3dlab", description="X3D Lab: build, validate and predict X3D scenes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--blender", action="store_true", help="also run X3D → Blender → glb (needs bpy and X3DLAB_ADDON)")
    b.add_argument("--bpy-python", help="Python with bpy (default: X3DLAB_BPY_PYTHON or this Python)")
    b.add_argument("--addon", help="x3d-blender-io source/ folder (default: X3DLAB_ADDON)")
    b.add_argument("--circle-divisions", type=int, default=48)
    w = sub.add_parser("write")
    w.add_argument("scene", choices=sorted(SCENES))
    w.add_argument("--encoding", choices=["xml", "vrml", "json"], default="xml")
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="+")
    v.add_argument("--json", action="store_true")
    p = sub.add_parser("predict")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    an = sub.add_parser("analyze", help="validate + predict one file (or - for stdin) as one JSON object")
    an.add_argument("file")
    an.add_argument("--name", default=None)
    g = sub.add_parser("glb")
    g.add_argument("file")
    sub.add_parser("mcp")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for sid, fn in SCENES.items():
            _doc, info = fn()
            print("%-18s %s" % (sid, info["title"]))
        return 0
    if a.cmd == "build":
        from .build import build
        build(a.out, blender=a.blender, bpy_python=a.bpy_python, addon=a.addon, circle_divisions=a.circle_divisions)
        return 0
    if a.cmd == "write":
        doc, _info = SCENES[a.scene]()
        sys.stdout.write({"xml": doc.xml, "vrml": doc.vrml, "json": doc.json}[a.encoding]())
        return 0
    if a.cmd == "validate":
        bad = 0
        for f in a.files:
            r = validate_file(f)
            bad += 0 if r["ok"] else 1
            if a.json:
                print(json.dumps(r, indent=1))
                continue
            print("%s  %s  %d errors, %d warnings, %s nodes" % ("PASS" if r["ok"] else "FAIL", f, r["errors"], r["warnings"],
                                                               r["stats"].get("nodes", "?")))
            for i in r["issues"]:
                print("  %-7s %-24s line %-5d %s" % (i["level"], i["code"], i["line"], i["message"]))
        return 1 if bad else 0
    if a.cmd == "predict":
        r = validate_file(a.file)
        rules = predict(r["stats"])
        if a.json:
            print(json.dumps({"rules": rules, "summary": summarise(rules)}, indent=1))
            return 0
        print("%-34s %s" % ("rule", "  ".join("%-8s" % leg for leg in LEGS)))
        for rule in rules:
            print("%-34s %s" % (rule["title"][:34], "  ".join("%s %-6s" % (ICON[rule["legs"][leg][0]], rule["legs"][leg][0])
                                                                 for leg in LEGS)))
        print("● kept  ◐ approx  ○ lost")
        return 0
    if a.cmd == "analyze":
        from .validate import validate_text
        text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
        r = validate_text(text, filename=a.name or a.file)
        rules = predict(r["stats"])
        print(json.dumps({"validation": {k: r[k] for k in ("ok", "errors", "warnings", "issues")}, "stats": r["stats"],
                          "fidelity": rules, "legSummary": summarise(rules)}))
        return 0
    if a.cmd == "glb":
        from .glb import inspect
        print(json.dumps(inspect(a.file), indent=1))
        return 0
    if a.cmd == "mcp":
        from .mcp_server import serve
        serve()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
