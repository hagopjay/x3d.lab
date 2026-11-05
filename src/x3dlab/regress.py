"""Regression harness: run the deterministic `ci` preset, snapshot every verdict, compare with a committed baseline.

    python -m x3dlab ci                                  # compare with src/x3dlab/baselines/ci.json; exit 1 on a regression
    python -m x3dlab ci --update                         # accept the current behaviour as the new baseline
    python -m x3dlab ci --oracles fork,lxml-xsd          # skip the consortium server

What is compared (all of it deterministic for fixed pins and seed):
  validate   the status of every generated scene
  matrix     every oracle's verdict on the curated corpus (17 break-it defects + 6 scene families)
  fuzz       every oracle's verdict on 80 seeded fuzz cases, recall/specificity, and the set of finding buckets

Gates
  FAIL  a verdict that got worse (a caught defect now missed, a clean scene now rejected), a new crash or new finding
        bucket, recall or specificity down by more than the tolerance, a validate row that stopped passing
  INFO  improvements (a bucket gone, a missed defect now caught), new or removed cases/oracles, pin changes
  WARN  timing more than `slow_factor` times the baseline (only with --timing: CI machines differ)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import pins

DEFAULT_BASELINE = Path(__file__).resolve().parent / "baselines" / "ci.json"   # ships with the package
TOLERANCE = 0.02


@dataclass
class Finding:
    level: str          # FAIL | WARN | INFO
    area: str
    what: str
    detail: str = ""


# ---------------------------------------------------------------------- snapshot
def snapshot(run) -> dict:
    """Everything the gates look at, from a finished StressRun of the ci preset."""
    snap = {"version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "pins": pins.summary(), "preset": run.cfg.preset,
            "seed": run.cfg.seed, "validate": {}, "matrix": {}, "fuzz": {}, "timing": {}}
    snap["validate"] = {r.case: r.status for r in run.rows if r.tier == "validate"}
    vt = [r.ms for r in run.rows if r.tier == "validate" and r.ms]
    if vt:
        snap["timing"]["validate_ms_median"] = round(statistics.median(vt), 1)
    m = run.research.get("matrix")
    if m:
        snap["matrix"] = {"oracles": m["oracles"], "expect": {c["id"]: c["expect"] for c in m["cases"]},
                          "verdicts": {cid: {o: cell["v"] for o, cell in row.items()} for cid, row in m["cells"].items()},
                          "sigs": {cid: {o: cell["sig"] for o, cell in row.items()} for cid, row in m["cells"].items()}}
    f = run.research.get("fuzz")
    if f:
        card = json.loads((run.dir / "fuzz" / "fuzz_card.json").read_text(encoding="utf-8"))
        snap["fuzz"] = {"oracles": f["oracles"], "cases": f["cases"],
                        "confusion": {o: {k: c.get(k) for k in ("recall", "specificity", "crash", "caught", "missed", "passed", "false_alarm")}
                                      for o, c in f["confusion"].items()},
                        "buckets": sorted(b["key"] for b in f["buckets"]),
                        "verdicts": card.get("verdicts", {}), "expect": card.get("expect", {})}
        snap["timing"]["fuzz_mean_ms"] = {o: c.get("mean_ms") for o, c in f["confusion"].items()}
    return snap


# ---------------------------------------------------------------------- gates
def _worse(expect: str, old: str, new: str) -> bool:
    good = lambda v: v == expect  # noqa: E731
    return (good(old) and not good(new)) or (old != "crash" and new == "crash")


def compare(base: dict, now: dict, tolerance: float = TOLERANCE, timing: bool = False, slow_factor: float = 2.0) -> list[Finding]:
    out: list[Finding] = []
    if base.get("pins") != now.get("pins"):
        changed = sorted(k for k in set(base.get("pins", {})) | set(now.get("pins", {})) if base.get("pins", {}).get(k) != now.get("pins", {}).get(k))
        out.append(Finding("INFO", "pins", "pins changed: %s" % ", ".join(changed),
                           "verdict changes below may be intended; review them, then --update"))
    if base.get("seed") != now.get("seed"):
        out.append(Finding("FAIL", "config", "seed changed %s -> %s: the snapshots are not comparable" % (base.get("seed"), now.get("seed"))))
        return out

    # validate
    for case, st in now.get("validate", {}).items():
        old = base.get("validate", {}).get(case)
        if old is None:
            out.append(Finding("INFO", "validate", "new case %s: %s" % (case, st)))
        elif old == "PASS" and st != "PASS":
            out.append(Finding("FAIL", "validate", "%s stopped passing (%s)" % (case, st)))
        elif old != "PASS" and st == "PASS":
            out.append(Finding("INFO", "validate", "%s passes now (was %s)" % (case, old)))

    # matrix and fuzz verdicts
    for area in ("matrix", "fuzz"):
        b, n = base.get(area) or {}, now.get(area) or {}
        if not b or not n:
            if b and not n:
                out.append(Finding("FAIL", area, "the %s tier did not run" % area))
            continue
        expect = {**b.get("expect", {}), **n.get("expect", {})}
        for o in sorted(set(b.get("oracles", [])) ^ set(n.get("oracles", []))):
            out.append(Finding("INFO", area, "oracle %s %s" % (o, "added" if o in n.get("oracles", []) else "not run this time")))
        flips_bad, flips_good = [], []
        for cid, row in n.get("verdicts", {}).items():
            old_row = b.get("verdicts", {}).get(cid)
            if old_row is None:
                continue
            for o, v in row.items():
                ov = old_row.get(o)
                if ov is None or ov == v:
                    continue
                e = expect.get(cid, "accept")
                (flips_bad if _worse(e, ov, v) else flips_good).append("%s/%s: %s -> %s (expected %s)" % (cid, o, ov, v, e))
        for f in flips_bad:
            out.append(Finding("FAIL", area, "verdict got worse", f))
        for f in flips_good:
            out.append(Finding("INFO", area, "verdict changed", f))
        gone = set(b.get("verdicts", {})) - set(n.get("verdicts", {}))
        if gone:
            out.append(Finding("INFO", area, "%d cases no longer run" % len(gone), ", ".join(sorted(gone)[:8])))
        if area == "fuzz":
            for o, c in n.get("confusion", {}).items():
                oc = b.get("confusion", {}).get(o) or {}
                for k in ("recall", "specificity"):
                    if oc.get(k) is not None and c.get(k) is not None and c[k] < oc[k] - tolerance:
                        out.append(Finding("FAIL", "fuzz", "%s %s fell %.3f -> %.3f" % (o, k, oc[k], c[k])))
                if (c.get("crash") or 0) > (oc.get("crash") or 0):
                    out.append(Finding("FAIL", "fuzz", "%s crashes %s -> %s" % (o, oc.get("crash") or 0, c.get("crash"))))
            nb, ob = set(n.get("buckets", [])), set(b.get("buckets", []))
            for k in sorted(nb - ob):
                out.append(Finding("FAIL", "fuzz", "new finding bucket", k))
            for k in sorted(ob - nb):
                out.append(Finding("INFO", "fuzz", "bucket gone", k))

    if timing:
        bt, nt = base.get("timing", {}), now.get("timing", {})
        if bt.get("validate_ms_median") and nt.get("validate_ms_median") and nt["validate_ms_median"] > slow_factor * bt["validate_ms_median"]:
            out.append(Finding("WARN", "timing", "validate median %.0f -> %.0f ms" % (bt["validate_ms_median"], nt["validate_ms_median"])))
        for o, ms in (nt.get("fuzz_mean_ms") or {}).items():
            old = (bt.get("fuzz_mean_ms") or {}).get(o)
            if old and ms and ms > slow_factor * old and ms - old > 20:
                out.append(Finding("WARN", "timing", "%s mean %.0f -> %.0f ms" % (o, old, ms)))
    return out


def markdown(findings: list[Finding], base: dict, now: dict) -> str:
    fails = [f for f in findings if f.level == "FAIL"]
    head = "## x3d.lab regression gate: %s\n\n" % ("❌ %d regression(s)" % len(fails) if fails else "✅ no regressions")
    pinline = " · ".join("%s `%s`" % (k, v) for k, v in (now.get("pins") or {}).items())
    lines = [head, "pins: %s  \nbaseline from %s\n" % (pinline, base.get("created", "?"))]
    conf = (now.get("fuzz") or {}).get("confusion") or {}
    if conf:
        lines.append("| oracle | recall | specificity | crashes |\n|---|---|---|---|")
        for o, c in conf.items():
            oc = ((base.get("fuzz") or {}).get("confusion") or {}).get(o) or {}
            lines.append("| %s | %s (was %s) | %s (was %s) | %s |" % (o, c.get("recall"), oc.get("recall"), c.get("specificity"),
                                                                     oc.get("specificity"), c.get("crash") or 0))
        lines.append("")
    for lvl in ("FAIL", "WARN", "INFO"):
        sel = [f for f in findings if f.level == lvl]
        if sel:
            lines.append("### %s (%d)" % (lvl, len(sel)))
            lines += ["- **%s** %s%s" % (f.area, f.what, (" — `%s`" % f.detail) if f.detail else "") for f in sel[:80]]
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- running it
def collect(lab, oracles: list[str] | None = None, seed: int | None = None):
    from . import stress
    cfg = stress.RunConfig.preset_for("ci", gpu_kind="CPU", seed=seed if seed is not None else 2026)
    if oracles:
        cfg.tiers["matrix"]["oracles"] = list(oracles)
        cfg.tiers["fuzz"]["oracles"] = list(oracles)
    run = stress.run(cfg, lab)
    return run, snapshot(run)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m x3dlab ci", description="x3d.lab regression gate")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--update", action="store_true", help="write the current snapshot as the new baseline")
    ap.add_argument("--oracles", default="fork,lxml-xsd,consortium")
    ap.add_argument("--timing", action="store_true", help="also warn about timing regressions")
    ap.add_argument("--tolerance", type=float, default=TOLERANCE)
    ap.add_argument("--out", default=None, help="also write the snapshot and the report here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    import x3dlab
    from .events import bus
    oracles = [o.strip() for o in a.oracles.split(",") if o.strip()]
    lab = x3dlab.up(tunnel="none", panel=False, log_level="WARN", log_to_screen=not a.quiet)
    try:
        if "consortium" in oracles:
            lab.start_upstream()
        run, now = collect(lab, oracles)
    finally:
        lab.down()
    bus.configure(to_screen=False)
    base_path = Path(a.baseline)
    if a.update or not base_path.exists():
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(json.dumps(now, indent=1, sort_keys=True), encoding="utf-8")
        print("baseline written: %s (%d matrix cases, %d fuzz cases)" % (base_path, len((now.get("matrix") or {}).get("verdicts", {})),
                                                                        (now.get("fuzz") or {}).get("cases", 0)))
        return 0
    base = json.loads(base_path.read_text(encoding="utf-8"))
    findings = compare(base, now, a.tolerance, a.timing)
    md = markdown(findings, base, now)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "snapshot.json").write_text(json.dumps(now, indent=1, sort_keys=True), encoding="utf-8")
        (out / "report.md").write_text(md, encoding="utf-8")
        (out / "findings.json").write_text(json.dumps([asdict(f) for f in findings], indent=1), encoding="utf-8")
    return 1 if any(f.level == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
