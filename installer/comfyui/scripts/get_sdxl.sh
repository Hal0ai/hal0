#!/usr/bin/env bash
# get_sdxl.sh — download SDXL base + SDXL-Lightning LoRA + SDXL VAE for ComfyUI
# hal0 model store: /mnt/ai-models/comfyui/models
# Follows kyuz0 vendored-script conventions (hf download, resume, dry-run).
set -euo pipefail

export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
HF="/opt/venv/bin/hf"

MODEL_DIR="${MODEL_DIR:-/mnt/ai-models/comfyui/models}"
STAGE="$MODEL_DIR/.hf_stage_sdxl"

PRECISION="${PRECISION:-fp16}"
DRY_RUN=0

# Parse args
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --precision=*) PRECISION="${arg#--precision=}" ;;
    --precision) shift; PRECISION="${1:-fp16}" ;;
  esac
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] MODEL_DIR=$MODEL_DIR  PRECISION=$PRECISION"
  echo "[dry-run] Would download to:"
  echo "  checkpoints/   stabilityai/stable-diffusion-xl-base-1.0 → sd_xl_base_1.0.safetensors"
  echo "  loras/         ByteDance/SDXL-Lightning → sdxl_lightning_8step_lora.safetensors"
  echo "  vae/           madebyollin/sdxl-vae-fp16-fix → diffusion_pytorch_model.safetensors"
  exit 0
fi

mkdir -p "$MODEL_DIR"/{checkpoints,loras,vae}
mkdir -p "$STAGE"

download_if_missing() {
  local repo="$1"
  local remote="$2"
  local dest_path="$3"
  local dest_name="${4:-$(basename "$remote")}"

  local dest_dir="$MODEL_DIR/$dest_path"
  local dest_file="$dest_dir/$dest_name"
  local staged="$STAGE/$remote"

  if [[ -f "$dest_file" ]]; then
    echo "✓ Already present: $dest_file"
    return
  fi

  echo "↓ Downloading $(basename "$remote") → $dest_file"
  mkdir -p "$(dirname "$staged")"
  mkdir -p "$dest_dir"

  "$HF" download "$repo" "$remote" \
      --repo-type model \
      --cache-dir "$HF_HOME" \
      --local-dir "$STAGE"
  mv -f "$staged" "$dest_file"
}

# 1. SDXL base checkpoint (stabilityai/stable-diffusion-xl-base-1.0)
echo "==> SDXL base checkpoint"
download_if_missing \
  "stabilityai/stable-diffusion-xl-base-1.0" \
  "sd_xl_base_1.0.safetensors" \
  "checkpoints"

# 2. SDXL VAE (madebyollin/sdxl-vae-fp16-fix)
echo "==> SDXL VAE (fp16-fix)"
download_if_missing \
  "madebyollin/sdxl-vae-fp16-fix" \
  "diffusion_pytorch_model.safetensors" \
  "vae" \
  "sdxl_vae_fp16_fix.safetensors"

# 3. SDXL-Lightning 8-step LoRA (ByteDance/SDXL-Lightning)
echo "==> SDXL-Lightning 8-step LoRA"
download_if_missing \
  "ByteDance/SDXL-Lightning" \
  "sdxl_lightning_8step_lora.safetensors" \
  "loras"

echo "✓ SDXL models ready in $MODEL_DIR"
