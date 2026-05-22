# Lemonade Server Spike Runbook — hal0 LXC 105

**Goal:** Evaluate Lemonade Server v10.6.0 as a unified replacement for hal0's per-modality toolboxes. Spike lives on LXC 105 (Strix Halo, gfx1151, XDNA NPU, amdxdna ≥7.0).

**Total wall-clock:** ~4–5 h hands-on, plus 1 h idle-probe wait.

**Ground rules:** All commands run as `root` on `ssh hal0` (10.0.1.142). Lemonade installs to `/opt/lemonade-spike`, listens on `:9100`. Bench artifacts land in `/root/lemonade-spike/findings/`.

---

## 1. Pre-flight checks (~10 min)

```bash
ssh hal0
mkdir -p /root/lemonade-spike/findings && cd /root/lemonade-spike

uname -r                                                    # verify: ≥ 7.0
lsmod | grep amdxdna                                        # verify: amdxdna loaded
ls /dev/accel/accel0                                        # verify: NPU char dev present
# NOTE: rocminfo not on LXC host — Lemonade ships its own ROCm runtime. Skip.
df -h /mnt/ai-models /opt /root                             # verify: ≥50 GB free on each
mount | grep /mnt/ai-models                                 # verify: rw
curl -sI https://github.com | head -1                       # verify: HTTP/2 200
systemctl list-units 'hal0-slot@*.service' --state=active --no-legend --plain \
  | awk '{print $1}' | tee active-slots.txt                 # verify: 5 services (embed, embed-rerank, primary, stt, tts)
curl -fsS http://127.0.0.1:8080/api/slots | jq 'length'     # verify: integer count of slots
```

If any fail, **stop**. Note in findings; don't paper over a missing kernel module.

---

## 2. Install Lemonade v10.6.0 (~15 min)

```bash
cd /opt
VER=10.6.0
curl -fL -o lemonade.tgz \
  "https://github.com/lemonade-sdk/lemonade/releases/download/v${VER}/lemonade-embeddable-${VER}-ubuntu-x64.tar.gz"
# verify: file ~150–300 MB
mkdir -p lemonade-spike && tar -xzf lemonade.tgz -C lemonade-spike --strip-components=1
ls /opt/lemonade-spike/{lemond,lemonade,LICENSE,resources}  # verify: 4 entries

cat > /opt/lemonade-spike/config.json <<'EOF'
{
  "port": 9100,
  "max_loaded_models": 6,
  "extra_models_dir": "/mnt/ai-models/local",
  "rocm_channel": "nightly",
  "global_timeout": 900,
  "log_level": "info"
}
EOF
# verify: jq . /opt/lemonade-spike/config.json parses

export LEMONADE_API_KEY="spike-$(openssl rand -hex 8)"
echo "$LEMONADE_API_KEY" > /root/lemonade-spike/api-key.txt
# verify: ls -l /root/lemonade-spike/api-key.txt

/opt/lemonade-spike/lemonade backends install llamacpp:rocm 2>&1 \
  | tee /root/lemonade-spike/findings/install-llamacpp-rocm.log
# verify: tail shows "installed" + ROCm 7.13+ version line

systemctl stop 'hal0-slot@*'
# verify: systemctl list-units 'hal0-slot@*' --state=active shows none

tmux new -d -s lemond \
  "LEMONADE_API_KEY=$LEMONADE_API_KEY /opt/lemonade-spike/lemond /opt/lemonade-spike --port 9100 \
    2>&1 | tee /root/lemonade-spike/findings/lemond.log"
sleep 5
curl -fsS -H "Authorization: Bearer $LEMONADE_API_KEY" http://127.0.0.1:9100/v1/health | jq .
# verify: JSON with backend_url, max_models, loaded:[]
curl -fsS -H "Authorization: Bearer $LEMONADE_API_KEY" http://127.0.0.1:9100/v1/system-info | jq .gpu
# verify: gfx1151 + ROCm version
```

---

## 3. Bench harness adaptation (~15 min)

