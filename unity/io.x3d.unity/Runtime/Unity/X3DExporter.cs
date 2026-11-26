using System.Collections.Generic;
using UnityEngine;
using X3D.Core;

namespace X3D.UnityBridge
{
    /// <summary>Unity hierarchy to X3D 4.0 (Transform / Shape / Material / IndexedTriangleSet / lights / Viewpoint). Mirrors the same axis as the importer, so import then export is a round trip.
    /// Unsupported renderers (skinned, particle, terrain) are listed in the returned gap list; nothing is exported silently half-way.</summary>
    public static class X3DExporter
    {
        public static string Export(GameObject root, MirrorAxis axis, out List<string> gaps)
        {
            gaps = new List<string>();
            var scene = new X3DScene { Profile = "Interchange", Version = "4.0" };
            var g = gaps;
            Walk(root.transform, null, scene, axis, g, true);
            return X3DWriter.Write(scene);
        }

        static string F(float v) { return v.ToString("R", System.Globalization.CultureInfo.InvariantCulture); }
        static string V3(Vector3 v) { return F(v.x) + " " + F(v.y) + " " + F(v.z); }

        static void Walk(Transform t, X3DNode parent, X3DScene scene, MirrorAxis axis, List<string> gaps, bool isRoot)
        {
            var n = new X3DNode { Type = "Transform", ContainerField = "children" };
            var p = Convention.Position(new[] { t.localPosition.x, t.localPosition.y, t.localPosition.z }, axis);
            t.localRotation.ToAngleAxis(out float deg, out Vector3 ax);
            var qa = Convention.Rotation(new[] { ax.x, ax.y, ax.z, deg * Mathf.Deg2Rad }, axis); // mirror an axis-angle the same way as the importer, inverse is the same involution
            // Convention.Rotation returns a quaternion; convert it back to axis-angle for X3D
            float w = Mathf.Clamp(qa[3], -1f, 1f); float ang = 2f * Mathf.Acos(w); float s = Mathf.Sqrt(Mathf.Max(0f, 1f - w * w));
            var axisOut = s < 1e-6f ? new[] { 0f, 1f, 0f } : new[] { qa[0] / s, qa[1] / s, qa[2] / s };
            n.Def = Safe(t.name) + "_" + t.GetInstanceID().ToString().Replace("-", "n");
            n.Fields["translation"] = F(p[0]) + " " + F(p[1]) + " " + F(p[2]);
            n.Fields["rotation"] = F(axisOut[0]) + " " + F(axisOut[1]) + " " + F(axisOut[2]) + " " + F(ang);
            n.Fields["scale"] = V3(t.localScale);
            var mf = t.GetComponent<MeshFilter>(); var mr = t.GetComponent<MeshRenderer>();
            if (mf != null && mf.sharedMesh != null && mr != null && mr.sharedMaterial != null) n.Children.Add(Shape(mf.sharedMesh, mr.sharedMaterial, axis));
            if (t.GetComponent<SkinnedMeshRenderer>() != null) gaps.Add("export:SkinnedMeshRenderer " + t.name + " (HAnim lane, not yet)");
            if (t.GetComponent<ParticleSystem>() != null) gaps.Add("export:ParticleSystem " + t.name);
            if (t.GetComponent<Terrain>() != null) gaps.Add("export:Terrain " + t.name);
            var lt = t.GetComponent<Light>();
            if (lt != null && lt.type == LightType.Directional)
            {
                var d = t.forward; var dd = Convention.Position(new[] { d.x, d.y, d.z }, axis);
                var l = new X3DNode { Type = "DirectionalLight", ContainerField = "children" };
                l.Fields["direction"] = F(dd[0]) + " " + F(dd[1]) + " " + F(dd[2]); l.Fields["intensity"] = F(lt.intensity); l.Fields["color"] = F(lt.color.r) + " " + F(lt.color.g) + " " + F(lt.color.b);
                n.Children.Add(l);
            }
            else if (lt != null) gaps.Add("export:" + lt.type + "Light " + t.name + " (only Directional exported in 0.1)");
            for (int i = 0; i < t.childCount; i++) Walk(t.GetChild(i), n, scene, axis, gaps, false);
            if (parent == null) scene.Roots.Add(n); else parent.Children.Add(n);
        }

        static string Safe(string s) { var o = new System.Text.StringBuilder(); foreach (char c in s) o.Append(char.IsLetterOrDigit(c) ? c : '_'); return o.Length == 0 || char.IsDigit(o[0]) ? "n" + o : o.ToString(); }

        static X3DNode Shape(Mesh m, Material mat, MirrorAxis axis)
        {
            var shape = new X3DNode { Type = "Shape", ContainerField = "children" };
            var app = new X3DNode { Type = "Appearance", ContainerField = "appearance" };
            var mt = new X3DNode { Type = "Material", ContainerField = "material" };
            Color c = mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor") : (mat.HasProperty("_Color") ? mat.GetColor("_Color") : Color.gray);
            mt.Fields["diffuseColor"] = F(c.r) + " " + F(c.g) + " " + F(c.b);
            if (c.a < 0.999f) mt.Fields["transparency"] = F(1f - c.a);
            app.Children.Add(mt); shape.Children.Add(app);
            var geo = new X3DNode { Type = "IndexedTriangleSet", ContainerField = "geometry" };
            var tris = m.triangles; var sb = new System.Text.StringBuilder();
            for (int i = 0; i + 2 < tris.Length; i += 3) sb.Append(tris[i] + " " + tris[i + 2] + " " + tris[i + 1] + " "); // undo the importer's winding flip
            geo.Fields["index"] = sb.ToString().Trim();
            var coord = new X3DNode { Type = "Coordinate", ContainerField = "coord" };
            var vs = m.vertices; var pb = new System.Text.StringBuilder();
            foreach (var v in vs) { var q = Convention.Position(new[] { v.x, v.y, v.z }, axis); pb.Append(F(q[0]) + " " + F(q[1]) + " " + F(q[2]) + "  "); }
            coord.Fields["point"] = pb.ToString().Trim(); geo.Children.Add(coord); shape.Children.Add(geo);
            return shape;
        }
    }
}
