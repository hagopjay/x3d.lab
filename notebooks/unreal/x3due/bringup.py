"""Steps 3-6 of notebook 07 in one call, for notebooks that only need a working Unreal (08 Gobbler):

    UP = bringup.up(work, drive_dir, engine_version="5.8")
    LAB, PLAN, ENGINE = UP["lab"], UP["plan"], UP["engine"]

GPU tier, Vulkan as user ue, the engine from Drive, gentle priorities, the shader cache from Drive, the project and a
cold start (the one-time global shader compile; saved back to Drive when it was long). Every step is printed; nothing
here raises on a failed start: up()["ok"] says whether Unreal draws on the GPU, and up()["why"] what to do if not.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import engine as E
from . import live as L
from . import pipeline, tier, uerun, vulkan


def gentle(plan: dict, cpus: int, log=print) -> None:
    """Low CPU and disk priority for this process and what it starts, and 4 cores left free: a Colab VM with every
    core busy can be recycled (seen 11 Nov 2026 during a shader compile)."""
    try:
        os.nice(max(0, 10 - os.nice(0)))
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], capture_output=True)
    except Exception:
        pass
    if cpus >= 8:
        plan["shader_workers"] = min(plan.get("shader_workers") or cpus, cpus - 4)
    log("gentle: nice %d · %s shader workers" % (os.nice(0), plan.get("shader_workers")))


def up(work, drive_dir, engine_source="auto", engine_version="5.8", verbose=True, visual=True, every=20, gentle_mode=True,
       cache=True, fast_cache=True, hwrt=None, ghcr=(None, None, None), log=print, detect=None, enable=None, probe=None, runner=None,
       substrate="auto", megalights="auto", async_compute=False) -> dict:
    """hwrt: None follows the GPU (hardware ray tracing where it has RT cores); False forces software Lumen everywhere,
    which keeps one shader cache for every GPU and avoided the L4's ray-tracing start-up crash and device-lost renders
    (11 Nov 2026). substrate / megalights: "auto" | "on" | "off" (project.features_for: auto = Substrate from 5.7,
    MegaLights from 5.8 where hardware ray tracing is on). async_compute: False writes r.Vulkan.AllowAsyncCompute=0.
    detect / enable / probe / runner: stand-ins for tests (default: tier.detect, vulkan.enable, vulkan.probe, uerun.run)."""
    t0 = time.time()
    out = {"ok": False, "why": None, "steps": {}}
    hw = (detect or tier.detect)()
    g = hw.get("gpu")
    log("GPU      : %s" % (("%s · %.0f GB · driver %s" % (g["name"], hw["vram_gb"], hw["driver"])) if g else "none"))
    log("machine  : %d vCPUs · %.0f GB RAM · %.0f GB free disk" % (hw["cpus"], hw.get("ram_gb", 0), hw["disk_free_gb"]))
    out["hw"] = hw
    if not g:
        out["why"] = "no NVIDIA GPU: pick a GPU runtime (G4, A100, L4)"
        return out
    vk_en = (enable or vulkan.enable)()
    out["steps"]["vulkan_enable"] = {k: vk_en.get(k) for k in ("driver", "skipped", "error")}
    uerun.ensure_user("ue", []) if runner is None else None
    vk = (probe or vulkan.probe)(user="ue")
    nv = vk.get("nvidia_device")
    log("Vulkan   : %s" % (("%s · API %s" % (nv.get("deviceName"), nv.get("apiVersion"))) if nv else "no NVIDIA device for user ue"))
    plan = tier.plan(hw, vk)
    log("ray tracing: %s" % plan["ray_tracing"]["why"])
    out.update(vk=vk, plan=plan)
    if not nv or nv.get("software"):
        out["why"] = "user ue does not see the NVIDIA GPU through Vulkan: run notebook 07's step 4b"
        return out
    lab = pipeline.Lab(work=work, hw=hw, plan=plan, log=log, runner=runner)
    lab.live = L.Live(verbose=verbose, visual=visual, every=every, disk_path=str(work)) if (verbose or visual) else None
    out["lab"] = lab
    if gentle_mode:
        gentle(plan, hw["cpus"], log)
    if not E.tool("zstd") and runner is None:
        subprocess.run("apt-get install -y -qq zstd || (apt-get update -qq && apt-get install -y -qq zstd)", shell=True,
                       capture_output=True)
    if drive_dir and not pipeline.drive_mounted(drive_dir):
        out["why"] = ("Google Drive is not mounted: run `from google.colab import drive; drive.mount('/content/drive', "
                      "force_remount=True)`, sign in with the account that owns the engine files, then run this cell again")
        return out
    try:
        eng = pipeline.get_engine(lab, engine_source, drive_dir, ghcr[0], ghcr[1], ghcr[2], want=engine_version)
    except Exception as exc:
        out["why"] = "no engine: %s" % exc
        return out
    out["engine"] = eng
    log("engine   : Unreal %s from %s in %.0f s" % (eng.get("version"), eng["source"], eng["seconds"]))
    lab.settings["engine_version"] = eng.get("version")
    lab.settings["features"] = {"substrate": substrate, "megalights": megalights, "async_compute": bool(async_compute)}
    lab.settings["sm6"] = E.default_sm6(eng.get("version") or "")
    if not lab.settings["sm6"]:
        lab.settings["ray_tracing"] = False
        E.disable_vulkan_rt(lab.engine_dir)
    elif plan["ray_tracing"]["hw_rt"] and hwrt is not False:
        lab.settings["ray_tracing"] = True
        E.enable_vulkan_rt(lab.engine_dir, lab.settings["bindless"])
    else:
        lab.settings["ray_tracing"] = False
        E.disable_vulkan_rt(lab.engine_dir)
        log("ray tracing: software Lumen%s" % (" (HWRT off by choice)" if plan["ray_tracing"]["hw_rt"] else ""))
    fast = cache and fast_cache and Path(drive_dir).exists() and not eng.get("parts")
    if cache and eng["source"] != "drive cache" and Path(drive_dir).exists() and not fast:
        # the single archive only when no parts will be written: with 5.8 (a 39 GB zip, ~70 GB unpacked) both caches
        # would need another ~20 GB of Drive, and the parts are what the next runtime restores
        out["steps"]["engine_cache"] = pipeline.cache_engine(lab, drive_dir, eng["version"])
    if cache and Path(drive_dir).exists():
        out["steps"]["shader_cache_restore"] = pipeline.ddc_restore(lab, drive_dir, eng.get("version"))
        log("shaders  : %s" % out["steps"]["shader_cache_restore"])
    proj = lab.write_project()
    out["project"] = proj
    f = proj.get("features") or {}
    out["features"] = f
    log("features : Substrate %s (%s) · MegaLights %s (%s) · async compute %s"
        % ("on" if f.get("substrate") else "off", (f.get("why") or {}).get("substrate"),
           "on" if f.get("megalights") else "off", (f.get("why") or {}).get("megalights"),
           "off" if str((proj.get("system_cvars") or {}).get("r.Vulkan.AllowAsyncCompute", 1)) == "0" else "on"))
    if proj.get("mcp_plugins"):
        out["mcp_plugins"] = proj["mcp_plugins"]
        log("MCP      : the engine ships %s (unused by batch runs; its editor server listens on 127.0.0.1 only)"
            % ", ".join(proj["mcp_plugins"]))
    s = pipeline.smoke(lab, "smoke_cold")
    out["smoke"] = s
    p = s["proof"]
    log("start    : rc %s in %.0f s · %s · %s" % (s["rc"], s["seconds"], p.get("verdict"), p.get("why")))
    if p.get("verdict") not in ("PASS", "WARN"):
        out["why"] = "Unreal did not start on the GPU (%s): run notebook 07's step 6c probe" % p.get("why")
        out["crash_stack"] = pipeline.crash_stack(lab)
        return out
    if cache and Path(drive_dir).exists() and s["seconds"] > 120:
        out["steps"]["shader_cache_save"] = pipeline.ddc_save(lab, drive_dir, eng.get("version"))
        log("shaders  : saved to Drive %s" % out["steps"]["shader_cache_save"])
    if fast:
        log("engine   : one-time split of the engine cache into %d parts on Drive (the next runtime restores it in parallel)…" % E.PARTS)
        out["steps"]["engine_parts"] = pipeline.cache_engine_parts(lab, drive_dir, eng["version"])
        log("engine   : %s" % out["steps"]["engine_parts"])
    out["ok"] = True
    out["seconds"] = round(time.time() - t0, 1)
    return out
