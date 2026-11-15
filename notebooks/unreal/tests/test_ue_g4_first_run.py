"""Fixes from the first Unreal 5.8.4 run on a Colab G4 (14-15 Nov 2026): the desktop libraries 5.8 links, 6c's false
crash on UnrealBuildTool's AutoSDK segfault, `lines` no longer shadowed, git's safe.directory for the add-on checkout,
Nanite geometry for ray tracing, and the per-scene exposure fit."""
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from x3due import engine as E, engines, pipeline, tier

UNREAL = Path(__file__).resolve().parents[1]
SRC = (UNREAL / "src" / "07_unreal.py").read_text()


# ---------------------------------------------------------------------- system libraries
def test_missing_system_libs_separates_bundled_from_system(tmp_path):
    b = tmp_path / "Engine/Binaries/Linux"
    b.mkdir(parents=True)
    (b / "UnrealEditor").write_text("")
    (b / "libUnrealEditor-Core.so").write_text("")
    (tmp_path / "Engine/Binaries/ThirdParty/Boost").mkdir(parents=True)
    (tmp_path / "Engine/Binaries/ThirdParty/Boost/libboost_atomic.so.1.85.0").write_text("")
    ldd = ("\tlibatk-1.0.so.0 => not found\n\tlibboost_atomic.so.1.85.0 => not found\n\tlibweird.so.9 => not found\n"
           "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6\n")
    run = lambda cmd, **k: SimpleNamespace(stdout=ldd, returncode=0)
    m = E.missing_system_libs(tmp_path, run=run)
    assert m["system"] == ["libatk-1.0.so.0", "libweird.so.9"]
    assert m["bundled"] == ["libboost_atomic.so.1.85.0"]
    assert m["packages"] == ["libatk1.0-0"] and m["unknown"] == ["libweird.so.9"]


def test_install_system_libs_tries_t64_and_rechecks(tmp_path):
    b = tmp_path / "Engine/Binaries/Linux"
    b.mkdir(parents=True)
    (b / "UnrealEditor").write_text("")
    state = {"installed": False}
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if cmd[0] == "ldd":
            return SimpleNamespace(stdout="" if state["installed"] else "\tlibatk-1.0.so.0 => not found\n", returncode=0)
        if cmd[:2] == ["apt-get", "install"]:
            ok = cmd[-1].endswith("t64")
            state["installed"] |= ok
            return SimpleNamespace(stdout="", returncode=0 if ok else 100)
        return SimpleNamespace(stdout="", returncode=0)
    r = E.install_system_libs(tmp_path, log=lambda *a: None, run=run)
    assert r["before"] == ["libatk-1.0.so.0"] and r["after"] == []
    assert r["installed"] == [("libatk1.0-0", True)]
    assert all("--no-install-recommends" in c for c in calls if c[:2] == ["apt-get", "install"])


def test_step5_installs_system_libs():
    i = SRC.index("#@title 5 · Engine onto the local disk")
    assert "engine.install_system_libs(LAB.engine_dir)" in SRC[i:SRC.index("#@title 6 ·", i)]


# ---------------------------------------------------------------------- 6c and `lines`
def test_6c_ignores_unrealbuildtool_segfault():
    i = SRC.index("CRASH_RX = re.compile")
    ns = {"re": re}
    exec(SRC[i:SRC.index("BIN = Path", i)], ns)
    ubt = ("/content/x3due/engine/Engine/Build/BatchFiles/Linux/Build.sh: line 37: 26869 Segmentation fault (core dumped) "
           "dotnet Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll -Mode=ValidatePlatforms")
    assert not ns["crashed"](ubt)
    assert ns["crashed"]("[2026.11.14-23.36.10:000][  0]LogCore: === Critical error: === Caught signal 11 Segmentation fault")
    assert "CRASH_RX.search(l)" not in SRC.replace("bool(CRASH_RX.search(l))", "")


def test_no_cell_rebinds_lines():
    hits = [l for l in SRC.splitlines() if re.match(r"\s*lines\s*=", l)]
    assert not hits, hits


# ---------------------------------------------------------------------- add-on checkout
def test_blender_io_checkout_passes_safe_directory(tmp_path, monkeypatch):
    seen = []
    real = subprocess.run

    def run(cmd, **k):
        seen.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="no network in tests")
    monkeypatch.setattr(engines.subprocess, "run", run)
    r = engines.blender_io_checkout("main", tmp_path / "io")
    assert not r["ok"]
    assert seen and all(c[:3] == ["git", "-c", "safe.directory=%s" % (tmp_path / "io").resolve()] for c in seen)
    monkeypatch.setattr(engines.subprocess, "run", real)


# ---------------------------------------------------------------------- Nanite for ray tracing
def test_ray_traced_rungs_trace_full_nanite():
    for kind, vram in (("G4", 96), ("L4", 22)):
        for name, r in tier.ladder(kind, vram, True).items():
            assert r["cvars"]["r.RayTracing.Nanite.Mode"] == 1, (kind, name)
    for r in tier.ladder("A100", 80, False).values():
        assert "r.RayTracing.Nanite.Mode" not in r["cvars"]


