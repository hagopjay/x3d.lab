"""X3D → Blender (x3d-blender-io) → glb, headless, through the ``bpy`` Python API. No MCP.

Run it with a Python that has ``bpy`` (the bpy wheel, or ``blender -b --python``):

    X3DLAB_ADDON=/path/to/x3d-blender-io/source \\
    python -m x3dlab.blender_leg scenes/orrery.x3d --out runs/x3d/orrery.glb --json runs/x3d/orrery.blender.json

For each scene it records what Blender built (objects, meshes, loose edges and points, smoothing, materials,
colour attributes, actions, the x3d_background property), cleans stray points the way the Foundry convert
does, exports a glb with lines and points kept, and inspects the glb (x3dlab.glb).

The add-on is registered the way its own tests do: ``source/`` exposed as the ``io_scene_x3d`` package.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def log(msg: str):
    print("[blender-leg] %s" % msg, flush=True)


def register_addon(source_dir: str):
    tmp = Path(tempfile.gettempdir()) / "x3dlab-addon"
    tmp.mkdir(exist_ok=True)
    pkg = tmp / "io_scene_x3d"
    if not pkg.exists():
        try:
            pkg.symlink_to(Path(source_dir).resolve(), target_is_directory=True)
        except OSError:
            import shutil
            shutil.copytree(source_dir, pkg)
    if str(tmp) not in sys.path:
        sys.path.insert(0, str(tmp))
    import importlib
    mod = importlib.import_module("io_scene_x3d")
    try:
        mod.register()
    except Exception:
        pass  # already registered in this interpreter
    return mod


def addon_version(source_dir: str) -> str:
    man = Path(source_dir) / "blender_manifest.toml"
    if man.exists():
        for line in man.read_text().splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def clean_stray_points(bpy):
    """Remove loose vertices of meshes that have edges or faces, and loose edges of meshes with faces
    (the Foundry convert's rule): a Coordinate shared between shapes otherwise leaves stray points."""
    import bmesh
    dropped = {"verts": 0, "edges": 0}
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        me = ob.data
        if not me.edges and not me.polygons:
            continue  # a point set: keep every point
        bm = bmesh.new()
        bm.from_mesh(me)
        if me.polygons:
            loose_e = [e for e in bm.edges if not e.link_faces]
            dropped["edges"] += len(loose_e)
            bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
        loose_v = [v for v in bm.verts if not v.link_edges]
        dropped["verts"] += len(loose_v)
        bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
        bm.to_mesh(me)
        bm.free()
    return dropped


def survey(bpy) -> dict:
    from mathutils import Vector
    objs = {}
    meshes = []
    for ob in bpy.data.objects:
        objs[ob.type] = objs.get(ob.type, 0) + 1
        if ob.type != "MESH":
            continue
        me = ob.data
        face_edges = set()
        for p in me.polygons:
            face_edges.update(p.edge_keys)
        loose_edges = sum(1 for e in me.edges if e.key not in face_edges)
        used = set()
        for e in me.edges:
            used.update(e.vertices)
        loose_verts = sum(1 for i in range(len(me.vertices)) if i not in used)
        zs = [(ob.matrix_world @ Vector(v.co)).z for v in me.vertices] if me.vertices else [0]
        smooth = sum(1 for p in me.polygons if p.use_smooth)
        meshes.append({
            "object": ob.name, "mesh": me.name, "verts": len(me.vertices), "edges": len(me.edges),
            "faces": len(me.polygons), "looseEdges": loose_edges, "looseVerts": loose_verts,
            "smoothFaces": smooth, "zRange": round(max(zs) - min(zs), 5),
            "colorAttributes": [a.name for a in getattr(me, "color_attributes", [])],
            "materials": [m.name for m in me.materials if m],
        })
    mats = []
    for m in bpy.data.materials:
        entry = {"name": m.name, "users": m.users}
        if m.use_nodes and m.node_tree:
            bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            emis_only = not bsdf and any(n.type == "EMISSION" for n in m.node_tree.nodes)
            if bsdf:
                bc = bsdf.inputs["Base Color"].default_value
                entry["baseColor"] = [round(x, 4) for x in bc[:3]]
                entry["roughness"] = round(bsdf.inputs["Roughness"].default_value, 4)
                entry["metallic"] = round(bsdf.inputs["Metallic"].default_value, 4)
                ec = bsdf.inputs.get("Emission Color")
                if ec is not None:
                    entry["emission"] = [round(x, 4) for x in ec.default_value[:3]]
                sil = bsdf.inputs.get("Specular IOR Level")
                if sil is not None:
                    entry["specularIorLevel"] = round(sil.default_value, 4)
            entry["unlitLike"] = bool(emis_only)
        mats.append(entry)
    scene = bpy.context.scene
    return {
        "objectsByType": objs,
        "meshes": meshes,
        "materials": mats,
        "actions": len(bpy.data.actions),
        "cameras": objs.get("CAMERA", 0),
        "lights": objs.get("LIGHT", 0),
        "fontObjects": objs.get("FONT", 0),
        "sceneProps": sorted(k for k in scene.keys() if k.startswith("x3d_")),
    }


def export_glb(bpy, out: str, lighting: str = "COMPAT") -> dict:
    """Export with lines and points kept. Blender 5.2 renamed export_loose_* to use_mesh_edges/use_mesh_vertices.

    Light units: COMPAT, the measured choice in the x3d.ai fidelity finding (SPEC overexposes X3D intensities)."""
    base = dict(filepath=out, export_format="GLB", export_cameras=True, export_lights=True,
                export_animations=True, export_extras=True, export_yup=True, export_apply=True,
                export_import_convert_lighting_mode=lighting)
    tried = []
    for extra in ({"use_mesh_edges": True, "use_mesh_vertices": True},
                  {"export_loose_edges": True, "export_loose_points": True}, {}):
        try:
            bpy.ops.export_scene.gltf(**base, **extra)
            return {"options": sorted(extra) or ["(no loose-geometry option)"], "lighting": lighting, "tried": tried}
        except TypeError as e:
            tried.append({"options": sorted(extra), "error": str(e)[:200]})
    raise RuntimeError("glTF export failed with every option set: %r" % tried)


def run(x3d_path: str, out_glb: str, *, addon: str, circle_divisions: int = 48, lighting: str = "COMPAT") -> dict:
    import bpy
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    register_addon(addon)
    log("importing %s (circle_divisions=%d)" % (os.path.basename(x3d_path), circle_divisions))
    warnings = []

    class Catch:
        def write(self, s):
            if "warn" in s.lower():
                warnings.append(s.strip()[:300])

    import logging
    handler = logging.StreamHandler(Catch())
    handler.setLevel(logging.WARNING)
    logging.getLogger().addHandler(handler)
    try:
        res = bpy.ops.import_scene.x3d(filepath=os.path.abspath(x3d_path), circle_divisions=circle_divisions)
    except TypeError:
        res = bpy.ops.import_scene.x3d(filepath=os.path.abspath(x3d_path))
    finally:
        logging.getLogger().removeHandler(handler)
    t_import = time.time() - t0
    before = survey(bpy)
    dropped = clean_stray_points(bpy)
    log("imported: %s objects, %d meshes, %d materials, %d actions in %.1f s" % (
        sum(before["objectsByType"].values()), len(before["meshes"]), len(before["materials"]), before["actions"], t_import))
    Path(out_glb).parent.mkdir(parents=True, exist_ok=True)
    exp = export_glb(bpy, out_glb, lighting)
    from .glb import inspect
    g = inspect(out_glb)
    log("exported %s: %d bytes, modes %s, %d animations" % (os.path.basename(out_glb), g["bytes"], g["primitiveModes"], g["animations"]))
    return {
        "scene": os.path.basename(x3d_path),
        "ok": "FINISHED" in res,
        "blender": bpy.app.version_string,
        "addon": {"path": os.path.abspath(addon), "version": addon_version(addon)},
        "circleDivisions": circle_divisions,
        "importSeconds": round(t_import, 2),
        "totalSeconds": round(time.time() - t0, 2),
        "importWarnings": warnings[:40],
        "survey": before,
        "droppedUnused": dropped,
        "export": exp,
        "glb": g,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("x3d", nargs="+")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--addon", default=os.environ.get("X3DLAB_ADDON"))
    ap.add_argument("--circle-divisions", type=int, default=48)
    ap.add_argument("--lighting", choices=["SPEC", "COMPAT", "RAW"], default="COMPAT", help="glTF light units")
    a = ap.parse_args(argv)
    if not a.addon:
        ap.error("set --addon or X3DLAB_ADDON to the add-on's source/ folder")
    results = []
    for path in a.x3d:
        stem = Path(path).stem
        out = os.path.join(a.out_dir, stem + ".glb")
        try:
            r = run(path, out, addon=a.addon, circle_divisions=a.circle_divisions, lighting=a.lighting)
        except Exception as e:  # one bad scene must not stop the batch
            import traceback
            r = {"scene": os.path.basename(path), "ok": False, "error": str(e), "trace": traceback.format_exc()[-2000:]}
            log("FAILED %s: %s" % (stem, e))
        Path(os.path.join(a.out_dir, stem + ".blender.json")).write_text(json.dumps(r, indent=1))
        results.append(r)
    print(json.dumps({"scenes": [{"scene": r["scene"], "ok": r.get("ok")} for r in results]}))
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
