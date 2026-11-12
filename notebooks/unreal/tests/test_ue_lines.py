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
            "COLOR_0": w.add(np.array([[1, 0, 0], [1, 0, 0], [0, 0, 1], [0, 0, 1]], np.float32), "VEC3", 34962)}}]},
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


def all_prims(js):
    return {m["name"]: m["primitives"] for m in js["meshes"]}


def colour_of(js, prim):
    m = js["materials"][prim["material"]]
    k = m.get("extensions", {}).get("KHR_materials_emissive_strength", {}).get("emissiveStrength", 1.0)
    return [round(c * k, 4) for c in m["emissiveFactor"]]


def test_every_line_and_point_primitive_becomes_triangles(tmp_path):
    src = make_glb(tmp_path / "in.glb")
    rep = lines.thicken(src, tmp_path / "out.glb", radius=0.01, strength=3.0)
    assert rep["changed"] and rep["primitives"] == 4 and not rep["skipped"]
    assert rep["segments"] == 2 + 2 + 3 and rep["points"] == 2
    js, binary = lines.read_glb(tmp_path / "out.glb")
    p = all_prims(js)
    assert {k: sorted({q["mode"] for q in v}) for k, v in p.items()} == {k: [4] for k in p}
    tri = lambda name: sum(js["accessors"][q["indices"]]["count"] // 3 for q in p[name])
    assert (tri("lines"), tri("strip"), tri("loop")) == (2 * 12, 2 * 12, 3 * 12)          # 6 sides: 12 triangles a segment
    assert tri("points") == 2 * 80                                                       # an icosphere of 80 faces each
    assert len(p["lines"]) == 2                                                          # red and blue: one primitive each
    assert p["surface"][0]["material"] == 2 and "indices" not in p["surface"][0]         # the surface is untouched
    s = gltfstat.stats(tmp_path / "out.glb")
    assert s["counts"]["primitives"] == 6


def test_tubes_sit_at_the_radius_in_mesh_space(tmp_path):
    lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01)
    js, binary = lines.read_glb(tmp_path / "out.glb")
    p = prims(js)
    red = next(q for q in all_prims(js)["lines"] if colour_of(js, q) == [1.0, 0.0, 0.0])
    v = lines.read_accessor(js, binary, red["attributes"]["POSITION"])
    first = v[:12]                                   # the red segment: (0,0,0) -> (1,0,0), under a node of scale 2
    radial = np.linalg.norm(first[:, 1:], axis=1)
    assert np.allclose(radial, 0.005, atol=1e-6)     # 0.01 in the world is 0.005 in that mesh's own units
    strip = lines.read_accessor(js, binary, p["strip"]["attributes"]["POSITION"])[:12]
    assert np.allclose(np.linalg.norm(strip[:, 1:], axis=1), 0.01, atol=1e-6)
    n = lines.read_accessor(js, binary, p["strip"]["attributes"]["NORMAL"])
    assert np.allclose(np.linalg.norm(n, axis=1), 1.0, atol=1e-5)


