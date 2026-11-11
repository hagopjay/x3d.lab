"""Blender (Cycles) stills of a glb from X3D viewpoints. Runs inside Blender or the bpy wheel:

    blender -b --factory-startup --python gob_cycles.py -- job.json      or      python gob_cycles.py job.json

job.json: {"glb", "out_dir", "res": [w, h], "samples", "exposure", "look", "device": "GPU"|"CPU", "backend": "OPTIX"|"CUDA"|...,
           "views": {name: {"position": [x, y, z], "orientation": [ax, ay, az, angle], "fov": rad}},
           "lights": [{"name", "location": [x, y, z], "color": [r, g, b], "watts"}], "denoise": true}
Positions are X3D (Y up); Blender's glTF importer turns Y up into Z up, and so does this script for cameras and lights.
Writes <out_dir>/cycles_<view>.png and <out_dir>/cycles_report.json.
"""
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
job = json.load(open(argv[0]))
REPORT = {"blender": bpy.app.version_string, "views": {}, "errors": []}
os.makedirs(job["out_dir"], exist_ok=True)
A = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))          # X3D / glTF (Y up) -> Blender (Z up)


def axis_angle(o):
    x, y, z, a = o
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return Matrix.Rotation(a, 3, (x / n, y / n, z / n))


def note(e):
    REPORT["errors"].append(str(e)[:300])


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
t0 = time.time()
bpy.ops.import_scene.gltf(filepath=job["glb"])
REPORT["import_s"] = round(time.time() - t0, 2)
REPORT["objects"] = len(bpy.data.objects)

scene.render.engine = "CYCLES"
cy = scene.cycles
cy.samples = int(job.get("samples", 128))
cy.use_denoising = bool(job.get("denoise", True))
try:
    cy.max_bounces = 8
except Exception as e:
    note(e)
REPORT["device"] = "CPU"
if job.get("device", "GPU") == "GPU":
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in [job.get("backend") or "OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"]:
            try:
                prefs.compute_device_type = backend
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == backend]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == backend
                    cy.device = "GPU"
                    REPORT["device"] = "%s (%s)" % (backend, ", ".join(d.name for d in gpus))
                    break
            except Exception as e:
                note(e)
    except Exception as e:
        note(e)

w, h = job.get("res", [1920, 1080])
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = int(w), int(h), 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
try:
    scene.view_settings.view_transform = job.get("view_transform") or "AgX"
    look = job.get("look")
    if look:
        scene.view_settings.look = look
except Exception as e:
    note(e)
scene.view_settings.exposure = float(job.get("exposure", 0.0))
world = bpy.data.worlds.new("x3dlab_black")
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs[0].default_value = (0, 0, 0, 1)
    bg.inputs[1].default_value = 0.0
scene.world = world

for L in job.get("lights", []):
    data = bpy.data.lights.new(L.get("name", "light"), "POINT")
    data.color = L.get("color", (1, 1, 1))[:3]
    data.energy = float(L.get("watts", 500))
    data.shadow_soft_size = float(L.get("radius", 0.25))
    ob = bpy.data.objects.new(L.get("name", "light"), data)
    ob.location = A @ __import__("mathutils").Vector(L["location"])
    scene.collection.objects.link(ob)

cam_data = bpy.data.cameras.new("x3dlab_cam")
cam = bpy.data.objects.new("x3dlab_cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
for name, vp in job["views"].items():
    R = A @ axis_angle(vp["orientation"])
    M = R.to_4x4()
    M.translation = A @ __import__("mathutils").Vector(vp["position"])
    cam.matrix_world = M
    cam_data.sensor_fit = "VERTICAL" if w >= h else "HORIZONTAL"
    cam_data.angle = float(vp.get("fov", math.pi / 4))
    cam_data.clip_start, cam_data.clip_end = 0.01, 5000.0
    out = os.path.join(job["out_dir"], "cycles_%s.png" % name)
    scene.render.filepath = out
    t = time.time()
    try:
        bpy.ops.render.render(write_still=True)
        REPORT["views"][name] = {"png": out, "seconds": round(time.time() - t, 2)}
    except Exception as e:
        note(e)
        REPORT["views"][name] = {"png": None, "error": str(e)[:300]}
    print("X3DLAB cycles %s %.1f s" % (name, time.time() - t), flush=True)

REPORT["seconds"] = round(time.time() - t0, 2)
json.dump(REPORT, open(os.path.join(job["out_dir"], "cycles_report.json"), "w"), indent=1)
print("X3DLAB cycles done", flush=True)
