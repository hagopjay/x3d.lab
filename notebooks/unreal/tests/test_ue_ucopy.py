"""The glb Unreal is given (x3due.ucopy): emissive colours x K, lines and points as tubes and spheres, and the X3D
Background's sky and ground gradient as a dome (X3D 4.0, 24.4.1)."""
import io
import math
import shutil

import numpy as np
import pytest

from x3due import gltfstat, lines, ucopy

CLOUD = {"skyColor": [0.012, 0.014, 0.03, 0.04, 0.035, 0.06], "skyAngle": [1.57],      # the x3d.demo electron cloud
         "groundColor": [0.012, 0.014, 0.03, 0.008, 0.008, 0.016], "groundAngle": [1.57]}


def test_gradient_follows_x3d_background_rules():
    g = ucopy.gradient(CLOUD, 181)                                     # one row per degree from the zenith
    assert np.allclose(g[0], [0.012, 0.014, 0.03])                     # zenith: skyColor[0]
    assert np.allclose(g[45], [0.026, 0.0245, 0.045], atol=2e-4)        # half way to skyAngle 1.57
    assert np.allclose(g[89], [0.04, 0.035, 0.06], atol=6e-4)           # just above the horizon: skyColor[1]
    assert np.allclose(g[180], [0.012, 0.014, 0.03])                    # nadir: groundColor[0]
    assert np.allclose(g[135], [0.01, 0.011, 0.023], atol=2e-4)         # ground, half way from the nadir
    # past the last skyAngle the last sky colour holds; past the last groundAngle there is no ground
    g2 = ucopy.gradient({"skyColor": [1, 0, 0, 0, 0, 1], "skyAngle": [0.5], "groundColor": [0, 1, 0, 1, 1, 0],
                         "groundAngle": [0.3]}, 181)
    assert np.allclose(g2[0], [1, 0, 0]) and np.allclose(g2[90], [0, 0, 1]) and np.allclose(g2[160], [0, 0, 1])
    assert np.allclose(g2[180], [0, 1, 0]) and np.allclose(g2[175], [0.29, 1, 0], atol=0.02)
    # one sky colour, no ground: the flat backdrop does it
    assert not ucopy.has_gradient({"skyColor": [0.1, 0.2, 0.3]}) and ucopy.has_gradient(CLOUD)
    assert not ucopy.has_gradient({"skyColor": [0.1, 0.2, 0.3], "groundColor": [0, 0, 0]}) and not ucopy.has_gradient(None)


def test_sky_texture_shows_x3d_values_in_either_colour_space():
    from PIL import Image
    bg = {"skyColor": [0.5, 0.5, 0.5, 0.2, 0.2, 0.2], "skyAngle": [3.14159]}
    top = lambda png: Image.open(io.BytesIO(png)).convert("RGB").getpixel((0, 0))[0]
    assert top(ucopy.sky_texture(bg, "x_ite")) == 128                  # the X3D value: read as sRGB, as X_ITE does
    assert top(ucopy.sky_texture(bg, "as_is")) == 188                  # encoded so the engine reads 0.5 as linear
    im = Image.open(io.BytesIO(ucopy.sky_texture(bg, "x_ite"))).convert("RGB")
    assert im.size == (4, ucopy.SKY_ROWS) and im.getpixel((0, ucopy.SKY_ROWS - 1))[0] == 51


def test_dome_faces_its_centre():
    pos, nrm, uv, tris = ucopy.dome(10.0, rings=8, segments=8)
    assert np.allclose(np.linalg.norm(pos, axis=1), 10.0, atol=1e-4)
    assert np.allclose(nrm, -pos / 10.0, atol=1e-5)                    # normals point in
    assert uv[:, 1].min() == 0.0 and uv[:, 1].max() == 1.0
    assert np.allclose(pos[uv[:, 1] == 0.0][:, 1], 10.0)                # v = 0 at the zenith (+Y, X3D's up)
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    n = np.cross(b - a, c - a)
    big = np.linalg.norm(n, axis=1) > 1e-6
    centre_side = np.einsum("ij,ij->i", n[big], -(a[big] + b[big] + c[big]) / 3)
    assert (centre_side > 0).all()                                      # wound to be seen from inside


