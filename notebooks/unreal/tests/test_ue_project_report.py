import io
import json
import math

import pytest
from test_ue_scene import SCENE

from x3due import compare, convert as C, project, report, x3dscene


def fake_engine(root, plugins=("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting", "MovieRenderPipeline",
                               "Interchange")):
    for p in plugins:
        d = root / "Engine/Plugins/X" / p
        d.mkdir(parents=True)
        (d / ("%s.uplugin" % p)).write_text("{}")
    return root


def test_project_written_for_this_engine(tmp_path):
    eng = fake_engine(tmp_path / "eng")
    r = project.write(tmp_path / "proj", eng, ray_tracing=True, cpus=12, workers=5, extra_cvars={"r.Streaming.PoolSize": 28000})
    up = json.loads((tmp_path / "proj/X3DLab.uproject").read_text())
    names = {p["Name"]: p["Enabled"] for p in up["Plugins"]}
    assert names["MovieRenderPipeline"] and names["Interchange"] and "NNEDenoiser" not in names   # the engine lacks it
    assert r["missing_required"] == []
    ini = (tmp_path / "proj/Config/DefaultEngine.ini").read_text()
    assert "+TargetedRHIs=SF_VULKAN_SM6" in ini and "r.RayTracing=True" in ini and "NumUnusedShaderCompilingThreads=7" in ini
    assert "r.Streaming.PoolSize=28000" in ini and "{{" not in ini
    assert (tmp_path / "proj/Content/Python/init_unreal.py").exists()
    r2 = project.write(tmp_path / "proj", fake_engine(tmp_path / "eng2", ("PythonScriptPlugin",)), sm6=False)
    assert set(r2["missing_required"]) == {"EditorScriptingUtilities", "SequencerScripting", "MovieRenderPipeline"}
    assert "+TargetedRHIs=SF_VULKAN_SM5" in (tmp_path / "proj/Config/DefaultEngine.ini").read_text()


def test_import_job_is_data_and_valid(tmp_path):
    xml, _ = x3dscene.ensure_viewpoint_defs(SCENE)
    m = x3dscene.manifest(xml)
    job = project.import_job("still life!", tmp_path / "s.glb", "gltf", m, tmp_path / "r.json", tmp_path)
    assert job["scene"] == "still_life" and job["map"] == "/Game/X3DLab/still_life/Map_still_life"
    assert set(job["placements"]) == set(C.AXES)
    st = job["placements"]["x,z,y"]
    cam = st["cameras"][0]
    assert cam["name"] == "Cam" and cam["headlight"] is None                    # NavigationInfo headlight="false"
    assert cam["location"] == pytest.approx((0, 1000, 0))
    assert 2 * math.degrees(math.atan(cam["sensor_height"] / 2 / cam["focal_length"])) == pytest.approx(math.degrees(0.6))
    kinds = sorted(L["kind"] for L in st["lights"])
    assert kinds == ["directional", "spot"]
    sp = next(L for L in st["lights"] if L["kind"] == "spot")
    assert sp["unit"] == "cd" and sp["inner_deg"] == pytest.approx(math.degrees(0.5)) and sp["radius_cm"] == pytest.approx(10000)
    json.dumps(job)                                                              # serialisable
    bad = dict(job, scene="bad name")
    with pytest.raises(AssertionError):
        project.validate_import_job(bad)


def test_render_jobs_valid_and_key_variant(tmp_path):
    rung = {"resolution": [3840, 2160], "spatial_samples": 512, "temporal_samples": 1, "renderer": "pathtracer",
            "cvars": {"r.PathTracing.MaxBounces": 16}, "tiles": 1, "warmup_frames": 32, "texture_streaming": "fully_load"}
    spec = project.render_jobs("s", ["Cam", "x3dlab_vp_1"], "ultra", rung, tmp_path, tmp_path / "r.json")
    assert [j["name"] for j in spec["jobs"]] == ["s__Cam__ultra", "s__x3dlab_vp_1__ultra"]
    assert spec["jobs"][0]["renderer"] == "pathtracer" and spec["jobs"][0]["formats"] == ["png", "exr"]
    k = project.render_jobs("s", ["Cam"], "preview", rung, tmp_path, tmp_path / "r.json", look="match", key=True, override_res=(960, 540))
    j = k["jobs"][0]
    assert j["backdrop"] == "key" and j["resolution"] == [960, 540] and j["spatial"] == 1 and j["renderer"] == "lumen"
    assert j["cvars"]["r.DynamicGlobalIlluminationMethod"] == 0
    rung["cvars"]["bad cvar; quit"] = 1
    with pytest.raises(AssertionError):
        project.render_jobs("s", ["Cam"], "ultra", rung, tmp_path, tmp_path / "r.json")


