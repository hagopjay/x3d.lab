using System;
using System.Collections.Generic;

namespace X3D.Core
{
    public sealed class MeshData
    {
        public float[] Positions = new float[0];   // xyz
        public float[] Normals = new float[0];     // xyz or empty
        public float[] Colors = new float[0];      // rgba or empty
        public float[] Uvs = new float[0];         // uv or empty
        public int[] Triangles = new int[0];
        public bool IsPoints;                      // PointSet: Positions/Colors only
        public bool DoubleSided;                   // solid=FALSE
        public int VertexCount { get { return Positions.Length / 3; } }
        public int TriangleCount { get { return Triangles.Length / 3; } }
    }

    /// <summary>Which axis to mirror when going from X3D (right-handed, Y up, camera -Z) to Unity (left-handed, Y up).
    /// Z (default) keeps "camera at +Z looking -Z" as "camera at -Z looking +Z", matching Unity's camera. X is what glTFast does for glTF.
    /// The two differ by a 180-degree turn about Y: a scene imported via glTFast and one imported natively face opposite ways (Confirmed by math, see tests).</summary>
    public enum MirrorAxis { Z = 0, X = 1 }

    public static class Convention
    {
        public static float[] Position(float[] p, MirrorAxis a)
        {
            return a == MirrorAxis.Z ? new[] { p[0], p[1], -p[2] } : new[] { -p[0], p[1], p[2] };
        }

        /// <summary>X3D axis-angle (ax,ay,az,angle) to Unity quaternion (x,y,z,w). A mirror turns an axis into a pseudovector: the two in-plane components flip.</summary>
        public static float[] Rotation(float[] r, MirrorAxis a)
        {
            double len = Math.Sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]);
            if (len < 1e-12) return new float[] { 0, 0, 0, 1 };
            double s = Math.Sin(r[3] / 2) / len, c = Math.Cos(r[3] / 2);
            double x = r[0] * s, y = r[1] * s, z = r[2] * s;
            return a == MirrorAxis.Z ? new[] { (float)-x, (float)-y, (float)z, (float)c } : new[] { (float)x, (float)-y, (float)-z, (float)c };
        }

        public static MeshData ToUnity(MeshData m, MirrorAxis a)
        {
            var o = new MeshData { IsPoints = m.IsPoints, DoubleSided = m.DoubleSided, Colors = m.Colors, Uvs = m.Uvs };
            o.Positions = (float[])m.Positions.Clone();
            o.Normals = (float[])m.Normals.Clone();
            int k = a == MirrorAxis.Z ? 2 : 0;
            for (int i = k; i < o.Positions.Length; i += 3) o.Positions[i] = -o.Positions[i];
            for (int i = k; i < o.Normals.Length; i += 3) o.Normals[i] = -o.Normals[i];
            var t = (int[])m.Triangles.Clone();
            for (int i = 0; i + 2 < t.Length; i += 3) { int tmp = t[i + 1]; t[i + 1] = t[i + 2]; t[i + 2] = tmp; } // a mirror reverses winding
            o.Triangles = t;
            return o;
        }

        /// <summary>Signed volume by the divergence theorem. Positive for outward-facing counter-clockwise (right-handed) triangles.</summary>
        public static double SignedVolume(MeshData m)
        {
            double v = 0;
            for (int i = 0; i + 2 < m.Triangles.Length; i += 3)
            {
                int a = m.Triangles[i] * 3, b = m.Triangles[i + 1] * 3, c = m.Triangles[i + 2] * 3;
                var P = m.Positions;
                v += (P[a] * (P[b + 1] * P[c + 2] - P[b + 2] * P[c + 1])
                    - P[a + 1] * (P[b] * P[c + 2] - P[b + 2] * P[c])
                    + P[a + 2] * (P[b] * P[c + 1] - P[b + 1] * P[c])) / 6.0;
            }
            return v;
        }
    }
}
