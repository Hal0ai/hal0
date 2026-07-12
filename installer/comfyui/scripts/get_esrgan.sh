#!/usr/bin/env bash
# get_esrgan.sh — download ESRGAN upscale models for ComfyUI
# Targets: upscale_models/4x-UltraSharp.pth + upscale_models/RealESRGAN_x4plus.pth
# hal0 model store: /mnt/ai-models/comfyui/models
# Follows kyuz0 vendored-script conventions (curl download, dry-run).
#
# #1200: the previous 4x-UltraSharp source (Kim2091/4x-UltraSharp) began
# returning HTTP 401, silently breaking the upscale workflow path. It is now
# pulled from the stable public mirror lokCX/4x-Ultrasharp. Each asset reports
# the specific source it failed on; 4x-UltraSharp is treated as OPTIONAL (a
# 401/404 there logs a skip and does not fail the run) while RealESRGAN_x4plus
# is REQUIRED (its failure exits non-zero).
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
  echo "  upscale_models/  4x-UltraSharp.pth     (optional)"
  echo "  upscale_models/  RealESRGAN_x4plus.pth (required)"
  exit 0
fi

mkdir -p "$MODEL_DIR/upscale_models"

# download_if_missing <url> <optional|required>
#   Returns 0 on success or when the file is already present. On failure it
#   prints the exact asset + source that failed and returns 1; the caller
#   decides whether that is fatal (required) or skippable (optional).
download_if_missing() {
  local url="$1"
  local kind="${2:-required}"
  local name
  name="$(basename "$url")"
  local dest_file="$MODEL_DIR/upscale_models/$name"

  if [[ -f "$dest_file" ]]; then
    echo "✓ Already present: $dest_file"
    return 0
  fi

  echo "↓ Downloading $name ($kind) → $dest_file"
  # Stage to a temp file so a partial/failed download never leaves a truncated
  # .pth in place (preserves idempotency on re-run).
  local tmp="${dest_file}.part"
  if curl -fL --progress-bar -o "$tmp" "$url"; then
    mv -f "$tmp" "$dest_file"
    echo "✓ Downloaded $name"
    return 0
  fi
  rm -f "$tmp"
  echo "✗ FAILED to download $name [$kind] from: $url" >&2
  return 1
}

rc=0

# 4x-UltraSharp — OPTIONAL. Widely used ESRGAN upscale model; served from the
# stable public mirror lokCX/4x-Ultrasharp (#1200: the old Kim2091 source 401s).
if ! download_if_missing \
  "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth" \
  "optional"; then
  echo "⚠ 4x-UltraSharp.pth unavailable — skipping (optional asset)." >&2
fi

# RealESRGAN x4plus — REQUIRED (xinntao/Real-ESRGAN official release).
if ! download_if_missing \
  "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth" \
  "required"; then
  rc=1
fi

if [[ "$rc" -ne 0 ]]; then
  echo "✗ ESRGAN: a required model failed to download (see errors above)." >&2
  exit "$rc"
fi

echo "✓ ESRGAN models ready in $MODEL_DIR/upscale_models"
