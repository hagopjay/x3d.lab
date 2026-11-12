import json
import math
import struct

import pytest

from x3due import compare, convert as C, gltfstat, x3dscene

SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE X3D PUBLIC "ISO//Web3D//DTD X3D 4.0//EN" "https://www.web3d.org/specifications/x3d-4.0.dtd">
<X3D profile="Immersive" version="4.0"><head><meta name="title" content="t"/></head><Scene>
<NavigationInfo headlight="false"/>
<Background skyColor="0.1 0.2 0.3"/>
<Transform translation="0 0 10"><Viewpoint DEF="Cam" description="front" position="0 0 0" fieldOfView="0.6"/></Transform>
<Viewpoint description="second" position="5 0 0" orientation="0 1 0 1.5708"/>
<DirectionalLight direction="0 -1 0" intensity="0.5"/>
<Transform rotation="0 0 1 1.5708"><SpotLight location="0 2 0" direction="0 -1 0" intensity="2" cutOffAngle="0.7" beamWidth="0.5"/></Transform>
<Transform DEF="A" translation="2 0 0"><Shape DEF="Box1"><Appearance><Material diffuseColor="1 0 0"/></Appearance><Box size="2 2 2"/></Shape></Transform>
<Transform translation="-2 0 0"><Shape USE="Box1"/></Transform>
<Shape><Appearance><PhysicalMaterial/><ImageTexture containerField="baseTexture" url='"tex/a.png" "https://x/a.png"'/></Appearance>
<IndexedFaceSet coordIndex="0 1 2 3 -1 0 2 4 -1"><Coordinate point="0 0 0 1 0 0 1 1 0 0 1 0 0.5 0.5 1"/></IndexedFaceSet></Shape>
<Switch whichChoice="1"><Shape><Box/></Shape><Shape><Sphere radius="0.5"/></Shape><Shape><Cone/></Shape></Switch>
<TimeSensor DEF="T" loop="true"/><PositionInterpolator DEF="P" key="0 1" keyValue="0 0 0 1 1 1"/>
<ROUTE fromNode="T" fromField="fraction_changed" toNode="P" toField="set_fraction"/>
<ROUTE fromNode="P" fromField="value_changed" toNode="A" toField="set_translation"/>
</Scene></X3D>"""


def test_manifest_counts_instances_and_lights():
    m = x3dscene.manifest(SCENE)
    c = m["counts"]
    assert c["shape_instances"] == 4                    # box twice (DEF + USE), IFS, the chosen Switch child
    assert c["geometries_shared"] == 1 and c["geometry_instances_of_shared"] == 2
    assert c["triangles"] == 12 * 2 + 3 + 32 * 16 * 2  # boxes + IFS (quad 2 + tri 1) + the sphere (approximate)
    assert c["triangles_approx"]
    assert c["hidden_switch_children"] == 2
    assert c["materials_by_type"] == {"Material": 1, "PhysicalMaterial": 1}
    assert m["texture_urls"] == ["https://x/a.png", "tex/a.png"]
    assert m["headlight"] is False
    assert c["lights_by_type"] == {"DirectionalLight": 1, "SpotLight": 1}
    spot = next(L for L in m["lights"] if L["type"] == "SpotLight")
    assert all(abs(a - b) < 1e-4 for a, b in zip(spot["location"], (-2, 0, 0)))       # rotated 90 deg about Z
    assert all(abs(a - b) < 1e-4 for a, b in zip(spot["direction"], (1, 0, 0)))
    traps = {t["family"] for t in m["traps"]}
    assert "light scoping" in traps                         # DirectionalLight global defaults to false
    assert "ROUTE" in m["node_types"] and "TimeSensor" in m["node_types"]


def test_manifest_bounds_and_viewpoints():
    m = x3dscene.manifest(SCENE)
    lo, hi = m["bounds"]
    assert lo[0] == -3 and hi[0] == 3
    vps = m["viewpoints"]
    assert [v.get("DEF") for v in vps] == ["Cam", None] and vps[0]["fieldOfView"] == 0.6
    assert C.mat4_point(vps[0]["world_matrix"], vps[0]["position"]) == (0, 0, 10)


def test_ensure_viewpoint_defs_and_strip_behaviour():
    xml, names = x3dscene.ensure_viewpoint_defs(SCENE)
    assert names == ["Cam", "x3dlab_vp_1"]
    assert x3dscene.manifest(xml)["viewpoints"][1]["DEF"] == "x3dlab_vp_1"
    assert "<ROUTE" not in x3dscene.strip_behaviour(SCENE)


def test_viewpoint_wrapper_keeps_the_world_pose():
    m = x3dscene.manifest(SCENE)
    vp = m["viewpoints"][1]
    w = compare.viewpoint_wrapper(vp, "scene.x3d", headlight=True)
    wv = x3dscene.manifest(w)["viewpoints"][0]
    a = C.viewpoint_to_ue(vp["world_matrix"], vp["position"], vp["orientation"])
    b = C.viewpoint_to_ue(wv["world_matrix"], wv["position"], wv["orientation"])
    assert all(abs(x - y) < 1e-3 for x, y in zip(a[0] + a[1], b[0] + b[1]))
    assert 'url=\'"scene.x3d"\'' in w and 'headlight="true"' in w


def test_look_rotation_special_cases():
    for f, u in (((0, 0, -1), (0, 1, 0)), ((0, 0, 1), (0, 1, 0)), ((1, 0, 0), (0, 1, 0)), ((0, -1, 0), (0, 0, -1))):
        rot = compare.look_rotation(f, u)
        R = C.axis_angle_to_mat3(rot[:3], rot[3])
        assert all(abs(a - b) < 1e-6 for a, b in zip(C.mat3_vec(R, (0, 0, -1)), f))


def _glb(js: dict, binary: bytes) -> bytes:
    j = json.dumps(js).encode()
    j += b" " * (-len(j) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<I4s", len(j), b"JSON") + j + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def test_gltfstat_counts_instancing_and_centres(tmp_path):
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1, 2, 3]}],
          "nodes": [{"mesh": 0, "translation": [2, 0, 0]}, {"mesh": 0, "translation": [-2, 0, 0]},
                    {"camera": 0, "translation": [0, 0, 5]}, {"extensions": {"KHR_lights_punctual": {"light": 0}}}],
          "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
          "accessors": [{"count": 8, "type": "VEC3", "componentType": 5126, "min": [-1, -1, -1], "max": [1, 1, 1]},
                        {"count": 36, "type": "SCALAR", "componentType": 5123}],
          "cameras": [{"type": "perspective", "perspective": {"yfov": 0.6, "aspectRatio": 1.7778, "znear": 0.1}}],
          "extensionsUsed": ["KHR_lights_punctual"],
          "extensions": {"KHR_lights_punctual": {"lights": [{"type": "directional", "intensity": 3}]}},
          "materials": [{}]}
    p = tmp_path / "t.glb"
    p.write_bytes(_glb(js, b"\0" * 64))
    s = gltfstat.stats(p)
    c = s["counts"]
    assert c["mesh_nodes"] == 2 and c["meshes"] == 1 and c["shared_meshes"] == 1 and c["triangles"] == 24
    assert c["cameras"] == 1 and c["lights"] == 1 and c["materials"] == 1
    assert sorted(s["shape_centres"]) == [(-2, 0, 0), (2, 0, 0)]
    assert s["cameras"][0]["position"] == (0, 0, 5)


def test_lit_shapes_decide_the_lighting_model_and_the_backdrop_follows_the_colour_space(tmp_path):
    from x3due import convert as C, project
    xml = ("<X3D profile='Full' version='4.0'><Scene><Background skyColor='0.5 0.5 0.5'/>"
           "<Viewpoint position='0 0 5'/><DirectionalLight direction='0 0 -1' intensity='0.3'/>"
           "<Shape><Appearance><PhysicalMaterial/></Appearance><Box/></Shape>"
           "<Shape><Appearance><PhysicalMaterial/></Appearance><Sphere/></Shape>"
           "<Shape><Appearance><Material/></Appearance><Cone/></Shape>"
           "<Shape><Appearance><Material emissiveColor='1 0 0'/></Appearance><PointSet><Coordinate point='0 0 0'/></PointSet></Shape>"
           "</Scene></X3D>")
    m = x3dscene.manifest(xml)
    assert m["counts"]["lit_shapes"] == {"classic": 1, "physical": 2}                  # the point set is not lit
    assert C.lighting_model(m) == "physical"
    x_ite = C.LightMapping(colour_space="x_ite")
    job = project.import_job("s", tmp_path / "s.glb", "gltf", m, tmp_path / "r.json", tmp_path, mapping=x_ite)
    assert job["background_rgb"][0] == pytest.approx(C.srgb_to_linear(0.5) * x_ite.k)
    assert job["key_rgb"] == [x_ite.k, 0.0, x_ite.k] and job["lighting_model"] == "physical"
    light = job["placements"]["x,z,y"]["lights"][0]
    assert light["intensity"] == pytest.approx(0.3 * x_ite.k)                             # physical: lux = i K
