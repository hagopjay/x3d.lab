"""Lines and points for Unreal (x3due.lines): glTF LINES / LINE_STRIP / LINE_LOOP / POINTS primitives become tubes and
spheres in an emissive-only material, because Unreal's glTF importer drops everything that is not triangles (the
x3d.demo scenes hold 458 IndexedLineSets and 110 PointSets)."""
import json
import math
import shutil

import numpy as np
import pytest

from x3due import gltfstat, lines


def make_glb(path):
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1, 2, 3, 4]}],
          "nodes": [{"mesh": 0, "scale": [2, 2, 2]}, {"mesh": 1}, {"mesh": 2}, {"mesh": 3}, {"mesh": 4}],
          "materials": [{"name": "Red", "emissiveFactor": [1, 0, 0],
                         "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 1]}},
                        {"name": "Green", "pbrMetallicRoughness": {"baseColorFactor": [0, 1, 0, 1]}},
                        {"name": "Surface", "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1]}}],
          "meshes": [], "buffers": [{"byteLength": 0}]}
    binary = bytearray()
    w = lines._Writer(js, binary)
    quad = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 1]], np.float32)
    tri3 = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], np.float32)
    js["meshes"] = [
        {"name": "lines", "primitives": [{"mode": 1, "material": 0, "attributes": {
            "POSITION": w.add(quad, "VEC3", 34962, minmax=True),
            "COLOR_0": w.add(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], np.float32), "VEC3", 34962)}}]},
        {"name": "strip", "primitives": [{"mode": 3, "material": 1, "attributes": {"POSITION": w.add(tri3, "VEC3", 34962, minmax=True)}}]},
        {"name": "loop", "primitives": [{"mode": 2, "attributes": {"POSITION": w.add(tri3, "VEC3", 34962, minmax=True)},
                                         "indices": w.add(np.array([[0], [1], [2]], np.uint32), "SCALAR", 34963)}]},
        {"name": "points", "primitives": [{"mode": 0, "material": 0, "attributes": {
            "POSITION": w.add(np.array([[0, 0, 0], [2, 0, 0]], np.float32), "VEC3", 34962, minmax=True)}}]},
        {"name": "surface", "primitives": [{"mode": 4, "material": 2, "attributes": {"POSITION": w.add(tri3, "VEC3", 34962, minmax=True)}}]},
    ]
    lines.write_glb(path, js, w.bin)
    return path


def prims(js):
    return {m["name"]: m["primitives"][0] for m in js["meshes"]}


def test_every_line_and_point_primitive_becomes_triangles(tmp_path):
    src = make_glb(tmp_path / "in.glb")
    rep = lines.thicken(src, tmp_path / "out.glb", radius=0.01, strength=3.0)
    assert rep["changed"] and rep["primitives"] == 4 and not rep["skipped"]
    assert rep["segments"] == 2 + 2 + 3 and rep["points"] == 2
    js, binary = lines.read_glb(tmp_path / "out.glb")
    p = prims(js)
    assert {k: v["mode"] for k, v in p.items()} == {"lines": 4, "strip": 4, "loop": 4, "points": 4, "surface": 4}
    tri = lambda name: js["accessors"][p[name]["indices"]]["count"] // 3
    assert (tri("lines"), tri("strip"), tri("loop")) == (2 * 12, 2 * 12, 3 * 12)          # 6 sides: 12 triangles a segment
    assert tri("points") == 2 * 80                                                       # an icosphere of 80 faces each
    assert p["surface"]["material"] == 2 and "indices" not in p["surface"]               # the surface is untouched
    s = gltfstat.stats(tmp_path / "out.glb")
    assert s["counts"]["primitives"] == 5


def test_tubes_sit_at_the_radius_in_mesh_space(tmp_path):
    lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01)
    js, binary = lines.read_glb(tmp_path / "out.glb")
    p = prims(js)
    v = lines.read_accessor(js, binary, p["lines"]["attributes"]["POSITION"])
    first = v[:12]                                   # the first segment: (0,0,0) -> (1,0,0), under a node of scale 2
    radial = np.linalg.norm(first[:, 1:], axis=1)
    assert np.allclose(radial, 0.005, atol=1e-6)     # 0.01 in the world is 0.005 in that mesh's own units
    strip = lines.read_accessor(js, binary, p["strip"]["attributes"]["POSITION"])[:12]
    assert np.allclose(np.linalg.norm(strip[:, 1:], axis=1), 0.01, atol=1e-6)
    n = lines.read_accessor(js, binary, p["strip"]["attributes"]["NORMAL"])
    assert np.allclose(np.linalg.norm(n, axis=1), 1.0, atol=1e-5)