# ---------------------------------------------------------------------- exposure fit
def _png(path, value, clip_share=0.0):
    a = np.full((90, 160, 3), value, np.uint8)
    a[: int(90 * clip_share)] = 255
    Image.fromarray(a).save(path)
    return str(path)


def test_luma_and_badness(tmp_path):
    white = pipeline.luma_stats(_png(tmp_path / "w.png", 255))
    mid = pipeline.luma_stats(_png(tmp_path / "m.png", 100))
    black = pipeline.luma_stats(_png(tmp_path / "b.png", 2))
    assert white["clip"] == 1.0 and black["black"] == 1.0
    assert pipeline.exposure_badness(mid) < pipeline.exposure_badness(white)
    assert pipeline.exposure_badness(mid) < pipeline.exposure_badness(black)


def test_next_offsets_refines_and_extends_at_the_edge():
    assert pipeline.next_offsets({4: 0.6, 0: 0.2, -4: 0.05, -8: 0.3}) == [-2, -3, -5, -6]
    assert pipeline.next_offsets({4: 0.6, 0: 0.4, -4: 0.3, -8: 0.2, -12: 0.1, -16: 0.05}) == [-14, -15, -17, -18, -20, -24]
    assert pipeline.next_offsets({4: 0.05, 0: 0.4}) == [12, 8, 6, 5, 3, 2]


def test_fit_exposure_finds_a_dark_scene(tmp_path, monkeypatch):
    """A fake Unreal whose still is at the target median at -14 EV (the odyssey White Room) and one stop changes it by 2x."""
    def fake_render_grades(lab, name, cams, rung_name, rung, grades, look, res_override, label):
        stills = {}
        for g, spec in grades.items():
            ev = spec["post"]["auto_exposure_bias"]
            lin = 0.38 ** 2.2 * 2.0 ** (-(ev + 14))       # median 0.38 exactly at -14 EV
            v = int(round(255 * min(1.0, lin ** (1 / 2.2))))
            stills[(g, cams[0])] = _png(tmp_path / ("%s_%s.png" % (label, g)), v)
        return {"stills": stills, "seconds": 60.0}
    monkeypatch.setattr(pipeline, "render_grades", fake_render_grades)
    fit = pipeline.fit_exposure(None, "odyssey", ["DroneCam"], "preview", {}, ev0=0.0, log=lambda *a: None)
    assert fit["offset"] == -14 and not fit["edge"]
    assert fit["ev"] == fit["offset"] and fit["seconds"] == 120.0


def test_step9_fits_exposure_for_the_cinematic_look():
    i = SRC.index("#@title 9 · Render")
    cell = SRC[i:SRC.index("# %%", i)]
    assert 'EXPOSURE == "auto" and LOOK == "cinematic"' in cell and "pipeline.fit_exposure(" in cell
    assert "grade=grade" in cell
    assert 'EXPOSURE = "auto"' in SRC


# ---------------------------------------------------------------------- grade names
def test_render_grades_refuses_colliding_grade_names():
    """'xg+3' and 'xg-3' both become 'xg_3' as Unreal job names: the second still overwrote the first (G4 sheet, 15 Nov)."""
    import pytest
    with pytest.raises(ValueError, match="same Unreal job name"):
        pipeline.render_grades(None, "gob", ["EyeToEye"], "gbx", {}, {"xg+3": {}, "xg-3": {}})


# ---------------------------------------------------------------------- X3D shadows (Leg E, 15 Nov)
from x3due import project, convert as C


def test_light_carries_x3d_shadows_field_default_false():
    m = C.LightMapping()
    base = {"type": "PointLight", "location": [0, 4.6, 0], "intensity": 12, "attenuation": [0, 0, 1], "radius": 20}
    off = project._light(dict(base), m, (0, 2.5, 0), C.DEFAULT_AXES, 0)
    on = project._light(dict(base, shadows=True), m, (0, 2.5, 0), C.DEFAULT_AXES, 1)
    assert off["shadows"] is False and on["shadows"] is True


def test_shadow_mode_per_look_and_grade():
    assert project.shadow_mode("match") == "x3d"
    assert project.shadow_mode("cinematic") == "on"
    assert project.shadow_mode("cinematic", {"shadows": "x3d"}) == "x3d"
    assert project.grade_ok({"shadows": "off"}) == {"shadows": "off"}
    jobs = project.render_jobs("e2-cornell", ["Front"], "preview", {"resolution": [64, 36], "spatial_samples": 1, "temporal_samples": 1,
                               "renderer": "lumen", "cvars": {}}, "/tmp/o", "/tmp/r.json", look="match")["jobs"]
    assert jobs[0]["shadows"] == "x3d"


def test_importer_and_render_apply_shadows():
    imp = (UNREAL / "ue_scripts" / "x3dlab_import.py").read_text()
    ren = (UNREAL / "ue_project" / "Content" / "Python" / "x3dlab_render.py").read_text()
    assert 'setp(comp, "cast_shadows", bool(L.get("shadows", False)))' in imp and '"x3dlab_shadows"' in imp
    assert "def apply_shadows(world, mode)" in ren and "apply_shadows(world," in ren
