"""Runs INSIDE Unreal in -game mode: Movie Render Queue driven by a Python host executor.

    UnrealEditor X3DLab.uproject /Game/X3DLab/<scene>/Map -game -RenderOffscreen -vulkan
        -MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor
        -ExecutorPythonClass=/Engine/PythonTypes.X3DLabExecutor -X3DLabJob=/abs/render_job.json

Epic's command-line rendering page documents this executor pair (its example class is
MoviePipelineExampleRuntimeExecutor); this one reads its jobs from a JSON file the notebook wrote (data only: map,
sequence, resolution, samples, console variables, look, output folder) and builds every Movie Render Queue job in
memory, so no queue or config asset has to exist. Each job reopens the map (a fresh world per still). Before each
job it switches on that viewpoint's headlight, picks the look (match / cinematic) and the backdrop (scene / key).

mode "highresshot" is the fallback when Movie Render Queue does not run: view target + HighResShot, no MRQ.
A report JSON is rewritten after every job, so the notebook sees progress and a partial run still says something.
"""
import glob
import json
import os
import shutil
import time

import unreal

STATE = {"spec": None, "jobs": [], "index": -1, "report": {"jobs": [], "api_misses": [], "t0": time.time()},
         "frames": 0, "phase": None}


def log(msg):
    unreal.log("X3DLAB: %s" % msg)


def miss(what, exc):
    STATE["report"]["api_misses"].append("%s: %s" % (what, str(exc)[:200]))


def attempt(what, *fns):
    last = None
    for fn in fns:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
    miss(what, last)
    return None


def write_report(final=False):
    spec = STATE["spec"] or {}
    r = STATE["report"]
    r["final"] = final
    r["seconds"] = round(time.time() - r["t0"], 2)
    path = spec.get("report")
    if path:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(r, fh, indent=1, default=str)
        os.replace(tmp, path)
        if final:
            with open(path + ".done", "w") as fh:
                fh.write("done\n")


def job_path():
    try:
        _, _, params = unreal.SystemLibrary.parse_command_line(unreal.SystemLibrary.get_command_line())
        for k, v in params.items():
            if k.lower() == "x3dlabjob":
                return v.strip('"')
    except Exception as exc:
        miss("parse_command_line", exc)
    return os.environ.get("X3DLAB_JOB")


def actors_with_tag(world, t):
    return attempt("actors with tag %s" % t, lambda: list(unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(t)))) or []


def prepare_world(world, job):
    """The active viewpoint's headlight on, the others off; one look; one backdrop."""
    for a in actors_with_tag(world, "x3dlab_headlight"):
        on = ("x3dlab_headlight_" + job["viewpoint"]) in [str(t) for t in a.tags]
        comp = attempt("headlight comp", lambda: a.get_component_by_class(unreal.LightComponent))
        if comp is not None:
            attempt("headlight visibility", lambda: comp.set_visibility(on))
    for look in ("match", "cinematic"):
        for v in actors_with_tag(world, "x3dlab_look_" + look):
            attempt("look enable", lambda: v.set_editor_property("enabled", look == job.get("look", "cinematic")))
    apply_grade(world, job)
    key = job.get("backdrop") == "key"
    for a in actors_with_tag(world, "x3dlab_backdrop"):
        attempt("backdrop", lambda: a.set_actor_hidden_in_game(key))
    for a in actors_with_tag(world, "x3dlab_backdropkey"):
        attempt("backdrop key", lambda: a.set_actor_hidden_in_game(not key))


def _set(obj, k, v):
    obj.set_editor_property(k, v)
    return True


def _enum_value(k, v):
    """Strings name enum members: auto_exposure_method "AEM_MANUAL" etc."""
    if not isinstance(v, str):
        return v
    for enum in ("AutoExposureMethod", "BloomMethod", "TemperatureMethod"):
        e = getattr(unreal, enum, None)
        if e is not None and hasattr(e, v):
            return getattr(e, v)
    return v