def test_materials_are_emissive_only_in_the_colour_x3d_draws(tmp_path):
    rep = lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01, strength=3.0)
    js, _ = lines.read_glb(tmp_path / "out.glb")
    p = prims(js)
    red = js["materials"][p["lines"]["material"]]
    assert red["emissiveFactor"] == [1.0, 0.0, 0.0] and red["pbrMetallicRoughness"]["baseColorFactor"] == [0, 0, 0, 1]
    assert red["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == pytest.approx(3.0)
    assert red["extensions"]["KHR_materials_specular"]["specularFactor"] == 0.0
    assert p["points"]["material"] == p["lines"]["material"]                    # one copy per source material
    green = js["materials"][p["strip"]["material"]]
    assert green["emissiveFactor"] == [0.0, 1.0, 0.0]                           # no emissive: the base colour is used
    white = js["materials"][p["loop"]["material"]]
    assert white["emissiveFactor"] == [1.0, 1.0, 1.0]                           # no material at all: white
    assert rep["materials"] == 3
    assert {"KHR_materials_emissive_strength", "KHR_materials_specular"} <= set(js["extensionsUsed"])
    assert js["materials"][2]["name"] == "Surface"                              # the originals stay


def test_vertex_colours_carry_over(tmp_path):
    lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01)
    js, binary = lines.read_glb(tmp_path / "out.glb")
    c = lines.read_accessor(js, binary, prims(js)["lines"]["attributes"]["COLOR_0"])
    assert c.shape[1] == 4 and np.allclose(c[:6, :3], [1, 0, 0]) and np.allclose(c[6:12, :3], [0, 1, 0])
    assert np.allclose(c[:, 3], 1.0)


def test_nothing_to_do_and_the_radius_from_the_first_viewpoint(tmp_path):
    js = {"asset": {"version": "2.0"}, "meshes": [], "buffers": [{"byteLength": 0}]}
    lines.write_glb(tmp_path / "empty.glb", js, b"")
    rep = lines.thicken(tmp_path / "empty.glb", tmp_path / "same.glb", radius=0.01)
    assert not rep["changed"] and (tmp_path / "same.glb").exists()
    man = {"bounds": ((-1, -1, -1), (1, 1, 1)), "viewpoints": [{"type": "Viewpoint", "position": (0, 0, 5), "fieldOfView": math.pi / 4}]}
    r = lines.radius_for(man, px=1.5, height=1080)
    assert r == pytest.approx(0.5 * 1.5 * 2 * 5 * math.tan(math.pi / 8) / 1080)
    assert lines.radius_for({"bounds": ((0, 0, 0), (10, 0, 0)), "viewpoints": []}) == pytest.approx(0.01)
    near = {"type": "Viewpoint", "position": (0, 0, 2), "fieldOfView": math.pi / 4}
    far = {"type": "Viewpoint", "position": (0, 0, 200), "fieldOfView": math.pi / 4}
    three = dict(man, viewpoints=[near, man["viewpoints"][0], far])
    assert lines.radius_for(three, px=1.5, height=1080) == pytest.approx(r)          # the median view, not the far one
    # a far-off shape moves the bounds' centre; centerOfRotation (or the median shape) is where the camera looks
    outlier = {"bounds": ((-1, -900, -1), (1, 1, 1)), "shape_centres": [(0, 0, 0), (0, 0.5, 0), (0, -900, 0)],
               "viewpoints": [{"type": "Viewpoint", "position": (0, 0, 5), "fieldOfView": math.pi / 4, "centerOfRotation": [0, 0, 0]}]}
    assert lines.radius_for(outlier) == pytest.approx(r)
    no_cor = dict(outlier, viewpoints=[{"type": "Viewpoint", "position": (0, 0, 5), "fieldOfView": math.pi / 4}])
    assert lines.radius_for(no_cor) == pytest.approx(0.5 * 1.5 * 2 * math.dist((0, 0, 5), (0, 0.5, 0)) * math.tan(math.pi / 8) / 1080)
    moved = dict(outlier, viewpoints=[dict(outlier["viewpoints"][0], world_matrix=((2, 0, 0, 0), (0, 2, 0, 0), (0, 0, 2, 0), (0, 0, 0, 1)))])
    assert lines.radius_for(moved) == pytest.approx(2 * r)                            # distances in world units


def test_segments_and_shapes():
    assert lines.segments_of(1, [0, 1, 2, 3, 4]).tolist() == [[0, 1], [2, 3]]       # a remainder is dropped
    assert lines.segments_of(3, [0, 1, 2]).tolist() == [[0, 1], [1, 2]]
    assert lines.segments_of(2, [0, 1, 2]).tolist() == [[0, 1], [1, 2], [2, 0]]
    assert lines.segments_of(3, [7]).shape == (0, 2)
    v, n, t = lines.spheres(np.zeros((lines.MAX_SPHERE_POINTS + 1, 3)), 0.1)
    assert len(t) == 8 * (lines.MAX_SPHERE_POINTS + 1)                           # huge point sets: octahedra
    v, n, t = lines.tubes(np.zeros((1, 3)), np.zeros((1, 3)), 0.1, 6)            # a zero-length segment still works
    assert np.isfinite(v).all() and len(t) == 12


