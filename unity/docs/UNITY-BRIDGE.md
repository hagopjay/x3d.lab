# The Gobbler's Unity bridge: where, how, why, when

Status 26 Nov 2026. Package `io.x3d.unity` 0.1.0 in `x3d.lab/unity/` until `hagopjay/x3d.unity` exists. Demo number: **80** (reserved; no demo page yet, see §8).
Grades: **C** Confirmed (ran here, number named), **L** Likely (reasoned or one secondary source), **S** Suspected (written, not run).
Rig for every number: .NET SDK 8.0.134 (Ubuntu 24.04, C# 9 enforced), x3d_mcp fork `4d433af` (XSD 4.0 + semantic validators imported directly), node 22. No Unity Editor ran in this session (none on the cloud box), so every Unity-API claim is **S** until §7's block is run on DESKTOP-JNLJ2MT.

## 1. The one-line answer
Unity is the **reach lane**: the bridge that puts a validated X3D scene on the phone and the headset in someone's hand (iOS, Android, Quest, visionOS) at real-world scale. Unreal stays the **fidelity lane** (stills, Lumen, path tracer). Blender stays the **authoring lane**. three.js / Babylon / X_ITE stay the **browser lanes**. Unity is the only lane whose job is "walk around it, in AR, on a device you already own".

## 2. Where Unity sits beside the other bridges
```
                         X3D (validated: x3d_mcp XSD + semantic = THE TETHER)
                                          |
   +-----------+-----------+--------------+--------------+------------+-------------+
   |           |           |              |              |            |             |
 X_ITE     three.js    Babylon       Blender (add-on)   glTF (Loom)  Atlas/splats  >>> UNITY <<<
 browser   browser     browser       author + Cycles    exchange      World Labs    device runtime
 (oracle)  (D3/BVH)    (WebGPU)      (blenderizer)      KHR_xmp       Marble        AR / VR / MR
                                          |                |                           ^
                                          +--> Unreal 5.8 fidelity (x3d.lab nb 07/08)  |
                                          +--> glTF --> glTFast --> Unity (path B) -----+
```
| Lane | Job | Output | Unity's relation |
|---|---|---|---|
| X_ITE | reference yardstick, the oracle | pixels in a browser | Unity is compared against it, never replaces it |
| three.js / Babylon | browser reach, WebGPU | pages | Unity WebGL exists but is the weakest Unity target (L) |
| Blender add-on | author, bake light, Cycles stills | X3D / glb | feeds Unity through path B, and path A for plain scenes |
| Unreal lane | highest fidelity stills | PNG, video | complementary: Unity is where you *stand* in the scene |
| Loom (glTF) | format-agnostic semantics (KHR_xmp_json_ld) | glb + packets | path B carries the packets; Unity reads them as extras |
| Atlas / splats | photoreal worlds | .spz / splat glb | path C: splats go to a Unity splat renderer, not our mesh path |

## 3. Four paths in, one tether
| Path | Route | When to use | Gaps it keeps | Grade |
|---|---|---|---|---|
| **A native** | `.x3d` -> `X3D.Core` -> Unity objects | validated scenes, ROUTE animation, live link, receipts | textures, Text, Extrusion, scripts (§6) | Core **C**, Unity layer **S** |
| **B via glTF** | X3D -> Blender add-on / Loom -> glb -> glTFast | baked light, PBR textures, skeletons | ROUTE logic is lost (glTF has none until KHR_interactivity) | **L** |
| **C splats** | .spz / `KHR_gaussian_splatting` -> a Unity splat renderer | Atlas / Marble worlds | glTFast support for the splat extension not found in search (**S**: assume absent) | **S** |
| **D live** | Gobbler -> `/push` -> gate -> device polls | iterate on a headset without a rebuild | scene size (rebuilds whole scene) | server **C**, client **S** |

**The tether.** `tools/live_server.mjs` refuses to publish any scene that fails the x3d_mcp gate. Measured (`tools/test_live.sh`, node 22, fork `4d433af`): valid scene published 200; XSD-invalid scene refused 422; XSD-valid-but-dangling-ROUTE scene refused 422 and the previous scene stays live; ETag 304 on repeat poll. 8/8 checks pass. A headset only ever shows what a validator passed.

## 4. How: what is built
| Piece | Lines of intent | Evidence |
|---|---|---|
| `Runtime/Core` pure C# | parser, DEF/USE, tessellator, handedness, ROUTE animation, writer, receipt | **C** 41/41 tests, dotnet 8.0.134 |
| `Runtime/Unity` | scene builder, player, live client, exporter, point-cloud baker | **S** |
| `Runtime/XR` | AR Foundation tap-to-place at metre scale | **S** |
| `Editor` | `.x3d` ScriptedImporter; headless `X3DBuild` (Android / iOS / Windows / WebGL) | **S** |
| `tools/gate.py`, `live_server.mjs` | the tether and the live server | **C** |

Nodes drawn (receipt `Drawn` list): Transform, Group, Switch, Shape, Appearance, Material, Viewpoint, Directional/Point/SpotLight, Background (ambient only), Box, Sphere, Cylinder, Cone, IndexedFaceSet (crease angle, ear clipping), IndexedTriangleSet, TriangleSet, PointSet, Coordinate/Normal/Color/ColorRGBA/TextureCoordinate, TimeSensor + Position/Orientation/Scalar/Color interpolators. The sample `samples/scenes/hello-xr.x3d` passes XSD 4.0 + semantic (fork `4d433af`), builds to 762 triangles + 6 points with **zero gaps**.

**Receipts.** Every build returns `x3d-unity-receipt/v1`: sha256 of the source, node counts, triangles, points, and a sorted **gap ledger** (`node:Extrusion`, `geometry:...`, `route-source:Script`, `transform:skew-lost`, `warning:USE before DEF`). Nothing is dropped silently.

## 5. XR: what to exploit, per device (all **L** unless noted; no device was touched)
| Target | Unity route | What the bridge adds | Watch-out |
|---|---|---|---|
| iPhone / iPad | AR Foundation + ARKit | `X3DARPlacer`: tap a plane, scene stands at 1 unit = 1 m | iOS builds need the Mac (hagsMac): `X3DBuild.IOS` emits an Xcode project, sign there |
| Android phone | AR Foundation + ARCore | same component | ARCore-supported device list; IL2CPP + ARM64 |
| Quest 3 / 3S | OpenXR plugin, Meta OpenXR feature, passthrough | Player + Live Client; hand tracking is the host app's | Quest is Android: the `X3DBuild.Android` path |
| Apple Vision Pro | Unity PolySpatial / visionOS | scene as RealityKit content | PolySpatial MR mode converts **Shader Graph** materials; our default Lit shader path is unproven there (**S**); plan a Shader Graph material variant |
| Android XR / other OpenXR | OpenXR | Player + Live Client | support is moving fast; verify before promising |
| Desktop PCVR | OpenXR | all of the above | none |

The XR trick that makes the bridge cheap: **scene content is device-agnostic**; only the rig differs. So the package ships the content half (`X3DPlayer`, `X3DLiveClient`) with no XR dependency and the rig half (`X3DARPlacer`) behind a version define that vanishes when AR Foundation is absent.

## 6. Not yet (honest gap list)
ImageTexture / textures, Text, Extrusion, ElevationGrid, IndexedLineSet, Billboard, LOD, HAnim -> skinned mesh, Sound, Scripts (ecmascript), Inline fetch, sensors (touch, proximity), per-vertex `colorIndex` / `texCoordIndex`, Fog, EnvironmentLight, physics. Each one prints in the receipt. Priority order for 0.2: textures, then Inline + touch sensors (needed for the Gobbler pages), then HAnim.

## 7. Paste-ready: run this on DESKTOP-JNLJ2MT (Unity 6, the long run)
```powershell
# 1. new 3D (URP) project, then in Packages\manifest.json add:
#    "io.x3d.unity": "file:C:/Users/Consultant/Desktop/YT4/x3d/x3d.lab/unity/io.x3d.unity"
# 2. open the project, let it compile. EXPECT possible compile errors in Runtime/Unity or Editor: send me the first 20 lines of the Console.
# 3. drag Samples~/hello-xr/hello-xr.x3d into Assets; click the asset: the Inspector shows receipt.json
# 4. new scene, empty GameObject + X3D Player, source = hello-xr.x3d, press Play: cube spins, bobs, pulses colour
# 5. live link:   node tools\live_server.mjs --port 8787      (needs x3d_mcp: set X3D_MCP_DIR)
#                 curl.exe --data-binary "@samples\scenes\hello-xr.x3d" http://localhost:8787/push
#    add X3D Live Client (server http://127.0.0.1:8787) and watch the scene appear
# 6. Android / Quest build, headless:
"C:\Program Files\Unity\Hub\Editor\<6000.x>\Editor\Unity.exe" -batchmode -quit -projectPath . -buildTarget Android -executeMethod X3D.UnityBridge.Editor.X3DBuild.Android -logFile build.log
```
On hagsMac: `X3DBuild.IOS`, open `Builds/iOS` in Xcode, sign with a free personal team, run on a phone.

## 8. The honest case: "Unreal is rising, Unity has fallen"
| Axis | Unity | Unreal | Verdict for us |
|---|---|---|---|
| Peak fidelity (Lumen, Nanite, path tracer) | good (HDRP) | best | Unreal. Our "highest quality graphics" goal lives there |
| Phone AR (ARKit/ARCore) | mature, one codebase | works, heavier | Unity (L) |
| Standalone headset (Quest) | very strong ecosystem | works, heavier on CPU/GPU budget | Unity (L) |
| visionOS | first-class PolySpatial route | not a path today (**S**) | Unity (L) |
| Scripting a bridge | C#, fast iteration, tiny runtime | C++/Blueprint, long compiles | Unity |
| Headless CI | Editor batchmode, licence needed | works (our G4 notebooks) | tie |
| Trust / prestige | dented by the 2023 runtime-fee episode (cancelled 2024) (L) | rising | Unreal; but irrelevant to a bridge |
| Cost to us | Personal licence tier (L: check the revenue cap) | free to 1M USD revenue (L) | no spend either way; no licence is bought here |

So "fallen" is true about prestige and peak pixels, not about where the cameras and headsets are. The bridge is cheap (the core is engine-agnostic and already tested), so the right call is **both engines, different jobs**, not a choice.

## 9. Keijiro Takahashi: inspiration, credit, licence rule
His open-source Unity work is the reference for three things we want: (1) **VFX Graph as a data renderer** (point clouds baked to textures: Pcx / PcxVFX, Rsvfx), (2) **splats in VFX Graph** (SplatVFX; its own README calls it experimental and recommends Aras Pranckevicius's UnityGaussianSplatting, confirmed by a search snippet 26 Nov), (3) **audio-reactive and live-IO patterns** (Klak: KlakMIDI / KlakNDI / KlakSpout, Reaktion). `X3DPointCloudBaker` uses the point-cloud-as-texture *pattern* in original code and credits it in its header.
**Licence status: not confirmed.** The search tool could not open the LICENSE files and the GitHub API is out of this session's scope, so **no Keijiro code is vendored**. Rule before any vendoring: read each repo's LICENSE at the pinned commit, copy the notice into `THIRD-PARTY.md`, name the commit. Same for UnityGaussianSplatting (believed MIT, **S**) and glTFast (believed Apache-2.0, **L**).

## 10. Roadmap (when)
| Step | Gate | Where it runs |
|---|---|---|
| 0.1 scaffold + tested core + gated live server | done | cloud |
| 0.2 compile in Editor, hello-xr plays, receipt shows | §7 steps 1-4 | his PC |
| 0.3 Android phone + Quest build, AR placement | §7 step 6 | his PC |
| 0.4 iOS build | free personal Apple team | hagsMac |
| 0.5 textures + Inline + touch sensors | 0.2 | cloud + PC |
| 0.6 splats via UnityGaussianSplatting (path C); Atlas worlds on a phone | licence read | PC |
| 0.7 MCP tool `unity_push` in x3d_mcp (nothing upstream until he says "send") | his word | cloud |
| 0.8 visionOS PolySpatial Shader Graph variant | Apple hardware + licence check | hagsMac |
Demo 80 becomes a page when 0.2 passes: a WebGL build is the cheap public face; the receipts and the AR build are the real product.
