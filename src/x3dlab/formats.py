"""Other formats as engines: glTF now, USD into the same slot next.

The trip X3D -> Blender (x3d-blender-io import) -> glTF (Blender's own exporter) is judged three ways, each by a
different referee, so a loss can be pinned on one step:

  1. the Khronos glTF Validator: is the file valid glTF 2.0 at all?             (blames Blender's exporter)
  2. Blender: import the glTF back into an empty scene and compare counts with  (blames the exporter, or says the
     what the X3D import produced: objects, meshes, vertices, lights, cameras,   X3D importer made something glTF
     and meshes shared between objects (DEF/USE instancing)                      cannot hold)
  3. X_ITE renders both: the original X3D, and a small X3D that Inlines the     (blames the whole trip; the expected
     glTF under the original's camera and headlight. Same renderer, same view,   losses below are subtracted first)
     so a silhouette that changes is something the conversion lost.

Stable API (other notebooks and sessions build on these names; a rename gets a deprecation alias first):
  match, compare_pngs, colour_kind, COLOUR_DELTA, validate_gltf, error_codes, losses, EXPECTED_LOSS, inline_wrapper,
  has_usd; and for the interchange run: usd_check, USD_SETTINGS, gltf_bounds, reference_camera, framed_scene, SAMPLES,
  fetch_samples, sample_file. tests/test_formats_api.py holds each of them to its signature.

Expected losses: glTF 2.0 has no place for X3D's behaviour (sensors, interpolators, ROUTEs, Script: glTF animation and
KHR_interactivity are a different model), for Text, Billboard, LOD and Switch semantics, Background and Fog, or for
scoped lights. A scene whose difference is all expected is not a finding; one with unexpected losses is.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

from . import bus, runtime

GLTF_VALIDATOR = "gltf-validator@2.0.0-dev.3.10"     # the Khronos validator, compiled from Dart to JS (npm)

# Node types glTF 2.0 (with the extensions Blender writes: KHR_lights_punctual, KHR_materials_*, KHR_texture_transform)
# has no place for, and why. Losing one of these in the trip is expected, not a defect.
EXPECTED_LOSS = {
    **{n: "behaviour: glTF has no sensors (KHR_interactivity is a separate, newer model)" for n in (
        "TouchSensor", "PlaneSensor", "CylinderSensor", "SphereSensor", "KeySensor", "StringSensor", "ProximitySensor",
        "VisibilitySensor", "TransformSensor", "TimeSensor", "CollisionSensor", "GeoProximitySensor", "LinePickSensor",
        "PointPickSensor", "PrimitivePickSensor", "VolumePickSensor", "GeoTouchSensor")},
    **{n: "behaviour: interpolators become glTF animation only if baked; the X3D importer does not bake them" for n in (
        "PositionInterpolator", "OrientationInterpolator", "ColorInterpolator", "ScalarInterpolator",
        "CoordinateInterpolator", "NormalInterpolator", "PositionInterpolator2D", "CoordinateInterpolator2D",
        "SplinePositionInterpolator", "SplineScalarInterpolator", "SquadOrientationInterpolator", "EaseInEaseOut",
        "PositionChaser", "OrientationChaser", "ColorChaser", "ScalarChaser", "PositionDamper", "OrientationDamper",
        "ColorDamper", "ScalarDamper", "BooleanSequencer", "IntegerSequencer", "BooleanFilter", "BooleanToggle",
        "BooleanTrigger", "IntegerTrigger", "TimeTrigger")},
    "Script": "behaviour: no scripting in glTF",
    "ROUTE": "behaviour: no event graph in glTF",
    "Text": "glTF has no text; Blender keeps it as a curve only if the importer converts it",
    "FontStyle": "glTF has no text",
    "ScreenFontStyle": "glTF has no text",
    "Billboard": "glTF has no view-dependent transforms",
    "Switch": "glTF has no choice node; only the active child can survive",
    "LOD": "glTF has no level of detail (MSFT_lod is not written by Blender)",
    "Collision": "glTF has no collision grouping",
    "Background": "glTF has no environment or sky",
    "TextureBackground": "glTF has no environment or sky",
    "Fog": "glTF has no fog",
    "LocalFog": "glTF has no fog",
    "FogCoordinate": "glTF has no fog",
    "EnvironmentLight": "image-based light: EXT_lights_image_based is not written by Blender",
    "NavigationInfo": "glTF has no navigation",
    "WorldInfo": "metadata only",
    "Anchor": "glTF has no links",
    "Sound": "glTF has no audio (KHR_audio is a draft)",
    "AudioClip": "glTF has no audio",
    "MovieTexture": "glTF has no video textures",
    "ProtoDeclare": "prototypes are expanded or lost",
    "ProtoInstance": "prototypes are expanded or lost",
    "ExternProtoDeclare": "prototypes are expanded or lost",
    "ParticleSystem": "glTF has no particles",
}

VALIDATE_JS = r"""
// node validate.js FILE...  ->  one JSON line per file
const fs = require('fs'), path = require('path');
const validator = require(path.join(__dirname, 'node_modules', 'gltf-validator'));
(async () => {
  for (const f of process.argv.slice(2)) {
    const out = { file: f };
    try {
      const r = await validator.validateBytes(new Uint8Array(fs.readFileSync(f)), {
        uri: path.basename(f), maxIssues: 200,
        externalResourceFunction: (uri) => new Promise((ok, no) => {
          try { ok(new Uint8Array(fs.readFileSync(path.resolve(path.dirname(f), decodeURIComponent(uri))))); } catch (e) { no(e.message); }
        }),
      });
      const i = r.issues || {};
      Object.assign(out, { ok: !i.numErrors, errors: i.numErrors || 0, warnings: i.numWarnings || 0, infos: i.numInfos || 0,
        hints: i.numHints || 0, messages: (i.messages || []).slice(0, 40).map((m) => ({ code: m.code, severity: m.severity,
        message: String(m.message).slice(0, 240), pointer: m.pointer || '' })),
        info: r.info ? { version: r.info.version, generator: r.info.generator, extensionsUsed: r.info.extensionsUsed || [],
          animationCount: r.info.animationCount, materialCount: r.info.materialCount, drawCallCount: r.info.drawCallCount,
          totalVertexCount: r.info.totalVertexCount, totalTriangleCount: r.info.totalTriangleCount,
          hasMorphTargets: r.info.hasMorphTargets, hasSkins: r.info.hasSkins, hasTextures: r.info.hasTextures } : null });
    } catch (e) { Object.assign(out, { ok: false, crashed: true, errors: 1, messages: [{ code: 'VALIDATOR_CRASH', severity: 0, message: String(e).slice(0, 300) }] }); }
    console.log(JSON.stringify(out));
  }
})();
"""

SEVERITY = {0: "error", 1: "warning", 2: "info", 3: "hint"}


def validator_dir() -> Path:
    """The Khronos validator in the lab's vendor folder (npm install once; 380 kB)."""
    from .hog import _node_bin
    d = runtime.sub("vendor", "gltf-validator")
    if not (d / "node_modules" / "gltf-validator" / "index.js").exists():
        node = _node_bin()
        npm = str(Path(node).with_name("npm")) if Path(node).with_name("npm").exists() else "npm"
        with bus.span("npm install %s" % GLTF_VALIDATOR, src="formats"):
            r = subprocess.run([npm, "install", "-q", "--no-audit", "--no-fund", "--prefix", str(d), GLTF_VALIDATOR],
                               capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError("npm install %s failed: %s" % (GLTF_VALIDATOR, (r.stderr or r.stdout)[-300:]))
    (d / "validate.js").write_text(VALIDATE_JS, encoding="utf-8")
    return d


def validate_gltf(paths: list, timeout: float = 600) -> dict:
    """{path: report} from the Khronos validator: ok, errors, warnings, messages [{code, severity, message, pointer}], info."""
    paths = [str(p) for p in paths if p and os.path.exists(str(p))]
    if not paths:
        return {}
    from .hog import _node_bin
    d = validator_dir()
    r = subprocess.run([_node_bin(), str(d / "validate.js"), *paths], capture_output=True, text=True, timeout=timeout)
    out = {}
    for ln in r.stdout.splitlines():
        if ln.startswith("{"):
            rep = json.loads(ln)
            out[rep["file"]] = rep
    for p in paths:
        out.setdefault(p, {"file": p, "ok": False, "crashed": True, "errors": 1,
                           "messages": [{"code": "NO_REPORT", "severity": 0, "message": (r.stderr or "no output")[-240:]}]})
    return out


def error_codes(report: dict) -> list:
    return sorted({m["code"] for m in report.get("messages", []) if m.get("severity") == 0})


# ---------------------------------------------------------------------- the X_ITE side
def _ancestry(el):
    out = []
    p = el.getparent()
    while p is not None and p.tag != "Scene":
        out.append(p)
        p = p.getparent()
    return list(reversed(out))


def inline_wrapper(xml: str, url: str, version: str = "4.0") -> str:
    """A scene that shows `url` (a .glb) under the original's first Viewpoint and NavigationInfo.
    The Viewpoint keeps the Transforms above it, so it sees what it saw; a viewpoint inside the Inline would not be bound."""
    from lxml import etree
    E = etree.Element
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True))
    out = E("X3D", profile="Full", version=version)
    etree.SubElement(out, "head")
    scene = etree.SubElement(out, "Scene")
    nav = next(iter(root.iter("NavigationInfo")), None)
    if nav is not None:
        n = E("NavigationInfo", {k: v for k, v in nav.attrib.items() if k not in ("DEF", "USE")})
        scene.append(n)
    vp = next((e for e in root.iter("Viewpoint", "OrthoViewpoint") if e.get("USE") is None), None)
    if vp is not None:
        parent = scene
        for a in _ancestry(vp):
            if a.tag in ("Transform", "HAnimJoint", "HAnimSegment", "HAnimSite"):
                keep = {k: a.get(k) for k in ("translation", "rotation", "scale", "scaleOrientation", "center") if a.get(k)}
                parent = etree.SubElement(parent, "Transform", keep)
        etree.SubElement(parent, vp.tag, {k: v for k, v in vp.attrib.items() if k not in ("DEF", "USE")})
    etree.SubElement(scene, "Inline", url='"%s"' % url)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(out, encoding="unicode", pretty_print=True)


