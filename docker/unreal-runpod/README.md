# RunPod image for the Unreal pipeline

Image: `ghcr.io/hagopjay/x3d-unreal-runpod:latest` (built by `.github/workflows/unreal-runpod-image.yml`).

What is in it: NVIDIA CUDA runtime base, Vulkan loader + glvnd, `vulkan-tools`, `rclone`, a non-root `ue` user,
the Unreal Linux runtime libraries, and a boot-time Vulkan self-check.
What is not in it: Unreal Engine itself (lives on the network volume), any keys, any private project data.
No Mesa Vulkan drivers on purpose, so a software renderer cannot fake a pass.

## Use on RunPod
1. Pods, Deploy, choose **Secure Cloud** and a GPU.
2. Edit Template: container image `ghcr.io/hagopjay/x3d-unreal-runpod:latest`, container disk 50 GB, env `NVIDIA_DRIVER_CAPABILITIES=all`, no ports.
3. Deploy. Read the pod **Logs** tab, or open the web terminal and run `cat /workspace/vulkan-report.txt`.
   The last lines read `VULKAN-SELFCHECK: PASS` or `FAIL`. Re-run any time with `vk-selfcheck`.

## Limit
The driver libraries are mounted from the RunPod host over the image at start. The image makes every try a repeatable one-paste test;
it cannot fix a host whose driver mount is broken. A FAIL report names the driver version and the first loader error.
