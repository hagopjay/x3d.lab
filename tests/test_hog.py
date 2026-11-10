"""The hog run: corpus building, the 4.1 scenes, finding rules, reports, and a small real run (server + X_ITE)."""

import json
import os
from pathlib import Path

import pytest

from x3dlab import hog

BOX = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Interchange" version="4.0" xmlns:xsd="http://www.w3.org/2001/XMLSchema-instance" xsd:noNamespaceSchemaLocation="https://www.web3d.org/specifications/x3d-4.0.xsd">
  <Scene><Viewpoint position="0 0 6"/><Shape><Appearance><Material diffuseColor="1 0 0"/></Appearance><Box/></Shape></Scene>
</X3D>"""
STRIP = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Interchange" version="4.0" xmlns:xsd="http://www.w3.org/2001/XMLSchema-instance" xsd:noNamespaceSchemaLocation="https://www.web3d.org/specifications/x3d-4.0.xsd">
  <Scene><Viewpoint position="0 0 6"/>
    <Shape><Appearance><Material diffuseColor="0 1 0"/></Appearance>
      <IndexedTriangleStripSet DEF="G" index="0 1 2 3 -1 4 5 6 7"><Coordinate point="0 0 0 1 0 0 0 1 0 1 1 0 2 0 0 3 0 0 2 1 0 3 1 0"/></IndexedTriangleStripSet>
    </Shape></Scene>
</X3D>"""


@pytest.fixture()
def fake_loom(tmp_path):
    fx = tmp_path / "x3d.webgpu" / "bridge" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "Box.x3d").write_text(BOX)
    (fx / "IndexedTriangleStripSet.x3d").write_text(STRIP)
    (fx / "assets").mkdir()
    return tmp_path


def test_to_version():
    out = hog.to_version(BOX, "4.1")
    assert 'version="4.1"' in out and "x3d-4.1.xsd" in out and 'version="1.0"' in out     # the XML declaration is untouched
    assert hog.to_version(out, "4.0") == BOX


def test_corpus_versions_and_41_scenes(fake_loom):
    items = hog.corpus(hog.HogConfig(include_lab_scenes=False), fake_loom)
    ids = {i.id for i in items}
    assert {"Box@4.0", "Box@4.1", "IndexedTriangleStripSet@4.0", "IndexedTriangleStripSet@4.1"} <= ids
    s41 = [i for i in items if i.source == "spec41"]
    assert sorted(i.node for i in s41) == sorted(hog.SPEC41) and all(i.expect == "valid" and i.version == "4.1" for i in s41)
    box = next(i for i in items if i.id == "Box@4.0")
    assert box.component == "Geometry3D" and box.visual
    only40 = hog.corpus(hog.HogConfig(include_lab_scenes=False, versions=["4.0"]), fake_loom)
    assert all(i.version == "4.0" for i in only40) and not [i for i in only40 if i.source == "spec41"]


def test_graphics_stack_shape():
    g = hog.graphics_stack()
    assert set(g) >= {"gpu", "libs", "vulkan_icds", "nvoptix_bin", "verdicts"}
    assert set(g["libs"]) == set(hog.NVIDIA_LIBS)


