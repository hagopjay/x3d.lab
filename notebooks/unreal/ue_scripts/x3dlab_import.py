"""Runs INSIDE Unreal as a one-shot editor task:

    UnrealEditor-Cmd X3DLab.uproject -run=pythonscript -script=x3dlab_import.py -X3DLabJob=/abs/job.json

Imports one glTF (or USD) scene into a new level with Interchange, measures what arrived (actors, meshes, materials,
textures, lights, cameras, instancing, bounds), decides which axis mapping the importer used, places the X3D
viewpoints as CineCameras and the X3D lights in Unreal units (both computed outside Unreal from the X3D manifest and
passed in as data), adds the backdrop and the two looks, makes one Level Sequence per viewpoint for Movie Render
Queue, saves everything, and writes a JSON report. No rendering and no game loop happen here.

The job file is data only (validated by the notebook); nothing in it is executed. Every Unreal API call that differs
between engine versions is tried in its known spellings, and a call that fails is recorded in report["api_misses"]
instead of stopping the import, so the first live run says exactly which spelling this engine has.
"""
import json
import math
import os
import sys
import time
import traceback

import unreal

REPORT = {"ok": False, "steps": [], "api_misses": [], "errors": [], "t0": time.time()}


def log(msg):
    unreal.log("X3DLAB: %s" % msg)
    REPORT["steps"].append([round(time.time() - REPORT["t0"], 2), str(msg)[:300]])


def miss(what, exc):
    REPORT["api_misses"].append("%s: %s" % (what, str(exc)[:200]))


def attempt(what, *fns):
    """First of fns() that works; records the failures."""
    last = None
    for fn in fns:
        try:
            return fn()
        except Exception as exc:          # noqa: BLE001 - every engine-version difference lands here
            last = exc
    miss(what, last)
    return None


def setp(obj, name, value):
    try:
        obj.set_editor_property(name, value)
        return True
    except Exception as exc:
        miss("set %s.%s" % (type(obj).__name__, name), exc)
        return False


def job_path():
    try:
        _, _, params = unreal.SystemLibrary.parse_command_line(unreal.SystemLibrary.get_command_line())
        for k, v in params.items():
            if k.lower() == "x3dlabjob":
                return v.strip('"')
    except Exception as exc:
        miss("parse_command_line", exc)
    for a in sys.argv:
        if a.lower().startswith("-x3dlabjob="):
            return a.split("=", 1)[1].strip('"')
    return os.environ.get("X3DLAB_JOB")


# ---------------------------------------------------------------------- subsystems
def actor_sub():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def level_sub():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def all_actors():
    return attempt("all level actors", lambda: list(actor_sub().get_all_level_actors()),
                   lambda: list(unreal.EditorLevelLibrary.get_all_level_actors())) or []


def spawn(cls, loc=(0, 0, 0), rot=(0, 0, 0)):
    L = unreal.Vector(*loc)
    R = unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1])
    return attempt("spawn %s" % cls.__name__, lambda: actor_sub().spawn_actor_from_class(cls, L, R),
                   lambda: unreal.EditorLevelLibrary.spawn_actor_from_class(cls, L, R))


def destroy(actor):
    attempt("destroy actor", lambda: actor_sub().destroy_actor(actor), lambda: unreal.EditorLevelLibrary.destroy_actor(actor))


def tag(actor, *tags):
    try:
        cur = list(actor.get_editor_property("tags"))
        actor.set_editor_property("tags", cur + [unreal.Name(t) for t in tags])
    except Exception as exc:
        miss("tags", exc)


def label(actor, text):
    attempt("set_actor_label", lambda: actor.set_actor_label(text))


# ---------------------------------------------------------------------- import
def new_level(map_path):
    ok = attempt("new_level", lambda: level_sub().new_level(map_path), lambda: unreal.EditorLevelLibrary.new_level(map_path))
    log("new level %s: %s" % (map_path, ok))
    return ok


