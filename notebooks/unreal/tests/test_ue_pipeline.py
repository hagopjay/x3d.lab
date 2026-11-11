"""The notebook's steps end to end with a fake Unreal: the runner writes logs and (by running the real Unreal-side
scripts against the stand-in `unreal` module) the reports the steps read."""
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest
import samples as S
import unreal_stub
from test_ue_engine import fake_zip
from test_ue_scene import SCENE

from x3due import convert as C, engine, pipeline, tier, x3dscene

UNREAL = Path(__file__).resolve().parents[1]


class FakeUnreal:
    """Plays Unreal: per call, writes the next canned log; for commandlets/renders runs the real script on the stub."""

    def __init__(self, logs=None):
        self.logs = list(logs or [])
        self.calls = []

    def __call__(self, cmd, log_path, user=None, env_vars=None, timeout=None, done_file=None, **kw):
        self.calls.append({"cmd": cmd, "user": user, "env": env_vars})
        text = self.logs.pop(0) if self.logs else S.UE_LOG_VULKAN_OK
        job = next((a.split("=", 1)[1] for a in cmd if a.startswith("-X3DLabJob=")), None)
        if job and "-run=pythonscript" in cmd:
            self._script(UNREAL / "ue_scripts" / "x3dlab_import.py", job)
        elif job:
            self._render(job)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(text)
        crashed = "DEVICE_LOST" in text
        return {"rc": 1 if crashed else 0, "seconds": 1.0, "killed": None, "gpu": {"peak_mem_mb": 1234.0}, "log": str(log_path), "cmd": cmd}

    def _script(self, path, job):
        unreal_stub.install()
        j = json.loads(Path(job).read_text())
        unreal_stub.STATE.update(cmdline="-X3DLabJob=%s" % job, import_points=j["shape_centres_m"], import_axes="x,z,y")
        spec = importlib.util.spec_from_file_location("x3dlab_import_run", path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

    def _render(self, job):
        spec = json.loads(Path(job).read_text())
        rep = {"jobs": [], "final": True}
        for j in spec["jobs"]:
            os.makedirs(j["output_dir"], exist_ok=True)
            from PIL import Image
            Image.new("RGB", tuple(j["resolution"]), (188, 188, 188)).save(os.path.join(j["output_dir"], j["name"] + ".0000.png"))
            rep["jobs"].append({"name": j["name"], "seconds": 2.0, "success": True})
        Path(spec["report"]).write_text(json.dumps(rep))
        Path(spec["report"] + ".done").write_text("done")


def make_lab(tmp_path, runner, rt=False):
    hw = tier.detect(S.SMI_QUERY["L4"], S.SMI_HEADER_A100, S.MEMINFO["A100-80"], cpus=8, disk_path="/")
    plan = tier.plan(hw, {"nvidia_device": {"extensions": list(tier.RT_EXTENSIONS) if rt else []}})
    eng = tmp_path / "engine"
    z = tmp_path / "drive" / "Linux_Unreal_Engine_5.8.4.zip"
    z.parent.mkdir()
    fake_zip(z)
    lab = pipeline.Lab(work=tmp_path / "work", engine_dir=eng, hw=hw, plan=plan, log=lambda *a: None, runner=runner)
    return lab, z.parent


def test_get_engine_from_zip_then_cache_then_restore(tmp_path):
    lab, drive = make_lab(tmp_path, FakeUnreal())
    got = pipeline.get_engine(lab, "auto", drive)
    assert got["source"] == "drive zip" and got["version"] == "5.8.4" and got["strategy"]["mode"] in ("copy_then_extract", "extract_from_drive")
    c = pipeline.cache_engine(lab, drive, "5.8.4")
    assert Path(c["archive"]).exists()
    assert pipeline.cache_engine(lab, drive, "5.8.4")["skipped"] == "already cached"
    shutil.rmtree(lab.engine_dir)
    again = pipeline.get_engine(lab, "auto", drive)
    assert again["source"] == "drive cache" and again["ok"]


def test_get_engine_says_what_to_do(tmp_path):
    lab, drive = make_lab(tmp_path, FakeUnreal())
    for f in drive.glob("*.zip"):
        f.unlink()
    with pytest.raises(RuntimeError, match="one-time step"):
        pipeline.get_engine(lab, "auto", drive)


def test_smoke_retries_device_lost_with_async_compute_off(tmp_path):
    fake = FakeUnreal([S.UE_LOG_DEVICE_LOST, S.UE_LOG_VULKAN_OK])
    lab, drive = make_lab(tmp_path, fake, rt=True)
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    r = pipeline.smoke(lab)
    assert r["proof"]["verdict"] == "PASS" and len(r["attempts"]) == 2 and r["attempts"][1]["change"] == "async compute off"
    assert "r.Vulkan.AllowAsyncCompute=0" in (lab.project_dir / "Config/DefaultEngine.ini").read_text()
    cmd = fake.calls[0]["cmd"]
    assert "-RenderOffscreen" in cmd and "-vulkan" in cmd and fake.calls[0]["user"] == "ue"
    assert fake.calls[0]["env"]["UE-LocalDataCachePath"].endswith("ddc")


def test_bindless_crash_switches_the_engine_config(tmp_path):
    fake = FakeUnreal([S.UE_LOG_BINDLESS, S.UE_LOG_VULKAN_OK])
    lab, drive = make_lab(tmp_path, fake, rt=True)
    pipeline.get_engine(lab, "zip", drive)
    engine.enable_vulkan_rt(lab.engine_dir)
    lab.write_project()
    r = pipeline.smoke(lab)
    assert r["attempts"][1]["change"] == "bindless for all shader types"
    assert "BindlessSupport=AllShaderTypes" in (lab.engine_dir / "Engine/Config/VulkanPC/DataDrivenPlatformInfo.ini").read_text()


def test_import_render_calibrate_end_to_end(tmp_path):
    fake = FakeUnreal()
    lab, drive = make_lab(tmp_path, fake)
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    xml, _ = x3dscene.ensure_viewpoint_defs(SCENE)
    m = x3dscene.manifest(xml)
    res = pipeline.import_scene(lab, "demo", str(tmp_path / "demo.glb"), "gltf", m)
    assert res["report"]["ok"] and res["report"]["axes"]["axes"] == "x,z,y"
    cams = sorted(res["report"]["sequences"])
    out = pipeline.render(lab, "demo", cams, "high", lab.plan["ladder"]["high"])
    assert out["proof"]["verdict"] == "PASS" and len(out["stills"]) == 2 and all(s["png"] for s in out["stills"])
    assert out["stills"][0]["seconds"] == 2.0
    cal = pipeline.calibration(out["stills"][0]["png"], 1.2)
    # 188/255 sRGB = 0.503 linear; X3D expects 0.8, so Unreal drew it 0.629x: K = 1.2 / 0.629
    assert cal["use"] and cal["k_measured"] == pytest.approx(1.2 / (pipeline.srgb_to_linear(188) / 0.8), rel=1e-3)
    m2 = C.LightMapping(measured_k=cal["k_measured"])
    assert m2.directional_lux(1) > C.LightMapping().directional_lux(1)


def test_calibration_rejects_saturation(tmp_path):
    from PIL import Image
    p = tmp_path / "white.png"
    Image.new("RGB", (64, 36), (255, 255, 255)).save(p)
    c = pipeline.calibration(p, 1.2)
    assert c["saturated"] and not c["use"]


def test_render_grades_runs_every_grade_in_one_start(tmp_path):
    fake = FakeUnreal()
    lab, drive = make_lab(tmp_path, fake)
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    rung = {"resolution": [320, 180], "spatial_samples": 4, "temporal_samples": 1, "renderer": "lumen", "cvars": {}}
    grades = {"glow": {"post": {"bloom_intensity": 1.0}}, "film": {"post": {"vignette_intensity": 0.4}, "headlight": False}}
    out = pipeline.render_grades(lab, "gob", ["EyeToEye", "Crown"], "round", rung, grades, label="r1")
    assert len(fake.calls) == 1 and len(out["spec"]["jobs"]) == 4
    assert set(out["stills"]) == {("glow", "EyeToEye"), ("glow", "Crown"), ("film", "EyeToEye"), ("film", "Crown")}
    assert all(p and p.endswith(".png") for p in out["stills"].values())
    assert {j["name"] for j in out["spec"]["jobs"]} >= {"gob__EyeToEye__round__glow", "gob__Crown__round__film"}
