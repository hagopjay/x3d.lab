"""The scripts that run inside Unreal, driven by a stand-in `unreal` module (tests/fixtures/unreal_stub.py).
This checks they compile and follow their flow; the real API names are checked by the first live run."""
import importlib.util
import json
import py_compile
import sys
from pathlib import Path

import pytest
import unreal_stub
from test_ue_scene import SCENE

from x3due import convert as C, project, x3dscene

UNREAL = Path(__file__).resolve().parents[1]
IMPORT = UNREAL / "ue_scripts" / "x3dlab_import.py"
RENDER = UNREAL / "ue_project" / "Content" / "Python" / "x3dlab_render.py"
INIT = UNREAL / "ue_project" / "Content" / "Python" / "init_unreal.py"


@pytest.mark.parametrize("f", [IMPORT, RENDER, INIT])
def test_compiles(f):
    py_compile.compile(str(f), doraise=True)


def _run_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("axes", ["x,z,y", "z,-x,y"])
def test_import_script_flow(tmp_path, axes):
    unreal_stub.install()
    xml, _ = x3dscene.ensure_viewpoint_defs(SCENE)
    m = x3dscene.manifest(xml)
    job = project.import_job("demo", tmp_path / "demo.glb", "gltf", m, tmp_path / "import_report.json", UNREAL)
    jp = tmp_path / "job.json"
    jp.write_text(json.dumps(job))
    unreal_stub.STATE.update(cmdline="UnrealEditor-Cmd X.uproject -run=pythonscript -X3DLabJob=%s" % jp,
                             import_axes=axes, import_points=m["shape_centres"])
    _run_script(IMPORT, "x3dlab_import_%s" % axes.replace(",", "_").replace("-", "m"))
    rep = json.loads((tmp_path / "import_report.json").read_text())
    assert rep["ok"], rep["errors"]
    assert rep["axes"]["axes"] == axes and rep["axes"]["fits"]                   # the importer's mapping was found
    imp = rep["imported"]
    assert imp["mesh_actors"] == len(m["shape_centres"]) and imp["static_meshes_used"] == 2
    assert imp["shared_static_meshes"] == 2 and imp["actors_by_class"]["DirectionalLight"] == 1
    assert rep["lights_removed"] == 1 and sorted(rep["lights_placed"]) == ["DirectionalLight_0", "SpotLight_1"]
    assert rep["cameras_placed"] == ["Cam", "x3dlab_vp_1"] and set(rep["sequences"]) == {"Cam", "x3dlab_vp_1"}
    assert rep["backdrop"] == ["Backdrop", "BackdropKey"] and rep["looks"] == ["match", "cinematic"]
    actors = unreal_stub.STATE["actors"]
    assert sum(1 for a in actors if "x3dlab_camera" in a.tags) == 2
    assert not any("x3dlab_headlight" in a.tags for a in actors)                  # headlight="false" in the scene


def test_import_script_reports_a_failure(tmp_path):
    unreal_stub.install()
    unreal_stub.STATE["cmdline"] = "-X3DLabJob=%s" % (tmp_path / "missing.json")
    _run_script(IMPORT, "x3dlab_import_fail")
    rep = json.loads((tmp_path / "import_report.json").read_text())
    assert not rep["ok"] and "FileNotFoundError" in rep["errors"][0]


