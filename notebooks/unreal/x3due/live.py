"""Live progress for long Unreal runs (the owner's standing rule: every notebook has VERBOSE and VISUAL switches).

VERBOSE (info): the log lines that matter, as they happen, rate-limited per kind, plus a heartbeat every `every` s even
when Unreal prints nothing: shader jobs done and their rate, shader workers running, Unreal's own memory, RAM, VRAM, GPU
use, free disk. It also warns when Unreal's memory keeps growing while nothing else moves.

VISUAL: every new image in the job's watch folders is shown the moment it exists, and each job ends with a small chart of
VRAM and GPU use over time.

Everything here runs on the notebook's main thread (uerun.run calls on_line/on_tick from its loop), so IPython display
works. Nothing here can stop a run: errors in printing are swallowed by the caller.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import time
from pathlib import Path

# (kind, pattern, minimum seconds between two printed lines of this kind, most lines of this kind per job)
KINDS = [
    ("error", re.compile(r"\bError:|\bFatal\b|Caught signal|Assertion failed|Unhandled Exception|VK_ERROR_|Segmentation fault"), 0, 40),
    ("rhi", re.compile(r"LogRHI:|LogVulkanRHI:|LogVulkan:.*(?:[Dd]evice|GPU|[Vv]endor|[Rr]ay ?[Tt]rac|API Version|Driver)"), 0, 25),
    ("milestone", re.compile(r"Engine is initialized|Game Engine Initialized|LogLoad: (?:Took|LoadMap)|LogInit: Build:|"
                              r"LogInit: Engine Version|LogExit: Exiting|Shader Model|SF_VULKAN"), 0, 30),
    ("python", re.compile(r"LogPython|\bx3dlab\b|X3DLab:", re.I), 0, 300),
    ("render", re.compile(r"LogMovieRenderPipeline|MoviePipeline|HighResShot|Screenshot", re.I), 5, 200),
    ("import", re.compile(r"LogInterchange\w*:.*(?:Error|Warning)|LogGLTF|LogUsd\w*:", re.I), 2, 40),
    ("shader", re.compile(r"LogShaderCompilers|ShaderCompileWorker|[Ss]haders? left|finished batch of"), 30, 200),
    ("warning", re.compile(r"\bWarning:"), 10, 30),
]
SHADER_DONE = re.compile(r"finished batch of (\d+) jobs?", re.I)
SHADER_LEFT = re.compile(r"(?:shaders?|jobs?)\s+(?:left(?: to compile)?|remaining)\D{0,3}(\d+)|(\d+)\s+(?:shaders?|jobs?)\s+(?:left|remaining)", re.I)
IMAGES = ("*.png", "*.jpg", "*.jpeg")


def proc_stats() -> dict:
    """Unreal's processes from /proc: resident memory of UnrealEditor and how many ShaderCompileWorkers run."""
    page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    rss, workers = 0, 0
    for d in glob.glob("/proc/[0-9]*"):
        try:
            comm = open(d + "/comm").read().strip()
            if comm.startswith("ShaderCompileWo"):
                workers += 1
            elif comm.startswith("UnrealEditor"):
                rss += int(open(d + "/statm").read().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue
    return {"unreal_rss_gb": rss / 2**30, "workers": workers}


def ram() -> dict:
    try:
        d = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            d[k] = int(v.split()[0]) / 2**20
        return {"used_gb": d["MemTotal"] - d["MemAvailable"], "total_gb": d["MemTotal"]}
    except (OSError, KeyError, ValueError):
        return {}


def mmss(s: float) -> str:
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


class Live:
    def __init__(self, verbose=True, visual=True, every=20.0, out=print, show_image=None, stats=None, disk_path="/",
                 max_images=12):
        self.verbose, self.visual, self.every = verbose, visual, every
        self.out = out
        self.show_image = show_image or _show_image
        self.stats = stats or proc_stats
        self.disk_path = disk_path
        self.max_images = max_images
        self.shown = set()           # images already shown (step 9 skips these)
        self._reset("", [])

    def _reset(self, label, watch):
        self.label, self.watch = label, [Path(w) for w in watch]
        self.t0 = time.time()
        self.last_tick = 0.0
        self.count = {k: 0 for k, *_ in KINDS}
        self.last_print = {k: -1e9 for k, *_ in KINDS}
        self.jobs_done, self.jobs_left = 0, None
        self.jobs_hist = []          # (t, jobs_done) per heartbeat, for the rate
        self.rss_hist = []           # (t, rss_gb) per heartbeat, for the growth warning
        self.last_line = ""
        self.lines = 0
        self.images_this_job = 0
        self.pending = {}            # image -> size at the last look

    # ---------------------------------------------------------------- the job
    def begin(self, label, watch=()):
        self._reset(label, watch)
        if self.verbose:
            self.out("▶ %s" % label)

    def line(self, text: str):
        self.lines += 1
        m = SHADER_DONE.search(text)
        if m:
            self.jobs_done += int(m.group(1))
        m = SHADER_LEFT.search(text)
        if m:
            self.jobs_left = int(m.group(1) or m.group(2))
        if not self.verbose:
            return
        now = time.time()
        for kind, rx, gap, cap in KINDS:
            if rx.search(text):
                self.last_line = text.strip()
                if self.count[kind] < cap and now - self.last_print[kind] >= gap:
                    self.count[kind] += 1
                    self.last_print[kind] = now
                    self.out("  │ %s %s" % (mmss(now - self.t0), _short(text)))
                return

    def tick(self, elapsed: float, gpu: dict | None = None):
        """Called about once a second from uerun.run's loop; acts every `every` seconds."""
        if self.visual:
            self._new_images()
        if elapsed - self.last_tick < self.every:
            return
        self.last_tick = elapsed
        st = {}
        try:
            st = self.stats() or {}
        except Exception:
            pass
        now = time.time()
        self.jobs_hist.append((now, self.jobs_done))
        rss = st.get("unreal_rss_gb")
        if rss is not None:
            self.rss_hist.append((now, rss))
        if not self.verbose:
            return
        parts = ["⏱ %s" % mmss(elapsed)]
        if self.jobs_done or self.jobs_left is not None or st.get("workers"):
            rate = self.rate_per_min()
            s = "shaders: %s jobs done" % format(self.jobs_done, ",")
            if rate:
                s += " (%s/min)" % format(int(rate), ",")
            if self.jobs_left is not None:
                s += ", %s left" % format(self.jobs_left, ",")
                if rate:
                    s += ", ~%s to go" % mmss(self.jobs_left / rate * 60)
            parts.append(s)
            parts.append("%d workers" % st.get("workers", 0))
        if rss:
            parts.append("Unreal %.1f GB" % rss)
        r = ram()
        if r:
            parts.append("RAM %.1f/%.0f GB" % (r["used_gb"], r["total_gb"]))
        if gpu:
            parts.append("VRAM %.1f/%.0f GB · GPU %d%%" % (gpu.get("mem_mb", 0) / 1024, gpu.get("total_mb", 0) / 1024, gpu.get("util", 0)))
        try:
            parts.append("disk %.0f GB free" % (shutil.disk_usage(self.disk_path).free / 2**30))
        except OSError:
            pass
        self.out("  " + " · ".join(parts))
        warn = self.growth_warning()
        if warn:
            self.out("  ⚠️ " + warn)

    def rate_per_min(self) -> float:
        h = [x for x in self.jobs_hist if x[0] >= self.jobs_hist[-1][0] - 120] if self.jobs_hist else []
        if len(h) < 2 or h[-1][0] <= h[0][0]:
            return 0.0
        return (h[-1][1] - h[0][1]) / (h[-1][0] - h[0][0]) * 60

    def growth_warning(self, gb=2.0, window=180) -> str:
        """Unreal's memory grew by more than `gb` within `window` seconds while no shader job finished: worth knowing
        before the RAM guard has to stop it (a -RenderOffscreen leak was reported on Linux in 2024)."""
        if len(self.rss_hist) < 2:
            return ""
        t_end, r_end = self.rss_hist[-1]
        old = [x for x in self.rss_hist if x[0] >= t_end - window]
        t0, r0 = old[0]
        jobs = [j for t, j in self.jobs_hist if t >= t0]
        if r_end - r0 > gb and (not jobs or jobs[-1] == jobs[0]):
            return "Unreal's memory grew %.1f GB in %s with no shader progress (now %.1f GB)" % (r_end - r0, mmss(t_end - t0), r_end)
        return ""

    def end(self, res: dict):
        if self.visual:
            self._new_images()
        if self.verbose:
            g = res.get("gpu") or {}
            self.out("■ %s: rc %s in %s%s%s%s" % (
                self.label, res.get("rc"), mmss(res.get("seconds") or (time.time() - self.t0)),
                " · stopped: %s" % res["killed"] if res.get("killed") else "",
                " · peak VRAM %.1f GB, GPU up to %d%%" % (g.get("peak_mem_mb", 0) / 1024, g.get("peak_util", 0)) if g else "",
                " · %s shader jobs" % format(self.jobs_done, ",") if self.jobs_done else ""))
        if self.visual and len(res.get("gpu_series") or []) >= 4:
            try:
                gpu_chart(res["gpu_series"], self.label)
            except Exception:
                pass

    # ---------------------------------------------------------------- images
    def _new_images(self):
        for w in self.watch:
            for pat in IMAGES:
                for f in sorted(glob.glob(str(w / "**" / pat), recursive=True)):
                    if f in self.shown or self.images_this_job >= self.max_images:
                        continue
                    try:
                        st = os.stat(f)
                    except OSError:
                        continue
                    if st.st_mtime < self.t0 - 1 or st.st_size == 0:
                        continue      # older than this job, or empty so far
                    if self.pending.get(f) != st.st_size:
                        self.pending[f] = st.st_size
                        continue      # show it once its size held still between two looks (fully written)
                    self.shown.add(f)
                    self.images_this_job += 1
                    self.show_image(f, "%s · %s · %s" % (self.label, Path(f).name, mmss(st.st_mtime - self.t0)))


def _short(text: str, n=200) -> str:
    t = re.sub(r"^\[\d{4}\.\d\d\.\d\d-\d\d\.\d\d\.\d\d:\d+\]\[\s*\d+\]", "", text.strip())
    return t if len(t) <= n else t[:n - 1] + "…"


def _show_image(path, caption):
    try:
        from IPython.display import Image, display
        print("  🖼 " + caption)
        display(Image(filename=path, width=560))
    except Exception:
        print("  🖼 " + caption + " (no display here)")


def gpu_chart(series, title=""):
    """series: [(seconds, vram_gb, util_pct)]: VRAM and GPU use over one job, inline."""
    import matplotlib
    import matplotlib.pyplot as plt
    t = [s[0] for s in series]
    fig, ax = plt.subplots(figsize=(7, 1.9), dpi=100)
    ax.plot(t, [s[1] for s in series], color="#2a6fdb", lw=1.6, label="VRAM GB")
    ax.set_ylabel("VRAM GB", fontsize=8)
    ax.set_xlabel("seconds", fontsize=8)
    ax2 = ax.twinx()
    ax2.fill_between(t, [s[2] for s in series], color="#e8833a", alpha=0.25, lw=0)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("GPU %", fontsize=8)
    ax.set_title(title, fontsize=9, loc="left")
    for a in (ax, ax2):
        a.tick_params(labelsize=7)
        for s in ("top",):
            a.spines[s].set_visible(False)
    fig.tight_layout()
    if matplotlib.get_backend().lower().endswith("agg") and "inline" not in matplotlib.get_backend().lower():
        plt.close(fig)
        return fig
    plt.show()
    return fig
