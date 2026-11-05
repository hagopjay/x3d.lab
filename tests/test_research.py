"""Research tiers and the regression gate. Pure helpers run anywhere; matrix/fuzz tiers use the live server."""

import io
import json
import os

import pytest

from x3dlab import browser, regress, research, stress


# ---------------------------------------------------------------------- browser helpers (pure)
def _png(rgba_rows, mode="RGBA"):
    import numpy as np
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.array(rgba_rows, dtype=np.uint8), mode).save(buf, format="PNG")
    return buf.getvalue()


def test_silhouettes_iou_and_overlay():
    import numpy as np
    mag, red = [255, 0, 255], [200, 30, 30]
    xite = _png([[mag, red, red, mag], [mag, red, red, mag]], "RGB")
    cyc = _png([[[0, 0, 0, 0], [9, 9, 9, 255], [9, 9, 9, 255], [9, 9, 9, 255]]] * 2)
    a, b = browser.silhouette(xite, "key"), browser.silhouette(cyc, "alpha")
    assert a.sum() == 4 and b.sum() == 6
    assert browser.iou(a, b) == pytest.approx(4 / 6)
    assert browser.iou(np.zeros((2, 2), bool), np.zeros((2, 2), bool)) == 1.0
    assert browser.overlay_png(a, b).startswith(b"\x89PNG")


def test_flat_background_replaces_every_background():
    x = '<X3D><Scene><Background skyColor="0 0 1"/><TextureBackground><X/></TextureBackground><Shape/></Scene></X3D>'
    y = browser.flat_background(x, "1 0 1")
    assert y.count("Background") == 1 and '<Background skyColor="1 0 1"/>' in y and "<Shape/>" in y


def test_copilot_results_are_normalised():
    assert browser.normalise_copilot(True)["ok"]
    r = browser.normalise_copilot({"issues": [{"severity": "error", "message": "bad"}, {"severity": "warning", "message": "meh"}]})
    assert not r["ok"] and r["errors"] == ["bad"]
    assert browser.normalise_copilot({"valid": True, "errors": []})["ok"]


def test_trace_summary_reads_user_timings():
    ev = {"traceEvents": [{"cat": "blink.user_timing", "ph": "b", "name": "parse"}, {"name": "RunTask", "dur": 80000},
                          {"name": "RunTask", "dur": 1000}]}
    s = browser.trace_summary(json.dumps(ev).encode())
    assert s["measures"] == ["parse"] and s["tasks_over_50ms"] == 1 and s["long_tasks_ms"][0] == 80.0


def test_research_presets_and_scope():
    assert {"fuzz", "matrix", "crossrender", "scale", "traces"} <= set(stress.PRESETS["research"]["tiers"])
    assert stress.PRESETS["ci"]["tiers"]["fuzz"]["seed"] == 2026
    assert research.in_scope("lxml-xsd", "xsd") and not research.in_scope("lxml-xsd", "semantic")
    assert research.in_scope("fork", "semantic")
    cases = research.corpus(size=2)
    assert len(cases) == 17 + 6
    assert {c.expect for c in cases if c.id.startswith("family:")} == {"accept"}
    assert dict((c.id, c.expect) for c in cases)["defect:invisible"] == "accept"


# ---------------------------------------------------------------------- the regression gate (pure)
def _snap(**kw):
    s = {"pins": {"x3d_mcp": "a"}, "seed": 2026, "validate": {"primitives × 4": "PASS"},
         "matrix": {"oracles": ["fork"], "expect": {"defect:x": "reject", "family:y": "accept"},
                    "verdicts": {"defect:x": {"fork": "reject"}, "family:y": {"fork": "accept"}}},
         "fuzz": {"oracles": ["fork"], "cases": 2, "confusion": {"fork": {"recall": 1.0, "specificity": 1.0, "crash": 0}},
                  "buckets": [], "expect": {"c1": "reject", "c2": "accept"},
                  "verdicts": {"c1": {"fork": "reject"}, "c2": {"fork": "accept"}}}, "timing": {"validate_ms_median": 40}}
    for k, v in kw.items():
        s[k] = v
    return s


def test_gate_is_quiet_when_nothing_changed():
    assert [f for f in regress.compare(_snap(), _snap()) if f.level != "INFO"] == []


