"""Fidelity predictor: what each leg of the chain keeps of a scene, read from the scene's own nodes.

Legs: ``xite`` (X_ITE in a browser, the reference), ``blender`` (x3d-blender-io importer, main),
``gltf`` (Blender's glTF export of that import), ``unreal`` (Interchange + x3due line tubes).

Each rule is a written-down claim from the 12 Nov 2026 fidelity-chain finding (or general X3D/glTF fact),
tagged with the measurement that can confirm or refute it. The Blender leg (blender_leg.py) and the MCP
mission measure; the report puts prediction and measurement side by side, the way BLACKBOX scores its
agent against an answer key.

Status vocabulary: kept · approx (kept with a documented approximation) · partial (measured only) · lost · n/a
"""
from __future__ import annotations

LEGS = ("xite", "blender", "gltf", "unreal")

LINE_NODES = ("IndexedLineSet", "LineSet", "PointSet", "Polyline2D", "Polypoint2D", "Arc2D", "Circle2D")
SHADER_NODES = ("ComposedShader", "ProgramShader", "PackagedShader")
SENSOR_NODES = ("TouchSensor", "PlaneSensor", "CylinderSensor", "SphereSensor", "ProximitySensor",
                "VisibilitySensor", "KeySensor", "StringSensor", "TransformSensor")
EVENT_UTIL = ("BooleanFilter", "BooleanToggle", "BooleanTrigger", "BooleanSequencer", "IntegerSequencer",
              "IntegerTrigger", "TimeTrigger")
INTERP = ("PositionInterpolator", "OrientationInterpolator", "ScalarInterpolator", "ColorInterpolator",
          "CoordinateInterpolator", "NormalInterpolator")
CURVED = ("Sphere", "Cylinder", "Cone")
GEOM2D = ("Arc2D", "ArcClose2D", "Circle2D", "Disk2D", "Polyline2D", "Polypoint2D", "Rectangle2D", "TriangleSet2D")


def R(id_, title, why, legs, measure=None, ref="finding-2026-11-12-x3d-fidelity-chain"):
    return {"id": id_, "title": title, "why": why, "legs": legs, "measure": measure, "ref": ref}


