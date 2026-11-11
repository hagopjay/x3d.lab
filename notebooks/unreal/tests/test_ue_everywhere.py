"""X_ITE's look baked into one glb (gobbler.write_glb_xite), and the engines that draw it (x3due.everywhere): Cycles
through a stand-in Blender, Babylon.js in headless Chrome when this machine has Chrome and npm (X3DLAB_TEST_BROWSER=1)."""
import io
import json
import os
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from test_ue_gobbler import SCENE

from x3due import everywhere as E, gobbler as G

UNREAL = Path(__file__).resolve().parents[1]


@pytest.fixture
def bake(tmp_path):
    f = tmp_path / "gob.x3d"
    f.write_text(SCENE)
    return G.decode(f)


def _doc(path):
    blob = Path(path).read_bytes()
    jl = struct.unpack("<I", blob[12:16])[0]
    return json.loads(blob[20:20 + jl]), blob


def test_xite_glb_carries_the_look_in_plain_textures(bake, tmp_path):
    out = G.write_glb_xite(bake, tmp_path / "x.glb")
    doc, blob = _doc(tmp_path / "x.glb")
    assert struct.unpack("<I", blob[8:12])[0] == len(blob)
    assert {i["name"] for i in doc["images"]} == {"T_facets", "T_ribbons", "T_points"}       # the scene has no eye
    for m in doc["meshes"]:
        assert set(m["primitives"][0]["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"}   # every engine reads every attribute
    mats = {m["name"]: m for m in doc["materials"]}
    assert mats["GOB_facets"]["emissiveTexture"]["index"] == mats["GOB_facets"]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    assert mats["GOB_ribbons"]["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == G.XITE["ribbons"]["strength"]
    assert doc["samplers"][0]["magFilter"] == 9728 and out["facets"] == 1 and out["ribbons"] == 2 and out["points"] == 2
    for a in doc["accessors"]:
        if a.get("type") == "VEC2":
            off = doc["bufferViews"][a["bufferView"]]["byteOffset"]
            bin_off = 20 + struct.unpack("<I", blob[12:16])[0] + 8
            uv = np.frombuffer(blob[bin_off + off: bin_off + off + a["count"] * 8], np.float32).reshape(-1, 2)
            assert (uv >= 0).all() and (uv <= 1).all()


def test_facet_colour_is_the_shaders_and_finite(bake):
    c = G.xite_facet_colours(bake)
    assert c.shape == (1, 4) and np.isfinite(c).all() and (c[:, :3] > 0).any()
    seen = G.xite_facet_colours(bake, view="Front")                 # as from that camera
    assert seen.shape == (1, 4) and np.isfinite(seen).all()


def test_atlas_cells_hold_their_colour():
    img, uv = G._atlas(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float))
    for i, want in enumerate(([255, 0, 0], [0, 255, 0], [0, 0, 255])):
        x, y = int(uv[i][0] * img.shape[1]), int(uv[i][1] * img.shape[0])
        assert img[y, x, :3].tolist() == want


def test_eye_texture_draws_the_iris(tmp_path):
    rec = {"uniforms": dict(G.FROZEN), "parts": [{"role": "eye", "local": np.array([[[-0.5, -0.5, 0], [0.5, -0.5, 0], [0, 0.5, 0]]])}]}
    tex, box = G.xite_eye_texture(rec, size=64)
    assert tex.shape == (64, 64, 4) and box == (-0.5, -0.5, 0.5, 0.5)
    assert tex[32, 32, 3] > 0.9 and tex[1, 1, 3] < 0.2              # opaque iris in the middle, clear in the corner


def test_bloom_only_lifts_around_the_bright(tmp_path):
    from PIL import Image
    a = np.zeros((90, 160, 3), np.uint8)
    a[40:50, 70:80] = 255
    Image.fromarray(a).save(tmp_path / "a.png")
    E.bloom(tmp_path / "a.png", tmp_path / "b.png", intensity=1.0)
    b = np.asarray(Image.open(tmp_path / "b.png"), int)
    assert b[45, 67].sum() > 0 and b[5, 5].sum() == 0 and b[45, 75].tolist() == [255, 255, 255]


def test_gpu_chart(tmp_path):
    p = E.gpu_chart({"Unreal": [(0, 2.0, 10), (1, 9.5, 97)], "Babylon": [(0, 1.0, 40), (1, 1.2, 60)]}, tmp_path / "g.png")
    assert p is None or Path(p).exists()


def test_cycles_through_a_stand_in_blender(bake, tmp_path, monkeypatch):
    fake = tmp_path / "fake_cycles.py"
    fake.write_text('''import json, sys
from PIL import Image
job = json.load(open(sys.argv[-1]))
rep = {"blender": "stand-in", "device": "CPU", "views": {}, "errors": []}
for k in job["views"]:
    p = "%s/cycles_%s.png" % (job["out_dir"], k)
    Image.new("RGB", tuple(job["res"]), (250, 200, 120)).save(p)
    rep["views"][k] = {"png": p, "seconds": 0.1}
    print("X3DLAB cycles %s" % k)
json.dump(rep, open("%s/cycles_report.json" % job["out_dir"], "w"))
''')
    monkeypatch.setattr(E, "CYCLES_SCRIPT", fake)
    glb = tmp_path / "x.glb"
    G.write_glb_xite(bake, glb)
    lines = []
    r = E.cycles([sys.executable], glb, E.views(bake), tmp_path / "cy", res=(64, 36), samples=4, lights=E.studio_lights(bake), log=lines.append)
    assert r["rc"] == 0 and r["stills"]["Front"].endswith("cycles_Front_bloom.png") and Path(r["stills"]["Front"]).exists()
    job = json.loads((tmp_path / "cy" / "cycles_job.json").read_text())
    assert job["views"]["Front"]["position"] == [0.0, 0.0, 10.0] and len(job["lights"]) == 4 and any("Front" in l for l in lines)


def test_views_are_x3d_numbers(bake):
    v = E.views(bake, ["Front", "Nope"])
    assert list(v) == ["Front"] and v["Front"]["fov"] == pytest.approx(0.785398)


def test_babylon_page_fills():
    page = E.PAGE % {"w": 64, "h": 36, "opts": json.dumps(dict(E.BABYLON_LOOK, lights=[]))}
    assert "WebGPUEngine" in page and "GlowLayer" in page and "useRightHandedSystem = true" in page and "%(" not in page


@pytest.mark.skipif(not (os.environ.get("X3DLAB_TEST_BROWSER") and shutil.which("npm")), reason="set X3DLAB_TEST_BROWSER=1 (needs Chrome, npm)")
def test_babylon_in_headless_chrome(bake, tmp_path):
    glb = tmp_path / "x.glb"
    G.write_glb_xite(bake, glb)
    r = E.babylon(glb, E.views(bake), tmp_path / "bj", res=(160, 90), frames=2, stress_frames=2, gpu=False,
                  vendor_dir=os.environ.get("X3DLAB_BABYLON_VENDOR") or tmp_path / "vendor", log=lambda *a: None)
    assert r["info"]["kind"] in ("WebGL2", "WebGPU") and r["info"]["meshes"] > 3 and Path(r["stills"]["Front"]).exists()
    from PIL import Image
    assert np.asarray(Image.open(r["stills"]["Front"]).convert("RGB")).max() > 40           # it drew something


def test_notebook_has_the_everywhere_section():
    src = (UNREAL / "src" / "08_gobbler.py").read_text()
    for k in ("gobbler.write_glb_xite(", "everywhere.cycles(", "everywhere.babylon(", "everywhere.gpu_chart(", 'GRADES["extreme"]',
              "UNREAL_RES", "STRESS_FRAMES", "gobbler.exposure_fit("):
        assert k in src, k
