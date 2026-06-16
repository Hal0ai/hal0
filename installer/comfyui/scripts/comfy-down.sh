#!/usr/bin/env bash
set -euo pipefail
docker stop comfyui >/dev/null 2>&1 && echo "[comfy-down] stopped" || echo "[comfy-down] not running"
