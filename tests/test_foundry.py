"""The Foundry: targeting, prompts, the offline contender, claims, artifacts, rules, state; formats (glTF engine) helpers.
The live parts (server, X_ITE, the bridge runner, Blender) are exercised by test_small_live_round when they are here."""

import io
import json
import os
import random
from pathlib import Path

import pytest

from x3dlab import duel, formats, foundry

BOX = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0"><head/><Scene>
  <NavigationInfo headlight="false"/>
  <Transform translation="0 1 0" rotation="0 1 0 0.5"><Viewpoint DEF="V" position="0 0 8" description="front"/></Transform>
  <Shape><Appearance><Material diffuseColor="1 0 0"/></Appearance><Box/></Shape>
</Scene></X3D>"""


# ---------------------------------------------------------------------- targets and prompts
def test_targets_spread_and_versions():
    cfg = foundry.FoundryConfig(targets=10)
    t = foundry.choose_targets(cfg, foundry.load_state(Path("/nonexistent")), {"Extrusion": "todo"}, random.Random(1))
    assert len(t) == 10
    per = {}
    for r in t:
        per[r["component"]] = per.get(r["component"], 0) + 1
        assert r["version"] == ("4.1" if foundry.node_catalogue()[r["node"]]["v41"] else "4.0")
        assert r["node"] not in foundry.SKIP_NODES
    assert max(per.values()) <= 2                                  # a round spreads over components
    assert all("never tried" in r["why"] for r in t)


def test_targets_learn_from_state():
    st = foundry.load_state(Path("/nonexistent"))
    cat = foundry.node_catalogue()
    for n in cat:                                                    # everything tried a lot, all valid, no trouble ...
        st["nodes"][n] = {"tried": 6, "valid": 6}
    st["nodes"]["Extrusion"] = {"tried": 6, "valid": 1, "disagreements_last": 2}    # ... except one node
    t = foundry.choose_targets(foundry.FoundryConfig(targets=3), st, {}, random.Random(2))
    assert t[0]["node"] == "Extrusion" and "models fail it" in t[0]["why"] and "disagreement" in t[0]["why"]


def test_pinned_nodes_and_prompt():
    cfg = foundry.FoundryConfig(nodes=["Tangent", "TriangleFanSet", "CADFace"])
    t = foundry.choose_targets(cfg, foundry.load_state(Path("/x")), {}, random.Random(0))
    assert [r["node"] for r in t] == ["Tangent", "TriangleFanSet", "CADFace"] and t[0]["version"] == "4.1"
    p = [foundry.prompt_for(r, random.Random(0)) for r in t]
    assert p[0]["focus"] == "Tangent" and p[0]["version"] == "4.1" and "X3D 4.1" in p[0]["text"]
    assert "geometry of a Shape" in p[1]["text"]
    assert '<component name="CADGeometry"' in p[2]["text"]           # not in Immersive: the prompt names the component


def test_corpus_contender_defects_then_repairs():
    foundry._CORPUS[("Box", "4.0")] = BOX
    cfg = duel.DuelConfig(contenders=["corpus"], version="4.0")
    convs = [foundry._corpus_factory(cfg, i, {"focus": "Box"}) for i in range(3)]
    first = [c.send("x").text for c in convs]
    assert first[0] == BOX and 'headlight="False"' in first[1] and "diffuseColour" in first[2]
    assert convs[1].send("repair").text == BOX
    assert "no scene" in foundry._corpus_factory(cfg, 0, {"focus": "Nope"}).send("x").text
    c, skipped = duel.resolve(duel.DuelConfig(contenders=["corpus", "sample"]))
    assert [x.provider for x in c] == ["corpus", "sample"] and not skipped
    assert duel.estimate(duel.DuelConfig(contenders=["corpus"], prompts=3))["requests_worst"] == 0


def test_rules_reach_the_system_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(duel.llm, "conversation", lambda p, k, m, system: seen.setdefault("system", system))
    c = duel.Contender("nvidia:x/y", "nvidia", "x/y", "nvidia · x/y", "k")
    duel._conversation(c, duel.DuelConfig(rules={"nvidia · x/y": "- Booleans are lowercase."}), 0, {})
    assert "Lessons from earlier rounds" in seen["system"] and "Booleans are lowercase" in seen["system"]


# ---------------------------------------------------------------------- claims, rules, state
def _scene(sid, node="Box", **kw):
    s = foundry.Scene(sid, node, "4.0", kw.pop("contender", "m"), "make a %s" % node, kw.pop("xml", BOX), kw.pop("draft", BOX),
                      "draft", True, ["Box", "Shape"])
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _judgement(fork_ok=True, xsd_valid=True, xsd_ok=True, allows=None, kinds=(), xsd_errors=(), semantic=(), key=""):
    return {"fork": {"ok": fork_ok, "xsd_valid": xsd_valid, "kinds": list(kinds), "xsd_errors": list(xsd_errors),
                     "semantic": list(semantic), "messages": ["msg %s" % k for k in kinds]},
            "xsd": {"ok": xsd_ok, "key": key, "first": "Element 'X': This element is not expected." if not xsd_ok else ""},
            "uom": {"allows": allows, "why": "Group.children accepts X: yes" if allows else ""},
            "effective": bool(xsd_ok or allows)}


def _round(tmp_path, scenes, **cfg):
    r = foundry.Round(foundry.FoundryConfig(**cfg), "rid", 1, "t", tmp_path)
    r.scenes = scenes
    return r


def test_claims_from_every_engine(tmp_path):
    ok = _judgement()
    a = _scene("a", referee={"final": _judgement(False, False, True, xsd_errors=["Element N: bad"], kinds=["value-format"]),
                             "draft": _judgement(False, False, True, xsd_errors=["Element N: bad"], kinds=["value-format"])})
    b = _scene("b", referee={"final": _judgement(True, True, False, key="xsd:Element 'Y': nope"), "draft": ok})
    c = _scene("c", referee={"final": _judgement(False, False, False, allows=True, key="xsd:placed"), "draft": ok})
    d = _scene("d", referee={"final": ok, "draft": ok}, sound=True, engines={
        "x_ite": {"ok": True, "errors": []},
        "blender_import": {"ok": True, "unsupported": ["IndexedQuadSet", "TouchSensor"]},
        "blender_rt_X3D40": {"file": "x.x3d", "valid": False, "xsd": {"ok": False, "key": "xsd:k", "first": "bad export"}},
        "convert_glb": {"validator": {"errors": 2, "codes": ["ACCESSOR_MIN_MISMATCH"]}, "flat_meshes": ["Shape_Sphere", "Shape_Box"],
                        "compare": {"match": 0.1, "iou": 0.0, "cov_original": 0.05}, "losses": {"unexpected": []}},
        "loom": {"compare": {"match": 0.9, "iou": 0.8, "cov_original": 0.05, "colour_delta": 150, "luma_ratio": 0.1}}})
    e = _scene("e", "Text", referee={"final": ok, "draft": ok}, sound=True, types=["Text", "Shape"], engines={
        "x_ite": {"ok": True, "errors": []},
        "convert_glb": {"compare": {"match": 0.2, "iou": 0.1, "cov_original": 0.05}, "losses": {"unexpected": []}},
        "loom": {"compare": {"match": 0.3, "iou": 0.2, "cov_original": 0.05}}})
    f = _scene("f", referee={"final": ok, "draft": ok}, sound=True, engines={"x_ite": {"ok": False, "errors": ["boom 12"]},
                                                                              "loom": {"error": "ERROR x"}})
    g = _scene("g", "TwoSidedMaterial", referee={"final": ok, "draft": ok}, sound=True, engines={
        "x_ite": {"ok": True, "errors": []},
        "convert_glb": {"compare": {"match": 1.0, "iou": 1.0, "cov_original": 0.06, "colour_delta": 128, "luma_ratio": 1.15}}})
    h = _scene("h", "EnvironmentLight", referee={"final": ok, "draft": ok}, sound=True, types=["EnvironmentLight", "Shape"], engines={
        "x_ite": {"ok": True, "errors": []},
        "convert_glb": {"compare": {"match": 1.0, "iou": 1.0, "cov_original": 0.1, "colour_delta": 182, "luma_ratio": 0.12}}})
    r = _round(tmp_path, [a, b, c, d, e, f, g, h])
    claims = foundry.find_claims(r, {"Text": "todo"})
    gc = {c.scene: c for c in claims if c.kind == "gltf-colour"}
    assert gc["g"].signature == "gltf-colour|hue TwoSidedMaterial" and not gc["g"].expected
    assert "EnvironmentLight" in gc["h"].expected and "darker" in gc["h"].signature
    kinds = {(c.kind, c.scene) for c in claims}
    assert ("fork-rejects-valid", "a") in kinds and ("fork-accepts-invalid", "b") in kinds and ("xsd-stricter-than-uom", "c") in kinds
    assert {("blender-unsupported", "d"), ("blender-export-invalid", "d"), ("gltf-invalid", "d"), ("gltf-lost", "d"),
            ("loom-colour", "d"), ("blender-flat", "d")} <= kinds
    assert next(c for c in claims if c.kind == "blender-flat").signature == "blender-flat|Sphere"   # a flat Box is right
    assert not any(c.kind == "blender-unsupported" and "TouchSensor" in c.signature for c in claims)   # sensors are expected
    gl_d = next(c for c in claims if c.kind == "gltf-lost" and c.scene == "d")
    assert "skips IndexedQuadSet" in gl_d.expected                    # the importer's own skip explains the glTF loss
    gl_e = next(c for c in claims if c.kind == "gltf-lost" and c.scene == "e")
    lo_e = next(c for c in claims if c.kind == "loom-differs" and c.scene == "e")
    assert "Text" in gl_e.expected and "Text" in lo_e.expected
    assert ("xite-error", "f") in kinds and not any(c.scene == "f" and c.kind.startswith("loom") for c in claims)
    fr = next(c for c in claims if c.kind == "fork-rejects-valid")
    assert fr.which == "draft" and fr.also == []                      # draft and final of one scene: one claim, no self-echo


def test_claims_merge_across_scenes(tmp_path):
    j = _judgement(False, False, True, xsd_errors=["Element N: bad"], kinds=["value-format"])
    r = _round(tmp_path, [_scene("a", referee={"final": j, "draft": j}), _scene("b", referee={"final": j, "draft": j})])
    claims = foundry.find_claims(r, {})
    assert len(claims) == 1 and claims[0].also == ["b"]


def test_learn_rules_and_state(tmp_path):
    bad = _judgement(False, False, False, kinds=["boolean-case", "unknown-field"])
    r = _round(tmp_path, [_scene("a", contender="m1", referee={"final": _judgement(), "draft": bad}),
                          _scene("b", contender="m1", referee={"final": _judgement(), "draft": bad})])
    st = foundry.load_state(tmp_path / "state.json")
    rules = foundry.learn_rules(r, st)
    assert set(rules["m1"]) == {"boolean-case", "unknown-field"} and rules["m1"]["boolean-case"]["seen"] == 2
    assert "lowercase" in rules["m1"]["boolean-case"]["text"] and 'Last time: "msg boolean-case"' in rules["m1"]["boolean-case"]["text"]
    txt = foundry.rules_for_duel(rules)["m1"]
    assert txt.count("\n- ") == 1 and txt.startswith("- ")
    r.targets = [{"node": "Box"}]
    r.rules_next = rules
    r.scoreboard = foundry.scoreboard(r, st)
    assert r.scoreboard["contenders"]["m1"]["draft_valid"] == 0 and r.scoreboard["contenders"]["m1"]["final_valid"] == 2
    foundry.update_state(r, st)
    foundry.save_state(tmp_path / "state.json", st)
    st2 = foundry.load_state(tmp_path / "state.json")
    assert st2["nodes"]["Box"]["tried"] == 2 and st2["rules"]["m1"] and len(st2["rounds"]) == 1
    r2 = _round(tmp_path, [_scene("c", contender="m1", referee={"final": _judgement(), "draft": _judgement()})])
    r2.n = 2
    assert foundry.scoreboard(r2, st2)["contenders"]["m1"]["draft_rate_delta"] == 1.0      # 0 of 2 -> 1 of 1


# ---------------------------------------------------------------------- artifacts
def _png(draw):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 240), (255, 0, 255))
    draw(ImageDraw.Draw(im))
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def test_pick_points_flat_patches_only():
    png = _png(lambda d: d.rectangle([100, 60, 220, 180], fill=(200, 40, 40)))
    pts = foundry.pick_points(png)
    cov = [p for p in pts if p[3] == 48]
    bg = [p for p in pts if p[3] == 24]
    assert len(cov) == 10 and len(bg) == 4
    assert all(102 <= x <= 218 and 62 <= y <= 178 and c == [200, 40, 40] for x, y, c, _ in cov)   # inside, away from edges
    assert all(c == [255, 0, 255] for _, _, c, _ in bg)
    assert len({(x * 4 // 320, y * 3 // 240) for x, y, _, _ in bg}) == 4                          # spread over regions


def test_match_forgives_thin_shifts():
    import numpy as np
    a = np.zeros((60, 80), bool)
    a[30, 10:70] = True                      # a one-pixel line
    b = np.roll(a, 1, axis=0)                # the same line, one pixel lower
    assert formats.match(a, b) == 1.0 and float((a & b).sum()) == 0          # IoU would say 0
    c = np.zeros_like(a)
    c[10:20, 10:20] = True
    assert formats.match(a, c) < 0.1


def test_compare_and_colour_kind():
    one = _png(lambda d: d.rectangle([100, 60, 220, 180], fill=(60, 180, 230)))
    dark = _png(lambda d: d.rectangle([100, 60, 220, 180], fill=(0, 0, 0)))
    c = formats.compare_pngs(one, dark)
    assert c["match"] == 1.0 and c["colour_delta"] > 100 and formats.colour_kind(c) == "darker"
    assert formats.colour_kind(formats.compare_pngs(one, one)) == ""


def test_mcp_tests_text_compiles(tmp_path):
    s = _scene("a", draft=BOX.replace("1 0 0", "1 0 0 '''"))
    r = _round(tmp_path, [s])
    c1 = foundry.Claim("fork-rejects-valid", "x3d_mcp", "a", "Box", "4.0", "fork-rejects-valid|x", 'rejects "this"', "draft")
    c2 = foundry.Claim("fork-accepts-invalid", "x3d_mcp", "a", "Box", "4.0", "fork-accepts-invalid|y", "accepts", "final",
                       shrunk={"xml": "<X3D/>"})
    text = foundry.mcp_tests(r, [c1, c2])
    compile(text, "t.py", "exec")
    assert text.count("@pytest.mark.xfail(strict=True") == 2 and 'assert not r["valid"]' in text and "CASE_2 = r'''<X3D/>'''" in text
    assert foundry._test_name(r, 1, c1) in text


def test_issue_markdown(tmp_path):
    s = _scene("a", engines={"x_ite": {"ok": True, "errors": []}, "loom": {"ours": "pass 2/2", "compare": {"match": 0.4}}})
    r = _round(tmp_path, [s])
    c = foundry.Claim("loom-differs", "loom", "a", "Box", "4.0", "loom-differs|Box", "the bridge draws ...", "final",
                      shrunk={"xml": "<X3D small/>", "before": {"bytes": 900, "nodes": 9}, "after": {"bytes": 90, "nodes": 2}, "checks": 7})
    md = foundry.issue_markdown(r, c)
    assert md.startswith("# The bridge draws a different picture than X_ITE (Box)")
    assert "<X3D small/>" in md and "900 to 90 bytes" in md and "x3d.loom (Babylon bridge)" in md


# ---------------------------------------------------------------------- the glTF engine's helpers
def test_inline_wrapper_keeps_the_camera():
    w = formats.inline_wrapper(BOX, "scenes/a.glb")
    from lxml import etree
    root = etree.fromstring(w.encode())
    sc = root.find("Scene")
    assert sc.find("NavigationInfo").get("headlight") == "false"
    t = sc.find("Transform")
    assert t.get("translation") == "0 1 0" and t.get("rotation") == "0 1 0 0.5"
    assert t.find("Viewpoint").get("position") == "0 0 8" and t.find("Viewpoint").get("DEF") is None
    assert sc.find("Inline").get("url") == '"scenes/a.glb"'


def test_losses_expected_and_unexpected():
    conv = {"counts": {"objects": 4, "meshes": 3, "vertices": 100, "faces": 50, "lights": 2, "cameras": 1, "materials": 2},
            "shared_meshes": 1,
            "reimport": {"counts": {"objects": 4, "meshes": 3, "vertices": 300, "faces": 90, "lights": 1, "cameras": 1, "materials": 2},
                         "shared_meshes": 0}}
    lo = formats.losses(["TimeSensor", "Shape", "Text"], conv)
    assert lo["expected"] == ["Text", "TimeSensor"]
    assert lo["unexpected"] == ["lights 2 -> 1"]                      # more vertices and faces is normal (seams, triangles)
    assert "shared meshes (DEF/USE instancing) 1 -> 0" in lo["info"]


def test_validate_gltf_reports_errors(tmp_path):
    from x3dlab import hog
    try:
        hog._node_bin()
        formats.validator_dir()
    except Exception as exc:                                         # no Node / npm registry here
        pytest.skip("glTF validator unavailable: %s" % exc)
    good = tmp_path / "good.gltf"
    good.write_text(json.dumps({"asset": {"version": "2.0"}}))
    bad = tmp_path / "bad.gltf"
    bad.write_text(json.dumps({"asset": {"version": "2.0"}, "scene": 3}))
    rep = formats.validate_gltf([good, bad])
    assert rep[str(good)]["ok"] and not rep[str(bad)]["ok"]
    assert formats.error_codes(rep[str(bad)])


def test_estimate_counts_only_paid():
    e = foundry.estimate(contenders=["corpus", "nvidia:a/b", "nvidia:c/d"], targets=8, repair_rounds=1)
    assert e["requests_worst"] == 32 and e["within_budget"] is True
    assert foundry.estimate(contenders=["nvidia:a/b"], targets=50, max_requests=80)["within_budget"] is False


# ---------------------------------------------------------------------- live: server + X_ITE (+ bridge and Blender when present)
def test_small_live_round(lab, tmp_path):
    have_browser = bool(os.environ.get("X3DLAB_XITE_DIR") or Path(os.path.expanduser("~/.cache/ms-playwright")).exists()
                        or Path("/opt/pw-browsers").exists())
    loom = os.environ.get("X3DLAB_LOOM_DIR", "")
    engines = (["xite"] if have_browser else []) + (["loom"] if loom else [])
    foundry._CORPUS.clear()
    foundry._CORPUS[("TriangleFanSet", "4.0")] = BOX.replace("<Box/>", '<TriangleFanSet fanCount="3"><Coordinate point="0 0 0 1 0 0 0 1 0"/></TriangleFanSet>')
    foundry._CORPUS[("Box", "4.0")] = BOX
    r = foundry.run(lab, contenders=["corpus"], nodes=["TriangleFanSet", "Box"], engines=engines, loom_dir=loom or str(tmp_path / "noloom"),
                    chrome_gpu=False, state=str(tmp_path / "state.json"), max_shrinks=1, shrink_budget=10)
    assert not {k: v for k, v in r.errors.items() if k != "loom checkout"}, r.errors
    assert [s.node for s in r.scenes] == ["TriangleFanSet", "Box"]
    tfs = r.scene("TriangleFanSet.corpus")
    assert tfs.referee["final"]["effective"] and tfs.referee["draft"]["fork"]["ok"]           # index 0: no defect
    box = r.scene("Box.corpus")
    assert not box.referee["draft"]["fork"]["ok"] and box.referee["final"]["fork"]["ok"]     # defect, then the repair turn
    if have_browser:
        assert tfs.engines["x_ite"]["ok"] and (r.dir / tfs.engines["x_ite"]["png"]).exists()
    assert r.rules_next.get("corpus")                                                         # Box's draft taught a rule
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["nodes"]["Box"]["tried"] == 1 and st["rules"]["corpus"]
    assert (r.dir / "report.html").exists() and (r.dir / "foundry_card.json").exists()