def _scene_centre(world):
    """Middle of the imported meshes (the backdrop and the lab's own actors excluded), from their bounds."""
    lo, hi = [1e30] * 3, [-1e30] * 3
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor):
        tags = [str(t) for t in a.tags]
        if any(t.startswith("x3dlab_backdrop") for t in tags) or "sky" in str(a.get_actor_label()).lower():
            continue
        o, e = a.get_actor_bounds(False)
        for i, (c, x) in enumerate(((o.x, e.x), (o.y, e.y), (o.z, e.z))):
            lo[i], hi[i] = min(lo[i], c - x), max(hi[i], c + x)
    if lo[0] > hi[0]:
        return None
    return [(lo[i] + hi[i]) / 2 for i in range(3)]


def _camera_focus(world, job, g):
    """Depth of field on the viewpoint's CineCamera: manual focus at `focus` cm ("auto": its distance to the scene
    centre) and the f-stop `aperture`."""
    cams = actors_with_tag(world, "x3dlab_vp_" + job["viewpoint"])
    if not cams:
        return None
    cam = cams[0]
    comp = cam.get_cine_camera_component() if hasattr(cam, "get_cine_camera_component") else cam.get_component_by_class(unreal.CineCameraComponent)
    dist = g.get("focus", "auto")
    if dist == "auto":
        c = _scene_centre(world)
        p = cam.get_actor_location()
        dist = ((p.x - c[0]) ** 2 + (p.y - c[1]) ** 2 + (p.z - c[2]) ** 2) ** 0.5 if c else 1000.0
    fs = comp.get_editor_property("focus_settings")
    fs.set_editor_property("focus_method", unreal.CameraFocusMethod.MANUAL)
    fs.set_editor_property("manual_focus_distance", float(dist))
    comp.set_editor_property("focus_settings", fs)
    if "aperture" in g:
        attempt("aperture", lambda: comp.set_editor_property("current_aperture", float(g["aperture"])))
    return round(float(dist), 1)


def apply_grade(world, job):
    """The job's grade (project.grade_ok): post-process fields on the active look volume, light scale, headlights.
    The map is reopened for every job, so nothing here carries over to the next still."""
    g = job.get("grade") or {}
    if not g:
        return
    done = STATE["report"].setdefault("grade_applied", {})
    post = g.get("post") or {}
    if post:
        look = job.get("look", "cinematic")
        for v in actors_with_tag(world, "x3dlab_look_" + look):
            s = attempt("grade settings", lambda: v.get_editor_property("settings"))
            if s is None:
                continue
            for k, val in post.items():
                if attempt("grade %s" % k, lambda: _set(s, k, _enum_value(k, val))):
                    attempt("grade override_%s" % k, lambda: _set(s, "override_" + k, True))
            attempt("grade settings back", lambda: v.set_editor_property("settings", s))
            done["post"] = sorted(post)
    if "light_scale" in g:
        n = 0
        for a in actors_with_tag(world, "x3dlab_light"):
            comp = attempt("grade light comp", lambda: a.get_component_by_class(unreal.LightComponent))
            if comp is not None:
                cur = attempt("grade intensity", lambda: comp.get_editor_property("intensity"))
                if cur is not None:
                    attempt("grade set intensity", lambda: comp.set_intensity(float(cur) * float(g["light_scale"])))
                    n += 1
        done["lights_scaled"] = n
    if "aperture" in g or "focus" in g:
        done["focus_cm"] = attempt("grade focus", lambda: _camera_focus(world, job, g))
    if g.get("headlight") is False:
        for a in actors_with_tag(world, "x3dlab_headlight"):
            comp = attempt("headlight comp", lambda: a.get_component_by_class(unreal.LightComponent))
            if comp is not None:
                attempt("headlight off", lambda: comp.set_visibility(False))
        done["headlight"] = False


