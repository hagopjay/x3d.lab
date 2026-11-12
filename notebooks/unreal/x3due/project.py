"""The Unreal side as data: the project folder, the import job and the render job (JSON files the Unreal scripts read).

    project.write(dest, engine_dir, plan)                 # X3DLab.uproject + Config/*.ini + Content/Python from ue_project/
    job = project.import_job(scene, source, manifest, ...)  # cameras and lights in Unreal units for every axis candidate
    rj = project.render_jobs(scene, manifest, rung, ...)    # one Movie Render Queue job per viewpoint

Nothing here runs Unreal; everything is validated (validate_*) before it is written, so the scripts inside Unreal only
ever see well-formed numbers and names.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from . import convert as C
from . import x3dscene

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "ue_project"
UE_SCRIPTS = HERE.parent / "ue_scripts"
REQUIRED_PLUGINS = ("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting", "MovieRenderPipeline")
# The path tracer's denoiser: OpenImageDenoise (CPU, runs on Linux). NNEDenoiser needs DirectML, which Linux lacks.
OPTIONAL_PLUGINS = ("Interchange", "GLTFImporter", "USDImporter", "OpenImageDenoise")
NAME_OK = re.compile(r"^[A-Za-z0-9_]{1,60}$")

# Renderer features chosen by engine version (12 Nov 2026, the owner's switch to 5.8). Each one changes which shaders
# get compiled, so it is decided before the first start, never by a retry:
#   substrate   Substrate materials: production-ready and on by default for new projects since 5.7. Left unset, the
#               GBuffer format is Unreal's default for an existing project (Adaptive, the full-quality one).
#   megalights  MegaLights: production-ready in 5.8. It needs SM6 and traces its shadow rays, so "auto" turns it on
#               only where hardware ray tracing is on (G4, L4); on the A100 (no RT cores) it stays off.
# EXISTS: the first version that has the setting at all (older engines get no line for it).
FEATURE_SINCE = {"substrate": (5, 7), "megalights": (5, 8)}
FEATURE_EXISTS = {"substrate": (5, 2), "megalights": (5, 5)}
FEATURE_KEYS = {"substrate": "r.Substrate", "megalights": "r.MegaLights.EnableForProject"}
# Console variables every run gets unless a caller overrides them. Async compute off: a device-lost crash faults
# Colab's GPU for the rest of the runtime (L4, 11 Nov 2026), and r.Vulkan.AllowAsyncCompute=0 is the workaround named
# for UE 5.8.0's device-lost crash on Linux. Offline stills lose next to nothing without it.
DEFAULT_SYSTEM_CVARS = {"r.Vulkan.AllowAsyncCompute": 0}
MCP_PLUGIN = re.compile(r"ModelContextProtocol|ToolsetRegistry|(?:^|[^A-Za-z])MCP", re.I)


def _vkey(version) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(version or ""))[:3])


def engine_version(engine_dir) -> str | None:
    """Engine/Build/Build.version -> "5.8.4" (None when the engine or the file is missing)."""
    try:
        d = json.loads(Path(engine_dir, "Engine/Build/Build.version").read_text())
        return "%s.%s.%s" % (d["MajorVersion"], d["MinorVersion"], d["PatchVersion"])
    except Exception:
        return None


def _wanted(setting) -> bool | None:
    s = str(setting).strip().lower() if setting is not None else "auto"
    if s in ("on", "true", "1", "yes"):
        return True
    if s in ("off", "false", "0", "no", "none"):
        return False
    if s == "auto":
        return None
    raise ValueError("feature setting %r: use auto, on or off" % (setting,))


def features_for(version, ray_tracing=False, substrate="auto", megalights="auto") -> dict:
    """The renderer features for this engine version and run, with the reason for each:
    {"substrate": bool, "megalights": bool, "why": {...}, "renderer": {ini key: "True"/"False"}}.
    "auto" follows FEATURE_SINCE (MegaLights also needs hardware ray tracing); "on"/"off" force it. A feature the
    engine does not have yet is off and gets no ini line, so older engines never see a key they do not know."""
    v = _vkey(version)
    out, why, renderer = {}, {}, {}
    for name, setting in (("substrate", substrate), ("megalights", megalights)):
        want = _wanted(setting)
        exists = bool(v) and v[:2] >= FEATURE_EXISTS[name]
        if not v:
            on, reason = False, "engine version unknown: left at the engine's default"
        elif not exists:
            on, reason = False, "Unreal %s has no %s" % (version, name)
        elif want is not None:
            on, reason = want, "forced %s" % ("on" if want else "off")
        elif v[:2] < FEATURE_SINCE[name]:
            on, reason = False, "auto: production-ready from %d.%d" % FEATURE_SINCE[name]
        elif name == "megalights" and not ray_tracing:
            on, reason = False, "auto: needs hardware ray tracing (software Lumen on this run)"
        else:
            on, reason = True, "auto: production-ready in %s" % version
        out[name], why[name] = on, reason
        if v and exists:
            renderer[FEATURE_KEYS[name]] = "True" if on else "False"
    return {**out, "why": why, "renderer": renderer, "version": version}


def mcp_plugins(engine_dir) -> list[str]:
    """Names of the engine's MCP plugins (Epic's Unreal MCP ships with 5.8). Recorded only: commandlets do not start
    its server, and the editor's starts on 127.0.0.1, so a batch run never depends on it."""
    root = Path(engine_dir or "", "Engine", "Plugins")
    if not engine_dir or not root.is_dir():
        return []
    return sorted({p.stem for p in root.glob("**/*.uplugin") if MCP_PLUGIN.search(p.stem)})


