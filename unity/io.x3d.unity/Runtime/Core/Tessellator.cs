using System;
using System.Collections.Generic;

namespace X3D.Core
{
    /// <summary>X3D geometry nodes to triangle meshes in X3D space (right-handed, CCW front faces). Apply Convention.ToUnity afterwards.</summary>
    public static class Tessellator
    {
        public static readonly string[] Supported =
        { "Box", "Sphere", "Cylinder", "Cone", "IndexedFaceSet", "IndexedTriangleSet", "TriangleSet", "PointSet" };

        public static MeshData Build(X3DNode g, List<string> gaps)
        {
            g = g.Resolve();
            MeshData m;
            switch (g.Type)
            {
                case "Box": m = Box(g.Vec("size", new[] { 2f, 2f, 2f })); break;
                case "Sphere": m = Sphere(g.F("radius", 1f), 24, 16); break;
                case "Cylinder": m = Cylinder(g.F("radius", 1f), g.F("height", 2f), g.B("top", true), g.B("bottom", true), g.B("side", true), 24); break;
                case "Cone": m = Cone(g.F("bottomRadius", 1f), g.F("height", 2f), g.B("bottom", true), g.B("side", true), 24); break;
                case "IndexedFaceSet": m = IndexedFaceSet(g, gaps); break;
                case "IndexedTriangleSet": m = IndexedTriangleSet(g); break;
                case "TriangleSet": m = TriangleSet(g); break;
                case "PointSet": m = PointSet(g); break;
                default: if (gaps != null) gaps.Add("geometry:" + g.Type); return null;
            }
            if (m != null) m.DoubleSided = !g.B("solid", true);
            return m;
        }

        static MeshData Box(float[] s)
        {
            float x = s[0] / 2, y = s[1] / 2, z = s[2] / 2;
            var faces = new[] {  // normal, then 4 corners CCW seen from outside
                new float[]{0,0,1,  -x,-y,z,  x,-y,z,  x,y,z,  -x,y,z},
                new float[]{0,0,-1, x,-y,-z, -x,-y,-z, -x,y,-z, x,y,-z},
                new float[]{1,0,0,  x,-y,z,  x,-y,-z,  x,y,-z,  x,y,z},
                new float[]{-1,0,0, -x,-y,-z, -x,-y,z, -x,y,z, -x,y,-z},
                new float[]{0,1,0,  -x,y,z,  x,y,z,   x,y,-z,  -x,y,-z},
                new float[]{0,-1,0, -x,-y,-z, x,-y,-z, x,-y,z,  -x,-y,z}};
            var P = new List<float>(); var N = new List<float>(); var U = new List<float>(); var T = new List<int>();
            foreach (var f in faces)
            {
                int b = P.Count / 3;
                for (int i = 0; i < 4; i++) { P.Add(f[3 + i * 3]); P.Add(f[4 + i * 3]); P.Add(f[5 + i * 3]); N.Add(f[0]); N.Add(f[1]); N.Add(f[2]); }
                U.AddRange(new float[] { 0, 0, 1, 0, 1, 1, 0, 1 });
                T.AddRange(new[] { b, b + 1, b + 2, b, b + 2, b + 3 });
            }
            return new MeshData { Positions = P.ToArray(), Normals = N.ToArray(), Uvs = U.ToArray(), Triangles = T.ToArray() };
        }

        static MeshData Sphere(float r, int seg, int rings)
        {
            var P = new List<float>(); var N = new List<float>(); var U = new List<float>(); var T = new List<int>();
            for (int i = 0; i <= rings; i++)
            {
                double th = Math.PI * i / rings; // 0 = +Y pole
                for (int j = 0; j <= seg; j++)
                {
                    double ph = 2 * Math.PI * j / seg;
                    float nx = (float)(Math.Sin(th) * Math.Sin(ph)), ny = (float)Math.Cos(th), nz = (float)(Math.Sin(th) * Math.Cos(ph));
                    P.Add(nx * r); P.Add(ny * r); P.Add(nz * r); N.Add(nx); N.Add(ny); N.Add(nz);
                    U.Add((float)j / seg); U.Add(1f - (float)i / rings);
                }
            }
            for (int i = 0; i < rings; i++)
                for (int j = 0; j < seg; j++)
                {
                    int a = i * (seg + 1) + j, b = a + seg + 1;
                    if (i != 0) T.AddRange(new[] { a, b, a + 1 });
                    if (i != rings - 1) T.AddRange(new[] { a + 1, b, b + 1 });
                }
            return new MeshData { Positions = P.ToArray(), Normals = N.ToArray(), Uvs = U.ToArray(), Triangles = T.ToArray() };
        }

