"""The Gobbler's shader-aware baker (x3due.gobbler): decoding the encodings its GLSL relies on, the glTF it writes, the
CPU reference renderer and the scoreboard. A synthetic scene uses the same encodings; the real Gobbler is checked when
X3DLAB_GOBBLER points at gobbler3.x3d."""
import json
import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from x3due import gobbler as G

UNREAL = Path(__file__).resolve().parents[1]


def code_normal(c):
    a = c * 0.39269908
    return "%.6f %.6f 0" % (math.cos(a), math.sin(a))


def alpha_loop(phase_q, span_q):
    return 0.7 + 0.3 * (phase_q + 100 * span_q) / 400.0


SCENE = """<X3D profile='Full' version='4.0'><Scene>
<Viewpoint DEF='Front' position='0 0 10' fieldOfView='0.785398'/>
<Shape><Appearance><Material emissiveColor='0.05 0.05 0.05'/><ComposedShader DEF='SkyShader' language='GLSL'/></Appearance>
  <Sphere radius='60' solid='false'/></Shape>
<Transform translation='1 0 0'>
 <Shape><Appearance><Material/><ComposedShader DEF='LineShader' language='GLSL'/></Appearance>
  <IndexedFaceSet solid='false' colorPerVertex='false' normalPerVertex='true'
     coordIndex='0 0 1 1 -1 1 1 2 2 -1' texCoordIndex='1 1 0 0 -1 2 2 1 1 -1' normalIndex='0 1 2 3 -1 12 13 14 15 -1'>
   <ColorRGBA color='1 0.8 0.4 0.3  0.6 0.9 1 %(loop)s'/>
   <Coordinate point='0 0 0  1 0 0  1 1 0'/>
   <Normal vector='%(n0)s, %(n1)s, %(n2)s, %(n3)s, %(n4)s, %(n5)s, %(n6)s, %(n7)s, %(n8)s, %(n9)s, %(n10)s, %(n11)s, %(n12)s, %(n13)s, %(n14)s, %(n15)s'/>
   <TextureCoordinate3D point='0 0 0  1 0 0  1 1 0'/>
  </IndexedFaceSet></Shape>
</Transform>
<Shape><Appearance><Material/><ComposedShader DEF='PointShader' language='GLSL'/></Appearance>
  <PointSet><ColorRGBA color='1 0.9 0.6 0.45  0.2 0.8 1 0.95  1 1 1 0.0'/><Coordinate point='0 0 0  0 2 0  3 3 3'/></PointSet></Shape>
<Shape><Appearance><Material/><ComposedShader DEF='FacetShader' language='GLSL'/></Appearance>
  <IndexedFaceSet solid='false' colorPerVertex='false' coordIndex='0 1 2 -1' texCoordIndex='0 1 2 -1'>
   <ColorRGBA color='0.84 0.7 0.36 0.75'/><Coordinate point='-1 -1 0  1 -1 0  0 1 0'/>
   <TextureCoordinate point='0 0 1 0 0 1'/></IndexedFaceSet></Shape>
<Shape><Appearance><Material/><ComposedShader DEF='Hide' language='GLSL'/></Appearance>
  <IndexedLineSet coordIndex='0 1 -1'><Coordinate point='0 0 0 1 1 1'/></IndexedLineSet></Shape>
</Scene></X3D>""" % dict({"loop": "%.6f" % alpha_loop(25, 2)}, **{"n%d" % c: code_normal(c) for c in range(16)})


@pytest.fixture
def bake(tmp_path):
    f = tmp_path / "gob.x3d"
    f.write_text(SCENE)
    return G.decode(f)


def test_pcg3_is_the_shaders_hash():
    # pinned values: a change here means the per-facet and per-point randomness no longer matches the GLSL
    r = G.rnd3i(np.array([[0, 0, 0], [100, -50, 7], [-4096, 0, 1]]))
    assert r.shape == (3, 3) and ((r >= 0) & (r < 1)).all()
    np.testing.assert_allclose(r, PINNED, atol=1e-9)
    assert G.qpos([[0.004, 0.005, -0.006]]).tolist() == [[0, 1, -1]]           # floor(p * 100 + 0.5)


PINNED = [[0.573714120081, 0.976591075072, 0.122501413105],
          [0.330589855788, 0.469933476532, 0.861790962052],
          [0.925114955055, 0.988877557451, 0.207655449631]]


