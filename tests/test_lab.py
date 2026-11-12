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
    # 02 cell 4 lists the enabled tiers over stress.TIERS, which also names the 03 research tiers
    assert [t for t in stress.TIERS if out.tiers.get(t, {}).get("on")] == list(labui.LAB_TIERS)
    assert set(p.on) == set(labui.LAB_TIERS) and "ci" not in p.preset.options


def test_02_cell4_survives_research_tiers():
    """The 02 Lab cell 4 line that crashed with KeyError 'matrix' once phase 3 extended stress.TIERS."""
    src = (Path(__file__).parents[1] / "notebooks" / "src" / "02_lab.py").read_text()
    line = next(l for l in src.splitlines() if l.startswith('print("run:"'))
    cfg = stress.RunConfig.preset_for("smoke", gpu_kind="CPU")
    exec(line, {"cfg": cfg, "stress": stress, "print": lambda *a, **k: None})


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


def test_mcp_log_classifier_hides_closed_session_tracebacks():
    """x3d_mcp logs through rich: the message wraps ('Error in standalone SSE' / 'writer') and the traceback is an
    indented box ending in ClosedResourceError. Seen on Colab (A100, 10 Nov 2026) as red ERROR lines."""
    from x3dlab import procs
    c = procs.mcp_log_classifier()
    seen = [c(line) for line in [
        "ERROR    Error in standalone SSE      streamable_http.py:775",
        "         writer",
        "         ╭─ Traceback (most recent ─╮",
        "         │ 3/dist-packages/anyio/st │",
        "         │ ❱ 141 │   │   │   │   │  │",
        "         ╰──────────────────────────╯",
        "         ClosedResourceError",
        "INFO     Processing request of type CallToolRequest",
        "ERROR    Tool validate_x3d failed: boom",
    ]]
    assert seen[:7] == ["DEBUG"] * 7
    assert seen[7] == "DEBUG" and seen[8] == "ERROR"            # chatter stays quiet, a real error is still an error
    c2 = procs.mcp_log_classifier()                               # a box cut short by the next record ends the quiet spell
    assert [c2(x) for x in ["ERROR    Error in standalone SSE writer", "  │ frame │", "ERROR    real failure"]] == ["DEBUG", "DEBUG", "ERROR"]
    c3 = procs.mcp_log_classifier()                               # rich's time column: "[00:34:20]" or blanked with spaces
    assert [c3(x) for x in ["[00:34:20] ERROR    Error in standalone SSE", "                    │ frame │",
                            "           ERROR    Tool failed: boom"]] == ["DEBUG", "DEBUG", "ERROR"]


@pytest.mark.skipif(not HAVE_BPY, reason="bpy wheel not installed (needs Python 3.13)")
def test_convert_keeps_x3d_lines_and_points_in_the_glb(tmp_path):
    """Blender 5.2's glTF exporter names loose edges and points use_mesh_edges / use_mesh_vertices. The convert task
    used to ask for export_loose_* first, which 5.2 refuses, and fell back to options without them: every
    IndexedLineSet and PointSet vanished from the glb (found 12 Nov 2026 while bringing x3d.demo scenes to Unreal)."""
    import struct
    from x3dlab import blender as xblender
    scene = tmp_path / "lines.x3d"
    scene.write_text("<?xml version='1.0' encoding='UTF-8'?><X3D profile='Full' version='4.0'><Scene>"
                     "<DirectionalLight direction='0 0 -1' intensity='0.3'/>"
                     "<Shape><Appearance><Material emissiveColor='1 0 0'/></Appearance><IndexedLineSet coordIndex='0 1 2 -1'>"
                     "<Coordinate point='0 0 0 1 0 0 1 1 0'/></IndexedLineSet></Shape>"
                     "<Shape><PointSet><Coordinate point='0 0 1 1 0 1'/></PointSet></Shape>"
                     "<Shape><Box/></Shape></Scene></X3D>")
    bl = xblender.setup()
    out = tmp_path / "lines.glb"
    r = bl.run([{"kind": "convert", "src": str(scene), "format": "glb", "out": str(out)}], timeout=600, label="test-convert")[0]
    res = r.get("result") or {}
    assert r.get("ok") and not res.get("error"), res
    raw = out.read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    modes = sorted(p.get("mode", 4) for m in gltf["meshes"] for p in m["primitives"])
    assert 1 in modes and 0 in modes and 4 in modes, modes          # LINES, POINTS and the box's TRIANGLES
    assert res["export_options"].get("use_mesh_edges") is True and res["export_options"].get("use_mesh_vertices") is True
    # lights in COMPAT units: the X3D intensity itself (SPEC, Blender's default, wrote 0.3 as 204.9 lux)
    assert res["export_options"].get("export_import_convert_lighting_mode") == "COMPAT"
    lights = gltf["extensions"]["KHR_lights_punctual"]["lights"]
    assert [round(L["intensity"], 3) for L in lights] == [0.3], lights


