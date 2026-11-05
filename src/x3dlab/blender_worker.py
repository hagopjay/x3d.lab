"""Runs INSIDE Blender (the `bpy` wheel or a Blender binary). Standalone: imports nothing from x3dlab.

    python blender_worker.py JOB.json
    blender -b --factory-startup --python blender_worker.py -- JOB.json

JOB.json: {"addon_src": ".../x3d-blender-io/source", "tasks": [{"id": ..., "kind": ..., ...}, ...]}

Every line starting with EVT is one JSON event for the lab; everything else is Blender's own output, which the
lab attaches to the task that was running (so an importer error message is never lost again).

Task kinds
  probe      Blender/Python versions and the Cycles devices of every GPU backend
  roundtrip  import -> snapshot -> export (version) -> re-import -> snapshot, `generations` times
  render     import, frame (X3D Viewpoint camera or automatic), Cycles on a backend, PNG + pixel statistics
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
    scn.cycles.use_denoising = bool(t.get("denoise", False))
    if t.get("denoise") and backend == "OPTIX":
        try:
            scn.cycles.denoiser = "OPTIX"
        except Exception:
            pass
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
    scn.render.filepath = t["out_png"]
    scn.render.image_settings.file_format = "PNG"
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
            "bytes": os.path.getsize(t["out_png"]) if os.path.exists(t["out_png"]) else 0, "pixels": stats,
            "counts": snapshot()["counts"]}


KINDS = {"probe": task_probe, "roundtrip": task_roundtrip, "render": task_render}

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
