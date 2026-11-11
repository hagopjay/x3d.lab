"""The steps of notebook 07, as functions the cells call (and the tests drive with fake runners).

    lab = pipeline.Lab(work="/content/x3due", engine_dir=..., hw=hw, plan=plan)
    pipeline.get_engine(lab, "auto", drive_dir)            # cache in Drive -> your zip in Drive -> ghcr image
    pipeline.smoke(lab)                                    # cold start: RHI proof + one-time shader compile time
    rep = pipeline.import_scene(lab, "stilllife", glb, "gltf", manifest)
    out = pipeline.render(lab, "stilllife", cams, "high", rung)

Unreal always runs as the normal user `lab.user`, in its own process per scene (a fresh world, no state leaking
between scenes: the lesson of the Babylon bridge's eight false failures). Every Unreal process is retried along
uerun.RETRY when its log shows a known crash signature, and every attempt is recorded.
"""

from __future__ import annotations

import glob
import re
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import convert as C
from . import engine as E
from . import oci, project, uerun

HERE = Path(__file__).resolve().parent


class Lab:
    def __init__(self, work="/content/x3due", engine_dir=None, hw=None, plan=None, user="ue", log=print, runner=None):
        self.work = Path(work)
        self.engine_dir = Path(engine_dir) if engine_dir else self.work / "engine"
        self.project_dir = self.work / "X3DLab"
        self.out = self.work / "out"
        self.ddc = self.work / "ddc"
        self.home = Path("/home") / user if os.geteuid() == 0 else Path.home()
        self.hw, self.plan, self.user, self.log = hw or {}, plan or {}, user, log
        self.runner = runner or uerun.run              # tests pass a fake
        self.settings = {"cvars": {}, "ray_tracing": bool((plan or {}).get("ray_tracing", {}).get("hw_rt")), "sm6": True,
                         "env": {}, "xvfb": False, "bindless": "RayTracingOnly", "usd": False}
        self.mapping = C.LightMapping()
        self.timings, self.attempts = {}, []
        for d in (self.work, self.out, self.ddc):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ project and environment
    def write_project(self):
        pools = {}
        rung = (self.plan.get("ladder") or {}).get("high") or {}
        for k, v in rung.get("cvars", {}).items():
            if k.startswith(("r.Streaming.PoolSize", "r.Nanite.Streaming", "r.VT.PoolSize")):
                pools[k] = v
        return project.write(self.project_dir, self.engine_dir, ray_tracing=self.settings["ray_tracing"], usd=self.settings["usd"],
                             cpus=self.hw.get("cpus", 4), workers=self.plan.get("shader_workers"), sm6=self.settings["sm6"],
                             extra_cvars={**pools, **self.settings["cvars"]})

    def env(self):
        return uerun.env(str(self.home), str(self.ddc), extra=self.settings["env"])

    def own(self):
        """Create the user and hand it the work folder and the engine. The engine (~200k files) is chowned once;
        the work folder every time (new scenes and jobs appear in it)."""
        paths = [self.work] + ([] if getattr(self, "_engine_owned", False) else [self.engine_dir])
        self._engine_owned = True
        return uerun.ensure_user(self.user, paths)

    def proj(self):
        return self.project_dir / "X3DLab.uproject"


