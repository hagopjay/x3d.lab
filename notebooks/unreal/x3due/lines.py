"""Lines and points that Unreal can see: glTF LINES, LINE_LOOP, LINE_STRIP and POINTS primitives become thin tubes and
small spheres (TRIANGLES) in an emissive-only material.

Unreal's glTF importer imports triangles only, so an X3D IndexedLineSet, LineSet or PointSet that reaches Unreal as a
glTF line or point primitive is dropped without a word (the x3d.demo scenes hold 458 IndexedLineSets and 110
PointSets). X_ITE and Babylon.js draw lines and points natively: only the copy for Unreal is changed.

    r = lines.radius_for(manifest, px=1.5, height=1080)            # metres: px pixels at the median viewpoint's distance
    rep = lines.thicken("scene.glb", "scene_unreal.glb", r, strength=K)
    rep -> {"changed": True, "segments": 512, "points": 978, "primitives": 7, "materials": 9, "colours": 9,
            "translucent": 2, "radius": 0.0021, ...}

X3D draws lines and points unlit, one pixel wide: in the colours of their Color or ColorRGBA node when they have one,
else in the Material's emissiveColor (X3D 4.0, 11.4.5 and 17.2.2.3). The tubes keep that. A primitive's COLOR_0 (the
Color node) colours each point and each line end, else the material does: an unlit material's base colour (what the
X3D add-on writes), else the emissive colour. All colours of a glb share at most MAX_COLOURS emissive-only materials
(black base, no specular, the colour as emissiveFactor x KHR_materials_emissive_strength `strength`, the notebook's
exposure constant K, so the colour lands on screen as X3D draws it), alphaMode BLEND when a ColorRGBA or transparency
asks for it. A segment between two colours is split in the middle. The width is a world-space radius chosen so the
tubes are `px` pixels wide from the scene's viewpoints at `height` pixels: thinner close up, the way a Lumen scene
needs geometry.
Standard library and numpy.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np

LINE_MODES = {1: "lines", 2: "line_loop", 3: "line_strip"}
POINT_MODE = 0
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NORM = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
MAX_SPHERE_POINTS = 200_000          # above this a point becomes an octahedron (6 vertices) instead of an icosphere (42)


# ---------------------------------------------------------------------- glb in and out
def read_glb(path) -> tuple[dict, bytearray]:
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("%s is not a .glb (binary glTF)" % path)
    _, _version, length = struct.unpack_from("<4sII", data, 0)
    off, js, binary = 12, None, bytearray()
    while off < min(length, len(data)):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == b"BIN\x00":
            binary = bytearray(chunk)
        off += 8 + clen
    if js is None:
        raise ValueError("GLB without a JSON chunk")
    return js, binary


def write_glb(path, js: dict, binary: bytes) -> int:
    binary = bytes(binary) + b"\x00" * (-len(binary) % 4)
    if binary:
        js.setdefault("buffers", [{}])[0]["byteLength"] = len(binary)        # before the JSON is written
    elif js.get("buffers"):
        js["buffers"] = [b for b in js["buffers"][1:]]                        # an empty buffer is not valid glTF
    body = json.dumps(js, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    total = 12 + 8 + len(body) + (8 + len(binary) if binary else 0)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(body), b"JSON") + body
    if binary:
        out += struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    Path(path).write_bytes(out)
    return len(out)


def read_accessor(js: dict, binary: bytes, index: int) -> np.ndarray:
    """(count, components) float32 for float or normalised data, int64 for indices; sparse accessors are refused."""
    acc = js["accessors"][index]
    if "sparse" in acc:
        raise ValueError("sparse accessor %d is not supported" % index)
    n, comps, ctype = acc["count"], COMPONENTS[acc["type"]], acc["componentType"]
    dtype = np.dtype(DTYPES[ctype])
    if "bufferView" not in acc:
        return np.zeros((n, comps), np.float32)
    view = js["bufferViews"][acc["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ValueError("accessor %d is not in the GLB's own buffer" % index)
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    item = dtype.itemsize * comps
    stride = view.get("byteStride") or item
    raw = np.frombuffer(bytes(binary), np.uint8, count=stride * (n - 1) + item if n else 0, offset=start) if n else np.zeros(0, np.uint8)
    if n == 0:
        return np.zeros((0, comps), np.float32)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(n, item), strides=(stride, 1))
    out = np.frombuffer(rows.copy().tobytes(), dtype).reshape(n, comps)
    if ctype == 5126:
        return out.astype(np.float32)
    if acc.get("normalized"):
        return (out.astype(np.float32) / NORM[ctype]).clip(-1.0, 1.0)
    return out.astype(np.int64)


class _Writer:
    """Appends arrays to the GLB's buffer as new bufferViews and accessors."""

    def __init__(self, js: dict, binary: bytearray):
        self.js, self.bin = js, binary
        js.setdefault("bufferViews", [])
        js.setdefault("accessors", [])
        if not js.get("buffers"):
            js["buffers"] = [{"byteLength": 0}]

    def add(self, arr: np.ndarray, kind: str, target: int | None, minmax=False) -> int:
        self.bin.extend(b"\x00" * (-len(self.bin) % 4))
        ctype = 5125 if arr.dtype.kind in "ui" else 5126
        arr = np.ascontiguousarray(arr, np.uint32 if ctype == 5125 else np.float32)
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": arr.nbytes}
        if target:
            view["target"] = target
        self.bin.extend(arr.tobytes())
        self.js["bufferViews"].append(view)
        acc = {"bufferView": len(self.js["bufferViews"]) - 1, "componentType": ctype, "count": int(arr.shape[0]), "type": kind}
        if minmax:
            acc["min"] = [float(x) for x in arr.min(axis=0)]
            acc["max"] = [float(x) for x in arr.max(axis=0)]
        self.js["accessors"].append(acc)
        return len(self.js["accessors"]) - 1


