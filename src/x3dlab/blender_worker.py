"""Runs INSIDE Blender (the `bpy` wheel or a Blender binary). Standalone: imports nothing from x3dlab.

    python blender_worker.py JOB.json
    blender -b --factory-startup --python blender_worker.py -- JOB.json

JOB.json: {"addon_src": ".../x3d-blender-io/source", "tasks": [{"id": ..., "kind": ..., ...}, ...]}

Every line starting with EVT is one JSON event for the lab; everything else is Blender's own output, which the
lab attaches to the task that was running (so an importer error message is never lost again).

Task kinds
  probe      Blender/Python versions and the Cycles devices of every GPU backend
  roundtrip  import -> snapshot -> export (version) -> re-import -> snapshot, `generations` times
  render     import, frame (X3D Viewpoint camera or automatic), Cycles on a backend, PNG + pixel statistics;
             film_transparent=True gives an RGBA PNG whose alpha is the silhouette (cross-renderer comparisons)
  import     import only: did the importer accept the file, and what came out (the fuzzer's Blender oracle)
"""
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=SyntaxWarning)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
JOB = json.load(open(argv[0]))


def emit(ev, **kw):
    sys.stdout.flush()
    # Leading newline: Blender sometimes leaves a line unterminated (a truncated traceback), and an event glued to
    # its end would be lost.
    print("\nEVT" + json.dumps(dict(ev=ev, t=time.time(), **kw), default=str), flush=True)


try:
    import bpy
    from mathutils import Vector
except Exception as exc:                                   # pragma: no cover - outside Blender
    emit("fatal", error="cannot import bpy: %s" % exc)
    sys.exit(0)

PKG = tempfile.mkdtemp(prefix="x3dlab_addon_")
_addon = None


def addon():
    """Import the add-on from its source folder once per process (as package io_scene_x3d)."""
    global _addon
    if _addon is None:
        link = os.path.join(PKG, "io_scene_x3d")
        try:
            os.symlink(os.path.abspath(JOB["addon_src"]), link, target_is_directory=True)
        except OSError:
            shutil.copytree(JOB["addon_src"], link)
        sys.path.insert(0, PKG)
        import importlib
        _addon = importlib.import_module("io_scene_x3d")
    return _addon


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        addon().register()
    except Exception:
        pass                                               # already registered in this process
    return bpy.context.scene


# ---------------------------------------------------------------------- snapshots
def r(v, n=4):
    return [round(float(x), n) for x in v]


