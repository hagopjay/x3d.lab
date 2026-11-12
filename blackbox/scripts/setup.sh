#!/usr/bin/env bash
# Reproducible environment for BLACKBOX.
#
# CPU baseline:
#   bash scripts/setup.sh
# Optional host-side Vulkan tools (does NOT install an NVIDIA driver):
#   BLACKBOX_GPU_TOOLS=1 bash scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${HOME}/.cache"
NODE_VERSION="${NODE_VERSION:-v22.20.0}"
CHROME_VERSION="${CHROME_VERSION:-159.0.8129.0}"
CHROME_DIR="${CACHE}/chrome-${CHROME_VERSION%%.*}"
CHROME_BIN="${CHROME_DIR}/chrome-linux64/chrome"
NODE_DIR="${CACHE}/rt/node-${NODE_VERSION}-linux-x64"
NODE_BIN="${NODE_DIR}/bin/node"

export DEBIAN_FRONTEND=noninteractive

echo "▸ Chrome runtime libraries"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libasound2 libxdamage1 libatspi2.0-0 libxcomposite1 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libxfixes3 libdrm2 fonts-liberation unzip xz-utils curl

if [[ "${BLACKBOX_GPU_TOOLS:-0}" == "1" ]]; then
  echo "▸ Optional Vulkan diagnostic CLI (host driver is supplied by the runtime)"
  sudo apt-get install -y --no-install-recommends vulkan-tools
fi

echo "▸ Node ${NODE_VERSION} (chrome-devtools-mcp heap tools require >= 22)"
mkdir -p "${CACHE}/rt"
if [ ! -x "${NODE_BIN}" ]; then
  curl -fSsL "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ -C "${CACHE}/rt"
fi
# Keep npm's CLI, but ensure its node shebang resolves to the pinned Node 22 binary.
export PATH="${NODE_DIR}/bin:${PATH}"

CHROME_VERSION_OUTPUT=""
if [ -x "${CHROME_BIN}" ]; then CHROME_VERSION_OUTPUT="$("${CHROME_BIN}" --version 2>/dev/null || true)"; fi
if [[ "${CHROME_VERSION_OUTPUT}" != *"${CHROME_VERSION}"* ]]; then
  echo "▸ Chrome for Testing ${CHROME_VERSION}"
  mkdir -p "${CHROME_DIR}"
  curl -fSsL "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip" -o /tmp/blackbox-chrome.zip
  unzip -q -o /tmp/blackbox-chrome.zip -d "${CHROME_DIR}"
  rm -f /tmp/blackbox-chrome.zip
else
  echo "▸ Chrome already present: ${CHROME_VERSION_OUTPUT}"
fi

if [ ! -d "${ROOT}/node_modules/chrome-devtools-mcp" ]; then
  echo "▸ Installing chrome-devtools-mcp@1.13.0"
  cd "${ROOT}"
  npm ci --no-audit --no-fund
else
  echo "▸ chrome-devtools-mcp dependencies already present"
fi

# Optional X3D Lab Blender leg: bpy (needs Python 3.13) + the x3d-blender-io add-on.
if [ "${BLACKBOX_X3D_BLENDER:-0}" = "1" ]; then
  PY313="$(command -v python3.13 || true)"
  if [ -z "${PY313}" ]; then
    echo "▸ X3D Blender leg skipped: bpy 5.3 needs python3.13 on PATH"
  else
    echo "▸ X3D Blender leg: bpy into .venv-bpy, add-on into .cache/x3d-blender-io"
    [ -d "${ROOT}/.venv-bpy" ] || "${PY313}" -m venv "${ROOT}/.venv-bpy"
    "${ROOT}/.venv-bpy/bin/pip" install --quiet "bpy==5.3.*"
    if [ ! -d "${ROOT}/.cache/x3d-blender-io/.git" ]; then
      git clone --quiet --depth 1 https://github.com/hagopjay/x3d-blender-io "${ROOT}/.cache/x3d-blender-io"
    else
      git -C "${ROOT}/.cache/x3d-blender-io" pull --quiet --ff-only || true
    fi
    X3D_ENV="export X3DLAB_BPY_PYTHON=${ROOT}/.venv-bpy/bin/python X3DLAB_ADDON=${ROOT}/.cache/x3d-blender-io/source"
  fi
fi

export CHROME_PATH="${CHROME_BIN}"
echo "▸ Environment preflight"
"${NODE_BIN}" "${ROOT}/agent/gpu-preflight.mjs" | sed -n '1,100p'

cat <<EOF

✅ BLACKBOX ready

  Node:   ${NODE_BIN}
  Chrome: ${CHROME_BIN}

  start the control plane:
    CHROME_PATH=${CHROME_BIN} ${NODE_BIN} server.mjs

  then open http://localhost:8080 and press "Run mission".
  The optional WebGPU lane is under the WebGPU Lab tab, or run it headless:
    CHROME_PATH=${CHROME_BIN} ${NODE_BIN} agent/webgpu-smoke.mjs --origin http://127.0.0.1:8080

  X3D Lab: npm run x3d:build, then http://localhost:8080/lab/x3d/ or npm run x3d:mission
    ${X3D_ENV:-(Blender leg off: BLACKBOX_X3D_BLENDER=1 bash scripts/setup.sh)}

  On a CPU-only runtime, the GPU lane records WebGPU as unavailable/software-backed;
  it never reports a hardware pass unless host-side GPU evidence exists.
EOF
