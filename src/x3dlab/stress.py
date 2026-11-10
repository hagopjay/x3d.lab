"""The 02 Lab stress test: a JSON-serialisable RunConfig, presets scaled to the GPU, five tiers, a run card.

    cfg = stress.RunConfig.preset_for("standard")     # smoke | standard | heavy, then scaled to the detected GPU
    cfg.tiers["render"]["samples"] = [32, 256]
    run = stress.run(cfg, lab, bl)                # rows stream to the notebook panel and the dashboard
    run.card_path                                 # JSON run card: config, seed, pins, hardware, every row

Tiers
  validate   every generated scene (families x sizes) through x3d_mcp
  roundtrip  X3D -> Blender -> X3D (3.3 / 4.0 / 4.1) -> Blender; fidelity compared; the export validated by x3d_mcp
  drift      N generations of the same round trip; per-generation metrics (light energy, geometry, instancing)
  render     Cycles ladders: samples, resolution, geometry; GPU vs CPU; peak VRAM; blank-frame check
  load       concurrent MCP sessions against the validator
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fidelity, pins, runtime, scenegen
from .events import bus

ADJ = ["amber", "brisk", "calm", "deft", "eager", "fleet", "gilded", "hardy", "keen", "lucid", "mellow", "nimble", "quiet",
       "rapid", "sly", "stout", "tidy", "vivid", "wry", "zesty"]
NOUN = ["anvil", "beacon", "comet", "delta", "ember", "falcon", "glacier", "harbor", "island", "jetty", "kestrel", "lantern",
        "meadow", "nebula", "orchard", "prism", "quarry", "reef", "summit", "tundra"]

PRESETS = {
    "smoke": {
        "families": ["primitives", "lights", "hierarchy"], "sizes": [4],
        "tiers": {"validate": {"on": True},
                  "roundtrip": {"on": True, "versions": ["X3D40"]},
                  "drift": {"on": True, "families": ["lights"], "versions": ["X3D33", "X3D40"], "generations": 3},
                  "render": {"on": True, "backend": "auto", "scene_size": 9, "samples": [16], "resolutions": [[480, 270]],
                             "geometry": [9], "denoise": False, "compare_cpu": False},
                  "load": {"on": True, "sessions": 4, "calls": 5}}},
    "standard": {
        "families": list(scenegen.FAMILIES), "sizes": [4, 16, 64],
        "tiers": {"validate": {"on": True},
                  "roundtrip": {"on": True, "versions": ["X3D33", "X3D40", "X3D41"]},
                  "drift": {"on": True, "families": ["lights", "hierarchy", "materials"], "versions": ["X3D33", "X3D40", "X3D41"],
                            "generations": 4},
                  "render": {"on": True, "backend": "auto", "scene_size": 36, "samples": [32, 128, 512],
                             "resolutions": [[960, 540], [1920, 1080]], "geometry": [16, 100, 400], "denoise": True,
                             "compare_cpu": True},
                  "load": {"on": True, "sessions": 16, "calls": 10}}},
    "heavy": {
        "families": list(scenegen.FAMILIES), "sizes": [16, 64, 256, 1024],
        "tiers": {"validate": {"on": True},
                  "roundtrip": {"on": True, "versions": ["X3D33", "X3D40", "X3D41"]},
                  "drift": {"on": True, "families": ["lights", "hierarchy", "materials", "meshes"], "versions": ["X3D33", "X3D40", "X3D41"],
                            "generations": 8},
                  "render": {"on": True, "backend": "auto", "scene_size": 100, "samples": [64, 256, 1024, 4096],
                             "resolutions": [[1280, 720], [1920, 1080], [3840, 2160]], "geometry": [100, 400, 1600, 6400],
                             "denoise": True, "compare_cpu": True},
                  "load": {"on": True, "sessions": 48, "calls": 20}}},
}
# The 03 notebook's presets. Research tiers are configured in research.DEFAULTS; a preset overrides what it names.
PRESETS["research"] = {
    "families": ["primitives", "lights", "hierarchy"], "sizes": [4],
    "tiers": {"validate": {"on": True}, "fuzz": {"on": True}, "matrix": {"on": True}, "crossrender": {"on": True},
              "scale": {"on": True}, "traces": {"on": True}}}
PRESETS["ci"] = {                        # deterministic, CPU-only, no browser or Blender required (they join when present)
    "families": ["primitives", "lights", "hierarchy", "materials"], "sizes": [4],
    "tiers": {"validate": {"on": True},
              "matrix": {"on": True, "oracles": ["fork", "lxml-xsd", "consortium"]},
              "fuzz": {"on": True, "n": 80, "seed": 2026, "oracles": ["fork", "lxml-xsd", "consortium"], "minimize": False,
                       "concurrency": 4}}}
# How far each GPU class stretches the render ladders (CPU-only runtimes are cut down hard).
GPU_SCALE = {"CPU": 0.125, "T4": 0.5, "L4": 1.0, "A100": 1.5, "H100": 2.0, "G4": 2.0, "other": 1.0}


@dataclass
class RunConfig:
    preset: str = "standard"
    seed: int = 7
    name: str = ""
    families: list = field(default_factory=lambda: list(scenegen.FAMILIES))
    sizes: list = field(default_factory=lambda: [4, 16, 64])
    tiers: dict = field(default_factory=dict)
    workers: int = 2                      # parallel Blender processes for round trips
    timeout_s: int = 1800                 # per Blender process
    notes: str = ""

    @classmethod
    def preset_for(cls, name: str = "standard", gpu_kind: str | None = None, seed: int | None = None) -> "RunConfig":
        p = copy.deepcopy(PRESETS[name])
        cfg = cls(preset=name, families=p["families"], sizes=p["sizes"], tiers=p["tiers"])
        if seed is not None:
            cfg.seed = seed
        kind = gpu_kind or runtime.detect().gpu_kind
        cfg.scale_to(kind)
        import os
        cfg.workers = max(1, min(6, (os.cpu_count() or 2) // 2))
        return cfg

    def scale_to(self, gpu_kind: str) -> None:
        f = GPU_SCALE.get(gpu_kind, 1.0)
        r = self.tiers.get("render")
        if not r or f == 1.0:
            return
        r["samples"] = sorted({max(4, int(s * f)) for s in r["samples"]})
        r["geometry"] = sorted({max(4, int(g * f)) for g in r["geometry"]})
        if f < 1:
            r["resolutions"] = [[max(160, int(w * max(f, 0.25))), max(90, int(h * max(f, 0.25)))] for w, h in r["resolutions"]]
            r["compare_cpu"] = False
        self.notes = "render ladders scaled ×%s for %s" % (f, gpu_kind)

    def run_name(self) -> str:
        if self.name:
            return self.name
        rnd = random.Random(self.seed)
        return "%s-%s-%d" % (rnd.choice(ADJ), rnd.choice(NOUN), self.seed)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "RunConfig":
        d = json.loads(text)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Row:
    tier: str
    case: str
    status: str               # PASS | WARN | FAIL | SKIP
    ms: float | None = None
    detail: str = ""
    metrics: dict = field(default_factory=dict)


class StressRun:
    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.id = uuid.uuid4().hex[:10]
        self.name = cfg.run_name()
        self.dir = runtime.sub("out", self.id)
        self.rows: list[Row] = []
        self.drift: dict = {}            # "family/version" -> per-generation rows
        self.renders: list[dict] = []
        self.started = time.time()
        self.ended: float | None = None
        self.blender: dict = {}
        self.research: dict = {}         # fuzz / matrix / crossrender / scale / traces results (03 notebook)
        self.card_path: Path | None = None

    def add(self, row: Row) -> Row:
        self.rows.append(row)
        lvl = {"FAIL": "ERROR", "WARN": "WARN"}.get(row.status, "INFO")
        bus.emit("%s · %s · %s%s" % (row.tier, row.case, row.status, (" · " + row.detail[:120]) if row.detail and row.status != "PASS" else ""),
                 level=lvl, src="stress", kind="stress", job=self.id,
                 data={"tier": row.tier, "case": row.case, "status": row.status, "ms": row.ms, "detail": row.detail[:300],
                       "metrics": row.metrics, "run": self.id})
        return row

    def summary(self) -> dict:
        out = {}
        for r in self.rows:
            t = out.setdefault(r.tier, {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0})
            t[r.status] += 1
        return out

    def file_url(self, name: str) -> str:
        return "out/%s/%s" % (self.id, name)


# ---------------------------------------------------------------------- tiers
def _scene_path(run: StressRun, family: str, size: int) -> Path:
    p = run.dir / ("%s_%d.x3d" % (family, size))
    if not p.exists():
        n = size * 300 if family == "meshes" else size
        p.write_text(scenegen.make(family, n, run.cfg.seed), encoding="utf-8")
    return p


def tier_validate(run: StressRun, lab) -> None:
    for fam in run.cfg.families:
        for size in run.cfg.sizes:
            p = _scene_path(run, fam, size)
            t = time.perf_counter()
            v = lab.client.check(p.read_text())
            run.add(Row("validate", "%s × %d" % (fam, size), "PASS" if v.ok else "FAIL", (time.perf_counter() - t) * 1000,
                        "" if v.ok else v.report()[:200], {"bytes": p.stat().st_size, "errors": len(v.errors), "warnings": len(v.warnings)}))


def _roundtrip_case(run: StressRun, lab, bl, fam: str, size: int, version: str, gens: int) -> dict:
    src = _scene_path(run, fam, size)
    prefix = str(run.dir / ("rt_%s_%d_%s" % (fam, size, version.lower())))
    return bl.roundtrip(str(src), version, generations=gens, out_prefix=prefix, job=run.id)


def tier_roundtrip(run: StressRun, lab, bl) -> None:
    t = run.cfg.tiers["roundtrip"]
    cases = [(f, s, v) for f in run.cfg.families for s in run.cfg.sizes for v in t["versions"]]
    with ThreadPoolExecutor(max_workers=run.cfg.workers) as ex:
        futs = {ex.submit(_roundtrip_case, run, lab, bl, f, s, v, 1): (f, s, v) for f, s, v in cases}
        for fut, (f, s, v) in [(k, futs[k]) for k in futs]:
            r = fut.result()
            case = "%s × %d → %s" % (f, s, v.replace("X3D", "X3D ")[:7])
            res = r.get("result") or {}
            if not r["ok"] or "final" not in res:
                run.add(Row("roundtrip", case, "FAIL", (r.get("seconds") or 0) * 1000, res.get("reason") or "failed (see the Blender log)"))
                continue
            g0 = res["generations"][0]
            fid = fidelity.compare(g0["snapshot"], res["final"]["snapshot"])
            exported = Path(g0["output"]).read_text(encoding="utf-8", errors="replace")
            ver = lab.client.check(exported)
            status = "PASS" if fid.ok and ver.ok else "WARN"
            detail = "; ".join(fid.findings + ([] if ver.ok else ["export not valid X3D: " + ver.report()[:160]]))
            run.add(Row("roundtrip", case, status, (r.get("seconds") or 0) * 1000, detail,
                        {"import_s": g0.get("import_s"), "export_s": g0.get("export_s"), "bytes": g0.get("bytes"),
                         "counts": g0["snapshot"]["counts"], "export_valid": ver.ok, "max_err": fid.max_err,
                         "light_ratios": fid.light_ratios[:8], "instancing": fid.instancing}))


def tier_drift(run: StressRun, lab, bl) -> None:
    t = run.cfg.tiers["drift"]
    size = run.cfg.sizes[min(1, len(run.cfg.sizes) - 1)]
    cases = [(f, v) for f in t["families"] for v in t["versions"]]
    with ThreadPoolExecutor(max_workers=run.cfg.workers) as ex:
        futs = [(ex.submit(_roundtrip_case, run, lab, bl, f, size, v, int(t["generations"])), f, v) for f, v in cases]
        for fut, f, v in futs:
            r = fut.result()
            res = r.get("result") or {}
            rows = fidelity.drift(res) if r["ok"] else []
            key = "%s/%s" % (f, v)
            run.drift[key] = rows
            if not rows:
                run.add(Row("drift", key, "FAIL", None, res.get("reason") or "no generations"))
                continue
            stable = all(x["ok_vs_gen0"] for x in rows)
            last = rows[-1]
            run.add(Row("drift", "%s × %d gens" % (key, len(rows) - 1), "PASS" if stable else "WARN", (r.get("seconds") or 0) * 1000,
                        "" if stable else "after %d gens: light energy ×%.3g–×%.3g, %d mesh(es) changed, objects %d→%d, meshes %d→%d"
                        % (len(rows) - 1, last["light_ratio_min"], last["light_ratio_max"], last["geometry_changed"],
                           rows[0]["objects"], last["objects"], rows[0]["meshes"], last["meshes"]),
                        {"generations": rows}))


def _render_tasks(run: StressRun, backend: str, label: str, items: list[tuple[str, int, int, list]]) -> list[dict]:
    tasks = []
    for case, size, samples, res in items:
        src = _scene_path(run, "primitives", size)
        png = "render_%s_%s.png" % (label, case.replace(" ", "").replace("×", "x").replace("/", "-"))
        tasks.append({"kind": "render", "src": str(src), "out_png": str(run.dir / png), "backend": backend, "samples": samples,
                      "resolution": res, "denoise": run.cfg.tiers["render"].get("denoise", False), "camera": "viewpoint",
                      "adaptive": bool(run.cfg.tiers["render"].get("adaptive", False)),
                      "_case": case, "_png": png})
    return tasks


def tier_render(run: StressRun, lab, bl) -> None:
    t = run.cfg.tiers["render"]
    backend = bl.best_backend if t.get("backend", "auto") == "auto" else t["backend"]
    mid_res = t["resolutions"][0]
    mid_s = t["samples"][min(1, len(t["samples"]) - 1)]
    ladders = {
        "samples": [("%d spp" % s, t["scene_size"], s, mid_res) for s in t["samples"]],
        "resolution": [("%dx%d" % tuple(r), t["scene_size"], mid_s, r) for r in t["resolutions"]],
        "geometry": [("%d shapes" % g, g, mid_s, mid_res) for g in t["geometry"]],
    }
    backends = [backend] + (["CPU"] if t.get("compare_cpu") and backend != "CPU" else [])
    for be in backends:
        for ladder, items in ladders.items():
            if be == "CPU" and backend != "CPU" and ladder != "samples":
                continue                                       # the CPU comparison runs one ladder
            tasks = _render_tasks(run, be, "%s_%s" % (be.lower(), ladder), items)
            t0 = time.time()
            if be != "CPU":
                with bl.gpu:
                    bl.sampler.start()
                    results = bl.run([{k: v for k, v in x.items() if not k.startswith("_")} for x in tasks], timeout=run.cfg.timeout_s,
                                     job=run.id, label="render")
            else:
                results = bl.run([{k: v for k, v in x.items() if not k.startswith("_")} for x in tasks], timeout=run.cfg.timeout_s,
                                 job=run.id, label="render")
            for task, r in zip(tasks, results):
                res = r.get("result") or {}
                case = "%s · %s · %s" % (be, ladder, task["_case"])
                if not r["ok"]:
                    run.add(Row("render", case, "FAIL", None, res.get("reason") or "failed (see the Blender log)"))
                    continue
                gpu = bl.sampler.peak(task_t0(r, t0), time.time()) if be != "CPU" else {}
                cov = (res.get("pixels") or {}).get("covered", 1)
                fell_back = be != "CPU" and res.get("backend") == "CPU"
                status = "WARN" if cov < 0.01 or fell_back or res.get("denoiser_note") else "PASS"
                detail = "; ".join(x for x in ("blank frame" if cov < 0.01 else "",
                                               "asked for %s, rendered on CPU" % be if fell_back else "",
                                               res.get("denoiser_note") or "") if x)
                px = res["resolution"][0] * res["resolution"][1]
                m = {"seconds": res["seconds"], "samples": res["samples"], "resolution": res["resolution"], "backend": res["backend"],
                     "devices": res.get("devices"), "mpix_samples_per_s": round(px * res["samples"] / max(res["seconds"], 1e-6) / 1e6, 2),
                     "vertices": res["counts"]["vertices"], "covered": cov, "denoiser": res.get("denoiser"),
                     "adaptive": res.get("adaptive"), **gpu}
                run.add(Row("render", case, status, res["seconds"] * 1000, detail, m))
                run.renders.append({"case": case, "url": run.file_url(task["_png"]), **m})
                bus.emit("render ready: %s" % case, level="DEBUG", src="stress", kind="render", job=run.id,
                         data={"url": run.file_url(task["_png"]), "case": case, "seconds": res["seconds"], "run": run.id})
    cpu = {r["samples"]: r["seconds"] for r in run.renders if r["backend"] == "CPU" and "samples" in r["case"]}
    gpu = {r["samples"]: r["seconds"] for r in run.renders if r["backend"] != "CPU" and "· samples ·" in r["case"]}
    for s in sorted(set(cpu) & set(gpu)):
        run.add(Row("render", "GPU speed-up at %d spp" % s, "PASS", None, "×%.1f (%s %.2f s vs CPU %.2f s)" % (cpu[s] / gpu[s], backend, gpu[s], cpu[s]),
                    {"speedup": round(cpu[s] / gpu[s], 2)}))


def task_t0(r: dict, fallback: float) -> float:
    return time.time() - (r.get("seconds") or 0) if r.get("seconds") else fallback


def tier_load(run: StressRun, lab) -> None:
    t = run.cfg.tiers["load"]
    scene = _scene_path(run, "primitives", run.cfg.sizes[0]).read_text()
    lat, errors = [], []

    async def worker():
        try:
            async with lab.client.session() as s:
                for _ in range(int(t["calls"])):
                    t0 = time.perf_counter()
                    try:
                        r = await s.call_tool("validate_x3d", {"content": scene})
                        if r.isError:
                            errors.append("tool error")
                    except Exception as e:
                        errors.append(type(e).__name__)
                    lat.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            errors.append("session " + type(e).__name__)

    async def all_():
        await asyncio.gather(*(worker() for _ in range(int(t["sessions"]))))

    t0 = time.perf_counter()
    lab.client.run(all_())
    wall = time.perf_counter() - t0
    total = int(t["sessions"]) * int(t["calls"])
    q = statistics.quantiles(lat, n=100) if len(lat) > 1 else [lat[0] if lat else 0] * 99
    run.add(Row("load", "%d sessions × %d calls" % (t["sessions"], t["calls"]), "FAIL" if errors else "PASS", wall * 1000,
                ("errors: %s" % sorted(set(errors))) if errors else "%.1f calls/s · p50 %.0f · p95 %.0f · p99 %.0f ms" % (total / wall, q[49], q[94], q[98]),
                {"calls_per_s": round(total / wall, 1), "p50": round(q[49], 1), "p95": round(q[94], 1), "p99": round(q[98], 1), "errors": len(errors)}))


TIERS = ["validate", "roundtrip", "drift", "render", "load", "matrix", "fuzz", "scale", "crossrender", "traces"]
NEEDS_BLENDER = ("roundtrip", "drift", "render")


def run(cfg: RunConfig, lab, bl=None, hb=None) -> StressRun:
    """Run every enabled tier. Needs `lab` (x3dlab.up()); Blender tiers need `bl` (blender.setup()); the browser
    tiers use `hb` or lab.browser (lab.start_browser())."""
    from . import research
    hb = hb or getattr(lab, "browser", None)
    sr = StressRun(cfg)
    sr.blender = dict(bl.info) if bl else {}
    bus.emit("stress run %s started (%s preset, seed %d)" % (sr.name, cfg.preset, cfg.seed), src="stress", kind="stress_start", job=sr.id,
             data={"run": sr.id, "name": sr.name, "tiers": [t for t in TIERS if cfg.tiers.get(t, {}).get("on")]})
    for tier in TIERS:
        tc = cfg.tiers.get(tier, {})
        if not tc.get("on"):
            continue
        if tier in NEEDS_BLENDER and bl is None:
            sr.add(Row(tier, "(tier)", "SKIP", None, "Blender is not set up: run the Blender cell first"))
            continue
        bus.emit("tier %s" % tier, src="stress", kind="stress_tier", job=sr.id, data={"tier": tier, "run": sr.id})
        t0 = time.time()
        try:
            if tier == "validate":
                tier_validate(sr, lab)
            elif tier == "roundtrip":
                tier_roundtrip(sr, lab, bl)
            elif tier == "drift":
                tier_drift(sr, lab, bl)
            elif tier == "render":
                tier_render(sr, lab, bl)
            elif tier == "load":
                tier_load(sr, lab)
            elif tier in research.TIERS:
                research.TIERS[tier](sr, lab, bl, hb)
        except Exception as exc:
            sr.add(Row(tier, "(tier crashed)", "FAIL", None, "%s: %s" % (type(exc).__name__, str(exc)[:300])))
        bus.emit("tier %s done" % tier, src="stress", kind="span", job=sr.id,
                 span={"name": "tier %s" % tier, "track": "Lab", "start": t0, "end": time.time()}, data={"tier": tier})
    if bl:
        bl.sampler.stop()
    sr.ended = time.time()
    write_card(sr, lab)
    bus.emit("stress run %s finished: %s" % (sr.name, sr.summary()), src="stress", kind="stress_end", job=sr.id,
             data={"run": sr.id, "summary": sr.summary(), "card": sr.file_url("run_card.json"), "report": sr.file_url("report.html")})
    return sr


# ---------------------------------------------------------------------- run card and report
def write_card(sr: StressRun, lab=None) -> Path:
    card = {"id": sr.id, "name": sr.name, "started": sr.started, "ended": sr.ended, "seconds": round((sr.ended or time.time()) - sr.started, 1),
            "config": asdict(sr.cfg), "pins": pins.summary(), "runtime": runtime.detect().as_dict(), "blender": sr.blender,
            "summary": sr.summary(), "rows": [asdict(r) for r in sr.rows], "drift": sr.drift, "renders": sr.renders,
            "research": sr.research}
    sr.card_path = sr.dir / "run_card.json"
    sr.card_path.write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
    (sr.dir / "report.html").write_text(report_html(card), encoding="utf-8")
    return sr.card_path


def report_html(card: dict) -> str:
    import html as H
    e = lambda s: H.escape(str(s))  # noqa: E731
    col = {"PASS": "#1d6b3a", "WARN": "#9a5b00", "FAIL": "#c62828", "SKIP": "#888"}
    rows = "".join("<tr><td>%s</td><td style='color:%s'>%s</td><td>%s</td><td align=right>%s</td><td>%s</td></tr>"
                   % (e(r["tier"]), col[r["status"]], r["status"], e(r["case"]), "" if r["ms"] is None else "%.0f" % r["ms"], e(r["detail"]))
                   for r in card["rows"])
    gallery = "".join("<figure><img src='%s' loading=lazy><figcaption>%s · %.2f s</figcaption></figure>"
                      % (e(r["url"].split("/")[-1]), e(r["case"]), r["seconds"]) for r in card["renders"])
    drift = ""
    for key, gens in card["drift"].items():
        drift += "<h4>%s</h4><table><tr><th>gen</th><th>objects</th><th>meshes</th><th>vertices</th><th>light energy</th><th>ratio min–max</th><th>meshes changed</th><th>bytes</th></tr>%s</table>" % (
            e(key), "".join("<tr><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%.3f</td><td>%.3f–%.3f</td><td>%d</td><td>%s</td></tr>"
                            % (g["gen"], g["objects"], g["meshes"], g["vertices"], g["light_energy_total"], g["light_ratio_min"],
                               g["light_ratio_max"], g["geometry_changed"], g["bytes"] or "") for g in gens))
    summ = " · ".join("%s %s" % (t, ", ".join("%d %s" % (n, s) for s, n in c.items() if n)) for t, c in card["summary"].items())
    return """<!doctype html><meta charset=utf-8><title>x3d.lab run %s</title>
