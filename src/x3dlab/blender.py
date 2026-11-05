"""Blender for the Lab: install it, probe the GPU, and run worker jobs with live events.

    bl = blender.setup()                      # bpy wheel when Python is 3.13, else the Blender 5.2 LTS tarball
    bl.probe()                                # {'blender': '5.2.3 LTS', 'best': 'OPTIX', 'backends': {...}}
    results = bl.run([{"kind": "render", "src": "a.x3d", "out_png": "a.png", "backend": "OPTIX"}])

Each task's Blender output (warnings, importer errors, tracebacks) is attached to the task's result as `log`,
so a failed import says *why* (the old notebook only said "import/export error: ?").
Renders share the GPU through a semaphore (`gpu_slots`); round trips are CPU work and run in parallel.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from . import install, pins, runtime
from .events import bus

WORKER = Path(__file__).with_name("blender_worker.py")
BLENDER_TARBALL = "https://download.blender.org/release/Blender{major}/blender-{ver}-linux-x64.tar.xz"
APT_LIBS = "libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libxkbcommon0 libsm6 libegl1"


class BlenderError(RuntimeError):
    pass


def reason(text: str | None) -> str:
    """The one line that says why: the last 'SomethingError: message' line of a traceback."""
    if not text:
        return ""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip() and not ln.strip().startswith(("File ", "Location:", "^", "~"))]
    hits = [ln for ln in lines if re.match(r"^[\w.]*(Error|Exception)\b.*:", ln)]
    return (hits[-1] if hits else lines[-1] if lines else "")[:300]


class GPUSampler:
    """nvidia-smi once a second while something runs on the GPU; emits 'metric' events and keeps peaks."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread = None
        self.exe = shutil.which("nvidia-smi")

    def start(self):
        if not self.exe or self._thread:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gpu-sampler")
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run([self.exe, "--query-gpu=utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
                u, used, total, power, temp = [x.strip() for x in out.splitlines()[0].split(",")[:5]]
                s = {"t": time.time(), "util": float(u), "mem_mb": float(used), "mem_total_mb": float(total),
                     "power_w": float(power) if power not in ("[N/A]", "") else None, "temp_c": float(temp)}
                self.samples.append(s)
                bus.emit("gpu %s%% · %.0f MB" % (u, float(used)), level="DEBUG", src="gpu", kind="metric", data=s)
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def peak(self, t0: float, t1: float) -> dict:
        win = [s for s in self.samples if t0 - 1 <= s["t"] <= t1 + 1]
        if not win:
            return {}
        return {"peak_mem_mb": max(s["mem_mb"] for s in win), "peak_util": max(s["util"] for s in win),
                "mean_util": round(sum(s["util"] for s in win) / len(win), 1)}


class Blender:
    def __init__(self, cmd: list[str], addon_src: Path, version: str = "", gpu_slots: int = 1):
        self.cmd = cmd                   # how to start a worker: [python] or [blender, -b, --factory-startup, --python]
        self.addon_src = Path(addon_src)
        self.version = version
        self.info: dict = {}
        self.gpu = threading.Semaphore(max(1, gpu_slots))
        self.sampler = GPUSampler()

    # ------------------------------------------------------------------ running jobs
    def _argv(self, job_path: Path) -> list[str]:
        if len(self.cmd) == 1:                                   # the bpy wheel: a plain Python
            return [self.cmd[0], str(WORKER), str(job_path)]
        return self.cmd + [str(WORKER), "--", str(job_path)]

    def run(self, tasks: list[dict], timeout: float = 1800, job: str | None = None, label: str = "blender") -> list[dict]:
        """Run tasks in one Blender process. Returns one result per task: {id, kind, ok, seconds, result, log}."""
        tasks = [dict(t, id=t.get("id") or uuid.uuid4().hex[:8]) for t in tasks]
        work = runtime.sub("blender", "jobs")
        job_path = work / ("%s.json" % uuid.uuid4().hex[:10])
        job_path.write_text(json.dumps({"addon_src": str(self.addon_src), "tasks": tasks}))
        by_id = {t["id"]: {"id": t["id"], "kind": t["kind"], "ok": False, "log": [], "result": None} for t in tasks}
        current = {"id": None, "t0": time.time()}
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(self._argv(job_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                start_new_session=True, bufsize=1)
        killer = threading.Timer(timeout, lambda: proc.kill())
        killer.start()
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                cut = line.find("EVT{")
                if cut > 0:                                    # an event glued to the end of Blender's own output
                    tail, line = line[:cut], line[cut:]
                    if current["id"] and tail.strip():
                        by_id[current["id"]]["log"].append(tail[-400:])
                if line.startswith("EVT{"):
                    try:
                        ev = json.loads(line[3:])
                    except ValueError:
                        continue
                    self._on_event(ev, by_id, current, job, label)
                elif line.strip():
                    tid = current["id"]
                    low = line.lower()
                    noise = "syntaxwarning" in low or "deprecationwarning" in low or "cuew" in low or "hipew" in low
                    level = "WARN" if (not noise and ("error" in low or "warning" in low or "traceback" in low)) else "DEBUG"
                    if tid and level == "WARN":
                        by_id[tid]["log"].append(line[:400])
                    bus.emit(line[:400], level=level, src="blender", kind="proclog", job=job)
            proc.wait(timeout=30)
        finally:
            killer.cancel()
            try:
                job_path.unlink()
            except OSError:
                pass
        if proc.returncode not in (0, None):
            for r in by_id.values():
                if r["result"] is None:
                    r["result"] = {"error": "Blender exited with code %s before this task finished" % proc.returncode}
        return [by_id[t["id"]] for t in tasks]

    def _on_event(self, ev: dict, by_id: dict, current: dict, job, label):
        kind = ev.get("ev")
        if kind == "start":
            current.update(id=ev["id"], t0=ev["t"])
        elif kind == "end":
            r = by_id.get(ev["id"])
            if r is not None:
                r.update(ok=ev.get("ok", False), seconds=ev.get("seconds"), result=ev.get("result"))
                res = r["result"] or {}
                status = "ok" if r["ok"] else "error"
                data = {"task": ev["id"], "kind": ev.get("kind"), "status": status}
                if not r["ok"]:
                    err = res.get("error") or next((g.get("error") for g in res.get("generations", []) if g.get("error")), "")
                    res["reason"] = reason(err) or reason("\n".join(r["log"]))
                    data["error"] = res["reason"]
                    data["log"] = r["log"][-5:]
                for k in ("backend", "seconds", "samples", "resolution", "camera", "pixels"):
                    if k in res:
                        data[k] = res[k]
                bus.emit("%s %s %s (%.1f s)" % (label, ev.get("kind"), status, ev.get("seconds") or 0),
                         level="INFO" if r["ok"] else "WARN", src="blender", kind="span", job=job, data=data,
                         span={"name": "%s %s" % (label, ev.get("kind")), "track": "Blender", "start": current["t0"], "end": ev["t"]})
            current["id"] = None
        elif kind == "progress":
            bus.emit("round trip gen %s: import %.2fs export %.2fs" % (ev.get("gen"), ev.get("import_s", 0), ev.get("export_s", 0)),
                     level="DEBUG", src="blender", kind="progress", job=job, data=ev)
        elif kind == "fatal":
            raise BlenderError(ev.get("error"))

    # ------------------------------------------------------------------ conveniences
    def probe(self) -> dict:
        r = self.run([{"kind": "probe"}], timeout=300, label="probe")[0]
        if not r["ok"]:
            raise BlenderError("probe failed: %s\n%s" % ((r["result"] or {}).get("error"), "\n".join(r["log"])))
        self.info = r["result"]
        self.version = self.info.get("blender", self.version)
        bus.info("Blender %s · best Cycles backend: %s" % (self.version, self.info.get("best")), src="blender", data=self.info)
        return self.info

    @property
    def best_backend(self) -> str:
        return self.info.get("best", "CPU") if self.info else "CPU"

    def render(self, src: str, out_png: str, backend: str | None = None, samples: int = 64, resolution=(960, 540),
               denoise: bool = True, camera: str = "viewpoint", job: str | None = None, film_transparent: bool = False) -> dict:
        backend = backend or self.best_backend
        task = {"kind": "render", "src": str(src), "out_png": str(out_png), "backend": backend, "samples": samples,
                "resolution": list(resolution), "denoise": denoise, "camera": camera, "film_transparent": film_transparent}
        if backend != "CPU":
            with self.gpu:
                self.sampler.start()
                t0 = time.time()
                r = self.run([task], job=job, label="render")[0]
                if r["result"]:
                    r["result"]["gpu"] = self.sampler.peak(t0, time.time())
        else:
            r = self.run([task], job=job, label="render")[0]
        return r

    def import_(self, src: str, job: str | None = None) -> dict:
        return self.run([{"kind": "import", "src": str(src)}], job=job, label="import")[0]

    def roundtrip(self, src: str, version: str = "X3D40", generations: int = 1, out_prefix: str | None = None,
                  job: str | None = None) -> dict:
        return self.run([{"kind": "roundtrip", "src": str(src), "version": version, "generations": generations,
                          "out_prefix": out_prefix}], job=job, label="roundtrip")[0]


# ---------------------------------------------------------------------- setup
def _bpy_python() -> str | None:
    """sys.executable if `import bpy` works there (installing the wheel if needed and possible)."""
    if sys.version_info[:2] != (3, 13):
        return None
    spec = pins.get("python", "bpy")
    ok = subprocess.run([sys.executable, "-c", "import bpy"], capture_output=True).returncode == 0
    if not ok:
        with bus.span("pip install %s (about 400 MB)" % spec, src="install"):
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", spec], capture_output=True, text=True)
        if r.returncode:
            bus.warn("bpy wheel install failed: %s" % r.stderr[-300:], src="install")
            return None
        ok = subprocess.run([sys.executable, "-c", "import bpy"], capture_output=True).returncode == 0
    return sys.executable if ok else None


def _blender_tarball() -> str:
    ver = pins.get("python", "bpy").split("==")[-1]
    major = ".".join(ver.split(".")[:2])
    root = runtime.sub("blender") / ("blender-%s-linux-x64" % ver)
    exe = root / "blender"
    if not exe.exists():
        url = BLENDER_TARBALL.format(major=major, ver=ver)
        tar = runtime.sub("blender") / "blender.tar.xz"
        with bus.span("download Blender %s (about 350 MB)" % ver, src="install"):
            urllib.request.urlretrieve(url, tar)
        with bus.span("unpack Blender", src="install"):
            with tarfile.open(tar) as tf:
                tf.extractall(runtime.sub("blender"), filter="data")
        tar.unlink()
    return str(exe)


def addon_source() -> Path:
    repo, ref = pins.get("x3d_blender_io", "repo"), pins.get("x3d_blender_io", "ref")
    if os.environ.get("X3DLAB_BLENDER_IO_DIR"):
        return Path(os.environ["X3DLAB_BLENDER_IO_DIR"]) / "source"
    dest = runtime.sub("repos") / "x3d-blender-io"
    install.clone(repo, ref, dest, install.github_token())
    return dest / "source"


def setup(prefer: str = "auto", gpu_slots: int | None = None) -> Blender:
    """prefer: 'auto' (wheel if possible), 'wheel' or 'tarball'."""
    if shutil.which("apt-get") and runtime.in_colab():
        subprocess.run("apt-get -qq install -y %s > /dev/null 2>&1" % APT_LIBS, shell=True)
    addon = addon_source()
    cmd = None
    if prefer in ("auto", "wheel"):
        py = _bpy_python()
        if py:
            cmd = [py]
        elif prefer == "wheel":
            raise BlenderError("the bpy wheel needs Python 3.13 (this is %s)" % sys.version.split()[0])
    if cmd is None:
        cmd = [_blender_tarball(), "-b", "--factory-startup", "--python"]
    if gpu_slots is None:
        kind = runtime.detect().gpu_kind
        gpu_slots = {"G4": 3, "H100": 2, "A100": 2}.get(kind, 1)
    bl = Blender(cmd, addon, gpu_slots=gpu_slots)
    bl.probe()
    return bl
