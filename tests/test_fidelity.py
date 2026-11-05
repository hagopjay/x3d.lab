"""Fidelity comparison on synthetic snapshots (no Blender needed)."""
import copy

from x3dlab import fidelity

BASE = {
    "objects": [
        {"name": "Shape", "type": "MESH", "location": [0, 0, 0], "scale": [1, 1, 1], "rotation": [1, 0, 0, 0],
         "verts": 24, "uverts": 8, "vhash": "aaa", "bbox": [[-1, -1, -1], [1, 1, 1]]},
        {"name": "Shape.001", "type": "MESH", "location": [3, 0, 0], "scale": [1, 1, 1], "rotation": [1, 0, 0, 0],
         "verts": 24, "uverts": 8, "vhash": "aaa", "bbox": [[2, -1, -1], [4, 1, 1]]},
        {"name": "Sun", "type": "LIGHT", "light": "SUN", "location": [0, 5, 0], "scale": [1, 1, 1],
         "rotation": [0.0076, 0.074, 0.1019, -0.992], "energy": 3.0, "color": [1, 1, 1]},
        {"name": "Bulb", "type": "LIGHT", "light": "POINT", "location": [1, 3, 1], "scale": [1, 1, 1],
         "rotation": [1, 0, 0, 0], "energy": 2.625, "color": [1, 1, 1]},
        {"name": "Viewpoint", "type": "CAMERA", "location": [0, -10, 5], "scale": [1, 1, 1], "rotation": [0.9, 0.4, 0, 0], "fov": 0.7854},
    ],
    "materials": [{"name": "Red", "base": [0.9, 0.2, 0.2]}],
    "counts": {"objects": 5, "meshes": 1, "materials": 1, "lights": 2, "cameras": 1, "vertices": 24, "faces": 12},
}


def test_identical():
    f = fidelity.compare(BASE, copy.deepcopy(BASE))
    assert f.ok and f.matched == 5


def test_renamed_objects_are_matched_by_position():
    b = copy.deepcopy(BASE)
    for o, n in zip(b["objects"], ["group_ME_Shape_Box_001", "group_ME_Shape_Box", "LA_Sun", "LA_Bulb", "CA_Viewpoint"]):
        o["name"] = n
    b["objects"][0]["location"], b["objects"][1]["location"] = [3, 0, 0], [0, 0, 0]       # listed in the other order
    b["objects"][0]["bbox"], b["objects"][1]["bbox"] = BASE["objects"][1]["bbox"], BASE["objects"][0]["bbox"]
    f = fidelity.compare(BASE, b)
    assert f.ok, f.findings


def test_sun_spin_about_its_axis_is_not_a_change():
    b = copy.deepcopy(BASE)
    b["objects"][2]["rotation"] = [0.992, 0.1019, -0.074, 0.0076]      # same beam direction, different quaternion
    assert fidelity.compare(BASE, b).ok


def test_seam_splits_are_not_loss_but_clamped_light_and_lost_instancing_are():
    b = copy.deepcopy(BASE)
    b["objects"][0]["verts"] = 36                                       # split at seams, same unique positions
    b["objects"][3]["energy"] = 1.75                                    # the X3D 3.3 clamp
    b["counts"].update(meshes=2, vertices=48)
    f = fidelity.compare(BASE, b)
    assert not f.geometry_changed
    assert f.instancing == [1, 2]
    assert any("instancing lost" in x for x in f.findings)
    assert any("light energy changed" in x and "×0.667" in x for x in f.findings)


def test_moved_geometry_is_found():
    b = copy.deepcopy(BASE)
    b["objects"][1]["vhash"] = "bbb"
    b["objects"][1]["location"] = [3, 0.5, 0]
    f = fidelity.compare(BASE, b)
    assert f.geometry_changed == ["Shape.001"] and f.max_err["location"] == 0.5


def test_drift_rows():
    gens = []
    for i, e in enumerate([2.625, 1.75, 1.75]):
        s = copy.deepcopy(BASE)
        s["objects"][3]["energy"] = e
        gens.append({"snapshot": s, "bytes": 1000 + i})
    rows = fidelity.drift({"generations": gens})
    assert [r["ok_vs_gen0"] for r in rows] == [True, False, False]
    assert rows[1]["light_ratio_min"] == 0.6667 and rows[0]["meshes"] == 1