# ---------------------------------------------------------------------- engine
def get_engine(lab: Lab, source="auto", drive_dir=None, ghcr_tag=None, ghcr_user=None, ghcr_token=None, trim=E.TRIM_SAFE,
               want="newest") -> dict:
    """Engine onto local disk. Returns {"source", "version", "seconds", ...}; raises with an actionable message.
    want: the engine version to use ("5.4", "5.8.4", "newest"): an engine already on disk, a Drive cache archive and a
    Drive zip are all chosen by it, so changing it in the options switches engines."""
    t0 = time.time()
    dest = lab.engine_dir
    have = E.check_engine(dest)
    if have["ok"]:
        if want in (None, "", "newest") or E.pick_version(["UE_%s" % have.get("version")], want):
            return {"source": "already on disk", **have, "seconds": 0}
        lab.log("engine %s on disk is not the %s asked for: removing it" % (have.get("version"), want))
        shutil.rmtree(dest, ignore_errors=True)
    tried = []
    drive_dir = Path(drive_dir) if drive_dir else None
    if source in ("auto", "cache") and drive_dir:
        arcs = E.pick_version(glob.glob(str(drive_dir / "cache" / "UE_*_trimmed.tar.*")), want)
        if arcs:
            lab.log("restoring the trimmed engine from your Drive: %s" % os.path.basename(arcs[-1]))
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = E.run_logged(E.unarchive_cmd(arcs[-1], dest.parent), lab.log)
            side = Path(arcs[-1].split(".tar")[0] + ".json")
            top = json.loads(side.read_text()).get("top") if side.exists() else None
            if top and (dest.parent / top).exists() and not dest.exists():
                (dest.parent / top).rename(dest)
            if E.check_engine(dest)["ok"]:
                return {"source": "drive cache", "archive": arcs[-1], **E.check_engine(dest), "seconds": round(time.time() - t0, 1)}
            tried.append("cache %s did not give a working engine (rc %s)" % (arcs[-1], r["rc"]))
        else:
            tried.append("no cache archive for %s in %s/cache" % (want, drive_dir))
    if source in ("auto", "zip") and drive_dir:
        zips = E.pick_version(E.find_zips(drive_dir), want)
        if zips:
            z = zips[-1]
            plan = E.zip_plan(z, trim)
            if not plan["ok"]:
                raise RuntimeError(plan["why"])
            free = shutil.disk_usage(dest.parent if dest.parent.exists() else "/").free / 2**30
            strat = E.choose_strategy(plan, free)
            lab.log("engine zip %s: %.1f GB zipped, %.1f GB to extract after trimming (%.1f untrimmed); %s"
                    % (os.path.basename(z), plan["zip_gb"], plan["keep_gb"], plan["full_gb"], strat["mode"]))
            if strat["mode"] == "none":
                raise RuntimeError(strat["why"])
            src = z
            if strat["mode"] == "copy_then_extract":
                src = str(lab.work / os.path.basename(z))
                if not (os.path.exists(src) and os.path.getsize(src) == os.path.getsize(z)):
                    lab.timings["engine_copy"] = E.copy_with_progress(z, src, log=lab.log)
            st = E.extract_zip(src, dest, trim, log=lab.log)
            if src != z:
                os.remove(src)
            E.fix_exec_bits(dest)
            chk = E.check_engine(dest)
            if not chk["ok"]:
                raise RuntimeError("the extracted engine is missing %s" % chk["missing"])
            return {"source": "drive zip", "zip": z, "plan": plan, "strategy": strat, "extract": st, **chk,
                    "seconds": round(time.time() - t0, 1)}
        tried.append("no Linux_Unreal_Engine_*.zip for %s in %s (found: %s)" % (
            want, drive_dir, ", ".join(os.path.basename(z) for z in E.find_zips(drive_dir)) or "none"))
    if source in ("auto", "ghcr") and ghcr_token and ghcr_tag:
        reg = oci.Registry("ghcr.io", ghcr_user, ghcr_token)
        m = reg.manifest("epicgames/unreal-engine", ghcr_tag)
        lab.log("ghcr.io/epicgames/unreal-engine:%s: %d layers, %.1f GB compressed"
                % (ghcr_tag, len(m.get("layers", [])), sum(l.get("size", 0) for l in m.get("layers", [])) / 2**30))
        rep = oci.extract_image(reg, "epicgames/unreal-engine", m, dest, exclude=oci.DEFAULT_EXCLUDE + tuple(trim), log=lab.log)
        E.fix_exec_bits(dest)
        chk = E.check_engine(dest)
        if not chk["ok"]:
            raise RuntimeError("the unpacked image has no working engine (missing %s)" % chk["missing"])
        return {"source": "ghcr", "tag": ghcr_tag, "pull": rep, **chk, "seconds": round(time.time() - t0, 1)}
    if source in ("auto", "ghcr") and not ghcr_token:
        tried.append("no GHCR_TOKEN secret, so the container route was not tried")
    raise RuntimeError("no engine found: " + "; ".join(tried) + ". See 'Your one-time step' at the top of the notebook.")