def test_khronos_validator_accepts_the_result(tmp_path):
    try:
        from x3dlab import formats
        if not shutil.which("node") and not shutil.which("nodejs"):
            pytest.skip("no node for the Khronos validator")
        lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01)
        rep = formats.validate_gltf([tmp_path / "out.glb"]).get(str(tmp_path / "out.glb"))
    except Exception as exc:                                             # the validator is fetched on first use
        pytest.skip("Khronos validator unavailable here: %s" % str(exc)[:120])
    if not rep:
        pytest.skip("Khronos validator gave no report")
    assert rep.get("errors", 0) == 0, json.dumps(rep.get("messages", [])[:5])


# ---------------------------------------------------------------------- choosing the X3D add-on for the Blender leg
def _addon_repo(tmp_path):
    import subprocess
    src = tmp_path / "x3d-blender-io-src"
    (src / "source").mkdir(parents=True)
    (src / "source" / "__init__.py").write_text("# add-on\n")
    run = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "add-on")
    run("checkout", "-q", "-b", "import-fidelity")
    (src / "source" / "import_x3d.py").write_text("FIDELITY = True\n")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fidelity")
    run("config", "uploadpack.allowAnySHA1InWant", "true")            # GitHub serves commits by SHA, too
    sha = run("rev-parse", "HEAD").stdout.strip()
    run("checkout", "-q", "main")
    return src, sha


def test_blender_io_checkout_by_branch_or_commit(tmp_path):
    from x3due import engines
    repo, sha = _addon_repo(tmp_path)
    co = engines.blender_io_checkout("import-fidelity", tmp_path / "w1", repo=repo)
    assert co["ok"] and co["commit"] == sha and (tmp_path / "w1/source/import_x3d.py").exists(), co
    again = engines.blender_io_checkout("main", tmp_path / "w1", repo=repo)         # the same folder, another ref
    assert again["ok"] and not (tmp_path / "w1/source/import_x3d.py").exists()
    by_sha = engines.blender_io_checkout(sha, tmp_path / "w2", repo=repo)
    assert by_sha["ok"] and by_sha["commit"] == sha
    assert not engines.blender_io_checkout("no-such-branch", tmp_path / "w3", repo=repo)["ok"]
    for bad in ("", "../etc", "-rf", "a b", "x" * 200):
        assert not engines.blender_io_checkout(bad, tmp_path / "w4", repo=repo)["ok"], bad


def test_use_blender_io_sets_and_clears_the_environment(tmp_path, monkeypatch):
    import os
    from x3due import engines
    repo, sha = _addon_repo(tmp_path)
    monkeypatch.setattr(engines, "BLENDER_IO_REPO", str(repo))
    monkeypatch.setattr(engines.blender_io_checkout, "__defaults__", (str(repo), 600))
    monkeypatch.delenv("X3DLAB_BLENDER_IO_DIR", raising=False)
    monkeypatch.delenv("BLENDER_X3D_CIRCLE_DIV", raising=False)
    out = engines.use_blender_io("import-fidelity", tmp_path / "work", divisions=64, log=lambda *a: None)
    assert out["ok"] and os.environ["X3DLAB_BLENDER_IO_DIR"].endswith("x3d-blender-io") and os.environ["BLENDER_X3D_CIRCLE_DIV"] == "64"
    pin = engines.use_blender_io("pin", tmp_path / "work", log=lambda *a: None)
    assert pin["ok"] and "X3DLAB_BLENDER_IO_DIR" not in os.environ
    lines_seen = []
    bad = engines.use_blender_io("nope", tmp_path / "work", log=lines_seen.append)
    assert not bad["ok"] and "X3DLAB_BLENDER_IO_DIR" not in os.environ and "pinned one runs" in lines_seen[0]


def test_notebook_07_uses_the_line_pass_and_the_addon_choice():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/07_unreal.py").read_text()
    c7 = src.split("#@title 7 · Download")[1].split("# %%")[0]
    assert c7.index("engines.use_blender_io(BLENDER_IO_REF, WORK, PRIMITIVE_DIVISIONS)") < c7.index("xblender.setup()")
    c8 = src.split("#@title 8 · Import every scene")[1].split("# %%")[0]
    assert "lines.thicken(src, dst, lines.radius_for(r[\"manifest\"], px=LINE_PX), strength=LAB.mapping.k)" in c8
    assert "report.account(gstats, rep" in c8 and 'n != "calib"' in c8
    for opt in ("PRIMITIVE_DIVISIONS = 64", 'BLENDER_IO_REF = "main"', "LINES_FOR_UNREAL = True", "LINE_PX = 1.5"):
        assert opt in src, opt
