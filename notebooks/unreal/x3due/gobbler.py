"""The Gobbler's shader-encoded geometry, decoded for engines that cannot run its GLSL (Unreal, glTF viewers).

The Gobbler (x3d.demo, gobbler3-webgpu) draws almost everything with ComposedShaders, and its geometry only makes
sense to those shaders:

* LineShader ribbons are IndexedFaceSets whose quads have zero area: every corner sits on an end of its edge
  (Coordinate), carries the other end in a TextureCoordinate3D, a 4-bit code in the angle of its Normal
  (bit 1 side, bit 2 which end, bits 4/8 head-lattice flags) and, in the alpha of a per-face ColorRGBA, the ribbon
  width or, at 0.7 and up, the flywheel loop position and span. The vertex shader widens each edge on screen.
* PointShader PointSets keep kind and size in the colour alpha: kind = floor(a * 4), size = (a - kind / 4) * 16.
* FacetShader faces take per-face ColorRGBA as colour and as the seed of their per-facet randomness.
* SkyShader, EyeShader: procedural colour from the object-space position.

Glossary: phase = the assembly animation (1 = the bird whole, the frozen state used here), time = shader clock.

`decode()` turns the scene into plain arrays (segments, points, triangles) at a frozen time, with the shaders' own
hash (pcg3, bit-exact in uint32) so per-facet and per-point "random" values match X_ITE. `preview()` renders them on
the CPU the way the GLSL does (screen-space ribbons and sprites), to check the decoding against X_ITE stills.
`write_glb()` turns them into geometry any engine can draw: tubes for ribbons, spheres for points, faces for facets.
"""
from __future__ import annotations

import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

FROZEN = {"phase": 1.0, "time": 0.0, "glow": 1.0, "scan": -9.0, "bloom": 0.0,
          "light0": (0.0, 2.5, 8.2), "light1": (7.69, -5.04, 6.56), "light2": (12.88, 1.5, 4.3), "light3": (-1.09, 4.48, 0.03)}
LIGHT_COLOURS = np.array([(1, 0.8, 0.42), (1, 0.36, 0.09), (0.12, 0.78, 1), (0.66, 0.28, 1)], float)


# ---------------------------------------------------------------------- the shaders' hash, bit-exact
def pcg3(v: np.ndarray) -> np.ndarray:
    """uvec3 pcg3(uvec3 v) from the Gobbler's GLSL, on uint32 arrays of shape (..., 3)."""
    v = (v.astype(np.uint64) * 1664525 + 1013904223) & 0xFFFFFFFF
    x, y, z = v[..., 0].copy(), v[..., 1].copy(), v[..., 2].copy()
    m = 0xFFFFFFFF
    x = (x + y * z) & m
    y = (y + z * x) & m
    z = (z + x * y) & m
    x ^= x >> 16
    y ^= y >> 16
    z ^= z >> 16
    x = (x + y * z) & m
    y = (y + z * x) & m
    z = (z + x * y) & m
    return np.stack([x, y, z], -1).astype(np.uint64)


def rnd3i(q: np.ndarray) -> np.ndarray:
    """vec3 rnd3i(ivec3 q) = vec3(pcg3(uvec3(q + 4096))) / 2^32 (negative ints keep their bits, as GLSL does)."""
    u = (np.asarray(q, np.int64) + 4096) & 0xFFFFFFFF
    return pcg3(u).astype(np.float64) * (1.0 / 4294967296.0)


def qpos(p: np.ndarray) -> np.ndarray:
    return np.floor(np.asarray(p, float) * 100.0 + 0.5).astype(np.int64)


def pool_at(p: np.ndarray, uni=FROZEN) -> np.ndarray:
    """vec3 poolAt(vec3 p): the four moving lights' colour washes."""
    p = np.asarray(p, float)
    out = np.zeros(p.shape[:-1] + (3,))
    for k in range(4):
        d = p - np.asarray(uni["light%d" % k], float)
        out += LIGHT_COLOURS[k] * np.exp(-(d * d).sum(-1) * 0.05)[..., None]
    return out * 0.9


# ---------------------------------------------------------------------- X3D
def floats(s, n=None):
    a = np.array([float(x) for x in (s or "").replace(",", " ").split()], float)
    return a.reshape(-1, n) if n else a


def ints(s):
    return [int(x) for x in (s or "").replace(",", " ").split()]


