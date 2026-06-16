#!/usr/bin/env bash
# Stop hal0 iGPU inference so ComfyUI has the GPU/unified memory to itself.
# Scope (per user choice): Lemonade (iGPU model runner) + Hermes agent (depends on it).
# Side-effect: Hindsight memory *extraction* (LLM via lemonade:13305 -> gemma3-4b on NPU)
#   pauses while down. Retain calls queue; embeddings/rerank are CPU-pinned and unaffected.
# Restore with: /opt/comfyui/start-inference.sh
set -euo pipefail
echo "[stop-inference] stopping hal0-agent@hermes.service ..."
systemctl stop hal0-agent@hermes.service || true
echo "[stop-inference] stopping hal0-lemonade.service ..."
systemctl stop hal0-lemonade.service || true
sleep 2
echo "[stop-inference] state:"
systemctl is-active hal0-lemonade.service hal0-agent@hermes.service hindsight-api.service || true
echo "[stop-inference] iGPU memory now:"
free -h | awk "NR==1||/Mem:/"
echo "[stop-inference] NOTE: Telegram/Discord (Hermes) are DARK and Hindsight extraction is PAUSED until start-inference.sh."