def test_ribbons_decode_the_corner_codes_and_the_alpha(bake):
    r = next(p for p in bake["parts"] if p["role"] == "ribbons")
    assert len(r["a"]) == 2
    np.testing.assert_allclose(r["a_local"][0], [0, 0, 0])
    np.testing.assert_allclose(r["b_local"][0], [1, 0, 0])
    np.testing.assert_allclose(r["a"][0], [1, 0, 0])                         # the Transform moved it
    assert r["code"].tolist() == [0, 12]
    assert not r["loop"][0] and r["width_a"][0] == pytest.approx(0.3) and not r["pulse"][0]
    assert r["loop"][1] and r["loop_phase"][1] == pytest.approx(0.25) and r["loop_span"][1] == pytest.approx(0.14)
    assert r["pulse"][1] and r["width_a"][1] == pytest.approx(0.3)


def test_points_decode_kind_size_and_drop_the_zero_size(bake):
    p = next(p for p in bake["parts"] if p["role"] == "points")
    assert p["kind"].tolist() == [1, 3, 0] and p["size"][:2] == pytest.approx([3.2, 3.2])
    assert p["visible"].tolist() == [True, True, False]
    assert p["diameter"][0] == pytest.approx(3.2 * 0.16 * (0.85 + 0.3 * p["twinkle"][0]))


def test_skipped_and_roles(bake):
    assert sorted(p["role"] for p in bake["parts"]) == ["facets", "points", "ribbons", "sky"]
    assert [s["why"] for s in bake["skipped"]] == ["Hide shader draws nothing"]
    assert "Front" in bake["viewpoints"]


def test_glb_is_well_formed_and_carries_the_data_channels(bake, tmp_path):
    out = G.write_glb(bake, tmp_path / "g.glb", gain=2.0)
    blob = (tmp_path / "g.glb").read_bytes()
    magic, ver, length = struct.unpack("<III", blob[:12])
    assert magic == 0x46546C67 and ver == 2 and length == len(blob)
    jl = struct.unpack("<I", blob[12:16])[0]
    doc = json.loads(blob[20:20 + jl])
    names = [n["name"] for n in doc["nodes"]]
    assert sorted(n.split("_")[1] for n in names) == ["facets", "points", "ribbons", "sky"]
    mats = {m["name"]: m for m in doc["materials"]}
    assert mats["GOB_points"]["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"] == pytest.approx(G.FALLBACK["points"]["strength"] * 2)
    rib = next(m for m in doc["meshes"] if m["name"].startswith("GOB_ribbons"))
    assert len([k for k in rib["primitives"][0]["attributes"] if k.startswith("TEXCOORD_")]) == len(G.CHANNELS["ribbons"])
    assert out["parts"]["02_PointShader"]["vertices"] == 2 * 42                # the zero-size point is not drawn
    assert doc["extras"]["x3dlab"]["gain"] == 2.0


def test_tube_radius_matches_the_on_screen_width():
    # half width (0.6 + 2.8 * 0.3) px at 640 px tall, 45 degrees, 10 m away
    r = G.ribbon_radius(0.3, 0.0, 10.0)
    px = r / (2 * 10.0 * math.tan(math.pi / 8)) * 640
    assert px == pytest.approx(1.44)


def test_preview_and_score(bake):
    img = G.preview(bake, "Front", 160, 90)
    assert img.shape == (90, 160, 3) and 0 <= img.min() and img.max() <= 1
    assert img[0, 0].sum() > 0                                                 # the sky
    s = G.score(img, img)
    assert s["lit_f"] == 1.0 and s["colour_delta"] == 0.0 and s["ssim"] > 0.99
    dark = np.zeros_like(img)
    assert G.score(dark, img)["lit_f"] == 0.0


def test_part_centres(bake):
    c = G.part_centres(bake)
    assert len(c) == len(bake["parts"]) and all(len(x) == 3 for x in c)


@pytest.mark.skipif(not os.environ.get("X3DLAB_GOBBLER"), reason="set X3DLAB_GOBBLER to gobbler3.x3d to check the real scene")
def test_the_real_gobbler():
    b = G.decode(os.environ["X3DLAB_GOBBLER"])
    roles = {}
    for p in b["parts"]:
        roles[p["role"]] = roles.get(p["role"], 0) + (len(p["a"]) if p["role"] == "ribbons" else len(p["p"]) if p["role"] == "points"
                                                      else len(p["world"]) if "world" in p else 1)
    assert roles == {"sky": 1, "points": 1027, "facets": 577, "ribbons": 1287, "eye": 32, "membrane": 250}
    assert len(b["skipped"]) == 4


