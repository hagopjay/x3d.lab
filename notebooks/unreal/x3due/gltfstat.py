"""What is inside a glTF 2.0 file (.glb or .gltf), counted the same way as the X3D manifest and the Unreal report.

    s = gltfstat.stats("scene.glb")
    s["counts"]  # nodes, mesh_nodes, meshes, shared_meshes, primitives, triangles, materials, textures, images, cameras, lights
    s["shape_centres"]  # world-space centres of every mesh node's bounding box (glTF space: Y up, metres)

Standard library only. Triangles come from index (or POSITION) accessor counts and the primitive mode.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from . import convert as C

MODES = {0: "points", 1: "lines", 2: "line_loop", 3: "line_strip", 4: "triangles", 5: "triangle_strip", 6: "triangle_fan"}


def load(path):
    p = Path(path)
    data = p.read_bytes()
    if data[:4] == b"glTF":
        _, version, length = struct.unpack_from("<4sII", data, 0)
        off, js, binary = 12, None, None
        while off < min(length, len(data)):
            clen, ctype = struct.unpack_from("<I4s", data, off)
            chunk = data[off + 8: off + 8 + clen]
            if ctype == b"JSON":
                js = json.loads(chunk.decode("utf-8"))
            elif ctype == b"BIN\x00":
                binary = chunk
            off += 8 + clen
        if js is None:
            raise ValueError("GLB without a JSON chunk")
        return js, {"container": "glb", "version": version, "bin_bytes": len(binary or b""), "bytes": len(data)}
    return json.loads(data.decode("utf-8")), {"container": "gltf", "bytes": len(data)}


def _node_matrix(n):
    if "matrix" in n:
        m = n["matrix"]                                   # column-major
        return tuple(tuple(m[c * 4 + r] for c in range(4)) for r in range(4))
    t = n.get("translation", [0, 0, 0])
    q = n.get("rotation", [0, 0, 0, 1])
    s = n.get("scale", [1, 1, 1])
    x, y, z, w = q
    r3 = ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
          (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
          (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))
    return C.mat4_mul(C.mat4_from3(r3, t), C.mat4_scale(s))


def _tris(prim, accessors):
    mode = prim.get("mode", 4)
    if "indices" in prim:
        n = accessors[prim["indices"]]["count"]
    else:
        pos = prim.get("attributes", {}).get("POSITION")
        n = accessors[pos]["count"] if pos is not None else 0
    if mode == 4:
        return n // 3
    if mode in (5, 6):
        return max(0, n - 2)
    return 0


def stats(path):
    js, info = load(path)
    nodes, meshes, acc = js.get("nodes", []), js.get("meshes", []), js.get("accessors", [])
    scenes = js.get("scenes", [])
    roots = scenes[js.get("scene", 0)]["nodes"] if scenes else [i for i in range(len(nodes))]
    world = {}

    def walk(i, m, depth=0):
        if depth > 256 or i in world:
            return
        world[i] = C.mat4_mul(m, _node_matrix(nodes[i]))
        for c in nodes[i].get("children", []):
            walk(c, world[i], depth + 1)

    for r in roots:
        walk(r, C.mat4_identity())
    mesh_users = {}
    centres, prim_modes, tri_total = [], {}, 0
    for i, n in enumerate(nodes):
        if "mesh" not in n or i not in world:
            continue
        mi = n["mesh"]
        mesh_users[mi] = mesh_users.get(mi, 0) + 1
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for p in meshes[mi].get("primitives", []):
            tri_total += _tris(p, acc)
            prim_modes[MODES.get(p.get("mode", 4), "?")] = prim_modes.get(MODES.get(p.get("mode", 4), "?"), 0) + 1
            a = acc[p["attributes"]["POSITION"]] if "POSITION" in p.get("attributes", {}) else None
            if a and "min" in a and "max" in a:
                lo = [min(lo[k], a["min"][k]) for k in range(3)]
                hi = [max(hi[k], a["max"][k]) for k in range(3)]
        if lo[0] < math.inf:
            from .x3dscene import transform_bounds
            b = transform_bounds(world[i], (tuple(lo), tuple(hi)))
            centres.append(tuple((b[0][k] + b[1][k]) / 2 for k in range(3)))
    lights_ext = js.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    cams = []
    for i, n in enumerate(nodes):
        if "camera" in n and i in world:
            c = js["cameras"][n["camera"]]
            cams.append({"node": n.get("name"), "type": c.get("type"), "yfov": c.get("perspective", {}).get("yfov"),
                         "aspect": c.get("perspective", {}).get("aspectRatio"), "position": C.mat4_point(world[i], (0, 0, 0))})
    light_nodes = [{"node": n.get("name"), **lights_ext[n["extensions"]["KHR_lights_punctual"]["light"]]}
                   for n in nodes if "KHR_lights_punctual" in n.get("extensions", {})
                   and n["extensions"]["KHR_lights_punctual"].get("light", -1) < len(lights_ext)]
    counts = {
        "nodes": len(nodes), "mesh_nodes": sum(mesh_users.values()), "meshes": len(meshes),
        "shared_meshes": sum(1 for v in mesh_users.values() if v > 1),
        "mesh_nodes_on_shared_meshes": sum(v for v in mesh_users.values() if v > 1),
        "primitives": sum(len(m.get("primitives", [])) for m in meshes), "triangles": tri_total,
        "materials": len(js.get("materials", [])), "textures": len(js.get("textures", [])), "images": len(js.get("images", [])),
        "cameras": len(cams), "lights": len(light_nodes), "animations": len(js.get("animations", [])),
    }
    return {"info": info, "counts": counts, "extensions_used": sorted(js.get("extensionsUsed", [])),
            "primitive_modes": prim_modes, "cameras": cams, "lights": light_nodes, "shape_centres": centres,
            "generator": js.get("asset", {}).get("generator")}