def _dilate(m, r: int):
    out = m.copy()
    h, w = m.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] |= m[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def match(a, b, r: int = 2) -> float:
    """Silhouette agreement that forgives r pixels: F-score of 'covered here, covered within r there' both ways.
    Plain IoU punishes thin things (lines, points, text) for a one-pixel shift; this does not, and it still drops
    hard when a shape is missing, moved or the wrong size."""
    a_n, b_n = int(a.sum()), int(b.sum())
    if not a_n and not b_n:
        return 1.0
    if not a_n or not b_n:
        return 0.0
    p = float((a & _dilate(b, r)).sum()) / a_n
    q = float((b & _dilate(a, r)).sum()) / b_n
    return 2 * p * q / (p + q) if (p + q) else 0.0


def compare_pngs(original: bytes | None, converted: bytes | None) -> dict:
    """Agreement of two pictures on the flat key background: `match` (silhouettes, forgiving 2 px: what the verdicts
    use), plain `iou`, coverage of each, and the mean colour change where both are covered (lighting models differ,
    so colour is evidence, not a verdict)."""
    if not original or not converted:
        return {"iou": None, "match": None}
    import numpy as np
    from PIL import Image
    import io
    from .browser import iou, silhouette
    a, b = silhouette(original, "key"), silhouette(converted, "key")
    if a.shape != b.shape:
        return {"iou": None, "match": None, "error": "sizes differ: %s vs %s" % (a.shape, b.shape)}
    both = a & b
    ia = np.asarray(Image.open(io.BytesIO(original)).convert("RGB")).astype(int)
    ib = np.asarray(Image.open(io.BytesIO(converted)).convert("RGB")).astype(int)
    dcol = float(np.abs(ia - ib)[both].mean()) if both.any() else None
    luma = lambda im: (im[..., 0] * 0.2126 + im[..., 1] * 0.7152 + im[..., 2] * 0.0722)[both].mean()   # noqa: E731
    ratio = float(luma(ib) / max(1.0, luma(ia))) if both.any() else None
    return {"match": round(match(a, b), 4), "iou": round(iou(a, b), 4), "cov_original": round(float(a.mean()), 4),
            "cov_converted": round(float(b.mean()), 4), "colour_delta": round(dcol, 1) if dcol is not None else None,
            "luma_ratio": round(ratio, 3) if ratio is not None else None}


# Mean colour change (0-255, over the pixels both pictures cover) above which colours "disagree", per comparison.
# Each pair of renderers shades differently by design (Phong vs PBR, tone mapping), so each gets its own number:
# calibrated on scenes where nothing was lost. New renderers (Unreal...) add a key; nobody keeps a local copy.
COLOUR_DELTA = {
    "x_ite_vs_gltf_in_x_ite": 64.0,   # X3D in X_ITE vs the glTF trip in X_ITE: 34-52 on clean scenes (10 Nov)
    "bridge_vs_x_ite": 64.0,          # x3d.loom's Babylon body vs X_ITE (Phong vs Babylon's standard material)
}


def colour_kind(cmp_: dict, delta: float | None = None, against: str = "x_ite_vs_gltf_in_x_ite") -> str:
    """'' when the colours agree well enough (lighting models differ a little), else darker / brighter / hue.
    The threshold is `delta` if given, else COLOUR_DELTA[against]."""
    delta = float(delta if delta is not None else COLOUR_DELTA.get(against, 64.0))
    d, r = cmp_.get("colour_delta"), cmp_.get("luma_ratio")
    if d is None or d <= delta:
        return ""
    if r is not None and r < 0.5:
        return "darker"
    if r is not None and r > 1.6:
        return "brighter"
    return "hue"


def losses(types_before: list, convert: dict | None) -> dict:
    """What the trip lost, from the worker's convert result: expected (glTF has no place for it) and unexpected.
    glTF splits vertices at seams and triangulates, so more vertices or faces after the trip is normal; fewer is not."""
    convert = convert or {}
    exp = sorted(t for t in types_before if t in EXPECTED_LOSS)
    c0, c1 = convert.get("counts") or {}, (convert.get("reimport") or {}).get("counts") or {}
    changed = {k: [c0.get(k, 0), c1.get(k, 0)] for k in ("objects", "meshes", "vertices", "faces", "lights", "cameras", "materials")
               if c1 and c0.get(k, 0) != c1.get(k, 0)}
    unexpected, info = [], []
    for k, (a, b) in changed.items():
        if k in ("vertices", "faces", "lights", "cameras") and b < a:
            unexpected.append("%s %d -> %d" % (k, a, b))
        elif k == "meshes" and b < a:
            unexpected.append("meshes %d -> %d" % (a, b))
        else:
            info.append("%s %d -> %d" % (k, a, b))
    s0, s1 = convert.get("shared_meshes"), (convert.get("reimport") or {}).get("shared_meshes")
    if s0 and s1 is not None and s1 < s0:
        info.append("shared meshes (DEF/USE instancing) %d -> %d" % (s0, s1))
    return {"expected": exp, "counts": changed, "unexpected": unexpected, "info": info}


def has_usd(blender=None) -> dict:
    """Can this machine do the USD trip? {"usd_core": bool, "version": "0.25.11"|None, "checker": bool,
    "blender_usd": bool|None}. usd-core (Pixar's wheel) validates; Blender writes. Pass a lab Blender to know whether
    its build has the USD exporter (None when not asked). Truthy keys only when that part is really there."""
    out = {"usd_core": False, "version": None, "checker": False, "blender_usd": None}
    try:
        from pxr import Usd
        out["usd_core"] = True
        out["version"] = ".".join(str(x) for x in Usd.GetVersion())
        from pxr import UsdUtils  # noqa: F401
        out["checker"] = hasattr(UsdUtils, "ComplianceChecker")
    except Exception:
        pass
    if blender is not None:
        try:
            out["blender_usd"] = bool((blender.info or {}).get("usd", True))
        except Exception:
            out["blender_usd"] = None
    return out


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "x"


# ====================================================================== USD
USD_SETTINGS = {"format": "usdc", "metersPerUnit": 1.0, "upAxis": "Z", "defaultPrim": "root", "materials": "UsdPreviewSurface",
                "instancing": True, "lights": True, "cameras": True}     # what blender_worker._export_usd writes (Blender 5.2.3)
_USD_RULE_METHODS = ("CheckStage", "CheckDiagnostics", "CheckUnresolvedPaths", "CheckDependencies", "CheckLayer", "CheckZipFile",
                     "CheckPrim", "ResetCaches")


def ensure_usd() -> bool:
    """pip install the pinned usd-core into this kernel if it is missing (about 27 MB). True when pxr imports."""
    try:
        import pxr  # noqa: F401
        return True
    except Exception:
        pass
    import sys
    from . import pins
    spec = pins.get("python", "usd_core", "usd-core")
    with bus.span("pip install %s" % spec, src="formats"):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", spec], capture_output=True, text=True, timeout=900)
    if r.returncode:
        bus.warn("usd-core install failed: %s" % (r.stderr or r.stdout)[-300:], src="formats")
        return False
    import importlib
    importlib.invalidate_caches()
    try:
        import pxr  # noqa: F401,F811
        return True
    except Exception:
        return False


def usd_check(path) -> dict:
    """Pixar's compliance checker on a USD file, plus what the stage holds. A rule that crashes inside the checker
    (usd-core 25.11 lacks a shader resource one rule needs) is reported under `skipped_rules`, never as a failure.
    Returns {ok, errors, warnings, failed, skipped_rules, stage: {upAxis, metersPerUnit, defaultPrim, types, instances,
    prototypes}} or {ok: None, error} without usd-core."""
    out = {"ok": None, "errors": [], "warnings": [], "failed": [], "skipped_rules": [], "stage": {}}
    try:
        from pxr import Usd, UsdGeom, UsdUtils
    except Exception:
        out["error"] = "usd-core is not installed (pip install %s)" % "usd-core"
        return out
    checker = UsdUtils.ComplianceChecker()
    skipped = out["skipped_rules"]

    def guard(fn, rule):
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as exc:
                name = type(rule).__name__
                if not any(s["rule"] == name for s in skipped):
                    skipped.append({"rule": name, "why": str(exc).strip().splitlines()[-1][:200] if str(exc).strip() else type(exc).__name__})
        return run
    for rule in getattr(checker, "_rules", []):
        for m in _USD_RULE_METHODS:
            if hasattr(rule, m):
                setattr(rule, m, guard(getattr(rule, m), rule))
    try:
        checker.CheckCompliance(str(path))
    except Exception as exc:
        out["errors"].append("checker: %s" % str(exc)[:300])
    out["errors"] += [str(e)[:300] for e in checker.GetErrors()]
    out["warnings"] = [str(w)[:300] for w in checker.GetWarnings()][:20]
    failed = [str(f)[:300] for f in checker.GetFailedChecks()]
    try:                       # without shader definitions every shader looks "invalid": that is the wheel, not the file
        from pxr import Sdr
        no_defs = Sdr.Registry().GetShaderNodeByIdentifier("UsdPreviewSurface") is None
    except Exception:
        no_defs = True
    if no_defs and any("ShaderPropertyTypeConformanceChecker" in f for f in failed):
        failed = [f for f in failed if "ShaderPropertyTypeConformanceChecker" not in f]
        if not any(x["rule"] == "ShaderPropertyTypeConformanceChecker" for x in skipped):
            skipped.append({"rule": "ShaderPropertyTypeConformanceChecker",
                            "why": "this usd-core has no shader definitions (Sdr does not know UsdPreviewSurface)"})
        else:
            next(x for x in skipped if x["rule"] == "ShaderPropertyTypeConformanceChecker")["why"] += \
                "; this usd-core has no shader definitions (Sdr does not know UsdPreviewSurface)"
    out["failed"] = failed[:20]
    out["ok"] = not out["errors"] and not out["failed"]
    try:
        st = Usd.Stage.Open(str(path))
        prims = list(st.Traverse())
        from collections import Counter
        out["stage"] = {"upAxis": str(UsdGeom.GetStageUpAxis(st)), "metersPerUnit": UsdGeom.GetStageMetersPerUnit(st),
                        "defaultPrim": st.GetDefaultPrim().GetName() if st.GetDefaultPrim() else None,
                        "types": dict(Counter(p.GetTypeName() for p in prims if p.GetTypeName())),
                        "instances": sum(1 for p in prims if p.IsInstance()), "prototypes": len(st.GetPrototypes())}
    except Exception as exc:
        out["stage"] = {"error": str(exc)[:300]}
    return out


# ====================================================================== glTF files: bounds and a camera that frames them
def gltf_json(path) -> dict:
    """The JSON part of a .gltf or .glb."""
    import struct
    data = Path(path).read_bytes()
    if data[:4] == b"glTF":
        length, ctype = struct.unpack_from("<II", data, 12)
        return json.loads(data[20:20 + length].decode("utf-8"))
    return json.loads(data.decode("utf-8"))


def _node_matrix(n: dict):
    import numpy as np
    if "matrix" in n:
        return np.array(n["matrix"], dtype=float).reshape(4, 4).T          # glTF stores column-major
    t = np.array(n.get("translation", [0, 0, 0]), dtype=float)
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    s = np.array(n.get("scale", [1, 1, 1]), dtype=float)
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    return m


def gltf_bounds(path) -> dict:
    """World-space bounds of the default scene, from each POSITION accessor's min/max (required by the glTF spec)
    through the node transforms. Skins, morph targets and GPU instancing are ignored. {min, max, center, radius}."""
    import numpy as np
    j = gltf_json(path)
    nodes, meshes, acc = j.get("nodes", []), j.get("meshes", []), j.get("accessors", [])
    if j.get("scenes"):
        roots = j["scenes"][j.get("scene", 0)].get("nodes", [])
    else:
        children = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    stack = [(i, np.eye(4)) for i in roots]
    while stack:
        i, parent = stack.pop()
        n = nodes[i]
        world = parent @ _node_matrix(n)
        if "mesh" in n:
            for prim in meshes[n["mesh"]].get("primitives", []):
                a = acc[prim["attributes"]["POSITION"]] if "POSITION" in prim.get("attributes", {}) else None
                if not a or "min" not in a or "max" not in a:
                    continue
                mn, mx = a["min"], a["max"]
                corners = np.array([[x, y, z, 1] for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]).T
                w = (world @ corners)[:3]
                lo, hi = np.minimum(lo, w.min(1)), np.maximum(hi, w.max(1))
        stack += [(c, world) for c in n.get("children", [])]
    if not np.isfinite(lo).all():
        return {"min": None, "max": None, "center": [0.0, 0.0, 0.0], "radius": 1.0}
    c = (lo + hi) / 2
    return {"min": [round(float(v), 6) for v in lo], "max": [round(float(v), 6) for v in hi],
            "center": [round(float(v), 6) for v in c], "radius": round(float(np.linalg.norm(hi - lo) / 2) or 1.0, 6)}


VIEWS = {   # unit vectors from the model's centre to the camera; up is always +Y
    "three-quarter": (math.sin(math.radians(30)) * math.cos(math.radians(20)), math.sin(math.radians(20)),
                      math.cos(math.radians(30)) * math.cos(math.radians(20))),     # front-right-above: no plane is edge-on
    "front": (0.0, 0.0, 1.0),
}


def _look_rotation(direction) -> list:
    """X3D orientation (axis, angle) for a camera at +direction looking back at the origin with +Y up."""
    import numpy as np
    z = np.array(direction, dtype=float)
    z /= np.linalg.norm(z)
    x = np.cross([0.0, 1.0, 0.0], z)
    x = x / np.linalg.norm(x) if np.linalg.norm(x) > 1e-9 else np.array([1.0, 0.0, 0.0])
    y = np.cross(z, x)
    m = np.column_stack([x, y, z])
    angle = math.acos(max(-1.0, min(1.0, (np.trace(m) - 1) / 2)))
    if angle < 1e-9:
        return [0.0, 0.0, 1.0, 0.0]
    axis = np.array([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1]]) / (2 * math.sin(angle))
    return [round(float(v), 6) for v in axis] + [round(angle, 6)]


