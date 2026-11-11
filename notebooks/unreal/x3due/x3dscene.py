"""Read an X3D (XML encoding) scene into a *manifest*: what is in it, where, and what Unreal cannot take.

The manifest is the ground truth the import is accounted against, and the source of cameras and lights in Unreal
(the glTF carries geometry, materials and textures). It walks the scene graph with the Transform stack, resolves
DEF/USE (a USE is a second instance of the same node), and records per shape instance its world bounding box.

    m = manifest(open("scene.x3d", encoding="utf-8").read())
    m["counts"], m["viewpoints"], m["lights"], m["traps"], m["bounds"]

What the X3D -> glTF trip loses is x3dlab.formats' job (EXPECTED_LOSS, losses); this module only feeds the Unreal
leg: cameras, lights, the backdrop, bounds for the axis check, and the traps of Unreal's light model.
Only the standard library is used (xml.etree). Triangle counts of primitives (Box, Sphere, ...) depend on the
tessellation an importer picks; they are marked approximate.
"""

from __future__ import annotations

import collections
import math
import re
import xml.etree.ElementTree as ET

from . import convert as C

GEOMETRY = {"Box", "Sphere", "Cone", "Cylinder", "IndexedFaceSet", "IndexedTriangleSet", "TriangleSet",
            "IndexedTriangleStripSet", "IndexedTriangleFanSet", "TriangleStripSet", "TriangleFanSet", "IndexedQuadSet",
            "QuadSet", "ElevationGrid", "Extrusion", "Text", "PointSet", "IndexedLineSet", "LineSet", "Disk2D",
            "Rectangle2D", "Circle2D", "Arc2D", "ArcClose2D", "Polyline2D", "Polypoint2D", "TriangleSet2D",
            "NurbsPatchSurface", "NurbsTrimmedSurface", "NurbsCurve", "NurbsSweptSurface", "NurbsSwungSurface",
            "GeoElevationGrid", "GaussianSplats", "InlineGeometry"}
POINTS_LINES = {"PointSet", "IndexedLineSet", "LineSet", "Polyline2D", "Polypoint2D", "Circle2D", "Arc2D", "NurbsCurve"}
MATERIALS = {"Material", "PhysicalMaterial", "UnlitMaterial", "TwoSidedMaterial"}
TEXTURES = {"ImageTexture", "PixelTexture", "MovieTexture", "ImageTexture3D", "PixelTexture3D", "ComposedTexture3D",
            "MultiTexture", "ComposedCubeMapTexture", "ImageCubeMapTexture", "GeneratedCubeMapTexture", "RenderedTexture"}
LIGHTS = {"DirectionalLight", "PointLight", "SpotLight", "EnvironmentLight"}
VIEWPOINTS = {"Viewpoint", "OrthoViewpoint", "GeoViewpoint"}
GROUPING = {"Transform", "Group", "StaticGroup", "Switch", "LOD", "Billboard", "Collision", "Anchor", "CADAssembly",
            "CADLayer", "CADPart", "CADFace", "ScreenGroup", "LayoutGroup", "GeoTransform", "GeoLocation", "HAnimHumanoid",
            "HAnimJoint", "HAnimSegment", "HAnimSite", "Layer", "LayerSet", "LayoutLayer", "Scene"}

_num = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def floats(s, default=None):
    if s is None:
        return default
    return [float(x) for x in _num.findall(s)]


def ints(s):
    return [int(float(x)) for x in _num.findall(s or "")]


def mfstring(s):
    """X3D MFString: '"a" "b"' -> ['a', 'b'] (a bare single value is accepted too)."""
    if s is None:
        return []
    got = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
    return got if got else [s.strip()] if s.strip() else []


def _tag(el):
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def _bool(s, default):
    if s is None:
        return default
    return s.strip().lower() == "true"


# ---------------------------------------------------------------------- geometry: triangles and local bounds
def _faces_from_index(idx):
    faces, cur = [], 0
    for i in idx:
        if i < 0:
            if cur:
                faces.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        faces.append(cur)
    return faces


