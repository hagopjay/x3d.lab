"""Interchange: glTF bounds and the reference camera, the framed scene, the USD checker's honesty, claims and the gap map."""

import json
import math
import struct
from pathlib import Path

import pytest

from x3dlab import formats, interchange


def _gltf(tmp_path, nodes, binary=False):
    j = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
         "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]}]}
    if not binary:
        f = tmp_path / "m.gltf"
        f.write_text(json.dumps(j))
        return f
    data = json.dumps(j).encode()
    data += b" " * (-len(data) % 4)
    f = tmp_path / "m.glb"
    f.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(data)) + struct.pack("<II", len(data), 0x4E4F534A) + data)
    return f


def test_bounds_follow_the_node_transforms(tmp_path):
    nodes = [{"translation": [10, 0, 0], "scale": [2, 2, 2], "children": [1]},
             {"rotation": [0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)], "mesh": 0}]    # 90° about Z inside
    for binary in (False, True):
        b = formats.gltf_bounds(_gltf(tmp_path, nodes, binary))
        assert b["min"] == pytest.approx([8, -2, 0], abs=1e-6) and b["max"] == pytest.approx([12, 2, 0], abs=1e-6)
        assert b["center"] == pytest.approx([10, 0, 0]) and b["radius"] == pytest.approx(math.sqrt(8), rel=1e-6)
    m = [{"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 5, 0, 1], "mesh": 0}]                     # column-major translation
    assert formats.gltf_bounds(_gltf(tmp_path, m))["center"] == pytest.approx([0, 5, 0])


def _apply(rot, v):
    import numpy as np
    ax, ang = np.array(rot[:3]), rot[3]
    v = np.array(v, dtype=float)
    return v * math.cos(ang) + np.cross(ax, v) * math.sin(ang) + ax * ax.dot(v) * (1 - math.cos(ang))


def test_reference_camera_frames_the_sphere():
    front = formats.reference_camera({"center": [1, 2, 3], "radius": 2.0}, view="front")
    d = 1.1 * 2.0 / math.sin(math.pi / 8)
    assert front["position"] == pytest.approx([1, 2, 3 + d]) and front["orientation"][3] == 0
    cam = formats.reference_camera({"center": [1, 2, 3], "radius": 2.0})
    to_centre = [c - p for c, p in zip(cam["centerOfRotation"], cam["position"])]
    look = _apply(cam["orientation"], [0, 0, -1])                    # the X3D camera looks down its -Z
    assert look == pytest.approx([v / d for v in to_centre], abs=1e-5)
    assert _apply(cam["orientation"], [0, 1, 0])[1] > 0.9              # and keeps +Y up
    assert cam["centerOfRotation"] == [1, 2, 3] and cam["view"] == "three-quarter"
    assert cam["avatarSize"][0] == pytest.approx(d * 0.01) and cam["visibilityLimit"] == pytest.approx(d + 8)
    tiny = formats.reference_camera({"center": [0, 0, 0], "radius": 0.001})
    assert tiny["avatarSize"][0] < tiny["distance"] / 50                # the near plane never cuts a tiny model
    xml = formats.framed_scene("src/Box/Box.glb", cam, background="0.5 0.5 0.5")
    from lxml import etree
    sc = etree.fromstring(xml.encode()).find("Scene")
    assert sc.find("Inline").get("url") == '"src/Box/Box.glb"' and sc.find("Background").get("skyColor") == "0.5 0.5 0.5"
    assert sc.find("NavigationInfo").get("headlight") == "true" and sc.find("Viewpoint").get("fieldOfView").startswith("0.785")


def test_samples_table():
    assert len(formats.SAMPLES) == 32
    assert all(v in ("glTF-Binary", "glTF-Embedded", "glTF") and p for v, p in formats.SAMPLES.values())
    assert {"MeshPrimitiveModes", "AlphaBlendModeTest", "UnlitTest", "OrientationTest"} <= set(formats.SAMPLES)


def test_usd_check_without_usd_core(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pxr(name, *a, **k):
        if name == "pxr" or name.startswith("pxr."):
            raise ImportError("no pxr")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pxr)
    r = formats.usd_check("/nonexistent.usdc")
    assert r["ok"] is None and "usd-core" in r["error"]


def test_usd_check_on_a_tiny_stage(tmp_path):
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    f = tmp_path / "t.usda"
    st = Usd.Stage.CreateNew(str(f))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = UsdGeom.Xform.Define(st, "/root")
    st.SetDefaultPrim(root.GetPrim())
    UsdGeom.Mesh.Define(st, "/root/m")
    st.GetRootLayer().Save()
    r = formats.usd_check(f)
    assert r["stage"]["upAxis"] == "Z" and r["stage"]["metersPerUnit"] == 1.0 and r["stage"]["defaultPrim"] == "root"
    assert r["stage"]["types"].get("Mesh") == 1 and isinstance(r["ok"], bool)
    assert pxr


def test_claims_and_gap_map(tmp_path):
    r = interchange.InterchangeRun(interchange.InterchangeConfig(), "rid", "t", tmp_path)
    good = {"match": 0.99, "iou": 0.98, "cov_original": 0.1, "colour_delta": 10.0, "luma_ratio": 1.0}
    r.results = {
        "MeshPrimitiveModes": {"probes": "p", "validator": {"errors": 0}, "reference": {"ok": True, "errors": []},
                               "xite": {"xsd_ok": True, "compare": good, "types": ["PointSet", "IndexedTriangleFanSet", "Shape"]},
                               "blender_x3d40": {"xsd_ok": True, "compare": {**good, "match": 0.6},
                                                 "types": ["IndexedFaceSet", "IndexedLineSet", "Shape"]},
                               "usd": {"ok": True}},
        "SimpleSkin": {"probes": "p", "reference": {"ok": True, "errors": []},
                       "blender_x3d40": {"xsd_ok": True, "compare": {**good, "match": 0.3}, "types": ["Shape"]}},
        "UnlitTest": {"probes": "p", "reference": {"ok": True, "errors": []},
                      "blender_x3d40": {"xsd_ok": False, "uom_allows": None, "version": "4.0", "xsd_first": "bad", "compare": good,
                                        "colour_kind": "hue", "types": ["UnlitMaterial"]},
                      "usd": {"ok": False, "failed": ["x"]}},
    }
    claims = interchange.find_claims(r)
    kinds = {(c["kind"], c["sample"]) for c in claims}
    assert ("blender-x3d-lost", "MeshPrimitiveModes") in kinds and not any(c["sample"] == "MeshPrimitiveModes" and c["kind"].startswith("xite") for c in claims)
    skin = next(c for c in claims if c["sample"] == "SimpleSkin")
    assert skin["expected"].startswith("glTF skins")
    assert {("blender-x3d-invalid", "UnlitTest"), ("blender-x3d-colour", "UnlitTest"), ("usd-invalid", "UnlitTest")} <= kinds
    gm = {g["sample"]: g for g in interchange.gap_map(r)}
    assert gm["MeshPrimitiveModes"]["only_xite"] == ["IndexedTriangleFanSet", "PointSet"]
    assert gm["MeshPrimitiveModes"]["only_blender"] == ["IndexedFaceSet", "IndexedLineSet"]
    r.claims = claims
    interchange.write_issues(r)
    issues = sorted((tmp_path / "kit" / "issues").glob("*.md"))
    assert issues and not any("SimpleSkin" in f.name for f in issues)          # expected losses get no issue
    interchange.write(r)
    assert (tmp_path / "report.html").exists() and json.loads((tmp_path / "interchange_card.json").read_text())["gap_map"]


def test_version_sniffing():
    assert interchange._version_of("<X3D profile='Interchange' version='4.1'>") == "4.1"
    assert interchange._version_of('<X3D version="3.3">') == "4.0"
    assert Path(interchange.__file__).name == "interchange.py"
