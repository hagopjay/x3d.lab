using UnityEngine;
using X3D.Core;
#if X3D_VFX
using UnityEngine.VFX;
#endif

namespace X3D.UnityBridge
{
    /// <summary>PointSet to two float textures (positions, colours): the "point cloud as texture" pattern that makes millions of points a GPU-only draw in VFX Graph.
    /// The pattern is the one popularised in Unity by Keijiro Takahashi's point-cloud VFX Graph samples (Pcx / PcxVFX, Rsvfx); this file is original code and vendors none of theirs.
    /// Create a VFX Graph asset with Texture2D properties PositionMap and ColorMap and an int PointCount; assign it to the Visual Effect on this object.</summary>
    public static class X3DPointCloudBaker
    {
        public static void Bake(MeshData points, out Texture2D positionMap, out Texture2D colorMap, out int count)
        {
            count = points.VertexCount;
            int w = Mathf.CeilToInt(Mathf.Sqrt(Mathf.Max(1, count))), h = Mathf.CeilToInt(count / (float)w);
            positionMap = new Texture2D(w, h, TextureFormat.RGBAFloat, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp, name = "X3D PositionMap" };
            colorMap = new Texture2D(w, h, TextureFormat.RGBAHalf, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Clamp, name = "X3D ColorMap" };
            var pc = new Color[w * h]; var cc = new Color[w * h];
            for (int i = 0; i < count; i++)
            {
                pc[i] = new Color(points.Positions[i * 3], points.Positions[i * 3 + 1], points.Positions[i * 3 + 2], 1f);
                cc[i] = points.Colors.Length / 4 == count ? new Color(points.Colors[i * 4], points.Colors[i * 4 + 1], points.Colors[i * 4 + 2], points.Colors[i * 4 + 3]) : Color.white;
            }
            positionMap.SetPixels(pc); colorMap.SetPixels(cc); positionMap.Apply(false, true); colorMap.Apply(false, true);
        }

#if X3D_VFX
        public static void Apply(VisualEffect vfx, MeshData points)
        {
            Texture2D p, c; int n; Bake(points, out p, out c, out n);
            vfx.SetTexture("PositionMap", p); vfx.SetTexture("ColorMap", c); vfx.SetInt("PointCount", n);
        }
#endif
    }
}