def configure(job_obj, job):
    """Movie Render Queue settings for one still, built in code."""
    cfg = job_obj.get_configuration()
    out = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    w, h = job["resolution"]
    out.set_editor_property("output_resolution", unreal.IntPoint(w, h))
    out.set_editor_property("output_directory", unreal.DirectoryPath(job["output_dir"]))
    out.set_editor_property("file_name_format", "{job_name}.{frame_number}")
    attempt("override existing", lambda: out.set_editor_property("override_existing_output", True))
    if job.get("renderer") == "pathtracer":
        cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPass_PathTracer)
    else:
        cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    if "png" in job.get("formats", ["png"]):
        cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    if "exr" in job.get("formats", []):
        cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_EXR)
    aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    for k, v in (("spatial_sample_count", int(job.get("spatial", 1))), ("temporal_sample_count", int(job.get("temporal", 8))),
                 ("override_anti_aliasing", True), ("anti_aliasing_method", unreal.AntiAliasingMethod.AAM_NONE),
                 ("render_warm_up_count", int(job.get("warmup", 32))), ("engine_warm_up_count", int(job.get("warmup", 32)))):
        attempt("aa.%s" % k, lambda: aa.set_editor_property(k, v))
    go = attempt("game overrides", lambda: cfg.find_or_add_setting_by_class(unreal.MoviePipelineGameOverrideSetting))
    if go is not None:
        attempt("cinematic quality", lambda: go.set_editor_property("cinematic_quality_settings", True))
        attempt("lod zero", lambda: go.set_editor_property("use_lod_zero", True))
        if job.get("texture_streaming") == "fully_load":
            attempt("texture streaming", lambda: go.set_editor_property("texture_streaming",
                                                                         unreal.MoviePipelineTextureStreamingMethod.FULLY_LOAD))
    if int(job.get("tiles", 1)) > 1:
        hr = cfg.find_or_add_setting_by_class(unreal.MoviePipelineHighResSetting)
        attempt("tiles", lambda: hr.set_editor_property("tile_count", int(job["tiles"])))
    cv = cfg.find_or_add_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
    cmds = []
    for k, v in sorted(job.get("cvars", {}).items()):
        if attempt("cvar %s" % k, lambda: cv.add_or_update_console_variable(k, float(v))) is None:
            cmds.append("%s %s" % (k, v))
    if cmds:
        attempt("start console commands", lambda: cv.set_editor_property("start_console_commands", cmds))