        static MeshData Revolve(float rTop, float rBot, float h, bool top, bool bottom, bool side, int seg)
        {
            var P = new List<float>(); var N = new List<float>(); var U = new List<float>(); var T = new List<int>();
            float hh = h / 2;
            if (side)
            {
                double slope = (rBot - rTop) / h; // outward normal tilt
                for (int j = 0; j <= seg; j++)
                {
                    double ph = 2 * Math.PI * j / seg; float s = (float)Math.Sin(ph), c = (float)Math.Cos(ph);
                    float len = (float)Math.Sqrt(1 + slope * slope);
                    P.Add(rTop * s); P.Add(hh); P.Add(rTop * c); N.Add(s / len); N.Add((float)slope / len); N.Add(c / len); U.Add((float)j / seg); U.Add(1);
                    P.Add(rBot * s); P.Add(-hh); P.Add(rBot * c); N.Add(s / len); N.Add((float)slope / len); N.Add(c / len); U.Add((float)j / seg); U.Add(0);
                }
                for (int j = 0; j < seg; j++)
                {
                    int a = j * 2;
                    if (rTop > 0) T.AddRange(new[] { a, a + 1, a + 2 });                 // top-left, bottom-left, top-right
                    T.AddRange(new[] { a + 2, a + 1, a + 3 });
                }
            }
            Action<float, float, float> cap = (y, rad, ny) =>
            {
                int b = P.Count / 3;
                P.Add(0); P.Add(y); P.Add(0); N.Add(0); N.Add(ny); N.Add(0); U.Add(.5f); U.Add(.5f);
                for (int j = 0; j <= seg; j++)
                {
                    double ph = 2 * Math.PI * j / seg; float s = (float)Math.Sin(ph), c = (float)Math.Cos(ph);
                    P.Add(rad * s); P.Add(y); P.Add(rad * c); N.Add(0); N.Add(ny); N.Add(0); U.Add(.5f + .5f * s); U.Add(.5f + .5f * c);
                }
                for (int j = 0; j < seg; j++)
                    if (ny > 0) T.AddRange(new[] { b, b + 1 + j, b + 2 + j }); else T.AddRange(new[] { b, b + 2 + j, b + 1 + j });
            };
            if (top && rTop > 0) cap(hh, rTop, 1);
            if (bottom) cap(-hh, rBot, -1);
            return new MeshData { Positions = P.ToArray(), Normals = N.ToArray(), Uvs = U.ToArray(), Triangles = T.ToArray() };
        }

        static MeshData Cylinder(float r, float h, bool top, bool bottom, bool side, int seg) { return Revolve(r, r, h, top, bottom, side, seg); }
        static MeshData Cone(float r, float h, bool bottom, bool side, int seg) { return Revolve(0, r, h, false, bottom, side, seg); }

        static float[] Coords(X3DNode g)
        {
            var c = g.First("coord"); return c == null ? new float[0] : c.Floats("point");
        }

        static float[] ColorsRgba(X3DNode g, int vertexCount)
        {
            var c = g.First("color"); if (c == null) return new float[0];
            var v = c.Floats(c.Type == "ColorRGBA" ? "color" : "color");
            int comps = c.Type == "ColorRGBA" ? 4 : 3;
            int n = v.Length / comps;
            var o = new float[n * 4];
            for (int i = 0; i < n; i++) { o[i * 4] = v[i * comps]; o[i * 4 + 1] = v[i * comps + 1]; o[i * 4 + 2] = v[i * comps + 2]; o[i * 4 + 3] = comps == 4 ? v[i * comps + 3] : 1f; }
            return o;
        }

        static MeshData PointSet(X3DNode g)
        {
            var P = Coords(g);
            return new MeshData { Positions = P, Colors = ColorsRgba(g, P.Length / 3), IsPoints = true };
        }

        static MeshData TriangleSet(X3DNode g)
        {
            var P = Coords(g); int n = P.Length / 3 / 3 * 3; var idx = new int[n];
            for (int i = 0; i < n; i++) idx[i] = i;
            return Flat(P, idx, g.B("ccw", true), g);
        }