# ---------------------------------------------------------------------- geometry
def segments_of(mode: int, idx: np.ndarray) -> np.ndarray:
    """(k, 2) vertex pairs for glTF LINES (1), LINE_LOOP (2) and LINE_STRIP (3)."""
    idx = np.asarray(idx, np.int64).ravel()
    if mode == 1:
        idx = idx[: len(idx) // 2 * 2]
        return idx.reshape(-1, 2)
    if len(idx) < 2:
        return np.zeros((0, 2), np.int64)
    pairs = np.stack([idx[:-1], idx[1:]], axis=1)
    if mode == 2:
        pairs = np.vstack([pairs, [[idx[-1], idx[0]]]])
    return pairs


def _frames(d: np.ndarray):
    """Two unit vectors perpendicular to each direction in d (k, 3)."""
    ref = np.where(np.abs(d[:, 1:2]) < 0.9, np.array([[0.0, 1.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]))
    u = np.cross(d, ref)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
    v = np.cross(d, u)
    return u, v


def tubes(p0: np.ndarray, p1: np.ndarray, radius: float, sides: int = 6):
    """One open tube per segment (p0[i] -> p1[i]): vertices (2 * sides per segment), normals, triangles."""
    k = len(p0)
    if k == 0:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    d = p1 - p0
    length = np.linalg.norm(d, axis=1, keepdims=True)
    d = np.where(length > 1e-12, d / np.maximum(length, 1e-12), np.array([[0.0, 1.0, 0.0]]))
    u, v = _frames(d)
    ang = 2 * np.pi * np.arange(sides) / sides
    ring = np.cos(ang)[None, :, None] * u[:, None, :] + np.sin(ang)[None, :, None] * v[:, None, :]    # (k, sides, 3)
    verts = np.concatenate([p0[:, None, :] + radius * ring, p1[:, None, :] + radius * ring], axis=1)  # (k, 2s, 3)
    normals = np.concatenate([ring, ring], axis=1)
    s = np.arange(sides)
    a, b = s, (s + 1) % sides
    quad = np.stack([np.stack([a, b, b + sides], 1), np.stack([a, b + sides, a + sides], 1)], 1).reshape(-1, 3)
    tris = quad[None, :, :] + (2 * sides * np.arange(k))[:, None, None]
    return verts.reshape(-1, 3), normals.reshape(-1, 3), tris.reshape(-1, 3)


def _icosphere(sub: int = 1):
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
         (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7),
         (9, 8, 1)]
    verts = [np.array(p, float) / np.linalg.norm(p) for p in v]
    for _ in range(sub):
        cache, nf = {}, []

        def mid(i, j):
            key = (min(i, j), max(i, j))
            if key not in cache:
                m = verts[i] + verts[j]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(verts, np.float32), np.array(f, np.int64)


_OCTA = (np.array([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], np.float32),
         np.array([(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)], np.int64))


def spheres(p: np.ndarray, radius: float, sub: int = 1):
    """A small sphere (an octahedron for very large point sets) around every point."""
    if len(p) == 0:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    unit, faces = _OCTA if len(p) > MAX_SPHERE_POINTS else _icosphere(sub)
    n = len(unit)
    verts = p[:, None, :] + radius * unit[None, :, :]
    tris = faces[None, :, :] + (n * np.arange(len(p)))[:, None, None]
    normals = np.broadcast_to(unit[None, :, :], verts.shape)
    return verts.reshape(-1, 3), np.ascontiguousarray(normals).reshape(-1, 3), tris.reshape(-1, 3)


# ---------------------------------------------------------------------- radius from the scene
def _viewing_distance(vp: dict, shape_centres: list, centre) -> float:
    """How far a Viewpoint looks: to its centerOfRotation (X3D's examine centre, in the viewpoint's own coordinates),
    else to the median shape centre, else to the bounds' centre. Both ends go through the viewpoint's world matrix."""
    from . import convert as C
    m = vp.get("world_matrix") or C.mat4_identity()
    eye = C.mat4_point(m, vp["position"])
    cor = vp.get("centerOfRotation")
    if cor is not None:
        d = math.dist(eye, C.mat4_point(m, cor))
        if d > 1e-3:
            return d
    if shape_centres:
        ds = sorted(math.dist(eye, c) for c in shape_centres)
        return max(1e-3, ds[len(ds) // 2])
    return max(1e-3, math.dist(eye, centre))


def radius_for(manifest: dict, px: float = 1.5, height: int = 1080, minimum: float = 1e-5) -> float:
    """Half the world width of `px` pixels for a frame `height` pixels high, at the median of the Viewpoints' pixel
    sizes. Each comes from how far that viewpoint looks (its centerOfRotation, else the median shape centre) and its
    fieldOfView, which X3D spans over the shorter side. One radius serves every viewpoint: the median keeps lines thin
    in close-ups without losing them far away. The bounds' centre is not used first: one far-off shape moves it (the
    Gobbler's pick marker parked 900 m below put it 420 m from every camera). Falls back to 0.1 % of the scene's size."""
    b = manifest.get("bounds") or ((-1, -1, -1), (1, 1, 1))
    centre = [(b[0][i] + b[1][i]) / 2 for i in range(3)]
    size = max(1e-6, math.dist(b[0], b[1]))
    vps = [v for v in manifest.get("viewpoints", []) if v.get("type", "Viewpoint") == "Viewpoint" and v.get("position")]
    if not vps:
        return max(minimum, 0.001 * size)
    shapes = [tuple(c) for c in manifest.get("shape_centres") or []]
    pixels = sorted(2 * _viewing_distance(v, shapes, centre) * math.tan((v.get("fieldOfView") or math.pi / 4) / 2)
                    / max(1, height) for v in vps)
    mid = len(pixels) // 2
    pixel = pixels[mid] if len(pixels) % 2 else (pixels[mid - 1] + pixels[mid]) / 2
    return max(minimum, 0.5 * px * pixel)


# ---------------------------------------------------------------------- the pass
MAX_COLOURS = 64        # line and point colours of one glb share at most this many emissive materials


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float64), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def srgb_to_linear(c):
    c = np.clip(np.asarray(c, np.float64), 0.0, None)
    return np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))


