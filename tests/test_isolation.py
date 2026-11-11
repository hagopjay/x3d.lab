"""Isolation: fetching x3d.loom by any ref, and telling a bridge defect (fails alone) from a leak (passes alone)."""

import inspect
import json
import os
import subprocess

import pytest

from x3dlab import formats, foundry, hog, pins


# ---------------------------------------------------------------------- the pin and the checkout
def test_loom_pin_is_a_full_hash():
    assert len(pins.get("x3d_loom", "ref")) == 40 and hog.LOOM_REF == pins.get("x3d_loom", "ref")
    assert hog.LOOM_REPO.endswith("/hagopjay/x3d.loom") and hog.BABYLON_VERSION and hog.NODE_VERSION


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def origin(tmp_path):
    """A local 'x3d.loom' with two commits; like GitHub, it serves any commit by its full hash."""
    o = tmp_path / "origin"
    (o / "x3d.webgpu" / "bridge" / "fixtures").mkdir(parents=True)
    _git(o, "init", "-q", "-b", "main")
    _git(o, "config", "uploadpack.allowAnySHA1InWant", "true")
    for i in (1, 2):
        (o / "x3d.webgpu" / "bridge" / "fixtures" / ("F%d.x3d" % i)).write_text("<X3D/>")
        _git(o, "add", "-A")
        _git(o, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c%d" % i)
    _git(o, "tag", "v1", "HEAD~1")
    return o


def test_checkout_by_short_hash_branch_tag(origin, monkeypatch, tmp_path):
    monkeypatch.setenv("X3DLAB_HOME", str(tmp_path / "home"))
    first, head = _git(origin, "rev-parse", "HEAD~1"), _git(origin, "rev-parse", "HEAD")
    url = "file://%s" % origin
    for ref, want in ((first[:7], first), ("main", head), ("v1", first), (head, head)):
        p = hog.loom_checkout(ref, repo=url)
        assert _git(p, "rev-parse", "HEAD") == want, ref
    with pytest.raises(RuntimeError, match="no commit, branch or tag"):
        hog.loom_checkout("deadbee", repo=url)


# ---------------------------------------------------------------------- hog: rerun failures alone
@pytest.fixture()
def loom(tmp_path):
    fx = tmp_path / "x3d.webgpu" / "bridge" / "fixtures"
    fx.mkdir(parents=True)
    for n, mode in (("A", "scene"), ("E", "events"), ("B", "scene"), ("C", "scene")):
        (fx / (n + ".test.json")).write_text(json.dumps({"node": n, "mode": mode, "steps": [{"settle": 1}]}))
    return tmp_path


def fake_runner(calls):
    """B is right alone and wrong after A (a leak); C is always wrong (a defect); the rest are right."""
    def run(loom_dir, names):
        calls.append(list(names))
        res = {}
        for i, n in enumerate(names):
            bad = n == "C" or (n == "B" and "A" in names[:i])
            res[n] = {"ok": not bad, "ours": "FAIL step 2" if bad else "pass 1/1", "xite": "pass 1/1", "oursOk": not bad, "xiteOk": True}
        return {"ok": not any(not r["ok"] for r in res.values()), "results": res}
    return run


def test_isolate_failures_tells_leaks_from_defects(loom):
    calls = []
    names = ["A", "E", "B", "C"]
    batch = fake_runner(calls)(loom, names)["results"]
    out = hog.isolate_failures(loom, names, batch, runner=fake_runner(calls))
    assert out == {"defects": ["C"], "leaks": {"B": "A"}}
    assert batch["B"]["verdict"] == "leak" and batch["B"]["culprit"] == "A" and batch["B"]["alone"]["ok"]
    assert batch["C"]["verdict"] == "defect" and not batch["C"]["alone"]["ok"]
    assert ["B"] in calls and ["A", "B"] in calls and ["C"] in calls      # E (events) is never the culprit of a scene
    run = hog.HogRun(hog.HogConfig(), "r", "t", loom)
    run.loom = {"results": batch}
    titles = {f.title: f for f in hog.findings(run)}
    assert "culprit: A" in titles["fails only after other fixtures in the same page, passes alone: a teardown leak"].evidence
    assert "bridge (Babylon reader) fails its fixture, alone too" in titles


# ---------------------------------------------------------------------- Foundry: bridge claims checked alone
BOX = '<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Immersive" version="4.0"><head/><Scene><Shape><Box/></Shape></Scene></X3D>'


class FakeBench:
    """s2 comes out dark only when s1 was drawn before it; s3 is always dark."""
    def __init__(self):
        self.calls = []

    def loom(self, xmls):
        self.calls.append(list(xmls))
        names = list(xmls)
        out = {}
        for i, n in enumerate(names):
            dark = n.startswith("s3") or (n.startswith("s2") and any(m.startswith("s1") for m in names[:i]))
            out[n] = {"error": "", "compare": {"match": 1.0, "iou": 1.0, "cov_original": 0.05,
                                               "colour_delta": 150.0 if dark else 10.0, "luma_ratio": 0.05 if dark else 1.0}}
        return out


def test_isolate_claims(tmp_path):
    r = foundry.Round(foundry.FoundryConfig(), "rid", 1, "t", tmp_path)
    for sid in ("s1.m", "s2.m", "s3.m"):
        s = foundry.Scene(sid, sid[:2].upper(), "4.0", "m", "", BOX, BOX, "draft", True, ["Box", "Shape"])
        s.engines = {"loom": {"compare": {}}}
        r.scenes.append(s)
    c2 = foundry.Claim("loom-colour", "loom", "s2.m", "S2", "4.0", "loom-colour|darker", "the bridge draws the right shapes in the wrong colours")
    c3 = foundry.Claim("loom-colour", "loom", "s3.m", "S3", "4.0", "loom-colour|darker", "the bridge draws the right shapes in the wrong colours")
    r.claims = [c2, c3]
    b = FakeBench()
    foundry.isolate_claims(r, b)
    assert c2.kind == "loom-leak" and c2.evidence["culprit"] == "s1.m" and c2.signature == "loom-leak|S2 after S1"
    assert "only after other scenes" in c2.summary and c2.evidence["was"] == "loom-colour"
    assert c3.kind == "loom-colour" and "a defect" in c3.evidence["alone"]
    assert ["s2.m", "s1.m"] not in b.calls and ["s1.m", "s2.m"] in b.calls                # the culprit runs first
    md = foundry.issue_markdown(r, c2)
    assert md.startswith("# The bridge leaks state from one world into the next (S2 after S1)")
    assert "Run this scene first" in md


# ---------------------------------------------------------------------- formats: the API other sessions build on
STABLE = {
    "match": ["a", "b", "r"],
    "compare_pngs": ["original", "converted"],
    "colour_kind": ["cmp_", "delta", "against"],
    "validate_gltf": ["paths", "timeout"],
    "error_codes": ["report"],
    "losses": ["types_before", "convert"],
    "inline_wrapper": ["xml", "url", "version"],
    "has_usd": ["blender"],
}


@pytest.mark.parametrize("name", sorted(STABLE))
def test_formats_stable_api(name):
    fn = getattr(formats, name)
    assert list(inspect.signature(fn).parameters)[:len(STABLE[name])] == STABLE[name]


def test_colour_thresholds_by_comparison():
    assert formats.COLOUR_DELTA["x_ite_vs_gltf_in_x_ite"] == 64 and "bridge_vs_x_ite" in formats.COLOUR_DELTA
    cmp_ = {"colour_delta": 70.0, "luma_ratio": 0.3}
    assert formats.colour_kind(cmp_) == "darker"
    assert formats.colour_kind(cmp_, delta=80) == ""
    formats.COLOUR_DELTA["unreal_vs_x_ite"] = 90.0                      # how a new renderer pair joins
    try:
        assert formats.colour_kind(cmp_, against="unreal_vs_x_ite") == ""
    finally:
        del formats.COLOUR_DELTA["unreal_vs_x_ite"]
    h = formats.has_usd()
    assert set(h) == {"usd_core", "version", "checker", "blender_usd"} and h["blender_usd"] is None


def test_toolchain_report_without_a_lab():
    from x3dlab import toolchain
    rep = toolchain.report()
    assert rep["x3d_loom"]["version"] == pins.get("x3d_loom", "ref")[:12] and rep["python"]["version"]
    assert "not set up" in rep["blender"]["source"] and "Python" in toolchain.line(rep)
    assert os.path.basename(rep["python"]["source"]).startswith("python")