        static MeshData IndexedTriangleSet(X3DNode g)
        {
            var idx = g.Ints("index"); var P = Coords(g);
            int n = idx.Length / 3 * 3; var t = new int[n]; Array.Copy(idx, t, n);
            return Flat(P, t, g.B("ccw", true), g);
        }

        static MeshData Flat(float[] P, int[] tri, bool ccw, X3DNode g)
        {
            var polys = new List<int[]>();
            for (int i = 0; i + 2 < tri.Length; i += 3) polys.Add(new[] { tri[i], tri[i + 1], tri[i + 2] });
            return FromPolys(P, polys, ccw, g.F("creaseAngle", 0f), null, g);
        }

        static MeshData IndexedFaceSet(X3DNode g, List<string> gaps)
        {
            var P = Coords(g); var ci = g.Ints("coordIndex");
            var polys = new List<int[]>(); var cur = new List<int>();
            foreach (int i in ci) { if (i < 0) { if (cur.Count >= 3) polys.Add(cur.ToArray()); cur.Clear(); } else cur.Add(i); }
            if (cur.Count >= 3) polys.Add(cur.ToArray());
            if (g.Has("texCoordIndex") && gaps != null) gaps.Add("field:IndexedFaceSet.texCoordIndex (UVs follow coordIndex)");
            if (g.Has("colorIndex") && gaps != null) gaps.Add("field:IndexedFaceSet.colorIndex (colour follows coordIndex)");
            return FromPolys(P, polys, g.B("ccw", true), g.F("creaseAngle", 0f), g, g);
        }

        static MeshData FromPolys(float[] P, List<int[]> polys, bool ccw, float crease, X3DNode ifs, X3DNode g)
        {
            // Triangulate each polygon (ear clipping in its Newell plane), then give every face corner its own vertex so crease angles can split normals.
            var tris = new List<int[]>(); var faceOf = new List<int>();
            for (int f = 0; f < polys.Count; f++)
                foreach (var t in Triangulate(P, polys[f])) { tris.Add(t); faceOf.Add(f); }
            int V = P.Length / 3;
            var fn = new float[tris.Count][];
            for (int i = 0; i < tris.Count; i++)
            {
                var t = tris[i]; fn[i] = Cross(P, t[0], t[1], t[2]);
                if (!ccw) { fn[i][0] = -fn[i][0]; fn[i][1] = -fn[i][1]; fn[i][2] = -fn[i][2]; }
            }
            var byVertex = new List<int>[V]; for (int v = 0; v < V; v++) byVertex[v] = new List<int>();
            for (int i = 0; i < tris.Count; i++) foreach (int v in tris[i]) if (v >= 0 && v < V) byVertex[v].Add(i);
            var colors = g != null ? ColorsRgba(g, V) : new float[0];
            var uvs = g != null && g.First("texCoord") != null ? g.First("texCoord").Floats("point") : new float[0];
            var userN = g != null && g.First("normal") != null ? g.First("normal").Floats("vector") : new float[0];
            bool perVertexN = userN.Length / 3 == V && V > 0;
            var oP = new List<float>(); var oN = new List<float>(); var oC = new List<float>(); var oU = new List<float>(); var oT = new List<int>();
            for (int i = 0; i < tris.Count; i++)
            {
                var t = tris[i];
                for (int k = 0; k < 3; k++)
                {
                    int v = t[k];
                    oP.Add(P[v * 3]); oP.Add(P[v * 3 + 1]); oP.Add(P[v * 3 + 2]);
                    float nx, ny, nz;
                    if (perVertexN) { nx = userN[v * 3]; ny = userN[v * 3 + 1]; nz = userN[v * 3 + 2]; }
                    else
                    {
                        nx = fn[i][0]; ny = fn[i][1]; nz = fn[i][2];
                        if (crease > 0)
                        {
                            double cos = Math.Cos(crease); nx = ny = nz = 0;
                            var own = Norm(fn[i]);
                            foreach (int j in byVertex[v])
                            {
                                var o = Norm(fn[j]);
                                if (own[0] * o[0] + own[1] * o[1] + own[2] * o[2] >= cos - 1e-6) { nx += fn[j][0]; ny += fn[j][1]; nz += fn[j][2]; }
                            }
                        }
                    }
                    var nn = Norm(new[] { nx, ny, nz }); oN.AddRange(nn);
                    if (colors.Length / 4 == V) oC.AddRange(new[] { colors[v * 4], colors[v * 4 + 1], colors[v * 4 + 2], colors[v * 4 + 3] });
                    if (uvs.Length / 2 == V) { oU.Add(uvs[v * 2]); oU.Add(uvs[v * 2 + 1]); }
                    oT.Add(i * 3 + k);
                }
                if (!ccw) { int a = oT[oT.Count - 2]; oT[oT.Count - 2] = oT[oT.Count - 1]; oT[oT.Count - 1] = a; }
            }
            return new MeshData { Positions = oP.ToArray(), Normals = oN.ToArray(), Colors = oC.ToArray(), Uvs = oU.ToArray(), Triangles = oT.ToArray() };
        }