def ue_name(s: str) -> str:
    """A name Unreal accepts for assets and tags."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s or "x").strip("_") or "x"
    return ("S_" + s if s[0].isdigit() else s)[:60]


def write(dest, engine_dir=None, plan=None, ray_tracing=False, usd=False, cpus=4, workers=None, sm6=True, extra_cvars=None,
          version=None, features=None) -> dict:
    """Copy the template project to `dest` and fill it for this run. Plugins are enabled only if the engine has them
    (a .uproject naming a plugin the engine lacks stops an unattended start).
    version: the engine's ("5.8.4"; read from engine_dir when None). features: what the run asks for,
    {"substrate": "auto"|"on"|"off", "megalights": ..., "async_compute": False}; see features_for()."""
    dest = Path(dest)
    asked = dict(features or {})
    version = version or engine_version(engine_dir)
    feats = features_for(version, ray_tracing and sm6, asked.get("substrate", "auto"), asked.get("megalights", "auto"))
    if dest.exists():
        for sub in ("Config", "Content/Python"):
            shutil.rmtree(dest / sub, ignore_errors=True)
    shutil.copytree(TEMPLATE, dest, dirs_exist_ok=True)
    for script in UE_SCRIPTS.glob("*.py"):              # the import script lives outside the template; a rewrite must keep it
        shutil.copy(script, dest / "Content" / "Python" / script.name)
    have = {}
    for p in REQUIRED_PLUGINS + OPTIONAL_PLUGINS:
        have[p] = bool(list(Path(engine_dir, "Engine", "Plugins").glob("**/%s.uplugin" % p))) if engine_dir else None
    want = {p: True for p in REQUIRED_PLUGINS}
    want.update({"Interchange": True, "GLTFImporter": False, "USDImporter": bool(usd), "OpenImageDenoise": True})
    plugins = [{"Name": p, "Enabled": on} for p, on in want.items() if have.get(p) in (True, None) or not on]
    up = json.loads((TEMPLATE / "X3DLab.uproject").read_text())
    up["Plugins"] = plugins
    (dest / "X3DLab.uproject").write_text(json.dumps(up, indent=1))
    workers = workers or max(1, cpus - 1)
    cvars = {**({} if asked.get("async_compute") else DEFAULT_SYSTEM_CVARS), **(extra_cvars or {})}
    ini = (TEMPLATE / "Config/DefaultEngine.ini").read_text()
    fill = {"SHADER_PLATFORM": "SF_VULKAN_SM6" if sm6 else "SF_VULKAN_SM5", "RAY_TRACING": "True" if ray_tracing else "False",
            "HWRT_LIGHTING_MODE": "2" if ray_tracing else "0", "UNUSED_SHADER_THREADS": str(max(0, cpus - workers)),
            "RENDER_FEATURES": "\n".join("%s=%s" % kv for kv in sorted(feats["renderer"].items())),
            "SYSTEM_CVARS": "\n".join("%s=%s" % kv for kv in sorted(cvars.items()))}
    for k, v in fill.items():
        ini = ini.replace("{{%s}}" % k, v)
    left = re.findall(r"\{\{\w+\}\}", ini)
    if left:
        raise ValueError("unfilled template fields: %s" % left)
    (dest / "Config/DefaultEngine.ini").write_text(ini)
    missing = [p for p in REQUIRED_PLUGINS if have.get(p) is False]
    return {"project": str(dest / "X3DLab.uproject"), "plugins": plugins, "missing_required": missing, "workers": workers,
            "ray_tracing": ray_tracing, "sm6": sm6, "version": version, "features": feats, "system_cvars": cvars,
            "mcp_plugins": mcp_plugins(engine_dir)}


# ---------------------------------------------------------------------- import job
def _light(L, mapping: C.LightMapping, centre, axes, i):
    kind = {"DirectionalLight": "directional", "PointLight": "point", "SpotLight": "spot"}.get(L["type"])
    if kind is None:
        return None
    rgb = [max(0.0, min(1.0, x)) for x in (L.get("color") or [1, 1, 1])[:3]]
    out = {"name": ue_name(L.get("DEF") or "%s_%d" % (L["type"], i)), "kind": kind, "color": rgb, "on": bool(L.get("on", True)),
           "x3d_intensity": L["intensity"]}
    if kind == "directional":
        out["rotation"] = C.light_dir_to_ue(L["direction"], axes)
        out["location"] = C.to_ue_point(centre, axes)
        out["intensity"] = mapping.directional_lux(L["intensity"])
        out["unit"] = "lux"
    else:
        loc = L["location"]
        d = max(0.05, math.dist(loc, centre))
        out["location"] = C.to_ue_point(loc, axes)
        out["intensity"] = mapping.point_candela(L["intensity"], tuple(L.get("attenuation", (1, 0, 0))), d)
        out["unit"] = "cd"
        out["matched_at_m"] = round(d, 3)
        out["radius_cm"] = max(1.0, L.get("radius", 100.0) * C.CM_PER_M)
        if kind == "spot":
            out["rotation"] = C.light_dir_to_ue(L["direction"], axes)
            out["inner_deg"], out["outer_deg"] = C.spot_cones_deg(L.get("beamWidth", math.pi / 4), L.get("cutOffAngle", math.pi / 2))
        else:
            out["rotation"] = (0.0, 0.0, 0.0)
    return out


def placements(manifest: dict, width: int, height: int, mapping: C.LightMapping, axes: str) -> dict:
    """Cameras (CineCamera filmback matching the image aspect, focal length from the X3D field of view applied to the
    short side) and lights (Unreal units) for one axis mapping."""
    b = manifest.get("bounds") or ((-1, -1, -1), (1, 1, 1))
    centre = tuple((b[0][i] + b[1][i]) / 2 for i in range(3))
    radius = max(1.0, math.dist(b[0], b[1]) / 2)
    head = {"intensity": mapping.directional_lux(1.0)} if manifest.get("headlight", True) else None
    cams = []
    for vp in manifest["viewpoints"]:
        if vp["type"] != "Viewpoint":
            continue                                          # OrthoViewpoint / GeoViewpoint: not in phase 1
        loc, rot = C.viewpoint_to_ue(vp.get("world_matrix") or C.mat4_identity(), vp["position"], vp["orientation"], axes)
        hfov = C.x3d_fov_to_horizontal(vp.get("fieldOfView", math.pi / 4), width, height)
        fb = C.cine_filmback(hfov, width, height)
        cams.append({"name": ue_name(x3dscene.viewpoint_name(vp)), "description": vp.get("description", ""), "location": loc,
                     "rotation": rot, "hfov_deg": math.degrees(hfov), **fb, "headlight": head})
    lights = [x for x in (_light(L, mapping, centre, axes, i) for i, L in enumerate(manifest["lights"])) if x]
    return {"cameras": cams, "lights": lights, "centre_cm": C.to_ue_point(centre, axes),
            "backdrop_radius_cm": max(5000.0, radius * 40 * C.CM_PER_M)}


def import_job(scene: str, source: str, source_kind: str, manifest: dict, report_path: str, x3due_path: str,
               aspect=(16, 9), mapping: C.LightMapping | None = None, lights_from="manifest", key_rgb=(1.0, 0.0, 1.0),
               shape_centres=None) -> dict:
    mapping = mapping or C.LightMapping()
    name = ue_name(scene)
    bg = manifest["backgrounds"][0]["skyColor"][:3] if manifest.get("backgrounds") else [0.0, 0.0, 0.0]
    k = mapping.k
    job = {"scene": name, "source": str(source), "source_kind": source_kind,
           "content_path": "/Game/X3DLab/%s" % name, "map": "/Game/X3DLab/%s/Map_%s" % (name, name),
           "shared_path": "/Game/X3DLab/Shared", "report": str(report_path), "x3due_path": str(x3due_path),
           "lights_from": lights_from, "ev100": mapping.ev100,
           # the backdrop is unlit: its emissive colour is scaled by K so the picture shows the X3D colour itself
           "background_rgb": [c * k for c in bg], "key_rgb": [c * k for c in key_rgb],
           "shape_centres_m": [list(p) for p in (shape_centres if shape_centres is not None else manifest.get("shape_centres", []))],
           "placements": {a: placements(manifest, aspect[0] * 100, aspect[1] * 100, mapping, a) for a in C.AXES}}
    validate_import_job(job)
    return job


def validate_import_job(job: dict) -> None:
    assert job["source_kind"] in ("gltf", "usd"), "source_kind"
    assert NAME_OK.match(job["scene"]), "scene name"
    for k in ("content_path", "map", "shared_path"):
        assert re.match(r"^/Game/[A-Za-z0-9_/]+$", job[k]), k
    for a, st in job["placements"].items():
        assert a in C.AXES
        for c in st["cameras"]:
            assert NAME_OK.match(c["name"]), c["name"]
            assert all(math.isfinite(x) for x in list(c["location"]) + list(c["rotation"]) + [c["focal_length"]])
            assert 0 < c["hfov_deg"] < 180
        for L in st["lights"]:
            assert NAME_OK.match(L["name"]) and L["kind"] in ("directional", "point", "spot")
            assert math.isfinite(L["intensity"]) and L["intensity"] >= 0


# ---------------------------------------------------------------------- render job
# A grade: render-time changes applied inside Unreal before each still, so a look can be tuned without re-importing.
#   post:        PostProcessSettings fields set (with their override_ flag) on the active look volume, e.g. bloom_intensity,
#                bloom_threshold, auto_exposure_bias (EV), vignette_intensity, film_grain_intensity, scene_fringe_intensity,
#                depth_of_field_fstop, depth_of_field_focal_distance (cm), white_temp, lumen_final_gather_quality
#   light_scale: multiplies every light the import placed (tag x3dlab_light)
#   headlight:   false switches the viewpoint headlights off (the scene's own lights and emissive surfaces only)
#   cvars:       console variables on top of the rung's (e.g. r.DepthOfFieldQuality 2 to let depth of field show)
#   aperture:    f-stop of the viewpoint's CineCamera (with focus: depth of field; needs r.DepthOfFieldQuality > 0)
#   focus:       "auto" (the middle of the imported meshes, backdrop excluded) or a distance in cm
POST_KEY = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


def grade_ok(grade: dict | None) -> dict:
    g = dict(grade or {})
    unknown = set(g) - {"post", "light_scale", "headlight", "cvars", "name", "aperture", "focus"}
    assert not unknown, "unknown grade keys %s" % sorted(unknown)
    for k, v in (g.get("post") or {}).items():
        assert POST_KEY.match(k) and not k.startswith("override_"), k
        assert isinstance(v, (bool, str)) or (isinstance(v, (int, float)) and math.isfinite(v)), (k, v)
    if "light_scale" in g:
        assert isinstance(g["light_scale"], (int, float)) and 0 <= g["light_scale"] <= 1e4
    if "aperture" in g:
        assert isinstance(g["aperture"], (int, float)) and 0.5 <= g["aperture"] <= 64
    if "focus" in g:
        assert g["focus"] == "auto" or (isinstance(g["focus"], (int, float)) and 1 <= g["focus"] <= 1e7)
    for k, v in (g.get("cvars") or {}).items():
        assert re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", k) and isinstance(v, (int, float)) and math.isfinite(v), (k, v)
    return g


def render_jobs(scene: str, camera_names: list, rung_name: str, rung: dict, out_dir: str, report_path: str, look="cinematic",
                key=False, formats=("png", "exr"), override_res=None, mode="mrq", grade=None, tag="") -> dict:
    name = ue_name(scene)
    res = list(override_res or rung["resolution"])
    jobs = []
    for cam in camera_names:
        jn = "%s__%s__%s%s%s" % (name, cam, rung_name, "__key" if key else "", ("__" + ue_name(tag)) if tag else "")
        jobs.append({"name": jn, "viewpoint": cam, "map": "/Game/X3DLab/%s/Map_%s" % (name, name),
                     "sequence": "/Game/X3DLab/%s/Seq/Seq_%s" % (name, cam), "output_dir": str(out_dir), "resolution": res,
                     "spatial": 1 if key else rung["spatial_samples"], "temporal": 1 if key else rung["temporal_samples"],
                     "renderer": "lumen" if key else rung["renderer"],
                     "cvars": ({"r.DynamicGlobalIlluminationMethod": 0, "r.ReflectionMethod": 0} if look == "match" else {}) | dict(rung["cvars"]),
                     "look": look, "backdrop": "key" if key else "scene", "formats": ["png"] if key else list(formats),
                     "tiles": rung.get("tiles", 1), "warmup": 4 if key else rung.get("warmup_frames", 32),
                     "texture_streaming": rung.get("texture_streaming", "default")})
        if grade:
            g = grade_ok(grade)
            jobs[-1]["cvars"] = dict(jobs[-1]["cvars"], **(g.get("cvars") or {}))
            jobs[-1]["grade"] = {k: v for k, v in g.items() if k != "cvars"}
    spec = {"scene": name, "report": str(report_path), "mode": mode, "jobs": jobs}
    validate_render_job(spec)
    return spec


def validate_render_job(spec: dict) -> None:
    assert spec["mode"] in ("mrq", "highresshot")
    for j in spec["jobs"]:
        assert re.match(r"^[A-Za-z0-9_]+$", j["name"]), j["name"]
        assert j["renderer"] in ("lumen", "pathtracer") and j["look"] in ("match", "cinematic")
        assert 16 <= j["resolution"][0] <= 16384 and 16 <= j["resolution"][1] <= 16384
        assert 1 <= j["spatial"] <= 4096 and 1 <= j["temporal"] <= 4096
        for k, v in j["cvars"].items():
            assert re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", k), k
            assert isinstance(v, (int, float)) and math.isfinite(v), (k, v)
