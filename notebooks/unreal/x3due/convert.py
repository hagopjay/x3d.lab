"""X3D -> Unreal conversions: units, axes, rotations, field of view, light intensity. Pure Python, no numpy.

X3D (and glTF 2.0) are right-handed, Y up, metres. Unreal is left-handed, Z up, centimetres, and a camera or light
looks down its local +X axis. Every mapping from X3D to Unreal that keeps "up" up is one of the four in AXES (a
mirror of one axis plus a turn about the up axis). Which one an importer used is *measured* after import (see
choose_axes), not assumed.

Field of view: an X3D Viewpoint's fieldOfView is the angle of the *smaller* image side (19775-1, Viewpoint);
Unreal's CameraComponent.FieldOfView is *horizontal*. Light intensity: X3D intensity is a unitless scale on the
lighting equation; Unreal directional lights are in lux, point and spot lights in candela, and the picture also
depends on the exposure. LightMapping turns one into the other with one calibration constant (see light_*).
"""

from __future__ import annotations

import math

CM_PER_M = 100.0

# Unreal = M @ X3D, then * 100. Each has determinant -1 (right-handed -> left-handed) and sends X3D +Y to Unreal +Z.
AXES = {
    "x,z,y": ((1, 0, 0), (0, 0, 1), (0, 1, 0)),      # X3D +X -> UE +X, X3D +Z -> UE +Y (a plain Y/Z swap)
    "z,-x,y": ((0, 0, 1), (-1, 0, 0), (0, 1, 0)),    # X3D +Z (towards the default viewer) -> UE +X
    "-x,-z,y": ((-1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "-z,x,y": ((0, 0, -1), (1, 0, 0), (0, 1, 0)),
}
DEFAULT_AXES = "x,z,y"   # what a plain Y/Z swap gives; confirmed or replaced by choose_axes() after import


# ---------------------------------------------------------------------- small linear algebra
def mat3_vec(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(v):
    n = math.sqrt(dot(v, v))
    return tuple(x / n for x in v) if n > 1e-12 else tuple(v)


def det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def axis_angle_to_mat3(axis, angle):
    """X3D SFRotation (x y z a) -> 3x3 rotation matrix (right-handed, rotation about axis by angle radians)."""
    x, y, z = norm(axis) if any(axis) else (0.0, 0.0, 1.0)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return ((t * x * x + c, t * x * y - s * z, t * x * z + s * y),
            (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
            (t * x * z - s * y, t * y * z + s * x, t * z * z + c))


# 4x4 matrices as tuples of rows; points are column vectors
def mat4_identity():
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat4_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)) for i in range(4))


def mat4_from3(m3, t=(0.0, 0.0, 0.0)):
    return tuple(tuple(m3[i]) + (t[i],) for i in range(3)) + ((0.0, 0.0, 0.0, 1.0),)


def mat4_translate(t):
    return mat4_from3(((1, 0, 0), (0, 1, 0), (0, 0, 1)), t)


def mat4_scale(s):
    return mat4_from3(((s[0], 0, 0), (0, s[1], 0), (0, 0, s[2])))


def mat4_point(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def mat4_dir(m, d):
    return tuple(m[i][0] * d[0] + m[i][1] * d[1] + m[i][2] * d[2] for i in range(3))


def x3d_transform(translation=(0, 0, 0), rotation=(0, 0, 1, 0), scale=(1, 1, 1), scale_orientation=(0, 0, 1, 0),
                  center=(0, 0, 0)):
    """The X3D Transform matrix: T * C * R * SR * S * -SR * -C (19775-1, Transform)."""
    R = mat4_from3(axis_angle_to_mat3(rotation[:3], rotation[3]))
    SR = mat4_from3(axis_angle_to_mat3(scale_orientation[:3], scale_orientation[3]))
    SRi = mat4_from3(axis_angle_to_mat3(scale_orientation[:3], -scale_orientation[3]))
    m = mat4_translate(translation)
    for x in (mat4_translate(center), R, SR, mat4_scale(scale), SRi, mat4_translate(tuple(-c for c in center))):
        m = mat4_mul(m, x)
    return m


# ---------------------------------------------------------------------- axes and units
def to_ue_point(p, axes=DEFAULT_AXES):
    return tuple(CM_PER_M * x for x in mat3_vec(AXES[axes], p))


def to_ue_dir(d, axes=DEFAULT_AXES):
    return norm(mat3_vec(AXES[axes], d))


def rotator_from_axes(forward, up=None):
    """Unreal FRotator (pitch, yaw, roll) in degrees whose local +X is `forward` and local +Z is as close to `up` as
    possible. Mirrors FMatrix::Rotator() (Unreal's left-handed convention, angles in degrees)."""
    f = norm(forward)
    pitch = math.atan2(f[2], math.sqrt(f[0] ** 2 + f[1] ** 2))
    yaw = math.atan2(f[1], f[0])
    if up is None:
        return (math.degrees(pitch), math.degrees(yaw), 0.0)
    u = norm(up)
    y_axis = norm(cross(u, f))                 # Unreal: Y = Z x X
    z_axis = cross(f, y_axis)                  # re-orthogonalised up
    sy = (-math.sin(yaw), math.cos(yaw), 0.0)  # Y axis of FRotator(pitch, yaw, 0)
    roll = math.atan2(dot(z_axis, sy), dot(y_axis, sy))
    return (math.degrees(pitch), math.degrees(yaw), math.degrees(roll))


def rotator_axes(pitch, yaw, roll):
    """(X, Y, Z) unit axes of an Unreal FRotator (FRotationMatrix), for tests and checks."""
    p, y, r = (math.radians(a) for a in (pitch, yaw, roll))
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    X = (cp * cy, cp * sy, sp)
    Y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    Z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return X, Y, Z


def viewpoint_to_ue(world_matrix, position, orientation, axes=DEFAULT_AXES):
    """X3D Viewpoint (local position + orientation under `world_matrix`) -> (UE location cm, UE rotator deg).
    An X3D viewer looks down its local -Z with +Y up; an Unreal camera looks down +X with +Z up."""
    R = axis_angle_to_mat3(orientation[:3], orientation[3])
    f_local, u_local = mat3_vec(R, (0, 0, -1)), mat3_vec(R, (0, 1, 0))
    pos_w = mat4_point(world_matrix, position)
    f_w, u_w = norm(mat4_dir(world_matrix, f_local)), norm(mat4_dir(world_matrix, u_local))
    return to_ue_point(pos_w, axes), rotator_from_axes(to_ue_dir(f_w, axes), to_ue_dir(u_w, axes))


def light_dir_to_ue(direction_world, axes=DEFAULT_AXES):
    """Rotator whose +X points the way the light travels (Unreal lights shine down local +X)."""
    return rotator_from_axes(to_ue_dir(direction_world, axes))


# ---------------------------------------------------------------------- field of view
def x3d_fov_to_horizontal(fov, width, height):
    """X3D fieldOfView (radians, smaller image side) -> horizontal field of view in radians for a W x H image."""
    if width >= height:                         # landscape: fov is vertical
        return 2 * math.atan(math.tan(fov / 2) * width / height)
    return fov                                  # portrait: fov is already horizontal


def horizontal_to_x3d_fov(hfov, width, height):
    if width >= height:
        return 2 * math.atan(math.tan(hfov / 2) * height / width)
    return hfov


def cine_filmback(hfov, width, height, sensor_width_mm=36.0):
    """CineCamera filmback + focal length giving horizontal fov `hfov` at aspect W/H (sensor_height follows aspect,
    so the camera does not crop or letterbox)."""
    sensor_h = sensor_width_mm * height / width
    focal = (sensor_width_mm / 2) / math.tan(hfov / 2)
    return {"sensor_width": sensor_width_mm, "sensor_height": sensor_h, "focal_length": focal}


# ---------------------------------------------------------------------- light intensity
COLOUR_SPACES = ("x_ite", "as_is")


def srgb_to_linear(v: float) -> float:
    """sRGB transfer function, extended past 1 by its power law (X3D intensities may exceed 1)."""
    v = max(0.0, float(v))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


class LightMapping:
    """X3D light intensity -> Unreal physical units, so a surface lands on screen as X_ITE draws it.

    With manual exposure at EV100 = ev100 Unreal scales scene luminance L (cd/m2) to a pixel value L / K with
    K = 1.2 * 2**ev100 (Unreal's exposure calibration; the constant is checked, and corrected, by the calibration
    scene the notebook renders first: `measured` replaces it). A Lambertian surface of albedo rho under illuminance E
    has L = rho * E / pi.

    What X_ITE draws (measured 12 Nov 2026, X_ITE 16.7, its default LINEAR_WHEN_PHYSICAL_MATERIAL colour space):
    * a classic Material (model "classic") is lit in display space: the pixel is rho * i itself. In linear terms that
      is lin(rho * i) ~ lin(rho) * lin(i). With colour_space "x_ite" the glb holds lin(rho) (the add-on's SRGB colour
      space), so the light needs E = lin(i) * pi * K; with "as_is" the glb holds rho, and E = i * pi * K matches at
      i = 1 (the calibration box) and is too bright below.
    * a PhysicalMaterial (model "physical") is lit by X_ITE's PBR in linear space: rho / pi * i. So E = i * K.

    Point and spot lights: X3D illumination is i / (a0 + a1 d + a2 d^2); Unreal's inverse-square candela light gives
    I / d^2 lux. There is no exact match unless attenuation is (0 0 1); we match at `d_ref` (the distance from the
    light to the scene's centre), so the scene's middle is lit as X3D lights it and falloff is physical elsewhere.
    """

    def __init__(self, ev100: float = 0.0, measured_k: float | None = None, colour_space: str = "as_is"):
        self.ev100 = ev100
        self.k = measured_k if measured_k else 1.2 * (2.0 ** ev100)
        if colour_space not in COLOUR_SPACES:
            raise ValueError("colour_space must be one of %s" % ", ".join(COLOUR_SPACES))
        self.colour_space = colour_space

    def colour(self, c: float) -> float:
        """An X3D classic colour value (Material, UnlitMaterial, Background) as the linear value Unreal should get."""
        return srgb_to_linear(c) if self.colour_space == "x_ite" else float(c)

    def _illuminance(self, i: float, model: str) -> float:
        """X3D intensity (attenuated) -> lux / K."""
        if model == "physical":
            return i
        return math.pi * self.colour(i)

    def directional_lux(self, intensity, model: str = "classic"):
        return self._illuminance(intensity, model) * self.k

    def point_candela(self, intensity, attenuation=(1, 0, 0), d_ref_m=1.0, model: str = "classic"):
        a0, a1, a2 = attenuation
        d = max(d_ref_m, 1e-3)
        denom = a0 + a1 * d + a2 * d * d
        if denom <= 0:
            denom = 1.0
        return self._illuminance(intensity / denom, model) * self.k * d * d

    def describe(self):
        rule = ("classic: directional lux = %s*pi*K, point/spot candela = %s*pi*K*d^2 at d = light-to-centre distance; "
                "physical: lux = i*K" % (("lin(i)", "lin(i/(a0+a1 d+a2 d^2))") if self.colour_space == "x_ite"
                                         else ("i", "i/(a0+a1 d+a2 d^2)")))
        return {"ev100": self.ev100, "k": round(self.k, 6), "colour_space": self.colour_space, "rule": rule}


def lighting_model(manifest: dict) -> str:
    """'physical' when more lit shapes use a PhysicalMaterial than a Material / TwoSidedMaterial, else 'classic'
    (X_ITE lights the two differently; Unreal's lights are shared by all shapes, so the majority decides)."""
    lit = (manifest.get("counts") or {}).get("lit_shapes") or manifest.get("lit_shapes") or {}
    return "physical" if lit.get("physical", 0) > lit.get("classic", 0) else "classic"


def spot_cones_deg(beam_width, cut_off_angle):
    """X3D SpotLight beamWidth / cutOffAngle (half angles, radians) -> Unreal inner / outer cone angles (degrees).
    Unreal caps the outer cone at 80 degrees."""
    outer = min(math.degrees(cut_off_angle), 80.0)
    inner = min(math.degrees(beam_width), outer)
    return inner, outer


# ---------------------------------------------------------------------- which axes did the importer use?
def choose_axes(manifest_points, imported_points, tol_cm=5.0):
    """Compare X3D-space points (metres) with the same points measured in Unreal (cm), e.g. shape bounding-box
    centres, under each candidate mapping. Returns {"axes": best, "errors_cm": {name: mean nearest error}, "ambiguous": bool}.
    Nearest-neighbour matching, so the order of the two lists does not matter."""
    if not manifest_points or not imported_points:
        return {"axes": DEFAULT_AXES, "errors_cm": {}, "ambiguous": True, "reason": "no points to compare"}
    errs = {}
    for name in AXES:
        mapped = [to_ue_point(p, name) for p in manifest_points]
        total = 0.0
        for q in imported_points:
            total += min(math.dist(q, m) for m in mapped)
        errs[name] = total / len(imported_points)
    ranked = sorted(errs, key=errs.get)
    best = ranked[0]
    scale = max(1.0, max(abs(c) for p in imported_points for c in p))
    ambiguous = len(ranked) > 1 and errs[ranked[1]] - errs[best] < max(tol_cm, 1e-3 * scale)
    return {"axes": best, "errors_cm": {k: round(v, 3) for k, v in errs.items()}, "ambiguous": ambiguous,
            "fits": errs[best] <= max(tol_cm, 1e-2 * scale)}
