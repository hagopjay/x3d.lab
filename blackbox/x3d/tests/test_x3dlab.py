"""x3dlab tests (stdlib unittest). Run: cd x3d && python3 -m unittest discover -s tests -v

The Blender test runs only when X3DLAB_BPY_PYTHON and X3DLAB_ADDON are set.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from x3dlab.build import measure, verdict  # noqa: E402
from x3dlab.fidelity import predict, summarise  # noqa: E402
from x3dlab.glb import inspect  # noqa: E402
from x3dlab.model import BuildError, Doc, N, format_value, qstr  # noqa: E402
from x3dlab.scenes import SCENES, terrain_height  # noqa: E402
from x3dlab.validate import validate_text  # noqa: E402

# One defect per line, each with the code the validator must report: an answer key, BLACKBOX style.
BROKEN = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Interchange" version="4.0">
  <head><component name="Shape" level="x"/></head>
  <Scene>
    <Shape DEF="A"><Appearance><Material diffuseColor="1 0 0" shinyness="0.5"/></Appearance><Spher/></Shape>
    <Shape DEF="A"><Appearance><Material diffuseColor="1.5 0 0"/></Appearance><Box size="1 1"/></Shape>
    <Transform USE="Nope"/>
    <Shape><IndexedFaceSet coordIndex="0 1 7 -1"><Coordinate point="0 0 0, 1 0 0, 1 1 0"/></IndexedFaceSet></Shape>
    <Shape><PointSet><Coordinate point="0 0 0, 1 1 1"/><Color color="1 0 0"/></PointSet></Shape>
    <Shape><ElevationGrid xDimension="3" zDimension="3" height="0 0 0 0"/></Shape>
    <Shape><Appearance><Material/></Appearance><Sphere solid="TRUE"/></Shape>
    <TimeSensor DEF="T" loop="true"/>
    <Transform DEF="X"/>
    <ROUTE fromNode="T" fromField="fraction_changed" toNode="X" toField="set_translation"/>
    <ROUTE fromNode="T" fromField="bogus" toNode="Ghost" toField="set_rotation"/>
    <Transform><Material/></Transform>
  </Scene>
</X3D>
"""
EXPECTED = {
    "E_COMPONENT", "E_UNKNOWN_FIELD", "E_UNKNOWN_NODE", "E_DEF_DUP", "E_COLOR_RANGE", "E_ARITY", "E_USE_UNDEFINED",
    "E_INDEX_RANGE", "E_COLOR_COUNT", "W_ELEVATION_UNDERFILLED", "E_BOOL", "E_ROUTE_TYPE", "E_ROUTE_NODE", "E_CONTAINER",
}


