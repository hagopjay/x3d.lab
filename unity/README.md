# x3d.lab/unity: the Gobbler's Unity bridge

`io.x3d.unity/` (UPM package), `tests/` (41 off-device tests), `tools/` (x3d_mcp gate, live server), `samples/`, `docs/UNITY-BRIDGE.md` (the where / how / why / when, XR matrix, honest Unity-vs-Unreal case, paste-ready PC run).
Moves to `hagopjay/x3d.unity` when that repo exists.

```bash
dotnet run -c Release --project tests/X3D.Core.Tests -- samples/scenes/hello-xr.x3d   # 41 passed, 0 failed (dotnet 8.0.134)
X3D_MCP_DIR=~/x3d_mcp tools/test_live.sh                                              # 8/8: gate refuses broken scenes
```
No Keijiro (or other third-party) code is vendored; see docs §9. The Unity-API layer is graded Suspected until compiled in the Editor.