def _run(tmp_path, items, matrix, **extra):
    r = hog.HogRun(hog.HogConfig(), "t", "t", tmp_path)
    r.items, r.matrix = items, matrix
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def test_finding_rules(tmp_path):
    it = lambda n, v="4.0", src="loom", comp="Geometry3D", expect="": hog.Item("%s@%s" % (n, v), n, v, src, "", comp, True, "", expect)  # noqa: E731
    items = [it("Strip"), it("Prof"), it("ImportFails"), it("BadExport"), it("Inline", "4.1", "spec41", "Networking", "valid"),
             it("Schema")]
    ok_x = {"ok": True, "errors": [], "coverage": 0.2}
    m = {
        "Strip@4.0": {"fork": {"ok": False, "xsd_valid": True, "kinds": ["index-out-of-range"],
                               "messages": {"index-out-of-range": "IndexedTriangleStripSet (DEF='G') index uses -1 but the coordinate node has 8 point(s)"}},
                      "xsd": {"ok": True}, "x_ite": ok_x},
        "Prof@4.0": {"fork": {"ok": False, "xsd_valid": True, "kinds": ["profile-component"],
                              "messages": {"profile-component": "Arc2D ... Browsers that enforce the header (X_ITE, x3d-tidy) refuse to load it."}},
                     "xsd": {"ok": True}, "x_ite": ok_x},
        "ImportFails@4.0": {"fork": {"ok": True, "xsd_valid": True, "kinds": []}, "xsd": {"ok": True}, "x_ite": ok_x,
                            "blender_import": {"ok": False, "error": "import returned ['CANCELLED']"}},
        "BadExport@4.0": {"fork": {"ok": True, "xsd_valid": True, "kinds": []}, "xsd": {"ok": True}, "x_ite": ok_x,
                          "blender_import": {"ok": True, "counts": {"objects": 1, "meshes": 1}},
                          "blender_rt_X3D41": {"ok": True, "valid": False, "kinds": ["unknown-field"], "first": "x not allowed"},
                          "cycles": {"ok": True, "covered": 0.0}},
        "Inline@4.1": {"fork": {"ok": False, "xsd_valid": False, "kinds": ["node-not-allowed"], "first": "not expected"},
                       "xsd": {"ok": False}, "x_ite": ok_x},
        "Schema@4.0": {"fork": {"ok": False, "xsd_valid": False, "kinds": ["value-format"], "first": "pattern"}, "xsd": {"ok": True}},
    }
    loom = {"results": {"Arc2D": {"ok": False, "oursOk": True, "xiteOk": False, "msg": "X_ITE disagrees"},
                        "Box": {"ok": True}, "Sound": {"ok": False, "oursOk": False, "ours": "fail 1/3"}},
            "nodes_status": {"bridged": 24, "gap": 76}}
    fs = hog.findings(_run(tmp_path, items, m, loom=loom))
    titles = {(f.target, f.title): f for f in fs}
    get = lambda target, words: next(f for (t, ti), f in titles.items() if t == target and words in ti)  # noqa: E731
    assert get("x3d_mcp", "-1 fan/strip separator").nodes == ["Strip@4.0"]
    assert get("x3d_mcp", "X_ITE loaded them").nodes == ["Prof@4.0"]
    assert get("loom", "profile-component").nodes == ["Prof@4.0"]
    assert not [f for f in fs if f.target == "loom" and "index-out-of-range" in f.title]          # the false positive is not loom's
    assert get("blender_io", "import fails").nodes == ["ImportFails@4.0"]
    assert get("blender_io", "export to X3D41 is not valid").nodes == ["BadExport@4.0"]
    assert get("blender_io", "Cycles shows nothing").nodes == ["BadExport@4.0"]
    assert get("x3d_mcp", "follows the 4.1 object model").nodes == ["Inline@4.1"]
    assert get("x3d_mcp", "schema layer disagrees").nodes == ["Schema@4.0"]
    assert get("loom", "X_ITE disagrees").nodes == ["Arc2D"] and get("loom", "fails its fixture").nodes == ["Sound"]
    assert get("loom", "coverage of the 260").severity == "info"
    r = _run(tmp_path, items, m, loom=loom, findings=fs)
    md = hog.issue_markdown(r, "blender_io")
    assert md.startswith("# x3d-blender-io") and "export to X3D41" in md and "scenes/BadExport@4.0.x3d" in md
    hog.write(r)
    assert json.loads((tmp_path / "findings.json").read_text()) and (tmp_path / "issues" / "x3d_mcp.md").exists()
    assert "Matrix (6 scenes)" in (tmp_path / "report.html").read_text()


