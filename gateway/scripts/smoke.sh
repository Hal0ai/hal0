#!/usr/bin/env bash
# Local smoke test for the hal0 Bifrost gateway. Run AFTER `make build`, on a
# host that can reach a lemond with an LLM slot loaded (e.g. CT105 post-deploy,
# or with GATEWAY_LEMOND pointed at one over SSH-forward).
#
# Verifies: (1) gateway answers, (2) a request with model "lemonade/primary"
# routes to the live slot, (3) enable_thinking=false reaches the upstream.
set -euo pipefail

GW="${GW:-http://127.0.0.1:8079}"
LEMOND_HEALTH="${LEMOND_HEALTH:-http://127.0.0.1:13305/api/v1/health}"

echo "== lemond loaded slot =="
curl -s --max-time 5 "$LEMOND_HEALTH" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print([m["model_name"] for m in d.get("all_models_loaded",[]) if m.get("type")=="llm"] or "NONE LOADED")'

echo "== gateway chat with virtual name lemonade/primary =="
curl -s --max-time 120 "$GW/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "lemonade/primary",
        "messages": [{"role":"user","content":"Reply with exactly: OK"}],
        "max_tokens": 8,
        "temperature": 0
      }' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("served model:", d.get("model")); print("content:", d.get("choices",[{}])[0].get("message",{}).get("content"))'

echo
echo "NOTE: to prove enable_thinking=false hit the wire, tail the gateway log"
echo "(BIFROST request log) or lemond's request log during the call above."