def cache_engine(lab: Lab, drive_dir, version) -> dict:
    """Write the trimmed engine to your Drive (tar + zstd), if there is room. Your own copy in your own Drive."""
    cache = Path(drive_dir) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    arc = cache / ("UE_%s_trimmed.tar.%s" % (version, "zst" if E.tool("zstd") else "gz"))
    if arc.exists():
        return {"skipped": "already cached", "archive": str(arc)}
    size = E.du_gb(lab.engine_dir)
    free = E.drive_free_gb(str(cache))
    if free is not None and free < size * 0.7 + 2:
        return {"skipped": "Drive has %.0f GB free; the archive needs about %.0f GB" % (free, size * 0.6)}
    tmp = lab.work / arc.name
    r = E.run_logged(E.archive_cmd(lab.engine_dir, tmp), lab.log)
    if r["rc"]:
        return {"error": "tar failed", **r}
    t = time.time()
    shutil.copyfile(tmp, str(arc) + ".part")
    os.replace(str(arc) + ".part", arc)
    os.remove(tmp)
    Path(str(arc).split(".tar")[0] + ".json").write_text(json.dumps({"top": lab.engine_dir.name, "version": version}))
    return {"archive": str(arc), "gb": round(arc.stat().st_size / 2**30, 2), "tar_s": r["seconds"], "copy_s": round(time.time() - t, 1)}


def ddc_restore(lab: Lab, drive_dir) -> dict:
    arc = next((Path(drive_dir) / "cache" / n for n in ("ddc.tar.zst", "ddc.tar.gz") if (Path(drive_dir) / "cache" / n).exists()), None)
    if arc is None:
        return {"skipped": "no DDC in Drive yet (first run: shaders compile from scratch)"}
    r = E.run_logged(E.unarchive_cmd(arc, lab.ddc.parent), lab.log)
    return {"restored": str(arc), **r}


def ddc_save(lab: Lab, drive_dir) -> dict:
    subprocess.run(["pkill", "-u", lab.user, "-f", "zenserver"], capture_output=True)   # only the user this notebook made
    arc = Path(drive_dir) / "cache" / ("ddc.tar.%s" % ("zst" if E.tool("zstd") else "gz"))
    arc.parent.mkdir(parents=True, exist_ok=True)
    tmp = lab.work / arc.name
    r = E.run_logged(E.archive_cmd(lab.ddc, tmp), lab.log)
    if r["rc"]:
        return {"error": "tar failed"}
    shutil.copyfile(tmp, str(arc) + ".part")
    os.replace(str(arc) + ".part", arc)
    return {"archive": str(arc), "gb": round(arc.stat().st_size / 2**30, 2)}


# ---------------------------------------------------------------------- running Unreal with the retry ladder
def apply_change(lab: Lab, change: dict) -> None:
    if "cvars" in change:
        lab.settings["cvars"].update(change["cvars"])
    if "bindless" in change:
        lab.settings["bindless"] = change["bindless"]
        E.enable_vulkan_rt(lab.engine_dir, change["bindless"])
    if change.get("ray_tracing") is False:
        lab.settings["ray_tracing"] = False
        E.disable_vulkan_rt(lab.engine_dir)
    if "env" in change:
        lab.settings["env"].update(change["env"])
    if change.get("xvfb"):
        lab.settings["xvfb"] = True
        if not shutil.which("xvfb-run"):
            subprocess.run(["apt-get", "install", "-y", "-qq", "xvfb"], capture_output=True)
    if change.get("sm6") is False:
        lab.settings["sm6"] = False
    lab.write_project()


def restore_settings(lab: Lab, saved: dict) -> None:
    """Put lab.settings back to `saved`, including the engine's Vulkan ray-tracing config and the project files."""
    rt_was, rt_now = saved.get("ray_tracing"), lab.settings.get("ray_tracing")
    lab.settings.clear()
    lab.settings.update(json.loads(json.dumps(saved)))
    if rt_was and not rt_now:
        E.enable_vulkan_rt(lab.engine_dir, lab.settings.get("bindless", "RayTracingOnly"))
    lab.write_project()


