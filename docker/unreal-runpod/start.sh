#!/usr/bin/env bash
# Container entrypoint: optional SSH (RunPod PUBLIC_KEY), self-check, then stay alive.
if [ -n "$PUBLIC_KEY" ]; then
  mkdir -p /root/.ssh && echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
  service ssh start >/dev/null 2>&1 || true
fi
vk-selfcheck
echo "Pod ready. Re-run the check any time with: vk-selfcheck"
sleep infinity
