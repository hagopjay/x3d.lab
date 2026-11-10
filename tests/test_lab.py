"""02 Lab: scene families, the run config, presets, card comparison, and (when bpy is installed) Blender itself."""
import importlib.util
import json
from pathlib import Path

import pytest

from x3dlab import scenegen, stress

HAVE_BPY = importlib.util.find_spec("bpy") is not None


@pytest.mark.parametrize("family", scenegen.FAMILIES)
def test_every_family_is_valid_x3d(lab, family):
    for n in (1, 12):
        xml = scenegen.make(family, n * (300 if family == "meshes" else 1))
        v = lab.check(xml)
        assert v.ok, (family, n, v.report())
    assert scenegen.make(family, 5, seed=3) == scenegen.make(family, 5, seed=3)


def test_config_json_round_trip_and_presets():
    c = stress.RunConfig.preset_for("standard", gpu_kind="L4", seed=11)
    again = stress.RunConfig.from_json(c.to_json())
    assert again == c and again.run_name() == c.run_name() and c.run_name().endswith("-11")
    cpu = stress.RunConfig.preset_for("standard", gpu_kind="CPU")
    g4 = stress.RunConfig.preset_for("standard", gpu_kind="G4")
    assert max(cpu.tiers["render"]["samples"]) < max(c.tiers["render"]["samples"]) < max(g4.tiers["render"]["samples"])
    assert cpu.tiers["render"]["compare_cpu"] is False and "CPU" in cpu.notes


def test_compare_cards():
    a = {"name": "a", "rows": [{"tier": "t", "case": "x", "status": "PASS", "ms": 100, "detail": ""},
                               {"tier": "t", "case": "gone", "status": "PASS", "ms": 1, "detail": ""}]}
    b = {"name": "b", "rows": [{"tier": "t", "case": "x", "status": "WARN", "ms": 100, "detail": "drift"},
                               {"tier": "t", "case": "new", "status": "PASS", "ms": 1, "detail": ""}]}
    changes = {(d["case"], d["change"]) for d in stress.compare_cards(a, b)}
    assert changes == {("x", "PASS → WARN"), ("new", "new"), ("gone", "gone")}


def test_panel_builds_the_same_config():
    pytest.importorskip("ipywidgets")
    from x3dlab import labui
    cfg = stress.RunConfig.preset_for("smoke", gpu_kind="L4", seed=5)
    p = labui.Panel(cfg)
    out = p.config()
    assert out.tiers == cfg.tiers and out.sizes == cfg.sizes and out.seed == 5


@pytest.mark.skipif(not HAVE_BPY, reason="bpy wheel not installed (needs Python 3.13)")
def test_smoke_run_end_to_end(lab):
    if lab.blender is None:
        lab.setup_blender()
    assert lab.blender.info["blender"].startswith("5.")
    run = lab.stress(stress.RunConfig.preset_for("smoke", gpu_kind="CPU"))
    s = run.summary()
    assert all(c["FAIL"] == 0 for c in s.values()), [r for r in run.rows if r.status == "FAIL"]
    assert s["render"]["PASS"] == 3 and s["validate"]["PASS"] == 3
    card = json.loads(run.card_path.read_text())
    assert card["blender"]["blender"] and card["rows"] and (run.dir / "report.html").exists()
    # the known findings of the pinned add-on: 3.3 clamps light intensity, 4.0 splits shared meshes
    details = " ".join(r.detail for r in run.rows)
    assert "instancing lost" in details and "light energy" in details


@pytest.mark.skipif(not HAVE_BPY, reason="bpy wheel not installed (needs Python 3.13)")
def test_blender_reports_why_an_import_failed(lab, tmp_path):
    if lab.blender is None:
        lab.setup_blender()
    bad = tmp_path / "bad.x3d"
    bad.write_text("<X3D><Scene><Transform")
    r = lab.blender.roundtrip(str(bad), "X3D40")
    res = r["result"] or {}
    assert not r["ok"]
    assert "unclosed token" in res.get("reason", ""), res.get("reason")      # the old notebook said "import/export error: ?"


def test_render_ladders_turn_off_adaptive_sampling(tmp_path, monkeypatch):
    """Adaptive sampling stops pixels early, so the samples ladder only means N samples with it off."""
    monkeypatch.setattr(stress.runtime, "sub", lambda *parts: tmp_path)
    cfg = stress.RunConfig.preset_for("smoke", gpu_kind="CPU")
    tasks = stress._render_tasks(stress.StressRun(cfg), "OPTIX", "optix_samples", [("16 spp", 4, 16, [480, 270])])
    assert tasks[0]["adaptive"] is False and tasks[0]["denoise"] is False
    cfg.tiers["render"]["adaptive"] = True
    assert stress._render_tasks(stress.StressRun(cfg), "OPTIX", "x", [("16 spp", 4, 16, [480, 270])])[0]["adaptive"] is True


def test_worker_retries_a_failed_optix_denoiser():
    """The worker runs inside Blender, so check its source: the OptiX-denoiser failure seen on Colab's A100 is retried with OIDN."""
    src = (Path(__file__).parents[1] / "src" / "x3dlab" / "blender_worker.py").read_text()
    body = src[src.index("def task_render"):src.index("KINDS =")]
    assert 'except RuntimeError' in body and '"denoiser" not in str(exc).lower()' in body
    assert body.count("bpy.ops.render.render(write_still=True)") == 2 and 'set_denoiser(scn, denoiser, backend)' in body
    assert '"denoiser": denoiser' in body and "use_adaptive_sampling" in body