        static float[] Cross(float[] P, int a, int b, int c)
        {
            if (a < 0 || b < 0 || c < 0 || a * 3 + 2 >= P.Length || b * 3 + 2 >= P.Length || c * 3 + 2 >= P.Length) return new float[] { 0, 1, 0 };
            float ux = P[b * 3] - P[a * 3], uy = P[b * 3 + 1] - P[a * 3 + 1], uz = P[b * 3 + 2] - P[a * 3 + 2];
            float vx = P[c * 3] - P[a * 3], vy = P[c * 3 + 1] - P[a * 3 + 1], vz = P[c * 3 + 2] - P[a * 3 + 2];
            return new[] { uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx };
        }

        static float[] Norm(float[] n)
        {
            double l = Math.Sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
            return l < 1e-12 ? new float[] { 0, 1, 0 } : new[] { (float)(n[0] / l), (float)(n[1] / l), (float)(n[2] / l) };
        }

        /// <summary>Ear clipping in the polygon's Newell plane; handles concave simple polygons. Bad indices yield no triangles.</summary>
        public static List<int[]> Triangulate(float[] P, int[] poly)
        {
            var res = new List<int[]>();
            foreach (int i in poly) if (i * 3 + 2 >= P.Length) return res;
            if (poly.Length == 3) { res.Add(new[] { poly[0], poly[1], poly[2] }); return res; }
            double nx = 0, ny = 0, nz = 0;
            for (int i = 0; i < poly.Length; i++)
            {
                int a = poly[i] * 3, b = poly[(i + 1) % poly.Length] * 3;
                nx += (P[a + 1] - P[b + 1]) * (P[a + 2] + P[b + 2]);
                ny += (P[a + 2] - P[b + 2]) * (P[a] + P[b]);
                nz += (P[a] - P[b]) * (P[a + 1] + P[b + 1]);
            }
            double ax = Math.Abs(nx), ay = Math.Abs(ny), az = Math.Abs(nz);
            int drop = ax >= ay && ax >= az ? 0 : (ay >= az ? 1 : 2);
            double sign = (drop == 0 ? nx : drop == 1 ? ny : nz) >= 0 ? 1 : -1;
            Func<int, double[]> pr = vi =>
            {
                int o = vi * 3; return drop == 0 ? new double[] { P[o + 1], P[o + 2] } : drop == 1 ? new double[] { P[o + 2], P[o] } : new double[] { P[o], P[o + 1] };
            };
            var idx = new List<int>(poly);
            int guard = 0;
            while (idx.Count > 3 && guard++ < 10000)
            {
                bool clipped = false;
                for (int i = 0; i < idx.Count; i++)
                {
                    int ia = idx[(i + idx.Count - 1) % idx.Count], ib = idx[i], ic = idx[(i + 1) % idx.Count];
                    var a = pr(ia); var b = pr(ib); var c = pr(ic);
                    double cr = ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * sign;
                    if (cr <= 1e-12) continue;
                    bool inside = false;
                    foreach (int j in idx)
                    {
                        if (j == ia || j == ib || j == ic) continue;
                        var p = pr(j);
                        double d1 = ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) * sign;
                        double d2 = ((c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])) * sign;
                        double d3 = ((a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])) * sign;
                        if (d1 >= 0 && d2 >= 0 && d3 >= 0) { inside = true; break; }
                    }
                    if (inside) continue;
                    res.Add(new[] { ia, ib, ic }); idx.RemoveAt(i); clipped = true; break;
                }
                if (!clipped) { // degenerate: fan the rest so no face disappears
                    for (int i = 1; i + 1 < idx.Count; i++) res.Add(new[] { idx[0], idx[i], idx[i + 1] });
                    return res;
                }
            }
            if (idx.Count == 3) res.Add(new[] { idx[0], idx[1], idx[2] });
            return res;
        }
    }
}