def test_gate_fails_on_worse_verdicts_new_buckets_and_lower_recall():
    now = _snap()
    now["matrix"] = json.loads(json.dumps(now["matrix"]))
    now["matrix"]["verdicts"]["defect:x"]["fork"] = "accept"            # a defect now slips through
    now["fuzz"] = json.loads(json.dumps(now["fuzz"]))
    now["fuzz"]["verdicts"]["c2"]["fork"] = "reject"                   # a clean case now rejected
    now["fuzz"]["buckets"] = ["false_alarm|fork|clean|xsd:x"]
    now["fuzz"]["confusion"]["fork"]["specificity"] = 0.5
    now["validate"] = {"primitives × 4": "FAIL"}
    fails = [f for f in regress.compare(_snap(), now) if f.level == "FAIL"]
    areas = sorted({f.area for f in fails})
    assert areas == ["fuzz", "matrix", "validate"] and len(fails) == 5
    md = regress.markdown(regress.compare(_snap(), now), _snap(), now)
    assert "regression" in md and "verdict got worse" in md


def test_gate_reports_improvements_as_info():
    base = _snap()
    base["matrix"] = json.loads(json.dumps(base["matrix"]))
    base["matrix"]["verdicts"]["defect:x"]["fork"] = "accept"           # used to miss it
    base["fuzz"]["buckets"] = ["missed|fork|x|y"]
    found = regress.compare(base, _snap())
    assert all(f.level == "INFO" for f in found) and any("bucket gone" in f.what for f in found)


def test_gate_refuses_a_different_seed_and_flags_timing_only_when_asked():
    assert regress.compare(_snap(), _snap(seed=1))[0].level == "FAIL"
    slow = _snap(timing={"validate_ms_median": 400})
    assert not [f for f in regress.compare(_snap(), slow) if f.level == "WARN"]
    assert [f for f in regress.compare(_snap(), slow, timing=True) if f.level == "WARN"]


def test_committed_baseline_is_well_formed():
    p = regress.DEFAULT_BASELINE
    if not p.exists():
        pytest.skip("no committed baseline")
    b = json.loads(p.read_text())
    assert b["seed"] == 2026 and b["fuzz"]["cases"] == 80 and len(b["matrix"]["verdicts"]) == 23
    assert set(b["fuzz"]["verdicts"]) == set(b["fuzz"]["expect"])


# ---------------------------------------------------------------------- with the live server
def test_matrix_tier_with_fork_and_xsd(lab):
    cfg = stress.RunConfig.preset_for("ci", gpu_kind="CPU", seed=2026)
    cfg.tiers = {"matrix": {"on": True, "oracles": ["fork", "lxml-xsd"], "families": ["primitives", "lights"], "size": 2}}
    run = stress.run(cfg, lab)
    rows = {r.case: r for r in run.rows if r.tier == "matrix"}
    assert rows["defect:python_bool"].status == "PASS"
    assert rows["defect:missing_def"].status == "PASS"          # the XSD engine is not blamed for a semantic defect
    assert rows["family:primitives"].status == "PASS"
    m = run.research["matrix"]
    assert m["cells"]["defect:unclosed_tag"]["lxml-xsd"]["v"] == "reject"
    card = json.loads(run.card_path.read_text())
    assert "matrix" in card["research"]


def test_browser_harness_loads_and_traces(tmp_path):
    d = os.environ.get("X3DLAB_XITE_DIR")
    if not d or browser.chromium_path() is None:
        pytest.skip("needs X3DLAB_XITE_DIR and a Chromium for Playwright")
    from x3dlab.scenes import LANTERN
    hb = browser.start(install=False)
    try:
        r = hb.load(LANTERN, screenshot=True, background="1 0 1")
        assert r.ok and r.png and r.t["parse"] > 0 and r.version == "4.0"
        assert 0.1 < browser.silhouette(r.png).mean() < 0.6
        bad = hb.load("<X3D><Scene><Shape><Box/></Shape></Scene")
        assert not bad.ok and bad.errors
        with hb.trace(tmp_path / "t.json", screenshots=False) as box:
            hb.load(LANTERN)
        assert {"parse", "replaceWorld", "first frames"} <= set(box["measures"])
        assert hb.metrics()["JSHeapUsedSize"] > 0
    finally:
        hb.close()