def reference_camera(bounds: dict, fov: float = math.pi / 4, margin: float = 1.1, view: str = "three-quarter") -> dict:
    """The reference-stills camera (coordination doc, item 4): at margin * r / sin(fov/2) from the centre along VIEWS[view]
    (default three-quarter: front-right-above, so a flat model in any principal plane still shows), looking at the
    centre with +Y up; near plane and visibility scaled to the model so tiny and huge models both show.
    Other engines reproduce it from `position`, `centerOfRotation` (look-at) and up +Y, or from `orientation`."""
    c, r = bounds["center"], float(bounds["radius"] or 1.0)
    d = margin * r / math.sin(fov / 2)
    u = VIEWS.get(view, VIEWS["three-quarter"])
    return {"position": [c[i] + d * u[i] for i in range(3)], "orientation": _look_rotation(u), "centerOfRotation": list(c),
            "up": [0.0, 1.0, 0.0], "view": view, "fieldOfView": fov, "distance": d,
            "avatarSize": [max(1e-5, d * 0.01), 1.6, 0.75], "visibilityLimit": d + 4 * r}


def framed_scene(url: str, cam: dict, background: str = "1 0 1", headlight: bool = True, version: str = "4.0") -> str:
    """An X3D page that shows `url` (glTF or X3D) through Inline under `cam`: how every reference still is taken."""
    f = lambda v: " ".join("%.6g" % x for x in v)          # noqa: E731
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Full" version="%s"><head/><Scene>\n'
            '  <Background skyColor="%s"/>\n'
            '  <NavigationInfo headlight="%s" type=\'"EXAMINE" "ANY"\' avatarSize="%s" visibilityLimit="%.6g"/>\n'
            '  <Viewpoint position="%s" orientation="%s" centerOfRotation="%s" fieldOfView="%.6g" description="reference"/>\n'
            '  <Inline url=\'"%s"\'/>\n</Scene></X3D>\n') % (
        version, background, "true" if headlight else "false", f(cam["avatarSize"]), cam["visibilityLimit"],
        f(cam["position"]), f(cam.get("orientation", [0, 0, 1, 0])), f(cam["centerOfRotation"]), cam["fieldOfView"], url)


