using System.Collections.Generic;
using UnityEngine;
using X3D.Core;

namespace X3D.UnityBridge
{
    /// <summary>Drop on a GameObject, point at a TextAsset or a URL. Builds the scene and drives ROUTE animation every frame from Time.time.</summary>
    [AddComponentMenu("X3D/X3D Player")]
    public sealed class X3DPlayer : MonoBehaviour
    {
        public TextAsset source;
        public MirrorAxis mirrorAxis = MirrorAxis.Z;
        public bool playOnStart = true;
        [Tooltip("Receipt JSON of the last build (read-only, also logged).")] [TextArea(3, 12)] public string receiptJson;

        public BuildResult Result { get; private set; }
        float t0;

        void Start() { if (source != null) Load(source.text); }

        public void Load(string xml)
        {
            if (Result != null && Result.Root != null) Destroy(Result.Root);
            Result = X3DSceneBuilder.Build(xml, mirrorAxis);
            Result.Root.transform.SetParent(transform, false);
            receiptJson = Result.Receipt.ToJson();
            Debug.Log("[X3D] " + Result.Receipt.Triangles + " tris, " + Result.Receipt.Points + " points, " + Result.Receipt.Gaps.Count + " gaps\n" + receiptJson);
            t0 = Time.time;
        }

        void Update()
        {
            if (Result == null || !playOnStart) return;
            foreach (var u in Result.Animation.Evaluate(Time.time - t0)) Apply(u);
        }

        void Apply(FieldUpdate u)
        {
            string def = u.Node.Def; if (def == null) return;
            Transform tf; Material mat;
            if (Result.Transforms.TryGetValue(def, out tf))
            {
                if (u.Field == "translation") { var p = Convention.Position(u.Value, mirrorAxis); tf.localPosition = new Vector3(p[0], p[1], p[2]); }
                else if (u.Field == "rotation") { var q = Convention.Rotation(u.Value, mirrorAxis); tf.localRotation = new Quaternion(q[0], q[1], q[2], q[3]); }
                else if (u.Field == "scale") tf.localScale = new Vector3(u.Value[0], u.Value[1], u.Value[2]);
            }
            else if (Result.Materials.TryGetValue(def, out mat))
            {
                if (u.Field == "diffuseColor") { var c = new Color(u.Value[0], u.Value[1], u.Value[2], mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor").a : 1f); if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c); if (mat.HasProperty("_Color")) mat.SetColor("_Color", c); }
                else if (u.Field == "transparency") { var c = mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor") : mat.color; c.a = 1f - u.Value[0]; if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c); if (mat.HasProperty("_Color")) mat.SetColor("_Color", c); }
            }
        }
    }
}