def import_gltf(src, content_path):
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    sd = unreal.InterchangeManager.create_source_data(src)
    can = attempt("can_translate_source_data", lambda: mgr.can_translate_source_data(sd, True))
    log("Interchange can translate %s: %s" % (os.path.basename(src), can))
    p = unreal.ImportAssetParameters()
    setp(p, "is_automated", True)
    setp(p, "replace_existing", True)
    world = attempt("editor world", lambda: unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world(),
                    lambda: unreal.EditorLevelLibrary.get_editor_world())
    if world is not None:
        lvl = attempt("persistent level", lambda: world.get_editor_property("persistent_level"))
        if lvl is not None:
            setp(p, "import_level", lvl)
    t = time.time()
    ok = attempt("import_scene", lambda: mgr.import_scene(content_path, sd, p))
    attempt("wait_until_all_tasks_done", lambda: mgr.wait_until_all_tasks_done(False))
    REPORT["import_seconds"] = round(time.time() - t, 2)
    log("Interchange import_scene -> %s in %.1f s" % (ok, time.time() - t))
    return bool(ok)


def import_usd(src, content_path):
    """USD: Interchange first (when a USD translator is registered), else the USD Importer plugin's import task."""
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    sd = unreal.InterchangeManager.create_source_data(src)
    if attempt("can_translate usd", lambda: mgr.can_translate_source_data(sd, True)):
        REPORT["usd_path"] = "interchange"
        return import_gltf(src, content_path)
    REPORT["usd_path"] = "UsdStageImportOptions"
    task = unreal.AssetImportTask()
    for k, v in (("filename", src), ("destination_path", content_path), ("automated", True), ("replace_existing", True), ("save", False)):
        setp(task, k, v)
    opts = attempt("UsdStageImportOptions", lambda: unreal.UsdStageImportOptions())
    if opts is not None:
        for k, v in (("import_actors", True), ("import_geometry", True), ("import_materials", True), ("import_lights", True),
                     ("import_cameras", True), ("meters_per_unit", 0.01)):
            setp(opts, k, v)
        setp(task, "options", opts)
    t = time.time()
    attempt("import_asset_tasks", lambda: asset_tools().import_asset_tasks([task]))
    REPORT["import_seconds"] = round(time.time() - t, 2)
    return True


# ---------------------------------------------------------------------- measure
def cls_name(o):
    try:
        return o.get_class().get_name()
    except Exception:
        return type(o).__name__


def mesh_of(actor):
    comp = attempt("static_mesh_component", lambda: actor.get_editor_property("static_mesh_component"),
                   lambda: actor.get_component_by_class(unreal.StaticMeshComponent))
    if comp is None:
        return None
    return attempt("static_mesh", lambda: comp.get_editor_property("static_mesh"))


def triangles(mesh):
    if mesh is None:
        return 0
    n = attempt("mesh triangles", lambda: mesh.get_num_triangles(0),
                lambda: unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_number_tris(mesh, 0) if hasattr(
                    unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem), "get_number_tris") else None)
    return int(n or 0)


def bounds(actor):
    try:
        o, e = actor.get_actor_bounds(False)
        return [o.x, o.y, o.z], [e.x, e.y, e.z]
    except Exception as exc:
        miss("get_actor_bounds", exc)
        return None, None


SKY_NODE = "X3DLab_Sky"          # x3due.ucopy: the X3D Background's gradient dome, part of the glb


def is_sky(actor, mesh=None):
    """The gradient dome x3due.ucopy adds to the glb: by its actor label or its mesh's name."""
    names = [attempt("label", lambda: actor.get_actor_label()) or "", mesh.get_name() if mesh is not None else ""]
    return any(SKY_NODE.lower() in str(n).lower() for n in names)


