# G4 bake notebook v2: run steps

File: `giza_bakes_g4_v2.ipynb` (same build sources as build 18751c82; only the notebook cells changed). Jobs: `crawl`, `masonic`, `fall`, `twins` (Cycles light bakes to image textures, exported as X3D 4.0 by x3d-blender-io).

## What changed from the notebook in the 23 Nov 2026 hand-over zip
1. Blender version is a dropdown in cell 3: **5.3.0** (default) or **5.1.2** (fallback). Each version unpacks to its own folder.
2. The add-on is pinned to x3d-blender-io `8e56437` (today's main, 17 merges newer than `47ed742`). The commit is a text box in cell 3.
3. Cell 1 accepts any NVIDIA GPU, so an **A100 works**. It no longer asks for the RTX PRO 6000.
4. Cell 5 has `DEVICE = "AUTO"`: it asks Blender which backends exist and takes OptiX, then CUDA.
5. Cell 6 stops with a red X if a GPU was requested and Blender fell back to the CPU (before, it kept baking slowly without saying so).
6. Results go to `<job>-<profile>-b530` or `-b512`, so a 5.3 run and a 5.1 run sit side by side.
7. Cell 7 no longer deletes an earlier Drive folder of the same name. It renames it with `-prev-<date>`.
8. The Blender download fails loudly if the file is missing (it used to pass a failed download to tar).

## Run it (A100 now, G4 later: same steps)
1. Upload `giza_bakes_g4_v2.ipynb` to Colab (File, Upload notebook).
2. Runtime, Change runtime type: A100 (or G4 when you get one).
3. Run cells 1 to 4 in order. Cell 2 asks for Drive access.
4. Cell 5: leave `JOB = crawl`, `PROFILE = quick`, `DEVICE = AUTO`. Run it. It must print a green tick and OPTIX or CUDA.
5. Run cell 6, then cell 7.
6. If quick worked: set `PROFILE = g4` in cell 5, run cells 5, 6, 7. Repeat for `masonic`, `fall`, `twins`.
7. Runtime, Disconnect and delete runtime.

Results land in `My Drive/x3d.io/blenderizer-bakes/`.

## If something goes wrong
- Cell 5 prints OPTIX `[]` and CUDA with a card name: normal on an A100. It bakes on CUDA.
- Cell 6 prints a red X about the CPU: set `DEVICE = CUDA` in cell 5, run 5 and 6 again.
- Anything odd on 5.3.0: set `BLENDER_VERSION = 5.1.2` in cell 3 and run from cell 3 down.

## What was tested, and what was not
- Confirmed: all four jobs run on Blender 5.3.0 with add-on 8e56437, quick profile, CPU. 76 surfaces baked, four X3D files, each valid X3D 4.0 (x3d_mcp fork 4d433af). Receipt: `test-on-blender-5.3.json`.
- Confirmed: both Blender downloads exist (5.3.0 is 407 MB, 5.1.2 is 396 MB).
- Not tested: any GPU path, the `g4` profile, and Colab itself. There is no GPU where this was checked.
- Likely: an A100 bakes these with CUDA. Whether OptiX shows up on a Colab A100 is Suspected only.
- One warning on 5.3.0: `World.use_nodes` goes away in Blender 6.0. Harmless today.

## Not fixed here (lives on the Mac)
- `build_g4_notebook.py` still pins 5.1.2 and `47ed742`. To rebuild from the Mac instead of using this file, change `BLENDER_VERSION` and `ADDON` there, or run `patch_nb.py <old.ipynb> <new.ipynb> 8e56437` on its output.
- `ab_shots.cjs` defaults to a Mac-only Playwright path. Set `PLAYWRIGHT_DIR` on any other machine.

## How this sits beside the Unreal notebooks
- `../unreal/07_Unreal_Fidelity.ipynb` and `../unreal/colab_g4/` are the Unreal 5.8.4 leg. They need the G4 (ray-tracing cores) and use Blender 5.2.3 only for the Cycles reference.
- This notebook is Blender only (Cycles bake, no Unreal), so it also runs on an A100. It shares nothing with the Unreal kernel and can run in its own runtime.
- Both write under `My Drive/x3d.io` or `My Drive/x3dlab`; neither touches the other's folders.