```bash
cp /root/llm-eval/bench.sh /root/lemonade-spike/bench-lemon.sh
sed -i \
  -e 's|http://127.0.0.1:8081|http://127.0.0.1:9100|g' \
  -e "s|^AUTH_HEADER=.*|AUTH_HEADER=\"Authorization: Bearer $LEMONADE_API_KEY\"|" \
  /root/lemonade-spike/bench-lemon.sh
grep -nE 'localhost|9100|Bearer' /root/lemonade-spike/bench-lemon.sh
# verify: every endpoint hits :9100 and carries the bearer header

# Resolve registry paths once
for m in hermes-4-14b-q5_k_m qwen3.5-0.8b qwen3.6-35b-a3b-q4_k_xl qwen3-coder-next-reap-40b-a3b-q4_k_xl; do
  python3 -c "import tomllib,sys; r=tomllib.load(open('/var/lib/hal0/registry/registry.toml','rb')); print('$m', r['models']['$m']['path'])"
done > /root/lemonade-spike/model-paths.txt
# verify: 4 lines, each pointing under /mnt/ai-models/local

# Smoke a warm model
MODEL=hermes-4-14b-q5_k_m
curl -fsS -H "Authorization: Bearer $LEMONADE_API_KEY" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:9100/v1/load -d "{\"model\":\"$MODEL\",\"backend\":\"llamacpp:rocm\"}"
sleep 5
/root/lemonade-spike/bench-lemon.sh "$MODEL" 1 | tee /tmp/smoke.txt
# verify: tok/s within ±20% of 20 tok/s baseline
```

---

## 4. LLM bench protocol (~75 min)

For each model `$M` in the four targets:

```bash
M=hermes-4-14b-q5_k_m   # repeat for qwen3.5-0.8b, qwen3.6-35b-a3b-q4_k_xl, qwen3-coder-next-reap-40b-a3b-q4_k_xl
OUT=/root/lemonade-spike/findings/bench-$M.txt

# COLD: unload, drop caches, single run
curl -sS -H "Authorization: Bearer $LEMONADE_API_KEY" -X POST http://127.0.0.1:9100/v1/unload -d '{}'
sync && echo 3 > /proc/sys/vm/drop_caches
/root/lemonade-spike/bench-lemon.sh "$M" 1 cold | tee -a "$OUT"
# verify: $OUT has a "cold" row with TTFT + tok/s

# WARM: 3 runs, model stays loaded
for i in 1 2 3; do
  /root/lemonade-spike/bench-lemon.sh "$M" 1 "warm-$i" | tee -a "$OUT"
done
curl -sS -H "Authorization: Bearer $LEMONADE_API_KEY" http://127.0.0.1:9100/v1/stats | jq . >> "$OUT"
# verify: 3 warm rows + stats blob
```

Tabulate in findings template (§10) against `primary-model-eval-2026-05-22.md` baseline. Success bar is **qualitative** — gather data, judge holistically post-spike. Treat large deltas (>15 % on hermes-4-14b, TTFT >2× baseline, GTT >+25 %) as yellow flags worth interrogating, not auto-kills.

---

## 5. Concurrency test (~20 min)

```bash
KEY="Authorization: Bearer $LEMONADE_API_KEY"
# Parallel loads (3 models, max_loaded_models=6 so all should fit)
for M in hermes-4-14b-q5_k_m qwen3.5-0.8b qwen3.6-35b-a3b-q4_k_xl; do
  curl -sS -H "$KEY" -H 'Content-Type: application/json' \
    -X POST http://127.0.0.1:9100/v1/load -d "{\"model\":\"$M\"}" &
done; wait
curl -sS -H "$KEY" http://127.0.0.1:9100/v1/health | jq '.loaded'
# verify: serialized — load timestamps should be staggered, not overlapping

# Concurrent mixed-modality (after embed/rerank loaded in §8)
( /root/lemonade-spike/bench-lemon.sh hermes-4-14b-q5_k_m 1 conc-chat &
  curl -sS -H "$KEY" -X POST http://127.0.0.1:9100/v1/embeddings \
    -H 'Content-Type: application/json' \
    -d '{"model":"bge-small-en","input":"hello world"}' &
  curl -sS -H "$KEY" -X POST http://127.0.0.1:9100/v1/reranking \
    -H 'Content-Type: application/json' \
    -d '{"model":"bge-reranker-v2-m3","query":"q","documents":["a","b"]}' &
  wait ) | tee /root/lemonade-spike/findings/conc-mixed.log
# verify: all 3 return non-error; record GTT deltas vs solo
```

