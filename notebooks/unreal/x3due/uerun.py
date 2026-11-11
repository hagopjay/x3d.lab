"""Run Unreal headless, correctly, and prove from its log what it rendered with.

    uerun.ensure_user("ue", [engine_dir, project_dir, out_dir])   # Unreal refuses root ("Refusing to run with the root privileges")
    cmd = uerun.render_cmd(engine, project, map_path, job_json, sm6=True)
    res = uerun.run(cmd, log_path, user="ue", env=uerun.env(...), timeout=7200)  # RAM watchdog + VRAM sampler
    proof = uerun.rhi_proof(open(log_path).read())   # Vulkan on the NVIDIA device? NullRHI? llvmpipe? a crash?

Never stops a process it did not start (Colab's own services listen on this machine, e.g. its web app on 8080).
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------- the log
RX = {
    "rhi_vulkan": re.compile(r"(Log(?:RHI|Init|VulkanRHI|Vulkan)).*?(?:Using (?:Default )?RHI:?\s*Vulkan|RHI\s*(?:is|=|:)\s*Vulkan|"
                             r"Vulkan RHI|VulkanRHI|Vulkan Device)", re.I),
    "null_rhi": re.compile(r"NullRHI|NullDrv|Using\s+(?:Default\s+)?RHI:?\s*Null|-nullrhi", re.I),
    "device_name": re.compile(r"LogVulkanRHI:.*?(?:DeviceName|Device\s+Name|Using Device \d+)\s*[:=]?\s*(.+)$"),
    "vendor": re.compile(r"VendorId\s*=\s*(0x[0-9a-fA-F]+)"),
    "api": re.compile(r"LogVulkanRHI:.*?API\s*=\s*([\d.]+)"),
    "driver": re.compile(r"LogRHI:.*?Driver Version:\s*([\d.]+)"),
    "shader_platform": re.compile(r"(VULKAN_SM6|VULKAN_SM5|SF_VULKAN_SM6|SF_VULKAN_SM5|PCD3D_SM6|PCD3D_SM5)"),
    "software": re.compile(r"llvmpipe|lavapipe|SwiftShader|softpipe|VendorId\s*=\s*0x10005", re.I),
    "ray_tracing_on": re.compile(r"(?:Ray ?Tracing (?:is )?(?:enabled|supported)|RayTracing.*enabled|"
                                 r"GRHISupportsRayTracing\s*=\s*1|Hardware ray tracing (?:is )?(?:enabled|supported))", re.I),
    "ray_tracing_off": re.compile(r"(?:Ray ?Tracing (?:is )?(?:disabled|not supported)|Hardware ray tracing (?:is )?"
                                  r"(?:disabled|not supported))", re.I),
    "engine_version": re.compile(r"LogInit:.*?Engine Version:\s*(\S+)"),
}
CRASH = [  # (signature, regex, advice)
    ("root", re.compile(r"Refusing to run with the root privileges", re.I), "run Unreal as a normal user (uerun.ensure_user)"),
    ("no_vulkan_device", re.compile(r"Cannot find a compatible Vulkan|Failed to (?:load|create) Vulkan|No Vulkan|"
                                    r"vkCreateInstance failed|VK_ERROR_INCOMPATIBLE_DRIVER|None of the devices meet", re.I),
     "Vulkan cannot see the NVIDIA GPU as this user: rerun step 2 (vulkan.enable) and check vulkaninfo as user ue"),
    ("device_lost", re.compile(r"VK_ERROR_DEVICE_LOST|TerminateOnGPUCrash|GPU crash", re.I),
     "GPU hang/crash (seen on UE 5.8.0 Linux); retry with r.Vulkan.AllowAsyncCompute=0, then without ray tracing, then -sm5"),
    ("bindless_rt", re.compile(r"ray tracing pipelines are only supported in bindless|SupportsBindless\(\)", re.I),
     "set BindlessSupport=AllShaderTypes for VULKAN_SM6 (DataDrivenPlatformInfo.ini) and retry"),
    ("oom_device", re.compile(r"VK_ERROR_OUT_OF_DEVICE_MEMORY|Out of video memory|Failed to allocate Device Memory", re.I),
     "out of VRAM: use a lower ladder rung or smaller pools"),
    ("oom_host", re.compile(r"Ran out of memory|Out of memory|\bOOM\b|std::bad_alloc", re.I), "out of RAM: fewer shader workers, High-RAM runtime"),
    ("no_display", re.compile(r"No available video device|SDL_Init failed|Could not initialize SDL|cannot open display", re.I),
     "SDL wants a display: retry with SDL_VIDEODRIVER=offscreen, then under Xvfb"),
    # Not the bare word "ShaderFormatVersion" (healthy shader-compile and DDC lines carry it; only the crash's stack
    # frame counts), and not Unreal's
    # "Unable to find shader format X from hinted modules, loading all potential format modules" (a lookup, not a
    # failure): both made clean A100 runs WARN and sent step 8 down the Shader Model 5 retry (11 Nov 2026).
    ("shader_format", re.compile(r"Unable to find shader format(?!.*from hinted modules)|No target platform (?:supports|for) shader format|"
                                 r"TargetPlatformManagerModule::ShaderFormatVersion", re.I),   # the UE 5.2 SM6 segfault's stack frame
     "this engine has no shader format for the targeted platform (seen: UE 5.2 asked for SF_VULKAN_SM6): retry with Shader Model 5 and no ray tracing"),
    ("assert", re.compile(r"Assertion failed:|appError called:|Fatal error!?|Unhandled Exception|Caught signal", re.I),
     "engine crash: see the lines below and the crash folder under Saved/Crashes"),
    ("python", re.compile(r"LogPython: Error:|Traceback \(most recent call last\)", re.I), "error in the lab's Unreal Python script"),
    ("missing_plugin", re.compile(r"(?:plugin|module) .*?(?:could not be found|failed to load|is missing)", re.I),
     "a plugin listed in X3DLab.uproject is missing from this engine build"),
]
KEEP = re.compile(r"^\[?.*?(LogRHI|LogVulkan|LogVulkanRHI|LogInit|LogLinux|LogCore|LogPython|LogMovieRenderPipeline|"
                  r"LogInterchange|LogShaderCompilers|X3DLAB|Error|Fatal|Assertion|Caught signal)", re.I)


def rhi_proof(text: str) -> dict:
    """What the log says Unreal rendered with. verdict: PASS (Vulkan on an NVIDIA device), FAIL (NullRHI, software
    Vulkan, crash), WARN (Vulkan but the device line is missing), SKIP (empty log)."""
    if not text.strip():
        return {"verdict": "SKIP", "why": "empty log"}
    lines = text.splitlines()
    find = lambda rx: next((m for m in (rx.search(l) for l in lines) if m), None)  # noqa: E731
    dev = find(RX["device_name"])
    ven = find(RX["vendor"])
    sp = find(RX["shader_platform"])
    d = {"vulkan": bool(find(RX["rhi_vulkan"])), "null_rhi": bool(find(RX["null_rhi"])),
         "device": dev.group(1).strip() if dev else None, "vendor_id": ven.group(1).lower() if ven else None,
         "api": (find(RX["api"]) or [None, None])[1] if find(RX["api"]) else None,
         "driver": find(RX["driver"]).group(1) if find(RX["driver"]) else None,
         "shader_platform": sp.group(1) if sp else None,
         "software": bool(find(RX["software"])),
         "ray_tracing": "on" if find(RX["ray_tracing_on"]) else "off" if find(RX["ray_tracing_off"]) else "unknown",
         "engine_version": find(RX["engine_version"]).group(1) if find(RX["engine_version"]) else None}
    crashes = []
    for name, rx, advice in CRASH:
        hit = next((l for l in lines if rx.search(l)), None)
        if hit:
            crashes.append({"signature": name, "line": hit.strip()[:300], "advice": advice})
    d["crashes"] = crashes
    nvidia = d["vendor_id"] == "0x10de" or (d["device"] and "nvidia" in d["device"].lower())
    if d["null_rhi"] and not d["vulkan"]:
        d["verdict"], d["why"] = "FAIL", "NullRHI: nothing was rendered on a GPU"
    elif d["software"]:
        d["verdict"], d["why"] = "FAIL", "software Vulkan (llvmpipe/lavapipe/SwiftShader), not the NVIDIA GPU"
    elif d["vulkan"] and nvidia:
        d["verdict"], d["why"] = "PASS", "Vulkan RHI on %s" % d["device"]
    elif d["vulkan"]:
        d["verdict"], d["why"] = "WARN", "Vulkan RHI, but no NVIDIA device line was found in the log"
    else:
        d["verdict"], d["why"] = "FAIL", "no Vulkan RHI line in the log"
    if crashes and d["verdict"] == "PASS" and any(c["signature"] not in ("python",) for c in crashes):
        d["verdict"], d["why"] = "WARN", d["why"] + "; but the log shows " + crashes[0]["signature"]
    return d


def excerpt(text: str, n: int = 60) -> str:
    """The lines worth reading when something failed: RHI/Vulkan/Init/errors, plus the last lines."""
    lines = text.splitlines()
    keep = [l for l in lines if KEEP.search(l)][-n:]
    tail = lines[-15:]
    seen, out = set(), []
    for l in keep + ["--- last lines ---"] + tail:
        if l not in seen:
            seen.add(l)
            out.append(l[:400])
    return "\n".join(out)


def first_render_seconds(text: str) -> dict:
    """Seconds from the first log timestamp to the first frame written, and the shader-compile log lines."""
    ts = re.compile(r"^\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):(\d{3})\]")

    def t(line):
        m = ts.match(line)
        if not m:
            return None
        y, mo, d, h, mi, s, ms = (int(x) for x in m.groups())
        return ((d * 24 + h) * 60 + mi) * 60 + s + ms / 1000.0

    lines = text.splitlines()
    t0 = next((t(l) for l in lines if t(l) is not None), None)
    first = next((t(l) for l in lines if t(l) is not None and re.search(r"X3DLAB: wrote|Wrote|Writing (?:image|frame|file)", l)), None)
    shader_lines = [l for l in lines if re.search(r"Compiling \d+ shaders|ShaderCompil|shaders? (?:left|remaining)", l, re.I)]
    return {"to_first_output_s": round(first - t0, 1) if (t0 is not None and first is not None) else None,
            "shader_log_lines": len(shader_lines), "shader_sample": shader_lines[-3:]}


# ---------------------------------------------------------------------- user, commands, environment
def ensure_user(name: str = "ue", paths=(), dry_run: bool = False) -> list:
    """A normal account for Unreal, owning the paths it writes to. Root only; a no-op (returns []) otherwise."""
    cmds = []
    if os.geteuid() != 0 and not dry_run:
        return cmds
    try:
        import pwd
        pwd.getpwnam(name)
        exists = True
    except KeyError:
        exists = False
    if not exists:
        cmds.append(["useradd", "-m", "-s", "/bin/bash", name])
    for grp in ("video", "render"):
        cmds.append(["usermod", "-aG", grp, name])
    for p in paths:
        cmds.append(["chown", "-R", "%s:%s" % (name, name), str(p)])
    if not dry_run:
        for c in cmds:
            subprocess.run(c, capture_output=True)
    return cmds


def engine_bins(engine: str | Path) -> dict:
    e = Path(engine)
    return {"editor": e / "Engine/Binaries/Linux/UnrealEditor", "cmd": e / "Engine/Binaries/Linux/UnrealEditor-Cmd"}


COMMON = ["-unattended", "-nosplash", "-nosound", "-nopause", "-stdout", "-FullStdOutLogOutput", "-utf8output"]


def commandlet_cmd(engine, project, script, args_json=None):
    """A one-shot editor task: -run=pythonscript (imports, saves assets and a level; no rendering, no game loop)."""
    b = engine_bins(engine)
    cmd = [str(b["cmd"]), str(project), "-run=pythonscript", "-script=%s" % script] + COMMON
    if args_json:
        cmd.append("-X3DLabJob=%s" % args_json)
    return cmd


def render_cmd(engine, project, map_path, job_json, sm6=True, resolution=(1920, 1080), extra=()):
    """-game with Movie Render Queue's Python host executor, on Vulkan, with no window (-RenderOffscreen)."""
    b = engine_bins(engine)
    cmd = [str(b["editor"]), str(project), map_path, "-game",
           "-MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor",
           "-ExecutorPythonClass=/Engine/PythonTypes.X3DLabExecutor",
           "-X3DLabJob=%s" % job_json, "-RenderOffscreen", "-vulkan", "-windowed",
           "-ResX=%d" % resolution[0], "-ResY=%d" % resolution[1], "-log", "-NoLoadingScreen"] + COMMON
    cmd.append("-sm6" if sm6 else "-sm5")
    return cmd + list(extra)