def test_materials_are_emissive_only_in_the_colour_x3d_draws(tmp_path):
    rep = lines.thicken(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", radius=0.01, strength=3.0)
    js, _ = lines.read_glb(tmp_path / "out.glb")
    p = all_prims(js)
    red = js["materials"][p["points"][0]["material"]]                          # Red's emissive, no COLOR_0
    assert red["emissiveFactor"] == [1.0, 0.0, 0.0] and red["pbrMetallicRoughness"]["baseColorFactor"] == [0, 0, 0, 1]
    assert red["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == pytest.approx(3.0)
    assert red["extensions"]["KHR_materials_specular"]["specularFactor"] == 0.0 and "alphaMode" not in red
    assert sorted(colour_of(js, q) for q in p["lines"]) == [[0.0, 0.0, 3.0], [3.0, 0.0, 0.0]]   # COLOR_0 wins over Red
    assert any(q["material"] == p["points"][0]["material"] for q in p["lines"])  # one material per colour, shared
    assert colour_of(js, p["strip"][0]) == [0.0, 3.0, 0.0]                      # no emissive: the base colour is used
    assert colour_of(js, p["loop"][0]) == [3.0, 3.0, 3.0]                       # no material at all: white
    assert rep["materials"] == 4 and rep["colours"] == 4 and rep["translucent"] == 0
    assert {"KHR_materials_emissive_strength", "KHR_materials_specular"} <= set(js["extensionsUsed"])
    assert js["materials"][2]["name"] == "Surface"                              # the originals stay
    assert all("COLOR_0" not in q["attributes"] for v in p.values() for q in v)  # the colour lives in the material


def _coloured_glb(path, colours, mode, material, n=None):
    """One primitive of `mode` with one COLOR_0 row per vertex, along the x axis."""
    colours = np.asarray(colours, np.float32)
    n = len(colours) if n is None else n
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
          "materials": [material] if material else [], "meshes": [], "buffers": [{"byteLength": 0}]}
    if material and "KHR_materials_unlit" in material.get("extensions", {}):
        js["extensionsUsed"] = ["KHR_materials_unlit"]
    w = lines._Writer(js, bytearray())
    pos = np.stack([np.arange(n, dtype=np.float32), np.zeros(n, np.float32), np.zeros(n, np.float32)], 1)
    prim = {"mode": mode, "attributes": {"POSITION": w.add(pos, "VEC3", 34962, minmax=True),
                                         "COLOR_0": w.add(colours, "VEC%d" % colours.shape[1], 34962)}}
    if material:
        prim["material"] = 0
    js["meshes"] = [{"name": "m", "primitives": [prim]}]
    lines.write_glb(path, js, w.bin)
    return path


def test_a_segment_between_two_colours_is_split_in_the_middle(tmp_path):
    src = _coloured_glb(tmp_path / "grad.glb", [[1, 0, 0], [0, 0, 1]], 1, None)
    rep = lines.thicken(src, tmp_path / "out.glb", radius=0.01)
    js, binary = lines.read_glb(tmp_path / "out.glb")
    halves = {tuple(colour_of(js, q)): lines.read_accessor(js, binary, q["attributes"]["POSITION"]) for q in js["meshes"][0]["primitives"]}
    assert set(halves) == {(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)} and rep["segments"] == 1
    assert np.allclose(sorted({round(float(x), 6) for x in halves[(1.0, 0.0, 0.0)][:, 0]}), [0.0, 0.5])
    assert np.allclose(sorted({round(float(x), 6) for x in halves[(0.0, 0.0, 1.0)][:, 0]}), [0.5, 1.0])


def test_unlit_colour_rgba_points_blend_with_their_alpha(tmp_path):
    """What the X3D add-on writes for a PointSet with a ColorRGBA: KHR_materials_unlit, alphaMode BLEND, COLOR_0."""
    unlit = {"name": "Dots_lines", "alphaMode": "BLEND", "extensions": {"KHR_materials_unlit": {}},
             "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}
    src = _coloured_glb(tmp_path / "rgba.glb", [[1, 0.5, 0, 0.25], [1, 0.5, 0, 0.25], [0, 1, 0, 1]], 0, unlit)
    rep = lines.thicken(src, tmp_path / "out.glb", radius=0.01, strength=2.0)
    js, _ = lines.read_glb(tmp_path / "out.glb")
    made = {tuple(colour_of(js, q)): js["materials"][q["material"]] for q in js["meshes"][0]["primitives"]}
    orange = made[(2.0, 1.0, 0.0)]
    assert orange["alphaMode"] == "BLEND" and orange["pbrMetallicRoughness"]["baseColorFactor"][3] == pytest.approx(0.25, abs=0.02)
    assert "alphaMode" not in made[(0.0, 2.0, 0.0)]
    assert rep["translucent"] == 1 and rep["points"] == 3
    opaque = dict(unlit, alphaMode="OPAQUE")                                 # alpha only counts when the material blends
    lines.thicken(_coloured_glb(tmp_path / "op.glb", [[1, 0.5, 0, 0.25]], 0, opaque), tmp_path / "op_out.glb", radius=0.01)
    js, _ = lines.read_glb(tmp_path / "op_out.glb")
    assert "alphaMode" not in js["materials"][js["meshes"][0]["primitives"][0]["material"]]


def test_many_colours_share_a_palette(tmp_path):
    rng = np.random.default_rng(7)
    colours = rng.random((500, 3))
    src = _coloured_glb(tmp_path / "many.glb", colours, 0, None)
    rep = lines.thicken(src, tmp_path / "out.glb", radius=0.01)
    assert rep["points"] == 500 and rep["colours"] == lines.MAX_COLOURS and rep["materials"] == lines.MAX_COLOURS
    pal, which = lines.palette(np.hstack([colours, np.ones((500, 1))]))
    err = np.abs(lines.linear_to_srgb(pal[which, :3]) - lines.linear_to_srgb(colours)).mean()
    assert err < 0.08                                                        # mean error in sRGB, 0..1
    few, which = lines.palette(np.array([[0.002, 0.002, 0.002, 1], [0.004, 0.004, 0.004, 1], [1, 1, 1, 1]]))
    assert len(few) == 3                                                     # dark steps survive (sRGB quantisation)


def test_black_lines_stay_black(tmp_path):
    """A Material without emissiveColor draws its lines black in X3D; the tubes must not turn white."""
    black = {"name": "Ink_lines", "extensions": {"KHR_materials_unlit": {}},
             "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 1]}}
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
          "materials": [black], "extensionsUsed": ["KHR_materials_unlit"], "meshes": [], "buffers": [{"byteLength": 0}]}
    w = lines._Writer(js, bytearray())
    pos = np.array([[0, 0, 0], [1, 0, 0]], np.float32)
    js["meshes"] = [{"primitives": [{"mode": 1, "material": 0, "attributes": {"POSITION": w.add(pos, "VEC3", 34962, minmax=True)}}]}]
    lines.write_glb(tmp_path / "black.glb", js, w.bin)
    lines.thicken(tmp_path / "black.glb", tmp_path / "out.glb", radius=0.01, strength=3.0)
    out, _ = lines.read_glb(tmp_path / "out.glb")
    m = out["materials"][out["meshes"][0]["primitives"][0]["material"]]
    assert m["emissiveFactor"] == [0.0, 0.0, 0.0] and "KHR_materials_emissive_strength" not in m["extensions"]


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
    # the colour space: only an add-on that reads BLENDER_X3D_COLOR_SPACE converts; otherwise the run says so and the
    # Unreal leg treats colours as they are
    monkeypatch.delenv("BLENDER_X3D_COLOR_SPACE", raising=False)
    said = []
    old = engines.use_blender_io("import-fidelity", tmp_path / "work", log=said.append, colour_space="x_ite")
    assert old["colour_space"] == "as_is" and os.environ["BLENDER_X3D_COLOR_SPACE"] == "AS_IS"
    assert any("BLENDER_X3D_COLOR_SPACE" in line for line in said)
    pinned = engines.use_blender_io("pin", tmp_path / "work", log=lambda *a: None, colour_space="x_ite")
    assert pinned["colour_space"] == "as_is"
    import subprocess
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "import-fidelity"], check=True)
    (repo / "source" / "import_x3d.py").write_text("COLOUR = os.environ.get('BLENDER_X3D_COLOR_SPACE')\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "colour"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    new = engines.use_blender_io("import-fidelity", tmp_path / "work", log=lambda *a: None, colour_space="x_ite")
    assert new["colour_space"] == "x_ite" and os.environ["BLENDER_X3D_COLOR_SPACE"] == "SRGB"
    plain = engines.use_blender_io("import-fidelity", tmp_path / "work", log=lambda *a: None, colour_space="as_is")
    assert plain["colour_space"] == "as_is" and os.environ["BLENDER_X3D_COLOR_SPACE"] == "AS_IS"


def test_notebook_07_uses_the_unreal_copy_the_addon_choice_and_the_colour_space():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/07_unreal.py").read_text()
    c7 = src.split("#@title 7 · Download")[1].split("# %%")[0]
    use = c7.index("engines.use_blender_io(BLENDER_IO_REF, WORK, PRIMITIVE_DIVISIONS, colour_space=X3D_COLOURS)")
    assert use < c7.index("xblender.setup()")
    assert use < c7.index("LAB.mapping = convert.LightMapping(") < c7.index("xblender.setup()")   # the colour space the add-on uses
    c8 = src.split("#@title 8 · Import every scene")[1].split("# %%")[0]
    assert "ucopy.unreal_copy(src, dst, r[\"manifest\"], k=LAB.mapping.k, colour_space=LAB.mapping.colour_space" in c8
    assert "line_radius=lines.radius_for(r[\"manifest\"], px=LINE_PX), thicken=LINES_FOR_UNREAL" in c8
    assert "sky=BACKGROUND_GRADIENT" in c8
    assert "pipeline.calibration(png, LAB.mapping.k, colour_space=LAB.mapping.colour_space)" in c8
    assert "report.account(gstats, rep" in c8 and 'n != "calib"' in c8
    for opt in ("PRIMITIVE_DIVISIONS = 64", 'BLENDER_IO_REF = "main"', "LINES_FOR_UNREAL = True", "LINE_PX = 1.5",
                'X3D_COLOURS = "x_ite"', "BACKGROUND_GRADIENT = True"):
        assert opt in src, opt
