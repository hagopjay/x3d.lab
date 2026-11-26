# io.x3d.unity: the Gobbler's Unity bridge (0.1.0, scaffold)

Import, play, live-link and export X3D in Unity. Pure-C# core (`Runtime/Core`, unit-tested off-device), a thin Unity layer, an optional AR Foundation layer.

| Piece | What it does | Status (see ../docs/UNITY-BRIDGE.md for the grading) |
|---|---|---|
| `Runtime/Core` | XML parser, DEF/USE, tessellator (Box, Sphere, Cylinder, Cone, IndexedFaceSet with crease angles and ear clipping, IndexedTriangleSet, TriangleSet, PointSet), handedness, ROUTE animation (TimeSensor + 4 interpolators), writer, receipt | Confirmed: 41/41 tests, dotnet 8.0.134, C# 9 |
| `Runtime/Unity` | `X3DSceneBuilder`, `X3DPlayer`, `X3DLiveClient`, `X3DExporter`, `X3DPointCloudBaker` | Suspected: written against Unity 6 / 2022.3 APIs, not yet compiled in the Editor |
| `Runtime/XR` | `X3DARPlacer` (AR Foundation 5+, iOS ARKit / Android ARCore) | Suspected: same |
| `Editor` | `.x3d` ScriptedImporter, headless `X3DBuild` for Android / iOS / Windows / WebGL | Suspected: same |

Install (UPM, local): Package Manager, "Add package from disk", pick `package.json` here. Drag `Samples~/hello-xr/hello-xr.x3d` into Assets.
