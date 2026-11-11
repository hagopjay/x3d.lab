# Later phases: design notes (nothing here is built yet)

Phase 1 (notebook 07) brings static X3D scenes into Unreal and renders stills. These notes are for what comes after: the **Gobbler** with its gyroscope/flywheel kinetics running in Unreal, its GLSL shaders, and people seeing it in browsers worldwide. Every claim that was not checked against a primary source or a run is marked **(unverified)**.

## 1. What the Gobbler is, in Unreal's terms

From the owner's public repos (10 Nov 2026):

| Edition | Where | What it holds |
|---|---|---|
| Gobbler v3 | `hagopjay/x3d.demo` `docs/demos/gobbler3-webgpu/v1/gobbler3.x3d` (also `hagopjay/x3d.ml` `gobbler-v3/build-public-candidate/`) | 121 graph entities, 17 ComposedShaders (6 distinct GLSL programs), 64 ROUTEs, 7 viewpoints, 11 lights |
| Gobbler Gyroscope / Meridian | `x3d.demo` `docs/demos/gobbler-gyroscope-webgpu/v1/gobbler3.x3d` (1.98 MB) | 39 ComposedShaders, 6 ParticleSystems with VolumeEmitters, 72 ROUTEs, 10 TimeSensors, 7 viewpoints |
| Gobbler worlds-within | `x3d.demo` `docs/demos/gobbler-worlds-within/v1/source/bird.x3d` | 105 ComposedShaders, 13 Inlines, 428 ROUTEs |
| Babylon WebGPU port | `hagopjay/x3d.loom` `runs/webgpu-gobbler-20261107/RECEIPT.md`, `x3d.webgpu/demo/src/gobbler/` | the 6 GLSL programs hand-ported to WGSL with a gap log (G1-G13) |

What the sandbox measured on Gobbler v3 (Blender leg, `x3dlab.formats`): glTF keeps 7 of 18 meshes (points, lines and the shader-only shapes go), 22 glTF animations, 7 cameras, 11 lights; the expected losses are the behaviour nodes, Background and ParticleSystem. Rendered from glTF it is not the Gobbler: its look *is* its shaders.

## 2. The kinetics (TimeSensor + interpolators + ROUTEs)

The "gyroscope" and "flywheel" motion in the X3D is keyframed (interpolators clocked by TimeSensors through ROUTEs), not simulated. Options, best first:

1. **Bake to Sequencer (recommended).** The import task already makes one Level Sequence per viewpoint. Extend it: for every ROUTE chain `TimeSensor → *Interpolator → Transform.set_rotation/translation/scale` (and `Material.set_*Color`, `*.set_transparency`), write transform / material-parameter tracks with the interpolator's keys scaled to the TimeSensor's cycleInterval, looped. Deterministic, renders in Movie Render Queue frame by frame, no game loop, no physics. The manifest parser already walks the graph; it needs to record ROUTEs as data (from, to, fields) and resolve key/keyValue. BooleanSequencer/Filter and TouchSensor logic (the Gobbler's "Dissolve" and ember trigger) become sequence events or are rendered in fixed states.
2. **Blueprint / C++ actors** for continuous motion that must run live (Pixel Streaming): a `RotatingMovementComponent` per flywheel (its rate from the interpolator's keys), a timeline per pulse. Needs a cooked/packaged build or `-game` with the editor binary.
3. **Real physics (Chaos)** only if the owner wants actual gyroscopic precession instead of the authored motion: rigid bodies with angular velocity and constraints. Whether Chaos models gyroscopic torque well enough to look right is **(unverified)**; it also makes renders non-deterministic, which conflicts with frame-locked comparisons (x3d.loom's gap G4).
4. **ParticleSystem → Niagara.** A mapping table (VolumeEmitter → Niagara shape location, speed/variation/mass/lifetime → spawn and update modules, colorRamp → colour curve) generated as data and built in the editor task. Niagara assets can be authored from Python only partially **(unverified)**; a template emitter with exposed parameters, instanced per X3D ParticleSystem, is the safer route.

Batch rule from the Babylon bridge (8 "failures" that were state leaks): one Unreal process per scene, and rerun a failure alone before calling it a defect.

## 3. The ComposedShader problem

glTF cannot carry GLSL, Unreal does not run GLSL, and the Gobbler's appearance is GLSL. Options:

* **Port per program to Unreal Materials (recommended).** There are only 6 distinct programs in v3. Each becomes a Material with a **Custom** (HLSL) expression node plus parameters for the shader's uniforms, ROUTE-driven uniforms become material-parameter tracks (phase 2.1). x3d.loom already ported them to WGSL and documented the traps (depth bias, point sprites, flat varyings, derivatives). A mechanical route GLSL → SPIR-V (glslang) → HLSL (SPIRV-Cross) can produce the Custom-node body **(unverified for Unreal's Custom node constraints)**; point sprites need geometry (quads, as in loom's port) or Niagara sprites.
* **Approximate** with PBR + emissive materials driven by the same parameters: cheaper, loses the look.
* **Keep X_ITE for shader-heavy scenes** and use Unreal where its renderer adds something (PBR scenes like *A Material Odyssey*).

The import task should record, per Shape, which ComposedShader it used (by a hash of the parts' source), so a material library keyed by that hash can be applied after import, the same idea as loom's "shader lookup by hash".

## 4. Seeing it worldwide

| Route | Needs | Colab fit | Notes |
|---|---|---|---|
| **Pixel Streaming** (Unreal renders, browsers watch a video) | a hardware encoder for H.264/AV1 (NVENC, AMD AMF, VideoToolbox, per Epic's Pixel Streaming reference); VP8/VP9 are CPU encoders | G4 (4 NVENC), L4 (2), T4 (1); **not A100** (0 NVENC) except with VP8/VP9 on the CPU | WebRTC media is UDP. Colab's tunnels (cloudflared, ngrok) carry TCP/HTTP only, so viewers need a **TURN relay** both sides can reach (e.g. coturn on a small VM, or a hosted TURN service); the signalling server can go through the tunnel. Cost and session limits make it a demo, not a service **(unverified on Colab)** |
| **Bake to the web** (Unreal or Cycles lighting baked into textures, shown in X_ITE / Babylon WebGPU) | a light bake; glTF/X3D export | any GPU | Unreal's Lumen does not bake; Lightmass static lighting on Linux, and GPU Lightmass on Vulkan, are **(unverified)**. Blender Cycles bake (already in the lab) is the dependable baker; Unreal stills stay as the reference look. Hosted on GitHub Pages like `x3d.demo` |
| **Gaussian splats from Unreal renders** | many MRQ stills of a static scene, a splat trainer on the same GPU | A100/G4 | the result goes into X3D 4.1 `GaussianSplats`, which X_ITE draws (HANDOFF: X_ITE loaded the 4.1 GaussianSplats scenes). Static only **(unverified end to end)** |
| **Pre-rendered media** | MRQ image sequences, turntables, panoramas | any | the simplest worldwide option; no interactivity |

Recommendation: for *viewing* worldwide, bake (Cycles) or splat into X3D/glTF for X_ITE and Babylon WebGPU; use Pixel Streaming only on a G4 or L4 runtime, with a TURN relay, for live demos.

## 5. Model in the loop (optional, low priority)

If a model is ever added (for example to propose camera paths or material choices), it proposes **validated JSON data** of the same shape as the import/render jobs (`x3due.project.validate_*`), never code to run inside Unreal, and it runs before or after Unreal (not alongside it) so the two do not compete for VRAM. Unreal 5.9's preview adds editor-side AI and MCP features **(unverified, preview)**; nothing here depends on them.
