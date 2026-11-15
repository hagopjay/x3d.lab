# Colab G4 cells: X3D to Unreal 5.8.4, measured (14–15 Nov 2026)

These are the extra cells that ran next to notebook 07 (`../07_Unreal_Fidelity.ipynb`) on the first Colab G4 session, and what
they found. Each file is a set of cells separated by `# %% <title>` lines. Each cell is pasted on its own, or loaded with the
small loader in the file's header.

## The rig

Every number below was measured on this rig unless its line says otherwise.

| part | version |
|---|---|
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition, 95.6 GB, driver 580.82.07 |
| host | Colab G4, Ubuntu 24.04, 48 vCPUs |
| Unreal | 5.8.4-58902052, Vulkan SM6 + ray tracing (VK_EXT_descriptor_buffer bindless), HW Lumen, MegaLights, Substrate |
| x3d.lab | `70551e8` in the kernel (before PRs #29–#31) unless a line names `main` |
| X_ITE | 16.7.0 in headless Chromium 159, ANGLE on Vulkan 1.4.312, on the same GPU |
| Cycles | Blender 5.2.3 with x3d-blender-io |
| FLIP | `flip-evaluator`, LDR |

## The files

| file | what it does | when |
|---|---|---|
| `unreal_g4_cells.py` | hardware card, instanced-glb test scenes, the first FLIP cell | first session |
| `exposure_cells.py` | 9X exposure bracket and 9Y 4K keepers, for stills the cinematic look blew out | after step 9 |
| `gobbler_cells.py` | G1–G6: the Gobbler from the CPU reference, X_ITE-look bakes, EV per view, looks round, 4K heroes | inside the 07 kernel |
| `leg_e_cells.py` | Leg E: PBR grid, Cornell box, light ladder; 10c FLIP over object pixels only; pass 2 beauty | after step 2 / step 10 |
| `tier1_proof_cells.py` | F0 settings and F1 proof (the FLIP triangle, a repeat render, bounds fixed before the run) | on a fresh runtime of `main` |
| `banger_cells.py` | J1 eight moods poster, J2 rack-focus and Cornell-dawn flipbooks | for fun |
| `bridge_g4_cells.py` | the x3d.loom Babylon WebGPU bridge suite with X_ITE as oracle, on the GPU | queued |
| `vulkan_burn.py` | Vulkan from Python (wgpu-py): FMA furnace in TFLOPS, 4K Mandelbulb, nvidia-smi chart | standalone |
| `vulkan_encore.py` | V1 262,144-star galaxy collision (N-body), V2 8K Mandelbulb poster with render-only timing | standalone |
| `scenes/` | the three Leg E scenes (X3D 4.0, written for this lab) | |

`vulkan_encore.py` V2 reads the Mandelbulb shader from `vulkan_burn.py`. Set `BURN_SRC` to that file's path. `BURN_QUICK=1` shrinks
both Vulkan files for a CPU smoke test on lavapipe (`VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.json`).

## What was measured

**Leg 1 (calib, stilllife; preview 1080p, match look):** 40 PASS, 3 WARN, 0 FAIL, 1 SKIP.
- The calibrated K came out at 1.017, against the 1.2 that was assumed.
- stilllife vs X_ITE: outline match 1.000, IoU 0.997, SSIM 0.820.
- A 1080p HW Lumen still takes about 10 s, with peak VRAM 7.5 GB.

**Leg E pass 1 (match look, preview).** FLIP is shown two ways: over the whole frame, and over object pixels only (the magenta
key background left out).

| scene | vs X_ITE (frame / object) | vs Cycles (frame / object) |
|---|---|---|
| E1 PBR grid | X_ITE drew nothing (cause not found yet) | 0.289 / 0.427 |
| E2 Cornell box | 0.321 / 0.489 | 0.285 / 0.433 |
| E3 light ladder | 0.092 / 0.735 | 0.091 / 0.724 |

Whole-frame FLIP is diluted by the background: E3's object covers 11.7% of the frame. Use the object column.

**Gobbler (720p, HW Lumen, vs the CPU shader reference):**
- The X_ITE-look bake (`gobbler.write_glb_xite`) took lit-pixel F from 0.78–0.85 to 0.87–0.95, and colour Δ from 39–74 to 19–34.
- SSIM was 0.56–0.69. The best pairing is the `xite_hot` bake with the `glow` look.

**Vulkan from Python (wgpu-py 0.33.0, Vulkan backend):**
- An FP32 FMA compute shader held 98.1 TFLOPS for 60 s, at 100% utilisation, 541 W peak and 2430 MHz SM clock.
- The burn's Mandelbulb average (10 fps at 4K, 4 rays per pixel) counted saving the PNGs inside the timer. The frames that saved
  nothing ran at about 79 fps. `vulkan_encore.py` V2 measures the render alone.

## Named gaps

Each gap is graded Confirmed, Likely or Suspected.

1. **Shadows (fixed in PR #31).** Unreal lights cast shadows although X3D 4.0's `shadows` field defaults to false. Confirmed on
   the Cornell box. Not yet re-measured on a runtime of `main`.
2. **One light model per scene.** X_ITE lights PhysicalMaterial with ρ/π·i and classic Material with ρ·i. `convert.LightMapping`
   picks one model for the whole scene, so a scene that mixes both can't match both. Confirmed by E3.
3. **Inverse-square falloff always.** Point and spot lights are matched at the scene centre, by design. Confirmed by E3.
4. **Metals turn black with no environment.** Unreal shows a metal by what it reflects. Likely (seen on E1 against Cycles; not isolated yet).
5. **EXT_mesh_gpu_instancing is dropped** by UE 5.8.4 Interchange: 1,711 instances went missing. Plain DEF/USE sharing survives.
   Confirmed.
6. **The USD path imports 0 meshes** in 5.8; lights and cameras arrive. Confirmed.
7. **One exposure per scene isn't enough** when cameras see very different light. odyssey's GiftFrame blew out where DroneCam was
   good. Confirmed.

## Tier 1: where it stands

Not claimed yet. Geometry, cameras, materials and outlines pass. Auto exposure, Nanite ray-tracing mode and the shadows fix are
merged but have not run end to end from a fresh runtime. `tier1_proof_cells.py` does that run, with bounds written down before
any numbers exist:

- outline IoU vs X_ITE ≥ 0.95;
- the same still rendered twice by Unreal differs by object FLIP ≤ 0.02 (the noise floor);
- Unreal's object FLIP to X_ITE and to Cycles is no larger than X_ITE's to Cycles, plus 0.05 (within the references' own spread).

## Lessons for the next session

- The rung cvars switch depth of field and chromatic fringe off (`r.DepthOfFieldQuality 0`, `r.SceneColorFringeQuality 0`). A grade
  that wants them has to turn them back on in its own `cvars`.
- `render_grades` job names go through `ue_name`, so `+3` and `-3` collide. Name grades `p3` / `m3`. PR #30 now refuses the collision.
- `gobbler.exposure_fit` returns exactly 0 when no pixel is lit in both images. Fall back to a percentile match.
- `flip.evaluate(..., applyMagma=False)` returns the error map itself. With the default, you get magma RGB.
- `gobbler.write_glb` writes flat placeholder materials, which gave a cream bird. Use `write_glb_xite`.
- The cinematic look is manual exposure at the lab's EV100. Scenes with their own strong lights need the step 9 bracket.
