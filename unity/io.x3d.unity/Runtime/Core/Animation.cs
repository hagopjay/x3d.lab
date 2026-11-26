using System;
using System.Collections.Generic;

namespace X3D.Core
{
    public sealed class FieldUpdate
    {
        public X3DNode Node; public string Field; public float[] Value;
    }

    /// <summary>Evaluates TimeSensor -> *Interpolator -> field ROUTE chains at an absolute time. Stateless: the host calls Evaluate(t) per frame.
    /// Covers TimeSensor, PositionInterpolator, OrientationInterpolator, ScalarInterpolator, ColorInterpolator, and ROUTE chains of any length.
    /// Anything else (scripts, sensors, CoordinateInterpolator, BooleanFilter...) is named in the gap ledger, never skipped silently.</summary>
    public sealed class AnimationGraph
    {
        public static readonly string[] SupportedSources = { "TimeSensor", "PositionInterpolator", "OrientationInterpolator", "ScalarInterpolator", "ColorInterpolator" };
        readonly X3DScene scene;
        readonly Dictionary<string, List<X3DRoute>> byFrom = new Dictionary<string, List<X3DRoute>>();
        public readonly List<string> Gaps = new List<string>();

        public AnimationGraph(X3DScene s)
        {
            scene = s;
            foreach (var r in s.Routes)
            {
                if (r.FromNode == null || r.ToNode == null) { Gaps.Add("route:incomplete"); continue; }
                List<X3DRoute> l; if (!byFrom.TryGetValue(r.FromNode, out l)) byFrom[r.FromNode] = l = new List<X3DRoute>();
                l.Add(r);
                X3DNode from, to;
                if (!s.Defs.TryGetValue(r.FromNode, out from) || !s.Defs.TryGetValue(r.ToNode, out to)) { Gaps.Add("route:dangling " + r.FromNode + "->" + r.ToNode); continue; }
                if (Array.IndexOf(SupportedSources, from.Type) < 0) Gaps.Add("route-source:" + from.Type);
            }
        }

        public List<FieldUpdate> Evaluate(double time)
        {
            var outs = new List<FieldUpdate>();
            foreach (var kv in scene.Defs)
                if (kv.Value.Type == "TimeSensor")
                {
                    float f; if (!Fraction(kv.Value, time, out f)) continue;
                    Propagate(kv.Key, "fraction_changed", new[] { f }, outs, 0);
                }
            return outs;
        }

        public static bool Fraction(X3DNode ts, double time, out float fraction)
        {
            fraction = 0;
            if (!ts.B("enabled", true)) return false;
            double cycle = ts.F("cycleInterval", 1f), start = ts.F("startTime", 0f), stop = ts.F("stopTime", 0f);
            bool loop = ts.B("loop", false);
            if (cycle <= 0) return false;
            if (time < start) return false;
            if (stop > start && time >= stop) return false;
            double t = (time - start) / cycle;
            if (loop) { t = t - Math.Floor(t); if (t == 0 && time > start) t = 1; }
            else if (t >= 1) t = 1;
            fraction = (float)t; return true;
        }

        void Propagate(string node, string field, float[] value, List<FieldUpdate> outs, int depth)
        {
            if (depth > 16) { Gaps.Add("route:cycle-or-too-deep at " + node); return; }
            List<X3DRoute> rs; if (!byFrom.TryGetValue(node, out rs)) return;
            foreach (var r in rs)
            {
                if (r.FromField != field) continue;
                X3DNode to; if (!scene.Defs.TryGetValue(r.ToNode, out to)) continue;
                if (r.ToField == "set_fraction" || r.ToField == "set_fraction")
                {
                    var v = Interpolate(to, value[0]);
                    if (v != null) Propagate(r.ToNode, "value_changed", v, outs, depth + 1);
                }
                else
                {
                    var f = r.ToField.StartsWith("set_") ? r.ToField.Substring(4) : r.ToField;
                    outs.Add(new FieldUpdate { Node = to, Field = f, Value = value });
                }
            }
        }

        public static float[] Interpolate(X3DNode n, float fr)
        {
            var key = n.Floats("key");
            var kv = n.Floats("keyValue");
            int comps;
            switch (n.Type) { case "PositionInterpolator": case "ColorInterpolator": comps = 3; break; case "OrientationInterpolator": comps = 4; break; case "ScalarInterpolator": comps = 1; break; default: return null; }
            if (key.Length == 0 || kv.Length < key.Length * comps) return null;
            if (fr <= key[0]) return Slice(kv, 0, comps);
            if (fr >= key[key.Length - 1]) return Slice(kv, key.Length - 1, comps);
            int i = 0; while (i + 1 < key.Length && fr >= key[i + 1]) i++;
            float u = (fr - key[i]) / (key[i + 1] - key[i]);
            var a = Slice(kv, i, comps); var b = Slice(kv, i + 1, comps);
            if (n.Type == "OrientationInterpolator") return Slerp(a, b, u);
            var o = new float[comps]; for (int k = 0; k < comps; k++) o[k] = a[k] + (b[k] - a[k]) * u; return o;
        }

        static float[] Slice(float[] v, int i, int c) { var o = new float[c]; Array.Copy(v, i * c, o, 0, c); return o; }

        /// <summary>Shortest-path slerp of two axis-angle rotations, returned as axis-angle.</summary>
        public static float[] Slerp(float[] a, float[] b, float u)
        {
            var qa = Quat(a); var qb = Quat(b);
            double d = qa[0] * qb[0] + qa[1] * qb[1] + qa[2] * qb[2] + qa[3] * qb[3];
            if (d < 0) { for (int i = 0; i < 4; i++) qb[i] = -qb[i]; d = -d; }
            double[] q = new double[4];
            if (d > 0.9995) { for (int i = 0; i < 4; i++) q[i] = qa[i] + (qb[i] - qa[i]) * u; }
            else
            {
                double th = Math.Acos(d), s = Math.Sin(th), w1 = Math.Sin((1 - u) * th) / s, w2 = Math.Sin(u * th) / s;
                for (int i = 0; i < 4; i++) q[i] = qa[i] * w1 + qb[i] * w2;
            }
            double l = Math.Sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]); for (int i = 0; i < 4; i++) q[i] /= l;
            double ang = 2 * Math.Acos(Math.Max(-1, Math.Min(1, q[3]))), sn = Math.Sqrt(Math.Max(0, 1 - q[3] * q[3]));
            if (sn < 1e-9) return new float[] { 0, 1, 0, 0 };
            return new[] { (float)(q[0] / sn), (float)(q[1] / sn), (float)(q[2] / sn), (float)ang };
        }

        static double[] Quat(float[] r)
        {
            double l = Math.Sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]); if (l < 1e-12) return new double[] { 0, 0, 0, 1 };
            double s = Math.Sin(r[3] / 2) / l; return new[] { r[0] * s, r[1] * s, r[2] * s, Math.Cos(r[3] / 2) };
        }
    }
}