def test_scorecard_rules():
    sc = report.Scorecard()
    sc.add("a", "x", "PASS", "fine")
    sc.add("a", "x", "WARN", "replaced")
    sc.add("b", "y", "SKIP", "not run: no GPU")
    assert sc.counts() == {"PASS": 0, "WARN": 1, "FAIL": 0, "SKIP": 1}
    with pytest.raises(ValueError):
        sc.add("c", "z", "OK", "x")
    with pytest.raises(ValueError):
        sc.add("c", "z", "PASS", " ")
    assert "WARN" in sc.html() and sc.word() == "passed, with warnings"


def test_accounting_rows():
    glb = {"counts": {"mesh_nodes": 5, "meshes": 4, "shared_meshes": 1, "triangles": 1000, "materials": 3, "textures": 2,
                      "lights": 3, "cameras": 1}}
    ue = {"imported": {"mesh_actors": 5, "static_meshes_used": 4, "shared_static_meshes": 1, "triangles": 1004,
                       "assets_by_class": {"MaterialInstanceConstant": 3, "Texture2D": 2, "StaticMesh": 4},
                       "actors_by_class": {"StaticMeshActor": 5, "DirectionalLight": 3, "CineCameraActor": 1}},
          "axes": {"axes": "x,z,y", "errors_cm": {"x,z,y": 0.2}, "fits": True, "ambiguous": False}}
    rows = {r["what"]: r for r in report.account(glb, ue)}
    assert all(r["status"] == "PASS" for r in rows.values()), rows
    ue["imported"]["shared_static_meshes"] = 0
    assert {r["what"]: r for r in report.account(glb, ue)}["shared meshes (instancing) kept"]["status"] == "WARN"
    assert report.account({"counts": {}}, {})[0]["status"] == "SKIP"


def _png(color, size=(64, 36), box=None, boxcolor=(200, 30, 30)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, color)
    if box:
        ImageDraw.Draw(im).rectangle(box, fill=boxcolor)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_compare_judge_uses_the_labs_referee_and_ssim():
    from x3dlab import formats
    a = _png((255, 0, 255), box=(10, 10, 40, 30))
    same = compare.judge(a, a, formats)
    assert same["match"] == 1.0 and same["ssim"] == pytest.approx(1.0) and compare.verdict(same) == "PASS"
    shifted = compare.judge(a, _png((255, 0, 255), box=(11, 10, 41, 30)), formats)
    assert shifted["match"] > 0.95 > shifted["iou"] - 0.0 or shifted["match"] >= shifted["iou"]
    gone = compare.judge(a, _png((255, 0, 255)), formats)
    assert gone["match"] == 0.0 and compare.verdict(gone) == "FAIL"
    dark = compare.judge(a, _png((255, 0, 255), box=(10, 10, 40, 30), boxcolor=(40, 5, 5)), formats)
    assert dark["match"] == 1.0 and dark["colour_kind"] == "darker" and compare.verdict(dark) == "WARN"


def test_contact_sheet(tmp_path):
    a = _png((255, 0, 255), box=(10, 10, 40, 30))
    out = compare.contact_sheet([{"label": "s · Cam", "cells": [("Unreal", a), ("X_ITE", None)]}], tmp_path / "sheet.png")
    from PIL import Image
    assert Image.open(out).size[0] > 900


def test_gltf_control_frames_its_bounds():
    xml, vp = compare.gltf_control_x3d("ToyCar.glb", ((-1, 0, -2), (1, 1, 2)))
    m = x3dscene.manifest(xml)
    assert m["viewpoints"][0]["DEF"] == "x3dlab_auto" and m["headlight"] is True and 'url=\'"ToyCar.glb"\'' in xml
    f = C.mat3_vec(C.axis_angle_to_mat3(vp["orientation"][:3], vp["orientation"][3]), (0, 0, -1))
    to_c = C.norm(tuple(c - p for c, p in zip(vp["centerOfRotation"], vp["position"])))
    assert C.dot(f, to_c) == pytest.approx(1.0, abs=1e-6)                       # looks at the centre