def _is_unlit(material: dict | None) -> bool:
    return "KHR_materials_unlit" in ((material or {}).get("extensions") or {})


def _base_rgba(material: dict | None) -> list:
    return list(((material or {}).get("pbrMetallicRoughness") or {}).get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0])


def _material_rgba(material: dict | None) -> list:
    """The colour and alpha X3D draws a line or point set in when it has no per-vertex colours: an unlit material's
    base colour (what the X3D add-on writes, from emissiveColor), else the emissive colour, else the base colour
    (lit-only content), else white. Alpha counts only when the material blends (glTF alphaMode BLEND)."""
    if not material:
        return [1.0, 1.0, 1.0, 1.0]
    base = _base_rgba(material)
    alpha = float(base[3]) if material.get("alphaMode") == "BLEND" else 1.0
    if _is_unlit(material):
        return [float(c) for c in base[:3]] + [alpha]
    em = list(material.get("emissiveFactor") or [0.0, 0.0, 0.0])
    strength = (material.get("extensions") or {}).get("KHR_materials_emissive_strength", {}).get("emissiveStrength", 1.0)
    if max(em) > 0:
        return [float(c) * strength for c in em] + [alpha]
    return [float(c) for c in base[:3]] + [alpha]


def _vertex_rgba(material: dict | None, col: np.ndarray) -> np.ndarray:
    """Per-vertex RGBA from COLOR_0: an X3D Color node, which replaces emissiveColor (X3D 4.0, 11.4.5), times an
    unlit material's base factor (glTF's rule). Alpha counts only when the material blends."""
    c = np.asarray(col, np.float64)
    if c.shape[1] == 3:
        c = np.hstack([c, np.ones((len(c), 1))])
    if _is_unlit(material):
        c = c * np.asarray(_base_rgba(material), np.float64)[None, :]
    if not material or material.get("alphaMode") != "BLEND":
        c[:, 3] = 1.0
    return c