def rotation_matrix(axis_angle) -> np.ndarray:
    x, y, z, a = (list(axis_angle) + [0, 0, 1, 0])[:4]
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / n, y / n, z / n
    c, s, t = math.cos(a), math.sin(a), 1 - math.cos(a)
    R = np.eye(4)
    R[:3, :3] = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                 [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                 [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    return R


def transform_matrix(el) -> np.ndarray:
    """X3D Transform: T * C * R * SR * S * -SR * -C."""
    def tr(v):
        M = np.eye(4)
        M[:3, 3] = v
        return M
    t = floats(el.get("translation") or "0 0 0")
    c = floats(el.get("center") or "0 0 0")
    r = floats(el.get("rotation") or "0 0 1 0")
    s = floats(el.get("scale") or "1 1 1")
    so = floats(el.get("scaleOrientation") or "0 0 1 0")
    S = np.diag(list(s) + [1.0])
    SR = rotation_matrix(so)
    return tr(t) @ tr(c) @ rotation_matrix(r) @ SR @ S @ SR.T @ tr(-c)


class Scene:
    def __init__(self, path):
        self.path = str(path)
        self.root = ET.parse(path).getroot()
        self.defs = {}
        for e in self.root.iter():
            if e.get("DEF"):
                self.defs[e.get("DEF")] = e

    def use(self, e):
        return self.defs.get(e.get("USE"), e) if e is not None and e.get("USE") else e

    def child(self, e, *tags):
        for c in e:
            if c.tag in tags:
                return self.use(c)
        return None

    def shapes(self):
        """(shape element, 4x4 world matrix, path of DEF names) for every Shape, outside of prototypes."""
        out = []

        def walk(e, M, path):
            e = self.use(e)
            if e.tag == "Transform":
                M = M @ transform_matrix(e)
            if e.get("DEF") and e.tag in ("Transform", "Group"):
                path = path + [e.get("DEF")]
            if e.tag == "Shape":
                out.append((e, M, path))
                return
            for c in e:
                if c.tag in ("Transform", "Group", "Shape", "Collision", "Switch", "Anchor", "Billboard"):
                    walk(c, M, path)
        scene = self.root.find("Scene")
        for c in scene:
            walk(c, np.eye(4), [])
        return out

    def viewpoints(self):
        vps = {}
        for v in self.root.iter("Viewpoint"):
            if v.get("USE"):
                continue
            vps[v.get("DEF") or v.get("description") or "vp%d" % len(vps)] = {
                "position": floats(v.get("position") or "0 0 10"), "orientation": floats(v.get("orientation") or "0 0 1 0"),
                "fov": float(v.get("fieldOfView") or math.pi / 4), "description": v.get("description") or ""}
        return vps

    def shader_of(self, shape):
        app = self.child(shape, "Appearance")
        if app is None:
            return None, None, None
        sh = self.child(app, "ComposedShader")
        mat = self.child(app, "Material", "UnlitMaterial")
        return (sh.get("DEF") if sh is not None else None), sh, mat


def apply(M, p):
    p = np.asarray(p, float)
    return p @ M[:3, :3].T + M[:3, 3]


# ---------------------------------------------------------------------- decoders
def faces_of(scene, geo):
    """IndexedFaceSet → list of polygons, each a list of (coord, texcoord, normal) indices, plus per-face colour rows."""
    ci = ints(geo.get("coordIndex"))
    ti = ints(geo.get("texCoordIndex")) or ci
    ni = ints(geo.get("normalIndex")) or ci
    polys, cur = [], []
    for k, c in enumerate(ci):
        if c < 0:
            if cur:
                polys.append(cur)
            cur = []
        else:
            cur.append((c, ti[k] if k < len(ti) else c, ni[k] if k < len(ni) else c))
    if cur:
        polys.append(cur)
    return polys


def node_arrays(scene, geo):
    def arr(tag, attr, n):
        e = scene.child(geo, *tag)
        return floats(e.get(attr), n) if e is not None and e.get(attr) else None
    return {"coord": arr(("Coordinate",), "point", 3), "rgba": arr(("ColorRGBA",), "color", 4), "rgb": arr(("Color",), "color", 3),
            "normal": arr(("Normal",), "vector", 3), "tex3": arr(("TextureCoordinate3D",), "point", 3),
            "tex2": arr(("TextureCoordinate",), "point", 2)}


def decode_ribbons(scene, geo, M, uni=FROZEN):
    """LineShader quads → one segment per quad, with what the vertex and fragment shaders need."""
    A = node_arrays(scene, geo)
    seg = {k: [] for k in ("a", "b", "a_local", "b_local", "rgba", "code")}
    for f, poly in enumerate(faces_of(scene, geo)):
        # the corner whose code has bit 2 clear carries a = its own position, b = its texcoord
        best = None
        for c, t, n in poly:
            nv = A["normal"][n]
            code = (int(math.floor(math.atan2(nv[1], nv[0]) / 0.39269908 + 0.5)) + 16) % 16
            if best is None or (code & 2) == 0:
                best = (c, t, code)
            if (code & 2) == 0:
                break
        c, t, code = best
        a, b = A["coord"][c], A["tex3"][t]
        seg["a_local"].append(a)
        seg["b_local"].append(b)
        seg["rgba"].append(A["rgba"][f] if A["rgba"] is not None else (1, 1, 1, 0.3))
        seg["code"].append(code)
    out = {k: np.array(v) for k, v in seg.items()}
    out["a"], out["b"] = apply(M, out["a_local"]), apply(M, out["b_local"])
    cal = out["rgba"][:, 3]
    loop = cal >= 0.699
    idx = np.where(loop, np.floor((cal - 0.7) / 0.3 * 400.0 + 0.5), 0.0)
    dq = np.floor(idx / 100.0)
    out["loop"] = loop
    out["loop_phase"] = (idx - 100.0 * dq) / 100.0
    out["loop_span"] = 0.06 + 0.04 * dq
    out["width_a"] = np.where(loop, 0.3, cal)                      # aw: half width = (0.6 + 2.8 aw) px at 640 px tall
    flags = out["code"] & 12
    out["pulse"] = flags != 0
    out["seed"] = rnd3i(qpos(out["a_local"]) + qpos(out["b_local"]))[:, 0]
    out["pool"] = pool_at((out["a_local"] + out["b_local"]) * 0.5, uni)
    out["bloom"] = uni["bloom"] * np.where(flags != 0, 1.0, 0.45)
    sc = ((out["a_local"][:, 0] + out["b_local"][:, 0]) * 0.425 + (out["a_local"][:, 1] + out["b_local"][:, 1]) * 0.15 - uni["scan"]) * 2.4
    out["scan"] = np.where(flags != 0, 0.0, np.exp(-sc * sc))
    out["s_from_a"] = np.where(out["loop"], 0.0, np.where(out["a_local"][:, 0] >= out["b_local"][:, 0], 0.0, 1.0))
    return out


def decode_points(scene, geo, M, uni=FROZEN):
    A = node_arrays(scene, geo)
    p = A["coord"].copy()
    rgba = A["rgba"] if A["rgba"] is not None else np.tile([1, 1, 1, 0.5], (len(p), 1))
    n = min(len(p), len(rgba))
    p, rgba = p[:n], rgba[:n]
    r = rnd3i(qpos(p) + np.array([11, 5, 3]))
    kind = np.floor(rgba[:, 3] * 4.0 + 0.001)
    size = (rgba[:, 3] - kind * 0.25) * 16.0
    t = uni["time"]
    drift = (kind > 1.5) & (kind < 2.5)
    off = np.stack([np.sin(t * (0.05 + 0.06 * r[:, 0]) + r[:, 1] * 6.283) * 0.7,
                    np.sin(t * (0.04 + 0.05 * r[:, 1]) + r[:, 2] * 6.283) * 0.5,
                    np.sin(t * (0.03 + 0.04 * r[:, 2]) + r[:, 0] * 6.283) * 0.6], -1)
    p = p + np.where(drift[:, None], off, 0.0)
    motes = kind > 2.5
    size = np.where(motes, size * (1.0 - 0.45 * uni["bloom"]), size)
    tw = 0.62 + 0.38 * np.sin(t * (0.7 + 1.9 * r[:, 1]) + r[:, 0] * 6.283)
    tw = np.where(kind < 0.5, 0.88 + 0.12 * tw, tw)
    pool = np.where(((kind > 0.5) & (kind < 2.5))[:, None], pool_at(p, uni), 0.0)
    colour = np.clip(rgba[:, :3] + pool * 0.8, 0.0, 1.0)
    scale = float(np.linalg.norm(M[:3, 0]))
    return {"p_local": p, "p": apply(M, p), "kind": kind, "size": size, "twinkle": tw, "colour": colour,
            "pool_gain": 1.0 + 0.9 * np.minimum(1.0, np.linalg.norm(pool, axis=-1)),
            "spike": (size > 2.75) & (kind > 0.5) & (kind < 1.5), "scale": scale,
            "visible": size > 0.01, "bloom": uni["bloom"],
            # the sprite's world diameter: px = size * scale * 0.16 * P11 * H / 2 / dist  →  size * scale * 0.16 world units
            "diameter": size * scale * 0.16 * (0.85 + 0.3 * tw)}


def decode_faces(scene, geo, M, per_face=True):
    A = node_arrays(scene, geo)
    tris, cols, bary_ok = [], [], A["tex2"] is not None
    for f, poly in enumerate(faces_of(scene, geo)):
        for k in range(1, len(poly) - 1):                          # fan, as X3D convex faces
            tris.append([poly[0][0], poly[k][0], poly[k + 1][0]])
            if A["rgba"] is not None:
                cols.append(A["rgba"][f] if per_face else A["rgba"][poly[0][0]])
            elif A["rgb"] is not None:
                cols.append(list(A["rgb"][f]) + [1.0])
    tris = np.array(tris, int).reshape(-1, 3)
    return {"local": A["coord"][tris], "world": apply(M, A["coord"][tris].reshape(-1, 3)).reshape(-1, 3, 3),
            "rgba": np.array(cols, float).reshape(-1, 4) if cols else None}


def decode(x3d_path, uni=None) -> dict:
    """Every Gobbler shape at a frozen moment, as plain arrays, keyed by role."""
    uni = dict(FROZEN, **(uni or {}))
    sc = Scene(x3d_path)
    parts, skipped = [], []
    for i, (shape, M, path) in enumerate(sc.shapes()):
        geo = next((sc.use(c) for c in shape if c.tag != "Appearance"), None)
        sdef, sh, mat = sc.shader_of(shape)
        name = "%02d_%s" % (i, (sdef or (mat.tag if mat is not None else "plain")))
        where = "/".join(path)
        if geo is None:
            continue
        if "PickMarker" in path or sdef == "Hide":
            skipped.append({"shape": i, "why": "PickMarker (parked 900 m below)" if "PickMarker" in path else "Hide shader draws nothing",
                            "path": where})
            continue
        base = {"shape": i, "name": name, "path": where, "shader": sdef, "matrix": M}
        if sdef == "LineShader" and geo.tag == "IndexedFaceSet":
            parts.append(dict(base, role="ribbons", **decode_ribbons(sc, geo, M, uni)))
        elif sdef == "PointShader" and geo.tag == "PointSet":
            parts.append(dict(base, role="points", **decode_points(sc, geo, M, uni)))
        elif sdef == "FacetShader":
            parts.append(dict(base, role="facets", **decode_faces(sc, geo, M)))
        elif sdef == "EyeShader":
            parts.append(dict(base, role="eye", **decode_faces(sc, geo, M)))
        elif sdef == "SkyShader" and geo.tag == "Sphere":
            parts.append(dict(base, role="sky", radius=float(geo.get("radius") or 1), centre=M[:3, 3].copy()))
        elif mat is not None and mat.tag == "UnlitMaterial" and geo.tag == "IndexedFaceSet":
            parts.append(dict(base, role="membrane", emissive=floats(mat.get("emissiveColor") or "1 1 1"), **decode_faces(sc, geo, M)))
        else:
            skipped.append({"shape": i, "why": "no decoder for %s with %s" % (geo.tag, sdef or "no shader"), "path": where})
    return {"source": str(x3d_path), "uniforms": uni, "parts": parts, "skipped": skipped, "viewpoints": sc.viewpoints()}


def summary(bake) -> list[str]:
    rows = []
    for p in bake["parts"]:
        n = {"ribbons": lambda: "%d segments (%d flywheel, %d head lattice)" % (len(p["a"]), p["loop"].sum(), p["pulse"].sum()),
             "points": lambda: "%d points (kinds %s)" % (len(p["p"]), {int(k): int(c) for k, c in zip(*np.unique(p["kind"].astype(int), return_counts=True))}),
             "facets": lambda: "%d triangles" % len(p["world"]), "eye": lambda: "%d triangles" % len(p["world"]),
             "membrane": lambda: "%d triangles, alpha %.2f-%.2f" % (len(p["world"]), p["rgba"][:, 3].min(), p["rgba"][:, 3].max()),
             "sky": lambda: "sphere r=%g" % p["radius"]}[p["role"]]()
        rows.append("%-14s %-9s %s" % (p["name"], p["role"], n))
    for s in bake["skipped"]:
        rows.append("%02d skipped: %s" % (s["shape"], s["why"]))
    return rows


# ---------------------------------------------------------------------- preview: the GLSL, on the CPU
def camera(vp, w, h):
    """View matrix (world → eye) and the X3D projection: fieldOfView spans the smaller image side."""
    R = rotation_matrix(vp["orientation"])
    C = R.copy()
    C[:3, 3] = vp["position"]
    V = np.linalg.inv(C)
    f = 1.0 / math.tan(vp["fov"] / 2)
    fx, fy = (f * h / w, f) if w >= h else (f, f * w / h)
    return V, fx, fy


def project(V, fx, fy, w, h, pw):
    """World points → pixel x, y (y down), view depth (positive in front)."""
    pv = apply(V, pw)
    z = -pv[..., 2]
    zs = np.where(np.abs(z) < 1e-6, 1e-6, z)
    x = (pv[..., 0] * fx / zs * 0.5 + 0.5) * w
    y = (0.5 - pv[..., 1] * fy / zs * 0.5) * h
    return x, y, z, pv


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _fract(x):
    return x - np.floor(x)


def _h1(p):
    p = _fract(p * 0.3183099 + 0.1) * 17.0
    return _fract(p[..., 0] * p[..., 1] * p[..., 2] * (p[..., 0] + p[..., 1] + p[..., 2]))


def _vnoise(x):
    i, f = np.floor(x), _fract(x)
    f = f * f * (3.0 - 2.0 * f)
    def h(o):
        return _h1(i + np.array(o, float))
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    def mix(a, b, t):
        return a + (b - a) * t
    return mix(mix(mix(h((0, 0, 0)), h((1, 0, 0)), fx), mix(h((0, 1, 0)), h((1, 1, 0)), fx), fy),
               mix(mix(h((0, 0, 1)), h((1, 0, 1)), fx), mix(h((0, 1, 1)), h((1, 1, 1)), fx), fy), fz)


def sky_colour(d):
    """SkyShader fragment (without the 1/255 dither), d = direction from the sky sphere's centre."""
    d = d / np.linalg.norm(d, axis=-1, keepdims=True)
    f = _smooth(0.0, 0.4, -d[..., 2])
    q = d[..., :2] / np.maximum(-d[..., 2], 0.1)[..., None]
    a = (q - np.array([-0.075, 0.03])) * np.array([1.0, 1.3])
    b = q - np.array([0.2, -0.05])
    n = _vnoise(d * 9.0) * 0.6 + _vnoise(d * 21.0) * 0.4
    col = (np.array([0.057, 0.050, 0.047])
           + (f * np.exp(-(a * a).sum(-1) / 0.03) * (0.8 + 0.4 * n))[..., None] * np.array([0.15, 0.095, 0.045])
           + (f * np.exp(-(b * b).sum(-1) / 0.05) * (0.7 + 0.6 * n))[..., None] * np.array([0.085, 0.022, 0.032])
           + (f * _smooth(0.55, 0.9, n) * np.exp(-(q * q).sum(-1) / 0.12))[..., None] * np.array([0.03, 0.02, 0.012]))
    return col


def _blend(img, rgb, a, mask, ys, xs):
    """Shader output vec4(em / a, a) blended SRC_ALPHA, ONE_MINUS_SRC_ALPHA."""
    sub = img[ys, xs]
    a = np.where(mask, np.clip(a, 0, 1), 0.0)[..., None]
    img[ys, xs] = sub * (1 - a) + rgb * a


def _facet_colour(base_rgba, N, V, vview, vobj, L4, uni):
    """FacetShader fragment at phase 1 (no burn), per pixel of one facet."""
    mx = lambda c: c.max(-1)
    mn = lambda c: c.min(-1)
    cq = np.floor(base_rgba * 1000.0 + 0.5).astype(np.int64)
    fr = rnd3i(cq[:3] + np.array([cq[3], 2 * cq[3], 3 * cq[3]]))
    fr2 = rnd3i(cq[[2, 0, 1]] + np.array([91, 17, 53]))
    fid = fr[0]
    NdV = np.clip((N * V).sum(-1), 0.0, 1.0)
    base = base_rgba[:3]
    tint = base / max(base.max(), 1e-3)
    hiHue = tint + (np.array([1.0, 0.86, 0.58]) - tint) * 0.5
    flesh = float(np.clip((base[0] - base[1] - 0.22) * 3.0, 0.0, 1.0))
    norm = lambda v: v / np.linalg.norm(v, axis=-1, keepdims=True)
    Ne = norm(N + (fr - 0.5) * 0.22)
    R = 2 * (Ne * V).sum(-1, keepdims=True) * Ne - V                # reflect(-V, Ne)
    dot = lambda u, v: (u * norm(np.array(v, float))).sum(-1)
    key = _smooth(0.62, 0.96, dot(R, (-0.45, 0.6, 0.66)))
    key2 = _smooth(0.82, 0.98, dot(R, (0.55, 0.5, 0.67)))
    strip = np.exp(-((R[..., 0] - 0.72) * 5.0) ** 2) * _smooth(-0.4, 0.4, R[..., 1])
    fill = _smooth(0.55, 1.0, dot(R, (0.25, -0.25, 0.93)))
    hor = np.exp(-(R[..., 1] * 5.5 + 0.35) ** 2)
    top = _smooth(0.3, 1.0, R[..., 1])
    c = lambda *v: np.array(v, float)
    env = (c(1.0, 0.88, 0.66) * (key * 2.1 + key2 * 1.3)[..., None] + c(1.0, 0.7, 0.4) * (strip * 0.9 + fill * 0.28)[..., None]
           + c(0.95, 0.6, 0.3) * (hor * 0.5)[..., None] + c(0.5, 0.4, 0.3) * (top * 0.25)[..., None] + c(0.035, 0.026, 0.02))
    F0 = np.clip(base * c(1.2, 1.0, 0.72) * (0.75 + 0.5 * fid), 0.0, 1.0)
    F = F0 + (hiHue - F0) * ((1.0 - NdV) ** 5)[..., None]
    metal = F * env
    L = norm(c(-0.5, 0.6, 0.62))
    dif = np.maximum((N * L).sum(-1), 0.0)
    spec = np.maximum((N * norm(L + V)).sum(-1), 0.0) ** 70.0
    skin = (base * (0.3 + 0.7 * dif)[..., None] + (base + (c(1.0, 0.86, 0.58) - base) * 0.5) * (spec * 0.7)[..., None]
            + base * (strip * 0.45)[..., None] + c(0.9, 0.3, 0.15) * ((1.0 - NdV) ** 2.5 * 0.9)[..., None])
    col = metal + (skin - metal) * flesh
    lit = 0
    for k in range(4):
        Lv = L4[k] - vview
        dl = np.linalg.norm(Lv, axis=-1)
        Ld = Lv / dl[..., None]
        att = 1.2 / (1.0 + 0.0075 * dl * dl)
        dfk = np.maximum((N * Ld).sum(-1), 0.0)
        rd = np.maximum((R * Ld).sum(-1), 0.0)
        sp = rd ** 30.0 * 1.7 + rd ** 7.0 * 0.28
        lit = lit + LIGHT_COLOURS[k] * att[..., None] * (hiHue * (dfk * 0.55)[..., None] + (hiHue + (1 - hiHue) * 0.5) * sp[..., None])
    col = col + lit * 0.5 * (1.0 - 0.35 * flesh)
    rim = (1.0 - NdV) ** 3.0
    rc = c(0.55, 0.08, 0.17) + (c(1.0, 0.46, 0.12) - c(0.55, 0.08, 0.17)) * np.clip(0.55 + vobj[..., 1] * 0.28, 0, 1)[..., None]
    col = col + rc * (rim * 0.85)[..., None]
    Np = norm(N + (fr2 - 0.5) * 0.3)
    Rp = 2 * (Np * V).sum(-1, keepdims=True) * Np - V
    glint = np.maximum(dot(Rp, (-0.45, 0.65, 0.6)), 0.0) ** 160.0
    flash = float(fid >= 0.9) * (0.5 + 0.5 * math.sin(uni["time"] * (1.1 + 2.0 * fid) + fid * 40.0)) ** 40.0
    col = col + (hiHue + (c(1.0, 0.95, 0.85) - hiHue) * 0.35) * ((glint * 1.5 + flash * 1.3) * (1.0 - 0.6 * flesh) * uni["glow"])[..., None]
    sc = np.exp(-((vobj[..., 0] * 0.85 + vobj[..., 1] * 0.3 - uni["scan"]) * 2.4) ** 2)
    col = col + (c(1.0, 0.66, 0.26) * 0.45 + c(1.0, 0.86, 0.6) * rim[..., None] * 0.9) * (sc * uni["glow"])[..., None]
    vmax, vmin = mx(col), mn(col)
    sat = (vmax - vmin) / np.maximum(vmax, 1e-4)
    st = 1.0 - tint.min()
    floorCol = vmax[..., None] * (1.0 - min(1.0, 0.42 / max(st, 0.42)) * (1.0 - tint))
    col = col + (floorCol - col) * np.clip((0.42 - sat) / 0.42, 0.0, 1.0)[..., None]
    m = mx(col)
    col = col / (1.0 + m * 0.12)[..., None]
    m = mx(col)
    over = m > 1.0
    blended = col / np.maximum(m, 1e-6)[..., None] * (1 - np.clip((m - 1.0) * 0.45, 0, 0.55))[..., None] \
        + c(1.0, 0.86, 0.58) * np.clip((m - 1.0) * 0.45, 0, 0.55)[..., None]
    col = np.where(over[..., None], blended, col)
    a = np.clip(base_rgba[3] + rim * 0.2 + sc * 0.15, 0.0, 1.0)
    return col, a


def _raster_tri(px, py, w, h):
    """Pixels inside a projected triangle and their barycentrics."""
    x0, x1 = int(max(0, math.floor(min(px)))), int(min(w - 1, math.ceil(max(px))))
    y0, y1 = int(max(0, math.floor(min(py)))), int(min(h - 1, math.ceil(max(py))))
    if x1 < x0 or y1 < y0:
        return None
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    X, Y = xs + 0.5, ys + 0.5
    d = (py[1] - py[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (py[0] - py[2])
    if abs(d) < 1e-9:
        return None
    l0 = ((py[1] - py[2]) * (X - px[2]) + (px[2] - px[1]) * (Y - py[2])) / d
    l1 = ((py[2] - py[0]) * (X - px[2]) + (px[0] - px[2]) * (Y - py[2])) / d
    l2 = 1 - l0 - l1
    m = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
    if not m.any():
        return None
    return ys, xs, np.stack([l0, l1, l2], -1), m


def preview(bake, viewpoint, w=960, h=540, roles=("sky", "facets", "membrane", "eye", "ribbons", "points"), line_gain=1.0):
    """The Gobbler from an X3D viewpoint, drawn the way its GLSL draws it (float RGB, 0-1)."""
    uni = bake["uniforms"]
    vp = bake["viewpoints"][viewpoint] if isinstance(viewpoint, str) else viewpoint
    V, fx, fy = camera(vp, w, h)
    img = np.zeros((h, w, 3))
    zbuf = np.full((h, w), np.inf)
    parts = {r: [p for p in bake["parts"] if p["role"] == r] for r in roles}
    # sky: the ray from the eye through each pixel, hit on the sky sphere, direction from its centre
    for p in parts.get("sky", []):
        ys, xs = np.mgrid[0:h, 0:w]
        dv = np.stack([((xs + 0.5) / w * 2 - 1) / fx, (1 - (ys + 0.5) / h * 2) / fy, -np.ones_like(xs, float)], -1)
        Rw = np.linalg.inv(V)[:3, :3]
        dw = dv @ Rw.T
        dw /= np.linalg.norm(dw, axis=-1, keepdims=True)
        o = np.asarray(vp["position"], float) - p["centre"]
        bq = (dw * o).sum(-1)
        cq = (o * o).sum() - p["radius"] ** 2
        t = -bq + np.sqrt(np.maximum(bq * bq - cq, 0.0))
        img[:] = sky_colour(o + dw * t[..., None])
    L4 = [apply(V, np.asarray(uni["light%d" % k], float)) for k in range(4)]
    # facets, membranes, eye: back to front, alpha blended; facets write depth
    tris = []
    for role in ("facets", "membrane", "eye"):
        for p in parts.get(role, []):
            for k in range(len(p["world"])):
                tris.append((role, p, k))
    def depth(item):
        role, p, k = item
        return -apply(V, p["world"][k].mean(0))[2]
    tris.sort(key=depth, reverse=True)
    for role, p, k in tris:
        X, Y, Z, PV = project(V, fx, fy, w, h, p["world"][k])
        if (Z <= 0.05).any():
            continue
        r = _raster_tri(X, Y, w, h)
        if r is None:
            continue
        ys, xs, lam, m = r
        # perspective-correct interpolation of view and object positions
        iw = lam / Z
        iw = iw / iw.sum(-1, keepdims=True)
        vview = iw @ PV
        vobj = iw @ p["local"][k]
        z = 1.0 / (lam / Z).sum(-1)
        if role == "facets":
            N = np.cross(PV[1] - PV[0], PV[2] - PV[0])
            N = N / (np.linalg.norm(N) or 1)
            Vd = -vview / np.linalg.norm(vview, axis=-1, keepdims=True)
            flip = (Vd @ N) < 0
            Nn = np.where(flip[..., None], -N, N)
            col, a = _facet_colour(p["rgba"][k], Nn, Vd, vview, vobj, L4, uni)
            _blend(img, col, a, m, ys, xs)
            zz = zbuf[ys, xs]
            zbuf[ys, xs] = np.where(m & (z < zz), z, zz)
        elif role == "membrane":
            rgba = p["rgba"][k] if p["rgba"] is not None else np.array([1, 1, 1, 1.0])
            _blend(img, np.broadcast_to(rgba[:3], m.shape + (3,)), np.full(m.shape, rgba[3]), m, ys, xs)
        else:                                                       # the eye, from its object-space x, y
            vP = vobj[..., :2]
            col, a = _eye_colour(vP, uni, px_size=_px_size(vP))
            _blend(img, col, a, m & (z < zbuf[ys, xs] + 0.004 * z), ys, xs)
    for p in parts.get("ribbons", []):
        _draw_ribbons(img, zbuf, p, V, fx, fy, w, h, uni, line_gain)
    for p in parts.get("points", []):
        _draw_points(img, zbuf, p, V, fx, fy, w, h, uni)
    return np.clip(img, 0, 1)


def _px_size(vP):
    gy, gx = np.gradient(np.linalg.norm(vP, axis=-1)) if vP.shape[0] > 1 and vP.shape[1] > 1 else (np.zeros(vP.shape[:2]),) * 2
    return np.abs(gx) + np.abs(gy) + 1e-4


def _eye_colour(vP, uni, px_size):
    r = np.linalg.norm(vP, axis=-1)
    aa = px_size * 1.2 + 1e-4
    ang = np.arctan2(vP[..., 1], vP[..., 0])
    ring = lambda rr, R, wd: 1.0 - _smooth(wd * 0.5, wd * 0.5 + aa, np.abs(rr - R))
    iris = 1.0 - _smooth(0.2 - aa, 0.2 + aa, r)
    fib = 0.5 + 0.5 * np.sin(ang * 27.0 + np.sin(ang * 5.0) * 2.5)
    c = lambda *v: np.array(v, float)
    ic = (c(1.0, 0.6, 0.14) + (c(0.6, 0.24, 0.04) - c(1.0, 0.6, 0.14)) * _smooth(0.1, 0.2, r)[..., None]) * (0.7 + 0.3 * fib)[..., None]
    ic = ic + (c(0.16, 0.05, 0.015) - ic) * _smooth(0.165, 0.2, r)[..., None]
    ic = ic + (c(0.018, 0.011, 0.008) - ic) * (1.0 - _smooth(0.105 - aa, 0.105 + aa, r))[..., None]
    c1 = vP - c(-0.055, 0.065)
    c2 = vP - c(0.06, -0.045)
    ic = ic + c(1.0, 0.97, 0.9) * (np.exp(-(c1 * c1).sum(-1) * 1100.0) + 0.45 * np.exp(-(c2 * c2).sum(-1) * 3000.0))[..., None]
    rg = (ring(r, 0.26, 0.012) + 0.8 * ring(np.linalg.norm(vP * c(1.0, 1.111), axis=-1), 0.36, 0.01)
          + 0.6 * ring(np.linalg.norm(vP * c(1.0, 1.25), axis=-1), 0.46, 0.008))
    gw = np.exp(-((r - 0.2) * 14.0) ** 2) * 0.4 * (1.0 - iris)
    em = ic * iris[..., None] + c(0.95, 0.85, 0.6) * (rg * (1.0 - iris))[..., None] + c(1.0, 0.58, 0.18) * gw[..., None]
    a = np.clip(iris + rg + gw, 0.0, 1.0)
    ph, t = uni["phase"], uni["time"]
    st = _smooth(0.17, 0.24, ph) * (1.0 - _smooth(0.84, 0.93, ph))
    if st > 0:
        tw = 0.8 + 0.2 * math.sin(t * 2.3)
        core = np.exp(-r * r * 260.0)
        spk = (np.exp(-np.abs(vP[..., 1]) * 90.0) * np.exp(-np.abs(vP[..., 0]) * 6.0)
               + np.exp(-np.abs(vP[..., 0]) * 90.0) * np.exp(-np.abs(vP[..., 1]) * 6.0)) * 1.1
        star = np.clip((core * 1.2 + spk + np.exp(-r * r * 28.0) * 0.35) * tw, 0.0, 1.0)
        sc = c(1.0, 0.62, 0.22) + (c(1.0, 0.95, 0.82) - c(1.0, 0.62, 0.22)) * np.clip(core * 1.5 + spk * 0.6, 0, 1)[..., None]
        em = em + (sc * star[..., None] - em) * st
        a = a + (star - a) * st
    return np.minimum(em / np.maximum(a, 1e-3)[..., None], 1.0), a


def _draw_ribbons(img, zbuf, p, V, fx, fy, w, h, uni, gain=1.0):
    ax, ay, az, _ = project(V, fx, fy, w, h, p["a"])
    bx, by, bz, _ = project(V, fx, fy, w, h, p["b"])
    t = uni["time"]
    glow = uni["glow"]
    for k in range(len(ax)):
        if az[k] <= 0.05 or bz[k] <= 0.05:
            continue
        bl = p["bloom"][k]
        half = (0.6 + 2.8 * p["width_a"][k]) * (1.0 + 1.6 * bl) * h / 640.0 * gain
        dx, dy = bx[k] - ax[k], by[k] - ay[k]
        ln = math.hypot(dx, dy)
        ux, uy = (dx / ln, dy / ln) if ln > 1e-5 else (1.0, 0.0)
        x0 = int(max(0, math.floor(min(ax[k], bx[k]) - half - 1)))
        x1 = int(min(w - 1, math.ceil(max(ax[k], bx[k]) + half + 1)))
        y0 = int(max(0, math.floor(min(ay[k], by[k]) - half - 1)))
        y1 = int(min(h - 1, math.ceil(max(ay[k], by[k]) + half + 1)))
        if x1 < x0 or y1 < y0:
            continue
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        rx, ry = xs + 0.5 - ax[k], ys + 0.5 - ay[k]
        along = (rx * ux + ry * uy) / max(ln, 1e-5)
        across = (-rx * uy + ry * ux)
        m = (along >= 0) & (along <= 1) & (np.abs(across) <= half)
        if not m.any():
            continue
        vX = across / half
        z = az[k] + (bz[k] - az[k]) * along
        m &= z * (1 - 0.002) <= zbuf[ys, xs] + 1e-6
        loop = bool(p["loop"][k])
        s0 = p["s_from_a"][k]
        vS = s0 + ((1.0 if loop else 1.0 - s0) - s0) * along
        bb = 1.0 + 1.6 * bl
        core = np.exp(-vX * vX * 9.0 * bb * bb)
        halo = np.exp(-vX * vX * 2.2 * bb) * (0.3 - 0.12 * bl) + np.exp(-vX * vX * 3.0) * 0.1 * bl
        pk = np.zeros_like(vX)
        seed = p["seed"][k]
        if p["pulse"][k]:
            tt = _fract(t * (0.09 + 0.13 * seed) + seed * 17.0) * 1.8 - 0.4
            dd = vS - tt
            pk = np.exp(-dd * dd * 60.0) * glow
        lk = np.zeros_like(vX)
        if loop:
            u = _fract(t / 14.0)
            pos = p["loop_phase"][k] + vS * p["loop_span"][k]
            d1, d2 = _fract(u - pos), _fract(u + 0.5 - pos)
            lk = (np.exp(-d1 * 11.0) + np.exp(-d2 * 11.0)) * glow
            pk = np.maximum(pk * 0.2, (np.exp(-d1 * 70.0) + np.exp(-d2 * 70.0)) * glow)
        vc = p["rgba"][k][:3]
        hc = vc * (1.0 + 0.6 * bl)
        pulseC = (vc + (np.array([1.0, 0.96, 0.86]) - vc) * 0.5) if loop else np.array([1.0, 0.85, 0.55])
        em = (vc * (core * 1.1 * (1.0 + 0.7 * loop + 1.6 * lk))[..., None] + hc * (halo * (1.0 + 1.2 * lk))[..., None]
              + pulseC * (pk * (core * 1.7 + halo * 2.2))[..., None]
              + np.array([1.0, 0.74, 0.38]) * (p["scan"][k] * (core + halo) * 1.3 * glow)[..., None]
              + p["pool"][k] * (core * 0.9 + halo * 1.1)[..., None] * 0.8)
        a = np.clip(em.max(-1), 0.0, 1.0)
        m &= a >= 0.004
        _blend(img, np.minimum(em / np.maximum(a, 1e-3)[..., None], 1.0), a, m, ys, xs)


def _draw_points(img, zbuf, p, V, fx, fy, w, h, uni):
    X, Y, Z, _ = project(V, fx, fy, w, h, p["p"])
    k_ = h / 640.0
    bloom = uni["bloom"]
    glow = uni["glow"]
    for i in np.argsort(-Z):
        if not p["visible"][i] or Z[i] <= 0.05:
            continue
        dist = max(0.2, Z[i])
        size, kind, tw = p["size"][i], p["kind"][i], p["twinkle"][i]
        px = size * p["scale"] * 0.16 * fy * h * 0.5 / dist
        coc = abs(dist - 14.0) * (0.9 if kind > 0.5 else 0.25) * k_
        s = float(np.clip(px * (0.85 + 0.3 * tw) * (1.0 + 0.9 * bloom) + coc, 1.0, 110.0 * k_))
        e = 0.0
        vB = tw * (1.0 + 0.9 * e) * float(np.clip(px * (1.0 + 0.9 * bloom) / s, 0.2, 1.0)) * (1.0 + (glow - 1.0) * 0.5)
        vB *= p["pool_gain"][i]
        half = s / 2
        x0, x1 = int(max(0, math.floor(X[i] - half))), int(min(w - 1, math.ceil(X[i] + half)))
        y0, y1 = int(max(0, math.floor(Y[i] - half))), int(min(h - 1, math.ceil(Y[i] + half)))
        if x1 < x0 or y1 < y0:
            continue
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        dx, dy = (xs + 0.5 - X[i]) / half, (ys + 0.5 - Y[i]) / half
        r2 = dx * dx + dy * dy
        m = (r2 <= 1.0) & (Z[i] * (1 - 0.003) <= zbuf[ys, xs] + 1e-6)
        bb = 1.0 + 0.9 * bloom
        core = np.exp(-r2 * 16.0 * bb * bb)
        halo = np.exp(-r2 * 3.5 * bb) * (0.62 - 0.2 * bloom) * (1.0 - r2) + np.exp(-r2 * 2.5) * 0.16 * bloom * (1.0 - r2)
        sp = float(p["spike"][i]) * (np.exp(-np.abs(dx) * 28.0) + np.exp(-np.abs(dy) * 28.0)) * np.maximum(0.0, 1.0 - np.sqrt(r2) / 0.7) * 0.7
        vc = p["colour"][i]
        hc = vc * (1.0 + 0.6 * bloom)
        em = (hc * (halo + sp)[..., None] + (vc + (1.0 - vc) * 0.6) * (core * 1.3)[..., None]) * vB
        a = np.clip(em.max(-1), 0.0, 1.0)
        m &= a >= 0.004
        _blend(img, np.minimum(em / np.maximum(a, 1e-3)[..., None], 1.0), a, m, ys, xs)


def to_png(img, path):
    from PIL import Image
    Image.fromarray((np.clip(img, 0, 1) ** (1 / 1.0) * 255 + 0.5).astype(np.uint8)).save(path)
    return str(path)


# ---------------------------------------------------------------------- glTF: geometry any engine can draw
# Data channels (TEXCOORD_n) per role, for engine materials that run the fragment shaders (G2). TEXCOORD_0 is a real UV.
CHANNELS = {
    "ribbons": ["(along s, 0)", "(r, g)", "(b, loop)", "(loop phase, loop span)", "(seed, pulse)", "(pool r, pool g)",
                "(pool b, scan)", "(width a, bloom)"],
    "points": ["(0, 0)", "(r, g)", "(b, brightness)", "(spike, kind)", "(size, twinkle)"],
    "facets": ["barycentric (x, y)", "(r, g)", "(b, a)", "(fr.x, fr.y)", "(fr.z, fr2.x)", "(fr2.y, fr2.z)", "(local x, y)", "(local z, 0)"],
    "eye": ["(local x, y)", "(local z, 0)"],
    "membrane": ["(0, 0)", "(r, g)", "(b, a)"],
    "sky": ["(local x, y)", "(local z, 0)"],
}


def _ring(a, b, r, n):
    """A tube from a to b: 2 rings of n vertices, outward normals, 2n triangles."""
    d = b - a
    L = np.linalg.norm(d)
    t = d / L if L > 1e-9 else np.array([1.0, 0, 0])
    u = np.cross(t, [0, 1, 0] if abs(t[1]) < 0.9 else [1, 0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(t, u)
    ang = np.arange(n) * 2 * math.pi / n
    nrm = np.outer(np.cos(ang), u) + np.outer(np.sin(ang), v)
    pos = np.concatenate([a + nrm * r, b + nrm * r])
    idx = []
    for k in range(n):
        k1 = (k + 1) % n
        idx += [k, k1, n + k, k1, n + k1, n + k]
    return pos, np.concatenate([nrm, nrm]), np.array(idx)


def _icosphere(sub=1):
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
         (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    v = [np.array(x, float) / np.linalg.norm(x) for x in v]
    for _ in range(sub):
        cache, nf = {}, []
        def mid(i, j):
            k = (min(i, j), max(i, j))
            if k not in cache:
                m = v[i] + v[j]
                v.append(m / np.linalg.norm(m))
                cache[k] = len(v) - 1
            return cache[k]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(v), np.array(f).reshape(-1)


def ribbon_radius(width_a, bloom, ref_dist, fov=math.pi / 4, ref_height=640):
    """The world half width a ribbon has on screen at ref_dist: (0.6 + 2.8 aw)(1 + 1.6 bl) px at a 640 px tall canvas."""
    px = (0.6 + 2.8 * width_a) * (1.0 + 1.6 * bloom)
    return px * 2.0 * ref_dist * math.tan(fov / 2) / ref_height


def meshes(bake, ref_dist=8.0, tube_sides=6, sphere_sub=1, sky_sub=3, core_scale=1.0):
    """Per part: positions, normals, indices and data channels (see CHANNELS), in X3D world coordinates (metres, Y up).
    core_scale: sphere radius as a fraction of the sprite's full extent. 1.0 for shader materials that fade the halo out
    themselves (G2); about 0.3 for flat placeholder materials, which would otherwise light the whole halo."""
    out = []
    ico_v, ico_f = _icosphere(sphere_sub)
    for p in bake["parts"]:
        role = p["role"]
        P, N, I, UV = [], [], [], [[] for _ in CHANNELS[role]]
        base = 0
        def add(pos, nrm, idx, uvs):
            nonlocal base
            P.append(pos)
            N.append(nrm)
            I.append(idx + base)
            for c, val in enumerate(uvs):
                UV[c].append(np.broadcast_to(np.asarray(val, float), (len(pos), 2)))
            base += len(pos)
        if role == "ribbons":
            for k in range(len(p["a"])):
                r = ribbon_radius(p["width_a"][k], p["bloom"][k], ref_dist)
                pos, nrm, idx = _ring(p["a"][k], p["b"][k], r, tube_sides)
                s0 = p["s_from_a"][k]
                s1 = 1.0 if p["loop"][k] else 1.0 - s0
                along = np.repeat([[s0, 0.0], [s1, 0.0]], tube_sides, axis=0)
                c = p["rgba"][k]
                add(pos, nrm, idx, [along, (c[0], c[1]), (c[2], float(p["loop"][k])), (p["loop_phase"][k], p["loop_span"][k]),
                                    (p["seed"][k], float(p["pulse"][k])), (p["pool"][k][0], p["pool"][k][1]),
                                    (p["pool"][k][2], p["scan"][k]), (p["width_a"][k], p["bloom"][k])])
        elif role == "points":
            for k in range(len(p["p"])):
                if not p["visible"][k] or p["diameter"][k] <= 0:
                    continue
                c = p["colour"][k]
                tw = p["twinkle"][k]
                vB = tw * p["pool_gain"][k] * min(1.0, max(0.2, 1.0 / (0.85 + 0.3 * tw)))
                add(p["p"][k] + ico_v * (p["diameter"][k] / 2 * core_scale), ico_v, ico_f,
                    [(0, 0), (c[0], c[1]), (c[2], vB), (float(p["spike"][k]), p["kind"][k]), (p["size"][k], tw)])
        elif role in ("facets", "eye", "membrane"):
            W, Lc = p["world"], p["local"]
            nrm = np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0])
            nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-12)
            pos = W.reshape(-1, 3)
            nn = np.repeat(nrm, 3, axis=0)
            idx = np.arange(len(pos))
            loc = Lc.reshape(-1, 3)
            if role == "facets":
                rgba = np.repeat(p["rgba"], 3, axis=0)
                cq = np.floor(p["rgba"] * 1000.0 + 0.5).astype(np.int64)
                fr = rnd3i(cq[:, :3] + np.stack([cq[:, 3], 2 * cq[:, 3], 3 * cq[:, 3]], -1))
                fr2 = rnd3i(cq[:, [2, 0, 1]] + np.array([91, 17, 53]))
                fr, fr2 = np.repeat(fr, 3, axis=0), np.repeat(fr2, 3, axis=0)
                bary = np.tile([[0, 0], [1, 0], [0, 1]], (len(W), 1))
                add(pos, nn, idx, [bary, rgba[:, :2], rgba[:, 2:], fr[:, :2], np.stack([fr[:, 2], fr2[:, 0]], -1),
                                   fr2[:, 1:], loc[:, :2], np.stack([loc[:, 2], np.zeros(len(loc))], -1)])
            elif role == "eye":
                add(pos, nn, idx, [loc[:, :2], np.stack([loc[:, 2], np.zeros(len(loc))], -1)])
            else:
                rgba = np.repeat(p["rgba"], 3, axis=0) if p["rgba"] is not None else np.tile([1, 1, 1, 1.0], (len(pos), 1))
                add(pos, nn, idx, [(0, 0), rgba[:, :2], rgba[:, 2:]])
        elif role == "sky":
            v, f = _icosphere(sky_sub)
            loc = v * p["radius"]
            add(p["centre"] + loc, -v, f, [loc[:, :2], np.stack([loc[:, 2], np.zeros(len(loc))], -1)])
        if not P:
            continue
        out.append({"name": p["name"], "role": role, "positions": np.concatenate(P), "normals": np.concatenate(N),
                    "indices": np.concatenate(I).astype(np.uint32), "uvs": [np.concatenate(u) for u in UV]})
    return out


# Fallback materials (glTF core + emissive strength): what an engine shows before the G2 shader materials replace them.
FALLBACK = {"ribbons": {"emissive": (1.0, 0.78, 0.45), "strength": 1.6, "base": (0, 0, 0), "blend": "BLEND", "alpha": 0.8},
            "points": {"emissive": (1.0, 0.9, 0.72), "strength": 2.5, "base": (0, 0, 0), "blend": "BLEND", "alpha": 0.9},
            "facets": {"emissive": (0.45, 0.25, 0.08), "strength": 1.0, "base": (0.9, 0.62, 0.3), "blend": "OPAQUE", "metal": 0.6, "rough": 0.35},
            "eye": {"emissive": (1.0, 0.55, 0.12), "strength": 2.0, "base": (0, 0, 0), "blend": "OPAQUE"},
            "membrane": {"emissive": (0.6, 0.62, 0.6), "strength": 1.0, "base": (0, 0, 0), "blend": "BLEND", "alpha": 0.06},
            "sky": {"emissive": (0.057, 0.05, 0.047), "strength": 1.0, "base": (0, 0, 0), "blend": "OPAQUE"}}


def write_glb(bake, path, ref_dist=8.0, tube_sides=6, sphere_sub=1, core_scale=0.3, gain=1.0, style=None) -> dict:
    """One glTF node per Gobbler part, named "GOB_<role>_<shape>", with material "GOB_<role>".
    style: per-role changes to FALLBACK ({"facets": {"metal": 0.9, "rough": 0.2}, "ribbons": {"strength": 4}}): a bake
    variant (BAKES) is a style plus core_scale / gain / tube_sides."""
    parts = meshes(bake, ref_dist, tube_sides, sphere_sub, core_scale=core_scale)
    bin_, views, accs, gmeshes, nodes = bytearray(), [], [], [], []
    roles = sorted({m["role"] for m in parts})
    mats = []
    for r in roles:
        f = dict(FALLBACK[r], **((style or {}).get(r) or {}))
        m = {"name": "GOB_%s" % r, "doubleSided": True, "alphaMode": f["blend"],
             "pbrMetallicRoughness": {"baseColorFactor": list(f["base"]) + [f.get("alpha", 1.0)],
                                      "metallicFactor": f.get("metal", 0.0), "roughnessFactor": f.get("rough", 1.0)},
             "emissiveFactor": list(f["emissive"]), "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": f["strength"] * gain}},
             "extras": {"x3dlab_role": r, "channels": CHANNELS[r]}}
        mats.append(m)

    def view(arr, target=None):
        nonlocal bin_
        while len(bin_) % 4:
            bin_ += b"\0"
        off = len(bin_)
        b = arr.tobytes()
        bin_ += b
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(b)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    def acc(arr, ctype, typ, minmax=False):
        a = {"bufferView": view(arr, 34963 if ctype == 5125 else 34962), "componentType": ctype, "count": len(arr), "type": typ}
        if minmax:
            a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1

    for m in parts:
        attrs = {"POSITION": acc(m["positions"].astype(np.float32), 5126, "VEC3", True),
                 "NORMAL": acc(m["normals"].astype(np.float32), 5126, "VEC3")}
        for c, uv in enumerate(m["uvs"]):
            attrs["TEXCOORD_%d" % c] = acc(uv.astype(np.float32), 5126, "VEC2")
        gmeshes.append({"name": "GOB_%s_%s" % (m["role"], m["name"]),
                        "primitives": [{"attributes": attrs, "indices": acc(m["indices"], 5125, "SCALAR"), "material": roles.index(m["role"])}]})
        nodes.append({"name": "GOB_%s_%s" % (m["role"], m["name"]), "mesh": len(gmeshes) - 1})
    while len(bin_) % 4:
        bin_ += b"\0"
    doc = {"asset": {"version": "2.0", "generator": "x3dlab gobbler baker (shader-aware)"},
           "extensionsUsed": ["KHR_materials_emissive_strength"], "scene": 0,
           "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes, "meshes": gmeshes, "materials": mats,
           "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}],
           "extras": {"x3dlab": {"source": bake["source"], "uniforms": {k: (list(v) if isinstance(v, tuple) else v) for k, v in bake["uniforms"].items()},
                                 "ref_dist": ref_dist, "core_scale": core_scale, "gain": gain, "style": style or {},
                                 "skipped": bake["skipped"]}}}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    blob = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)) + struct.pack("<II", len(js), 0x4E4F534A) + js \
        + struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    Path(path).write_bytes(blob)
    stats = {m["name"]: {"role": m["role"], "vertices": len(m["positions"]), "triangles": len(m["indices"]) // 3} for m in parts}
    return {"path": str(path), "bytes": len(blob), "parts": stats,
            "vertices": sum(s["vertices"] for s in stats.values()), "triangles": sum(s["triangles"] for s in stats.values())}


def part_centres(bake) -> list:
    """One centre per baked part (X3D metres): what the Unreal import matches against to measure its axes."""
    out = []
    for p in bake["parts"]:
        pts = {"ribbons": lambda: np.concatenate([p["a"], p["b"]]), "points": lambda: p["p"], "sky": lambda: p["centre"][None]}.get(
            p["role"], lambda: p["world"].reshape(-1, 3))()
        out.append([float(x) for x in (pts.min(0) + pts.max(0)) / 2])
    return out


def bounds(bake, roles=("ribbons", "points", "facets", "eye", "membrane")) -> tuple:
    """The bird's box (X3D metres), without the sky sphere and the parked pick marker."""
    pts = []
    for p in bake["parts"]:
        if p["role"] not in roles:
            continue
        pts.append({"ribbons": lambda: np.concatenate([p["a"], p["b"]]), "points": lambda: p["p"]}.get(
            p["role"], lambda: p["world"].reshape(-1, 3))())
    a = np.concatenate(pts) if pts else np.zeros((1, 3))
    return tuple(float(x) for x in a.min(0)), tuple(float(x) for x in a.max(0))


def studio_lights(uni=FROZEN, intensity=1.0, radius=100.0) -> list:
    """The FacetShader's four point lights (positions and colours from its uniforms) as X3D PointLights, in the
    manifest's form, so Unreal lights the gold with the same key, rim and fill the GLSL computes."""
    out = []
    for i in range(4):
        out.append({"type": "PointLight", "DEF": "GobLight%d" % i, "color": [float(c) for c in LIGHT_COLOURS[i]],
                    "intensity": float(intensity), "location": [float(c) for c in uni["light%d" % i]], "attenuation": (1.0, 0.0, 0.0),
                    "radius": float(radius), "on": True, "global": True, "ambientIntensity": 0.0})
    return out


def manifest_for(bake, x3d_manifest: dict, lights=True, light_intensity=1.0) -> dict:
    """The scene manifest Unreal's import is placed from, fixed for the Gobbler: its box without the sky and the
    pick marker, one centre per baked part (the axis check), and the shader's four lights as real lights."""
    m = dict(x3d_manifest)
    m["bounds"] = bounds(bake)
    m["shape_centres"] = part_centres(bake)
    m["lights"] = studio_lights(bake["uniforms"], light_intensity) if lights else []
    return m


# ---------------------------------------------------------------------- looks: bake variants and render-time grades
# A BAKE changes the glb (re-import, minutes); a GRADE changes only the render (project.grade_ok; seconds to set).
BAKES = {
    "studio": {"core_scale": 0.3, "gain": 1.0, "tube_sides": 8, "style": {}},
    "neon": {"core_scale": 0.22, "gain": 2.5, "tube_sides": 8,
             "style": {"facets": {"metal": 0.85, "rough": 0.22, "strength": 0.5}, "membrane": {"alpha": 0.04}}},
    "gilded": {"core_scale": 0.3, "gain": 1.4, "tube_sides": 8,
               "style": {"facets": {"base": (1.0, 0.71, 0.29), "metal": 1.0, "rough": 0.18, "strength": 0.25}}},
}
_DOF = {"r.DepthOfFieldQuality": 2}
GRADES = {
    # fidelity: as close to X_ITE's still as Unreal gets (no bloom, no lens, linear-ish)
    "fidelity": {"post": {"bloom_intensity": 0.0, "vignette_intensity": 0.0, "film_grain_intensity": 0.0}, "headlight": False},
    # cinematic: the emissive ribbons light the gold (Lumen), filmic bloom, a lens
    "glow": {"post": {"bloom_intensity": 1.0, "bloom_threshold": 0.8, "vignette_intensity": 0.25}, "headlight": False, "light_scale": 1.5},
    "film": {"post": {"bloom_intensity": 0.7, "bloom_threshold": 1.0, "vignette_intensity": 0.4, "film_grain_intensity": 0.08,
                      "scene_fringe_intensity": 0.6}, "headlight": False, "light_scale": 1.2},
    "macro": {"post": {"bloom_intensity": 0.8, "bloom_threshold": 0.9, "vignette_intensity": 0.35}, "headlight": False,
              "light_scale": 1.3, "aperture": 2.0, "focus": "auto", "cvars": _DOF},
    "warm": {"post": {"bloom_intensity": 0.9, "bloom_threshold": 0.8, "vignette_intensity": 0.3, "white_temp": 7500.0},
             "headlight": False, "light_scale": 1.3},
    # extreme: everything Unreal's Lumen has, for hero stills (8K, many samples): the GPU earns its keep here
    "extreme": {"post": {"bloom_intensity": 0.8, "bloom_threshold": 0.9, "vignette_intensity": 0.3, "lumen_scene_lighting_quality": 4.0,
                         "lumen_scene_detail": 4.0, "lumen_final_gather_quality": 4.0, "lumen_reflection_quality": 4.0,
                         "lumen_max_trace_distance": 20000.0},
                "headlight": False, "light_scale": 1.3,
                "cvars": {"r.Bloom.Quality": 5, "r.Tonemapper.Quality": 5, "r.Lumen.Reflections.DownsampleFactor": 1,
                          "r.Lumen.ScreenProbeGather.DownsampleFactor": 8, "r.Shadow.Virtual.SMRT.RayCountLocal": 16,
                          "r.Shadow.Virtual.SMRT.SamplesPerRayLocal": 16}},
}


def with_exposure(grade: dict, ev: float) -> dict:
    """A copy of `grade` at exposure bias `ev` (EV, manual exposure)."""
    g = json.loads(json.dumps(grade))
    g.setdefault("post", {})["auto_exposure_bias"] = round(float(ev), 2)
    return g


def neighbours(name: str, grade: dict, ev: float) -> dict:
    """The next round's candidates around the chosen look: exposure +-0.7 EV, bloom x/÷1.6, lights x/÷1.5."""
    out = {}
    for tag, dev, mul_b, mul_l in (("bright", +0.7, 1, 1), ("dark", -0.7, 1, 1), ("bloom+", 0, 1.6, 1), ("bloom-", 0, 1 / 1.6, 1),
                                   ("lights+", 0, 1, 1.5), ("lights-", 0, 1, 1 / 1.5)):
        g = with_exposure(grade, ev + dev)
        if mul_b != 1:
            g["post"]["bloom_intensity"] = round(g["post"].get("bloom_intensity", 0.35) * mul_b, 3)
        if mul_l != 1:
            g["light_scale"] = round(g.get("light_scale", 1.0) * mul_l, 3)
        out["%s_%s" % (name, tag.replace("+", "p").replace("-", "m"))] = (g, ev + dev)
    return out


def contact_sheet(rows: dict, cols: list, path, cell_w=480, label_h=40, scores=None):
    """rows: {row_name: {col_name: png path or None}} → one PNG with labels (and a score line under each image)."""
    from PIL import Image, ImageDraw
    cell_h = int(cell_w * 9 / 16)
    W, H = cell_w * len(cols), (cell_h + label_h) * len(rows) + label_h
    sheet = Image.new("RGB", (W, H), (18, 18, 20))
    d = ImageDraw.Draw(sheet)
    for j, c in enumerate(cols):
        d.text((j * cell_w + 8, 12), c, fill=(235, 235, 235))
    for i, (r, row) in enumerate(rows.items()):
        y = label_h + i * (cell_h + label_h)
        for j, c in enumerate(cols):
            f = row.get(c)
            if f and Path(f).exists():
                sheet.paste(Image.open(f).convert("RGB").resize((cell_w, cell_h)), (j * cell_w, y))
            sc = (scores or {}).get((r, c))
            d.text((j * cell_w + 8, y + cell_h + 6), "%s%s" % (r, ("  ·  SSIM %.3f · lit F %.3f · Δ %.0f" % (sc["ssim"], sc["lit_f"], sc["colour_delta"])) if sc else ""),
                   fill=(200, 200, 200))
    sheet.save(path)
    return str(path)


def _srgb_to_linear(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def exposure_fit(test, ref, threshold=0.06, clip=4.0) -> float:
    """EV to add to the test's exposure so its lit pixels match the reference's brightness: log2 of the ratio of
    median linear luminance over the pixels lit in both (clipped to +-clip EV). 0 when they share no lit pixels."""
    lt, _ = _lum(test)
    lr, _ = _lum(ref)
    both = (lt > np.median(lt) + threshold) & (lr > np.median(lr) + threshold)
    if both.sum() < 20:
        return 0.0
    a, b = np.median(_srgb_to_linear(lt[both])), np.median(_srgb_to_linear(lr[both]))
    if a <= 1e-6 or b <= 1e-6:
        return 0.0
    return float(np.clip(math.log2(b / a), -clip, clip))


def resize_like(png, ref_shape):
    """An Unreal still as a 0-1 RGB array the size of the reference (h, w)."""
    from PIL import Image
    im = Image.open(png).convert("RGB").resize((ref_shape[1], ref_shape[0]), Image.LANCZOS)
    return np.asarray(im, float) / 255.0


# ---------------------------------------------------------------------- the sprint's scoreboard
def _lum(img):
    a = np.asarray(img, float)
    if a.max() > 1.5:
        a = a / 255.0
    return a[..., :3] @ np.array([0.2126, 0.7152, 0.0722]), a[..., :3]


def _dilate(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def score(test, ref, threshold=0.06, tol=2) -> dict:
    """Unreal (test) against the reference (X_ITE or the CPU preview), same size, 0-1 or 0-255 RGB arrays.
    lit_f: F-score of 'which pixels light up' (luminance over the sky by `threshold`), with `tol` pixels of slack.
    colour_delta: mean |RGB| difference over the pixels lit in either, in 0-255 units. ssim: on luminance, 8x8 windows."""
    lt, ct = _lum(test)
    lr, cr = _lum(ref)
    bt, br = np.median(lt), np.median(lr)
    mt, mr = lt > bt + threshold, lr > br + threshold
    tp_p = (mt & _dilate(mr, tol)).sum() / max(mt.sum(), 1)
    tp_r = (mr & _dilate(mt, tol)).sum() / max(mr.sum(), 1)
    f = 2 * tp_p * tp_r / max(tp_p + tp_r, 1e-9)
    either = mt | mr
    cd = float(np.abs(ct - cr)[either].mean() * 255) if either.any() else 0.0
    def blocks(x):
        h, w = (x.shape[0] // 8) * 8, (x.shape[1] // 8) * 8
        return x[:h, :w].reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2).reshape(-1, 64)
    A, B = blocks(lt), blocks(lr)
    ma, mb = A.mean(1), B.mean(1)
    va, vb = A.var(1), B.var(1)
    cov = ((A - ma[:, None]) * (B - mb[:, None])).mean(1)
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    ssim = float((((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))).mean())
    return {"lit_f": round(float(f), 3), "lit_precision": round(float(tp_p), 3), "lit_recall": round(float(tp_r), 3),
            "colour_delta": round(cd, 1), "ssim": round(ssim, 3), "lit_px": [int(mt.sum()), int(mr.sum())]}


# ---------------------------------------------------------------------- the X_ITE look, baked (any engine)
# write_glb() keeps the shaders' data for custom materials (G2); write_glb_xite() bakes what X_ITE's shaders draw into
# plain textures, so Blender, Babylon.js and Unreal all load the same look with no custom shader: per-facet FacetShader
# colours (an atlas), the EyeShader's iris (a texture), the LineShader / PointShader core colours (atlases), a near-black
# sky and faint membranes. Only TEXCOORD_0 is written, so every engine reads every attribute.
XITE = {"facets": {"base_k": 0.5, "metal": 0.55, "rough": 0.32, "emissive_k": 0.6, "strength": 1.0},
        "ribbons": {"strength": 3.0, "core": 0.45, "whiten": 0.35},
        "points": {"strength": 4.0, "core": 0.3, "max_d": 0.35},
        "eye": {"strength": 2.0},
        "membrane": {"alpha": 0.03, "emissive_k": 0.4},
        "sky": {"emissive": (0.012, 0.010, 0.009)}}


def _atlas(cols, cell=4):
    """One flat cell per item: an RGBA uint8 image and each item's UV (the cell centre; sample it NEAREST)."""
    cols = np.clip(np.asarray(cols, float), 0, 1)
    if cols.shape[1] == 3:
        cols = np.concatenate([cols, np.ones((len(cols), 1))], 1)
    n = max(1, len(cols))
    side = int(math.ceil(math.sqrt(n)))
    size = 1 << int(math.ceil(math.log2(max(4, side * cell))))
    img = np.zeros((size, size, 4), np.uint8)
    uv = np.zeros((n, 2))
    for i, c in enumerate(cols):
        r, q = divmod(i, side)
        img[r * cell:(r + 1) * cell, q * cell:(q + 1) * cell] = np.round(c * 255)
        uv[i] = ((q + 0.5) * cell / size, (r + 0.5) * cell / size)
    return img, uv


def _bird_centre(bake):
    lo, hi = bounds(bake)
    return (np.asarray(lo) + np.asarray(hi)) / 2


def xite_facet_colours(bake, view=None):
    """Per facet (all facet parts, in order): the FacetShader's colour at its centroid. view=None: seen head-on (V = N,
    lights and positions in world space), one look for every camera; view=<viewpoint>: exactly as from that camera."""
    uni = bake["uniforms"]
    centre = _bird_centre(bake)
    if view is not None:
        vp = bake["viewpoints"][view] if isinstance(view, str) else view
        Vm = camera(vp, 16, 9)[0]
        L4 = [apply(Vm, np.asarray(uni["light%d" % k], float)) for k in range(4)]
    else:
        Vm = None
        L4 = [np.asarray(uni["light%d" % k], float) for k in range(4)]
    out = []
    for p in bake["parts"]:
        if p["role"] != "facets":
            continue
        for k in range(len(p["world"])):
            W = p["world"][k]
            c = W.mean(0)
            N = np.cross(W[1] - W[0], W[2] - W[0])
            N = N / (np.linalg.norm(N) or 1)
            if (N @ (c - centre)) < 0:
                N = -N
            if Vm is None:
                Vd, vview = N, c
            else:
                PV = apply(Vm, W)
                vview = PV.mean(0)
                Nv = np.cross(PV[1] - PV[0], PV[2] - PV[0])
                Nv = Nv / (np.linalg.norm(Nv) or 1)
                Vd = -vview / (np.linalg.norm(vview) or 1)
                N = Nv if Nv @ Vd >= 0 else -Nv
            col, a = _facet_colour(p["rgba"][k], N[None], Vd[None], vview[None], p["local"][k].mean(0)[None], L4, uni)
            out.append(np.concatenate([col[0], [float(np.asarray(a).reshape(-1)[0])]]))
    return np.array(out) if out else np.zeros((0, 4))


def xite_eye_texture(bake, size=512):
    """The EyeShader over the eye's local x, y box: (RGBA float image, (x0, y0, x1, y1))."""
    eyes = [p for p in bake["parts"] if p["role"] == "eye"]
    if not eyes:
        return None, None
    loc = np.concatenate([p["local"].reshape(-1, 3) for p in eyes])
    x0, y0 = loc[:, 0].min(), loc[:, 1].min()
    x1, y1 = loc[:, 0].max(), loc[:, 1].max()
    ys, xs = np.mgrid[0:size, 0:size]
    vP = np.stack([x0 + (xs + 0.5) / size * (x1 - x0), y1 - (ys + 0.5) / size * (y1 - y0)], -1)
    col, a = _eye_colour(vP, bake["uniforms"], px_size=max(x1 - x0, y1 - y0) / size)
    return np.concatenate([col, a[..., None]], -1), (x0, y0, x1, y1)


def xite_ribbon_colours(bake, whiten=0.0):
    """Per segment: the LineShader's core emission at the frozen moment (no pulse in flight). whiten: how far toward its
    own peak (white-hot) the colour goes; X_ITE's additive core and halo saturate a bright line toward white."""
    glow = bake["uniforms"]["glow"]
    out = []
    for p in bake["parts"]:
        if p["role"] != "ribbons":
            continue
        for k in range(len(p["a"])):
            vc = np.asarray(p["rgba"][k][:3], float)
            loop = float(p["loop"][k])
            em = vc * 1.1 * (1 + 0.7 * loop) + np.asarray(p["pool"][k], float) * 0.72 + np.array([1.0, 0.74, 0.38]) * p["scan"][k] * 1.3 * glow
            out.append(em + (em.max() - em) * whiten)
    return np.array(out) if out else np.zeros((0, 3))


def xite_point_colours(bake):
    """Per visible point: the PointShader's core colour times its brightness."""
    glow = bake["uniforms"]["glow"]
    out = []
    for p in bake["parts"]:
        if p["role"] != "points":
            continue
        for k in range(len(p["p"])):
            if not p["visible"][k] or p["diameter"][k] <= 0:
                continue
            vc = np.asarray(p["colour"][k], float)
            tw = p["twinkle"][k]
            vB = tw * p["pool_gain"][k] * (1.0 + (glow - 1.0) * 0.5)
            out.append((vc + (1.0 - vc) * 0.6) * 1.3 * max(vB, 0.35))
    return np.array(out) if out else np.zeros((0, 3))


def _png_bytes(img):
    import io
    from PIL import Image
    a = np.asarray(img)
    if a.dtype != np.uint8:
        a = np.round(np.clip(a, 0, 1) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(a, "RGBA" if a.shape[-1] == 4 else "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _norm_rows(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def xite_meshes(bake, ref_dist=8.0, tube_sides=8, sphere_sub=2, sky_sub=3, look=None, view=None):
    """(meshes, textures): meshes as write_glb's (positions, normals, indices, one UV set), textures as RGBA arrays."""
    L = {k: dict(v, **((look or {}).get(k) or {})) for k, v in XITE.items()}
    fcol = xite_facet_colours(bake, view)
    rcol = xite_ribbon_colours(bake, L["ribbons"].get("whiten", 0.0))
    pcol = xite_point_colours(bake)
    t_fac, uv_fac = _atlas(fcol[:, :3]) if len(fcol) else (None, None)
    t_rib, uv_rib = _atlas(rcol / max(1.0, rcol.max() if len(rcol) else 1.0)) if len(rcol) else (None, None)
    t_pts, uv_pts = _atlas(pcol / max(1.0, pcol.max() if len(pcol) else 1.0)) if len(pcol) else (None, None)
    t_eye, eye_box = xite_eye_texture(bake)
    centre = _bird_centre(bake)
    ico_v, ico_f = _icosphere(sphere_sub)
    out = []
    fi = ri = pi = 0
    for p in bake["parts"]:
        role = p["role"]
        P, N, I, UV = [], [], [], []
        base = 0
        def add(pos, nrm, idx, uv):
            nonlocal base
            P.append(pos)
            N.append(nrm)
            I.append(idx + base)
            UV.append(np.broadcast_to(np.asarray(uv, float), (len(pos), 2)))
            base += len(pos)
        if role == "facets":
            W = p["world"]
            nrm = _norm_rows(np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0]))
            flip = ((W.mean(1) - centre) * nrm).sum(-1) < 0
            nrm[flip] *= -1
            n = len(W)
            add(W.reshape(-1, 3), np.repeat(nrm, 3, 0), np.arange(3 * n), np.repeat(uv_fac[fi:fi + n], 3, 0))
            fi += n
        elif role == "eye":
            W, Lc = p["world"], p["local"].reshape(-1, 3)
            nrm = _norm_rows(np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0]))
            x0, y0, x1, y1 = eye_box
            uv = np.stack([(Lc[:, 0] - x0) / max(x1 - x0, 1e-9), (y1 - Lc[:, 1]) / max(y1 - y0, 1e-9)], -1)
            add(W.reshape(-1, 3), np.repeat(nrm, 3, 0), np.arange(len(Lc)), uv)
        elif role == "membrane":
            W = p["world"]
            nrm = _norm_rows(np.cross(W[:, 1] - W[:, 0], W[:, 2] - W[:, 0]))
            add(W.reshape(-1, 3), np.repeat(nrm, 3, 0), np.arange(3 * len(W)), (0.5, 0.5))
        elif role == "ribbons":
            for k in range(len(p["a"])):
                r = ribbon_radius(p["width_a"][k], p["bloom"][k], ref_dist) * L["ribbons"]["core"]
                pos, nrm, idx = _ring(p["a"][k], p["b"][k], r, tube_sides)
                add(pos, nrm, idx, uv_rib[ri])
                ri += 1
        elif role == "points":
            for k in range(len(p["p"])):
                if not p["visible"][k] or p["diameter"][k] <= 0:
                    continue
                d = min(p["diameter"][k] * L["points"]["core"], L["points"]["max_d"])
                add(p["p"][k] + ico_v * (d / 2), ico_v, ico_f, uv_pts[pi])
                pi += 1
        elif role == "sky":
            v, f = _icosphere(sky_sub)
            add(p["centre"] + v * p["radius"], -v, f, (0.5, 0.5))
        if not P:
            continue
        out.append({"name": p["name"], "role": role, "positions": np.concatenate(P), "normals": np.concatenate(N),
                    "indices": np.concatenate(I).astype(np.uint32), "uvs": [np.concatenate(UV)]})
    tex = {"facets": t_fac, "ribbons": t_rib, "points": t_pts, "eye": t_eye}
    stats = {"facets": len(fcol), "ribbons": len(rcol), "points": len(pcol), "eye_box": eye_box,
             "ribbon_peak": float(rcol.max()) if len(rcol) else 0.0, "point_peak": float(pcol.max()) if len(pcol) else 0.0}
    return out, tex, L, stats


def write_glb_xite(bake, path, ref_dist=8.0, tube_sides=8, sphere_sub=2, look=None, view=None, gain=1.0) -> dict:
    """The Gobbler as X_ITE draws it, baked into a plain glTF any engine renders (see XITE). gain scales every emissive."""
    parts, tex, L, stats = xite_meshes(bake, ref_dist, tube_sides, sphere_sub, look=look, view=view)
    bin_ = bytearray()
    views, accs, images, textures, gmeshes, nodes, mats = [], [], [], [], [], [], []
    samplers = [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071},       # atlases: NEAREST
                {"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}]       # the eye: LINEAR

    def view_of(b, target=None):
        nonlocal bin_
        while len(bin_) % 4:
            bin_ += b"\0"
        v = {"buffer": 0, "byteOffset": len(bin_), "byteLength": len(b)}
        bin_ += b
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    def acc(arr, ctype, typ, minmax=False):
        a = {"bufferView": view_of(arr.tobytes(), 34963 if ctype == 5125 else 34962), "componentType": ctype, "count": len(arr), "type": typ}
        if minmax:
            a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1

    tex_index = {}
    for role, img in tex.items():
        if img is None:
            continue
        images.append({"bufferView": view_of(_png_bytes(img)), "mimeType": "image/png", "name": "T_%s" % role})
        textures.append({"source": len(images) - 1, "sampler": 1 if role == "eye" else 0, "name": "T_%s" % role})
        tex_index[role] = len(textures) - 1

    def material(role):
        es = lambda s: {"KHR_materials_emissive_strength": {"emissiveStrength": float(s) * gain}}
        m = {"name": "GOB_%s" % role, "doubleSided": True, "extras": {"x3dlab_role": role, "look": "xite"}}
        if role == "facets":
            f = L["facets"]
            m.update(pbrMetallicRoughness={"baseColorTexture": {"index": tex_index["facets"]}, "baseColorFactor": [f["base_k"]] * 3 + [1.0],
                                           "metallicFactor": f["metal"], "roughnessFactor": f["rough"]},
                     emissiveTexture={"index": tex_index["facets"]}, emissiveFactor=[f["emissive_k"]] * 3, extensions=es(f["strength"]))
        elif role in ("ribbons", "points"):
            m.update(pbrMetallicRoughness={"baseColorFactor": [0, 0, 0, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0},
                     emissiveTexture={"index": tex_index[role]}, emissiveFactor=[1, 1, 1], extensions=es(L[role]["strength"]))
        elif role == "eye":
            m.update(alphaMode="BLEND", pbrMetallicRoughness={"baseColorTexture": {"index": tex_index["eye"]}, "baseColorFactor": [0, 0, 0, 1],
                                                              "metallicFactor": 0.0, "roughnessFactor": 1.0},
                     emissiveTexture={"index": tex_index["eye"]}, emissiveFactor=[1, 1, 1], extensions=es(L["eye"]["strength"]))
        elif role == "membrane":
            mp = next((p for p in bake["parts"] if p["role"] == "membrane" and p.get("rgba") is not None), None)
            rgb = (mp["rgba"][:, :3].mean(0) if mp is not None else np.ones(3)) * L["membrane"]["emissive_k"]
            m.update(alphaMode="BLEND", pbrMetallicRoughness={"baseColorFactor": [0, 0, 0, L["membrane"]["alpha"]], "metallicFactor": 0.0,
                                                              "roughnessFactor": 1.0},
                     emissiveFactor=[float(x) for x in np.clip(rgb, 0, 1)], extensions=es(1.0))
        else:                                                                       # sky
            m.update(pbrMetallicRoughness={"baseColorFactor": [0, 0, 0, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0},
                     emissiveFactor=list(L["sky"]["emissive"]), extensions=es(1.0))
        return m

    roles = sorted({m["role"] for m in parts})
    mats = [material(r) for r in roles]
    for m in parts:
        attrs = {"POSITION": acc(m["positions"].astype(np.float32), 5126, "VEC3", True),
                 "NORMAL": acc(m["normals"].astype(np.float32), 5126, "VEC3"),
                 "TEXCOORD_0": acc(m["uvs"][0].astype(np.float32), 5126, "VEC2")}
        gmeshes.append({"name": "GOB_%s_%s" % (m["role"], m["name"]),
                        "primitives": [{"attributes": attrs, "indices": acc(m["indices"], 5125, "SCALAR"), "material": roles.index(m["role"])}]})
        nodes.append({"name": "GOB_%s_%s" % (m["role"], m["name"]), "mesh": len(gmeshes) - 1})
    while len(bin_) % 4:
        bin_ += b"\0"
    doc = {"asset": {"version": "2.0", "generator": "x3dlab gobbler baker (X_ITE look)"},
           "extensionsUsed": ["KHR_materials_emissive_strength"], "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
           "nodes": nodes, "meshes": gmeshes, "materials": mats, "textures": textures, "images": images, "samplers": samplers,
           "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}],
           "extras": {"x3dlab": {"source": bake["source"], "look": "xite", "view": view if isinstance(view, str) else None,
                                 "gain": gain, "settings": L, "stats": {k: v for k, v in stats.items() if k != "eye_box"}}}}
    js = json.dumps(doc, separators=(",", ":"), default=float).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    blob = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)) + struct.pack("<II", len(js), 0x4E4F534A) + js \
        + struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    Path(path).write_bytes(blob)
    return {"path": str(path), "bytes": len(blob), "vertices": int(sum(len(m["positions"]) for m in parts)),
            "triangles": int(sum(len(m["indices"]) // 3 for m in parts)), "textures": {r: list(t.shape[:2]) for r, t in tex.items() if t is not None},
            **stats}