def test_render_executor_flow(tmp_path):
    unreal = unreal_stub.install()
    rung = {"resolution": [1920, 1080], "spatial_samples": 1, "temporal_samples": 16, "renderer": "lumen", "cvars": {"r.ScreenPercentage": 100}}
    spec = project.render_jobs("demo", ["Cam", "x3dlab_vp_1"], "high", rung, tmp_path / "stills", tmp_path / "render_report.json")
    jp = tmp_path / "rj.json"
    jp.write_text(json.dumps(spec))
    unreal_stub.STATE["cmdline"] = "UnrealEditor X.uproject -game -X3DLabJob=%s" % jp
    a1 = unreal_stub.Actor("DirectionalLight")
    a1.set_editor_property("tags", ["x3dlab_headlight", "x3dlab_headlight_Cam"])
    unreal_stub.STATE["actors"].append(a1)
    mod = _run_script(RENDER, "x3dlab_render")
    ex = mod.X3DLabExecutor()
    ex.execute_delayed(None)
    assert unreal_stub.STATE["opened"] == ["/Game/X3DLab/demo/Map_demo"]
    for k in range(2):
        ex.on_map_load(None)
        assert ex.is_rendering()
        (tmp_path / "stills").mkdir(exist_ok=True)
        (tmp_path / "stills" / (spec["jobs"][k]["name"] + ".0000.png")).write_bytes(b"png")
        ex.on_job_finished(unreal_stub.Obj("results", success=True))
    rep = json.loads((tmp_path / "render_report.json").read_text())
    assert rep["final"] and [j["name"] for j in rep["jobs"]] == ["demo__Cam__high", "demo__x3dlab_vp_1__high"]
    assert all(j["success"] and len(j["files"]) == 1 for j in rep["jobs"])
    assert (tmp_path / "render_report.json.done").exists()
    assert "executor finished" in unreal_stub.LOG
    assert len(unreal_stub.STATE["pipelines"]) == 2                              # a fresh pipeline (and world) per still


def test_render_applies_a_grade(tmp_path):
    unreal_stub.install()
    rung = {"resolution": [1280, 720], "spatial_samples": 8, "temporal_samples": 1, "renderer": "lumen", "cvars": {"r.DepthOfFieldQuality": 0}}
    grade = {"post": {"bloom_intensity": 1.2, "auto_exposure_bias": -1.5, "auto_exposure_method": "AEM_MANUAL"}, "light_scale": 2.0,
             "headlight": False, "cvars": {"r.DepthOfFieldQuality": 2}}
    spec = project.render_jobs("gob", ["Front"], "high", rung, tmp_path / "stills", tmp_path / "rr.json", grade=grade, tag="glow2")
    j = spec["jobs"][0]
    assert j["name"] == "gob__Front__high__glow2" and j["cvars"]["r.DepthOfFieldQuality"] == 2 and "cvars" not in j["grade"]
    jp = tmp_path / "rj.json"
    jp.write_text(json.dumps(spec))
    unreal_stub.STATE["cmdline"] = "UnrealEditor X.uproject -game -X3DLabJob=%s" % jp
    vol = unreal_stub.Actor("PostProcessVolume")
    vol.set_editor_property("tags", ["x3dlab_look", "x3dlab_look_cinematic"])
    light = unreal_stub.Actor("PointLight")
    light.set_editor_property("tags", ["x3dlab_light"])
    head = unreal_stub.Actor("DirectionalLight")
    head.set_editor_property("tags", ["x3dlab_headlight", "x3dlab_headlight_Front"])
    unreal_stub.STATE["actors"] += [vol, light, head]
    mod = _run_script(RENDER, "x3dlab_render")
    ex = mod.X3DLabExecutor()
    ex.execute_delayed(None)
    ex.on_map_load(None)
    s = vol.get_editor_property("settings")
    assert s.get_editor_property("bloom_intensity") == 1.2 and s.get_editor_property("override_bloom_intensity") is True
    assert s.get_editor_property("auto_exposure_bias") == -1.5
    applied = mod.STATE["report"]["grade_applied"]
    assert applied["post"] == ["auto_exposure_bias", "auto_exposure_method", "bloom_intensity"] and applied["headlight"] is False


def test_grade_is_validated():
    import pytest
    rung = {"resolution": [64, 64], "spatial_samples": 1, "temporal_samples": 1, "renderer": "lumen", "cvars": {}}
    for bad in ({"post": {"override_bloom_intensity": True}}, {"post": {"bloom_intensity": float("nan")}}, {"lights": 2},
                {"cvars": {"r.X; quit": 1}}):
        with pytest.raises(AssertionError):
            project.render_jobs("g", ["A"], "high", rung, "/tmp/o", "/tmp/r.json", grade=bad)
