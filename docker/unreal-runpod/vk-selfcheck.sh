#!/usr/bin/env bash
# Vulkan self-check. Prints and saves a PASS/FAIL report. Safe to re-run any time:
#   vk-selfcheck
# PASS = vulkaninfo lists an NVIDIA GPU that is not a software renderer.
unset DISPLAY WAYLAND_DISPLAY
OUT=/workspace/vulkan-report.txt
[ -w /workspace ] || OUT=/tmp/vulkan-report.txt

{
echo "=== x3d Vulkan self-check  $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "--- host / driver ---"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1
grep PRETTY /etc/os-release; ldd --version | head -1
echo "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-unset}"
echo "--- icd manifests ---"
ls -la /etc/vulkan/icd.d /usr/share/vulkan/icd.d 2>&1
echo "--- host-injected nvidia libs ---"
ldconfig -p | grep -Ei 'libGLX_nvidia|libnvidia-(glcore|glsi|gpucomp|glvkspirv|rtcore)' | awk '{print $1, $NF}' | sort -u
echo "--- vulkaninfo --summary ---"
VK=$(VK_LOADER_DEBUG=error vulkaninfo --summary 2>&1)
echo "$VK" | grep -vE 'implicit_layer|XDG_RUNTIME' | head -40
echo "--- loader errors ---"
echo "$VK" | grep -iE 'ERROR|Found no drivers|Could not get' | sort -u | head -10
echo "--- result ---"
DEV=$(echo "$VK" | grep -E 'deviceName' | head -3)
if echo "$DEV" | grep -qi nvidia && ! echo "$DEV" | grep -qiE 'llvmpipe|swiftshader'; then
  echo "VULKAN-SELFCHECK: PASS  ($(echo "$DEV" | head -1 | sed 's/^ *//'))"
else
  echo "VULKAN-SELFCHECK: FAIL  (no NVIDIA Vulkan device; device lines: ${DEV:-none})"
fi
} 2>&1 | tee "$OUT"
echo "(report saved to $OUT)"
