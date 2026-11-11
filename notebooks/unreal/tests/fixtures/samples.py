"""Recorded and SYNTHETIC tool outputs for the tests.

Real (from the owner's Colab A100 run, notebooks/05_GPU_Hog.00.ipynb, 10-11 Nov 2026): the A100 nvidia-smi header
and query values (driver 580.82.07, CUDA 13.0, 81920 MiB), the unpacked package names and versions, the device name.
Everything else is SYNTHETIC: written in the tools' documented formats with plausible values, not captured on that
hardware. In particular the per-GPU Vulkan extension lists are assumptions to exercise the decision logic, and the
Unreal log lines follow the formats quoted in Epic forum posts (LogVulkanRHI "- DeviceName:", "- API=... VendorId=").
"""

SMI_HEADER_A100 = """Wed Nov 11 00:33:34 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.82.07              Driver Version: 580.82.07      CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
|   0  NVIDIA A100-SXM4-80GB          Off |   00000000:00:05.0 Off |                    0 |
| N/A   32C    P0             56W /  400W |       0MiB /  81920MiB |      0%      Default |
+-----------------------------------------+------------------------+----------------------+
"""
SMI_QUERY = {   # name, memory.total [MiB], driver_version, compute_cap, pci.device_id
    "A100-80": "NVIDIA A100-SXM4-80GB, 81920, 580.82.07, 8.0, 0x20B210DE",          # real values (device id from Chrome's renderer string)
    "A100-40": "NVIDIA A100-SXM4-40GB, 40960, 580.82.07, 8.0, 0x20B010DE",          # synthetic
    "T4": "Tesla T4, 15360, 580.82.07, 7.5, 0x1EB810DE",                            # synthetic
    "L4": "NVIDIA L4, 23034, 580.82.07, 8.9, 0x27B810DE",                           # synthetic
    "G4": "NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887, 580.95.05, 12.0, 0x2BB510DE",   # synthetic
    "H100": "NVIDIA H100 80GB HBM3, 81559, 580.82.07, 9.0, 0x233010DE",             # synthetic
}
MEMINFO = {"A100-80": "MemTotal:       175079608 kB\nMemAvailable:   170000000 kB\n",     # 167 GB: matches the hog run
           "T4": "MemTotal:       13290464 kB\nMemAvailable:   11000000 kB\n"}         # synthetic (~12.7 GB)

MADISON_580 = {   # apt-cache madison, as Colab's sources (Ubuntu + NVIDIA's CUDA repo) would print it -- synthetic layout
    "libnvidia-gl-580": " libnvidia-gl-580 | 580.196.07-1ubuntu1 | https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64  Packages\n"
                        " libnvidia-gl-580 | 580.82.07-0ubuntu1 | http://archive.ubuntu.com/ubuntu jammy-updates/restricted amd64 Packages\n",
    "libnvidia-gpucomp-580": " libnvidia-gpucomp-580 | 580.196.07-1ubuntu1 | https://developer.download.nvidia.com/...\n"
                             " libnvidia-gpucomp-580 | 580.82.07-0ubuntu1 | http://archive.ubuntu.com/ubuntu jammy-updates/restricted amd64 Packages\n",
    "libnvidia-common-580": " libnvidia-common-580 | 580.196.07-1ubuntu1 | https://developer.download.nvidia.com/...\n"
                            " libnvidia-common-580 | 580.82.07-0ubuntu1 | http://archive.ubuntu.com/ubuntu jammy-updates/restricted all Packages\n",
}

VK_SUMMARY_NVIDIA = """==========
VULKANINFO
==========

Vulkan Instance Version: 1.3.204

Devices:
========
GPU0:
	apiVersion         = 1.4.312
	driverVersion      = 580.82.7.0
	vendorID           = 0x10de
	deviceID           = 0x20b2
	deviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
	deviceName         = {name}
	driverID           = DRIVER_ID_NVIDIA_PROPRIETARY
	driverName         = NVIDIA
	driverInfo         = 580.82.07
	conformanceVersion = 1.4.1.3
GPU1:
	apiVersion         = 1.3.255
	driverVersion      = 0.0.1
	vendorID           = 0x10005
	deviceID           = 0x0000
	deviceType         = PHYSICAL_DEVICE_TYPE_CPU
	deviceName         = llvmpipe (LLVM 15.0.7, 256 bits)
	driverID           = DRIVER_ID_MESA_LLVMPIPE
	driverName         = llvmpipe
"""
VK_SUMMARY_SOFTWARE_ONLY = """Devices:
========
GPU0:
	apiVersion         = 1.3.255
	vendorID           = 0x10005
	deviceType         = PHYSICAL_DEVICE_TYPE_CPU
	deviceName         = llvmpipe (LLVM 15.0.7, 256 bits)
	driverName         = llvmpipe
"""