def snapshot():
    """Everything the fidelity checks compare, in plain JSON."""
    objs = []
    for o in sorted(bpy.data.objects, key=lambda o: o.name):
        d = {"name": o.name, "type": o.type, "parent": o.parent.name if o.parent else None,
             "location": r(o.matrix_world.translation), "scale": r(o.matrix_world.to_scale()),
             "rotation": r(o.matrix_world.to_quaternion())}
        if o.type == "MESH" and o.data:
            m = o.data
            uniq = sorted({(round(v.co.x, 3), round(v.co.y, 3), round(v.co.z, 3)) for v in m.vertices})
            d.update(verts=len(m.vertices), faces=len(m.polygons), uverts=len(uniq),
                     vhash=hashlib.sha1(json.dumps(uniq).encode()).hexdigest()[:12],
                     materials=[s.material.name if s.material else None for s in o.material_slots])
            pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
            d["bbox"] = [r((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
                         r((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))]
        elif o.type == "LIGHT":
            L = o.data
            d.update(light=L.type, energy=round(L.energy, 4), color=r(L.color))
        elif o.type == "CAMERA":
            d.update(lens=round(o.data.lens, 3), fov=round(o.data.angle, 4))
        objs.append(d)
    mats = []
    for m in sorted(bpy.data.materials, key=lambda m: m.name):
        base = None
        if m.use_nodes and m.node_tree:
            b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if b is not None:
                base = r(b.inputs["Base Color"].default_value[:3])
        mats.append({"name": m.name, "base": base or r(m.diffuse_color[:3])})
    return {"objects": objs, "materials": mats,
            "counts": {"objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials),
                       "lights": len(bpy.data.lights), "cameras": len(bpy.data.cameras),
                       "vertices": sum(len(m.vertices) for m in bpy.data.meshes),
                       "faces": sum(len(m.polygons) for m in bpy.data.meshes)}}


def do_import(path):
    t = time.time()
    res = bpy.ops.import_scene.x3d(filepath=path)
    return sorted(res), time.time() - t


def do_export(path, version):
    t = time.time()
    res = bpy.ops.export_scene.x3d(filepath=path, x3d_version=version)
    return sorted(res), time.time() - t, (os.path.getsize(path) if os.path.exists(path) else 0)


# ---------------------------------------------------------------------- tasks
def task_probe(t):
    out = {"blender": bpy.app.version_string, "python": sys.version.split()[0], "backends": {}}
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception as exc:
        out["error"] = "Cycles add-on unavailable: %s" % exc
        return out
    default = ["METAL"] if sys.platform == "darwin" else ["OPTIX", "CUDA", "HIP", "ONEAPI"]
    for backend in t.get("backends", default):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
            gpus = [d.name for d in prefs.devices if d.type == backend]
            out["backends"][backend] = gpus
        except Exception as exc:
            out["backends"][backend] = "unavailable: %s" % str(exc)[:80]
    out["best"] = next((b for b in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")
                        if isinstance(out["backends"].get(b), list) and out["backends"][b]), "CPU")
    return out


def task_roundtrip(t):
    src, version, gens = t["src"], t.get("version", "X3D40"), int(t.get("generations", 1))
    stem = t.get("out_prefix") or os.path.splitext(src)[0]
    out = {"version": version, "generations": []}
    cur = src
    for g in range(gens):
        fresh()
        imp, ti = do_import(cur)
        snap = snapshot()
        gen = {"gen": g, "input": cur, "import": imp, "import_s": round(ti, 3), "snapshot": snap}
        if "FINISHED" not in imp:
            gen["error"] = "import returned %s" % imp
            out["generations"].append(gen)
            break
        nxt = "%s.gen%d.%s.x3d" % (stem, g + 1, version.lower())
        exp, te, size = do_export(nxt, version)
        gen.update(export=exp, export_s=round(te, 3), output=nxt, bytes=size)
        out["generations"].append(gen)
        emit("progress", id=t["id"], gen=g, import_s=gen["import_s"], export_s=gen["export_s"], bytes=size,
             counts=snap["counts"])
        if "FINISHED" not in exp or size == 0:
            gen["error"] = "export returned %s (%d bytes)" % (exp, size)
            break
        cur = nxt
    if not any("error" in g for g in out["generations"]) and gens:
        fresh()                                            # the last export, imported once more
        imp, ti = do_import(cur)
        out["final"] = {"input": cur, "import": imp, "import_s": round(ti, 3), "snapshot": snapshot()}
    return out


def task_import(t):
    fresh()
    imp, ti = do_import(t["src"])
    if "FINISHED" not in imp:
        return {"error": "import returned %s" % imp}
    return {"import": imp, "import_s": round(ti, 3), "counts": snapshot()["counts"]}


def camera_info(scn):
    cam = scn.camera
    if cam is None:
        return {}
    d = cam.data
    q = lambda x: round(float(x), 4)  # noqa: E731
    return {"name": cam.name, "type": d.type, "angle": q(d.angle), "angle_x": q(d.angle_x), "angle_y": q(d.angle_y),
            "sensor_fit": d.sensor_fit, "lens": q(d.lens), "clip": [q(d.clip_start), q(d.clip_end)],
            "location": r(cam.matrix_world.translation), "rotation": r(cam.matrix_world.to_euler())}


def set_backend(scn, backend):
    scn.render.engine = "CYCLES"
    if backend in (None, "", "CPU"):
        scn.cycles.device = "CPU"
        return "CPU", []
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = backend
    prefs.get_devices()
    names = []
    for d in prefs.devices:
        d.use = d.type == backend
        if d.use:
            names.append(d.name)
    if not names:
        scn.cycles.device = "CPU"
        return "CPU", []
    scn.cycles.device = "GPU"
    return backend, names


def set_denoiser(scn, name, backend):
    """Pick the denoiser; OpenImageDenoise runs on the GPU too where Blender supports it."""
    try:
        scn.cycles.denoiser = name
    except Exception:
        pass
    if name == "OPENIMAGEDENOISE" and hasattr(scn.cycles, "denoising_use_gpu"):
        scn.cycles.denoising_use_gpu = backend not in (None, "", "CPU")


def frame_camera(scn, use_viewpoint=True):
    cams = [o for o in scn.objects if o.type == "CAMERA"]
    if use_viewpoint and cams:
        scn.camera = cams[0]
        return "viewpoint"
    pts = [o.matrix_world @ Vector(c) for o in scn.objects if o.type == "MESH" for c in o.bound_box]
    if not pts:
        raise RuntimeError("no mesh geometry to frame")
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centre, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.5)
    cam = bpy.data.objects.new("lab_cam", bpy.data.cameras.new("lab_cam"))
    scn.collection.objects.link(cam)
    cam.location = centre + Vector((0.8, -1.6, 0.9)).normalized() * radius * 3.0
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scn.camera = cam
    return "auto"


def task_render(t):
    scn = fresh()
    imp, ti = do_import(t["src"])
    if "FINISHED" not in imp:
        return {"error": "import returned %s" % imp}
    backend, devices = set_backend(scn, t.get("backend", "CPU"))
    framing = frame_camera(scn, t.get("camera", "viewpoint") == "viewpoint")
    w, h = t.get("resolution", [640, 360])
    scn.render.resolution_x, scn.render.resolution_y, scn.render.resolution_percentage = int(w), int(h), 100
    scn.cycles.samples = int(t.get("samples", 32))
    # Adaptive sampling (Blender's default) stops a pixel early once it looks clean, so "N samples" is a ceiling.
    # The stress ladders turn it off so the samples ladder measures N samples; single renders keep Blender's default.
    scn.cycles.use_adaptive_sampling = bool(t.get("adaptive", True))
    scn.cycles.use_denoising = bool(t.get("denoise", False))
    denoiser = None
    if t.get("denoise"):
        denoiser = "OPTIX" if backend == "OPTIX" else "OPENIMAGEDENOISE"
        set_denoiser(scn, denoiser, backend)
    if scn.world is None:
        scn.world = bpy.data.worlds.new("lab_world")
    scn.world.use_nodes = True
    bg = scn.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.10, 0.11, 0.14, 1.0)
        bg.inputs[1].default_value = 1.0
    if not any(o.type == "LIGHT" for o in scn.objects):
        sun = bpy.data.objects.new("lab_sun", bpy.data.lights.new("lab_sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (0.9, 0.2, 0.6)
        scn.collection.objects.link(sun)
    if t.get("film_transparent"):
        scn.render.film_transparent = True
        scn.render.image_settings.color_mode = "RGBA"
    scn.render.filepath = t["out_png"]
    scn.render.image_settings.file_format = "PNG"
    t0 = time.time()
    denoiser_note = ""
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        # The OptiX denoiser needs a trained model shipped with the driver (nvoptix.bin); some containers, Colab's
        # A100 among them, lack it, so the OptiX ray tracer works but its denoiser fails. Retry with OpenImageDenoise.
        if denoiser != "OPTIX" or "denoiser" not in str(exc).lower():
            raise
        denoiser = "OPENIMAGEDENOISE"
        set_denoiser(scn, denoiser, backend)
        denoiser_note = "OptiX denoiser unavailable (%s); used OpenImageDenoise" % str(exc).strip().splitlines()[0][:80]
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
    secs = time.time() - t0
    stats = {}
    try:                                                   # is anything on screen? (background colour vs the rest)
        img = bpy.data.images.load(t["out_png"])
        px = img.pixels[:]
        n = len(px) // 4
        lum = [0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2] for i in range(0, len(px), 4 * max(1, n // 20000))]
        bgl = lum[0]
        stats = {"mean": round(sum(lum) / len(lum), 4), "covered": round(sum(abs(x - bgl) > 0.02 for x in lum) / len(lum), 4)}
    except Exception as exc:
        stats = {"error": str(exc)[:120]}
    return {"backend": backend, "devices": devices, "camera": framing, "seconds": round(secs, 3), "import_s": round(ti, 3),
            "samples": scn.cycles.samples, "resolution": [int(w), int(h)], "png": t["out_png"],
            "adaptive": scn.cycles.use_adaptive_sampling, "denoiser": denoiser, "denoiser_note": denoiser_note,
            "bytes": os.path.getsize(t["out_png"]) if os.path.exists(t["out_png"]) else 0, "pixels": stats,
            "counts": snapshot()["counts"], "camera_info": camera_info(scn)}


def _shared_meshes():
    """Meshes used by more than one object: X3D DEF/USE instancing as Blender sees it."""
    return sum(1 for m in bpy.data.meshes if m.users > 1)


def _smooth_faces():
    """Faces shaded smooth. X3D draws Sphere, Cylinder and Cone smooth; an importer that leaves them flat shows facets
    in Blender and writes per-face normals to every format after it."""
    return sum(1 for m in bpy.data.meshes for p in m.polygons if p.use_smooth)


def _flat_meshes():
    """Meshes of 16 faces or more with not one face shaded smooth (a sphere or cylinder imported faceted), by name."""
    return sorted(m.name for m in bpy.data.meshes if len(m.polygons) >= 16 and not any(p.use_smooth for p in m.polygons))


GLTF_EXTRAS = (
    # Blender's glTF exporter calls loose edges and points use_mesh_edges / use_mesh_vertices ("Loose Edges", "Loose
    # Points"). The export_loose_* names tried first until 12 Nov 2026 do not exist in Blender 5.2, so the export fell
    # back to the next set and every X3D IndexedLineSet and PointSet vanished from the glb.
    # Light units: COMPAT ("Unitless") writes an X3D intensity as the glTF light's intensity. The default (SPEC,
    # physical units) turned a DirectionalLight of 0.3 into 204.9 lux, and X_ITE and Babylon.js drew the glb
    # saturated (255) where X_ITE draws the X3D at 61 (Material) and 78 (PhysicalMaterial); with COMPAT the
    # PhysicalMaterial matches exactly (78 / 78 / 78), measured 12 Nov 2026.
    {"export_lights": True, "export_cameras": True, "use_mesh_edges": True, "use_mesh_vertices": True,
     "export_import_convert_lighting_mode": "COMPAT"},
    {"export_lights": True, "export_cameras": True, "export_loose_edges": True, "export_loose_points": True,
     "export_import_convert_lighting_mode": "COMPAT"},
    {"export_lights": True, "export_cameras": True},
    {},
)
LAST_GLTF_EXTRAS = {}


def _drop_unused_coordinates():
    """X3D draws a face set's faces and a line set's polylines, and nothing else of its Coordinate. Blender keeps
    every point, so a point no face or polyline uses (a Coordinate shared with another shape: the Gobbler's face sets
    share theirs with its point sets) becomes a loose vertex, and a face with fewer than three distinct corners a loose
    edge. With the exporter's loose points and edges on (X3D lines and point sets need them), those reached the glb as
    stray points and lines: about 1,200 points on the Gobbler (12 Nov 2026).

    Removed before the export: loose vertices of meshes that have edges or faces, and loose edges of meshes that have
    faces. A mesh of vertices only (a PointSet) and one of edges only (a line set, or a face set made only of
    two-corner quads, which the Gobbler's shader draws as wide lines) keep theirs. Shape keys, UVs, colours and
    custom normals go through bmesh unchanged."""
    import bmesh
    dropped = {"meshes": 0, "vertices": 0, "edges": 0}
    for me in bpy.data.meshes:
        if me.users == 0 or not len(me.vertices) or not (len(me.polygons) or len(me.edges)):
            continue
        has_faces = len(me.polygons) > 0
        if has_faces:
            loose = sum(1 for v in me.vertices) - len({i for p in me.polygons for i in p.vertices})
            if not loose and len(me.edges) == len({k for p in me.polygons for k in p.edge_keys}):
                continue
        bm = bmesh.new()
        bm.from_mesh(me)
        n_verts = len(bm.verts)
        edges = [e for e in bm.edges if not e.link_faces] if has_faces else []
        if edges:
            bmesh.ops.delete(bm, geom=edges, context='EDGES')        # may take their now-bare vertices along
        verts = [v for v in bm.verts if not v.link_edges]
        if verts:
            bmesh.ops.delete(bm, geom=verts, context='VERTS')
        if edges or verts:
            bm.to_mesh(me)
            me.update()
            dropped["meshes"] += 1
            dropped["vertices"] += n_verts - len(bm.verts)
            dropped["edges"] += len(edges)
        bm.free()
    return dropped


def _export_gltf(out, fmt):
    kw = {"filepath": out, "export_format": "GLB" if fmt == "glb" else "GLTF_SEPARATE"}
    # lights and cameras are off by default; loose edges and points too (X3D lines and point sets would vanish)
    for extra in GLTF_EXTRAS:                   # option names differ between Blender versions: the first one it takes
        try:
            r = bpy.ops.export_scene.gltf(**kw, **extra)
        except TypeError:
            continue
        LAST_GLTF_EXTRAS.clear()
        LAST_GLTF_EXTRAS.update(extra)
        return r
    return bpy.ops.export_scene.gltf(**kw)


def task_convert(t):
    """Import X3D, export another format (glb, gltf, usd), import that back into an empty scene.
    The counts before and after say what the trip through the other format kept; the file goes to that format's own
    validator and renderers outside Blender."""
    fmt, out = t.get("format", "glb"), t["out"]
    fresh()
    imp, ti = do_import(t["src"])
    if "FINISHED" not in imp:
        return {"error": "import returned %s" % imp}
    res = {"format": fmt, "import": imp, "import_s": round(ti, 3), "counts": snapshot()["counts"], "shared_meshes": _shared_meshes(),
           "smooth_faces": _smooth_faces(), "flat_meshes": _flat_meshes()}
    t0 = time.time()
    if fmt in ("glb", "gltf"):
        res["dropped_unused"] = _drop_unused_coordinates()      # before the loose points and edges go out
        exp = _export_gltf(out, fmt)
    elif fmt in ("usd", "usda", "usdc"):
        if not hasattr(bpy.ops.wm, "usd_export"):
            return dict(res, error="this Blender build has no USD exporter")
        exp = _export_usd(out)
    else:
        return dict(res, error="unknown format %r" % fmt)
    res.update(export=sorted(exp), export_s=round(time.time() - t0, 3), output=out,
               bytes=os.path.getsize(out) if os.path.exists(out) else 0)
    if fmt in ("glb", "gltf"):
        res["export_options"] = dict(LAST_GLTF_EXTRAS)          # which option set this Blender took
    if "FINISHED" not in exp or not res["bytes"]:
        res["error"] = "export returned %s (%d bytes)" % (sorted(exp), res["bytes"])
        return res
    fresh()
    try:
        back = _import_any(out)
        res["reimport"] = {"result": sorted(back), "counts": snapshot()["counts"], "shared_meshes": _shared_meshes()}
    except Exception as exc:
        res["reimport"] = {"error": str(exc)[:300]}
    return res


def _import_any(path):
    """Import a glTF (.glb/.gltf), USD (.usd/.usda/.usdc/.usdz) or X3D file into the current scene."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        return bpy.ops.import_scene.gltf(filepath=path)
    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        return bpy.ops.wm.usd_import(filepath=path)
    return bpy.ops.import_scene.x3d(filepath=path)


def _export_usd(out):
    return bpy.ops.wm.usd_export(filepath=out, export_materials=True, generate_preview_surface=True, export_lights=True,
                                 export_cameras=True, use_instancing=True)


def task_ingest(t):
    """The other direction: a glTF or USD file into an empty scene, then out as X3D (each version asked for), and
    optionally as USD. The X3D files go to the validators and to X_ITE; the counts say what Blender itself made of it."""
    fresh()
    t0 = time.time()
    try:
        imp = _import_any(t["src"])
    except Exception as exc:
        return {"error": "import: %s" % str(exc)[:300]}
    res = {"import": sorted(imp), "import_s": round(time.time() - t0, 3), "counts": snapshot()["counts"],
           "shared_meshes": _shared_meshes(), "smooth_faces": _smooth_faces(),
           "materials": sorted({m.name for m in bpy.data.materials}),
           "actions": len(bpy.data.actions), "armatures": len(bpy.data.armatures),
           "shape_keys": sum(1 for m in bpy.data.meshes if m.shape_keys), "outputs": {}}
    if "FINISHED" not in imp:
        res["error"] = "import returned %s" % sorted(imp)
        return res
    for version in t.get("versions", ["X3D40"]):
        out = "%s.%s.x3d" % (t["out_prefix"], version.lower())
        try:
            r, secs, size = do_export(out, version)
            res["outputs"][version] = {"result": r, "seconds": round(secs, 3), "bytes": size, "output": out}
        except Exception as exc:
            res["outputs"][version] = {"error": str(exc)[:300]}
    if t.get("usd"):
        out = "%s.usdc" % t["out_prefix"]
        try:
            r = _export_usd(out)
            res["outputs"]["USD"] = {"result": sorted(r), "bytes": os.path.getsize(out) if os.path.exists(out) else 0, "output": out}
        except Exception as exc:
            res["outputs"]["USD"] = {"error": str(exc)[:300]}
    return res


KINDS = {"probe": task_probe, "roundtrip": task_roundtrip, "render": task_render, "import": task_import, "convert": task_convert,
         "ingest": task_ingest}

try:
    for task in JOB["tasks"]:
        emit("start", id=task["id"], kind=task["kind"])
        t0 = time.time()
        try:
            result = KINDS[task["kind"]](task)
            ok = "error" not in result
        except Exception as exc:
            result, ok = {"error": "%s: %s" % (type(exc).__name__, exc), "trace": traceback.format_exc()[-1500:]}, False
        emit("end", id=task["id"], kind=task["kind"], ok=ok, seconds=round(time.time() - t0, 3), result=result)
finally:
    shutil.rmtree(PKG, ignore_errors=True)                 # the old worker leaked one of these per run
    emit("done")