def env(home: str, ddc: str | None = None, icd: str | None = "/usr/share/vulkan/icd.d/nvidia_icd.json", extra=None) -> dict:
    """Environment for an Unreal process: its own HOME, the local DDC where the run caches it, Vulkan pinned to
    NVIDIA's ICD (so a software ICD can never be picked)."""
    e = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "NVIDIA_DRIVER_CAPABILITIES",
                                                     "NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "TZ")}
    e.update(HOME=home, USER=os.path.basename(home), LANG=e.get("LANG", "C.UTF-8"), XDG_CONFIG_HOME=os.path.join(home, ".config"))
    if ddc:
        e["UE-LocalDataCachePath"] = ddc            # Epic's DDC page: the local DDC (Zen from 5.4) lives here
        e["UE-SharedDataCachePath"] = "None"
        e["UE_SharedDataCachePath"] = "None"
    if icd and os.path.exists(icd):
        e["VK_ICD_FILENAMES"] = icd
        e["VK_DRIVER_FILES"] = icd
    e.update(extra or {})
    return e


# ---------------------------------------------------------------------- running
class GPUSampler:
    """nvidia-smi every `interval` s: total memory used, utilisation; peak and baseline per run."""

    def __init__(self, interval=0.5, smi=None):
        self.interval, self.samples, self._stop, self._t = interval, [], threading.Event(), None
        self.smi = smi or shutil.which("nvidia-smi")

    def __enter__(self):
        if self.smi:
            self.baseline = self._one()
            self._t = threading.Thread(target=self._loop, daemon=True)
            self._t.start()
        return self

    def _one(self):
        try:
            out = subprocess.run([self.smi, "--query-gpu=memory.used,memory.total,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
            u, tot, ut = (float(x) for x in out.splitlines()[0].split(",")[:3])
            return {"t": time.time(), "mem_mb": u, "total_mb": tot, "util": ut}
        except Exception:
            return None

    def _loop(self):
        while not self._stop.is_set():
            s = self._one()
            if s:
                self.samples.append(s)
            self._stop.wait(self.interval)

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(3)

    def series(self, max_points=240) -> list:
        """[(seconds since start, VRAM GB, GPU %)], at most `max_points`, for a chart."""
        if not self.samples:
            return []
        t0 = self.samples[0]["t"]
        step = max(1, len(self.samples) // max_points)
        return [(round(s["t"] - t0, 1), round(s["mem_mb"] / 1024, 2), s["util"]) for s in self.samples[::step]]

    def summary(self):
        if not self.samples:
            return {}
        base = (getattr(self, "baseline", None) or {}).get("mem_mb", 0)
        return {"peak_mem_mb": max(s["mem_mb"] for s in self.samples), "baseline_mem_mb": base,
                "peak_used_by_run_mb": max(s["mem_mb"] for s in self.samples) - base,
                "total_mb": self.samples[-1]["total_mb"], "peak_util": max(s["util"] for s in self.samples),
                "mean_util": round(sum(s["util"] for s in self.samples) / len(self.samples), 1), "n": len(self.samples)}


def _ram_used_fraction():
    try:
        d = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            d[k] = int(v.split()[0])
        return 1 - d["MemAvailable"] / d["MemTotal"]
    except Exception:
        return 0.0


def run(cmd, log_path, user=None, env_vars=None, timeout=7200, cwd=None, ram_limit=0.94, on_line=None, done_file=None,
        done_grace=60, on_tick=None, min_free_gb=2.0, disk_path=None) -> dict:
    """Start `cmd` (as `user` via runuser when root), stream its output to `log_path`, sample VRAM, kill it (and only
    its own process group) on timeout, when RAM use passes `ram_limit`, or when free disk at `disk_path` (default: the
    log's folder) drops under `min_free_gb`. `done_file`: once it exists, give the process `done_grace` seconds to exit
    by itself, then stop it (Unreal sometimes lingers after a render).

    Output is read on a background thread and handed to the caller's thread through a queue, so `on_line(text)` runs
    for every line and `on_tick(elapsed_s, latest_gpu_sample)` about once a second, also while Unreal prints nothing
    (a cold shader compile can be quiet for minutes). Both run on the calling thread (IPython display works there);
    their errors never stop the run."""
    full = list(cmd)
    if user and os.geteuid() == 0:
        envs = ["%s=%s" % kv for kv in (env_vars or {}).items()]
        full = ["runuser", "-u", user, "--", "env", "-i"] + envs + full
        proc_env = None
    else:
        proc_env = env_vars
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    disk_path = disk_path or str(Path(log_path).parent)
    t0 = time.time()
    reason = None
    with open(log_path, "w", encoding="utf-8", errors="replace") as log, GPUSampler() as gs:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                env=proc_env, cwd=cwd, start_new_session=True, bufsize=1)
        state = {"done_at": None}

        def watchdog():
            nonlocal reason
            while proc.poll() is None:
                el = time.time() - t0
                if el > timeout:
                    reason = "timeout after %d s" % timeout
                elif _ram_used_fraction() > ram_limit:
                    reason = "RAM above %d%%" % int(ram_limit * 100)
                elif min_free_gb and _free_gb(disk_path) < min_free_gb:
                    reason = "disk nearly full (%.1f GB free at %s)" % (_free_gb(disk_path), disk_path)
                elif done_file and os.path.exists(done_file):
                    state["done_at"] = state["done_at"] or time.time()
                    if time.time() - state["done_at"] > done_grace:
                        reason = "finished (report written) but did not exit within %d s" % done_grace
                if reason:
                    _stop_group(proc)
                    return
                time.sleep(1)

        th = threading.Thread(target=watchdog, daemon=True)
        th.start()
        q = queue.Queue()

        def reader():
            for line in proc.stdout:
                log.write(line)
                q.put(line)
            q.put(None)

        rd = threading.Thread(target=reader, daemon=True)
        rd.start()
        finished = False
        while not finished:
            try:
                item = q.get(timeout=1.0)
                batch = [item]
                while len(batch) < 2000:          # drain what is already there, then tick
                    try:
                        batch.append(q.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                batch = []
            for item in batch:
                if item is None:
                    finished = True
                    break
                if on_line:
                    try:
                        on_line(item.rstrip("\n"))
                    except Exception:
                        pass
            if on_tick:
                try:
                    on_tick(time.time() - t0, gs.samples[-1] if gs.samples else None)
                except Exception:
                    pass
        proc.wait()
        rd.join(2)
        th.join(2)
    return {"rc": proc.returncode, "seconds": round(time.time() - t0, 1), "killed": reason, "gpu": gs.summary(),
            "gpu_series": gs.series(), "log": str(log_path), "cmd": cmd}


def _free_gb(path) -> float:
    try:
        return shutil.disk_usage(path).free / 2**30
    except OSError:
        return float("inf")


def _stop_group(proc):
    """SIGTERM then SIGKILL to the process group this module started (start_new_session), nothing else."""
    try:
        pg = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
        try:
            os.killpg(pg, sig)
        except OSError:
            return
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


# ---------------------------------------------------------------------- retry ladder
RETRY = [  # (signature, label, change) -- applied in order, one new attempt each, only when the signature is seen
    ("shader_format", "Shader Model 5, no ray tracing", {"sm6": False, "ray_tracing": False}),
    ("device_lost", "async compute off", {"cvars": {"r.Vulkan.AllowAsyncCompute": 0}}),
    ("bindless_rt", "bindless for all shader types", {"bindless": "AllShaderTypes"}),
    ("device_lost", "no ray tracing", {"ray_tracing": False}),
    ("no_display", "SDL offscreen", {"env": {"SDL_VIDEODRIVER": "offscreen"}}),
    ("no_display", "Xvfb", {"xvfb": True}),
    ("device_lost", "Shader Model 5", {"sm6": False}),
]


def next_attempt(proof: dict, tried: list) -> tuple | None:
    """The next change to try for the crash signatures in `proof`, skipping labels already tried."""
    sigs = {c["signature"] for c in proof.get("crashes", [])}
    for sig, label, change in RETRY:
        if sig in sigs and label not in tried:
            return label, change
    return None


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    return path