def triangles(el, coord_count=None):
    """(triangle count, approximate?) for a geometry element."""
    t = _tag(el)
    a = el.attrib
    if t == "Box":
        return 12, False
    if t == "Sphere":
        return 32 * 16 * 2, True                         # importer tessellation; Blender's add-on default is not fixed here
    if t in ("Cone", "Cylinder"):
        return 32 * 4, True
    if t == "IndexedFaceSet":
        return sum(max(0, n - 2) for n in _faces_from_index(ints(a.get("coordIndex")))), False
    if t == "IndexedTriangleSet":
        return len(ints(a.get("index"))) // 3, False
    if t == "TriangleSet":
        return (coord_count or 0) // 3, coord_count is None
    if t in ("IndexedTriangleFanSet", "IndexedTriangleStripSet"):
        return sum(max(0, n - 2) for n in _faces_from_index(ints(a.get("index")))), False
    if t in ("TriangleFanSet", "TriangleStripSet"):
        key = "fanCount" if t == "TriangleFanSet" else "stripCount"
        return sum(max(0, n - 2) for n in ints(a.get(key))), False
    if t == "IndexedQuadSet":
        return len(ints(a.get("index"))) // 4 * 2, False
    if t == "QuadSet":
        return (coord_count or 0) // 4 * 2, coord_count is None
    if t == "ElevationGrid":
        x, z = int(a.get("xDimension", 0)), int(a.get("zDimension", 0))
        return max(0, x - 1) * max(0, z - 1) * 2, False
    if t == "Extrusion":
        cs = len(floats(a.get("crossSection"), [1, 1, 1, -1, -1, -1, -1, 1, 1, 1])) // 2
        sp = len(floats(a.get("spine"), [0, 0, 0, 0, 1, 0])) // 3
        return max(0, cs - 1) * max(0, sp - 1) * 2 + (2 * max(0, cs - 2)), True
    if t in ("Rectangle2D", "TriangleSet2D"):
        return (2 if t == "Rectangle2D" else len(floats(a.get("vertices"), [])) // 6), False
    if t in ("Disk2D", "ArcClose2D"):
        return 32, True
    return 0, False


def _coord_points(el, defs):
    """The point list of the Coordinate child (resolving USE), or None."""
    for ch in el:
        tag = _tag(ch)
        if tag in ("Coordinate", "CoordinateDouble", "GeoCoordinate"):
            if ch.get("USE") and ch.get("USE") in defs:
                ch = defs[ch.get("USE")]
            p = floats(ch.get("point"), [])
            return [tuple(p[i:i + 3]) for i in range(0, len(p) - 2, 3)]
    return None


def local_bounds(el, defs):
    """Axis-aligned (min, max) of a geometry in its local frame, or None if it has no extent we can tell."""
    t, a = _tag(el), el.attrib
    if t == "Box":
        s = floats(a.get("size"), [2, 2, 2])
        return tuple(-x / 2 for x in s), tuple(x / 2 for x in s)
    if t == "Sphere":
        r = floats(a.get("radius"), [1])[0]
        return (-r, -r, -r), (r, r, r)
    if t == "Cone":
        r, h = floats(a.get("bottomRadius"), [1])[0], floats(a.get("height"), [2])[0]
        return (-r, -h / 2, -r), (r, h / 2, r)
    if t == "Cylinder":
        r, h = floats(a.get("radius"), [1])[0], floats(a.get("height"), [2])[0]
        return (-r, -h / 2, -r), (r, h / 2, r)
    if t == "ElevationGrid":
        x, z = int(a.get("xDimension", 0)), int(a.get("zDimension", 0))
        dx, dz = floats(a.get("xSpacing"), [1])[0], floats(a.get("zSpacing"), [1])[0]
        hs = floats(a.get("height"), [0]) or [0]
        return (0, min(hs), 0), (max(0, x - 1) * dx, max(hs), max(0, z - 1) * dz)
    if t == "Rectangle2D":
        s = floats(a.get("size"), [2, 2])
        return (-s[0] / 2, -s[1] / 2, 0), (s[0] / 2, s[1] / 2, 0)
    if t == "Disk2D":
        r = floats(a.get("outerRadius"), [1])[0]
        return (-r, -r, 0), (r, r, 0)
    pts = _coord_points(el, defs)
    if pts:
        lo = tuple(min(p[i] for p in pts) for i in range(3))
        hi = tuple(max(p[i] for p in pts) for i in range(3))
        return lo, hi
    return None


def transform_bounds(m, b):
    lo, hi = b
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    w = [C.mat4_point(m, c) for c in corners]
    return tuple(min(p[i] for p in w) for i in range(3)), tuple(max(p[i] for p in w) for i in range(3))


# ---------------------------------------------------------------------- the walk
class _Walker:
    def __init__(self, root):
        self.root = root
        self.defs = {}
        for el in root.iter():
            d = el.get("DEF")
            if d:
                self.defs[d] = el
        self.census = collections.Counter()           # every element in the file, once
        for el in root.iter():
            self.census[_tag(el)] += 1
        self.instances = collections.Counter()        # walked (USE counts again)
        self.shapes, self.lights, self.viewpoints, self.backgrounds, self.navinfo = [], [], [], [], []
        self.geometry_users = collections.Counter()   # id(geometry element) -> shape instances using it
        self.geometry_elems = {}
        self.material_elems, self.texture_elems = {}, {}
        self.texture_urls = []
        self.hidden_switch_children = 0
        self.use_count = sum(1 for el in root.iter() if el.get("USE"))
        self.notes = []

    def resolve(self, el):
        u = el.get("USE")
        if u:
            if u in self.defs:
                return self.defs[u], True
            self.notes.append("USE %r has no DEF" % u)
            return None, True
        return el, False

    def walk(self, el, m, depth=0):
        if depth > 200:
            self.notes.append("graph deeper than 200 levels: truncated")
            return
        el, used = self.resolve(el)
        if el is None:
            return
        t = _tag(el)
        self.instances[t] += 1
        a = el.attrib
        if t == "Transform":
            m = C.mat4_mul(m, C.x3d_transform(floats(a.get("translation"), [0, 0, 0]),
                                              floats(a.get("rotation"), [0, 0, 1, 0]),
                                              floats(a.get("scale"), [1, 1, 1]),
                                              floats(a.get("scaleOrientation"), [0, 0, 1, 0]),
                                              floats(a.get("center"), [0, 0, 0])))
        if t == "Shape":
            self.shape(el, m, used)
            return
        if t in LIGHTS:
            self.light(el, t, m)
        elif t in VIEWPOINTS:
            self.viewpoint(el, t, m)
        elif t in ("Background", "TextureBackground"):
            self.backgrounds.append({"type": t, "skyColor": floats(a.get("skyColor"), [0, 0, 0]),
                                     "skyAngle": floats(a.get("skyAngle"), []), "groundColor": floats(a.get("groundColor"), []),
                                     "groundAngle": floats(a.get("groundAngle"), []),
                                     "urls": {k: mfstring(a.get(k)) for k in ("frontUrl", "backUrl", "leftUrl", "rightUrl",
                                                                              "topUrl", "bottomUrl") if a.get(k)},
                                     "transparency": floats(a.get("transparency"), [0])[0]})
        elif t == "NavigationInfo":
            self.navinfo.append({"headlight": _bool(a.get("headlight"), True), "type": mfstring(a.get("type"))})
        kids = list(el)
        if t == "Switch":
            choice = int(floats(a.get("whichChoice"), [-1])[0])
            vis = [k for k in kids if _tag(k) not in ("IS", "metadata") and not _tag(k).startswith("Metadata")]
            self.hidden_switch_children += max(0, len(vis) - (1 if 0 <= choice < len(vis) else 0))
            kids = [vis[choice]] if 0 <= choice < len(vis) else []
        elif t == "LOD" and kids:
            kids = [k for k in kids if not _tag(k).startswith("Metadata")][:1]    # the finest level
        for k in kids:
            if _tag(k) in ("ROUTE", "IS", "field", "fieldValue", "connect"):
                continue
            self.walk(k, m, depth + 1)

    def shape(self, el, m, used):
        geom, app = None, None
        for ch in el:
            tag = _tag(ch)
            r, _ = self.resolve(ch)
            if r is None:
                continue
            rt = _tag(r)
            if rt in GEOMETRY:
                geom = r
            elif rt == "Appearance":
                app = r
        mats, texs = [], []
        if app is not None:
            for ch in app.iter():
                r, _ = self.resolve(ch)
                if r is None:
                    continue
                rt = _tag(r)
                if rt in MATERIALS:
                    self.material_elems[id(r)] = rt
                    mats.append(rt)
                elif rt in TEXTURES:
                    if id(r) not in self.texture_elems:
                        self.texture_elems[id(r)] = rt
                        self.texture_urls += mfstring(r.get("url"))
                    texs.append(rt)
        rec = {"geometry": _tag(geom) if geom is not None else None, "materials": mats, "textures": texs, "triangles": 0,
               "approx": False, "bounds": None, "use": used}
        if geom is not None:
            self.geometry_users[id(geom)] += 1
            self.geometry_elems[id(geom)] = geom
            pts = _coord_points(geom, self.defs)
            tri, approx = triangles(geom, len(pts) if pts is not None else None)
            rec.update(triangles=tri, approx=approx)
            lb = local_bounds(geom, self.defs)
            if lb is not None:
                rec["bounds"] = transform_bounds(m, lb)
        self.shapes.append(rec)

    def light(self, el, t, m):
        a = el.attrib
        d = {"type": t, "DEF": a.get("DEF"), "on": _bool(a.get("on"), True), "intensity": floats(a.get("intensity"), [1])[0],
             "ambientIntensity": floats(a.get("ambientIntensity"), [0])[0], "color": floats(a.get("color"), [1, 1, 1]),
             "global": _bool(a.get("global"), t != "DirectionalLight"), "shadows": _bool(a.get("shadows"), False)}
        if t in ("DirectionalLight", "SpotLight"):
            d["direction"] = C.norm(C.mat4_dir(m, floats(a.get("direction"), [0, 0, -1])))
        if t in ("PointLight", "SpotLight"):
            d["location"] = C.mat4_point(m, floats(a.get("location"), [0, 0, 0]))
            d["attenuation"] = floats(a.get("attenuation"), [1, 0, 0])
            sc = sum(math.sqrt(sum(m[r][c] ** 2 for r in range(3))) for c in range(3)) / 3 or 1.0   # mean axis scale
            d["radius"] = floats(a.get("radius"), [100])[0] * sc
        if t == "SpotLight":
            d["beamWidth"] = floats(a.get("beamWidth"), [math.pi / 4])[0]
            d["cutOffAngle"] = floats(a.get("cutOffAngle"), [math.pi / 2])[0]
        self.lights.append(d)

    def viewpoint(self, el, t, m):
        a = el.attrib
        fov = floats(a.get("fieldOfView"), [math.pi / 4] if t != "OrthoViewpoint" else [-1, -1, 1, 1])
        self.viewpoints.append({"type": t, "DEF": a.get("DEF"), "description": a.get("description", ""),
                                "position": floats(a.get("position"), [0, 0, 10]),
                                "orientation": floats(a.get("orientation"), [0, 0, 1, 0]),
                                "fieldOfView": fov[0] if t != "OrthoViewpoint" else fov,
                                "centerOfRotation": floats(a.get("centerOfRotation"), [0, 0, 0]),
                                "world_matrix": m, "index": len(self.viewpoints)})


def parse(text):
    """ElementTree root of an X3D XML document (DOCTYPE and namespaces tolerated)."""
    text = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?>", "", text, count=1)
    return ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)


