#!/usr/bin/env bash
# Reinstall custom-node Python deps into the container venv.
# WHY: node *code* lives on the mounted custom_nodes (persistent), but node *deps*
# live in the container's /opt/venv (ephemeral). After any `docker rm` + recreate
# the deps are gone and the added nodes fail to import. comfy-up.sh runs this
# automatically on a fresh create; run it by hand if you ever recreate manually
# or add nodes via the Manager UI and want them to survive a recreate.
set -uo pipefail
NAME=comfyui
if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "[comfy-postinstall] container '$NAME' not running — start it first (comfy-up.sh)"; exit 1
fi
docker exec "$NAME" bash /root/comfy-models/.node-install.sh
echo "[comfy-postinstall] node deps reinstalled"
