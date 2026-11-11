"""x3due: the Unreal leg of x3d.lab (notebooks/unreal). See notebooks/unreal/README.md.

tier      GPU detection, capability table, settings ladders
vulkan    NVIDIA's Vulkan driver into a Colab container (the unpack route), vulkaninfo parsing
engine    Unreal from your Drive (zip or cache) or Epic's ghcr image (oci), trimming, Vulkan ray-tracing config
oci       minimal registry client (no Docker)
uerun     non-root runs, commands, log proof (Vulkan on NVIDIA vs NullRHI / llvmpipe / crashes), VRAM sampling
x3dscene  X3D manifest: viewpoints, lights, backdrop, bounds (the Unreal-side truth)
convert   axes, units, FOV, light units
gltfstat  what a .glb holds, counted like the Unreal report
project   the Unreal project and the import / render job files
pipeline  the notebook's steps
engines   X_ITE, Cycles and the X3D -> glTF trip through the merged x3dlab package
compare   formats.compare_pngs + SSIM + viewpoint wrappers + contact sheet
report    scorecard, accounting, run card
showcase  the scenes and their licences
"""
__version__ = "0.1.0"
