
## How this sits beside the Unreal notebooks
- `../unreal/07_Unreal_Fidelity.ipynb` and `../unreal/colab_g4/` are the Unreal 5.8.4 leg. They need the G4 (ray-tracing cores) and use Blender 5.2.3 only for the Cycles reference.
- This notebook is Blender only (Cycles bake, no Unreal), so it also runs on an A100. It shares nothing with the Unreal kernel and can run in its own runtime.
- Both write under `My Drive/x3d.io` or `My Drive/x3dlab`; neither touches the other's folders.
