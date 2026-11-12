"""Six original X3D 4.0 showcase scenes, each aimed at a known loss in the X3D → Blender → glTF → Unreal chain.

Every scene is deterministic (no randomness, no external assets), so builds are byte-identical and the
mission's pixel and count checks are repeatable. ``SCENES`` maps an id to a builder returning (Doc, info).

The ``info`` dict is what the dev console and the mission read: a blurb, what it exercises, and named
probes (viewpoints and screen-space samples) that the MCP mission measures in X_ITE.
"""
from __future__ import annotations

import colorsys
import math

from .model import Doc, Field, N, Proto, Route, Text

TAU = math.pi * 2


def r6(x: float) -> float:
    return round(x, 5)


def hue(h: float, s: float = 0.75, v: float = 0.95):
    return tuple(r6(c) for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def vp(desc, position, orientation=(0, 1, 0, 0), fov=0.785398, DEF=None, center=None):
    kw = dict(description=desc, position=position, orientation=orientation, fieldOfView=fov)
    if center is not None:
        kw["centerOfRotation"] = center
    return N("Viewpoint", DEF=DEF, **kw)


def look_at(eye, target=(0, 0, 0)):
    """Viewpoint orientation (axis-angle) that looks from ``eye`` at ``target`` with +Y up."""
    fx, fy, fz = (target[0] - eye[0], target[1] - eye[1], target[2] - eye[2])
    n = math.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx / n, fy / n, fz / n
    # camera looks down -Z; build rotation from yaw then pitch
    yaw = math.atan2(-fx, -fz)
    pitch = math.asin(max(-1.0, min(1.0, fy)))
    # quaternion yaw(Y) * pitch(X)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    w, x, y, z = cy * cp, cy * sp, sy * cp, -sy * sp
    angle = 2 * math.acos(max(-1.0, min(1.0, w)))
    s = math.sqrt(max(1e-12, 1 - w * w))
    return (r6(x / s), r6(y / s), r6(z / s), r6(angle)) if angle > 1e-6 else (0, 1, 0, 0)


def quad(cx, cy, z, half):
    return [(cx - half, cy - half, z), (cx + half, cy - half, z), (cx + half, cy + half, z), (cx - half, cy + half, z)]


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 1. Material ladder: Blinn-Phong → GGX, PBR, TwoSidedMaterial, and a colour-space probe
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def material_ladder():
    doc = Doc("Interchange", title="Material ladder",
              description="Blinn-Phong shininess and specular ladder, PhysicalMaterial metal/roughness, "
                          "TwoSidedMaterial and a four-swatch colour-space probe.",
              components=[("Shape", 4), ("Lighting", 2)])
    doc.add(N("WorldInfo", title="Material ladder", info=["x3dlab scene 1"]),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.06, 0.07, 0.1)]),
            vp("Ladder", (0, 0.4, 11.5), DEF="VP_Ladder", center=(0, 0.4, 0)),
            vp("Colour-space probe", (0, -40, 5), DEF="VP_ColourSpace", fov=0.785398, center=(0, -40, 0)),
            N("DirectionalLight", direction=(-0.4, -0.6, -0.7), intensity=0.9),
            N("PointLight", location=(4, 5, 6), intensity=0.6, radius=60))

    shin = [0.04, 0.12, 0.3, 0.6, 1.0]
    row_a = N("Transform", DEF="RowPhong", translation=(0, 2.4, 0))
    for i, s in enumerate(shin):
        row_a.add(N("Transform", N("Shape",
                                   N("Appearance", N("Material", DEF="Phong%d" % i, diffuseColor=hue(0.02 + i * 0.12, 0.7, 0.75),
                                                     specularColor=(0.9, 0.9, 0.9), shininess=s)),
                                   N("Sphere", radius=0.8)), translation=((i - 2) * 2.0, 0, 0)))
    row_b = N("Transform", DEF="RowPhysical", translation=(0, 0.3, 0))
    for i in range(5):
        row_b.add(N("Transform", N("Shape",
                                   N("Appearance", N("PhysicalMaterial", DEF="Pbr%d" % i, baseColor=(0.95, 0.72, 0.38),
                                                     metallic=r6(i / 4), roughness=0.3)),
                                   N("Sphere", radius=0.8)), translation=((i - 2) * 2.0, 0, 0)))
    row_c = N("Transform", DEF="RowRough", translation=(0, -1.8, 0))
    for i in range(5):
        row_c.add(N("Transform", N("Shape",
                                   N("Appearance", N("PhysicalMaterial", baseColor=(0.55, 0.62, 0.7), metallic=1,
                                                     roughness=r6(0.08 + i * 0.22))),
                                   N("Sphere", radius=0.8)), translation=((i - 2) * 2.0, 0, 0)))
    # TwoSidedMaterial card that turns, so both faces show
    card = N("Transform", DEF="Card", translation=(0, -3.7, 0))
    card.add(N("Shape",
               N("Appearance", N("TwoSidedMaterial", diffuseColor=(0.9, 0.25, 0.2), backDiffuseColor=(0.2, 0.45, 0.95),
                                 separateBackColor=True)),
               N("IndexedFaceSet", N("Coordinate", point=[(-3, -0.6, 0), (3, -0.6, 0), (3, 0.6, 0), (-3, 0.6, 0)]),
                 coordIndex=[0, 1, 2, 3, -1], solid=False)))
    spin = [N("TimeSensor", DEF="CardClock", cycleInterval=10, loop=True),
            N("OrientationInterpolator", DEF="CardTurn", key=[0, 0.5, 1],
              keyValue=[(1, 0, 0, 0), (1, 0, 0, 3.14159), (1, 0, 0, 6.28318)]),
            Route("CardClock", "fraction_changed", "CardTurn", "set_fraction"),
            Route("CardTurn", "value_changed", "Card", "set_rotation")]

    # Colour-space probe: four flat unlit-ish swatches at y=-40, each fills one quadrant of VP_ColourSpace.
    # All four ask for 0.5 grey. X_ITE's default colour space (LINEAR_WHEN_PHYSICAL_MATERIAL) reads Material and
    # UnlitMaterial colours as sRGB (displays 128) and PhysicalMaterial as linear (displays ~188).
    def swatch(DEF, cx, cy, appearance, color_node=None):
        ifs = N("IndexedFaceSet", N("Coordinate", point=quad(cx, cy, 0, 0.98)), coordIndex=[0, 1, 2, 3, -1], solid=False,
                colorPerVertex=False if color_node else True)
        if color_node:
            ifs.add(color_node)
            ifs.fields["colorIndex"] = [0]
        return N("Shape", appearance, ifs, DEF=DEF)

    probe = N("Transform", DEF="ColourSpaceProbe", translation=(0, -40, 0))
    probe.add(swatch("CS_Material", -1, 1, N("Appearance", N("Material", diffuseColor=(0, 0, 0), emissiveColor=(0.5, 0.5, 0.5)))),
              swatch("CS_Unlit", 1, 1, N("Appearance", N("UnlitMaterial", emissiveColor=(0.5, 0.5, 0.5)))),
              swatch("CS_Physical", -1, -1, N("Appearance", N("PhysicalMaterial", baseColor=(0, 0, 0),
                                                               emissiveColor=(0.5, 0.5, 0.5), metallic=0, roughness=1))),
              swatch("CS_ColorNode", 1, -1, N("Appearance", N("UnlitMaterial")), N("Color", color=[(0.5, 0.5, 0.5)])))
    doc.add(row_a, row_b, row_c, card, *spin, probe)
    info = {
        "title": "Material ladder",
        "blurb": "Shininess and specular ladder (Blinn-Phong), metal and roughness ladders (PBR), a turning "
                 "TwoSidedMaterial card, and a four-swatch colour-space probe.",
        "exercises": ["Material.specularColor/shininess → GGX", "PhysicalMaterial", "TwoSidedMaterial",
                      "colour space: Material vs PhysicalMaterial vs Color node"],
        "probes": {"colourSpace": {"viewpoint": "VP_ColourSpace", "distance": 5, "fov": 0.785398, "plane": "z=0",
                                   "samples": {"CS_Material": [-1, 1], "CS_Unlit": [1, 1], "CS_Physical": [-1, -1],
                                               "CS_ColorNode": [1, -1]},
                                   "expect_xite": {"CS_Material": 128, "CS_Unlit": 128, "CS_Physical": 188,
                                                   "CS_ColorNode": 128},
                                   "expect_linear_pipeline": {"CS_Material": 188, "CS_Unlit": 188, "CS_Physical": 188,
                                                              "CS_ColorNode": 128}}},
    }
    return doc, info


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 2. Lines and points: unlit, coloured per vertex, ColorRGBA alpha, LineProperties, gradient sky
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def lines_and_points():
    doc = Doc("Immersive", title="Lissajous observatory",
              description="IndexedLineSet, LineSet and PointSet with per-vertex Color and ColorRGBA, LineProperties, "
                          "an emissive-only line set and a gradient Background.",
              components=[("Shape", 3)])
    doc.add(N("WorldInfo", title="Lissajous observatory"),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.01, 0.01, 0.04), (0.05, 0.03, 0.14), (0.16, 0.08, 0.22)], skyAngle=[0.9, 1.57],
              groundColor=[(0.01, 0.01, 0.04), (0.03, 0.02, 0.06)], groundAngle=[1.57]),
            vp("Observatory", (0, 1.2, 9.5), look_at((0, 1.2, 9.5), (0, 0, 0)), DEF="VP_Main"),
            vp("Top", (0, 11, 0.01), look_at((0, 11, 0.01), (0, 0, 0)), DEF="VP_Top"))

    # Lissajous knot, 720 vertices, rainbow per vertex
    n = 720
    pts, cols = [], []
    for i in range(n):
        t = TAU * i / n
        pts.append((r6(2.6 * math.sin(3 * t + 0.5)), r6(2.0 * math.sin(4 * t)), r6(2.6 * math.sin(5 * t + 1.1))))
        cols.append(hue(i / n, 0.8, 1.0))
    knot = N("Shape", DEF="Knot")
    knot.add(N("Appearance", N("LineProperties", linewidthScaleFactor=3, applied=True)),
             N("IndexedLineSet", N("Coordinate", point=pts), N("Color", color=cols),
               coordIndex=list(range(n)) + [0, -1], colorPerVertex=True))

    # LineSet of 18 latitude rings, coloured per ring via per-vertex colours
    rings_pts, rings_cols, counts = [], [], []
    for k in range(18):
        lat = -1.3 + k * (2.6 / 17)
        rr = 3.6 * math.cos(lat * 0.9)
        y = 3.2 * math.sin(lat * 0.9)
        m = 64
        c = hue(0.55 + k * 0.012, 0.6, 0.65)
        for j in range(m + 1):
            a = TAU * j / m
            rings_pts.append((r6(rr * math.cos(a)), r6(y), r6(rr * math.sin(a))))
            rings_cols.append(c)
        counts.append(m + 1)
    rings = N("Shape", DEF="Rings")
    rings.add(N("Appearance", N("Material", emissiveColor=(0.2, 0.25, 0.4), transparency=0.6)),
              N("LineSet", N("Coordinate", point=rings_pts), N("ColorRGBA", color=[c + (0.35,) for c in rings_cols]),
                vertexCount=counts))

    # Emissive-only line set: no Color node, so X3D draws it in emissiveColor (unlit)
    axes = N("Shape", N("Appearance", N("Material", diffuseColor=(1, 0, 0), emissiveColor=(0.95, 0.75, 0.2))),
             N("IndexedLineSet", N("Coordinate", point=[(-4.5, 0, 0), (4.5, 0, 0), (0, -4, 0), (0, 4, 0), (0, 0, -4.5), (0, 0, 4.5)]),
               coordIndex=[0, 1, -1, 2, 3, -1, 4, 5, -1]), DEF="Axes")

    # Galaxy: 2,400 points, two arms, ColorRGBA with falling alpha
    gp, gc = [], []
    m = 2400
    for i in range(m):
        arm = i % 2
        f = (i // 2) / (m / 2)
        r = 0.25 + 4.2 * f
        a = arm * math.pi + f * 9.0 + 0.35 * math.sin(i * 12.9898)
        jitter = 0.18 * math.sin(i * 78.233) * (0.4 + f)
        gp.append((r6((r + jitter) * math.cos(a)), r6(0.25 * math.sin(i * 4.1414) * (1 - f)), r6((r + jitter) * math.sin(a))))
        c = hue(0.62 - 0.5 * f, 0.55, 1.0)
        gc.append(c + (r6(1.0 - 0.75 * f),))
    galaxy = N("Transform", N("Shape", N("Appearance", N("PointProperties", pointSizeScaleFactor=2)),
                              N("PointSet", N("Coordinate", point=gp), N("ColorRGBA", color=gc)), DEF="Galaxy"),
               DEF="GalaxySpin", translation=(0, -2.6, 0))
    spin = [N("TimeSensor", DEF="Clock", cycleInterval=40, loop=True),
            N("OrientationInterpolator", DEF="Turn", key=[0, 0.5, 1], keyValue=[(0, 1, 0, 0), (0, 1, 0, 3.14159), (0, 1, 0, 6.28318)]),
            Route("Clock", "fraction_changed", "Turn", "set_fraction"),
            Route("Turn", "value_changed", "GalaxySpin", "set_rotation")]
    # A PointSet with a Material and no Color: X3D says emissiveColor (black) — invisible on purpose.
    dark = N("Shape", N("Appearance", N("Material", diffuseColor=(1, 1, 1))),
             N("PointSet", N("Coordinate", point=[(r6(-4 + i * 0.4), 3.6, 0) for i in range(21)])), DEF="DarkPoints")
    doc.add(knot, rings, axes, galaxy, dark, *spin)
    info = {
        "title": "Lissajous observatory",
        "blurb": "A 720-vertex rainbow knot, 18 translucent rings, a 2,400-point two-arm galaxy, emissive-only axes, and "
                 "a deliberately invisible PointSet (no emissiveColor, so X3D draws it black).",
        "exercises": ["unlit lines and points", "Color / ColorRGBA per vertex", "LineProperties / PointProperties",
                      "Background sky/ground gradient"],
        "probes": {},
    }
    return doc, info


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 3. Shader ribbon: GLSL ComposedShader with a Material fallback (X_ITE-only look)
# ─────────────────────────────────────────────────────────────────────────────────────────────────
VERT = """#version 300 es
precision highp float;
uniform mat4 x3d_ProjectionMatrix;
uniform mat4 x3d_ModelViewMatrix;
uniform mat3 x3d_NormalMatrix;
uniform float phase;
in vec4 x3d_Vertex;
in vec3 x3d_Normal;
in vec4 x3d_TexCoord0;
out vec3 vNormal;
out vec3 vView;
out vec2 vUv;
void main () {
  vec4 p = x3d_Vertex;
  p.xyz += x3d_Normal * 0.06 * sin (x3d_TexCoord0.s * 40.0 - phase * 6.2831853);
  vec4 eye = x3d_ModelViewMatrix * p;
  vView = -eye.xyz;
  vNormal = normalize (x3d_NormalMatrix * x3d_Normal);
  vUv = x3d_TexCoord0.st;
  gl_Position = x3d_ProjectionMatrix * eye;
}
"""

FRAG = """#version 300 es
precision highp float;
uniform float phase;
uniform vec3 tint;
in vec3 vNormal;
in vec3 vView;
in vec2 vUv;
out vec4 x3d_FragColor;
vec3 palette (float t) {
  return 0.5 + 0.5 * cos (6.2831853 * (vec3 (0.0, 0.33, 0.67) + t));
}
void main () {
  float t = phase * 6.2831853;
  float v = sin (vUv.s * 23.0 + t) + sin ((vUv.t * 7.0 + vUv.s * 11.0) - t * 1.3) + sin (length (vUv - 0.5) * 30.0 - t * 2.0);
  vec3 n = normalize (vNormal);
  if (!gl_FrontFacing) n = -n;
  float rim = pow (1.0 - abs (dot (n, normalize (vView))), 2.5);
  vec3 c = palette (v * 0.16 + phase) * tint + rim * vec3 (0.9, 0.95, 1.0);
  x3d_FragColor = vec4 (c, 1.0);
}
"""


def shader_ribbon():
    doc = Doc("Full", title="Plasma ribbon",
              description="A twisted ribbon drawn by a GLSL ComposedShader whose phase is driven by a TimeSensor ROUTE; "
                          "the Appearance also carries a plain Material, the fallback every other engine draws.",
              components=[("Shaders", 1)])
    doc.add(N("WorldInfo", title="Plasma ribbon"),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.02, 0.02, 0.03)]),
            N("DirectionalLight", direction=(-0.3, -0.5, -0.8), intensity=1),
            vp("Ribbon", (0, 0.6, 8.2), look_at((0, 0.6, 8.2), (0, 0, 0)), DEF="VP_Main"))
    # ribbon: u along a (2,3) torus-knot-ish path, v across a twisting strip
    nu, nv = 240, 10
    pts, tex, idx = [], [], []
    for i in range(nu + 1):
        u = i / nu
        a = TAU * u
        cx, cy, cz = 2.4 * math.cos(a) + 0.6 * math.cos(3 * a), 0.9 * math.sin(2 * a), 2.4 * math.sin(a) - 0.6 * math.sin(3 * a)
        twist = 1.5 * a
        # local frame: radial and up, twisted
        rx, rz = math.cos(a), math.sin(a)
        for j in range(nv + 1):
            v = j / nv - 0.5
            w = 0.55 * v
            ox = w * (math.cos(twist) * rx)
            oy = w * math.sin(twist)
            oz = w * (math.cos(twist) * rz)
            pts.append((r6(cx + ox), r6(cy + oy), r6(cz + oz)))
            tex.append((r6(u), r6(j / nv)))
    for i in range(nu):
        for j in range(nv):
            a0 = i * (nv + 1) + j
            b0 = (i + 1) * (nv + 1) + j
            idx += [a0, b0, b0 + 1, a0 + 1, -1]
    shader = N("ComposedShader", DEF="Plasma", language="GLSL")
    shader.add(Field("phase", "SFFloat", "inputOutput", 0), Field("tint", "SFColor", "inputOutput", (1, 0.85, 1)),
               N("ShaderPart", Text(VERT), type="VERTEX"), N("ShaderPart", Text(FRAG), type="FRAGMENT"))
    ribbon = N("Shape", DEF="Ribbon")
    ribbon.add(N("Appearance", N("Material", DEF="Fallback", diffuseColor=(0.55, 0.3, 0.85), specularColor=(0.6, 0.6, 0.6),
                                 shininess=0.5), shader),
               N("IndexedFaceSet", N("Coordinate", point=pts), N("TextureCoordinate", point=tex), coordIndex=idx,
                 solid=False, creaseAngle=3.14159))
    orb_shader = N("ComposedShader", DEF="OrbShader", language="GLSL")
    orb_shader.add(Field("phase", "SFFloat", "inputOutput", 0), Field("tint", "SFColor", "inputOutput", (0.45, 0.9, 1)),
                   N("ShaderPart", Text(VERT), type="VERTEX"), N("ShaderPart", Text(FRAG), type="FRAGMENT"))
    orb = N("Shape", N("Appearance", N("Material", diffuseColor=(0.2, 0.6, 0.8)), orb_shader), N("Sphere", radius=0.9), DEF="Orb")
    doc.add(ribbon, orb,
            N("TimeSensor", DEF="Clock", cycleInterval=6, loop=True),
            Route("Clock", "fraction_changed", "Plasma", "phase"),
            Route("Clock", "fraction_changed", "OrbShader", "phase"))
    info = {
        "title": "Plasma ribbon",
        "blurb": "A 2,400-quad twisted ribbon and an orb drawn by GLSL ComposedShaders, animated by a TimeSensor ROUTE. "
                 "Every other engine draws the fallback Material instead: the Gobbler problem in miniature.",
        "exercises": ["ComposedShader / ShaderPart (GLSL ES 3.0)", "shader uniforms routed from TimeSensor",
                      "Material fallback", "TextureCoordinate"],
        "probes": {},
    }
    return doc, info


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 4. Terrain: an ElevationGrid whose heights come from a Script (and its baked twin)
# ─────────────────────────────────────────────────────────────────────────────────────────────────
GRID = 49
SPACING = 0.25


