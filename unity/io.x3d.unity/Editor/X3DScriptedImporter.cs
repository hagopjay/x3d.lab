using UnityEditor.AssetImporters;
using UnityEngine;
using X3D.Core;

namespace X3D.UnityBridge.Editor
{
    /// <summary>Drag a .x3d file into Assets: it becomes a prefab-like GameObject with meshes, materials, lights and viewpoint cameras as sub-assets,
    /// plus a receipt text asset beside it that names every node the bridge did not draw.</summary>
    [ScriptedImporter(1, "x3d")]
    public sealed class X3DScriptedImporter : ScriptedImporter
    {
        public MirrorAxis mirrorAxis = MirrorAxis.Z;

        public override void OnImportAsset(AssetImportContext ctx)
        {
            var xml = System.IO.File.ReadAllText(ctx.assetPath);
            BuildResult r;
            try { r = X3DSceneBuilder.Build(xml, mirrorAxis); }
            catch (System.Exception e) { ctx.LogImportError("X3D parse failed: " + e.Message); return; }
            ctx.AddObjectToAsset("root", r.Root);
            ctx.SetMainObject(r.Root);
            foreach (var mf in r.Root.GetComponentsInChildren<MeshFilter>()) ctx.AddObjectToAsset("mesh_" + mf.GetInstanceID(), mf.sharedMesh);
            foreach (var mr in r.Root.GetComponentsInChildren<MeshRenderer>()) ctx.AddObjectToAsset("mat_" + mr.GetInstanceID(), mr.sharedMaterial);
            ctx.AddObjectToAsset("receipt", new TextAsset(r.Receipt.ToJson()) { name = "receipt.json" });
            foreach (var g in r.Receipt.Gaps) ctx.LogImportWarning("X3D gap: " + g);
        }
    }
}
