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
OPTIONAL_PLUGINS = ("Interchange", "GLTFImporter", "USDImporter", "NNEDenoiser")
NAME_OK = re.compile(r"^[A-Za-z0-9_]{1,60}$")


def ue_name(s: str) -> str:
    """A name Unreal accepts for assets and tags."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s or "x").strip("_") or "x"
    return ("S_" + s if s[0].isdigit() else s)[:60]


def write(dest, engine_dir=None, plan=None, ray_tracing=False, usd=False, cpus=4, workers=None, sm6=True, extra_cvars=None) -> dict:
    """Copy the template project to `dest` and fill it for this run. Plugins are enabled only if the engine has them
    (a .uproject naming a plugin the engine lacks stops an unattended start)."""
    dest = Path(dest)
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
    want.update({"Interchange": True, "GLTFImporter": False, "USDImporter": bool(usd), "NNEDenoiser": bool(ray_tracing)})
    plugins = [{"Name": p, "Enabled": on} for p, on in want.items() if have.get(p) in (True, None) or not on]
    up = json.loads((TEMPLATE / "X3DLab.uproject").read_text())
    up["Plugins"] = plugins
    (dest / "X3DLab.uproject").write_text(json.dumps(up, indent=1))
    workers = workers or max(1, cpus - 1)
    cvars = dict(extra_cvars or {})
    ini = (TEMPLATE / "Config/DefaultEngine.ini").read_text()
    fill = {"SHADER_PLATFORM": "SF_VULKAN_SM6" if sm6 else "SF_VULKAN_SM5", "RAY_TRACING": "True" if ray_tracing else "False",
            "HWRT_LIGHTING_MODE": "2" if ray_tracing else "0", "UNUSED_SHADER_THREADS": str(max(0, cpus - workers)),
            "SYSTEM_CVARS": "\n".join("%s=%s" % kv for kv in sorted(cvars.items()))}
    for k, v in fill.items():
        ini = ini.replace("{{%s}}" % k, v)
    left = re.findall(r"\{\{\w+\}\}", ini)
    if left:
        raise ValueError("unfilled template fields: %s" % left)
    (dest / "Config/DefaultEngine.ini").write_text(ini)
    missing = [p for p in REQUIRED_PLUGINS if have.get(p) is False]
    return {"project": str(dest / "X3DLab.uproject"), "plugins": plugins, "missing_required": missing, "workers": workers,
            "ray_tracing": ray_tracing, "sm6": sm6}


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
def render_jobs(scene: str, camera_names: list, rung_name: str, rung: dict, out_dir: str, report_path: str, look="cinematic",
                key=False, formats=("png", "exr"), override_res=None, mode="mrq") -> dict:
    name = ue_name(scene)
    res = list(override_res or rung["resolution"])
    jobs = []
    for cam in camera_names:
        jn = "%s__%s__%s%s" % (name, cam, rung_name, "__key" if key else "")
        jobs.append({"name": jn, "viewpoint": cam, "map": "/Game/X3DLab/%s/Map_%s" % (name, name),
                     "sequence": "/Game/X3DLab/%s/Seq/Seq_%s" % (name, cam), "output_dir": str(out_dir), "resolution": res,
                     "spatial": 1 if key else rung["spatial_samples"], "temporal": 1 if key else rung["temporal_samples"],
                     "renderer": "lumen" if key else rung["renderer"],
                     "cvars": ({"r.DynamicGlobalIlluminationMethod": 0, "r.ReflectionMethod": 0} if look == "match" else {}) | dict(rung["cvars"]),
                     "look": look, "backdrop": "key" if key else "scene", "formats": ["png"] if key else list(formats),
                     "tiles": rung.get("tiles", 1), "warmup": 4 if key else rung.get("warmup_frames", 32),
                     "texture_streaming": rung.get("texture_streaming", "default")})
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
