// X3D.Core: pure C# (no UnityEngine). Compiles under Unity (C# 9) and under plain dotnet, so it is unit-tested off-device.
using System;
using System.Collections.Generic;
using System.Globalization;

namespace X3D.Core
{
    public sealed class X3DNode
    {
        public string Type;
        public string Def;
        public string ContainerField;
        /// <summary>Set when this element was USE="name"; <see cref="Resolve"/> returns the DEF'd node.</summary>
        public string UseName;
        public X3DNode UseTarget;
        public readonly Dictionary<string, string> Fields = new Dictionary<string, string>();
        public readonly List<X3DNode> Children = new List<X3DNode>();

        public X3DNode Resolve() { return UseTarget ?? this; }

        public string Get(string field, string fallback = null)
        {
            string v;
            return Resolve().Fields.TryGetValue(field, out v) ? v : fallback;
        }

        public bool Has(string field) { return Resolve().Fields.ContainsKey(field); }

        public IEnumerable<X3DNode> In(string containerField)
        {
            foreach (var c in Resolve().Children) if (c.ContainerField == containerField) yield return c;
        }

        public X3DNode First(string containerField)
        {
            foreach (var c in In(containerField)) return c;
            return null;
        }

        public float F(string field, float fallback) { var s = Get(field); return s == null ? fallback : (float)Parse.Double(s); }
        public bool B(string field, bool fallback) { var s = Get(field); return s == null ? fallback : s.Trim() == "true" || s.Trim() == "TRUE"; }
        public float[] Floats(string field) { var s = Get(field); return s == null ? new float[0] : Parse.Floats(s); }
        public int[] Ints(string field) { var s = Get(field); return s == null ? new int[0] : Parse.Ints(s); }
        public float[] Vec(string field, float[] fallback) { var f = Floats(field); return f.Length == fallback.Length ? f : fallback; }

        public static string DefaultContainerField(string type)
        {
            switch (type)
            {
                case "Appearance": return "appearance";
                case "Material": case "PhysicalMaterial": case "UnlitMaterial": return "material";
                case "ImageTexture": case "PixelTexture": case "MovieTexture": return "texture";
                case "TextureTransform": return "textureTransform";
                case "Coordinate": case "CoordinateDouble": return "coord";
                case "Normal": return "normal";
                case "Color": case "ColorRGBA": return "color";
                case "TextureCoordinate": return "texCoord";
                case "Box": case "Sphere": case "Cylinder": case "Cone": case "IndexedFaceSet": case "IndexedTriangleSet":
                case "TriangleSet": case "PointSet": case "IndexedLineSet": case "LineSet": case "ElevationGrid": case "Extrusion":
                case "Text": return "geometry";
                case "FontStyle": return "fontStyle";
                default: return "children";
            }
        }
    }

    public sealed class X3DRoute
    {
        public string FromNode, FromField, ToNode, ToField;
    }

    public sealed class X3DScene
    {
        public string Version = "4.0";
        public string Profile = "Interchange";
        public readonly List<X3DNode> Roots = new List<X3DNode>();
        public readonly Dictionary<string, X3DNode> Defs = new Dictionary<string, X3DNode>();
        public readonly List<X3DRoute> Routes = new List<X3DRoute>();
        public readonly List<string> Warnings = new List<string>();
    }

    public static class Parse
    {
        static readonly char[] Sep = { ' ', ',', '\t', '\r', '\n' };
        public static double Double(string s) { return double.Parse(s, NumberStyles.Float, CultureInfo.InvariantCulture); }
        public static float[] Floats(string s)
        {
            var parts = s.Split(Sep, StringSplitOptions.RemoveEmptyEntries);
            var r = new float[parts.Length];
            for (int i = 0; i < parts.Length; i++) r[i] = (float)Double(parts[i]);
            return r;
        }
        public static int[] Ints(string s)
        {
            var parts = s.Split(Sep, StringSplitOptions.RemoveEmptyEntries);
            var r = new int[parts.Length];
            for (int i = 0; i < parts.Length; i++) r[i] = int.Parse(parts[i], CultureInfo.InvariantCulture);
            return r;
        }
        public static string Fmt(float[] v)
        {
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < v.Length; i++) { if (i > 0) sb.Append(' '); sb.Append(v[i].ToString("R", CultureInfo.InvariantCulture)); }
            return sb.ToString();
        }
    }
}
