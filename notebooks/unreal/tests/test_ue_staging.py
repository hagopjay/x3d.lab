"""The first A100 run (11 Nov 2026), step 6 to step 9: what went wrong and the fixes.

- the shader_format signature fired on healthy logs and on Unreal's "from hinted modules" lookup;
- a failing retry left the whole run on Shader Model 5 and deleted the import script;
- a rerun of step 8 found the previous run's map and made 0 sequences;
- A Material Odyssey is dark at time 0 (its lights fade up through a TimeSensor): freeze it lit;
- ToyCar is 4 cm with its viewpoints 10 m away: frame it with x3dlab_auto, outside the near clip.
"""
import json
import math
import shutil
from pathlib import Path

import samples as S
from test_ue_pipeline import FakeUnreal, make_lab

from x3due import pipeline, project, staging, uerun, x3dscene

UNREAL = Path(__file__).resolve().parents[1]

HEALTHY_A100 = S.UE_LOG_VULKAN_OK + """
[2026.11.11-11.30.24:806][  0]LogTargetPlatformManager: Unable to find shader format SF_VULKAN_SM5 from hinted modules, loading all potential format modules to find it
[2026.11.11-11.30.24:809][  0]LogTargetPlatformManager: Loaded format module VulkanShaderFormat
[2026.11.11-11.14.30:001][  0]LogShaderCompilers: Display: Using ShaderFormatVersion 0x1234 for SF_VULKAN_SM6
"""

