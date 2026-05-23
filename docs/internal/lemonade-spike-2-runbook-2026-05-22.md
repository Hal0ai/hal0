# Lemonade Spike #2 — Concurrency Runbook — 2026-05-22

Purpose: validate the per-type LRU + multi-process topology choices that came out of the 4-agent research pass, before locking ADR-0008.

Target host: `ssh hal0` (root@10.0.1.142, LXC 105).

Three phases, gated. Stop and report if a phase fails its acceptance criteria.

---

## Prerequisites

- [ ] `lemonade --version` on hal0 reports v10.6.0 or later
- [ ] llamacpp:rocm backend installed (`lemonade backends list` shows `llamacpp:rocm = INSTALLED`)
- [ ] `unzip` present (`which unzip`)
- [ ] Test models on disk under `/mnt/ai-models/local/`:
  - Chat A: `hermes-4-14b-q5_k_m.gguf` (current primary, parity confirmed in spike #1)
  - Chat B: `qwen3.5-0.8b-nano.gguf` (small, fast load — second concurrent slot)
  - Embed: `nomic-embed-text-v1-q8_0.gguf`
- [ ] No live hal0-api running on 8081 (avoid port collision)

---

## Phase A — Cross-process isolation

**Hypothesis:** two Lemonade processes on different ports + separate `LEMONADE_HOME` dirs are fully isolated. Eviction on one cannot touch the other. Embed and chat can serve concurrently.

### A.1 — Start two lemonds

```bash
# Process 1: embed-only
LEMONADE_HOME=/tmp/spike2/embed lemonade server \
  --host 127.0.0.1 --port 8001 \
  --log-level info > /tmp/spike2/embed.log 2>&1 &
echo $! > /tmp/spike2/embed.pid

# Process 2: chat-only
LEMONADE_HOME=/tmp/spike2/chat lemonade server \
  --host 127.0.0.1 --port 8002 \
  --log-level info > /tmp/spike2/chat.log 2>&1 &
echo $! > /tmp/spike2/chat.pid

sleep 5
curl -s http://127.0.0.1:8001/v1/health | jq .
curl -s http://127.0.0.1:8002/v1/health | jq .
```

**Acceptance A.1:** both `/v1/health` return 200, distinct `state` blocks, no cross-pollination of `loaded[]`.

### A.2 — Register embed model on :8001 via `/v1/pull`

```bash
curl -s -X POST http://127.0.0.1:8001/v1/pull \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "user.nomic-embed-text-v1",
    "checkpoint": "/mnt/ai-models/local/nomic-embed-text-v1-q8_0.gguf",
    "recipe": "llamacpp",
    "embedding": true,
    "llamacpp_backend": "rocm"
  }' | jq .
```

**Acceptance A.2:** response shows `status: registered` (or equivalent), model appears under `/v1/models?show_all=true`.

### A.3 — Load embed and test

```bash
curl -s -X POST http://127.0.0.1:8001/v1/load \
  -H "Content-Type: application/json" \
  -d '{"model_name":"user.nomic-embed-text-v1"}' | jq .

# OpenAI-compat embed call
time curl -s -X POST http://127.0.0.1:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"user.nomic-embed-text-v1","input":"the quick brown fox"}' \
  | jq '.data[0].embedding | length'
```

**Acceptance A.3:** returns a non-empty embedding vector (768 or 4096 dim depending on model). Latency recorded.

### A.4 — Register + load chat model on :8002

```bash
# Direct load — hermes-4-14b should already be in server_models.json
curl -s -X POST http://127.0.0.1:8002/v1/load \
  -H "Content-Type: application/json" \
  -d '{"model_name":"hermes-4-14b","llamacpp_backend":"rocm"}' | jq .

# Smoke
time curl -s -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"hermes-4-14b",
    "messages":[{"role":"user","content":"Reply with the single word: OK"}],
    "max_tokens":8
  }' | jq -r '.choices[0].message.content'
```

**Acceptance A.4:** chat returns `OK`. Both `/v1/health` calls confirm one model loaded on each port.

### A.5 — Concurrency under load

```bash
# Run 5 embed requests + 5 chat requests in parallel
for i in 1 2 3 4 5; do
  (curl -s -X POST http://127.0.0.1:8001/v1/embeddings \
    -H "Content-Type: application/json" \
    -d '{"model":"user.nomic-embed-text-v1","input":"sample text '$i'"}' \
    -o /tmp/spike2/e$i.json -w 'embed%{http_code} %{time_total}\n') &
  (curl -s -X POST http://127.0.0.1:8002/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"hermes-4-14b","messages":[{"role":"user","content":"count to 3"}],"max_tokens":24}' \
    -o /tmp/spike2/c$i.json -w 'chat%{http_code} %{time_total}\n') &
done
wait
```

Then:
- Record per-modality latency distribution.
- Diff `/v1/stats` snapshots before and after.
- Confirm no `"evicting all models"` line in either log.

**Acceptance A.5:** all 10 requests succeed, no nuclear evict in either log, embed p50 within 2× of A.3 baseline, chat tok/s within 80% of single-tenant baseline.

### A.6 — Forced-error isolation test

Try to load a deliberately-broken model on :8002 to force a nuclear evict — confirm :8001 is unaffected.

```bash
curl -s -X POST http://127.0.0.1:8002/v1/load \
  -H "Content-Type: application/json" \
  -d '{"model_name":"hermes-4-14b","llamacpp_args":"--gpu-layers 9999 --ctx-size 2147483647"}' || true

sleep 3
curl -s http://127.0.0.1:8002/v1/health | jq '.loaded'  # may be []
curl -s http://127.0.0.1:8001/v1/health | jq '.loaded'  # must still show nomic
```

**Acceptance A.6:** :8001 still has the embed model loaded after :8002's nuke. Cross-process isolation confirmed.

### Cleanup A

```bash
kill $(cat /tmp/spike2/embed.pid) $(cat /tmp/spike2/chat.pid) || true
```

---

## Phase B — Per-type LRU at limit=2

**Hypothesis:** raising `max_loaded_models` for the LLM type allows two GPU chat models to be co-resident in ONE Lemonade process. Concurrent inference against both works. LRU evicts oldest only when third is loaded.

### B.1 — Find the limit knob

Per architect agent's source read, `max_loaded_models` is per-type. Knob may be:
- `lemonade config set max_loaded_models.llm 2` (most likely)
- Env: `LEMONADE_MAX_LOADED_MODELS_LLM=2`
- Server flag: `--max-loaded-llm 2`

Try in order. Confirm via `/v1/health` showing the new limit.

```bash
lemonade config set max_loaded_models.llm 2 || true
LEMONADE_HOME=/tmp/spike2/multi LEMONADE_MAX_LOADED_MODELS_LLM=2 \
  lemonade server --host 127.0.0.1 --port 8003 \
  --log-level info > /tmp/spike2/multi.log 2>&1 &
echo $! > /tmp/spike2/multi.pid

sleep 5
curl -s http://127.0.0.1:8003/v1/health | jq '.max_loaded_models, .limits'
```

**Acceptance B.1:** `/v1/health` reports limit ≥ 2 for the LLM type.

### B.2 — Load two GPU chat models

```bash
curl -s -X POST http://127.0.0.1:8003/v1/load \
  -d '{"model_name":"hermes-4-14b","llamacpp_backend":"rocm"}' \
  -H "Content-Type: application/json" | jq .

curl -s -X POST http://127.0.0.1:8003/v1/load \
  -d '{"model_name":"qwen3.5-0.8b","llamacpp_backend":"rocm"}' \
  -H "Content-Type: application/json" | jq .

curl -s http://127.0.0.1:8003/v1/health | jq '.loaded[].model_name'
```

**Acceptance B.2:** both models present in `loaded[]`. No eviction log line. RAM (free -h / nvtop / radeontop) shows roughly sum of both.

### B.3 — Concurrent inference, both models

```bash
for i in 1 2 3; do
  (time curl -s -X POST http://127.0.0.1:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"hermes-4-14b","messages":[{"role":"user","content":"reply ONE"}],"max_tokens":8}' \
    -o /tmp/spike2/B-h$i.json) &
  (time curl -s -X POST http://127.0.0.1:8003/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3.5-0.8b","messages":[{"role":"user","content":"reply TWO"}],"max_tokens":8}' \
    -o /tmp/spike2/B-q$i.json) &
done
wait

# Are the queries even hitting both? Confirm via /v1/stats per-model.
curl -s http://127.0.0.1:8003/v1/stats | jq .
```

**Acceptance B.3:** all 6 requests succeed. Responses correspond to the right model (hermes returns "ONE"-ish, qwen returns "TWO"-ish). tok/s for hermes within 70% of single-tenant baseline (allowing for GPU contention).

### B.4 — Trigger LRU (load a third)

```bash
curl -s -X POST http://127.0.0.1:8003/v1/load \
  -d '{"model_name":"qwen3.6-35b-a3b","llamacpp_backend":"rocm"}' \
  -H "Content-Type: application/json" | jq .

curl -s http://127.0.0.1:8003/v1/health | jq '.loaded[].model_name'
grep -i evict /tmp/spike2/multi.log | tail
```

**Acceptance B.4:** `loaded[]` shows exactly 2 models (the 35b plus most-recently-used of the prior two). Log shows a NORMAL eviction line (not "non-file-not-found error, evicting all models"). LRU policy confirmed.

### Cleanup B

```bash
kill $(cat /tmp/spike2/multi.pid) || true
```

---

## Phase C — NPU + Lemonade cross-provider concurrency

**Hypothesis:** the FLM/NPU toolbox running independently + a Lemonade process running embed+chat = all three serving concurrently with no cross-interference. This is the v0.2 hybrid-provider target.

### C.1 — Start Lemonade with embed + chat (one process, per-type budget 1 LLM + 1 embed)

```bash
LEMONADE_HOME=/tmp/spike2/hybrid lemonade server \
  --host 127.0.0.1 --port 8004 \
  --log-level info > /tmp/spike2/hybrid.log 2>&1 &
echo $! > /tmp/spike2/hybrid.pid
sleep 5

# Re-register embed (per-LEMONADE_HOME so user.* namespace is fresh)
curl -s -X POST http://127.0.0.1:8004/v1/pull -H "Content-Type: application/json" \
  -d '{"model_name":"user.nomic-embed-text-v1","checkpoint":"/mnt/ai-models/local/nomic-embed-text-v1-q8_0.gguf","recipe":"llamacpp","embedding":true,"llamacpp_backend":"rocm"}'

curl -s -X POST http://127.0.0.1:8004/v1/load \
  -d '{"model_name":"user.nomic-embed-text-v1"}' -H "Content-Type: application/json"
curl -s -X POST http://127.0.0.1:8004/v1/load \
  -d '{"model_name":"hermes-4-14b","llamacpp_backend":"rocm"}' -H "Content-Type: application/json"
```

**Acceptance C.1:** `/v1/health.loaded` shows both, types LLM + embed.

### C.2 — Bring up FLM/NPU toolbox

Per memory `hal0_flm_models_mount_path`: `docker run` MUST bind-mount the host FLM cache to `/var/lib/hal0/.config/flm/models`.

```bash
# Pull a small FLM-namespace model (e.g. qwen2:1.5b — verify from `flm list -j`)
docker run --rm -d --name spike2-flm \
  --device /dev/dxg --device /dev/accel/accel0 \
  -v /mnt/ai-models/flm:/var/lib/hal0/.config/flm/models \
  -p 8005:8000 \
  ghcr.io/hal0ai/hal0-toolbox-flm:v1 \
  flm serve --host 0.0.0.0 --port 8000

sleep 8
curl -s http://127.0.0.1:8005/v1/models | jq '.data[].id' | head
```

Verify NPU is detected:
```bash
docker logs spike2-flm 2>&1 | grep -i -E 'xrt|npu|amdxdna'
```

**Acceptance C.2:** FLM serves, an FLM-namespace model is loadable, NPU visible in logs (`amdxdna` device).

### C.3 — Triple concurrent inference

```bash
for i in 1 2 3; do
  # GPU chat via Lemonade
  (curl -s -X POST http://127.0.0.1:8004/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"hermes-4-14b","messages":[{"role":"user","content":"GPU chat ping"}],"max_tokens":16}' \
    -w 'gpu-chat %{time_total}\n' -o /tmp/spike2/C-gc$i.json) &
  # GPU embed via Lemonade
  (curl -s -X POST http://127.0.0.1:8004/v1/embeddings \
    -H "Content-Type: application/json" \
    -d '{"model":"user.nomic-embed-text-v1","input":"sample '$i'"}' \
    -w 'gpu-emb %{time_total}\n' -o /tmp/spike2/C-ge$i.json) &
  # NPU chat via FLM
  (curl -s -X POST http://127.0.0.1:8005/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen2:1.5b","messages":[{"role":"user","content":"NPU chat ping"}],"max_tokens":16}' \
    -w 'npu-chat %{time_total}\n' -o /tmp/spike2/C-nc$i.json) &
done
wait
```

**Acceptance C.3:** all 9 requests succeed. Per-modality p50 within 80% of its solo baseline. No nuclear evict in either Lemonade log. FLM toolbox unaffected.

### Cleanup C

```bash
kill $(cat /tmp/spike2/hybrid.pid) || true
docker stop spike2-flm || true
```

---

## Outputs

After all phases:

1. Write findings into `/home/halo/dev/hal0/docs/internal/lemonade-spike-2-findings-2026-05-22.md` with:
   - Pass/fail per phase
   - Latency tables (single-tenant vs concurrent)
   - Eviction events observed
   - Any anomalies / new bugs discovered
2. Decide ADR-0008 topology:
   - Phase A pass → cross-process isolation works → multi-process topology is safe (worst-case fallback).
   - Phase B pass → per-type LRU at limit=2 works → SINGLE-process topology is viable (architect's preferred path).
   - Phase C pass → hybrid Lemonade+FLM works → v0.2 target architecture confirmed.

## Stop conditions

- If A.1 fails (lemonds won't start): abort, diagnose env.
- If A.5 fails (concurrent requests degrade > 50%): architect's single-process recommendation is wrong; escalate to multi-process.
- If B.1 fails (can't raise limit): architect's preferred path is blocked; multi-process is mandatory.
- If B.4 nukes nuclear: per-type LRU is broken; multi-process mandatory.
- If C.2 fails: FLM/NPU prereq blocks; investigate libxrt-npu2 before retry.