def test_bake_variants_and_studio_lights(bake, tmp_path):
    for name, v in G.BAKES.items():
        out = G.write_glb(bake, tmp_path / ("%s.glb" % name), core_scale=v["core_scale"], gain=v["gain"], tube_sides=v["tube_sides"],
                          style=v["style"])
        blob = (tmp_path / ("%s.glb" % name)).read_bytes()
        doc = json.loads(blob[20:20 + struct.unpack("<I", blob[12:16])[0]])
        fac = next(m for m in doc["materials"] if m["name"] == "GOB_facets")
        want = dict(G.FALLBACK["facets"], **v["style"].get("facets", {}))
        assert fac["pbrMetallicRoughness"]["metallicFactor"] == want["metal"] and doc["extras"]["x3dlab"]["style"] == json.loads(json.dumps(v["style"]))
        assert out["triangles"] > 0
    L = G.studio_lights()
    assert [l["DEF"] for l in L] == ["GobLight0", "GobLight1", "GobLight2", "GobLight3"] and L[0]["location"] == [0.0, 2.5, 8.2]
    m = G.manifest_for(bake, {"viewpoints": [], "lights": [{"type": "PointLight"}], "bounds": ((-900, -900, -900), (1, 1, 1))})
    assert len(m["lights"]) == 4 and m["bounds"][0][1] > -900 and len(m["shape_centres"]) == len(bake["parts"])


def test_grades_are_valid_render_grades():
    from x3due import project
    for name, g in G.GRADES.items():
        project.grade_ok(g)
        for n2, (g2, ev) in G.neighbours(name, g, 0.0).items():
            project.grade_ok(g2)
            assert g2["post"]["auto_exposure_bias"] == round(ev, 2)
    nb = G.neighbours("glow", G.GRADES["glow"], 1.0)
    assert set(nb) == {"glow_bright", "glow_dark", "glow_bloomp", "glow_bloomm", "glow_lightsp", "glow_lightsm"}
    assert nb["glow_bloomp"][0]["post"]["bloom_intensity"] == round(1.0 * 1.6, 3) and nb["glow_dark"][1] == pytest.approx(0.3)


def test_contact_sheet_and_resize(bake, tmp_path):
    img = G.preview(bake, "Front", 160, 90)
    G.to_png(img, tmp_path / "a.png")
    sheet = G.contact_sheet({"Front": {"ref": str(tmp_path / "a.png"), "glow": None}}, ["ref", "glow"], tmp_path / "s.png", cell_w=160,
                            scores={("Front", "ref"): {"ssim": 1.0, "lit_f": 1.0, "colour_delta": 0.0}})
    assert Path(sheet).exists()
    r = G.resize_like(tmp_path / "a.png", (45, 80))
    assert r.shape == (45, 80, 3) and r.max() <= 1


def test_notebook_has_the_gobbler_steps():
    src = (UNREAL / "src" / "08_gobbler.py").read_text()
    for k in ("gobbler.decode(", "gobbler.write_glb(", "gobbler.preview(", "gobbler.score(", "gobbler.manifest_for(", "bringup.up(",
              "pipeline.import_scene(", "pipeline.render_grades(", "gobbler.exposure_fit(", "scoreboard.jsonl", "gobbler.neighbours(", "gobbler.contact_sheet(",
              "VERBOSE", "VISUAL"):
        assert k in src, k


def test_exposure_fit_finds_the_ev_between_two_stills(bake):
    ref = G.preview(bake, "Front", 160, 90)
    lin = np.where(ref <= 0.04045, ref / 12.92, ((ref + 0.055) / 1.055) ** 2.4)
    dark = lin / 4                                                              # two stops darker, in linear light
    test = np.where(dark <= 0.0031308, dark * 12.92, 1.055 * dark ** (1 / 2.4) - 0.055)
    assert G.exposure_fit(test, ref) == pytest.approx(2.0, abs=0.15)
    assert G.exposure_fit(np.zeros_like(ref), ref) == 0.0
