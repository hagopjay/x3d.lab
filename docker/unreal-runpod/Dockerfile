# RunPod image for the x3d Unreal pipeline: Vulkan loader + glvnd wired the way
# NVIDIA's container toolkit expects, a non-root `ue` user, rclone, and a boot-time
# Vulkan self-check that writes a PASS/FAIL report.
# Unreal Engine itself is NOT baked in (it lives on the network volume).
# Deliberately NO mesa-vulkan-drivers: a software ICD (llvmpipe) could fake a pass.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip xz-utils sudo less nano git procps \
      openssh-server \
      libvulkan1 vulkan-tools \
      libglvnd0 libgl1 libglx0 libegl1 libgles2 libopengl0 \
      libx11-6 libxext6 libxcb1 libxkbcommon0 libxi6 libxrandr2 libxcursor1 \
      libxinerama1 libxcomposite1 libxdamage1 libxss1 libsm6 libice6 \
      libnss3 libasound2 libgbm1 libdbus-1-3 libfontconfig1 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# rclone (official build; apt's is old). Used to pull the engine zip from Google Drive.
RUN curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip \
 && unzip -q /tmp/rclone.zip -d /tmp && cp /tmp/rclone-*-linux-amd64/rclone /usr/local/bin/rclone \
 && chmod 755 /usr/local/bin/rclone && rm -rf /tmp/rclone*

# Fallback manifests, used only if the host does not inject its own (the host's win).
RUN mkdir -p /usr/share/vulkan/icd.d /usr/share/glvnd/egl_vendor.d \
 && printf '{\n  "file_format_version" : "1.0.0",\n  "ICD": { "library_path": "libGLX_nvidia.so.0", "api_version" : "1.3.0" }\n}\n' > /usr/share/vulkan/icd.d/nvidia_icd.json \
 && printf '{\n  "file_format_version" : "1.0.0",\n  "ICD": { "library_path": "libEGL_nvidia.so.0" }\n}\n' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json

# Unreal refuses to run as root.
RUN useradd -m -s /bin/bash ue && echo 'ue ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ue \
 && mkdir -p /workspace && chown ue:ue /workspace

COPY vk-selfcheck.sh /usr/local/bin/vk-selfcheck
COPY start.sh /start.sh
RUN chmod 755 /usr/local/bin/vk-selfcheck /start.sh

WORKDIR /workspace
CMD ["/start.sh"]