Note any pending-load timeouts; Lemonade queues loads indefinitely so a stuck one blocks everything.

### Metrics shim viability check

```bash
KEY="Authorization: Bearer $LEMONADE_API_KEY"
# Discover backend_url per loaded model and probe its /metrics
curl -sS -H "$KEY" http://127.0.0.1:9100/v1/health \
  | jq -r '.loaded[] | "\(.model) \(.backend_url)"' > /root/lemonade-spike/findings/backend-urls.txt
while read M URL; do
  echo "=== $M ==="
  curl -sfI "$URL/metrics" 2>&1 | head -1
  curl -sf "$URL/metrics" 2>/dev/null | head -20
done < /root/lemonade-spike/findings/backend-urls.txt | tee /root/lemonade-spike/findings/backend-metrics.log
# verify: at least one llamacpp child returns text/plain Prometheus metrics (llamacpp:requests_processing etc.)
# If empty: metrics shim falls back to /v1/stats polling (Task #5 fallback path)
```

---

## 6. Idle-behavior probe (~60 min wallclock, ~2 min hands-on)

```bash
KEY="Authorization: Bearer $LEMONADE_API_KEY"
curl -sS -H "$KEY" -X POST http://127.0.0.1:9100/v1/load \
  -H 'Content-Type: application/json' -d '{"model":"qwen3.5-0.8b"}'
date +%s > /root/lemonade-spike/findings/idle-start.txt
sleep 3600
curl -sS -H "$KEY" http://127.0.0.1:9100/v1/health | jq '.loaded[] | select(.model=="qwen3.5-0.8b")'
# verify: model still resident; confirms no idle TTL (LRU-only eviction)
```

Run in parallel with §7/§8 if tight on time.

---

## 7. NPU smoke via FLM (~30 min)

```bash
/opt/lemonade-spike/lemonade backends install flm 2>&1 \
  | tee /root/lemonade-spike/findings/install-flm.log
# verify: log contains "flm installed" and libxrt-npu2 detected

# Pick smallest tag from FLM model list
jq -r '.models[] | select(.size_gb<1) | .name' /share/flm/model_list.json | head -3
FLM_MODEL=$(jq -r '.models[] | select(.size_gb<1) | .name' /share/flm/model_list.json | head -1)

KEY="Authorization: Bearer $LEMONADE_API_KEY"
curl -fsS -H "$KEY" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:9100/v1/pull -d "{\"model\":\"$FLM_MODEL\",\"backend\":\"flm\"}" \
  | tee /root/lemonade-spike/findings/flm-pull.json
curl -fsS -H "$KEY" -X POST http://127.0.0.1:9100/v1/load \
  -H 'Content-Type: application/json' -d "{\"model\":\"$FLM_MODEL\"}"
curl -fsS -H "$KEY" -X POST http://127.0.0.1:9100/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$FLM_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi in 5 words.\"}]}" \
  | tee /root/lemonade-spike/findings/flm-completion.json
curl -sS -H "$KEY" http://127.0.0.1:9100/v1/stats | jq . \
  > /root/lemonade-spike/findings/flm-stats.json
# verify: completion has non-empty choices[0].message.content; stats has tokens_per_second
```

If FLM install fails on amdxdna mismatch, capture full stderr and stop — that's a known sharp edge (see `hal0_flm_models_mount_path.md`).

---

## 8. Modality smoke tests (~20 min)