def vk_full(rt: bool) -> str:
    base = ["VK_KHR_16bit_storage", "VK_KHR_buffer_device_address", "VK_KHR_dynamic_rendering", "VK_KHR_synchronization2",
            "VK_EXT_descriptor_indexing", "VK_EXT_mesh_shader", "VK_KHR_deferred_host_operations"]
    if rt:
        base += ["VK_KHR_acceleration_structure", "VK_KHR_ray_query", "VK_KHR_ray_tracing_pipeline",
                 "VK_KHR_ray_tracing_position_fetch"]
    dev0 = "\n".join("\t%-40s: extension revision 1" % e for e in sorted(base))
    dev1 = "\n".join("\t%-40s: extension revision 1" % e for e in ("VK_KHR_16bit_storage", "VK_KHR_swapchain"))
    return ("Instance Extensions: count = 2\n====================\n\tVK_KHR_surface : extension revision 25\n\n"
            "Device Properties and Extensions:\n=================================\nGPU0:\nVkPhysicalDeviceProperties:\n"
            "---------------------------\n\tapiVersion = 1.4.312\n\nDevice Extensions: count = %d\n------------------------------\n%s\n\n"
            "VkQueueFamilyProperties:\n========================\n\tqueueCount = 16\n\n"
            "GPU1:\nVkPhysicalDeviceProperties:\n---------------------------\n\tapiVersion = 1.3.255\n\n"
            "Device Extensions: count = 2\n------------------------------\n%s\n\nVkQueueFamilyProperties:\n" % (len(base), dev0, dev1))


T = "[2026.11.11-01.02.03:%03d][  0]"
UE_LOG_VULKAN_OK = "\n".join([
    T % 0 + "LogInit: Display: Running engine for game: X3DLab",
    T % 1 + "LogInit: Engine Version: 5.8.4-45123456+++UE5+Release-5.8",
    T % 2 + "LogRHI: Using Default RHI: Vulkan",
    T % 3 + "LogVulkanRHI: Display: Built with Vulkan header version 1.4.312",
    T % 4 + "LogVulkanRHI: Display: Found 2 device(s)",
    T % 5 + "LogVulkanRHI: Display: - DeviceName: NVIDIA A100-SXM4-80GB",
    T % 6 + "LogVulkanRHI: Display: - API=1.4.312 (0x404138) Driver=0x91148dc0 VendorId=0x10de",
    T % 7 + "LogRHI: Driver Version: 580.82.07 (internal:580.82.07, unified:)",
    T % 8 + "LogVulkanRHI: Display: Vulkan RHI ShaderPlatform for SM6: VULKAN_SM6.",
    "[2026.11.11-01.12.03:000][  1]LogShaderCompilers: Display: Compiling 4021 shaders",
    "[2026.11.11-01.40.00:000][ 90]LogPython: X3DLAB: wrote stilllife__CA_Camera__high (2 files, 61.0 s)",
])
UE_LOG_NULLRHI = "\n".join([T % 0 + "LogInit: Display: Running engine for game: X3DLab",
                            T % 1 + "LogRHI: Display: Using NullRHI (-nullrhi)"])
UE_LOG_LLVMPIPE = "\n".join([T % 0 + "LogRHI: Using Default RHI: Vulkan",
                             T % 1 + "LogVulkanRHI: Display: - DeviceName: llvmpipe (LLVM 15.0.7, 256 bits)",
                             T % 2 + "LogVulkanRHI: Display: - API=1.3.255 (0x4030ff) Driver=0x1 VendorId=0x10005"])
UE_LOG_ROOT = "Refusing to run with the root privileges.\n"
UE_LOG_DEVICE_LOST = UE_LOG_VULKAN_OK.split("\n[2026.11.11-01.12")[0] + "\n" + "\n".join([
    T % 20 + "LogVulkanRHI: Error: Result failed, VkResult=-4",
    T % 21 + " at ./Runtime/VulkanRHI/Private/VulkanSynchronization.cpp:136 with error VK_ERROR_DEVICE_LOST",
    T % 22 + "LogVulkanRHI: Error: FVulkanDynamicRHI.TerminateOnGPUCrash"])
UE_LOG_BINDLESS = UE_LOG_VULKAN_OK.split("\n[2026.11.11-01.12")[0] + "\n" + "\n".join([
    T % 20 + "LogRHI: Warning: Bindless in Vulkan must currently be fully enabled (all samplers and resources) or fully disabled.",
    T % 21 + "LogCore: Error: appError called: Assertion failed: InDevice->SupportsBindless() [File:./Runtime/VulkanRHI/Private/VulkanRayTracing.cpp] [Line: 1441]",
    T % 22 + "Vulkan ray tracing pipelines are only supported in bindless."])
