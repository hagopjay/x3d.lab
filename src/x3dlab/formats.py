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
  has_usd. tests/test_formats_api.py holds each of them to its signature.

Expected losses: glTF 2.0 has no place for X3D's behaviour (sensors, interpolators, ROUTEs, Script: glTF animation and
KHR_interactivity are a different model), for Text, Billboard, LOD and Switch semantics, Background and Fog, or for
scoped lights. A scene whose difference is all expected is not a finding; one with unexpected losses is.
"""

from __future__ import annotations

import json
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
