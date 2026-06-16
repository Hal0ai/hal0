#!/usr/bin/env bash
# Launch (or resume) the ComfyUI container on hal0 iGPU (gfx1151).
# - Image is DIGEST-PINNED for reproducibility (a re-pull can't silently change the build).
#   To update: pull a new tag, then replace the @sha256 below with its RepoDigest.
# - --restart no: does NOT auto-start on boot, so it never contends with Lemonade at boot.
#   (After a CT reboot, run this script to bring ComfyUI back up.)
# - On a FRESH create it self-heals node deps (which live in the ephemeral container venv).
set -euo pipefail
IMG=docker.io/kyuz0/amd-strix-halo-comfyui@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2
NAME=comfyui
ROOT=/mnt/ai-models/comfyui
if docker ps -a --format "{{.Names}}" | grep -qx "$NAME"; then
  docker start "$NAME" >/dev/null && echo "[comfy-up] resumed existing container"
else
  docker run -d --name "$NAME" --restart no \
    --device /dev/kfd --device /dev/dri \
    --group-add video --group-add render \
    --security-opt apparmor=unconfined --security-opt seccomp=unconfined \
    --ipc=host --shm-size=8g \
    -p 8188:8188 \
    -v "$ROOT/models":/root/comfy-models \
    -v "$ROOT/output":/opt/ComfyUI/output \
    -v "$ROOT/input":/opt/ComfyUI/input \
    -v "$ROOT/user":/opt/ComfyUI/user \
    -v "$ROOT/custom_nodes":/opt/ComfyUI/custom_nodes \
    -v "$ROOT/extra_model_paths.yaml":/opt/ComfyUI/extra_model_paths.yaml:ro \
    --entrypoint bash "$IMG" \
    -lc "cd /opt/ComfyUI && exec python main.py --listen 0.0.0.0 --port 8188 --disable-mmap --bf16-vae --cache-none" >/dev/null
  echo "[comfy-up] created container — waiting for it, then installing custom-node deps (fresh venv)…"
  for i in $(seq 1 40); do [ "$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8188/ 2>/dev/null)" = "200" ] && break; sleep 3; done
  if /opt/comfyui/comfy-postinstall.sh; then
    docker restart "$NAME" >/dev/null && echo "[comfy-up] node deps installed; restarted to load them"
  else
    echo "[comfy-up] WARN: postinstall failed — run /opt/comfyui/comfy-postinstall.sh then 'docker restart comfyui'"
  fi
fi
echo "[comfy-up] ComfyUI → http://$(hostname -I | awk '{print $1}'):8188   (logs: /opt/comfyui/comfy-logs.sh)"
