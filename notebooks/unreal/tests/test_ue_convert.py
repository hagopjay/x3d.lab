import math

import pytest

from x3due import convert as C


@pytest.mark.parametrize("name", list(C.AXES))
def test_axes_flip_handedness_and_keep_up(name):
    m = C.AXES[name]
    assert C.det3(m) == -1
    assert C.mat3_vec(m, (0, 1, 0)) == (0, 0, 1)      # X3D up is Unreal up


def test_units_metres_to_centimetres():
    assert C.to_ue_point((1, 2, 3), "x,z,y") == (100, 300, 200)


def close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


@pytest.mark.parametrize("fwd,up", [((1, 0, 0), (0, 0, 1)), ((0, -1, 0), (0, 0, 1)), ((0.3, 0.4, -0.5), (0, 0, 1)),
                                    ((0, 0.6, 0.8), (0.0, -0.8, 0.6)), ((1, 1, 0), (0.2, -0.2, 0.96))])
def test_rotator_round_trip(fwd, up):
    p, y, r = C.rotator_from_axes(fwd, up)
    X, Y, Z = C.rotator_axes(p, y, r)
    f = C.norm(fwd)
    assert close(X, f, 1e-6)
    u = C.norm(up)
    u_perp = C.norm(tuple(a - C.dot(u, f) * b for a, b in zip(u, f)))
    assert close(Z, u_perp, 1e-6)
    assert close(Y, C.cross(Z, X), 1e-6)            # left-handed: Y = Z x X


def test_default_viewpoint_looks_down_minus_z():
    loc, rot = C.viewpoint_to_ue(C.mat4_identity(), (0, 0, 10), (0, 0, 1, 0), "x,z,y")
    assert close(loc, (0, 1000, 0))
    X, _, Z = C.rotator_axes(*rot)
    assert close(X, (0, -1, 0)) and close(Z, (0, 0, 1))


def test_viewpoint_inside_transform():
    m = C.x3d_transform(translation=(1, 0, 0), rotation=(0, 1, 0, math.pi / 2))
    loc, rot = C.viewpoint_to_ue(m, (0, 0, 5), (0, 0, 1, 0), "x,z,y")
    # rotating (0,0,5) about +Y by 90 deg gives (5,0,0); plus the translation: (6,0,0); it now looks down -X
    assert close(loc, (600, 0, 0))
    X, _, _ = C.rotator_axes(*rot)
    assert close(X, (-1, 0, 0))


def test_x3d_transform_scale_orientation_and_center():
    m = C.x3d_transform(center=(1, 0, 0), rotation=(0, 0, 1, math.pi))
    assert close(C.mat4_point(m, (2, 0, 0)), (0, 0, 0))     # rotation about the centre (1,0,0)


def test_fov_short_side_rule():
    f = 0.7854
    assert C.x3d_fov_to_horizontal(f, 1080, 1920) == f                        # portrait: already horizontal
    h = C.x3d_fov_to_horizontal(f, 1920, 1080)
    assert math.isclose(math.tan(h / 2), math.tan(f / 2) * 16 / 9)
    assert math.isclose(C.horizontal_to_x3d_fov(h, 1920, 1080), f)
    fb = C.cine_filmback(h, 1920, 1080)
    assert math.isclose(fb["sensor_height"], 36 * 9 / 16)
    assert math.isclose(2 * math.atan(fb["sensor_width"] / 2 / fb["focal_length"]), h)
    assert math.isclose(2 * math.atan(fb["sensor_height"] / 2 / fb["focal_length"]), f, rel_tol=1e-9)   # vertical = X3D's


def test_light_mapping():
    m = C.LightMapping()
    assert math.isclose(m.k, 1.2)
    assert math.isclose(m.directional_lux(1), math.pi * 1.2)
    # inverse-square X3D attenuation: the same candela at any matching distance
    assert math.isclose(m.point_candela(2, (0, 0, 1), 3), m.point_candela(2, (0, 0, 1), 7))
    # constant attenuation: matched at d (illuminance at d equals the X3D value)
    d = 4.0
    cd = m.point_candela(1, (1, 0, 0), d)
    assert math.isclose(cd / d ** 2, m.directional_lux(1))
    assert math.isclose(C.LightMapping(measured_k=2.0).k, 2.0)


def test_spot_cones():
    assert C.spot_cones_deg(math.pi / 8, math.pi / 4) == pytest.approx((22.5, 45.0))
    assert C.spot_cones_deg(1.5, 1.5)[1] == 80.0


def test_choose_axes_finds_the_importers_mapping():
    pts = [(0, 0, 0), (2, 1, 0), (-1, 3, 4), (5, 0, -2), (0.5, -1, 1)]
    for name in C.AXES:
        imported = [C.to_ue_point(p, name) for p in pts]
        got = C.choose_axes(pts, imported)
        assert got["axes"] == name and got["fits"] and not got["ambiguous"]


def test_choose_axes_symmetric_scene_is_ambiguous():
    pts = [(1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1)]
    got = C.choose_axes(pts, [C.to_ue_point(p, "z,-x,y") for p in pts])
    assert got["ambiguous"]