def test_small_real_run(lab, fake_loom, monkeypatch):
    monkeypatch.setattr(hog, "chrome_renderers", lambda: {"swiftshader": {"webgl": "test"}})
    cfg = hog.HogConfig(stages=["probe", "corpus", "matrix", "findings"], loom_dir=str(fake_loom), include_lab_scenes=False,
                        blender=False, chrome_gpu=False, name="t-hog")
    have_browser = bool(os.environ.get("X3DLAB_XITE_DIR") or Path(os.path.expanduser("~/.cache/ms-playwright")).exists()
                        or Path("/opt/pw-browsers").exists())
    if not have_browser:
        cfg.stages.remove("matrix")
    r = lab.hog(cfg)
    assert not r.errors, r.errors
    assert {"Box@4.0", "Box@4.1", "EnvironmentLight@4.1"} <= {i.id for i in r.items}
    if have_browser:
        assert r.matrix["Box@4.0"]["fork"]["ok"] and r.matrix["Box@4.1"]["xsd"]["ok"]
        assert r.matrix["Box@4.0"]["x_ite"]["ok"] and r.matrix["Box@4.0"]["x_ite"]["coverage"] > 0.01
        f = [f for f in r.findings if "separator" in f.title]
        assert f and set(f[0].nodes) == {"IndexedTriangleStripSet@4.0", "IndexedTriangleStripSet@4.1"}
        inl = [f for f in r.findings if "object model" in f.title]
        assert inl and {"InlineGeometry@4.1", "RenderedTexture@4.1"} <= set(inl[0].nodes)
    assert (r.dir / "report.html").exists() and (r.dir / "hog_card.json").exists()


class FakeBlender:
    """Answers like Blender.run; the scene named Crash kills its whole process (as a segfault would)."""
    best_backend = "OPTIX"

    def __init__(self, out_dir):
        self.calls, self.out_dir = [], out_dir

    def run(self, tasks, timeout=0, job=None, label=""):
        self.calls.append(len(tasks))
        res, dead = [], False
        for t in tasks:
            dead = dead or "Crash" in t["src"]              # the process dies here; earlier tasks already finished
            if dead:
                res.append({"ok": False, "log": [], "result": {"error": "Blender exited with code -11 before this task finished"}})
            elif t["kind"] == "import":
                res.append({"ok": True, "log": [], "result": {"counts": {"objects": 1, "meshes": 1}}})
            elif t["kind"] == "roundtrip":
                out = t["out_prefix"] + ".gen1.%s.x3d" % t["version"].lower()
                Path(out).write_text(BOX if t["version"] == "X3D40" else BOX.replace("<Box/>", "<Box sizee='1 1 1'/>"))
                res.append({"ok": True, "log": [], "result": {"generations": [{"output": out, "bytes": 300}]}})
            else:
                res.append({"ok": True, "log": [], "result": {"pixels": {"covered": 0.12}, "seconds": 0.4, "backend": "OPTIX"}})
        return res


def test_blender_stage_plumbing(lab, tmp_path):
    it = lambda n: hog.Item("%s@4.0" % n, n, "4.0", "loom", BOX, "Geometry3D", True)  # noqa: E731
    items = [it("Box"), it("Crash"), it("Box2")]
    r = _run(tmp_path, items, {})
    r.cfg.blender_chunk = 20                        # all tasks in one chunk: the crash takes the chunk down
    lab_like = type("L", (), {"blender": FakeBlender(tmp_path), "client": lab.client})()
    hog._blender_all(lab_like, items, r.cfg, r)
    assert r.matrix["Box@4.0"]["blender_import"]["ok"] and r.matrix["Box2@4.0"]["cycles"]["covered"] == 0.12
    assert not r.matrix["Crash@4.0"]["blender_import"]["ok"] and "exited" in r.matrix["Crash@4.0"]["blender_import"]["error"]
    assert lab_like.blender.calls == [12] + [1] * 8     # one chunk; then everything it did not finish, one by one
    assert r.matrix["Box@4.0"]["blender_rt_X3D41"]["types"] == ["Appearance", "Box", "Material", "Shape", "Viewpoint"]
    from x3dlab import aio
    aio.run(hog._validate_exports(lab_like, r, r.cfg))
    assert r.matrix["Box@4.0"]["blender_rt_X3D40"]["valid"] is True
    e41 = r.matrix["Box@4.0"]["blender_rt_X3D41"]
    assert e41["valid"] is False and "unknown-field" in e41["kinds"]
    for i in items:                                   # the source scenes themselves are valid
        r.matrix[i.id]["fork"] = {"ok": True, "xsd_valid": True, "kinds": []}
    titles = [f.title for f in hog.findings(r)]
    assert "export to X3D41 is not valid X3D" in titles and "import fails on a valid scene" in titles
