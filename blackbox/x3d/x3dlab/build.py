"""Build the X3D Lab: scenes in three encodings, validation, fidelity predictions, the optional Blender leg,
prediction-vs-measurement verdicts, and the dev console's index.json.

    python -m x3dlab build --out ../site/x3d/scenes                      # Python only
    python -m x3dlab build --out ../site/x3d/scenes --blender            # + X3D → Blender → glb
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .fidelity import LEGS, predict, summarise
from .scenes import SCENES
from .validate import parse, validate_text

HERE = Path(__file__).resolve().parent


# ── measurement: what the Blender leg shows for each predicted rule ──────────────────────────────────
def measure(rule_id: str, b: dict | None, info: dict) -> dict:
    """Return {leg: (status, evidence)} for the legs a Blender run can speak to (blender, gltf)."""
    if not b:
        return {}
    if not b.get("ok"):
        err = (b.get("error") or "").strip().splitlines()
        last = next((l for l in reversed(err) if "Error" in l), err[-1] if err else "import failed")
        return {"blender": ("lost", "import failed: " + last.strip()[:160]), "gltf": ("lost", "no glb (import failed)")}
    s, g = b["survey"], b["glb"]
    meshes = s["meshes"]
    mats = g["materials"]
    if rule_id == "shaders":
        return {"blender": ("lost", "%d Blender materials, none from GLSL" % len(s["materials"])),
                "gltf": ("lost", "glb materials are the fallback Materials (%d)" % len(mats))}
    if rule_id == "lines-points":
        lines = g["primitiveModes"].get("LINES", 0) + g["primitiveModes"].get("LINE_STRIP", 0)
        pts = g["primitiveModes"].get("POINTS", 0)
        col = g["primitivesWithColor0"]
        unlit = g["unlitMaterials"]
        curves = s["objectsByType"].get("CURVE", 0)
        status_b = "kept" if (lines + pts) else "lost"
        ev_b = "%d curve objects, %d point meshes" % (curves, sum(1 for m in meshes if m["faces"] == 0 and m["edges"] == 0))
        facts = info.get("stats", {}).get("facts", {})
        want_col = facts.get("colouredLineSets", 0)
        if not (lines + pts):
            status_g = "lost"
        elif (want_col and col == 0) or unlit == 0:
            status_g = "approx"
        else:
            status_g = "kept"
        ev_g = "%d LINES, %d POINTS; COLOR_0 on %d (source has %d coloured sets), KHR_materials_unlit on %d" % (
            lines, pts, col, want_col, unlit)
        return {"blender": (status_b, ev_b), "gltf": (status_g, ev_g)}
    if rule_id == "elevation-underfilled":
        grid = [m for m in meshes if m["faces"] > 100]
        z = max((m["zRange"] for m in grid), default=0)
        return {"blender": ("lost" if z < 1e-3 else "kept", "terrain height range %.3f m" % z),
                "gltf": ("lost" if z < 1e-3 else "kept", "same mesh")}
    if rule_id == "blinn-phong":
        has = "KHR_materials_specular" in g["extensionsUsed"]
        return {"gltf": ("approx" if has else "lost", "KHR_materials_specular %s" % ("used" if has else "absent"))}
    if rule_id == "tessellation":
        spheres = [m for m in meshes if m["faces"] >= 500]
        smooth = all(m["smoothFaces"] == m["faces"] for m in spheres) if spheres else None
        return {"blender": ("approx", "%d baked curved meshes, %s faces each, %s" % (
            len(spheres), spheres[0]["faces"] if spheres else "-", "smooth" if smooth else "faceted" if smooth is False else "-"))}
    if rule_id == "background-gradient":
        kept = "x3d_background" in s["sceneProps"]
        return {"blender": ("approx" if kept else "lost", "x3d_background property %s" % ("present" if kept else "absent")),
                "gltf": ("lost", "glTF has no background")}
    if rule_id == "animation":
        want = len(info.get("stats", {}).get("facts", {}).get("animatedTransforms", []))
        got = g.get("animatedNodes", 0)
        st = "lost" if not got else "approx" if got >= want else "partial"
        return {"blender": ("approx" if s["actions"] else "lost", "%d actions" % s["actions"]),
                "gltf": (st, "%d of %d interpolator-driven Transforms animated (%d channels)" % (got, want, g["animationChannels"]))}
    if rule_id == "protos":
        want = info.get("expectProtoMeshes")
        got = len(meshes)
        return {"blender": ("approx" if not want or got >= want else "lost",
                            "%d mesh objects%s" % (got, (" (expected ≥ %d)" % want) if want else ""))}
    if rule_id == "text":
        fonts = s["fontObjects"]
        in_glb = any("text" in (m["name"] or "").lower() for m in g["meshDetail"])
        return {"blender": ("approx" if fonts else "lost", "%d font objects" % fonts),
                "gltf": ("approx" if in_glb else "lost", "text mesh %s in glb" % ("present" if in_glb else "absent"))}
    if rule_id == "two-sided":
        return {"gltf": ("approx", "%d doubleSided materials" % sum(1 for m in mats if m["doubleSided"]))}
    if rule_id == "pbr":
        return {"gltf": ("kept", "%d pbrMetallicRoughness materials" % len(mats))}
    if rule_id == "lights":
        return {"blender": ("approx", "%d lights" % s["lights"]),
                "gltf": ("approx" if g["lights"] else "lost", "%d KHR_lights_punctual lights" % g["lights"])}
    if rule_id == "geometry2d":
        flat = sum(1 for m in meshes if m["faces"] and m["zRange"] == 0) + s["objectsByType"].get("CURVE", 0)
        return {"blender": ("kept" if flat else "lost", "%d flat meshes / curves" % flat)}
    return {}


ROOT_CAUSE_RULES = {"scripts", "elevation-underfilled", "script-geometry"}


def verdict(pred: str, meas: str | None) -> str:
    if meas is None:
        return "unmeasured"
    order = {"kept": 0, "approx": 1, "partial": 2, "lost": 3}
    if pred == meas:
        return "confirmed"
    return "worse" if order.get(meas, 0) > order.get(pred, 0) else "better"


# ── glb wrapper: same viewpoints and backdrop, so X_ITE can show source and glb from the same eye ───
def glb_wrapper(source_xml: str, glb_name: str, title: str) -> str:
    doc = parse(source_xml)
    x3d = doc.children[0]
    scene = next(c for c in x3d.children if c.tag == "Scene")
    keep = [c for c in scene.children if c.tag in ("Viewpoint", "NavigationInfo", "Background")]

    def emit(el):
        attrs = " ".join('%s="%s"' % (k, v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;"))
                         for k, v in el.attrs.items())
        return "    <%s %s/>" % (el.tag, attrs)

    body = "\n".join(emit(e) for e in keep)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Interchange" version="4.0">\n  <head>\n'
            '    <meta name="title" content="%s (glb from Blender)"/>\n'
            '    <meta name="description" content="Viewpoints, NavigationInfo and Background copied from the source; '
            'everything else is the glb."/>\n  </head>\n  <Scene>\n%s\n    <Inline url=\'"%s"\'/>\n  </Scene>\n</X3D>\n') % (
        title, body, glb_name)


def build(out: str, *, blender: bool = False, bpy_python: str | None = None, addon: str | None = None,
          circle_divisions: int = 48, log=print) -> dict:
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    entries = []
    for sid, fn in SCENES.items():
        doc, info = fn()
        xml, vrml, js = doc.xml(), doc.vrml(), doc.json()
        (outp / (sid + ".x3d")).write_text(xml)
        (outp / (sid + ".x3dv")).write_text(vrml)
        (outp / (sid + ".x3dj")).write_text(js)
        v = validate_text(xml, filename=sid + ".x3d")
        rules = predict(v["stats"])
        entries.append({"id": sid, **info, "files": {"xml": sid + ".x3d", "vrml": sid + ".x3dv", "json": sid + ".x3dj"},
                        "bytes": {"xml": len(xml.encode()), "vrml": len(vrml.encode()), "json": len(js.encode())},
                        "validation": {k: v[k] for k in ("ok", "errors", "warnings", "issues")},
                        "stats": v["stats"], "fidelity": rules, "legSummary": summarise(rules)})
        log("built %-18s %6d nodes  %3d routes  %d errors %d warnings  %d fidelity rules" % (
            sid, v["stats"]["nodes"], v["stats"]["routes"], v["errors"], v["warnings"], len(rules)))

    blender_meta = None
    if blender:
        bpy_python = bpy_python or os.environ.get("X3DLAB_BPY_PYTHON") or sys.executable
        addon = addon or os.environ.get("X3DLAB_ADDON")
        if not addon:
            raise SystemExit("--blender needs X3DLAB_ADDON (the x3d-blender-io source/ folder) or --addon")
        bdir = outp / "blender"
        bdir.mkdir(exist_ok=True)
        cmd = [bpy_python, "-m", "x3dlab.blender_leg", *[str(outp / (e["id"] + ".x3d")) for e in entries],
               "--out-dir", str(bdir), "--addon", addon, "--circle-divisions", str(circle_divisions)]
        log("blender leg: %s" % " ".join(cmd[:3]))
        env = {**os.environ, "PYTHONPATH": str(HERE.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        last = 0.0
        for line in proc.stdout:
            if line.startswith("[blender-leg]") or time.time() - last > 5:
                log(line.rstrip()[:240])
                last = time.time()
        proc.wait()
        blender_meta = {"python": bpy_python, "addon": addon, "exit": proc.returncode}
        for e in entries:
            jp = bdir / (e["id"] + ".blender.json")
            b = json.loads(jp.read_text()) if jp.exists() else None
            if b and b.get("ok"):
                glb = bdir / (e["id"] + ".glb")
                if glb.exists():
                    (outp / (e["id"] + ".glb")).write_bytes(glb.read_bytes())
                    (outp / (e["id"] + ".glb.x3d")).write_text(
                        glb_wrapper((outp / (e["id"] + ".x3d")).read_text(), e["id"] + ".glb", e["title"]))
                    e["files"]["glb"] = e["id"] + ".glb"
                    e["files"]["glbWrapper"] = e["id"] + ".glb.x3d"
            e["blender"] = summarise_blender(b)
            if e["id"] == "orrery":
                e["expectProtoMeshes"] = 6 + 2  # six planets + sun + comet
            for r in e["fidelity"]:
                m = measure(r["id"], b, e)
                r["measured"] = {leg: list(v) for leg, v in m.items()}
                r["verdicts"] = {leg: verdict(r["legs"][leg][0], m.get(leg, (None,))[0]) for leg in LEGS if leg in m}
                if b and not b.get("ok") and r["id"] not in ROOT_CAUSE_RULES:
                    # the import failed for another reason: don't count every downstream rule as a separate loss
                    r["verdicts"] = {leg: "blocked" for leg in r["verdicts"]}
            if blender_meta.get("blender") is None and b and b.get("blender"):
                blender_meta.update({"blender": b["blender"], "addonVersion": b["addon"]["version"]})
    index = {"generator": "x3dlab %s" % __version__, "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "schema": json.loads((HERE / "schema" / "x3d40.json").read_text())["source"],
             "seconds": round(time.time() - t0, 2), "blender": blender_meta, "scenes": entries}
    (outp / "index.json").write_text(json.dumps(index, indent=1))
    log("wrote %s (%d scenes) in %.1f s" % (outp / "index.json", len(entries), time.time() - t0))
    return index


def summarise_blender(b: dict | None) -> dict | None:
    if b is None:
        return None
    if not b.get("ok"):
        err = (b.get("error") or "").strip().splitlines()
        return {"ok": False, "error": next((l.strip() for l in reversed(err) if "Error" in l), "import failed")[:200]}
    s, g = b["survey"], b["glb"]
    return {"ok": True, "blender": b["blender"], "addonVersion": b["addon"]["version"], "seconds": b["totalSeconds"],
            "objects": s["objectsByType"], "meshes": len(s["meshes"]), "materials": len(s["materials"]),
            "actions": s["actions"], "sceneProps": s["sceneProps"], "dropped": b["droppedUnused"],
            "exportOptions": b["export"]["options"],
            "glb": {k: g.get(k) for k in ("bytes", "primitiveModes", "primitivesWithColor0", "unlitMaterials", "animations",
                                      "animationChannels", "animatedNodes", "cameras", "lights", "extensionsUsed", "meshes", "nodes")}}