def measure(content_path, skip=()):
    acts = [a for a in all_actors() if a not in skip]
    by_class, meshes, centres, cams, lights, tri = {}, {}, [], [], [], 0
    for a in acts:
        c = cls_name(a)
        by_class[c] = by_class.get(c, 0) + 1
        if c == "StaticMeshActor":
            m = mesh_of(a)
            key = m.get_path_name() if m is not None else None
            meshes[key] = meshes.get(key, 0) + 1
            n = triangles(m)
            tri += n
            if is_sky(a, m):
                # the backdrop, not a shape: hidden in key stills like the flat backdrop, no shadow, and not a
                # shape centre for the axis check
                tag(a, "x3dlab_backdrop", "x3dlab_sky")
                comp = attempt("sky component", lambda: a.get_editor_property("static_mesh_component"))
                if comp is not None:
                    setp(comp, "cast_shadow", False)
                REPORT.setdefault("sky", []).append(attempt("label", lambda: a.get_actor_label()))
                continue
            o, _ = bounds(a)
            if o is not None:
                centres.append(o)
        elif "Camera" in c:
            l = a.get_actor_location()
            cams.append({"label": attempt("label", lambda: a.get_actor_label()), "class": c, "location": [l.x, l.y, l.z]})
        elif c in ("DirectionalLight", "PointLight", "SpotLight", "RectLight", "SkyLight"):
            lights.append({"label": attempt("label", lambda: a.get_actor_label()), "class": c, "actor": a})
    assets = attempt("list_assets", lambda: list(unreal.EditorAssetLibrary.list_assets(content_path, True, False))) or []
    by_asset = {}
    for p in assets:
        d = attempt("find_asset_data", lambda: unreal.EditorAssetLibrary.find_asset_data(p))
        k = None
        if d is not None:
            k = attempt("asset class", lambda: str(d.asset_class_path.asset_name), lambda: str(d.asset_class))
        by_asset[k or "?"] = by_asset.get(k or "?", 0) + 1
    shared = {k: v for k, v in meshes.items() if v > 1 and k}
    return {"actors_by_class": by_class, "mesh_actors": sum(meshes.values()), "static_meshes_used": len([k for k in meshes if k]),
            "shared_static_meshes": len(shared), "mesh_actors_on_shared": sum(shared.values()), "triangles": tri,
            "assets_by_class": by_asset, "cameras": cams, "lights": [{k: v for k, v in l.items() if k != "actor"} for l in lights],
            "_light_actors": [l["actor"] for l in lights], "centres_cm": centres}


# ---------------------------------------------------------------------- build the stage
def place_lights(lights):
    out = []
    for L in lights:
        cls = {"directional": unreal.DirectionalLight, "point": unreal.PointLight, "spot": unreal.SpotLight}[L["kind"]]
        a = spawn(cls, L.get("location", (0, 0, 0)), L.get("rotation", (0, 0, 0)))
        if a is None:
            continue
        comp = attempt("light_component", lambda: a.get_editor_property("light_component"),
                       lambda: a.get_component_by_class(unreal.LightComponent))
        if comp is None:
            continue
        setp(comp, "mobility", unreal.ComponentMobility.MOVABLE)
        if L["kind"] != "directional":
            attempt("intensity units", lambda: comp.set_editor_property("intensity_units", unreal.LightUnits.CANDELAS))
            setp(comp, "use_inverse_squared_falloff", True)
            attempt("attenuation radius", lambda: comp.set_attenuation_radius(L["radius_cm"]))
        attempt("set_intensity", lambda: comp.set_intensity(L["intensity"]))
        attempt("set_light_color", lambda: comp.set_light_color(unreal.LinearColor(*L["color"], 1.0)))
        if L["kind"] == "spot":
            attempt("inner cone", lambda: comp.set_inner_cone_angle(L["inner_deg"]))
            attempt("outer cone", lambda: comp.set_outer_cone_angle(L["outer_deg"]))
        if not L.get("on", True):
            attempt("visibility", lambda: comp.set_visibility(False))
        label(a, L["name"])
        tag(a, "x3dlab_light")
        out.append(L["name"])
    return out


def place_cameras(cams):
    out = {}
    for c in cams:
        a = spawn(unreal.CineCameraActor, c["location"], c["rotation"])
        if a is None:
            continue
        comp = attempt("cine camera component", lambda: a.get_cine_camera_component(),
                       lambda: a.get_editor_property("camera_component"))
        if comp is not None:
            fb = attempt("filmback", lambda: comp.get_editor_property("filmback"))
            if fb is not None:
                setp(fb, "sensor_width", c["sensor_width"])
                setp(fb, "sensor_height", c["sensor_height"])
                setp(comp, "filmback", fb)
            setp(comp, "current_focal_length", c["focal_length"])
            fs = attempt("focus_settings", lambda: comp.get_editor_property("focus_settings"))
            if fs is not None:
                attempt("focus off", lambda: fs.set_editor_property("focus_method", unreal.CameraFocusMethod.DISABLE))
                setp(comp, "focus_settings", fs)
            setp(comp, "constrain_aspect_ratio", False)
        label(a, "VP_" + c["name"])
        tag(a, "x3dlab_camera", "x3dlab_vp_" + c["name"])
        out[c["name"]] = a
        if c.get("headlight"):
            h = spawn(unreal.DirectionalLight, c["location"], c["rotation"])
            if h is not None:
                hc = attempt("headlight component", lambda: h.get_editor_property("light_component"))
                if hc is not None:
                    setp(hc, "mobility", unreal.ComponentMobility.MOVABLE)
                    attempt("headlight intensity", lambda: hc.set_intensity(c["headlight"]["intensity"]))
                    attempt("headlight hidden", lambda: hc.set_visibility(False))   # the render executor turns on the active one
                label(h, "Headlight_" + c["name"])
                tag(h, "x3dlab_headlight", "x3dlab_headlight_" + c["name"])
    return out