# ====================================================================== the Khronos sample models
# name -> (variant, what it probes in X3D terms). Small models only (about 14 MB in all), one feature each.
SAMPLES = {
    "Box": ("glTF-Binary", "one mesh, one PBR material -> Shape, PhysicalMaterial"),
    "BoxInterleaved": ("glTF-Binary", "interleaved vertex buffers"),
    "BoxTextured": ("glTF-Binary", "base colour texture -> ImageTexture, TextureCoordinate"),
    "BoxVertexColors": ("glTF-Binary", "vertex colours -> Color"),
    "Triangle": ("glTF-Embedded", "the smallest glTF: no material, data URI buffer"),
    "TriangleWithoutIndices": ("glTF-Embedded", "non-indexed triangles -> TriangleSet"),
    "SimpleMeshes": ("glTF-Embedded", "one mesh used by two nodes -> DEF/USE"),
    "MeshPrimitiveModes": ("glTF-Embedded", "points, lines, loops, strips, fans -> PointSet, LineSet, *StripSet, *FanSet"),
    "OrientationTest": ("glTF-Binary", "node rotations: the handedness and up-axis check"),
    "NegativeScaleTest": ("glTF-Binary", "mirroring flips the winding -> ccw"),
    "TextureCoordinateTest": ("glTF-Binary", "UV orientation"),
    "TextureSettingsTest": ("glTF-Binary", "wrap and filter modes -> TextureProperties"),
    "MultiUVTest": ("glTF-Binary", "two UV sets -> MultiTextureCoordinate"),
    "TextureTransformTest": ("glTF", "KHR_texture_transform -> TextureTransform"),
    "VertexColorTest": ("glTF-Binary", "vertex colour vs texture"),
    "NormalTangentTest": ("glTF-Binary", "normal maps and tangents -> PhysicalMaterial.normalTexture, Tangent (4.1)"),
    "MetalRoughSpheresNoTextures": ("glTF-Binary", "the metallic/roughness grid -> PhysicalMaterial"),
    "AlphaBlendModeTest": ("glTF-Binary", "OPAQUE, MASK, BLEND -> Appearance.alphaMode, alphaCutoff"),
    "TwoSidedPlane": ("glTF", "doubleSided -> solid false"),
    "Cameras": ("glTF-Embedded", "perspective and orthographic cameras -> Viewpoint, OrthoViewpoint"),
    "LightsPunctualLamp": ("glTF-Binary", "KHR_lights_punctual -> SpotLight, PointLight"),
    "DirectionalLight": ("glTF-Binary", "a directional light -> DirectionalLight"),
    "UnlitTest": ("glTF-Binary", "KHR_materials_unlit -> UnlitMaterial"),
    "EmissiveStrengthTest": ("glTF-Binary", "KHR_materials_emissive_strength: emission above 1"),
    "BoxAnimated": ("glTF-Binary", "node animation -> TimeSensor and interpolators"),
    "InterpolationTest": ("glTF-Binary", "STEP, LINEAR, CUBICSPLINE interpolation"),
    "AnimatedTriangle": ("glTF-Embedded", "rotation animation"),
    "MorphPrimitivesTest": ("glTF-Binary", "morph targets"),
    "SimpleSkin": ("glTF-Embedded", "skinning (X3D has HAnim, not general skins)"),
    "SimpleInstancing": ("glTF-Binary", "EXT_mesh_gpu_instancing"),
    "Duck": ("glTF-Binary", "the classic textured model"),
    "DamagedHelmet": ("glTF-Binary", "full PBR: base, metal-rough, normal, occlusion, emissive textures"),
}