@pytest.mark.skipif(not HAVE_BPY, reason="bpy wheel not installed (needs Python 3.13)")
def test_convert_leaves_out_what_a_switch_does_not_choose(tmp_path):
    """A Switch draws only the child whichChoice names (none for -1). The add-on imports every child and hides the others
    from the render; the glTF export wrote them anyway, and the electron cloud's hidden "Charge" sphere covered its
    nucleus in X_ITE and Babylon.js (found 12 Nov 2026 comparing x3d.demo scenes with X_ITE)."""
    import struct
    from x3dlab import blender as xblender
    scene = tmp_path / "switch.x3d"
    scene.write_text("<?xml version='1.0' encoding='UTF-8'?><X3D profile='Full' version='4.0'><Scene>"
                     "<Shape DEF='Shown'><Box/></Shape>"
                     "<Switch whichChoice='-1'><Shape DEF='HiddenA'><Sphere/></Shape></Switch>"
                     "<Switch whichChoice='1'><Shape DEF='HiddenB'><Cone/></Shape><Shape DEF='Chosen'><Cylinder/></Shape>"
                     "</Switch></Scene></X3D>")
    bl = xblender.setup()
    out = tmp_path / "switch.glb"
    r = bl.run([{"kind": "convert", "src": str(scene), "format": "glb", "out": str(out)}], timeout=600, label="test-switch")[0]
    res = r.get("result") or {}
    assert r.get("ok") and not res.get("error"), res
    assert res["export_options"].get("use_renderable") is True
    raw = out.read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    assert len(gltf["meshes"]) == 2, [m.get("name") for m in gltf["meshes"]]      # the box and the chosen cylinder


@pytest.mark.skipif(not HAVE_BPY, reason="bpy wheel not installed (needs Python 3.13)")
def test_convert_drops_coordinates_no_face_or_line_uses(tmp_path):
    """A Coordinate shared between a face set and a point set left the face set's unused points as loose vertices,
    and faces with two distinct corners as loose edges: with loose points and edges exported, stray points reached the
    glb (about 1,200 on the Gobbler, 12 Nov 2026). The point set keeps all its points, and a face set made only of
    two-corner quads (drawn as wide lines by the Gobbler's shader) keeps its lines."""
    import struct
    from x3dlab import blender as xblender
    scene = tmp_path / "shared.x3d"
    scene.write_text("<?xml version='1.0' encoding='UTF-8'?><X3D profile='Full' version='4.0'><Scene>"
                     "<Shape><Appearance><Material/></Appearance><IndexedFaceSet coordIndex='0 1 2 -1'>"
                     "<Coordinate DEF='P' point='0 0 0 1 0 0 0 1 0 5 5 5 6 6 6'/></IndexedFaceSet></Shape>"
                     "<Shape><Appearance><Material emissiveColor='1 1 0'/></Appearance><PointSet><Coordinate USE='P'/>"
                     "</PointSet></Shape>"
                     "<Shape><Appearance><Material emissiveColor='0 1 1'/></Appearance>"
                     "<IndexedFaceSet coordIndex='0 0 1 1 -1 1 1 2 2 -1'><Coordinate point='0 3 0 1 3 0 2 3 0 9 9 9'/>"
                     "</IndexedFaceSet></Shape>"
                     "<Shape><Appearance><Material/></Appearance><IndexedFaceSet coordIndex='0 1 2 -1 2 2 3 3 -1'>"
                     "<Coordinate point='0 5 0 1 5 0 0 6 0 3 6 0'/></IndexedFaceSet></Shape></Scene></X3D>")
    bl = xblender.setup()
    out = tmp_path / "shared.glb"
    r = bl.run([{"kind": "convert", "src": str(scene), "format": "glb", "out": str(out)}], timeout=600, label="test-unused")[0]
    res = r.get("result") or {}
    assert r.get("ok") and not res.get("error"), res
    raw = out.read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    acc = gltf["accessors"]
    by_modes = sorted(tuple(sorted(p.get("mode", 4) for p in m["primitives"])) for m in gltf["meshes"])
    assert by_modes == [(0,), (1,), (4,), (4,)], by_modes                     # no stray points or lines
    points = next(p for m in gltf["meshes"] for p in m["primitives"] if p.get("mode") == 0)
    assert acc[points["attributes"]["POSITION"]]["count"] == 5                # the point set keeps every point
    assert res["dropped_unused"] == {"meshes": 3, "vertices": 4, "edges": 1}, res["dropped_unused"]