def backdrop_material(shared_path):
    """An unlit, two-sided material whose emissive colour is a parameter: the X3D Background (and the magenta key)."""
    path = shared_path + "/M_X3DLabBackdrop"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    mat = attempt("create backdrop material", lambda: asset_tools().create_asset("M_X3DLabBackdrop", shared_path, unreal.Material,
                                                                                 unreal.MaterialFactoryNew()))
    if mat is None:
        return None
    setp(mat, "shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    setp(mat, "two_sided", True)
    mel = unreal.MaterialEditingLibrary
    col = attempt("vector param", lambda: mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -400, 0))
    if col is not None:
        setp(col, "parameter_name", "Color")
        attempt("connect emissive", lambda: mel.connect_material_property(col, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR))
    attempt("recompile", lambda: mel.recompile_material(mat))
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def backdrop(content_path, shared_path, centre, radius_cm, color, key_color):
    base = backdrop_material(shared_path)
    sphere = attempt("load sphere", lambda: unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere.Sphere"))
    made = []
    for name, rgb, hidden in (("Backdrop", color, False), ("BackdropKey", key_color, True)):
        mi = None
        if base is not None:
            mi = attempt("material instance", lambda: asset_tools().create_asset("MI_" + name, content_path, unreal.MaterialInstanceConstant,
                                                                                unreal.MaterialInstanceConstantFactoryNew()))
            if mi is not None:
                setp(mi, "parent", base)
                attempt("set colour", lambda: unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
                    mi, "Color", unreal.LinearColor(*rgb, 1.0)))
                unreal.EditorAssetLibrary.save_loaded_asset(mi)
        a = spawn(unreal.StaticMeshActor, centre)
        if a is None or sphere is None:
            continue
        comp = a.get_editor_property("static_mesh_component")
        attempt("sphere mesh", lambda: comp.set_static_mesh(sphere))
        if mi is not None:
            attempt("sphere material", lambda: comp.set_material(0, mi))
        s = radius_cm / 50.0                     # the engine sphere has a 50 cm radius
        a.set_actor_scale3d(unreal.Vector(s, s, s))
        setp(comp, "cast_shadow", False)
        setp(comp, "mobility", unreal.ComponentMobility.MOVABLE)
        if hidden:
            attempt("hide key", lambda: a.set_actor_hidden_in_game(True))
        label(a, name)
        tag(a, "x3dlab_" + name.lower())
        made.append(name)
    return made


PP_COMMON = {"auto_exposure_method": "AEM_MANUAL", "auto_exposure_bias": 0.0, "auto_exposure_apply_physical_camera_exposure": False,
             "bloom_intensity": 0.0, "vignette_intensity": 0.0, "film_grain_intensity": 0.0, "motion_blur_amount": 0.0,
             "lens_flare_intensity": 0.0, "scene_fringe_intensity": 0.0}
LOOKS = {"match": dict(PP_COMMON, tone_curve_amount=0.0),            # X3D-like: linear light to sRGB, nothing added
         "cinematic": dict(PP_COMMON, bloom_intensity=0.35)}          # filmic tone curve, a little bloom


def looks(ev100):
    made = []
    for name, props in LOOKS.items():
        v = spawn(unreal.PostProcessVolume)
        if v is None:
            continue
        setp(v, "unbound", True)
        setp(v, "enabled", name == "cinematic")
        s = attempt("pp settings", lambda: v.get_editor_property("settings"))
        if s is None:
            continue
        for k, val in dict(props, auto_exposure_bias=float(ev100)).items():
            if isinstance(val, str):
                val = getattr(unreal.AutoExposureMethod, val, val)
            if setp(s, k, val):
                setp(s, "override_" + k, True)
        setp(v, "settings", s)
        label(v, "Look_" + name)
        tag(v, "x3dlab_look", "x3dlab_look_" + name)
        made.append(name)
    return made


