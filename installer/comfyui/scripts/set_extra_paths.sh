#!/bin/bash
# set_extra_paths.sh
# Vendored from https://raw.githubusercontent.com/kyuz0/amd-strix-halo-comfyui-toolboxes/main/scripts/set_extra_paths.sh
# vendored 2026-06-16 — adapted: MODEL_DIR defaults to /mnt/ai-models/comfyui/models (hal0 store)

set -euo pipefail

CONFY_DIR="${CONFY_DIR:-/opt/ComfyUI}"
YAML_FILE="$CONFY_DIR/extra_model_paths.yaml"
MODEL_DIR="${MODEL_DIR:-/mnt/ai-models/comfyui/models}"

mkdir -p "$MODEL_DIR"/{text_encoders,vae,diffusion_models,loras}

cat > "$YAML_FILE" <<EOF
comfyui:
    base_path: $MODEL_DIR

    text_encoders: text_encoders
    vae: vae
    checkpoints: checkpoints
    diffusion_models: diffusion_models
    unet: unet
    loras: loras
    latent_upscale_models: latent_upscale_models
    clip_vision: clip_vision
EOF

echo "✅ Wrote $YAML_FILE"