def manifest(text):
    root = parse(text)
    if _tag(root) != "X3D":
        raise ValueError("not an X3D XML document (root element is %s)" % _tag(root))
    scene = next((el for el in root if _tag(el) == "Scene"), None)
    if scene is None:
        raise ValueError("X3D document has no Scene")
    w = _Walker(root)
    w.walk(scene, C.mat4_identity())
    head = next((el for el in root if _tag(el) == "head"), None)
    meta = {}
    if head is not None:
        for el in head:
            if _tag(el) == "meta" and el.get("name"):
                meta[el.get("name")] = el.get("content", "")
    comps = [(el.get("name"), el.get("level")) for el in (head if head is not None else []) if _tag(el) == "component"]
    tri_total = sum(s["triangles"] for s in w.shapes)
    unique_geom = len(w.geometry_users)
    shared = {k: v for k, v in w.geometry_users.items() if v > 1}
    bounds = [s["bounds"] for s in w.shapes if s["bounds"]]
    scene_bounds = None
    if bounds:
        scene_bounds = (tuple(min(b[0][i] for b in bounds) for i in range(3)), tuple(max(b[1][i] for b in bounds) for i in range(3)))
    traps = []     # Unreal-leg fidelity traps (what glTF loses on the way is x3dlab.formats' job, not this module's)
    scoped = [L for L in w.lights if L["type"] == "DirectionalLight" and not L["global"]]
    if scoped:
        traps.append({"family": "light scoping", "nodes": {"DirectionalLight global=false": len(scoped)}, "count": len(scoped),
                     "note": "X3D lights only their siblings; Unreal lights are global, so these light more than in X3D"})
    amb = sum(L["ambientIntensity"] for L in w.lights if L["on"])
    if amb > 0:
        traps.append({"family": "ambient light", "nodes": {"ambientIntensity > 0": sum(1 for L in w.lights if L["ambientIntensity"] > 0)},
                     "count": 1, "note": "X3D ambientIntensity has no Unreal equivalent; not mapped (sum %.3g)" % amb})
    headlight = w.navinfo[0]["headlight"] if w.navinfo else True
    counts = {
        "nodes": sum(w.census.values()), "DEF": len(w.defs), "USE": w.use_count,
        "shape_instances": len(w.shapes), "shapes_in_file": w.census.get("Shape", 0),
        "geometries_unique": unique_geom, "geometries_shared": len(shared),
        "geometry_instances_of_shared": sum(shared.values()),
        "triangles": tri_total, "triangles_approx": any(s["approx"] for s in w.shapes),
        "materials": len(w.material_elems), "materials_by_type": dict(collections.Counter(w.material_elems.values())),
        "textures": len(w.texture_elems), "textures_by_type": dict(collections.Counter(w.texture_elems.values())),
        "lights": len(w.lights), "lights_by_type": dict(collections.Counter(L["type"] for L in w.lights)),
        "viewpoints": len(w.viewpoints), "backgrounds": len(w.backgrounds), "hidden_switch_children": w.hidden_switch_children,
        "points_lines_shapes": sum(1 for s in w.shapes if s["geometry"] in POINTS_LINES),
    }
    return {"version": root.get("version"), "profile": root.get("profile"), "components": comps, "meta": meta,
            "census": dict(w.census), "counts": counts, "viewpoints": w.viewpoints, "lights": w.lights,
            "backgrounds": w.backgrounds, "headlight": headlight, "texture_urls": sorted(set(w.texture_urls)),
            "bounds": scene_bounds, "shape_centres": [tuple((b[0][i] + b[1][i]) / 2 for i in range(3)) for b in bounds],
            "traps": traps, "node_types": sorted(t for t in w.census if t[:1].isupper()), "notes": w.notes}