def terrain_height(i: int, j: int) -> float:
    """Deterministic ridged terrain. The Script below computes exactly this in JavaScript."""
    x, z = i / (GRID - 1), j / (GRID - 1)
    h = 0.0
    amp, freq = 1.0, 1.0
    for o in range(4):
        h += amp * math.sin(freq * (x * 6.1 + o * 1.7)) * math.cos(freq * (z * 5.3 - o * 0.9))
        amp *= 0.5
        freq *= 2.03
    ridge = 1.0 - abs(math.sin(x * 4.0 + z * 3.0))
    d = math.hypot(x - 0.5, z - 0.5)
    return 0.55 * h + 0.6 * ridge * ridge - 1.4 * d * d


def terrain_color(h: float):
    if h < -0.15:
        return (0.12, 0.3, 0.55)
    if h < 0.05:
        return (0.76, 0.7, 0.5)
    if h < 0.55:
        return (0.25, 0.55, 0.22)
    if h < 0.85:
        return (0.45, 0.4, 0.35)
    return (0.95, 0.95, 0.97)


TERRAIN_JS = """
function heightAt (i, j) {
  var x = i / (%(G)d - 1), z = j / (%(G)d - 1);
  var h = 0, amp = 1, freq = 1;
  for (var o = 0; o < 4; ++ o) {
    h += amp * Math.sin (freq * (x * 6.1 + o * 1.7)) * Math.cos (freq * (z * 5.3 - o * 0.9));
    amp *= 0.5;
    freq *= 2.03;
  }
  var ridge = 1 - Math.abs (Math.sin (x * 4 + z * 3));
  var d = Math.hypot (x - 0.5, z - 0.5);
  return 0.55 * h + 0.6 * ridge * ridge - 1.4 * d * d;
}
function colorAt (h) {
  if (h < -0.15) return new SFColor (0.12, 0.3, 0.55);
  if (h < 0.05)  return new SFColor (0.76, 0.7, 0.5);
  if (h < 0.55)  return new SFColor (0.25, 0.55, 0.22);
  if (h < 0.85)  return new SFColor (0.45, 0.4, 0.35);
  return new SFColor (0.95, 0.95, 0.97);
}
function initialize () {
  var heights = new MFFloat ();
  var colors = new MFColor ();
  for (var j = 0; j < %(G)d; ++ j)
    for (var i = 0; i < %(G)d; ++ i) {
      var h = heightAt (i, j);
      heights [heights .length] = h;
      colors [colors .length] = colorAt (h);
    }
  grid .set_height = heights;
  grid .color .color = colors;
  Browser .print ('x3dlab terrain: ' + heights .length + ' heights grown by Script');
}
""" % {"G": GRID}


