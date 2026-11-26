using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using X3D.Core;

namespace X3D.UnityBridge
{
    public sealed class BuildResult
    {
        public GameObject Root;
        public Receipt Receipt;
        public AnimationGraph Animation;
        public readonly Dictionary<string, Transform> Transforms = new Dictionary<string, Transform>();
        public readonly Dictionary<string, Material> Materials = new Dictionary<string, Material>();
        public readonly List<Camera> Viewpoints = new List<Camera>();
    }

    /// <summary>X3D text to a GameObject tree. Pure function of (xml, options); the receipt says what was and was not drawn.</summary>
    public static class X3DSceneBuilder
    {
        public static BuildResult Build(string xml, MirrorAxis axis = MirrorAxis.Z, Shader shaderOverride = null)
        {
            var scene = X3DParser.Parse(xml);
            var res = new BuildResult { Root = new GameObject("X3D") };
            var gaps = new List<string>(); int tris = 0, pts = 0;
            foreach (var n in scene.Roots) Add(n, res.Root.transform, axis, res, gaps, ref tris, ref pts, shaderOverride, scene);
            res.Animation = new AnimationGraph(scene);
            gaps.AddRange(res.Animation.Gaps);
            res.Receipt = Receipt.Build(xml, scene, gaps, tris, pts);
            return res;
        }

        static void Add(X3DNode raw, Transform parent, MirrorAxis axis, BuildResult res, List<string> gaps, ref int tris, ref int pts, Shader sh, X3DScene scene)
        {
            var n = raw.Resolve();
            switch (n.Type)
            {
                case "Transform":
                case "Group":
                case "Switch":
                {
                    var go = new GameObject(raw.Def ?? n.Def ?? n.Type); go.transform.SetParent(parent, false);
                    if (n.Type == "Transform")
                    {
                        var t = Xform.FromTransform(n, axis);
                        go.transform.localPosition = new Vector3(t.T[0], t.T[1], t.T[2]);
                        go.transform.localRotation = new Quaternion(t.Q[0], t.Q[1], t.Q[2], t.Q[3]);
                        go.transform.localScale = new Vector3(t.S[0], t.S[1], t.S[2]);
                        if (t.SkewLost) gaps.Add("transform:skew-lost " + (n.Def ?? ""));
                    }
                    if (n.Def != null) res.Transforms[n.Def] = go.transform;
                    int which = n.Type == "Switch" ? (int)n.F("whichChoice", -1) : int.MaxValue;
                    int i = 0;
                    foreach (var c in n.Children)
                    {
                        if (n.Type == "Switch" && i++ != which) continue;
                        Add(c, go.transform, axis, res, gaps, ref tris, ref pts, sh, scene);
                    }
                    break;
                }
                case "Shape": AddShape(n, parent, axis, res, gaps, ref tris, ref pts, sh); break;
                case "Viewpoint":
                {
                    var go = new GameObject("VP " + (n.Get("description") ?? n.Def ?? "")); go.transform.SetParent(parent, false);
                    var p = Convention.Position(n.Vec("position", new[] { 0f, 0f, 10f }), axis);
                    var q = Convention.Rotation(n.Vec("orientation", new[] { 0f, 0f, 1f, 0f }), axis);
                    go.transform.localPosition = new Vector3(p[0], p[1], p[2]); go.transform.localRotation = new Quaternion(q[0], q[1], q[2], q[3]);
                    var cam = go.AddComponent<Camera>(); cam.enabled = false; cam.fieldOfView = n.F("fieldOfView", 0.7854f) * Mathf.Rad2Deg;
                    res.Viewpoints.Add(cam);
                    break;
                }
                case "DirectionalLight": case "PointLight": case "SpotLight":
                {
                    var go = new GameObject(n.Type); go.transform.SetParent(parent, false);
                    var l = go.AddComponent<Light>(); var c = n.Vec("color", new[] { 1f, 1f, 1f });
                    l.color = new Color(c[0], c[1], c[2]); l.intensity = n.F("intensity", 1f);
                    l.enabled = n.B("on", true);
                    if (n.Type == "DirectionalLight")
                    {
                        l.type = LightType.Directional; var d = Convention.Position(n.Vec("direction", new[] { 0f, 0f, -1f }), axis);
                        go.transform.rotation = Quaternion.LookRotation(new Vector3(d[0], d[1], d[2]));
                    }
                    else
                    {
                        var p = Convention.Position(n.Vec("location", new[] { 0f, 0f, 0f }), axis); go.transform.localPosition = new Vector3(p[0], p[1], p[2]);
                        l.range = n.F("radius", 100f);
                        if (n.Type == "SpotLight")
                        {
                            l.type = LightType.Spot; var d = Convention.Position(n.Vec("direction", new[] { 0f, 0f, -1f }), axis);
                            go.transform.rotation = Quaternion.LookRotation(new Vector3(d[0], d[1], d[2])); l.spotAngle = n.F("cutOffAngle", 0.7854f) * 2f * Mathf.Rad2Deg;
                        }
                        else l.type = LightType.Point;
                    }
                    break;
                }
                case "Background":
                {
                    var c = n.Vec("skyColor", new[] { 0f, 0f, 0f });
                    RenderSettings.ambientMode = AmbientMode.Flat; RenderSettings.ambientLight = new Color(c[0], c[1], c[2]) * 0.8f;
                    break;
                }
                case "Inline": gaps.Add("Inline:url not fetched at import (use X3DLiveClient to load " + n.Get("url") + ")"); break;
                case "NavigationInfo": case "WorldInfo": break;
                case "TimeSensor": case "PositionInterpolator": case "OrientationInterpolator": case "ScalarInterpolator": case "ColorInterpolator": break; // driven by AnimationGraph
                default:
                    if (Array.IndexOf(Receipt.SupportedNodes, n.Type) < 0) { /* named in Receipt */ }
                    break;
            }
        }

