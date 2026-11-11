"""A heartbeat on Drive, so a run can be watched from anywhere (and by Claude, through a shared Drive folder).

    hb = heartbeat.Heartbeat(drive_dir, out_dir, context=lambda: globals()).start()

Every `beat_s`: <drive_dir>/heartbeat/<run_id>/heartbeat.json (now) and one line in heartbeat.log (history), plus
<drive_dir>/heartbeat/latest.json naming the newest run. Every `mirror_s`: new or changed files under `out_dir` (logs,
stills, reports, the run card) copied to <run>/out/. It keeps going while the browser is disconnected, until the VM
goes away.

Data goes out only: nothing is ever read back from Drive and run (no command channel).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def _utc(fmt="%Y-%m-%dT%H:%M:%SZ"):
    return datetime.datetime.now(datetime.timezone.utc).strftime(fmt)


class Heartbeat:
    def __init__(self, drive_dir, out_dir, context=None, beat_s=60, mirror_s=300, mirror_max_mb=200, run_id=None):
        self.drive = Path(drive_dir)
        self.out = Path(out_dir)
        self.context = context or (lambda: {})
        self.beat_s, self.mirror_s, self.max_bytes = beat_s, mirror_s, mirror_max_mb * 2**20
        gpu = (_sh("nvidia-smi --query-gpu=name --format=csv,noheader") or "CPU").split("\n")[0].replace("NVIDIA ", "").replace(" ", "")
        self.run_id = run_id or "%s_%s" % (_utc("%Y-%m-%dT%H-%MZ"), gpu)
        self.dir = self.drive / "heartbeat" / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0, self.beats, self.mirrored, self.last_mirror = time.time(), 0, {}, 0.0
        self.stop_ev = threading.Event()
        self.th = threading.Thread(target=self._loop, daemon=True, name="drive-heartbeat")

    def status(self) -> dict:
        g = _sh("nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits").split(",")
        mem = {}
        try:
            for line in open("/proc/meminfo"):
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) / 2**20
        except OSError:
            pass
        ctx = self.context() or {}
        lv = getattr(ctx.get("LAB"), "live", None)
        smoke = ctx.get("SMOKE") or {}
        sc = ctx.get("SC")
        try:
            uptime = int(float(open("/proc/uptime").read().split()[0]))
            load = open("/proc/loadavg").read().split()[:3]
        except OSError:
            uptime, load = None, None
        return {
            "run_id": self.run_id, "utc": _utc(), "vm_uptime_s": uptime, "heartbeat_age_s": int(time.time() - self.t0),
            "beat": self.beats, "job": getattr(lv, "label", None),
            "job_elapsed_s": int(time.time() - lv.t0) if lv and getattr(lv, "t0", None) else None,
            "shader_jobs_done": getattr(lv, "jobs_done", None), "last_log_line": (getattr(lv, "last_line", "") or "")[:300],
            "ram_gb": [round(mem["MemTotal"] - mem["MemAvailable"], 1), round(mem["MemTotal"], 1)] if "MemAvailable" in mem else None,
            "vram_gb": [round(float(g[0]) / 1024, 2), round(float(g[1]) / 1024, 1)] if len(g) >= 3 else None,
            "gpu_util": float(g[2]) if len(g) >= 3 else None, "load_avg": load, "cpus": os.cpu_count(),
            "disk_free_gb": round(shutil.disk_usage(self.out if self.out.exists() else "/").free / 2**30, 1),
            "processes": _sh("ps -eo comm= | sort | uniq -c | grep -E 'UnrealEditor|ShaderCompile|zenserver|tar|zstd|blender|chrome' "
                             "|| true").splitlines(),
            "engine": (ctx.get("ENGINE") or {}).get("version"),
            "steps_done": sorted({r.get("part") or r.get("step") for r in getattr(sc, "rows", []) if isinstance(r, dict)} - {None}),
            "smoke": {k: {x: v.get(x) for x in ("rc", "seconds", "killed")} for k, v in smoke.items() if isinstance(v, dict)},
            "stage": ctx.get("STAGE"), "mirrored_files": len(self.mirrored),
        }

    def write(self, st: dict) -> None:
        tmp = self.dir / "heartbeat.json.tmp"
        tmp.write_text(json.dumps(st, indent=1, default=str))
        os.replace(tmp, self.dir / "heartbeat.json")
        with open(self.dir / "heartbeat.log", "a") as f:
            f.write("%s up %ss · %s %ss · %s · shaders %s · RAM %s · VRAM %s · GPU %s%% · load %s · disk %s GB\n" % (
                st["utc"], st["vm_uptime_s"], st["job"], st["job_elapsed_s"], st.get("stage"), st["shader_jobs_done"], st["ram_gb"],
                st["vram_gb"], st["gpu_util"], (st["load_avg"] or ["?"])[0], st["disk_free_gb"]))
        (self.drive / "heartbeat" / "latest.json").write_text(json.dumps({"run_id": self.run_id, "utc": st["utc"], "folder": str(self.dir)}))

    def mirror(self) -> int:
        """New or changed files under out_dir → Drive (files over mirror_max_mb are skipped)."""
        if not self.out.is_dir():
            return 0
        n = 0
        for f in self.out.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                key, sig = str(f), (st.st_size, int(st.st_mtime))
                if self.mirrored.get(key) == sig or st.st_size > self.max_bytes:
                    continue
                dst = self.dir / "out" / f.relative_to(self.out)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
                self.mirrored[key] = sig
                n += 1
            except Exception:
                continue
        return n

    def beat(self) -> dict:
        self.beats += 1
        st = self.status()
        if time.time() - self.last_mirror >= self.mirror_s:
            st["mirrored_now"] = self.mirror()
            self.last_mirror = time.time()
        self.write(st)
        return st

    def _loop(self):
        while not self.stop_ev.is_set():
            try:
                self.beat()
            except Exception as exc:
                try:
                    with open(self.dir / "heartbeat.log", "a") as f:
                        f.write("%s heartbeat error: %r\n" % (_utc(), exc))
                except Exception:
                    pass
            self.stop_ev.wait(self.beat_s)

    def start(self):
        self.th.start()
        return self

    def alive(self) -> bool:
        return self.th.is_alive()

    def stop(self) -> int:
        self.stop_ev.set()
        return self.mirror()