def ensure_viewpoint_defs(text, prefix="x3dlab_vp_"):
    """Give every Viewpoint without a DEF a stable one (x3dlab_vp_<n>), so all three engines can bind it by name.
    Returns (new_text, [names in document order])."""
    names, n = [], [0]

    def fix(mo):
        tag = mo.group(0)
        i = n[0]
        n[0] += 1
        d = re.search(r'\bDEF\s*=\s*["\']([^"\']+)["\']', tag)
        if d:
            names.append(d.group(1))
            return tag
        if re.search(r'\bUSE\s*=', tag):
            names.append(None)
            return tag
        name = "%s%d" % (prefix, i)
        names.append(name)
        return tag.replace(mo.group(1), '%s DEF="%s"' % (mo.group(1), name), 1)

    out = re.sub(r"(<(?:Viewpoint|OrthoViewpoint))\b[^>]*>", fix, text)
    return out, [x for x in names if x]


def strip_behaviour(text):
    """Remove ROUTEs (so TimeSensors and interpolators no longer drive anything): every engine then shows the authored,
    time-0 state, which is what Blender and Unreal import. Used for the X_ITE comparison render."""
    return re.sub(r"<ROUTE\b[^>]*/>", "", text)


def viewpoint_name(vp):
    return vp.get("DEF") or "viewpoint_%d" % vp["index"]