def run_unreal(lab: Lab, make_cmd, label, log_dir, timeout=7200, done_file=None, max_attempts=4, need_render=True) -> dict:
    """make_cmd(lab) -> argv. Runs, reads the log, retries along uerun.RETRY. Returns the last attempt plus the list."""
    tried, attempts = [], []
    before = json.loads(json.dumps(lab.settings))      # a change that doesn't help is rolled back (see below)
    ok = False
    for k in range(max_attempts):
        cmd = make_cmd(lab)
        if lab.settings["xvfb"]:
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd
        log_path = Path(log_dir) / ("%s.%d.log" % (label, k))
        if done_file and os.path.exists(done_file):
            os.remove(done_file)
        res = lab.runner(cmd, log_path, user=lab.user, env_vars=lab.env(), timeout=timeout, done_file=done_file)
        text = Path(log_path).read_text(errors="replace") if Path(log_path).exists() else ""
        proof = uerun.rhi_proof(text)
        res.update(proof=proof, attempt=k, change=tried[-1] if tried else None, timing=uerun.first_render_seconds(text))
        attempts.append(res)
        lab.attempts.append({"label": label, **{x: res.get(x) for x in ("rc", "seconds", "killed", "change")},
                             "verdict": proof.get("verdict"), "crashes": [c["signature"] for c in proof.get("crashes", [])]})
        ok = (res.get("rc") == 0 or (done_file and os.path.exists(done_file))) and not any(
            c["signature"] in ("device_lost", "no_vulkan_device", "root", "assert", "no_display", "bindless_rt")
            for c in proof.get("crashes", []))
        if need_render:
            ok = ok and proof.get("verdict") in ("PASS", "WARN")
        if ok:
            break
        nxt = uerun.next_attempt(proof, tried)
        if not nxt:
            break
        lbl, change = nxt
        lab.log("%s failed (%s); retrying with: %s" % (label, ", ".join(c["signature"] for c in proof["crashes"]) or proof.get("why"), lbl))
        tried.append(lbl)
        apply_change(lab, change)
    if not ok and tried and lab.settings != before:
        # Every retry failed: put the settings back, so one failing job (11 Nov: a USD import with no USD plugin)
        # doesn't leave every later import and render on Shader Model 5 without ray tracing.
        restore_settings(lab, before)
        lab.log("%s: none of the retries helped (%s); settings restored" % (label, ", ".join(tried)))
    last = dict(attempts[-1])
    last["attempts"] = [{x: a.get(x) for x in ("rc", "seconds", "killed", "change", "attempt")} for a in attempts]
    last["excerpt"] = uerun.excerpt(Path(last["log"]).read_text(errors="replace")) if Path(last["log"]).exists() else ""
    return last


def crash_stack(lab: Lab, max_chars=2500) -> str:
    """The call stack of Unreal's newest crash report under the project's Saved/Crashes, or ''."""
    dirs = sorted(glob.glob(str(lab.project_dir / "Saved" / "Crashes" / "*")), key=os.path.getmtime)
    if not dirs:
        return ""
    ctx = Path(dirs[-1]) / "CrashContext.runtime-xml"
    if not ctx.exists():
        return ""
    m = re.search(r"<CallStack>(.*?)</CallStack>", ctx.read_text(errors="replace"), re.S)
    txt = (m.group(1) if m else "").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
    return txt[:max_chars]


def smoke_cmd(lab: Lab):
    b = uerun.engine_bins(lab.engine_dir)
    return [str(b["editor"]), str(lab.proj()), "/Engine/Maps/Entry", "-game", "-RenderOffscreen", "-vulkan", "-windowed",
            "-ResX=640", "-ResY=360", "-log", "-ExecCmds=quit", "-sm6" if lab.settings["sm6"] else "-sm5"] + uerun.COMMON


def smoke(lab: Lab, label="smoke") -> dict:
    """Start Unreal on Vulkan with no window and quit: proves the RHI and pays the one-time global shader compile."""
    lab.own()
    return run_unreal(lab, smoke_cmd, label, lab.out / "logs", timeout=4 * 3600)


