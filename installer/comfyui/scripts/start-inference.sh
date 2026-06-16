#!/usr/bin/env bash
# Restore hal0 inference after ComfyUI work. Reverses stop-inference.sh.
set -euo pipefail
echo "[start-inference] starting hal0-lemonade.service ..."
systemctl start hal0-lemonade.service || true
sleep 3
echo "[start-inference] starting hal0-agent@hermes.service ..."
systemctl start hal0-agent@hermes.service || true
sleep 2
echo "[start-inference] state:"
systemctl is-active hal0-lemonade.service hal0-agent@hermes.service hindsight-api.service || true
echo "[start-inference] Hermes messaging + Hindsight extraction restored."
