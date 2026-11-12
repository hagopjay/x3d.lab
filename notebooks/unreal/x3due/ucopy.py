"""The glb Unreal is given: the Foundry's glb, plus what Unreal needs to draw the scene as X_ITE draws the X3D.

    rep = ucopy.unreal_copy("scene.glb", "scene_unreal.glb", manifest, k=LAB.mapping.k,
                            colour_space=LAB.mapping.colour_space, line_radius=lines.radius_for(manifest))

Three things, each optional:

* **Emission x K.** Unreal turns an emissive value E into the pixel E / K at the lab's exposure (convert.LightMapping),
  so an X3D emissiveColor or UnlitMaterial colour c, written into the glb as c, would land on screen at c / K.
  Every emissive material gets K as (more) KHR_materials_emissive_strength, as the backdrop and the line tubes do.
* **Lines and points** become tubes and spheres (lines.thicken_js): Unreal's importer drops them.
* **The Background's sky and ground gradient** (X3D 4.0, 24.4.1) becomes a dome around the scene: an emissive
  texture of the gradient by angle from the zenith, x K, on a sphere just inside the lab's flat backdrop sphere
  (same centre, 0.9 of its radius), so scene stills show the gradient and the key stills (the magenta backdrop)
  hide it like the backdrop (the import script tags the node SKY_NODE as x3dlab_backdrop). Only when the Background
  has a gradient or a ground; a single sky colour stays the flat backdrop.

Colours follow `colour_space` (convert.COLOUR_SPACES): "x_ite" when the add-on converted the classic X3D colours from
sRGB (X_ITE's own reading), "as_is" when it did not. The gradient texture is written so that either way it shows the
X3D values as the rest of the scene's colours do. Standard library and numpy.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np

from . import lines as L

SKY_NODE = "X3DLab_Sky"
SKY_ROWS = 512                 # texture rows from the zenith (top) to the nadir (bottom)


# ---------------------------------------------------------------------- the X3D Background gradient
def _colours(flat, n):
    vals = [float(x) for x in (flat or [])]
    return [tuple(vals[i:i + 3]) for i in range(0, len(vals) - len(vals) % 3, 3)][:n] if n else []


def has_gradient(background: dict | None) -> bool:
    """True when the Background draws more than one colour: a sky gradient or a ground."""
    if not background:
        return False
    sky = _colours(background.get("skyColor"), 10 ** 6)
    ground = _colours(background.get("groundColor"), 10 ** 6)
    return (len(sky) > 1 and bool(background.get("skyAngle"))) or (len(ground) > 1 and bool(background.get("groundAngle")))


def _ramp(theta, angles, colours):
    """Colour at `theta` (radians from the ramp's start): colours[0] at 0, colours[k + 1] at angles[k], linear in angle
    between, the last colour past the last angle."""
    if len(colours) == 1 or not angles:
        return colours[0]
    stops = [0.0] + [float(a) for a in angles[:len(colours) - 1]]
    if theta <= stops[0]:
        return colours[0]
    for k in range(1, len(stops)):
        if theta <= stops[k]:
            t = (theta - stops[k - 1]) / max(1e-12, stops[k] - stops[k - 1])
            return tuple(colours[k - 1][c] + t * (colours[k][c] - colours[k - 1][c]) for c in range(3))
    return colours[len(stops) - 1]


def gradient(background: dict, rows: int = SKY_ROWS) -> np.ndarray:
    """(rows, 3) X3D colour values from the zenith (row 0) to the nadir: the sky by skyColor / skyAngle (from the zenith,
    clamped to the last colour down to the nadir), then the ground by groundColor / groundAngle (from the nadir, nothing
    past the last groundAngle: the sky shows there). X3D 4.0, 24.4.1 Background."""
    sky = _colours(background.get("skyColor"), 10 ** 6) or [(0.0, 0.0, 0.0)]
    sky_angles = [float(a) for a in background.get("skyAngle") or []]
    ground = _colours(background.get("groundColor"), 10 ** 6)
    ground_angles = [min(math.pi / 2, float(a)) for a in background.get("groundAngle") or []][:max(0, len(ground) - 1)]
    reach = ground_angles[-1] if ground_angles else None        # no angle: no ground region (it ends at the nadir)
    out = np.zeros((rows, 3))
    for r in range(rows):
        theta = math.pi * r / (rows - 1)
        from_nadir = math.pi - theta
        if reach is not None and from_nadir <= reach + 1e-9:
            out[r] = _ramp(from_nadir, ground_angles, ground)
        else:
            out[r] = _ramp(theta, sky_angles, sky)
    return np.clip(out, 0.0, None)


def _png(rgb: np.ndarray) -> bytes:
    """An 8-bit RGB PNG of a (h, w, 3) array of 0..1 values, standard library only."""
    h, w, _ = rgb.shape
    data = np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def sky_texture(background: dict, colour_space: str = "x_ite", rows: int = SKY_ROWS, width: int = 4) -> bytes:
    """The gradient as an sRGB PNG (glTF reads emissive textures as sRGB). 'x_ite': the X3D values themselves, so the
    engine sees their linear values, as the add-on's converted colours. 'as_is': encoded so the engine sees the X3D
    values as linear numbers, as the unconverted colours elsewhere in the glb."""
    g = gradient(background, rows)
    if colour_space != "x_ite":
        g = L.linear_to_srgb(np.clip(g, 0.0, 1.0))
    return _png(np.repeat(g[:, None, :], width, axis=1))


def dome(radius: float, rings: int = 128, segments: int = 64):
    """A UV sphere seen from inside: positions, inward normals, UVs (u around, v = angle from the zenith / pi) and
    triangles, wound to face the centre."""
    th = np.linspace(0.0, math.pi, rings + 1)
    ph = np.linspace(0.0, 2 * math.pi, segments + 1)
    T, P = np.meshgrid(th, ph, indexing="ij")
    d = np.stack([np.sin(T) * np.cos(P), np.cos(T), -np.sin(T) * np.sin(P)], -1).reshape(-1, 3)
    uv = np.stack([P / (2 * math.pi), T / math.pi], -1).reshape(-1, 2)
    i = np.arange(rings)[:, None] * (segments + 1) + np.arange(segments)[None, :]
    a, b, c, e = i, i + 1, i + segments + 1, i + segments + 2
    tris = np.concatenate([np.stack([a, b, e], -1).reshape(-1, 3), np.stack([a, e, c], -1).reshape(-1, 3)])
    return (d * radius).astype(np.float32), (-d).astype(np.float32), uv.astype(np.float32), tris.astype(np.uint32)


def add_sky(js: dict, writer: "L._Writer", background: dict, centre, radius: float, k: float,
            colour_space: str = "x_ite", rings: int = 128, segments: int = 64) -> dict:
    """Append the gradient dome to a glb in memory: one image, sampler, texture, material, mesh and root node."""
    png = sky_texture(background, colour_space)
    writer.bin.extend(b"\x00" * (-len(writer.bin) % 4))
    js["bufferViews"].append({"buffer": 0, "byteOffset": len(writer.bin), "byteLength": len(png)})
    writer.bin.extend(png)
    js.setdefault("images", []).append({"bufferView": len(js["bufferViews"]) - 1, "mimeType": "image/png", "name": SKY_NODE})
    js.setdefault("samplers", []).append({"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071})
    js.setdefault("textures", []).append({"source": len(js["images"]) - 1, "sampler": len(js["samplers"]) - 1, "name": SKY_NODE})
    js.setdefault("materials", []).append({
        "name": SKY_NODE, "doubleSided": True,
        "pbrMetallicRoughness": {"baseColorFactor": [0.0, 0.0, 0.0, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0},
        "emissiveFactor": [1.0, 1.0, 1.0], "emissiveTexture": {"index": len(js["textures"]) - 1},
        "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": float(k)},
                       "KHR_materials_specular": {"specularFactor": 0.0}}})
    used = js.setdefault("extensionsUsed", [])
    for ext in ("KHR_materials_emissive_strength", "KHR_materials_specular"):
        if ext not in used:
            used.append(ext)
    pos, nrm, uv, tris = dome(radius, rings, segments)
    js.setdefault("meshes", []).append({"name": SKY_NODE, "primitives": [{
        "attributes": {"POSITION": writer.add(pos, "VEC3", 34962, minmax=True), "NORMAL": writer.add(nrm, "VEC3", 34962),
                       "TEXCOORD_0": writer.add(uv, "VEC2", 34962)},
        "indices": writer.add(tris.ravel()[:, None], "SCALAR", 34963), "mode": 4, "material": len(js["materials"]) - 1}]})
    js.setdefault("nodes", []).append({"name": SKY_NODE, "mesh": len(js["meshes"]) - 1,
                                       "translation": [float(c) for c in centre]})
    scenes = js.setdefault("scenes", [{"nodes": []}])
    scenes[js.get("scene", 0)].setdefault("nodes", []).append(len(js["nodes"]) - 1)
    return {"radius_m": round(float(radius), 3), "triangles": int(len(tris)), "png_bytes": len(png),
            "colours": [[round(float(x), 4) for x in row] for row in gradient(background, 5)]}


# ---------------------------------------------------------------------- emission
def scale_emission(js: dict, k: float, skip=()) -> int:
    """Multiply the emission of every emissive material (emissiveFactor above 0, or an emissiveTexture) by K, through
    KHR_materials_emissive_strength. `skip`: material indices that already carry K (the line tubes, the sky)."""
    n = 0
    for i, m in enumerate(js.get("materials") or []):
        if i in skip:
            continue
        if max(m.get("emissiveFactor") or [0.0]) <= 0 and "emissiveTexture" not in m:
            continue
        ext = m.setdefault("extensions", {}).setdefault("KHR_materials_emissive_strength", {})
        ext["emissiveStrength"] = float(ext.get("emissiveStrength", 1.0)) * float(k)
        n += 1
    if n:
        used = js.setdefault("extensionsUsed", [])
        if "KHR_materials_emissive_strength" not in used:
            used.append("KHR_materials_emissive_strength")
    return n


# ---------------------------------------------------------------------- the copy
def backdrop_sphere(manifest: dict) -> tuple:
    """The centre (X3D metres) and radius (metres) of the lab's flat backdrop sphere (project.placements)."""
    b = manifest.get("bounds") or ((-1, -1, -1), (1, 1, 1))
    centre = tuple((b[0][i] + b[1][i]) / 2 for i in range(3))
    radius = max(1.0, math.dist(b[0], b[1]) / 2)
    return centre, max(50.0, radius * 40)


def unreal_copy(src, dst, manifest: dict, k: float, colour_space: str = "x_ite", line_radius: float | None = None,
                thicken: bool = True, sky: bool = True, **thicken_options) -> dict:
    """Write `dst`, the glb Unreal imports. Returns what was done: emissive materials scaled, the line pass's report,
    the sky dome (or why not). `dst` is written even when nothing changed."""
    js, binary = L.read_glb(src)
    writer = L._Writer(js, binary)
    rep = {"k": float(k), "colour_space": colour_space}
    n_before = len(js.get("materials") or [])
    original = set(range(n_before))
    rep["emission_scaled"] = scale_emission(js, k, skip=())
    if thicken and line_radius:
        lr = L.thicken_js(js, binary, writer, line_radius, strength=k, **thicken_options)
        rep["lines"] = {key: v for key, v in lr.items() if key != "skipped"} | {"skipped": lr["skipped"][:5]}
    else:
        rep["lines"] = {"changed": False, "why": "off" if not thicken else "no radius"}
    bg = (manifest.get("backgrounds") or [None])[0]
    if sky and has_gradient(bg):
        centre, r_backdrop = backdrop_sphere(manifest)
        rep["sky"] = add_sky(js, writer, bg, centre, 0.9 * r_backdrop, k, colour_space)
    else:
        rep["sky"] = {"added": False, "why": "off" if not sky else ("no Background" if not bg else "one sky colour: the flat backdrop")}
    rep["materials_original"] = len(original)
    rep["bytes"] = L.write_glb(dst, js, writer.bin)
    rep["changed"] = bool(rep["emission_scaled"] or (rep["lines"] or {}).get("changed") or "radius_m" in rep["sky"])
    return rep
