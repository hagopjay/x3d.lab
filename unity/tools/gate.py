#!/usr/bin/env python3
"""The tether gate: nothing reaches a headset unless x3d_mcp's XSD and semantic validators pass it.
usage: gate.py FILE.x3d   (exit 0 = pass, 1 = fail; JSON report on stdout)
env X3D_MCP_DIR points at a hagopjay/x3d_mcp checkout (default ~/x3d_mcp or /home/user/x3d_mcp)."""
import json, os, sys
d = os.environ.get("X3D_MCP_DIR") or next((p for p in (os.path.expanduser("~/x3d_mcp"), "/home/user/x3d_mcp") if os.path.isdir(p)), None)
if not d:
    print(json.dumps({"valid": False, "errors": ["X3D_MCP_DIR not set and no x3d_mcp checkout found"]})); sys.exit(1)
sys.path[:0] = [os.path.join(d, "src"), d]
from validation.validate import validate_xml           # noqa: E402
from validation.semantic import validate_semantic      # noqa: E402
text = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 and sys.argv[1] != "-" else sys.stdin.read()
xsd = validate_xml(text)
sem_raw = validate_semantic(text)
# validate_semantic returns a Markdown report ("## Errors" then "- **[code]** message" bullets), not JSON.
sem_errs, mode = [], None
for line in str(sem_raw).splitlines():
    if line.startswith("## "): mode = line[3:].strip().lower()
    elif mode == "errors" and line.startswith("- "): sem_errs.append(line[2:].strip())
ok = bool(xsd.get("valid")) and not sem_errs
print(json.dumps({"valid": ok, "xsd": {"valid": xsd.get("valid"), "version": xsd.get("schema_version"), "errors": xsd.get("errors", [])[:20]},
                  "semantic": {"errors": sem_errs[:20]}}))
sys.exit(0 if ok else 1)