def sequences(content_path, cams):
    made = {}
    for name, cam in cams.items():
        seq_path = content_path + "/Seq"
        seq = attempt("create sequence", lambda: asset_tools().create_asset("Seq_" + name, seq_path, unreal.LevelSequence,
                                                                             unreal.LevelSequenceFactoryNew()))
        if seq is None:
            continue
        attempt("display rate", lambda: seq.set_display_rate(unreal.FrameRate(24, 1)))
        attempt("playback start", lambda: seq.set_playback_start(0))
        attempt("playback end", lambda: seq.set_playback_end(1))
        b = attempt("add_possessable", lambda: seq.add_possessable(cam))
        trk = attempt("camera cut track", lambda: seq.add_track(unreal.MovieSceneCameraCutTrack),
                      lambda: seq.add_master_track(unreal.MovieSceneCameraCutTrack))
        if trk is not None and b is not None:
            sec = trk.add_section()
            attempt("cut range", lambda: sec.set_range(-100, 100), lambda: (sec.set_start_frame(-100), sec.set_end_frame(100)))
            bid = attempt("binding id", lambda: seq.get_portable_binding_id(seq, b),
                          lambda: unreal.MovieSceneSequenceExtensions.get_portable_binding_id(seq, seq, b),
                          lambda: seq.get_binding_id(b))
            if bid is not None:
                attempt("camera binding", lambda: sec.set_camera_binding_id(bid))
        unreal.EditorAssetLibrary.save_loaded_asset(seq)
        made[name] = seq.get_path_name().split(".")[0]
    return made


def save_all():
    attempt("save level", lambda: level_sub().save_current_level(), lambda: unreal.EditorLevelLibrary.save_current_level())
    attempt("save dirty", lambda: unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True))


def choose_axes(job, centres):
    sys.path.insert(0, job["x3due_path"])
    from x3due import convert                 # pure Python, shared with the notebook (tested outside Unreal)
    return convert.choose_axes([tuple(p) for p in job.get("shape_centres_m", [])], [tuple(c) for c in centres])


def main():
    path = job_path()
    if not path:
        raise RuntimeError("no -X3DLabJob=... on the command line")
    job = json.load(open(path))
    REPORT.update(scene=job["scene"], engine=attempt("engine version", lambda: unreal.SystemLibrary.get_engine_version()))
    new_level(job["map"])
    before = set(all_actors())
    ok = import_usd(job["source"], job["content_path"]) if job.get("source_kind") == "usd" else import_gltf(job["source"], job["content_path"])
    REPORT["import_ok"] = ok
    imported = measure(job["content_path"], skip=before)
    light_actors = imported.pop("_light_actors")
    REPORT["imported"] = imported
    axes = choose_axes(job, imported["centres_cm"])
    REPORT["axes"] = axes
    log("axes: %s (errors %s)" % (axes.get("axes"), axes.get("errors_cm")))
    stage = job["placements"][axes["axes"]]
    if job.get("lights_from", "manifest") == "manifest":
        for a in light_actors:
            destroy(a)
        REPORT["lights_removed"] = len(light_actors)
        REPORT["lights_placed"] = place_lights(stage["lights"])
    cams = place_cameras(stage["cameras"])
    REPORT["cameras_placed"] = sorted(cams)
    REPORT["backdrop"] = backdrop(job["content_path"], job["shared_path"], stage["centre_cm"], stage["backdrop_radius_cm"],
                                  job["background_rgb"], job["key_rgb"])
    REPORT["looks"] = looks(job.get("ev100", 0.0))
    REPORT["sequences"] = sequences(job["content_path"], cams)
    save_all()
    REPORT["ok"] = bool(ok) and bool(REPORT["sequences"])
    log("done: %d sequences" % len(REPORT["sequences"]))
    return job


_job = None
try:
    _job = main()
except Exception as exc:
    REPORT["errors"].append("%s: %s" % (type(exc).__name__, exc))
    REPORT["trace"] = traceback.format_exc()[-3000:]
    unreal.log_error("X3DLAB: import failed: %s" % exc)
finally:
    REPORT["seconds"] = round(time.time() - REPORT["t0"], 2)
    out = (_job or {}).get("report") or os.path.join(os.path.dirname(job_path() or "/tmp/x"), "import_report.json")
    with open(out, "w") as fh:
        json.dump(REPORT, fh, indent=1, default=str)