def fetch_samples(names: list | None = None, ref: str = "", repo: str = "") -> Path:
    """A sparse, blob-less checkout of the Khronos sample assets at the pinned commit, holding only the named models'
    variants (and the model index). Cached under the lab's vendor folder. Returns its root."""
    from . import pins
    repo = repo or "https://github.com/%s" % pins.get("gltf_samples", "repo", "KhronosGroup/glTF-Sample-Assets")
    ref = ref or pins.get("gltf_samples", "ref")
    names = list(names or SAMPLES)
    root = runtime.sub("vendor", "gltf-sample-assets-%s" % ref[:12])
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    git = lambda *a, t=900: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, env=env, timeout=t)   # noqa: E731
    if not (root / ".git").is_dir():
        for cmd in (("init", "-q"), ("remote", "add", "origin", repo)):
            git(*cmd)
    patterns = ["/Models/model-index.json"] + ["/Models/%s/%s/" % (n, SAMPLES[n][0]) for n in names if n in SAMPLES]
    want = root / ".x3dlab-sparse"
    if not want.exists() or want.read_text() != "\n".join(patterns) or not (root / "Models").is_dir():
        if git("cat-file", "-e", "%s^{commit}" % ref).returncode:
            r = git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref)
            if r.returncode:
                raise RuntimeError("fetching the glTF sample assets failed: %s" % (r.stderr or r.stdout)[-300:])
        git("sparse-checkout", "init", "--no-cone")
        r = git("sparse-checkout", "set", "--no-cone", *patterns)
        r2 = git("checkout", "-q", ref)
        if r.returncode or r2.returncode:
            raise RuntimeError("checking out the glTF sample assets failed: %s" % ((r.stderr or "") + (r2.stderr or ""))[-300:])
        want.write_text("\n".join(patterns))
    return root


def sample_file(root: Path, name: str) -> Path | None:
    """The model file of a sample's variant (the .glb, or the .gltf of an embedded or separate variant)."""
    d = Path(root) / "Models" / name / SAMPLES[name][0]
    for pat in ("*.glb", "*.gltf"):
        hits = sorted(d.glob(pat))
        if hits:
            return hits[0]
    return None