# A tour: one TimeSensor; the spot light is dark at t=0, full from 0.4 to 0.8; the car moves; the camera turns.
TOUR = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0"><Scene>
<NavigationInfo headlight="false"/>
<Viewpoint DEF="Cam" position="0 1 8" orientation="0 1 0 0"/>
<SpotLight DEF="Spot" location="0 4 0" direction="0 -1 0" intensity="0"/>
<Transform DEF="Car" translation="0 0 0"><Shape><Box size="1 1 1"/></Shape></Transform>
<TimeSensor DEF="Clock" cycleInterval="20" loop="true"/>
<ScalarInterpolator DEF="SpotI" key="0 0.3 0.4 0.8 0.9 1" keyValue="0 0 1 1 0 0"/>
<PositionInterpolator DEF="CarP" key="0 1" keyValue="0 0 0, 10 0 0"/>
<OrientationInterpolator DEF="CamO" key="0 1" keyValue="0 1 0 0, 0 1 0 3.14159"/>
<ROUTE fromNode="Clock" fromField="fraction_changed" toNode="SpotI" toField="set_fraction"/>
<ROUTE fromNode="Clock" fromField="fraction_changed" toNode="CarP" toField="set_fraction"/>
<ROUTE fromNode="Clock" fromField="fraction_changed" toNode="CamO" toField="set_fraction"/>
<ROUTE fromNode="SpotI" fromField="value_changed" toNode="Spot" toField="set_intensity"/>
<ROUTE fromNode="CarP" fromField="value_changed" toNode="Car" toField="set_translation"/>
<ROUTE fromNode="CamO" fromField="value_changed" toNode="Cam" toField="set_orientation"/>
</Scene></X3D>
"""

# ToyCar's shape: the Web3D conversion scales the car by 0.0001 (it is about 4 cm long); its cameras are 10 m away.
TINY = """<X3D profile="Interchange" version="4.0"><Scene>
<Viewpoint DEF="Camera001_2" position="0 0 10"/>
<Transform scale="0.0001 0.0001 0.0001"><Shape><Box size="400 200 150"/></Shape></Transform>
</Scene></X3D>
"""


# ---------------------------------------------------------------------- the shader_format signature
def test_healthy_logs_are_not_shader_format_failures():
    proof = uerun.rhi_proof(HEALTHY_A100)
    assert "shader_format" not in [c["signature"] for c in proof["crashes"]]
    assert proof["verdict"] == "PASS"


def test_real_shader_format_errors_still_count():
    for line in ("LogShaderCompilers: Error: No target platform supports shader format SF_VULKAN_SM6",
                 "LogTargetPlatformManager: Error: Unable to find shader format SF_VULKAN_SM6"):
        assert "shader_format" in [c["signature"] for c in uerun.rhi_proof(S.UE_LOG_VULKAN_OK + line + "\n")["crashes"]]


# ---------------------------------------------------------------------- retries, the import script, reruns
def test_a_retry_that_does_not_help_is_rolled_back(tmp_path):
    bad = S.UE_LOG_VULKAN_OK + "LogShaderCompilers: Error: No target platform supports shader format SF_VULKAN_SM6\nAssertion failed: x\n"
    lab, _ = make_lab(tmp_path, FakeUnreal([bad, bad, bad, bad]))
    lab.write_project()
    assert lab.settings["sm6"]
    res = pipeline.run_unreal(lab, pipeline.smoke_cmd, "smoke_t", tmp_path / "logs", need_render=False)
    assert [a["change"] for a in res["attempts"]][1] == "Shader Model 5, no ray tracing"
    assert lab.settings["sm6"] is True                      # put back: the next job runs on SM6 again
    assert "SF_VULKAN_SM6" in (lab.project_dir / "Config" / "DefaultEngine.ini").read_text()


def test_a_retry_that_helps_is_kept(tmp_path):
    bad = S.UE_LOG_VULKAN_OK + "LogShaderCompilers: Error: No target platform supports shader format SF_VULKAN_SM6\n"
    bad = bad.replace("LogInit", "LogInit", 1)
    runner = FakeUnreal([bad, S.UE_LOG_VULKAN_OK])
    orig = runner.__call__

    def call(cmd, log_path, **kw):                       # the first attempt fails (rc 1), the SM5 retry succeeds
        r = orig(cmd, log_path, **kw)
        r["rc"] = 1 if len(runner.calls) == 1 else 0
        return r
    lab, _ = make_lab(tmp_path, call)
    lab.write_project()
    pipeline.run_unreal(lab, pipeline.smoke_cmd, "smoke_t", tmp_path / "logs", need_render=False)
    assert lab.settings["sm6"] is False


def test_rewriting_the_project_keeps_the_import_script(tmp_path):
    dest = tmp_path / "X3DLab"
    project.write(dest)
    assert (dest / "Content" / "Python" / "x3dlab_import.py").exists()
    project.write(dest)                                  # what a retry does
    assert (dest / "Content" / "Python" / "x3dlab_import.py").exists()


def test_reimport_starts_from_a_clean_scene_folder(tmp_path):
    lab, _ = make_lab(tmp_path, FakeUnreal())
    lab.write_project()
    stale = lab.project_dir / "Content" / "X3DLab" / "calib" / "Map_calib.umap"
    stale.parent.mkdir(parents=True)
    stale.write_text("from the first run")
    shared = lab.project_dir / "Content" / "X3DLab" / "Shared" / "M_Backdrop.uasset"
    shared.parent.mkdir(parents=True)
    shared.write_text("kept")
    m = x3dscene.manifest(TINY)
    glb = tmp_path / "x.glb"
    glb.write_bytes(b"glTF")
    pipeline.import_scene(lab, "calib", str(glb), "gltf", m)
    assert not stale.exists() and shared.exists()


def test_usd_setting_turns_the_plugin_on(tmp_path):
    lab, _ = make_lab(tmp_path, FakeUnreal())
    (lab.engine_dir / "Engine" / "Plugins" / "Importers" / "USDImporter").mkdir(parents=True, exist_ok=True)
    (lab.engine_dir / "Engine" / "Plugins" / "Importers" / "USDImporter" / "USDImporter.uplugin").write_text("{}")
    lab.settings["usd"] = True
    names = [p["Name"] for p in lab.write_project()["plugins"] if p["Enabled"]]
    assert "USDImporter" in names


# ---------------------------------------------------------------------- freezing an animated scene
def test_dark_at_time_zero_is_frozen_at_the_lit_moment():
    out, rep = staging.freeze(TOUR)
    assert rep["lights_t0"] == 0 and 0.4 <= rep["fraction"] <= 0.8 and rep["lights_frozen"] == 1
    m = x3dscene.manifest(out)
    assert m["lights"][0]["intensity"] == 1
    car = x3dscene.parse(out).find(".//*[@DEF='Car']")
    assert abs(float(car.get("translation").split()[0]) - 10 * rep["fraction"]) < 1e-3
    cam = x3dscene.parse(out).find(".//*[@DEF='Cam']")
    assert abs(float(cam.get("orientation").split()[3]) - 3.14159 * rep["fraction"]) < 1e-3
    assert out.count("<ROUTE") == TOUR.count("<ROUTE")    # behaviour left for the comparison to strip


def test_lit_scenes_and_still_scenes_are_left_alone():
    lit = TOUR.replace('intensity="0"', 'intensity="1"').replace('keyValue="0 0 1 1 0 0"', 'keyValue="1 1 1 1 1 1"')
    assert staging.freeze(lit)[0] == lit
    assert staging.freeze(TINY) == (TINY, staging.freeze(TINY)[1]) and staging.freeze(TINY)[1]["driven"] == 0
    out, rep = staging.freeze(TOUR, 0.95)                 # a fraction given explicitly is honoured
    assert rep["fraction"] == 0.95 and x3dscene.manifest(out)["lights"][0]["intensity"] == 0


def test_interpolation_is_x3d_linear_and_held_at_the_ends():
    keys, vals = [0.2, 0.6], [(0.0,), (4.0,)]
    assert staging.evaluate("ScalarInterpolator", keys, vals, 0.0) == (0.0,)
    assert abs(staging.evaluate("ScalarInterpolator", keys, vals, 0.4)[0] - 2.0) < 1e-12
    assert staging.evaluate("ScalarInterpolator", keys, vals, 1.0) == (4.0,)
    half = staging.evaluate("OrientationInterpolator", [0, 1], [(0, 1, 0, 0), (0, 1, 0, math.pi / 2)], 0.5)
    assert abs(half[3] - math.pi / 4) < 1e-9 and abs(half[1] - 1) < 1e-9


# ---------------------------------------------------------------------- framing
def test_framing_verdicts():
    box = ((-1, -1, -1), (1, 1, 1))
    vp = lambda pos, rot=(0, 1, 0, 0): {"type": "Viewpoint", "position": pos, "orientation": rot, "fieldOfView": 0.7854}
    assert staging.framing(vp((0, 0, 6)), box)["verdict"] == "frames"
    assert staging.framing(vp((0, 0, 6), (0, 1, 0, math.pi)), box)["verdict"] == "misses"
    assert staging.framing(vp((0, 0, 6000)), box)["verdict"] == "tiny"
    assert staging.framing(vp((0, 0, 0.5)), box)["verdict"] == "inside"


def test_toycar_gets_a_camera_it_can_be_seen_from():
    m = x3dscene.manifest(TINY)
    fr = staging.frame_all(m)
    assert fr["Camera001_2"]["verdict"] == "tiny" and staging.needs_auto(fr)
    out, vp = staging.add_auto_viewpoint(TINY, m["bounds"])
    assert out.index('DEF="x3dlab_auto"') > out.index('DEF="Camera001_2"')       # the authored one stays first
    d = math.dist(vp["position"], vp["centerOfRotation"])
    assert d >= 1.0                                                              # outside both engines' near clip
    fr2 = staging.frame_all(x3dscene.manifest(out))
    assert fr2["x3dlab_auto"]["verdict"] == "frames" and not staging.needs_auto(fr2)
    assert staging.add_auto_viewpoint(out, m["bounds"])[0] == out                 # once only


def test_pick_puts_the_viewpoints_that_show_the_model_first():
    fr = {"A": {"verdict": "tiny"}, "B": {"verdict": "frames"}, "C": {"verdict": "inside"}, "x3dlab_auto": {"verdict": "frames"}}
    assert staging.pick(["A", "B", "C", "x3dlab_auto"], fr, 3) == ["B", "C", "x3dlab_auto"]
    assert staging.pick(["A", "x3dlab_auto"], fr, 4) == ["x3dlab_auto"]
    assert staging.pick(["A", "D"], {"A": {"verdict": "tiny"}}, 4) == ["D"]       # unknown names count as framing
    assert staging.pick(["A"], {"A": {"verdict": "misses"}}, 4) == ["A"]          # nothing better: still render it
