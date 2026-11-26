using System;

namespace X3D.Core
{
    /// <summary>X3D Transform to a local TRS in Unity space. Exact when center = 0 and scaleOrientation = identity (the case in nearly all files);
    /// otherwise the full matrix T C R SR S -SR -C is composed and decomposed, and skew that Unity cannot hold is reported.</summary>
    public sealed class Trs
    {
        public float[] T = { 0, 0, 0 }; public float[] Q = { 0, 0, 0, 1 }; public float[] S = { 1, 1, 1 }; public bool SkewLost;
    }

    public static class Xform
    {
        public static Trs FromTransform(X3DNode n, MirrorAxis axis)
        {
            var t = n.Vec("translation", new[] { 0f, 0f, 0f });
            var r = n.Vec("rotation", new[] { 0f, 0f, 1f, 0f });
            var s = n.Vec("scale", new[] { 1f, 1f, 1f });
            var c = n.Vec("center", new[] { 0f, 0f, 0f });
            var so = n.Vec("scaleOrientation", new[] { 0f, 0f, 1f, 0f });
            bool simple = IsZero(c) && Math.Abs(so[3]) < 1e-9;
            var o = new Trs();
            if (simple)
            {
                o.T = Convention.Position(t, axis); o.Q = Convention.Rotation(r, axis); o.S = s; return o;
            }
            // M = T * C * R * SR * S * SR^-1 * C^-1 in X3D space
            var M = Mul(Trans(t), Mul(Trans(c), Mul(RotM(r), Mul(RotM(so), Mul(Scl(s), Mul(RotM(new[] { so[0], so[1], so[2], -so[3] }), Trans(new[] { -c[0], -c[1], -c[2] })))))));
            // reflect: M' = F M F with F = diag mirror
            double[] F = axis == MirrorAxis.Z ? new double[] { 1, 1, -1 } : new double[] { -1, 1, 1 };
            var Mr = (double[,])M.Clone();
            for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) Mr[i, j] *= F[i] * F[j];
            for (int i = 0; i < 3; i++) Mr[i, 3] *= F[i];
            return Decompose(Mr);
        }

        static bool IsZero(float[] v) { return Math.Abs(v[0]) + Math.Abs(v[1]) + Math.Abs(v[2]) < 1e-9; }
        static double[,] Id() { var m = new double[4, 4]; for (int i = 0; i < 4; i++) m[i, i] = 1; return m; }
        static double[,] Trans(float[] t) { var m = Id(); m[0, 3] = t[0]; m[1, 3] = t[1]; m[2, 3] = t[2]; return m; }
        static double[,] Scl(float[] s) { var m = Id(); m[0, 0] = s[0]; m[1, 1] = s[1]; m[2, 2] = s[2]; return m; }
        static double[,] RotM(float[] r)
        {
            double l = Math.Sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]); var m = Id(); if (l < 1e-12) return m;
            double x = r[0] / l, y = r[1] / l, z = r[2] / l, c = Math.Cos(r[3]), s = Math.Sin(r[3]), k = 1 - c;
            m[0, 0] = c + x * x * k; m[0, 1] = x * y * k - z * s; m[0, 2] = x * z * k + y * s;
            m[1, 0] = y * x * k + z * s; m[1, 1] = c + y * y * k; m[1, 2] = y * z * k - x * s;
            m[2, 0] = z * x * k - y * s; m[2, 1] = z * y * k + x * s; m[2, 2] = c + z * z * k;
            return m;
        }
        static double[,] Mul(double[,] a, double[,] b)
        {
            var m = new double[4, 4];
            for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) { double s = 0; for (int k = 0; k < 4; k++) s += a[i, k] * b[k, j]; m[i, j] = s; }
            return m;
        }

        public static Trs Decompose(double[,] m)
        {
            var o = new Trs(); o.T = new[] { (float)m[0, 3], (float)m[1, 3], (float)m[2, 3] };
            var sc = new double[3]; var col = new double[3][];
            for (int j = 0; j < 3; j++) { col[j] = new[] { m[0, j], m[1, j], m[2, j] }; sc[j] = Math.Sqrt(col[j][0] * col[j][0] + col[j][1] * col[j][1] + col[j][2] * col[j][2]); }
            double det = col[0][0] * (col[1][1] * col[2][2] - col[1][2] * col[2][1]) - col[1][0] * (col[0][1] * col[2][2] - col[0][2] * col[2][1]) + col[2][0] * (col[0][1] * col[1][2] - col[0][2] * col[1][1]);
            if (det < 0) sc[0] = -sc[0];
            var R = new double[3, 3];
            for (int j = 0; j < 3; j++) for (int i = 0; i < 3; i++) R[i, j] = col[j][i] / (sc[j] == 0 ? 1 : sc[j]);
            double d01 = R[0, 0] * R[0, 1] + R[1, 0] * R[1, 1] + R[2, 0] * R[2, 1];
            double d02 = R[0, 0] * R[0, 2] + R[1, 0] * R[1, 2] + R[2, 0] * R[2, 2];
            double d12 = R[0, 1] * R[0, 2] + R[1, 1] * R[1, 2] + R[2, 1] * R[2, 2];
            o.SkewLost = Math.Abs(d01) + Math.Abs(d02) + Math.Abs(d12) > 1e-4;
            o.S = new[] { (float)sc[0], (float)sc[1], (float)sc[2] };
            double tr = R[0, 0] + R[1, 1] + R[2, 2], qx, qy, qz, qw;
            if (tr > 0) { double s = Math.Sqrt(tr + 1) * 2; qw = s / 4; qx = (R[2, 1] - R[1, 2]) / s; qy = (R[0, 2] - R[2, 0]) / s; qz = (R[1, 0] - R[0, 1]) / s; }
            else if (R[0, 0] > R[1, 1] && R[0, 0] > R[2, 2]) { double s = Math.Sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2; qw = (R[2, 1] - R[1, 2]) / s; qx = s / 4; qy = (R[0, 1] + R[1, 0]) / s; qz = (R[0, 2] + R[2, 0]) / s; }
            else if (R[1, 1] > R[2, 2]) { double s = Math.Sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2; qw = (R[0, 2] - R[2, 0]) / s; qx = (R[0, 1] + R[1, 0]) / s; qy = s / 4; qz = (R[1, 2] + R[2, 1]) / s; }
            else { double s = Math.Sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2; qw = (R[1, 0] - R[0, 1]) / s; qx = (R[0, 2] + R[2, 0]) / s; qy = (R[1, 2] + R[2, 1]) / s; qz = s / 4; }
            o.Q = new[] { (float)qx, (float)qy, (float)qz, (float)qw };
            return o;
        }
    }
}