# ---------------------------------------------------------------------- import and render
def import_scene(lab: Lab, name, source, kind, manifest, shape_centres=None, timeout=3600) -> dict:
    d = lab.out / "scenes" / name
    d.mkdir(parents=True, exist_ok=True)
    rep_path = d / ("import_report%s.json" % ("_usd" if kind == "usd" else ""))
    job = project.import_job(name if kind == "gltf" else name + "_usd", source, kind, manifest, rep_path, HERE.parent,
                             mapping=lab.mapping, shape_centres=shape_centres)
    jp = uerun.write_json(d / ("import_job%s.json" % ("_usd" if kind == "usd" else "")), job)
    # A rerun of step 8 found the map and the sequence from the first run, could not create them again, and reported
    # 0 sequences. Unreal is not running between jobs, so removing the scene's own folder is safe; Shared stays.
    shutil.rmtree(lab.project_dir / "Content" / "X3DLab" / job["scene"], ignore_errors=True)
    lab.own()
    script = lab.project_dir / "Content" / "Python" / "x3dlab_import.py"
    shutil.copy(project.UE_SCRIPTS / "x3dlab_import.py", script)
    if rep_path.exists():
        rep_path.unlink()
    res = run_unreal(lab, lambda L: uerun.commandlet_cmd(L.engine_dir, L.proj(), script, jp), "import_" + job["scene"],
                     lab.out / "logs", timeout=timeout, need_render=False)
    res["report"] = json.loads(rep_path.read_text()) if rep_path.exists() else {"ok": False, "errors": ["no report written"]}
    res["job"] = job
    return res


def render(lab: Lab, name, camera_names, rung_name, rung, look="cinematic", key=False, res_override=None, timeout=4 * 3600,
           mode="mrq") -> dict:
    d = lab.out / "stills" / name
    d.mkdir(parents=True, exist_ok=True)
    tag = "%s_%s%s" % (rung_name, look, "_key" if key else "")
    rep = lab.out / "scenes" / name / ("render_report_%s.json" % tag)
    spec = project.render_jobs(name, camera_names, rung_name, rung, d, rep, look=look, key=key, override_res=res_override, mode=mode)
    jp = uerun.write_json(lab.out / "scenes" / name / ("render_job_%s.json" % tag), spec)
    lab.own()
    map_path = spec["jobs"][0]["map"] if spec["jobs"] else "/Engine/Maps/Entry"
    res_px = spec["jobs"][0]["resolution"] if spec["jobs"] else (1280, 720)
    out = run_unreal(lab, lambda L: uerun.render_cmd(L.engine_dir, L.proj(), map_path, jp, sm6=L.settings["sm6"],
                                                     resolution=(min(res_px[0], 1920), min(res_px[1], 1080))),
                     "render_%s_%s" % (project.ue_name(name), tag), lab.out / "logs", timeout=timeout, done_file=str(rep) + ".done")
    report = json.loads(rep.read_text()) if rep.exists() else {"jobs": [], "error": "no render report"}
    stills = []
    for j in spec["jobs"]:
        files = sorted(glob.glob(str(d / (j["name"] + ".*"))))
        r = next((x for x in report.get("jobs", []) if x.get("name") == j["name"]), {})
        stills.append({"job": j["name"], "viewpoint": j["viewpoint"], "files": files, "seconds": r.get("seconds"),
                       "png": next((f for f in files if f.endswith(".png")), None), "exr": next((f for f in files if f.endswith(".exr")), None),
                       "resolution": j["resolution"], "renderer": j["renderer"], "samples": [j["spatial"], j["temporal"]]})
    out.update(report=report, stills=stills, spec=spec)
    return out


# ---------------------------------------------------------------------- calibration
def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def calibration(png_path, k_assumed, albedo=0.8, intensity=1.0) -> dict:
    """The calibration still (match look, no tone curve): the box face's centre pixel tells how bright Unreal drew
    X3D intensity 1. Returns the measured K to use from here on (kept only if within 20x of the assumed one)."""
    from PIL import Image
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    cx, cy, r = w // 2, h // 2, max(2, min(w, h) // 40)
    px = [im.getpixel((x, y)) for x in range(cx - r, cx + r) for y in range(cy - r, cy + r)]
    lin = sum(srgb_to_linear(sum(p) / 3.0) for p in px) / len(px)
    expected = albedo * intensity
    ratio = lin / expected if expected else float("nan")
    k = k_assumed / ratio if ratio > 0 else None
    sane = k is not None and math.isfinite(k) and (k_assumed / 20 <= k <= k_assumed * 20)
    return {"centre_linear": round(lin, 4), "expected_linear": expected, "ratio": round(ratio, 4) if math.isfinite(ratio) else None,
            "k_assumed": k_assumed, "k_measured": round(k, 5) if sane else None,
            "saturated": lin > 0.98, "use": sane and lin <= 0.98}