def predict(stats: dict) -> list[dict]:
    """``stats`` is validate_text(...)['stats']. Returns the rules that fire for this scene."""
    by = stats.get("byType", {})
    facts = stats.get("facts", {})
    has = lambda *names: sum(by.get(n, 0) for n in names)  # noqa: E731
    out = []

    if has(*SHADER_NODES):
        out.append(R("shaders", "GLSL shaders (%d)" % has(*SHADER_NODES),
                     "Only X_ITE runs ComposedShader. Every other leg draws the Appearance's fallback Material, "
                     "so the scene's look is X_ITE-only unless the shaders are hand-translated (the Gobbler).",
                     {"xite": ("kept", "runs the GLSL"), "blender": ("lost", "fallback Material only"),
                      "gltf": ("lost", "no shader in glTF; fallback Material"),
                      "unreal": ("lost", "hand-translate to Unreal materials")},
                     measure="glb.materials == fallback colours; no shader extension"))
    if has("Script"):
        out.append(R("scripts", "Scripts (%d)" % has("Script"),
                     "Scripts run only in an X3D browser. Importers read the file as written: whatever a Script "
                     "computes at initialize() never reaches Blender, glTF or Unreal.",
                     {"xite": ("kept", "ecmascript runs"), "blender": ("lost", "not executed"),
                      "gltf": ("lost", "not executed"), "unreal": ("lost", "not executed")}))
    under = [g for g in facts.get("elevationGrids", []) if g["given"] < g["expected"]]
    if under:
        g = under[0]
        out.append(R("elevation-underfilled", "ElevationGrid %s: %d of %d heights in the file" % (g["DEF"] or "", g["given"], g["expected"]),
                     "The heights arrive later (a Script). X_ITE draws missing heights as 0, then the Script fills "
                     "them. blender_IO ≥ fidelity-v2 pads with zeros and warns, so the import is a flat plane.",
                     {"xite": ("kept", "Script grows it"), "blender": ("lost", "flat plane (zeros), with a warning"),
                      "gltf": ("lost", "flat plane"), "unreal": ("lost", "flat plane")},
                     measure="blender.mesh['Terrain'].height_range ≈ 0"))
    if facts.get("scriptsDriveGeometry") and not under:
        out.append(R("script-geometry", "Script writes geometry",
                     "Geometry set by a Script after load is invisible to importers.",
                     {"xite": ("kept", ""), "blender": ("lost", ""), "gltf": ("lost", ""), "unreal": ("lost", "")}))
    n_lines = has(*LINE_NODES)
    if n_lines:
        out.append(R("lines-points", "Lines and points (%d sets)" % n_lines,
                     "X3D draws lines and points unlit, in the Color node's colours, else emissiveColor (black "
                     "when unset). blender_IO ≥ v2 imports them unlit; glTF carries them as LINES/POINTS with "
                     "KHR_materials_unlit and COLOR_0; Unreal Interchange drops non-triangle primitives, so "
                     "x3due/lines.py rebuilds them as tubes and small spheres.",
                     {"xite": ("kept", "native lines/points"), "blender": ("kept", "unlit, colour attribute"),
                      "gltf": ("kept", "LINES/POINTS + KHR_materials_unlit + COLOR_0"),
                      "unreal": ("approx", "tubes / spheres (x3due/lines.py)")},
                     measure="glb primitives with mode 0/1/3 > 0; COLOR_0 present"))
    if facts.get("lineProperties") or facts.get("pointProperties"):
        out.append(R("line-properties", "LineProperties / PointProperties",
                     "Line width and point size scale factors have no glTF field; every line gets the pipeline's "
                     "fixed LINE_PX downstream.",
                     {"xite": ("kept", "width / size honoured"), "blender": ("lost", "no width"),
                      "gltf": ("lost", "no line width in glTF"), "unreal": ("approx", "fixed LINE_PX tube radius")}))
    if facts.get("colourMidtones"):
        out.append(R("colour-space", "Mid-tone Material colours (%d materials)" % facts["colourMidtones"],
                     "X_ITE's default colour space (LINEAR_WHEN_PHYSICAL_MATERIAL) treats Material and UnlitMaterial "
                     "colours as sRGB. blender_IO passes them through, and Blender/glTF/Unreal read them as linear: "
                     "an emissiveColor of 0.5 shows as 128/255 in X_ITE and about 188/255 downstream. The open "
                     "colour-space decision; the material-ladder probe measures it.",
                     {"xite": ("kept", "sRGB reading (reference)"), "blender": ("approx", "read as linear: brighter mid-tones"),
                      "gltf": ("approx", "read as linear"), "unreal": ("approx", "read as linear")},
                     measure="colour-space probe: X_ITE pixels vs glb-in-X_ITE pixels"))
    if facts.get("specular"):
        out.append(R("blinn-phong", "Blinn-Phong specular (%d materials)" % facts["specular"],
                     "X3D Material is Blinn-Phong: specularColor × (N·H)^(128·shininess), no Fresnel. blender_IO "
                     "maps it to GGX roughness, specular IOR level and tint (phong_to_principled). A close but not "
                     "exact highlight.",
                     {"xite": ("kept", "Blinn-Phong"), "blender": ("approx", "GGX via phong_to_principled"),
                      "gltf": ("approx", "roughness + KHR_materials_specular"),
                      "unreal": ("approx", "Substrate / GGX from glTF")},
                     measure="glb uses KHR_materials_specular"))
    if has(*CURVED):
        out.append(R("tessellation", "Curved primitives (%d)" % has(*CURVED),
                     "X3D leaves Sphere/Cylinder/Cone tessellation to the browser. Importers bake a mesh: "
                     "circle_divisions (default 16; BLENDER_X3D_CIRCLE_DIV) sets the facet count; ≥ v2 smooths them.",
                     {"xite": ("kept", "browser tessellation"), "blender": ("approx", "baked mesh, circle_divisions"),
                      "gltf": ("approx", "baked mesh"), "unreal": ("approx", "baked mesh")},
                     measure="blender sphere face count / smooth shading"))
    if has(*GEOM2D):
        out.append(R("geometry2d", "Geometry2D (%d)" % has(*GEOM2D),
                     "blender_IO ≥ v2 imports every Geometry2D node; open ones (Arc2D, Circle2D, Polyline2D, "
                     "Polypoint2D) become lines or points and follow the lines-and-points rule.",
                     {"xite": ("kept", ""), "blender": ("kept", "≥ fidelity-v2"), "gltf": ("kept", "as mesh / lines"),
                      "unreal": ("approx", "lines as tubes")}))
    if facts.get("gradientBackground"):
        out.append(R("background-gradient", "Background sky/ground gradient",
                     "glTF has no background. blender_IO keeps the gradient as the scene property x3d_background "
                     "(for re-export) but leaves the world colour alone; Unreal's backdrop is one colour.",
                     {"xite": ("kept", "gradient sky"), "blender": ("approx", "x3d_background property only"),
                      "gltf": ("lost", "no backgrounds in glTF"), "unreal": ("lost", "single colour backdrop")}))
    if has("Fog"):
        out.append(R("fog", "Fog",
                     "glTF has no fog. Blender and Unreal need a volume or post-process set up by hand.",
                     {"xite": ("kept", ""), "blender": ("lost", "not imported"), "gltf": ("lost", "no fog in glTF"),
                      "unreal": ("lost", "add Exponential Height Fog by hand")}))
    if has("TimeSensor") and has(*INTERP):
        out.append(R("animation", "TimeSensor + interpolator animation (%d interpolators)" % has(*INTERP),
                     "blender_IO turns TimeSensor → Position/Orientation/Scalar interpolator ROUTEs into actions; "
                     "glTF exports them as animations. Routes into anything but a Transform are not translated.",
                     {"xite": ("kept", "ROUTE graph"), "blender": ("approx", "Transform targets only"),
                      "gltf": ("approx", "baked animations"), "unreal": ("approx", "glTF animations")},
                     measure="blender actions > 0; glb animations > 0"))
    if has(*SENSOR_NODES) or has(*EVENT_UTIL):
        out.append(R("interaction", "Sensors and event utilities (%d)" % (has(*SENSOR_NODES) + has(*EVENT_UTIL)),
                     "Pointer sensors and event logic are X3D behaviour; no file format downstream carries them.",
                     {"xite": ("kept", "clickable"), "blender": ("lost", ""), "gltf": ("lost", ""),
                      "unreal": ("lost", "rebuild in Blueprints")}))
    if stats.get("protos"):
        out.append(R("protos", "PROTOs (%d declared, %d instances)" % (len(stats["protos"]), stats.get("protoInstances", 0)),
                     "blender_IO expands PROTO instances while parsing, so geometry arrives; the PROTO itself (a "
                     "reusable type) does not survive.",
                     {"xite": ("kept", ""), "blender": ("approx", "expanded instances"), "gltf": ("approx", "expanded"),
                      "unreal": ("approx", "expanded")},
                     measure="blender object count includes every instance"))
    if facts.get("twoSidedBack"):
        out.append(R("two-sided", "TwoSidedMaterial with separateBackColor",
                     "blender_IO ≥ v2 reads the front material and warns; the back colour is lost.",
                     {"xite": ("kept", "front + back"), "blender": ("approx", "front only"), "gltf": ("approx", "front, doubleSided"),
                      "unreal": ("approx", "front, two-sided")}))
    if has("Text"):
        out.append(R("text", "Text (%d)" % has("Text"),
                     "blender_IO makes a Blender font curve. Fonts differ from X_ITE's; glTF needs it converted to a mesh.",
                     {"xite": ("kept", "X_ITE fonts"), "blender": ("approx", "font curve, Blender font"),
                      "gltf": ("approx", "only if converted to mesh"), "unreal": ("approx", "")},
                     measure="glb mesh named Text*"))
    if has("Extrusion"):
        out.append(R("extrusion", "Extrusion (%d)" % has("Extrusion"),
                     "Imported as a mesh with the spine, scale and orientation baked.",
                     {"xite": ("kept", ""), "blender": ("kept", "baked mesh"), "gltf": ("kept", ""), "unreal": ("kept", "")}))
    if has("PhysicalMaterial"):
        out.append(R("pbr", "PhysicalMaterial (%d)" % has("PhysicalMaterial"),
                     "Metallic-roughness PBR maps one-to-one onto Principled BSDF and glTF.",
                     {"xite": ("kept", ""), "blender": ("kept", "Principled BSDF"), "gltf": ("kept", "pbrMetallicRoughness"),
                      "unreal": ("kept", "")}))
    if facts.get("transparency"):
        out.append(R("transparency", "Material transparency (%d)" % facts["transparency"],
                     "Becomes alpha blending everywhere; sort order and shadows differ by engine.",
                     {"xite": ("kept", ""), "blender": ("approx", "Blended"), "gltf": ("approx", "alphaMode BLEND"),
                      "unreal": ("approx", "translucent")}))
    lights = has("PointLight", "DirectionalLight", "SpotLight")
    if lights:
        out.append(R("lights", "Lights (%d)" % lights,
                     "X3D intensities have no physical unit. glTF's KHR_lights_punctual uses candela/lux, and which "
                     "conversion the Foundry exports with (SPEC, COMPAT or RAW) is still open.",
                     {"xite": ("kept", ""), "blender": ("approx", "watts from intensity"),
                      "gltf": ("approx", "KHR_lights_punctual units"), "unreal": ("approx", "")}))
    return out


def summarise(rules: list[dict]) -> dict:
    """Per leg: how many rules end kept / approx / lost."""
    out = {}
    for leg in LEGS:
        c = {"kept": 0, "approx": 0, "partial": 0, "lost": 0}
        for r in rules:
            s = r["legs"].get(leg, ("n/a", ""))[0]
            if s in c:
                c[s] += 1
        out[leg] = c
    return out
