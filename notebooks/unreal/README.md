# notebooks/unreal: X3D in Unreal Engine, headless, on a Colab GPU

**07 Unreal Fidelity** ([open in Colab](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/unreal/07_Unreal_Fidelity.ipynb)) brings X3D scenes into Unreal Engine 5, renders the highest-fidelity stills the Colab GPU allows, and measures what arrived and how it compares with X_ITE and Blender Cycles. Streaming is out of scope here; [DESIGN.md](DESIGN.md) covers the later phases (the Gobbler's kinetics, GLSL shaders, viewing it worldwide).

**08 Gobbler Cinematic** ([open in Colab](https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/unreal/08_Gobbler_Cinematic.ipynb)) is the Gobbler sprint. It bakes the Gobbler's shader-encoded geometry (`x3due/gobbler.py`) into a glb Unreal can draw, draws a CPU reference from the same decode (it matches X_ITE at SSIM 0.91–1.0), then runs rounds: every grade (bloom, exposure, lens, depth of field, light scale) × every viewpoint in one Unreal start, scored against the reference and laid out on a contact sheet. You pick a look; the next round tries its neighbours; the hero cell renders the winner at 4K/8K. Scores add up across runtimes in `DRIVE_DIR/gobbler/scoreboard.jsonl`.

Status: first live runs on Colab L4 and A100 (11 Nov 2026). The engine starts, imports and renders; fixes from those runs: the Nanite streaming pool stays under Vulkan's 2 GB allocation (the A100 crash), all samples are spatial (temporal ones did not accumulate with no motion blur), the Zen shader cache goes to Drive, OpenImageDenoise replaces NNEDenoiser, a Drive heartbeat, and step 6c for any start-up crash. The notebooks explain every failure from Unreal's own log.

## Which GPU

| Colab GPU | RT cores | NVENC | VRAM | Unreal gets | Verdict |
|---|---|---|---|---|---|
| **G4** (RTX PRO 6000 Blackwell Server Edition) | 188, 4th gen | 4 | 96 GB | hardware ray tracing, path tracer, 8K | **best for fidelity** |
| **A100** | none | none | 40 / 80 GB | software Lumen only; 8K stills without tiling, resident texture pools | big stills, no path tracer |
| **L4** | 3rd gen | 2 | 24 GB | hardware ray tracing, path tracer, 4K | good value |
| **T4** | 40, 1st gen | 1 | 16 GB | hardware ray tracing, small stills; slow first shader compile on 2 vCPU / 13 GB | smoke test |

Sources: NVIDIA's A100 architecture whitepaper ("does not include ... RT Cores ... or an NVENC encoder"), the NVENC/NVDEC support matrix, the T4, L4 and RTX PRO 6000 Blackwell Server Edition datasheets. The table is only a prior: at run time the Vulkan device's own extensions (`VK_KHR_ray_tracing_pipeline`, `VK_KHR_acceleration_structure`) decide.

## The one manual step

Download Unreal Engine for Linux with **your** Epic account (Epic's EULA; this repository never contains or fetches the engine for you):

* **A** (recommended): `Linux_Unreal_Engine_5.8.4.zip` from <https://www.unrealengine.com/linux> into `MyDrive/x3dlab/unreal/`, or
* **B**: link GitHub to Epic, accept the EpicGames organisation invite, make a classic token with `read:packages`, store `GHCR_USER` / `GHCR_TOKEN` as Colab secrets; the notebook pulls `ghcr.io/epicgames/unreal-engine:dev-slim-5.8.4` without Docker.

After a successful run the notebook can cache a trimmed engine (no debug symbols, templates or samples) and the shader cache (DDC) in the same Drive folder.

## The pipeline

```
X3D ──(x3dlab.formats / Blender: notebook 06's leg)──► glTF ──(Interchange, one-shot editor task)──► Unreal level
 │                                                       │                                             │ cameras, lights from the X3D
 └── manifest: viewpoints, lights, backdrop, bounds ──────┴── glTF in vs Unreal out (accounting) ──────┤ (data, computed outside Unreal)
                                                                                                       ▼
                         Movie Render Queue in -game -RenderOffscreen on Vulkan (Python host executor), one process per scene
                                                                                                       ▼
                         stills (EXR + PNG) ── formats.compare_pngs + SSIM vs X_ITE and Cycles ── contact sheet, scorecard, run card
```

* **Vulkan:** Colab's container has NVIDIA's compute driver only. `x3due.vulkan` downloads `libnvidia-gl`, `libnvidia-gpucomp`, `libnvidia-common` at exactly the driver's version and unpacks them (`dpkg -x`), never `apt-get install`s them (that breaks Colab's CUDA), never hand-writes an ICD.
* **Non-root:** Unreal refuses root; it runs as user `ue` with `VK_ICD_FILENAMES` pinned to NVIDIA's ICD.
* **Proof:** every Unreal log is read: PASS only for the Vulkan RHI on an NVIDIA device; NullRHI, llvmpipe/lavapipe, crashes are named, and known crashes are retried along a ladder (async compute off, bindless for all shaders, no ray tracing, Shader Model 5).
* **Fidelity traps handled:** X3D metres/Y-up vs Unreal centimetres/Z-up (the importer's axis mapping is *measured* from shape centres), X3D field of view on the short image side vs Unreal's horizontal, X3D unitless intensity vs lux/candela (calibrated by a rendered calibration box), X3D headlight (one per viewpoint, switched per still), Background as an unlit backdrop, scoped X3D lights becoming global.

## Files

| Path | What |
|---|---|
| `src/07_unreal.py` | the notebook source (percent format; `python tools/build_notebooks.py` writes `07_Unreal_Fidelity.ipynb`) |
| `x3due/` | host-side helpers: `tier`, `vulkan`, `engine`, `oci`, `uerun`, `x3dscene`, `convert`, `gltfstat`, `project`, `pipeline`, `engines`, `compare`, `report`, `showcase` |
| `ue_project/` | the Unreal project template (`X3DLab.uproject`, `Config/*.ini`, `Content/Python/init_unreal.py`, `x3dlab_render.py`: the Movie Render Queue executor) |
| `ue_scripts/x3dlab_import.py` | the one-shot import task (Interchange, accounting, cameras, lights, backdrop, looks, sequences) |
| `tests/` | `pytest notebooks/unreal/tests` (fixtures are labelled real or synthetic; `unreal_stub.py` stands in for the `unreal` module) |
| `DESIGN.md` | later phases: Gobbler kinetics, ComposedShader, worldwide viewing |

## Division of labour with notebook 06 (Foundry)

The Foundry (`src/x3dlab/formats.py`, `foundry.py`) owns X3D → Blender → glTF/USD: the conversion, the Khronos validator, the expected losses. This folder consumes it (`x3due.engines.to_gltf`) and owns glTF/USD → Unreal: import, accounting, rendering, the GPU ladder, the stills, judged with the Foundry's `formats.compare_pngs` so the numbers sit on one scale.

## Tests

```bash
pip install -e ".[research,dev]"
pytest notebooks/unreal/tests        # not part of the default `pytest` run (testpaths = tests)
```