def palette(rgba: np.ndarray, limit: int = MAX_COLOURS):
    """(colours, index per row): at most `limit` RGBA colours (linear) for the rows. Rows are grouped by their
    sRGB-quantised colour (6 bits per channel, 5 for alpha, so dark colours keep their steps), the groups by a
    count-weighted median cut when there are more than `limit`; each colour is the mean of its rows."""
    rgba = np.asarray(rgba, np.float64).reshape(-1, 4)
    if not len(rgba):
        return np.zeros((0, 4)), np.zeros(0, np.int64)
    key = np.round(np.hstack([np.clip(linear_to_srgb(rgba[:, :3]), 0, 1) * 63, np.clip(rgba[:, 3:4], 0, 1) * 31]))
    uniq, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inverse = np.asarray(inverse).ravel()
    boxes = [np.arange(len(uniq))]
    while len(boxes) < limit:
        spans = [float(np.ptp(uniq[b], axis=0).max()) if len(b) > 1 else -1.0 for b in boxes]
        i = int(np.argmax(spans))
        if spans[i] <= 0:
            break
        b = boxes.pop(i)
        axis = int(np.argmax(np.ptp(uniq[b], axis=0)))
        order = b[np.argsort(uniq[b, axis], kind="stable")]
        w = np.cumsum(counts[order])
        cut = int(min(max(np.searchsorted(w, w[-1] / 2.0), 1), len(order) - 1))
        boxes += [order[:cut], order[cut:]]
    box_of = np.empty(len(uniq), np.int64)
    for k, b in enumerate(boxes):
        box_of[b] = k
    which = box_of[inverse]
    n = np.bincount(which, minlength=len(boxes)).astype(np.float64)
    colours = np.stack([np.bincount(which, weights=rgba[:, c], minlength=len(boxes)) for c in range(4)], 1) / n[:, None]
    return colours, which                    # each colour is the mean of its rows: exact when a box holds one colour


