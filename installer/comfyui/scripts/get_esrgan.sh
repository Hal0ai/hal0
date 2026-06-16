#!/usr/bin/env bash
# get_esrgan.sh — download ESRGAN upscale models for ComfyUI
# Targets: upscale_models/4x-UltraSharp.pth + upscale_models/RealESRGAN_x4plus.pth
# hal0 model store: /mnt/ai-models/comfyui/models
# Follows kyuz0 vendored-script conventions (curl download, dry-run).
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/mnt/ai-models/comfyui/models}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] MODEL_DIR=$MODEL_DIR"
  echo "[dry-run] Would download to:"
  echo "  upscale_models/  4x-UltraSharp.pth"
  echo "  upscale_models/  RealESRGAN_x4plus.pth"
  exit 0
fi

mkdir -p "$MODEL_DIR/upscale_models"

download_if_missing() {
  local url="$1"
  local dest_file="$MODEL_DIR/upscale_models/$(basename "$url")"

  if [[ -f "$dest_file" ]]; then
    echo "✓ Already present: $dest_file"
    return
  fi

  echo "↓ Downloading $(basename "$url") → $dest_file"
  curl -fL --progress-bar -o "$dest_file" "$url"
}

# 4x-UltraSharp (widely used ESRGAN upscale model)
download_if_missing \
  "https://huggingface.co/Kim2091/4x-UltraSharp/resolve/main/4x-UltraSharp.pth"

# RealESRGAN x4plus (xinntao/Real-ESRGAN official release)
download_if_missing \
  "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"

echo "✓ ESRGAN models ready in $MODEL_DIR/upscale_models"
