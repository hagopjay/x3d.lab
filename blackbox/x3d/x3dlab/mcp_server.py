"""x3dlab as an MCP server: a thin skin over the library (stdio, newline-delimited JSON-RPC 2.0, stdlib only).

    python -m x3dlab mcp            # or: python -m x3dlab.mcp_server

Tools: list_scenes, write_scene, compose_scene, validate_x3d, predict_fidelity, blender_roundtrip, inspect_glb.
Resources: x3dlab://scenes/<id>.x3d (and .x3dv / .x3dj), x3dlab://schema/x3d40.json.

Every tool is one library call; nothing here is reachable only through MCP (see the project's Python-API-vs-MCP
note: batch work stays on the Python APIs, MCP is for interactive and agent use).

File access is confined to X3DLAB_ROOT (default: the BLACKBOX folder above x3d/) and the client's roots.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .fidelity import LEGS, predict, summarise
from .glb import inspect as inspect_glb
from .model import BuildError, Doc, N, Route
from .scenes import SCENES
from .schema import SCHEMA
from .validate import validate_text

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("X3DLAB_ROOT") or HERE.parents[1]).resolve()
OUT = Path(os.environ.get("X3DLAB_OUT") or (ROOT / "runs" / "x3dlab-mcp")).resolve()
CLIENT_ROOTS: list[Path] = []
STATE = {"clientRoots": False}


def log(*a):
    print("[x3dlab-mcp]", *a, file=sys.stderr, flush=True)


def allowed(p: Path) -> bool:
    p = p.resolve()
    return any(p == r or r in p.parents for r in [ROOT, OUT, *CLIENT_ROOTS])


def resolve_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not allowed(p):
        raise ValueError("path %s is outside the workspace (%s)" % (p, ROOT))
    return p


def source_of(args: dict) -> tuple[str, str]:
    """(xml text, label) from exactly one of scene / path / text."""
    given = [k for k in ("scene", "path", "text") if args.get(k)]
    if len(given) != 1:
        raise ValueError("pass exactly one of scene, path or text")
    if args.get("scene"):
        sid = args["scene"]
        if sid not in SCENES:
            raise ValueError("unknown scene %r; list_scenes names them" % sid)
        doc, _ = SCENES[sid]()
        return doc.xml(), sid
    if args.get("path"):
        p = resolve_path(args["path"])
        return p.read_text(encoding="utf-8"), str(p.relative_to(ROOT) if ROOT in p.parents else p)
    return args["text"], "<text>"


# ── tools ────────────────────────────────────────────────────────────────────────────────────────
def t_list_scenes(_a):
    rows = []
    for sid, fn in SCENES.items():
        _doc, info = fn()
        rows.append({"id": sid, "title": info["title"], "blurb": info["blurb"], "exercises": info["exercises"]})
    return {"scenes": rows}


def t_write_scene(a):
    sid, enc = a["scene"], a.get("encoding", "xml")
    if sid not in SCENES:
        raise ValueError("unknown scene %r" % sid)
    doc, info = SCENES[sid]()
    text = {"xml": doc.xml, "vrml": doc.vrml, "json": doc.json}[enc]()
    ext = {"xml": ".x3d", "vrml": ".x3dv", "json": ".x3dj"}[enc]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (sid + ext)
    path.write_text(text)
    lines = text.splitlines()
    return {"path": str(path), "bytes": len(text.encode()), "lines": len(lines),
            "preview": "\n".join(lines[:30]) + ("\n…" if len(lines) > 30 else "")}


def build_node(spec, depth=0):
    """{"type": "Transform", "DEF": "...", "fields": {...}, "children": [...]} → model.N (schema-checked)."""
    if depth > 40:
        raise BuildError("node tree deeper than 40")
    if not isinstance(spec, dict) or "type" not in spec:
        raise BuildError("each node is an object with a 'type'")
    kids = [build_node(c, depth + 1) for c in spec.get("children", [])]
    return N(spec["type"], *kids, DEF=spec.get("DEF"), USE=spec.get("USE"), cf=spec.get("containerField"),
             **(spec.get("fields") or {}))


def t_compose_scene(a):
    doc = Doc(a.get("profile", "Immersive"), title=a["title"], description=a.get("description", ""))
    for n in a.get("nodes", []):
        doc.add(build_node(n))
    for r in a.get("routes", []):
        doc.add(Route(r["fromNode"], r["fromField"], r["toNode"], r["toField"]))
    xml = doc.xml()
    v = validate_text(xml, filename=a["title"])
    out = {"validation": {k: v[k] for k in ("ok", "errors", "warnings")}, "issues": v["issues"][:20],
           "stats": {k: v["stats"].get(k) for k in ("nodes", "routes", "byType")}}
    if a.get("save_as"):
        name = Path(a["save_as"]).name
        if not name.endswith(".x3d"):
            name += ".x3d"
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(xml)
        out["path"] = str(OUT / name)
    out["xml"] = xml if len(xml) < 20000 else xml[:20000] + "\n<!-- truncated -->"
    return out


def t_validate(a):
    text, label = source_of(a)
    v = validate_text(text, filename=label)
    limit = int(a.get("max_issues", 25))
    return {"file": label, "ok": v["ok"], "errors": v["errors"], "warnings": v["warnings"],
            "issues": v["issues"][:limit], "truncated": max(0, len(v["issues"]) - limit),
            "stats": {k: v["stats"].get(k) for k in ("profile", "version", "nodes", "routes", "defs", "protos", "byType")}}


def t_predict(a):
    text, label = source_of(a)
    v = validate_text(text, filename=label)
    rules = predict(v["stats"])
    table = [{"rule": r["id"], "title": r["title"], **{leg: r["legs"][leg][0] for leg in LEGS}, "why": r["why"]}
             for r in rules]
    return {"file": label, "rules": table, "summary": summarise(rules),
            "note": "Predictions from the scene's own nodes. blender_roundtrip measures the Blender and glTF legs."}


def bpy_python() -> str | None:
    cand = os.environ.get("X3DLAB_BPY_PYTHON")
    if cand:
        return cand
    try:
        import importlib.util
        if importlib.util.find_spec("bpy"):
            return sys.executable
    except Exception:
        pass
    return None


def t_blender(a):
    from .build import measure, summarise_blender, verdict
    py = bpy_python()
    addon = os.environ.get("X3DLAB_ADDON")
    if not py or not addon:
        raise RuntimeError("Blender leg unavailable: set X3DLAB_BPY_PYTHON (a Python with bpy) and X3DLAB_ADDON "
                           "(x3d-blender-io/source). See docs/X3D-LAB.md.")
    text, label = source_of(a)
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="bpy-", dir=str(OUT)))
    stem = (a.get("scene") or Path(label).stem or "scene").replace("/", "_")
    src = work / (stem + ".x3d")
    src.write_text(text)
    cmd = [py, "-m", "x3dlab.blender_leg", str(src), "--out-dir", str(work), "--addon", addon,
           "--circle-divisions", str(int(a.get("circle_divisions", 48)))]
    env = {**os.environ, "PYTHONPATH": str(HERE.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    jp = work / (stem + ".blender.json")
    b = json.loads(jp.read_text()) if jp.exists() else {"ok": False, "error": proc.stdout[-1500:] + proc.stderr[-1500:]}
    v = validate_text(text, filename=label)
    rules = predict(v["stats"])
    info = {"stats": v["stats"]}
    if a.get("scene") == "orrery":
        info["expectProtoMeshes"] = 8
    rows = []
    for r in rules:
        m = measure(r["id"], b, info)
        for leg, (status, ev) in m.items():
            rows.append({"rule": r["id"], "leg": leg, "predicted": r["legs"][leg][0], "measured": status,
                         "verdict": verdict(r["legs"][leg][0], status), "evidence": ev})
    return {"scene": label, "seconds": round(time.time() - t0, 2), "blender": summarise_blender(b),
            "glb": str(work / (stem + ".glb")) if (work / (stem + ".glb")).exists() else None,
            "comparison": rows, "log": [l for l in proc.stdout.splitlines() if l.startswith("[blender-leg]")][-12:]}


def t_inspect_glb(a):
    return inspect_glb(str(resolve_path(a["path"])))


SCENE_ENUM = sorted(SCENES)
SRC_PROPS = {"scene": {"type": "string", "enum": SCENE_ENUM, "description": "a built-in x3dlab scene id"},
             "path": {"type": "string", "description": "an .x3d file inside the workspace"},
             "text": {"type": "string", "description": "X3D XML text"}}

TOOLS = [
    {"name": "list_scenes", "description": "List the built-in x3dlab showcase scenes with what each exercises.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "fn": t_list_scenes,
     "annotations": {"readOnlyHint": True}},
    {"name": "write_scene",
     "description": "Write a built-in scene to the workspace in an X3D encoding (XML .x3d, ClassicVRML .x3dv or JSON .x3dj).",
     "inputSchema": {"type": "object", "properties": {"scene": SRC_PROPS["scene"],
                                                      "encoding": {"type": "string", "enum": ["xml", "vrml", "json"]}},
                     "required": ["scene"], "additionalProperties": False}, "fn": t_write_scene},
    {"name": "compose_scene",
     "description": ("Author a new X3D 4.0 scene from a JSON node tree. Every node type and field is checked against "
                     "X3DUOM 4.0 as it is built, then the result is validated. Node: {type, DEF?, USE?, containerField?, "
                     "fields?: {name: value}, children?: [node]}. Vectors and colours are arrays of numbers."),
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "description": {"type": "string"},
         "profile": {"type": "string", "enum": ["Interchange", "Interactive", "Immersive", "Full"]},
         "nodes": {"type": "array", "items": {"type": "object"}},
         "routes": {"type": "array", "items": {"type": "object", "properties": {
             "fromNode": {"type": "string"}, "fromField": {"type": "string"}, "toNode": {"type": "string"},
             "toField": {"type": "string"}}, "required": ["fromNode", "fromField", "toNode", "toField"]}},
         "save_as": {"type": "string", "description": "file name to save under the workspace output folder"}},
         "required": ["title", "nodes"], "additionalProperties": False}, "fn": t_compose_scene},
    {"name": "validate_x3d",
     "description": "Validate X3D XML against X3DUOM 4.0: nodes, fields, value types, DEF/USE, containment, ROUTE types, "
                    "index ranges and colour counts. Pass exactly one of scene, path, text.",
     "inputSchema": {"type": "object", "properties": {**SRC_PROPS, "max_issues": {"type": "integer"}},
                     "additionalProperties": False}, "fn": t_validate, "annotations": {"readOnlyHint": True}},
    {"name": "predict_fidelity",
     "description": "Predict what X_ITE, Blender (x3d-blender-io), glTF and Unreal keep of a scene: kept / approx / lost "
                    "per rule, from the scene's own nodes. Pass exactly one of scene, path, text.",
     "inputSchema": {"type": "object", "properties": SRC_PROPS, "additionalProperties": False}, "fn": t_predict,
     "annotations": {"readOnlyHint": True}},
    {"name": "blender_roundtrip",
     "description": "Run X3D → Blender (x3d-blender-io) → glb headless through bpy, then compare each fidelity prediction "
                    "with what Blender and the glb actually hold. Needs X3DLAB_BPY_PYTHON and X3DLAB_ADDON.",
     "inputSchema": {"type": "object", "properties": {**SRC_PROPS, "circle_divisions": {"type": "integer"}},
                     "additionalProperties": False}, "fn": t_blender},
    {"name": "inspect_glb", "description": "Summarise a .glb: primitive modes, COLOR_0, materials and extensions, animations.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                     "additionalProperties": False}, "fn": t_inspect_glb, "annotations": {"readOnlyHint": True}},
]
TOOL_MAP = {t["name"]: t for t in TOOLS}

MIME = {".x3d": "model/x3d+xml", ".x3dv": "model/x3d-vrml", ".x3dj": "model/x3d+json"}


def resources():
    out = []
    for sid, fn in SCENES.items():
        _d, info = fn()
        for ext, mime in MIME.items():
            out.append({"uri": "x3dlab://scenes/%s%s" % (sid, ext), "name": "%s%s" % (sid, ext),
                        "title": info["title"], "mimeType": mime})
    out.append({"uri": "x3dlab://schema/x3d40.json", "name": "x3d40.json", "title": SCHEMA["source"],
                "mimeType": "application/json"})
    return out


def read_resource(uri: str):
    if uri == "x3dlab://schema/x3d40.json":
        return (HERE / "schema" / "x3d40.json").read_text(), "application/json"
    prefix = "x3dlab://scenes/"
    if uri.startswith(prefix):
        name = uri[len(prefix):]
        for ext in MIME:
            if name.endswith(ext) and name[:-len(ext)] in SCENES:
                doc, _ = SCENES[name[:-len(ext)]]()
                return {"x3d": doc.xml, "x3dv": doc.vrml, "x3dj": doc.json}[ext[1:]](), MIME[ext]
    raise KeyError(uri)


# ── JSON-RPC loop ────────────────────────────────────────────────────────────────────────────────
def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method is None:
        return  # a response to something we never send
    if mid is None:
        # notifications: once initialized, ask a client that offers roots which folders it shares
        if method == "notifications/initialized" and STATE["clientRoots"]:
            send({"jsonrpc": "2.0", "id": "x3dlab-roots-1", "method": "roots/list"})
        return
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            caps = params.get("capabilities") or {}
            result = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                      "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                      "serverInfo": {"name": "x3dlab", "title": "BLACKBOX X3D Lab", "version": __version__},
                      "instructions": ("Build, validate and predict the fidelity of X3D scenes; run the Blender leg. "
                                       "Start with list_scenes, then validate_x3d / predict_fidelity on a scene.")}
            STATE["clientRoots"] = bool(caps.get("roots"))
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]}
        elif method == "tools/call":
            name = params.get("name")
            tool = TOOL_MAP.get(name)
            if tool is None:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool %r" % name}})
                return
            t0 = time.time()
            try:
                data = tool["fn"](params.get("arguments") or {})
                text = json.dumps(data, indent=1)
                result = {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}
            except Exception as e:  # a failing tool is data for the agent, not a protocol error
                log("tool %s failed: %s" % (name, e))
                result = {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}
            log("%s %.0f ms" % (name, (time.time() - t0) * 1000))
        elif method == "resources/list":
            result = {"resources": resources()}
        elif method == "resources/read":
            text, mime = read_resource(params.get("uri", ""))
            result = {"contents": [{"uri": params["uri"], "mimeType": mime, "text": text}]}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})
            return
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except KeyError as e:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32002, "message": "resource not found: %s" % e}})
    except Exception as e:
        log(traceback.format_exc())
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


def on_response(msg):
    """The client's answer to our roots/list request."""
    if msg.get("id") == "x3dlab-roots-1" and "result" in msg:
        for r in msg["result"].get("roots", []):
            u = urlparse(r.get("uri", ""))
            if u.scheme == "file":
                CLIENT_ROOTS.append(Path(unquote(u.path)).resolve())
        log("client roots: %s" % [str(p) for p in CLIENT_ROOTS])


def serve():
    log("x3dlab %s on stdio · root %s" % (__version__, ROOT))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if "method" not in msg and "id" in msg:
            on_response(msg)
            continue
        handle(msg)


if __name__ == "__main__":
    serve()