def _palette_material(js: dict, rgba, strength: float, cache: dict, k: int) -> int:
    """The emissive-only material of palette colour k: black base, no specular, the colour as emission x `strength`
    (KHR_materials_emissive_strength), alphaMode BLEND below alpha 1 (Unreal: translucent, emission x opacity)."""
    if k in cache:
        return cache[k]
    colour, alpha = [float(c) for c in rgba[:3]], float(rgba[3])
    peak = max(colour)
    m = {"name": "x3dlab_lines_%02d" % k,
         "pbrMetallicRoughness": {"baseColorFactor": [0.0, 0.0, 0.0, round(alpha, 4)], "metallicFactor": 0.0,
                                  "roughnessFactor": 1.0},
         "emissiveFactor": [min(1.0, c / peak) for c in colour] if peak > 0 else [0.0, 0.0, 0.0],
         "extensions": {"KHR_materials_specular": {"specularFactor": 0.0}}}
    if peak > 0:
        m["extensions"]["KHR_materials_emissive_strength"] = {"emissiveStrength": float(peak * strength)}
    if alpha < 0.999:
        m["alphaMode"] = "BLEND"
    used = js.setdefault("extensionsUsed", [])
    for ext in m["extensions"]:
        if ext not in used:
            used.append(ext)
    mats = js.setdefault("materials", [])
    mats.append(m)
    cache[k] = len(mats) - 1
    return cache[k]


def _mesh_scales(js: dict) -> dict:
    """mesh index -> the largest world scale of the first node that draws it (tube radii are in mesh space)."""
    from . import gltfstat
    nodes = js.get("nodes", [])
    scales = {}

    def walk(i, parent):
        n = nodes[i]
        m = _mat_mul(parent, gltfstat._node_matrix(n))
        if "mesh" in n and n["mesh"] not in scales:
            scales[n["mesh"]] = max(1e-9, max(math.sqrt(sum(m[r][c] ** 2 for r in range(3))) for c in range(3)))
        for ch in n.get("children", []):
            walk(ch, m)
    ident = tuple(tuple(1.0 if r == c else 0.0 for c in range(4)) for r in range(4))
    roots = (js.get("scenes") or [{}])[js.get("scene", 0)].get("nodes", list(range(len(nodes)))) if nodes else []
    for i in roots:
        walk(i, ident)
    return scales


def _mat_mul(a, b):
    return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)) for r in range(4))


def thicken(src, dst, radius: float, strength: float = 1.0, tube_sides: int = 6, sphere_sub: int = 1,
            point_scale: float = 1.25, max_colours: int = MAX_COLOURS) -> dict:
    """Write `dst`: `src` with every line and point primitive replaced by tubes and spheres. Other primitives, nodes,
    materials and extensions are kept. Returns what changed; `dst` is written even when nothing did.

    Colours: a primitive's COLOR_0 (an X3D Color node) gives each point and each line end its own colour, else the
    material's (_material_rgba). All colours of the glb share at most `max_colours` emissive materials (palette); a
    segment whose two ends differ is split in the middle, each half in its end's colour."""
    js, binary = read_glb(src)
    writer = _Writer(js, binary)
    rep = thicken_js(js, binary, writer, radius, strength, tube_sides, sphere_sub, point_scale, max_colours)
    rep["bytes"] = write_glb(dst, js, writer.bin)
    return rep


