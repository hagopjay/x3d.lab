"""Read a binary glTF (.glb) without dependencies and summarise what survived the trip."""
from __future__ import annotations

import json
import struct

MODES = {0: "POINTS", 1: "LINES", 2: "LINE_LOOP", 3: "LINE_STRIP", 4: "TRIANGLES", 5: "TRIANGLE_STRIP", 6: "TRIANGLE_FAN"}


def read_json(path: str) -> dict:
    with open(path, "rb") as f:
        head = f.read(12)
        magic, version, _length = struct.unpack("<4sII", head)
        if magic != b"glTF":
            raise ValueError("%s is not a .glb (magic %r)" % (path, magic))
        clen, ctype = struct.unpack("<I4s", f.read(8))
        if ctype != b"JSON":
            raise ValueError("first chunk is not JSON")
        return json.loads(f.read(clen))


def inspect(path: str) -> dict:
    import os
    g = read_json(path)
    modes: dict[str, int] = {}
    color0 = 0
    meshes = []
    for m in g.get("meshes", []):
        mm = {"name": m.get("name", ""), "primitives": []}
        for p in m.get("primitives", []):
            mode = MODES.get(p.get("mode", 4), str(p.get("mode")))
            modes[mode] = modes.get(mode, 0) + 1
            attrs = p.get("attributes", {})
            if "COLOR_0" in attrs:
                color0 += 1
            pos = g["accessors"][attrs["POSITION"]] if "POSITION" in attrs else {}
            mm["primitives"].append({"mode": mode, "count": pos.get("count"), "min": pos.get("min"), "max": pos.get("max"),
                                     "attributes": sorted(attrs), "material": p.get("material")})
        meshes.append(mm)
    mats = []
    for m in g.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        ext = m.get("extensions", {})
        mats.append({"name": m.get("name", ""), "baseColor": pbr.get("baseColorFactor"),
                     "metallic": pbr.get("metallicFactor", 1), "roughness": pbr.get("roughnessFactor", 1),
                     "emissive": m.get("emissiveFactor"), "alphaMode": m.get("alphaMode", "OPAQUE"),
                     "doubleSided": m.get("doubleSided", False), "extensions": sorted(ext)})
    return {
        "file": os.path.basename(path),
        "bytes": os.path.getsize(path),
        "generator": g.get("asset", {}).get("generator"),
        "extensionsUsed": sorted(g.get("extensionsUsed", [])),
        "nodes": len(g.get("nodes", [])),
        "meshes": len(meshes),
        "primitiveModes": modes,
        "primitivesWithColor0": color0,
        "materials": mats,
        "unlitMaterials": sum(1 for m in mats if "KHR_materials_unlit" in m["extensions"]),
        "animations": len(g.get("animations", [])),
        "animationChannels": sum(len(a.get("channels", [])) for a in g.get("animations", [])),
        "animatedNodes": len({c.get("target", {}).get("node") for a in g.get("animations", []) for c in a.get("channels", [])}),
        "cameras": len(g.get("cameras", [])),
        "lights": len(g.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])),
        "meshDetail": meshes,
    }