```bash
KEY="Authorization: Bearer $LEMONADE_API_KEY"
F=/root/lemonade-spike/findings

# Embeddings
curl -fsS -H "$KEY" -X POST http://127.0.0.1:9100/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-small-en","input":"hal0 spike"}' | tee $F/smoke-embed.json
# verify: data[0].embedding is a float array of length ~384

# Rerank
curl -fsS -H "$KEY" -X POST http://127.0.0.1:9100/v1/reranking \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-reranker-v2-m3","query":"home AI","documents":["cars","hal0 is a home AI platform"]}' \
  | tee $F/smoke-rerank.json
# verify: results[*].relevance_score, second doc ranked higher

# ASR
curl -fsS -H "$KEY" -F model=whisper-tiny \
  -F file=@/usr/share/sounds/alsa/Front_Center.wav \
  http://127.0.0.1:9100/v1/audio/transcriptions | tee $F/smoke-asr.json
# verify: text contains "front" or "center"

# TTS
curl -fsS -H "$KEY" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:9100/v1/audio/speech \
  -d '{"model":"kokoro","input":"hello","voice":"af"}' --output $F/smoke-tts.wav
file $F/smoke-tts.wav   # verify: RIFF WAVE audio

# Image
curl -fsS -H "$KEY" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:9100/v1/images/generations \
  -d '{"model":"sd-tiny","prompt":"a lemon","size":"256x256","n":1}' | tee $F/smoke-img.json
# verify: data[0].b64_json or url present
```

Each failure is interesting, not fatal — note it. Skip cleanly if model isn't in registry.

---

## 9. Cleanup & restore (~5 min)

```bash
tmux send-keys -t lemond C-c && sleep 2 && tmux kill-session -t lemond
# verify: pgrep lemond -> empty
while read u; do systemctl start "$u"; done < /root/lemonade-spike/active-slots.txt
sleep 10
curl -fsS http://127.0.0.1:8080/api/slots | jq 'length'
# verify: "ok" + all original slots back to active

cp /root/lemonade-spike/findings/lemond.log /root/lemonade-spike/findings/lemond.log.final
tar -czf /root/lemonade-spike/findings-$(date +%Y%m%d).tar.gz -C /root/lemonade-spike findings
# verify: tarball exists and is non-empty
```

Leave `/opt/lemonade-spike` in place for a possible second pass; nuke when done with `rm -rf /opt/lemonade-spike /opt/lemonade.tgz`.

---

## 10. Findings template

Save as `/root/lemonade-spike/findings/SUMMARY.md`:

```markdown
# Lemonade Spike — Findings ($(date +%F))

## Topology recommendation
[ ] Replace toolboxes wholesale
[ ] Adopt for LLM only, keep toolboxes for ASR/TTS/img
[ ] Keep current; revisit at Lemonade vX
Why:

## Perf table (vs 2026-05-22 baseline)
| Model | Baseline tok/s | Lemonade warm tok/s | TTFT (ms) | Cold load (s) | Delta |
|---|---|---|---|---|---|
| hermes-4-14b-q5_k_m | 20 |  |  |  |  |
| qwen3.5-0.8b |  |  |  |  |  |
| qwen3.6-35b-a3b-q4_k_xl |  |  |  |  |  |
| qwen3-coder-next-reap-40b-a3b-q4_k_xl |  |  |  |  |  |

## NPU (FLM)
Install: [ok / failed — reason]
Model:               TTFT:        tok/s:

## Modality smokes
embed [ ] rerank [ ] asr [ ] tts [ ] img [ ]

## Surprises / sharp edges
-

## Yellow flags surfaced (qualitative — not auto-kills)
[ ] >15% regression on hermes-4-14b
[ ] TTFT >2× baseline
[ ] GTT footprint >+25%
[ ] FLM install fails on current kernel
[ ] Concurrent load deadlock
[ ] Missing modality endpoint
[ ] Metrics shim path broken (backend_url /metrics unreachable)

## Verdict (qualitative judgment over the data above)

```

---

**Total est:** §1 10m + §2 15m + §3 15m + §4 75m + §5 20m + §6 60m idle (overlap) + §7 30m + §8 20m + §9 5m ≈ **3h 10m hands-on**, allow 4–5 h with surprises.
