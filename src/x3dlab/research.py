"""The 03 notebook's research tiers. They plug into stress.run() like the 02 tiers and add rows to the same run card.

  fuzz         grammar-aware differential fuzzing (fuzz.py): every case to every oracle, bucketed, minimised
  matrix       a curated corpus (the 17 break-it defects + every scenegen family) through every validator:
               who catches what, at which layer, and where they disagree
  crossrender  the same scene and Viewpoint in X_ITE (WebGL) and Blender Cycles: silhouette IoU and an overlay picture
  scale        big meshes (through the blob store): validator time, X_ITE parse/first frame/heap, Blender import,
               Cycles render time and peak VRAM, per size
  traces       a Chrome trace of X_ITE loading the scale scenes (open it in DevTools > Performance; the lab's own
               bars sit on x3d.lab tracks) plus Chrome's Performance metrics

Oracles available to fuzz and matrix (whatever the session has started):
  fork (lab.client) · consortium (lab.start_upstream()) · lxml-xsd (always) · x_ite (lab.start_browser())
  copilot (lab.load_copilot() before start_browser()) · blender (lab.setup_blender())
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import breakit, browser as browser_mod, fuzz, runtime, scenegen
from .events import bus
from .stress import Row, StressRun

DEFAULTS = {
    "fuzz": {"on": True, "n": 150, "seed": 7, "oracles": ["fork", "consortium", "lxml-xsd", "x_ite", "copilot"],
             "minimize": True, "min_budget": 60, "concurrency": 4, "time_budget_s": 1200,
             "recall_floor": 0.9, "specificity_floor": 0.9},
    "matrix": {"on": True, "oracles": ["fork", "consortium", "lxml-xsd", "x_ite", "copilot", "blender"], "families": list(scenegen.FAMILIES),
               "size": 4},
    "crossrender": {"on": True, "scenes": ["lantern", "pagoda", "primitives:9", "hierarchy:4", "materials:9", "lights:4"],
                    "resolution": [480, 270], "samples": 16, "backend": "auto", "pass_iou": 0.9, "warn_iou": 0.7},
    "scale": {"on": True, "vertices": [20000, 200000, 1000000], "render": True, "render_resolution": [640, 360],
              "render_samples": 16, "xite": True, "blender": True},
    "traces": {"on": True, "screenshots": True},
}


# What each oracle is able to see. A schema engine cannot be blamed for a semantic defect, and viewers are lenient.
SCOPE = {"lxml-xsd": {"xml", "xsd"}}
VALIDATORS = ("fork", "consortium", "copilot")      # full validators: judged on everything, compared with each other


def in_scope(oracle: str, layer: str) -> bool:
    return layer in SCOPE.get(oracle, {layer})


# ---------------------------------------------------------------------- oracles from what the session started
def oracles(lab, names: list, bl=None, hb=None, workdir: Path | None = None) -> tuple[list, list]:
    """Build the requested oracles. Returns (oracles, skipped names with the reason)."""
    out, skipped = [], []
    hb = hb or getattr(lab, "browser", None)
    for n in names:
        if n == "fork":
            out.append(fuzz.MCPOracle(lab.client, "fork"))
        elif n == "consortium":
            if getattr(lab, "upstream_client", None) is None:
                skipped.append((n, "run lab.start_upstream() first"))
            else:
                out.append(fuzz.MCPOracle(lab.upstream_client, "consortium"))
        elif n == "lxml-xsd":
            try:
                out.append(fuzz.XSDOracle())
            except Exception as exc:
                skipped.append((n, "lxml/XSD unavailable: %s" % exc))
        elif n == "x_ite":
            if hb is None:
                skipped.append((n, "run lab.start_browser() first"))
            else:
                out.append(fuzz.XITEOracle(hb))
        elif n == "copilot":
            if hb is None or not getattr(hb, "copilot_dir", None):
                skipped.append((n, "no Copilot build loaded (lab.load_copilot(), then lab.start_browser())"))
            else:
                out.append(fuzz.CopilotOracle(hb))
        elif n == "blender":
            if bl is None:
                skipped.append((n, "Blender is not set up"))
            else:
                out.append(fuzz.BlenderOracle(bl, workdir or runtime.sub("fuzz_blender")))
        else:
            skipped.append((n, "unknown oracle"))
    for n, why in skipped:
        bus.info("oracle %s skipped: %s" % (n, why), src="research")
    return out, skipped


def _tc(run: StressRun, tier: str) -> dict:
    return {**DEFAULTS[tier], **(run.cfg.tiers.get(tier) or {})}


# ---------------------------------------------------------------------- fuzz
def tier_fuzz(run: StressRun, lab, bl=None, hb=None) -> None:
    t = _tc(run, "fuzz")
    orcs, skipped = oracles(lab, t["oracles"], bl, hb, run.dir / "fuzz_blender")
    for n, why in skipped:
        run.add(Row("fuzz", "oracle %s" % n, "SKIP", None, why))
    from . import scenes
    seeds = {"lantern": scenes.LANTERN, "pagoda": scenes.PAGODA,
             **{f: scenegen.make(f, 4, run.cfg.seed) for f in ("primitives", "lights", "hierarchy", "materials")}}
    cfg = fuzz.FuzzConfig(n=int(t["n"]), seed=int(t["seed"]), seeds=seeds, minimize=bool(t["minimize"]), min_budget=int(t["min_budget"]),
                          concurrency=int(t["concurrency"]), time_budget_s=float(t["time_budget_s"]),
                          **({"ops": t["ops"]} if t.get("ops") else {}))
    t0 = time.time()
    fr = fuzz.run(cfg, orcs)
    fr.write(run.dir / "fuzz")
    card = fr.card()
    run.research["fuzz"] = {k: card[k] for k in ("cases", "ops", "confusion", "agreement", "by_operator", "buckets", "disagreements", "oracles")}
    run.research["fuzz"]["report"] = run.file_url("fuzz/report.html")
    for name, c in card["confusion"].items():
        low = [k for k, floor in (("recall", t["recall_floor"]), ("specificity", t["specificity_floor"]))
               if name in VALIDATORS and c.get(k) is not None and c[k] < floor]
        status = "FAIL" if c.get("crash") else "WARN" if low else "PASS"
        run.add(Row("fuzz", "oracle %s" % name, status, c.get("mean_ms"),
                    "recall %s · specificity %s · %d crash%s" % (c.get("recall"), c.get("specificity"), c.get("crash", 0),
                                                                 (" · below floor: " + ", ".join(low)) if low else ""), c))
    for b in card["buckets"]:
        status = "FAIL" if b["kind"] == "crash" else "WARN"
        if b["kind"] == "missed" and (b["oracle"] in fuzz.VIEWERS or
                                      not in_scope(b["oracle"], fuzz.Mutators.LAYER.get(b["op"], "semantic"))):
            status = "PASS"                     # lenient by design or out of the oracle's scope: recorded, not flagged
        run.add(Row("fuzz", "%s · %s · %s" % (b["kind"], b["oracle"], b["op"]), status, None,
                    "%s ×%d%s" % (b["sig"][:120], b["count"], " · repro %s→%s B" % (b["orig_bytes"], b["repro_bytes"]) if b.get("repro_bytes") else ""),
                    {"key": b["key"], "count": b["count"], "first": b["first"]}))
    bus.info("fuzz tier: %d cases, %d buckets in %.0f s" % (card["cases"], len(card["buckets"]), time.time() - t0), src="research")


# ---------------------------------------------------------------------- matrix
def corpus(families=scenegen.FAMILIES, size: int = 4, seed: int = 7) -> list:
    """The curated corpus: each break-it defect (expected verdict known) and each scene family (expected: accept)."""
    cases = []
    for d in breakit.CATALOG:
        expect = "accept" if d.expect == "nobody" else "reject"
        cases.append(fuzz.Case("defect:" + d.id, d.apply(breakit.LANTERN), "defect:" + d.id, expect, d.title, d.expect))
    for f in families:
        cases.append(fuzz.Case("family:%s" % f, scenegen.make(f, size, seed), "family:" + f, "accept", "scenegen %s × %d" % (f, size), "accept"))
    return cases


def tier_matrix(run: StressRun, lab, bl=None, hb=None) -> None:
    t = _tc(run, "matrix")
    orcs, skipped = oracles(lab, t["oracles"], bl, hb, run.dir / "matrix_blender")
    for n, why in skipped:
        run.add(Row("matrix", "oracle %s" % n, "SKIP", None, why))
    cases = corpus(t["families"], int(t["size"]), run.cfg.seed)
    fr = fuzz.run(fuzz.FuzzConfig(n=len(cases), seed=run.cfg.seed, minimize=False, concurrency=4), orcs, cases=cases)
    names = [o.name for o in orcs]
    cells = {c.id: {n: {"v": fr.out[c.id][n].verdict, "sig": fr.out[c.id][n].sig, "ms": round(fr.out[c.id][n].ms, 1)}
                    for n in names if n in fr.out.get(c.id, {})} for c in fr.cases}
    run.research["matrix"] = {"oracles": names, "cases": [{"id": c.id, "expect": c.expect, "layer": c.target, "note": c.note} for c in fr.cases],
                              "cells": cells, "confusion": fr.confusion(), "agreement": fr.agreement()}
    for c in fr.cases:
        row = cells[c.id]
        layer = c.target if c.target in ("xml", "xsd", "semantic") else "xsd"
        wrong = [n for n in names if n in row and row[n]["v"] in ("accept", "reject") and row[n]["v"] != c.expect
                 and n not in fuzz.VIEWERS and (c.expect == "accept" or in_scope(n, layer))]
        crashed = [n for n in names if n in row and row[n]["v"] == "crash"]
        validators = [n for n in names if n in VALIDATORS and n in row]
        split = len({row[n]["v"] for n in validators}) > 1
        status = "FAIL" if crashed else "WARN" if wrong else "PASS"
        detail = " · ".join("%s %s%s" % (n, row[n]["v"], (" (%s)" % row[n]["sig"][:40]) if row[n]["sig"] else "") for n in names if n in row)
        if split:
            detail = "validators disagree · " + detail
        run.add(Row("matrix", c.id, status, None, detail[:300], {"expect": c.expect, "wrong": wrong, "split": split}))


# ---------------------------------------------------------------------- cross-renderer
def _scene_xml(spec: str, seed: int) -> tuple[str, str]:
    from . import scenes
    if spec == "lantern":
        return "lantern", scenes.LANTERN
    if spec == "pagoda":
        return "pagoda", scenes.PAGODA
    fam, _, size = spec.partition(":")
    return "%s_%s" % (fam, size or 4), scenegen.make(fam, int(size or 4), seed)


def tier_crossrender(run: StressRun, lab, bl=None, hb=None) -> None:
    t = _tc(run, "crossrender")
    hb = hb or getattr(lab, "browser", None)
    if hb is None or bl is None:
        run.add(Row("crossrender", "(tier)", "SKIP", None, "needs both the headless browser (lab.start_browser()) and Blender"))
        return
    w, h = t["resolution"]
    backend = bl.best_backend if t.get("backend", "auto") == "auto" else t["backend"]
    items, tasks = [], []
    for spec in t["scenes"]:
        name, xml = _scene_xml(spec, run.cfg.seed)
        src = run.dir / ("xr_%s.x3d" % name)
        src.write_text(xml, encoding="utf-8")
        x = hb.load(xml, screenshot=True, size=(w, h), background="1 0 1", antialiased=False)
        if x.png:
            (run.dir / ("xr_%s_xite.png" % name)).write_bytes(x.png)
        items.append((name, x))
        tasks.append({"kind": "render", "src": str(src), "out_png": str(run.dir / ("xr_%s_cycles.png" % name)), "backend": backend,
                      "samples": int(t["samples"]), "resolution": [w, h], "denoise": False, "camera": "viewpoint", "film_transparent": True})
    results = bl.run(tasks, timeout=run.cfg.timeout_s, job=run.id, label="crossrender")
    out = []
    for (name, x), r in zip(items, results):
        res = r.get("result") or {}
        case = name
        if not x.ok or not x.png:
            run.add(Row("crossrender", case, "FAIL", None, "X_ITE: " + "; ".join(x.errors[:2])))
            continue
        if not r["ok"]:
            run.add(Row("crossrender", case, "FAIL", None, "Cycles: %s" % (res.get("error") or res.get("reason") or "failed")))
            continue
        a = browser_mod.silhouette(x.png, "key")
        b = browser_mod.silhouette(Path(res["png"]).read_bytes(), "alpha")
        if a.shape != b.shape:
            run.add(Row("crossrender", case, "FAIL", None, "image sizes differ: X_ITE %s, Cycles %s" % (a.shape, b.shape)))
            continue
        score = browser_mod.iou(a, b)
        (run.dir / ("xr_%s_overlay.png" % name)).write_bytes(browser_mod.overlay_png(a, b))
        status = "PASS" if score >= t["pass_iou"] else "WARN" if score >= t["warn_iou"] else "FAIL"
        cam = res.get("camera_info") or {}
        detail = "IoU %.3f · X_ITE covers %.1f%% · Cycles %.1f%% · Blender camera %s (fit %s, angle %s)" % (
            score, 100 * a.mean(), 100 * b.mean(), res.get("camera"), cam.get("sensor_fit"), cam.get("angle"))
        m = {"iou": round(score, 4), "xite_cover": round(float(a.mean()), 4), "cycles_cover": round(float(b.mean()), 4),
             "xite_only": round(float((a & ~b).mean()), 4), "cycles_only": round(float((~a & b).mean()), 4), "camera": cam,
             "xite_ms": round(x.ms), "cycles_s": res.get("seconds")}
        run.add(Row("crossrender", case, status, None, detail, m))
        out.append({"case": name, "xite": run.file_url("xr_%s_xite.png" % name), "cycles": run.file_url("xr_%s_cycles.png" % name),
                    "overlay": run.file_url("xr_%s_overlay.png" % name), **m})
    run.research["crossrender"] = out


# ---------------------------------------------------------------------- scale
def tier_scale(run: StressRun, lab, bl=None, hb=None) -> None:
    t = _tc(run, "scale")
    hb = hb or getattr(lab, "browser", None)
    rows = []
    for nv in t["vertices"]:
        xml = scenegen.make("meshes", int(nv), run.cfg.seed)
        src = run.dir / ("scale_%d.x3d" % nv)
        src.write_text(xml, encoding="utf-8")
        mb = len(xml.encode()) / 1e6
        rec = {"vertices": nv, "mb": round(mb, 2), "path": str(src)}
        t0 = time.perf_counter()
        try:
            v = lab.client.check(xml)
            rec["validate_ms"] = round((time.perf_counter() - t0) * 1000)
            rec["valid"] = v.ok
            run.add(Row("scale", "validate %s verts (%.1f MB%s)" % (f"{nv:,}", mb, ", blob" if mb > 3.5 else ""),
                        "PASS" if v.ok else "FAIL", rec["validate_ms"], "" if v.ok else v.report(2), {"mb": rec["mb"]}))
        except Exception as exc:
            run.add(Row("scale", "validate %s verts" % f"{nv:,}", "FAIL", None, "%s: %s" % (type(exc).__name__, str(exc)[:200])))
        if t.get("xite") and hb is not None:
            x = hb.load(xml, timeout=300, frames=3)
            rec["xite"] = {"ok": x.ok, **x.t, "heap_mb": round((x.heap or 0) / 1e6, 1)}
            run.add(Row("scale", "X_ITE %s verts" % f"{nv:,}", "PASS" if x.ok and not x.errors else "FAIL", x.t.get("total"),
                        x.summary() + (" · heap %.0f MB" % ((x.heap or 0) / 1e6)), rec["xite"]))
        if t.get("blender") and bl is not None:
            r = bl.import_(str(src), job=run.id)
            res = r.get("result") or {}
            rec["blender_import_s"] = res.get("import_s")
            run.add(Row("scale", "Blender import %s verts" % f"{nv:,}", "PASS" if r["ok"] else "FAIL", (res.get("import_s") or 0) * 1000,
                        ("%s vertices, %s faces" % (res["counts"]["vertices"], res["counts"]["faces"])) if r["ok"]
                        else (res.get("error") or "failed"), res.get("counts") or {}))
            if t.get("render") and r["ok"]:
                rr = bl.render(str(src), str(run.dir / ("scale_%d.png" % nv)), samples=int(t["render_samples"]),
                               resolution=tuple(t["render_resolution"]), denoise=False, job=run.id)
                rres = rr.get("result") or {}
                gpu = rres.get("gpu") or {}
                rec["render"] = {"seconds": rres.get("seconds"), "backend": rres.get("backend"), **gpu}
                run.add(Row("scale", "Cycles %s verts (%s)" % (f"{nv:,}", rres.get("backend", "?")), "PASS" if rr["ok"] else "FAIL",
                            (rres.get("seconds") or 0) * 1000,
                            ("%.2f s%s" % (rres.get("seconds") or 0, " · peak VRAM %s MB" % gpu["peak_mem_mb"] if gpu.get("peak_mem_mb") else ""))
                            if rr["ok"] else (rres.get("error") or "failed"), rec["render"]))
                if rr["ok"]:
                    run.renders.append({"case": "scale %s verts" % f"{nv:,}", "url": run.file_url("scale_%d.png" % nv),
                                        "seconds": rres.get("seconds") or 0, "samples": rres.get("samples"), "backend": rres.get("backend")})
        rows.append(rec)
    run.research["scale"] = rows


# ---------------------------------------------------------------------- traces
def tier_traces(run: StressRun, lab, bl=None, hb=None) -> None:
    t = _tc(run, "traces")
    hb = hb or getattr(lab, "browser", None)
    if hb is None:
        run.add(Row("traces", "(tier)", "SKIP", None, "needs the headless browser (lab.start_browser())"))
        return
    from . import scenes
    scale = run.research.get("scale") or []
    targets = [("pagoda", scenes.PAGODA)] + [("meshes_%d" % s["vertices"], Path(s["path"]).read_text(encoding="utf-8")) for s in scale[:2]]
    out = []
    for name, xml in targets:
        path = run.dir / ("trace_%s.json" % name)
        with hb.trace(path, screenshots=bool(t["screenshots"])) as box:
            r = hb.load(xml, timeout=300, frames=10)
        m = hb.metrics()
        info = {"case": name, "url": run.file_url(path.name), "events": box.get("events"), "measures": sorted(set(box.get("measures", []))),
                "long_tasks_ms": box.get("long_tasks_ms"), "tasks_over_50ms": box.get("tasks_over_50ms"),
                "js_heap_mb": round(m.get("JSHeapUsedSize", 0) / 1e6, 1), "script_s": round(m.get("ScriptDuration", 0), 2),
                "layout_s": round(m.get("LayoutDuration", 0), 3), "load_ok": r.ok}
        out.append(info)
        has_bars = {"parse", "replaceWorld", "first frames"} <= set(info["measures"])
        run.add(Row("traces", name, "PASS" if r.ok and has_bars else "WARN", r.t.get("total"),
                    "%s events · %d tasks > 50 ms (longest %s ms) · heap %.0f MB%s" % (
                        f"{info['events'] or 0:,}", info["tasks_over_50ms"] or 0, (info["long_tasks_ms"] or ["–"])[0], info["js_heap_mb"],
                        "" if has_bars else " · x3d.lab bars missing from the trace"), info))
    run.research["traces"] = out


TIERS = {"fuzz": tier_fuzz, "matrix": tier_matrix, "crossrender": tier_crossrender, "scale": tier_scale, "traces": tier_traces}


def save_json(obj, path: Path) -> Path:
    path.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    return path