def thicken_js(js: dict, binary, writer: "_Writer", radius: float, strength: float = 1.0, tube_sides: int = 6,
               sphere_sub: int = 1, point_scale: float = 1.25, max_colours: int = MAX_COLOURS) -> dict:
    """thicken() on a glb already in memory (`writer` appends to its buffer); the caller writes it out."""
    rep = {"changed": False, "segments": 0, "points": 0, "primitives": 0, "materials": 0, "colours": 0,
           "translucent": 0, "radius": radius, "triangles_added": 0, "skipped": []}
    scales = _mesh_scales(js)
    mats = js.get("materials", [])
    found = []                                   # (mesh, primitive, mode, positions, indices, per-vertex RGBA or None, RGBA)
    for mi, mesh in enumerate(js.get("meshes", [])):
        for prim in mesh.get("primitives", []):
            mode = prim.get("mode", 4)
            if mode not in LINE_MODES and mode != POINT_MODE:
                continue
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                rep["skipped"].append("mesh %d: a %s primitive without POSITION" % (mi, LINE_MODES.get(mode, "points")))
                continue
            material = mats[prim["material"]] if "material" in prim and prim["material"] < len(mats) else None
            try:
                pos = read_accessor(js, binary, attrs["POSITION"]).astype(np.float64)
                idx = read_accessor(js, binary, prim["indices"]).ravel() if "indices" in prim else np.arange(len(pos))
                col = read_accessor(js, binary, attrs["COLOR_0"]) if "COLOR_0" in attrs else None
            except ValueError as exc:
                rep["skipped"].append("mesh %d: %s" % (mi, exc))
                continue
            idx = np.asarray(idx[(idx >= 0) & (idx < len(pos))], np.int64)
            per_vertex = _vertex_rgba(material, col) if col is not None and len(col) == len(pos) else None
            found.append((mi, prim, mode, pos, idx, per_vertex, _material_rgba(material)))
    rows = [f[5] if f[5] is not None else np.asarray([f[6]], np.float64) for f in found]
    colours, which = palette(np.vstack(rows) if rows else np.zeros((0, 4)), max_colours)
    cache, offset, replaced = {}, 0, {}
    for (mi, prim, mode, pos, idx, per_vertex, _rgba), row in zip(found, rows):
        k_of = which[offset:offset + len(row)]
        offset += len(row)
        k_vertex = k_of if per_vertex is not None else np.full(len(pos), k_of[0], np.int64)
        r_local = radius / scales.get(mi, 1.0)
        groups = {}                               # palette colour -> [(verts, normals, tris)]
        if mode == POINT_MODE:
            rep["points"] += int(len(idx))
            for k in np.unique(k_vertex[idx]):
                groups.setdefault(int(k), []).append(spheres(pos[idx[k_vertex[idx] == k]], r_local * point_scale, sphere_sub))
        else:
            pairs = segments_of(mode, idx)
            rep["segments"] += int(len(pairs))
            if len(pairs):
                k0, k1 = k_vertex[pairs[:, 0]], k_vertex[pairs[:, 1]]
                p0, p1 = pos[pairs[:, 0]], pos[pairs[:, 1]]
                mid = (p0 + p1) / 2.0
                same = k0 == k1
                for k in np.unique(np.concatenate([k0, k1])):
                    whole = same & (k0 == k)
                    first, second = ~same & (k0 == k), ~same & (k1 == k)
                    a = np.concatenate([p0[whole], p0[first], mid[second]])
                    b = np.concatenate([p1[whole], mid[first], p1[second]])
                    if len(a):
                        groups.setdefault(int(k), []).append(tubes(a, b, r_local, tube_sides))
        new_prims = []
        for k, parts in sorted(groups.items()):
            verts = np.concatenate([g[0] for g in parts])
            normals = np.concatenate([g[1] for g in parts])
            base, tris = 0, []
            for g in parts:
                tris.append(g[2] + base)
                base += len(g[0])
            tris = np.concatenate(tris)
            if not len(tris):
                continue
            new_prims.append({"attributes": {"POSITION": writer.add(verts.astype(np.float32), "VEC3", 34962, minmax=True),
                                             "NORMAL": writer.add(normals.astype(np.float32), "VEC3", 34962)},
                              "indices": writer.add(tris.astype(np.uint32).ravel()[:, None], "SCALAR", 34963), "mode": 4,
                              "material": _palette_material(js, colours[k], strength, cache, k),
                              "extras": {"x3dlab": "thickened %s" % ("points" if mode == POINT_MODE else LINE_MODES[mode])}})
            rep["triangles_added"] += int(len(tris))
        if not new_prims:
            rep["skipped"].append("mesh %d: an empty %s primitive" % (mi, LINE_MODES.get(mode, "points")))
            continue
        replaced[id(prim)] = new_prims
        rep["primitives"] += 1
        rep["changed"] = True
    for mesh in js.get("meshes", []):
        if any(id(p) in replaced for p in mesh.get("primitives", [])):
            mesh["primitives"] = [q for p in mesh["primitives"] for q in replaced.get(id(p), [p])]
    rep["materials"] = len(cache)
    rep["colours"] = len(colours)
    rep["translucent"] = sum(1 for k in cache if colours[k][3] < 0.999)
    rep["material_indices"] = sorted(cache.values())
    return rep