        static void AddShape(X3DNode shape, Transform parent, MirrorAxis axis, BuildResult res, List<string> gaps, ref int tris, ref int pts, Shader sh)
        {
            var geo = shape.First("geometry"); if (geo == null) return;
            var md = Tessellator.Build(geo, gaps); if (md == null) return;
            md = Convention.ToUnity(md, axis);
            var go = new GameObject(shape.Def ?? geo.Resolve().Type); go.transform.SetParent(parent, false);
            var mesh = new Mesh { name = go.name, indexFormat = IndexFormat.UInt32 };
            mesh.vertices = ToV3(md.Positions);
            if (md.Normals.Length == md.Positions.Length) mesh.normals = ToV3(md.Normals);
            if (md.Colors.Length / 4 == md.VertexCount) { var c = new Color[md.VertexCount]; for (int i = 0; i < c.Length; i++) c[i] = new Color(md.Colors[i * 4], md.Colors[i * 4 + 1], md.Colors[i * 4 + 2], md.Colors[i * 4 + 3]); mesh.colors = c; }
            if (md.Uvs.Length / 2 == md.VertexCount) { var u = new Vector2[md.VertexCount]; for (int i = 0; i < u.Length; i++) u[i] = new Vector2(md.Uvs[i * 2], md.Uvs[i * 2 + 1]); mesh.uv = u; }
            if (md.IsPoints)
            {
                var idx = new int[md.VertexCount]; for (int i = 0; i < idx.Length; i++) idx[i] = i;
                mesh.SetIndices(idx, MeshTopology.Points, 0); pts += md.VertexCount;
            }
            else { mesh.triangles = md.Triangles; tris += md.TriangleCount; }
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            mr.sharedMaterial = MakeMaterial(shape.First("appearance"), md, res, sh);
        }

        static Vector3[] ToV3(float[] a) { var v = new Vector3[a.Length / 3]; for (int i = 0; i < v.Length; i++) v[i] = new Vector3(a[i * 3], a[i * 3 + 1], a[i * 3 + 2]); return v; }

        public static Shader DefaultShader()
        {
            var rp = GraphicsSettings.currentRenderPipeline;                 // never stripped from device builds, unlike Shader.Find
            if (rp != null && rp.defaultShader != null) return rp.defaultShader;
            return Shader.Find("Standard");
        }

        static Material MakeMaterial(X3DNode appearance, MeshData md, BuildResult res, Shader sh)
        {
            var mat = new Material(sh ?? DefaultShader());
            var m = appearance != null ? appearance.First("material") : null;
            Color col = new Color(0.8f, 0.8f, 0.8f, 1f), emis = Color.black; float transp = 0;
            if (m != null)
            {
                var d = m.Vec("diffuseColor", new[] { 0.8f, 0.8f, 0.8f }); col = new Color(d[0], d[1], d[2]);
                var e = m.Vec("emissiveColor", new[] { 0f, 0f, 0f }); emis = new Color(e[0], e[1], e[2]);
                transp = m.F("transparency", 0f);
                if (m.Def != null) res.Materials[m.Def] = mat;
            }
            col.a = 1f - transp;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", col);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", col);
            if (emis.maxColorComponent > 0 && mat.HasProperty("_EmissionColor")) { mat.EnableKeyword("_EMISSION"); mat.SetColor("_EmissionColor", emis); }
            if (transp > 0.001f)
            {   // URP Lit transparent; built-in Standard fade
                if (mat.HasProperty("_Surface")) { mat.SetFloat("_Surface", 1); mat.SetFloat("_Blend", 0); mat.SetOverrideTag("RenderType", "Transparent"); mat.SetInt("_SrcBlend", (int)BlendMode.SrcAlpha); mat.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha); mat.SetInt("_ZWrite", 0); mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT"); }
                else if (mat.HasProperty("_Mode")) { mat.SetFloat("_Mode", 2); mat.SetInt("_SrcBlend", (int)BlendMode.SrcAlpha); mat.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha); mat.SetInt("_ZWrite", 0); mat.EnableKeyword("_ALPHABLEND_ON"); }
                mat.renderQueue = (int)RenderQueue.Transparent;
            }
            if (md.DoubleSided && mat.HasProperty("_Cull")) mat.SetInt("_Cull", (int)CullMode.Off);
            return mat;
        }
    }
}