<style>body{font:14px system-ui;margin:20px;max-width:1200px}table{border-collapse:collapse;font-size:12.5px;margin:6px 0}
td,th{border-bottom:1px solid #ddd;padding:3px 6px;text-align:left}figure{display:inline-block;margin:6px;width:300px}
img{width:100%%;border-radius:6px}figcaption{font-size:12px;color:#666}code{font-size:12px}</style>
<h1>%s</h1><p>%s preset · seed %s · %s s · %s</p><p><code>%s</code></p><p>%s</p>
<h2>Rows</h2><table><tr><th>tier</th><th>status</th><th>case</th><th>ms</th><th>detail</th></tr>%s</table>
<h2>Drift</h2>%s<h2>Renders</h2>%s""" % (
        e(card["name"]), e(card["name"]), e(card["config"]["preset"]), card["config"]["seed"], card["seconds"],
        e(card["runtime"].get("gpu_kind")), e(json.dumps(card["pins"])), e(summ), rows, drift, gallery)


def compare_cards(a: dict, b: dict) -> list[dict]:
    """What changed between two run cards: status flips and big timing changes, per (tier, case)."""
    ra = {(r["tier"], r["case"]): r for r in a["rows"]}
    out = []
    for r in b["rows"]:
        k = (r["tier"], r["case"])
        o = ra.get(k)
        if o is None:
            out.append({"tier": k[0], "case": k[1], "change": "new", "now": r["status"]})
        elif o["status"] != r["status"]:
            out.append({"tier": k[0], "case": k[1], "change": "%s → %s" % (o["status"], r["status"]), "now": r["detail"][:120]})
        elif o.get("ms") and r.get("ms") and (r["ms"] > 1.5 * o["ms"] + 50 or r["ms"] < o["ms"] / 1.5 - 50):
            out.append({"tier": k[0], "case": k[1], "change": "time ×%.2f" % (r["ms"] / o["ms"]), "now": "%.0f ms" % r["ms"]})
    for k in ra.keys() - {(r["tier"], r["case"]) for r in b["rows"]}:
        out.append({"tier": k[0], "case": k[1], "change": "gone", "now": ""})
    return out