def make_glb(path):
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1]}], "nodes": [{"mesh": 0}, {"mesh": 1}],
          "materials": [{"name": "Glow", "emissiveFactor": [0.5, 0.25, 0], "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 0, 1]}},
                        {"name": "Grey", "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1]}},
                        {"name": "Ink_lines", "extensions": {"KHR_materials_unlit": {}},
                         "pbrMetallicRoughness": {"baseColorFactor": [0, 1, 0, 1]}}],
          "extensionsUsed": ["KHR_materials_unlit"], "meshes": [], "buffers": [{"byteLength": 0}]}
    w = lines._Writer(js, bytearray())
    tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    js["meshes"] = [{"name": "faces", "primitives": [{"mode": 4, "material": 0, "attributes": {"POSITION": w.add(tri, "VEC3", 34962, minmax=True)}},
                                                     {"mode": 4, "material": 1, "attributes": {"POSITION": w.add(tri, "VEC3", 34962, minmax=True)}}]},
                    {"name": "lines", "primitives": [{"mode": 1, "material": 2, "attributes": {
                        "POSITION": w.add(np.array([[0, 0, 0], [0, 0, 1]], np.float32), "VEC3", 34962, minmax=True)}}]}]
    lines.write_glb(path, js, w.bin)
    return path


MANIFEST = {"bounds": ((-1, -1, -1), (1, 1, 1)), "backgrounds": [CLOUD],
            "viewpoints": [{"type": "Viewpoint", "position": (0, 0, 5), "fieldOfView": math.pi / 4}]}


def test_unreal_copy_scales_emission_thickens_lines_and_adds_the_sky(tmp_path):
    src = make_glb(tmp_path / "in.glb")
    rep = ucopy.unreal_copy(src, tmp_path / "out.glb", MANIFEST, k=1.5, colour_space="x_ite",
                            line_radius=lines.radius_for(MANIFEST))
    assert rep["changed"] and rep["emission_scaled"] == 1 and rep["lines"]["segments"] == 1
    js, binary = lines.read_glb(tmp_path / "out.glb")
    glow = js["materials"][0]
    assert glow["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == pytest.approx(1.5)
    assert "KHR_materials_emissive_strength" not in js["materials"][1].get("extensions", {})     # not emissive: untouched
    tube = next(p for m in js["meshes"] if m.get("name") == "lines" for p in m["primitives"])
    assert tube["mode"] == 4 and lines._material_rgba(js["materials"][tube["material"]])[:3] == [0.0, 1.5, 0.0]
    sky = next(n for n in js["nodes"] if n.get("name") == ucopy.SKY_NODE)
    assert sky["translation"] == [0.0, 0.0, 0.0] and len(js["scenes"][0]["nodes"]) == 3
    smat = next(m for m in js["materials"] if m["name"] == ucopy.SKY_NODE)
    assert smat["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == pytest.approx(1.5)
    assert smat["doubleSided"] and smat["pbrMetallicRoughness"]["baseColorFactor"] == [0, 0, 0, 1]
    assert rep["sky"]["radius_m"] == pytest.approx(0.9 * 40 * math.sqrt(3), abs=1e-3)   # inside the flat backdrop
    assert rep["sky"]["colours"][0] == [0.012, 0.014, 0.03]
    s = gltfstat.stats(tmp_path / "out.glb")
    assert s["counts"]["triangles"] >= 2 + 12 + rep["sky"]["triangles"]


def test_unreal_copy_without_a_gradient_or_with_everything_off(tmp_path):
    src = make_glb(tmp_path / "in.glb")
    flat = dict(MANIFEST, backgrounds=[{"skyColor": [0.2, 0.2, 0.2]}])
    rep = ucopy.unreal_copy(src, tmp_path / "flat.glb", flat, k=1.2, line_radius=0.01)
    assert rep["sky"] == {"added": False, "why": "one sky colour: the flat backdrop"}
    off = ucopy.unreal_copy(src, tmp_path / "off.glb", MANIFEST, k=1.2, line_radius=0.01, thicken=False, sky=False)
    assert off["lines"]["why"] == "off" and off["sky"]["why"] == "off" and off["emission_scaled"] == 1
    js, _ = lines.read_glb(tmp_path / "off.glb")
    assert js["meshes"][1]["primitives"][0]["mode"] == 1                # lines left as lines


def test_khronos_validator_accepts_the_copy(tmp_path):
    from x3dlab import formats
    if not shutil.which("node") and not shutil.which("npx"):
        pytest.skip("no Node for the Khronos validator")
    ucopy.unreal_copy(make_glb(tmp_path / "in.glb"), tmp_path / "out.glb", MANIFEST, k=1.2, line_radius=0.01)
    v = formats.validate_gltf([str(tmp_path / "out.glb")]).get(str(tmp_path / "out.glb"), {})
    if v.get("error") or v.get("errors") is None:
        pytest.skip("validator unavailable: %s" % v.get("error"))
    assert v["errors"] == 0, v