@unreal.uclass()
class X3DLabExecutor(unreal.MoviePipelinePythonHostExecutor):
    active_pipeline = unreal.uproperty(unreal.MoviePipeline)
    queue = unreal.uproperty(unreal.MoviePipelineQueue)

    def _post_init(self):
        self.active_pipeline = None
        self.queue = None

    @unreal.ufunction(override=True)
    def execute_delayed(self, in_pipeline_queue):
        path = job_path()
        log("executor start, job %s" % path)
        try:
            STATE["spec"] = json.load(open(path))
        except Exception as exc:
            STATE["report"]["error"] = "cannot read job %s: %s" % (path, exc)
            write_report(True)
            self.finish()
            return
        STATE["jobs"] = STATE["spec"]["jobs"]
        STATE["report"]["engine"] = attempt("engine version", lambda: unreal.SystemLibrary.get_engine_version())
        STATE["report"]["rhi"] = attempt("rhi name", lambda: unreal.SystemLibrary.get_rendering_device_name() if hasattr(
            unreal.SystemLibrary, "get_rendering_device_name") else None)
        self.queue = unreal.new_object(unreal.MoviePipelineQueue, outer=self)
        self.next_job()

    def next_job(self):
        STATE["index"] += 1
        if STATE["index"] >= len(STATE["jobs"]):
            write_report(True)
            self.finish()
            return
        job = STATE["jobs"][STATE["index"]]
        STATE["report"]["jobs"].append({"name": job["name"], "t_start": time.time()})
        write_report()
        log("job %d/%d %s: opening %s" % (STATE["index"] + 1, len(STATE["jobs"]), job["name"], job["map"]))
        unreal.GameplayStatics.open_level(self.get_last_loaded_world(), job["map"], True,
                                          "?game=/Script/MovieRenderPipelineCore.MoviePipelineGameMode")

    @unreal.ufunction(override=True)
    def on_map_load(self, in_world):
        if STATE["index"] < 0 or STATE["index"] >= len(STATE["jobs"]):
            return
        job = STATE["jobs"][STATE["index"]]
        rec = STATE["report"]["jobs"][-1]
        rec["t_map"] = time.time()
        world = self.get_last_loaded_world()
        prepare_world(world, job)
        if STATE["spec"].get("mode", "mrq") == "highresshot":
            self.start_highresshot(world, job)
            return
        try:
            self.queue.delete_all_jobs()
        except Exception:
            pass
        mjob = self.queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        mjob.set_editor_property("job_name", job["name"])
        mjob.set_editor_property("map", unreal.SoftObjectPath(job["map"]))
        mjob.set_editor_property("sequence", unreal.SoftObjectPath(job["sequence"]))
        try:
            configure(mjob, job)
        except Exception as exc:
            rec["error"] = "configure: %s" % exc
            log(rec["error"])
        self.active_pipeline = unreal.new_object(self.target_pipeline_class, outer=world, base_type=unreal.MoviePipeline)
        self.active_pipeline.on_movie_pipeline_work_finished_delegate.add_function_unique(self, "on_job_finished")
        self.active_pipeline.initialize(mjob)
        log("job %s: rendering %dx%d, %s, %s x %s samples" % (job["name"], job["resolution"][0], job["resolution"][1],
                                                              job.get("renderer"), job.get("spatial"), job.get("temporal")))

    @unreal.ufunction(override=True)
    def is_rendering(self):
        return self.active_pipeline is not None or STATE["phase"] is not None

    @unreal.ufunction(ret=None, params=[unreal.MoviePipelineOutputData])
    def on_job_finished(self, results):
        rec = STATE["report"]["jobs"][-1]
        job = STATE["jobs"][STATE["index"]]
        rec["t_end"] = time.time()
        rec["seconds"] = round(rec["t_end"] - rec["t_start"], 2)
        rec["success"] = attempt("results.success", lambda: bool(results.success))
        rec["files"] = sorted(glob.glob(os.path.join(job["output_dir"], job["name"] + ".*")))
        log("wrote %s (%d files, %.1f s)" % (job["name"], len(rec["files"]), rec["seconds"]))
        self.active_pipeline = None
        write_report()
        self.next_job()

    # ------------------------------------------------------------------ fallback: no Movie Render Queue
    def start_highresshot(self, world, job):
        pc = unreal.GameplayStatics.get_player_controller(world, 0)
        cams = [a for a in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name("x3dlab_vp_" + job["viewpoint"]))]
        if pc is not None and cams:
            attempt("view target", lambda: pc.set_view_target_with_blend(cams[0], 0.0))
        for k, v in job.get("cvars", {}).items():
            unreal.SystemLibrary.execute_console_command(world, "%s %s" % (k, v))
        STATE["frames"], STATE["phase"] = 0, "warmup"
        STATE["shot_dir"] = os.path.join(unreal.Paths.project_saved_dir(), "Screenshots")
        STATE["shot_before"] = set(glob.glob(os.path.join(STATE["shot_dir"], "**", "*.png"), recursive=True))

    @unreal.ufunction(override=True)
    def on_begin_frame(self):
        super(X3DLabExecutor, self).on_begin_frame()
        if STATE["phase"] is None:
            return
        STATE["frames"] += 1
        job = STATE["jobs"][STATE["index"]]
        world = self.get_last_loaded_world()
        if STATE["phase"] == "warmup" and STATE["frames"] >= int(job.get("warmup", 32)) + int(job.get("temporal", 8)):
            w, h = job["resolution"]
            unreal.SystemLibrary.execute_console_command(world, "HighResShot %dx%d" % (w, h))
            STATE["phase"], STATE["frames"] = "shot", 0
        elif STATE["phase"] == "shot":
            new = set(glob.glob(os.path.join(STATE["shot_dir"], "**", "*.png"), recursive=True)) - STATE["shot_before"]
            if new or STATE["frames"] > 600:
                rec = STATE["report"]["jobs"][-1]
                os.makedirs(job["output_dir"], exist_ok=True)
                files = []
                for i, f in enumerate(sorted(new)):
                    dst = os.path.join(job["output_dir"], "%s.%04d.png" % (job["name"], i))
                    shutil.move(f, dst)
                    files.append(dst)
                rec.update(files=files, success=bool(files), t_end=time.time(), mode="highresshot")
                rec["seconds"] = round(rec["t_end"] - rec["t_start"], 2)
                log("wrote %s (highresshot, %d files)" % (job["name"], len(files)))
                STATE["phase"] = None
                write_report()
                self.next_job()

    def finish(self):
        log("executor finished")
        attempt("executor finished", lambda: self.on_executor_finished_impl())
        attempt("quit", lambda: unreal.SystemLibrary.execute_console_command(None, "quit"))
