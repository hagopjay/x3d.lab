using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace X3D.Core
{
    /// <summary>The tether: every import returns a receipt that names what was drawn, what was not, and the hash of the source.
    /// A gap is a node or field the bridge saw and did not render. No silent drops.</summary>
    public sealed class Receipt
    {
        public string Sha256;
        public string Version, Profile;
        public int Triangles, Points;
        public readonly SortedDictionary<string, int> NodeCounts = new SortedDictionary<string, int>();
        public readonly SortedSet<string> Gaps = new SortedSet<string>();
        public readonly SortedSet<string> Drawn = new SortedSet<string>();

        public static readonly string[] SupportedNodes =
        { "Transform", "Group", "Switch", "Shape", "Appearance", "Material", "UnlitMaterial", "Viewpoint", "DirectionalLight", "PointLight", "SpotLight",
          "NavigationInfo", "Background", "WorldInfo", "Inline",
          "Box", "Sphere", "Cylinder", "Cone", "IndexedFaceSet", "IndexedTriangleSet", "TriangleSet", "PointSet", "Coordinate", "Normal", "Color", "ColorRGBA", "TextureCoordinate",
          "TimeSensor", "PositionInterpolator", "OrientationInterpolator", "ScalarInterpolator", "ColorInterpolator",
          "MetadataString", "MetadataFloat", "MetadataInteger", "MetadataBoolean", "MetadataDouble", "MetadataSet" };

        public static Receipt Build(string xml, X3DScene scene, IEnumerable<string> extraGaps, int triangles, int points)
        {
            var r = new Receipt { Version = scene.Version, Profile = scene.Profile, Triangles = triangles, Points = points };
            using (var sha = SHA256.Create()) r.Sha256 = string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(xml)).Select(b => b.ToString("x2")));
            Action<X3DNode> walk = null;
            walk = n =>
            {
                if (n.UseName != null) { r.Count("USE"); return; }
                r.Count(n.Type);
                if (Array.IndexOf(SupportedNodes, n.Type) < 0) r.Gaps.Add("node:" + n.Type); else r.Drawn.Add(n.Type);
                foreach (var c in n.Children) walk(c);
            };
            foreach (var n in scene.Roots) walk(n);
            foreach (var w in scene.Warnings) r.Gaps.Add("warning:" + w);
            if (extraGaps != null) foreach (var g in extraGaps) r.Gaps.Add(g);
            return r;
        }

        void Count(string t) { int c; NodeCounts.TryGetValue(t, out c); NodeCounts[t] = c + 1; }

        public string ToJson()
        {
            var sb = new StringBuilder();
            sb.Append("{\n  \"schema\": \"x3d-unity-receipt/v1\",\n  \"sha256\": \"" + Sha256 + "\",\n  \"x3d_version\": \"" + Version + "\",\n  \"profile\": \"" + Profile + "\",\n");
            sb.Append("  \"triangles\": " + Triangles + ",\n  \"points\": " + Points + ",\n  \"node_counts\": {");
            sb.Append(string.Join(", ", NodeCounts.Select(kv => "\"" + kv.Key + "\": " + kv.Value)));
            sb.Append("},\n  \"gaps\": [" + string.Join(", ", Gaps.Select(g => "\"" + g.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"")) + "]\n}\n");
            return sb.ToString();
        }
    }
}