class Model(unittest.TestCase):
    def test_format_values(self):
        self.assertEqual(format_value("SFBool", True), "true")
        self.assertEqual(format_value("SFBool", True, vrml=True), "TRUE")
        self.assertEqual(format_value("MFVec3f", [(0, 1, 2), (3, 4.5, 6)]), "0 1 2, 3 4.5 6")
        self.assertEqual(format_value("MFString", ["EXAMINE", "ANY"]), '"EXAMINE" "ANY"')
        self.assertEqual(qstr('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(BuildError):
            format_value("SFColor", (1, 0))

    def test_typos_fail_at_build_time_with_a_suggestion(self):
        with self.assertRaisesRegex(BuildError, "did you mean 'radius'"):
            N("Sphere", radiuss=2)
        with self.assertRaisesRegex(BuildError, "did you mean 'Sphere'"):
            N("Spher")

    def test_container_fields_follow_the_schema(self):
        doc = Doc(title="t").add(N("Shape", N("Appearance", N("Material")), N("Box")))
        xml = doc.xml()
        self.assertIn("<Appearance>", xml)
        self.assertNotIn("containerField", xml)  # defaults need no attribute
        self.assertIn("appearance Appearance", doc.vrml())
        self.assertIn('"-appearance"', doc.json())


class Scenes(unittest.TestCase):
    def test_every_scene_builds_valid_in_three_encodings(self):
        for sid, fn in SCENES.items():
            with self.subTest(sid):
                doc, info = fn()
                r = validate_text(doc.xml(), filename=sid)
                self.assertTrue(r["ok"], [i for i in r["issues"] if i["level"] == "error"])
                self.assertTrue(doc.vrml().startswith("#X3D V4.0 utf8"))
                self.assertIn("X3D", json.loads(doc.json()))
                self.assertTrue(info["title"] and info["blurb"])

    def test_builds_are_deterministic(self):
        for sid, fn in SCENES.items():
            self.assertEqual(fn()[0].xml(), fn()[0].xml(), sid)

    def test_baked_terrain_matches_the_height_function(self):
        doc, _ = SCENES["terrain-baked"]()
        grid = next(n for n in doc.nodes() if n.type == "ElevationGrid")
        self.assertEqual(len(grid.fields["height"]), 49 * 49)
        self.assertAlmostEqual(grid.fields["height"][50], round(terrain_height(1, 1), 5))
        script_doc, _ = SCENES["terrain-script"]()
        self.assertNotIn("height", next(n for n in script_doc.nodes() if n.type == "ElevationGrid").fields)


class Validator(unittest.TestCase):
    def test_answer_key(self):
        r = validate_text(BROKEN, filename="broken.x3d")
        codes = {i["code"] for i in r["issues"]}
        self.assertFalse(r["ok"])
        missing = EXPECTED - codes
        self.assertFalse(missing, "validator missed %s; reported %s" % (missing, sorted(codes)))
        self.assertTrue(all(i["line"] > 0 for i in r["issues"]))

    def test_not_xml(self):
        r = validate_text("<X3D><Scene>", filename="x")
        self.assertEqual(r["issues"][0]["code"], "E_XML")

    def test_routes_into_shader_and_proto_fields(self):
        for sid in ("shader-ribbon", "orrery"):
            r = validate_text(SCENES[sid]()[0].xml())
            self.assertEqual(r["errors"], 0, r["issues"][:3])
            self.assertGreater(r["stats"]["routes"], 0)


class Fidelity(unittest.TestCase):
    def rules(self, sid):
        return {r["id"]: r for r in predict(validate_text(SCENES[sid]()[0].xml())["stats"])}

    def test_rules_fire_from_the_scene(self):
        self.assertEqual(self.rules("shader-ribbon")["shaders"]["legs"]["gltf"][0], "lost")
        self.assertIn("elevation-underfilled", self.rules("terrain-script"))
        self.assertNotIn("elevation-underfilled", self.rules("terrain-baked"))
        self.assertIn("protos", self.rules("orrery"))
        self.assertIn("colour-space", self.rules("material-ladder"))
        summary = summarise(list(self.rules("orrery").values()))
        self.assertEqual(summary["xite"]["lost"], 0)  # X_ITE is the reference

    def test_measurement_and_verdicts(self):
        self.assertEqual(verdict("approx", "lost"), "worse")
        self.assertEqual(verdict("lost", "lost"), "confirmed")
        failed = {"ok": False, "error": "Traceback\nIndexError: list index out of range"}
        self.assertEqual(measure("elevation-underfilled", failed, {})["blender"][0], "lost")


def tiny_glb() -> bytes:
    gltf = {"asset": {"version": "2.0", "generator": "x3dlab test"}, "extensionsUsed": ["KHR_materials_unlit"],
            "meshes": [{"name": "Lines", "primitives": [{"mode": 1, "attributes": {"POSITION": 0, "COLOR_0": 1}, "material": 0}]}],
            "materials": [{"name": "m", "extensions": {"KHR_materials_unlit": {}}}],
            "accessors": [{"count": 2, "min": [0, 0, 0], "max": [1, 1, 1]}, {"count": 2}],
            "animations": [{"channels": [{"target": {"node": 0}}, {"target": {"node": 0}}]}]}
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class Glb(unittest.TestCase):
    def test_inspect(self):
        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
            f.write(tiny_glb())
        g = inspect(f.name)
        os.unlink(f.name)
        self.assertEqual(g["primitiveModes"], {"LINES": 1})
        self.assertEqual((g["primitivesWithColor0"], g["unlitMaterials"], g["animatedNodes"]), (1, 1, 1))


class McpServer(unittest.TestCase):
    def test_stdio_session(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "validate_x3d", "arguments": {"text": BROKEN}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "compose_scene", "arguments": {"title": "t", "nodes": [{"type": "Sphere", "fields": {"radiuss": 1}}]}}},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "x3dlab://scenes/orrery.x3dv"}},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"},
        ]
        env = {**os.environ, "PYTHONPATH": str(HERE.parent), "X3DLAB_OUT": tempfile.mkdtemp()}
        out = subprocess.run([sys.executable, "-m", "x3dlab", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                             capture_output=True, text=True, env=env, cwd=str(HERE.parent), timeout=60)
        replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "x3dlab")
        self.assertEqual(len(replies[2]["result"]["tools"]), 7)
        self.assertFalse(replies[3]["result"]["structuredContent"]["ok"])
        self.assertTrue(replies[4]["result"]["isError"])
        self.assertIn("did you mean 'radius'", replies[4]["result"]["content"][0]["text"])
        self.assertTrue(replies[5]["result"]["contents"][0]["text"].startswith("#X3D V4.0"))
        self.assertEqual(replies[6]["error"]["code"], -32601)


@unittest.skipUnless(os.environ.get("X3DLAB_BPY_PYTHON") and os.environ.get("X3DLAB_ADDON"), "Blender leg not configured")
class BlenderLeg(unittest.TestCase):
    def test_baked_terrain_round_trip(self):
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "terrain-baked.x3d"
        src.write_text(SCENES["terrain-baked"]()[0].xml())
        env = {**os.environ, "PYTHONPATH": str(HERE.parent)}
        subprocess.run([os.environ["X3DLAB_BPY_PYTHON"], "-m", "x3dlab.blender_leg", str(src), "--out-dir", str(tmp),
                        "--addon", os.environ["X3DLAB_ADDON"]], env=env, check=True, capture_output=True, timeout=600)
        r = json.loads((tmp / "terrain-baked.blender.json").read_text())
        self.assertTrue(r["ok"])
        self.assertGreater(max(m["zRange"] for m in r["survey"]["meshes"]), 1.0)  # the heights survived
        self.assertEqual(r["export"]["lighting"], "COMPAT")
        self.assertGreater(r["glb"]["primitivesWithColor0"], 0)


if __name__ == "__main__":
    unittest.main()