def terrain(baked: bool):
    title = "Terrain (baked)" if baked else "Terrain (Script-grown)"
    doc = Doc("Full" if not baked else "Immersive", title=title,
              description=("ElevationGrid heights written literally by Python (the same function the Script runs)."
                           if baked else
                           "A 49 × 49 ElevationGrid with no heights in the file: a Script grows them at initialize(), "
                           "the way the manifold demo does. Plus Fog and a translucent water plane."),
              components=[("Scripting", 1)] if not baked else [])
    doc.add(N("WorldInfo", title=title),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.55, 0.7, 0.9), (0.8, 0.88, 0.95)], skyAngle=[1.4]),
            N("Fog", color=(0.8, 0.88, 0.95), fogType="EXPONENTIAL", visibilityRange=40),
            N("DirectionalLight", direction=(-0.5, -0.8, -0.3), intensity=1.05),
            vp("Overlook", (0, 5.5, 10.5), look_at((0, 5.5, 10.5), (0, 0, 0)), DEF="VP_Main"))
    size = (GRID - 1) * SPACING
    heights = [r6(terrain_height(i, j)) for j in range(GRID) for i in range(GRID)] if baked else None
    grid_kw = dict(xDimension=GRID, zDimension=GRID, xSpacing=SPACING, zSpacing=SPACING, creaseAngle=1.2, solid=False)
    if baked:
        grid_kw["height"] = heights
    color = N("Color", color=[terrain_color(h) for h in heights]) if baked else N("Color")
    grid = N("ElevationGrid", color, DEF="Terrain", **grid_kw)
    land = N("Transform", N("Shape", N("Appearance", N("Material", diffuseColor=(1, 1, 1), specularColor=(0.05, 0.05, 0.05))),
                            grid), translation=(-size / 2, 0, -size / 2))
    water = N("Shape", N("Appearance", N("Material", diffuseColor=(0.1, 0.35, 0.6), transparency=0.45,
                                         specularColor=(0.6, 0.6, 0.6), shininess=0.8)),
              N("Box", size=(size, 0.02, size)), DEF="Water")
    doc.add(land, N("Transform", water, translation=(0, -0.12, 0)))
    if not baked:
        script = N("Script", Field("grid", "SFNode", "initializeOnly", None, N("ElevationGrid", USE="Terrain")),
                   Text(TERRAIN_JS), DEF="Grower", directOutput=True)
        doc.add(script)
    info = {
        "title": title,
        "blurb": ("The same terrain with every height written into the file by Python. Compare it with the Script-grown "
                  "twin: identical in X_ITE, but only this one survives the Blender leg."
                  if baked else
                  "A 49 × 49 ElevationGrid shipped with no heights: an ecmascript Script grows the terrain and its "
                  "colours at initialize(). Engines that do not run Scripts see a flat plane."),
        "exercises": ["ElevationGrid", "Script (directOutput, SFNode field)", "Fog", "transparency"]
                     if not baked else ["ElevationGrid (literal heights)", "Fog", "transparency", "bake Script output"],
        "probes": {"twin": "terrain-baked" if not baked else "terrain-script"},
    }
    return doc, info


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 5. Kinetic orrery: PROTO, interpolators, TouchSensor event chain, gradient sky
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def orrery():
    doc = Doc("Immersive", title="Kinetic orrery",
              description="A PROTO'd planet, six orbits driven by TimeSensors and OrientationInterpolators, a comet on a "
                          "PositionInterpolator ellipse, Circle2D orbit rings, and a TouchSensor on the sun that pauses "
                          "every orbit through BooleanFilter → BooleanToggle.",
              components=[("Geometry2D", 2), ("EventUtilities", 1)])
    planet = Proto("Planet", [Field("radius", "SFFloat", "initializeOnly", 0.3),
                              Field("color", "SFColor", "inputOutput", (0.8, 0.8, 0.8)),
                              Field("distance", "SFVec3f", "initializeOnly", (2, 0, 0))],
                   [N("Transform", N("Shape", N("Appearance", N("Material", IS={"diffuseColor": "color"},
                                                                     specularColor=(0.3, 0.3, 0.3), shininess=0.3)),
                                         N("Sphere", IS={"radius": "radius"})), IS={"translation": "distance"})],
                   appinfo="A sphere of a given radius and colour, offset by distance from its orbit's centre.")
    doc.add(planet,
            N("WorldInfo", title="Kinetic orrery"),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.0, 0.0, 0.02), (0.02, 0.02, 0.08), (0.08, 0.05, 0.14)], skyAngle=[1.2, 1.57],
              groundColor=[(0.0, 0.0, 0.02), (0.02, 0.01, 0.04)], groundAngle=[1.57]),
            vp("Orrery", (0, 6.5, 12.5), look_at((0, 6.5, 12.5), (0, 0, 0)), DEF="VP_Main"),
            vp("Ecliptic", (0, 0.6, 13), look_at((0, 0.6, 13), (0, 0, 0)), DEF="VP_Side"),
            N("PointLight", location=(0, 0, 0), intensity=1.6, radius=40, attenuation=(1, 0, 0)),
            N("DirectionalLight", direction=(0, -1, -0.3), intensity=0.15))
    sun = N("Transform", N("TouchSensor", DEF="SunTouch", description="Pause / resume the orrery"),
            N("Shape", N("Appearance", N("Material", diffuseColor=(1, 0.6, 0.1), emissiveColor=(1, 0.55, 0.12))),
              N("Sphere", radius=0.85)), DEF="Sun")
    doc.add(sun,
            N("BooleanFilter", DEF="OnPress"),
            N("BooleanToggle", DEF="Running", toggle=True),
            Route("SunTouch", "isActive", "OnPress", "set_boolean"),
            Route("OnPress", "inputTrue", "Running", "set_boolean"))
    specs = [(1.7, 0.18, 0.12, 4), (2.6, 0.27, 0.05, 7), (3.6, 0.3, 0.6, 11), (4.7, 0.22, 0.02, 17),
             (6.2, 0.55, 0.09, 29), (7.6, 0.45, 0.14, 43)]
    for k, (dist, rad, h, period) in enumerate(specs):
        orbit = "Orbit%d" % k
        clock = "Clock%d" % k
        turn = "Turn%d" % k
        phase = (k * 0.37) % 1.0
        keys = [0, 0.25, 0.5, 0.75, 1]
        kv = [(0, 1, 0, r6(TAU * (phase + f))) for f in keys]
        doc.add(N("Transform", planet.instance(DEF="Planet%d" % k, radius=rad, color=hue(h, 0.65, 0.95), distance=(dist, 0, 0)),
                  DEF=orbit),
                N("Transform", N("Shape", N("Appearance", N("Material", emissiveColor=(0.18, 0.2, 0.32))),
                                 N("Circle2D", radius=dist)), rotation=(1, 0, 0, -1.5708)),
                N("TimeSensor", DEF=clock, cycleInterval=period, loop=True),
                N("OrientationInterpolator", DEF=turn, key=keys, keyValue=kv),
                Route(clock, "fraction_changed", turn, "set_fraction"),
                Route(turn, "value_changed", orbit, "set_rotation"),
                Route("Running", "toggle_changed", clock, "set_enabled"))
    # Comet on an ellipse (PositionInterpolator, 33 keys)
    keys = [r6(i / 32) for i in range(33)]
    vals = []
    for f in keys:
        a = TAU * f
        vals.append((r6(9.5 * math.cos(a) - 3.5), r6(1.2 * math.sin(a)), r6(3.2 * math.sin(a))))
    doc.add(N("Transform", N("Shape", N("Appearance", N("Material", emissiveColor=(0.7, 0.9, 1))), N("Sphere", radius=0.12)),
              DEF="Comet"),
            N("TimeSensor", DEF="CometClock", cycleInterval=23, loop=True),
            N("PositionInterpolator", DEF="CometPath", key=keys, keyValue=vals),
            Route("CometClock", "fraction_changed", "CometPath", "set_fraction"),
            Route("CometPath", "value_changed", "Comet", "set_translation"),
            Route("Running", "toggle_changed", "CometClock", "set_enabled"))
    info = {
        "title": "Kinetic orrery",
        "blurb": "Six PROTO planets on TimeSensor-driven orbits, a comet on an elliptical PositionInterpolator, Circle2D "
                 "rings, and a sun you can click: TouchSensor → BooleanFilter → BooleanToggle pauses every clock.",
        "exercises": ["ProtoDeclare / ProtoInstance / IS", "TimeSensor + interpolators", "TouchSensor event chain",
                      "Circle2D", "Background gradient"],
        "probes": {"pause": {"touch": "SunTouch", "toggle": "Running", "clock": "Clock0"}},
    }
    return doc, info


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 6. Primitive garden: curved primitives, Geometry2D, quads, Extrusion, Text
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def primitives():
    doc = Doc("Full", title="Primitive garden",
              description="Sphere, Cylinder, Cone, Box, every Geometry2D node, an IndexedQuadSet, an Extrusion spiral "
                          "and a Text label: the node-coverage scene for the importer.",
              components=[("Geometry2D", 2), ("CADGeometry", 2), ("Text", 1)])
    doc.add(N("WorldInfo", title="Primitive garden"),
            N("NavigationInfo", type=["EXAMINE", "ANY"], headlight=False),
            N("Background", skyColor=[(0.93, 0.93, 0.9)]),
            N("DirectionalLight", direction=(-0.4, -0.7, -0.6), intensity=0.95),
            N("DirectionalLight", direction=(0.6, 0.2, 0.7), intensity=0.25),
            vp("Garden", (0, 1.4, 12.5), look_at((0, 1.4, 12.5), (0, 0.4, 0)), DEF="VP_Main"))

    def mat(h):
        return N("Appearance", N("Material", diffuseColor=hue(h, 0.6, 0.85), specularColor=(0.5, 0.5, 0.5), shininess=0.4))

    solids = [N("Sphere", radius=0.8), N("Cylinder", radius=0.7, height=1.6), N("Cone", bottomRadius=0.8, height=1.6),
              N("Box", size=(1.3, 1.3, 1.3))]
    for i, g in enumerate(solids):
        doc.add(N("Transform", N("Shape", mat(0.08 + i * 0.13), g), translation=((i - 1.5) * 2.4, 2.4, 0)))
    flat = [N("Arc2D", radius=0.8, startAngle=0.3, endAngle=4.2), N("ArcClose2D", radius=0.8, startAngle=0.3, endAngle=4.2, closureType="PIE"),
            N("ArcClose2D", radius=0.8, startAngle=0.3, endAngle=4.2, closureType="CHORD"), N("Circle2D", radius=0.8),
            N("Disk2D", innerRadius=0.35, outerRadius=0.8), N("Rectangle2D", size=(1.4, 1.0)),
            N("TriangleSet2D", vertices=[(-0.8, -0.6), (0.8, -0.6), (0, 0.8)]),
            N("Polyline2D", lineSegments=[(-0.8, -0.5), (-0.3, 0.6), (0.2, -0.4), (0.8, 0.5)]),
            N("Polypoint2D", point=[(r6(0.7 * math.cos(a * TAU / 12)), r6(0.7 * math.sin(a * TAU / 12))) for a in range(12)])]
    for i, g in enumerate(flat):
        unlit = g.type in ("Arc2D", "Circle2D", "Polyline2D", "Polypoint2D")
        app = N("Appearance", N("Material", emissiveColor=(0.1, 0.1, 0.12))) if unlit else mat(0.55 + i * 0.05)
        doc.add(N("Transform", N("Shape", app, g), translation=(r6((i - 4) * 1.75), 0.2, 0)))
    # IndexedQuadSet: a small stepped pyramid of quads
    qp, qi = [], []
    for k in range(4):
        s = 0.9 - k * 0.2
        y = k * 0.2
        base = len(qp)
        qp += [(-s, y, -s), (s, y, -s), (s, y, s), (-s, y, s)]
        qi += [base, base + 1, base + 2, base + 3]
    doc.add(N("Transform", N("Shape", mat(0.92), N("IndexedQuadSet", N("Coordinate", point=qp), index=qi, solid=False)),
              translation=(-3.6, -2.0, 0)))
    # Extrusion: a star cross-section swept along a spiral spine, twisting and shrinking
    star = []
    for k in range(11):
        a = TAU * (k % 10) / 10
        r = 0.35 if k % 2 == 0 else 0.16
        star.append((r6(r * math.cos(a)), r6(r * math.sin(a))))
    spine, scale, orient = [], [], []
    for k in range(25):
        f = k / 24
        a = f * TAU * 1.25
        spine.append((r6(0.9 * math.cos(a)), r6(-0.9 + 1.8 * f), r6(0.9 * math.sin(a))))
        scale.append((r6(1 - 0.7 * f), r6(1 - 0.7 * f)))
        orient.append((0, 1, 0, r6(f * 3)))
    doc.add(N("Transform", N("Shape", mat(0.33), N("Extrusion", crossSection=star, spine=spine, scale=scale,
                                                   orientation=orient, creaseAngle=0.6, solid=False)),
              translation=(0, -1.9, 0)))
    doc.add(N("Transform", N("Shape", N("Appearance", N("Material", diffuseColor=(0.15, 0.15, 0.2))),
                             N("Text", N("FontStyle", family=["SANS"], style="BOLD", justify=["MIDDLE", "MIDDLE"], size=0.8),
                               string=["X3D 4.0"])), translation=(3.6, -1.9, 0)))
    info = {
        "title": "Primitive garden",
        "blurb": "Four curved and boxy solids, every Geometry2D node (Arc2D, ArcClose2D PIE and CHORD, Circle2D, Disk2D, "
                 "Rectangle2D, TriangleSet2D, Polyline2D, Polypoint2D), an IndexedQuadSet, a star Extrusion swept on a "
                 "spiral, and Text.",
        "exercises": ["Sphere/Cylinder/Cone smoothing and tessellation", "Geometry2D", "IndexedQuadSet", "Extrusion", "Text"],
        "probes": {},
    }
    return doc, info


SCENES = {
    "material-ladder": material_ladder,
    "lines-and-points": lines_and_points,
    "shader-ribbon": shader_ribbon,
    "terrain-script": lambda: terrain(False),
    "terrain-baked": lambda: terrain(True),
    "orrery": orrery,
    "primitives": primitives,
}
