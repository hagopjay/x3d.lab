using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using X3D.Core;

static class T
{
    static int pass, fail;
    static void Ok(bool c, string name) { if (c) { pass++; Console.WriteLine("  ok   " + name); } else { fail++; Console.WriteLine("  FAIL " + name); } }
    static bool Near(double a, double b, double tol) { return Math.Abs(a - b) <= tol; }
    static string Scene(string body) { return "<X3D profile='Interchange' version='4.0'><Scene>" + body + "</Scene></X3D>"; }
    static MeshData Geo(string geomXml) { var s = X3DParser.Parse(Scene("<Shape>" + geomXml + "</Shape>")); return Tessellator.Build(s.Roots[0].First("geometry"), null); }
    static double Area(MeshData m)
    {
        double a = 0; var P = m.Positions;
        for (int i = 0; i + 2 < m.Triangles.Length; i += 3)
        {
            int p = m.Triangles[i] * 3, q = m.Triangles[i + 1] * 3, r = m.Triangles[i + 2] * 3;
            double ux = P[q] - P[p], uy = P[q + 1] - P[p + 1], uz = P[q + 2] - P[p + 2], vx = P[r] - P[p], vy = P[r + 1] - P[p + 1], vz = P[r + 2] - P[p + 2];
            double cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx; a += 0.5 * Math.Sqrt(cx * cx + cy * cy + cz * cz);
        }
        return a;
    }
    static double[] Rot(float[] q, double[] v) // v' = q v q^-1
    {
        double x = q[0], y = q[1], z = q[2], w = q[3];
        double tx = 2 * (y * v[2] - z * v[1]), ty = 2 * (z * v[0] - x * v[2]), tz = 2 * (x * v[1] - y * v[0]);
        return new[] { v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx) };
    }

    static int Main(string[] args)
    {
        Console.WriteLine("parser");
        var s = X3DParser.Parse(Scene("<Transform DEF='A'><Shape DEF='S'><Box/></Shape></Transform><Transform><Shape USE='S'/></Transform><ROUTE fromNode='A' fromField='x' toNode='A' toField='y'/>"));
        Ok(s.Roots.Count == 2 && s.Defs.ContainsKey("A") && s.Defs.ContainsKey("S"), "DEF registered");
        var use = s.Roots[1].Children[0];
        Ok(use.UseName == "S" && use.Resolve().Type == "Shape" && use.Resolve().First("geometry").Type == "Box", "USE resolves to DEF'd node");
        Ok(s.Routes.Count == 1, "ROUTE collected");
        Ok(X3DParser.Parse(Scene("<Shape USE='Nope'/>")).Warnings.Count == 1, "USE before DEF warned, not thrown");
        bool threw = false; try { X3DParser.Parse("<html/>"); } catch (FormatException) { threw = true; }
        Ok(threw, "non-X3D root rejected");

        Console.WriteLine("tessellation (X3D space, right-handed, CCW outward: signed volume > 0)");
        var box = Geo("<Box/>");
        Ok(box.VertexCount == 24 && box.TriangleCount == 12, "Box 24 verts, 12 tris");
        Ok(Near(Convention.SignedVolume(box), 8, 1e-4), "Box default volume 8");
        var sph = Geo("<Sphere radius='1'/>"); Ok(Convention.SignedVolume(sph) > 3.9 && Convention.SignedVolume(sph) < 4.2, "Sphere volume ~4.19 (got " + Convention.SignedVolume(sph).ToString("F3") + ")");
        var cyl = Geo("<Cylinder/>"); Ok(Near(Convention.SignedVolume(cyl), 6.21, 0.2), "Cylinder volume ~pi*2 (got " + Convention.SignedVolume(cyl).ToString("F3") + ")");
        var cone = Geo("<Cone/>"); Ok(Near(Convention.SignedVolume(cone), 2.07, 0.15), "Cone volume ~2.09 (got " + Convention.SignedVolume(cone).ToString("F3") + ")");
        var lshape = Geo("<IndexedFaceSet coordIndex='0 1 2 3 4 5 -1'><Coordinate point='0 0 0  2 0 0  2 1 0  1 1 0  1 2 0  0 2 0'/></IndexedFaceSet>");
        Ok(lshape.TriangleCount == 4 && Near(Area(lshape), 3, 1e-4), "concave L polygon: 4 tris, area 3 (ear clipping, no fan error)");
        var cw = Geo("<IndexedFaceSet ccw='false' coordIndex='0 1 2 3 -1  4 7 6 5 -1  0 4 5 1 -1  1 5 6 2 -1  2 6 7 3 -1  3 7 4 0 -1'><Coordinate point='0 0 0  1 0 0  1 1 0  0 1 0  0 0 1  1 0 1  1 1 1  0 1 1'/></IndexedFaceSet>");
        Ok(cw.TriangleCount == 12, "ccw=false cube: 12 tris");
        var ccwCube = Geo("<IndexedFaceSet coordIndex='0 3 2 1 -1  4 5 6 7 -1  0 1 5 4 -1  1 2 6 5 -1  2 3 7 6 -1  3 0 4 7 -1'><Coordinate point='0 0 0  1 0 0  1 1 0  0 1 0  0 0 1  1 0 1  1 1 1  0 1 1'/></IndexedFaceSet>");
        Ok(Near(Convention.SignedVolume(ccwCube), 1, 1e-4), "IFS unit cube CCW outward: volume +1");
        var flat = Geo("<IndexedFaceSet creaseAngle='0' coordIndex='0 3 2 1 -1  4 5 6 7 -1  0 1 5 4 -1  1 2 6 5 -1  2 3 7 6 -1  3 0 4 7 -1'><Coordinate point='0 0 0  1 0 0  1 1 0  0 1 0  0 0 1  1 0 1  1 1 1  0 1 1'/></IndexedFaceSet>");
        var smooth = Geo("<IndexedFaceSet creaseAngle='3.2' coordIndex='0 3 2 1 -1  4 5 6 7 -1  0 1 5 4 -1  1 2 6 5 -1  2 3 7 6 -1  3 0 4 7 -1'><Coordinate point='0 0 0  1 0 0  1 1 0  0 1 0  0 0 1  1 0 1  1 1 1  0 1 1'/></IndexedFaceSet>");
        Ok(flat.Normals.All(n => Near(Math.Abs(n), 0, 1e-5) || Near(Math.Abs(n), 1, 1e-5)), "creaseAngle 0: axis-aligned flat normals");
        Ok(smooth.Normals.Any(n => !Near(Math.Abs(n), 0, 1e-3) && !Near(Math.Abs(n), 1, 1e-3)), "creaseAngle > pi: corner normals blend (not axis-aligned)");

        Console.WriteLine("handedness (X3D right-handed -> Unity left-handed)");
        foreach (var ax in new[] { MirrorAxis.Z, MirrorAxis.X })
        {
            var u = Convention.ToUnity(box, ax);
            Ok(Near(Convention.SignedVolume(u), 8, 1e-4), ax + " mirror + winding flip keeps faces outward (volume still +8)");
            var bad = new MeshData { Positions = u.Positions, Triangles = box.Triangles };
            Ok(Convention.SignedVolume(bad) < 0, ax + " mirror WITHOUT winding flip turns the mesh inside out (the failure we guard against)");
        }
        var qz = Convention.Rotation(new float[] { 0, 1, 0, (float)(Math.PI / 2) }, MirrorAxis.Z);
        var r1 = Rot(qz, new double[] { 1, 0, 0 });
        Ok(Near(r1[0], 0, 1e-5) && Near(r1[2], 1, 1e-5), "Z-mirror: X3D (1,0,0) turned +90 about Y lands at (0,0,-1); mirrored expectation (0,0,+1)");
        var qx = Convention.Rotation(new float[] { 0, 1, 0, (float)(Math.PI / 2) }, MirrorAxis.X);
        var r2 = Rot(qx, new double[] { -1, 0, 0 });
        Ok(Near(r2[0], 0, 1e-5) && Near(r2[2], -1, 1e-5), "X-mirror (glTFast style): same rotation, mirrored point (-1,0,0) lands at (0,0,-1)");
        var p1 = Convention.Position(new float[] { 1, 2, 3 }, MirrorAxis.Z); var p2 = Convention.Position(new float[] { 1, 2, 3 }, MirrorAxis.X);
        Ok(p1[2] == -3 && p2[0] == -1, "positions mirror on the chosen axis only");
        // the glTFast finding: the two conventions differ by exactly a 180-degree turn about Y
        Ok(Near(p1[0], -p2[0], 1e-6) && Near(p1[2], -p2[2], 1e-6) && p1[1] == p2[1], "Z-mirror and X-mirror differ by a 180 deg turn about Y (so native and glTFast imports face opposite ways)");

        Console.WriteLine("transform");
        var tn = X3DParser.Parse(Scene("<Transform translation='1 2 3' rotation='0 1 0 1.5708' scale='2 2 2'/>")).Roots[0];
        var trs = Xform.FromTransform(tn, MirrorAxis.Z);
        Ok(trs.T[2] == -3 && trs.S[0] == 2 && !trs.SkewLost, "simple TRS, Z mirrored");
        var cn = X3DParser.Parse(Scene("<Transform center='1 0 0' scale='2 2 2'/>")).Roots[0];
        var ct = Xform.FromTransform(cn, MirrorAxis.Z);
        Ok(Near(ct.T[0], -1, 1e-5) && Near(ct.S[0], 2, 1e-5), "scale about center (1,0,0): translation -1, scale 2 (matrix path)");
        var sk = X3DParser.Parse(Scene("<Transform scaleOrientation='0 0 1 0.6' scale='3 1 1'/>")).Roots[0];
        Ok(Xform.FromTransform(sk, MirrorAxis.Z).SkewLost, "non-uniform scale in a rotated frame reports skew Unity cannot hold");

        Console.WriteLine("animation");
        var a = X3DParser.Parse(File.ReadAllText(args.Length > 0 ? args[0] : "../../samples/scenes/hello-xr.x3d"));
        var g = new AnimationGraph(a);
        var at0 = g.Evaluate(0); var at1 = g.Evaluate(1); var at2 = g.Evaluate(2); var at4 = g.Evaluate(4);
        Ok(at0.Count == 3 + 0 || at0.Count == 3, "t=0 : 3 chains drive Spinner.rotation/translation and CubeMat.diffuseColor (got " + at0.Count + ")");
        Func<List<FieldUpdate>, string, float[]> val = (l, f) => l.First(u => u.Field == f).Value;
        Ok(Near(val(at2, "translation")[1], 1.4, 1e-4), "t=2 (half of 4 s cycle): bob peak y=1.4");
        Ok(Near(val(at1, "translation")[1], 1.1, 1e-4), "t=1 (quarter): bob y=1.1");
        Ok(Near(val(at2, "rotation")[3], 3.14159, 1e-3), "t=2: orientation key at 3.14159");
        Ok(Near(val(at1, "rotation")[3], 1.5708, 2e-3), "t=1: slerp midpoint ~pi/2");
        Ok(Near(val(at4, "diffuseColor")[0], 0.9, 1e-3), "t=4 (loop wraps): colour back at start");
        Ok(g.Gaps.Count == 0, "sample has no routing gaps");
        var once = X3DParser.Parse(Scene("<TimeSensor DEF='T' cycleInterval='2' startTime='1'/><PositionInterpolator DEF='P' key='0 1' keyValue='0 0 0 10 0 0'/><Transform DEF='X'/><ROUTE fromNode='T' fromField='fraction_changed' toNode='P' toField='set_fraction'/><ROUTE fromNode='P' fromField='value_changed' toNode='X' toField='set_translation'/>"));
        var og = new AnimationGraph(once);
        Ok(og.Evaluate(0.5).Count == 0, "before startTime: nothing fires");
        Ok(Near(og.Evaluate(99)[0].Value[0], 10, 1e-4), "non-loop clamps to the last key");
        var badG = new AnimationGraph(X3DParser.Parse(Scene("<Script DEF='Sc'/><Transform DEF='X'/><ROUTE fromNode='Sc' fromField='out' toNode='X' toField='set_translation'/><ROUTE fromNode='Ghost' fromField='a' toNode='X' toField='b'/>")));
        Ok(badG.Gaps.Contains("route-source:Script") && badG.Gaps.Any(x => x.StartsWith("route:dangling")), "Script source and dangling ROUTE land in the gap ledger");

        Console.WriteLine("writer round trip");
        var xml = File.ReadAllText(args.Length > 0 ? args[0] : "../../samples/scenes/hello-xr.x3d");
        var w1 = X3DWriter.Write(a); var s2 = X3DParser.Parse(w1); var w2 = X3DWriter.Write(s2);
        Ok(w1 == w2, "parse -> write -> parse -> write is a fixed point");
        Ok(s2.Defs.Count == a.Defs.Count && s2.Routes.Count == a.Routes.Count, "DEF and ROUTE counts survive (" + s2.Defs.Count + " DEFs, " + s2.Routes.Count + " ROUTEs)");

        Console.WriteLine("receipt");
        int tri = 0, pts = 0; var gaps = new List<string>();
        Action<X3DNode> walk = null;
        walk = n => { var r = n.Resolve(); if (r.Type == "Shape") { var geo = r.First("geometry"); if (geo != null) { var m = Tessellator.Build(geo, gaps); if (m != null) { tri += m.TriangleCount; if (m.IsPoints) pts += m.VertexCount; } } } foreach (var c in r.Children) walk(c); };
        foreach (var n in a.Roots) walk(n);
        var rc = Receipt.Build(xml, a, gaps, tri, pts);
        Ok(rc.Gaps.Count == 0, "hello-xr: zero gaps");
        Ok(rc.Sha256.Length == 64 && rc.ToJson().Contains("x3d-unity-receipt/v1"), "receipt carries sha256 + schema");
        var gx = X3DParser.Parse(Scene("<Shape><Extrusion/></Shape><Script/>")); var gg = new List<string>();
        Tessellator.Build(gx.Roots[0].First("geometry"), gg);
        var rg = Receipt.Build("x", gx, gg, 0, 0);
        Ok(rg.Gaps.Contains("node:Extrusion") && rg.Gaps.Contains("node:Script") && rg.Gaps.Contains("geometry:Extrusion"), "unsupported nodes named, not dropped");
        Console.WriteLine("hello-xr: " + tri + " triangles, " + pts + " points, nodes " + string.Join(",", rc.NodeCounts.Select(k => k.Key + "=" + k.Value)));

        Console.WriteLine("\n" + pass + " passed, " + fail + " failed");
        return fail == 0 ? 0 : 1;
    }
}
