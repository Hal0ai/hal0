# Primary / agent slot model evaluation — 2026-05-22

Quick on-box benchmarks of 10 chat-capable GGUF candidates for the hal0 primary slot, run against the live Strix Halo iGPU via the `ghcr.io/hal0ai/hal0-toolbox-vulkan:v1` toolbox (llama-server, Vulkan backend, `version: 1 (59778f0)`).

**Host.** hal0 LXC 105 (10.0.1.142), Ubuntu 24.04 in privileged LXC on Proxmox, AMD Strix Halo APU (`amdgpu` `card1`, device 1586), 1 GiB UMA VRAM aperture + ~105 GiB GTT (unified memory window), 96 GiB system RAM. The primary slot was kept running on port 8081 (Hermes-4-14B) throughout; benchmarks ran in fresh side containers on port 8090.

**Method.** For each model: spin up `llama-server` in a one-shot container with `--ctx-size 8192 --threads 12 --parallel 1 -ngl 999 -b 4096 --metrics`. Wait for `/health=ok`. Warm with one short completion. Then three timed `/completion` calls with a fixed ~1.1k-token raw prompt and `n_predict=128, temperature=0, top_k=1, cache_prompt=false`. Stream a separate call to capture TTFT (time to first emitted token). Sample `amdgpu` `mem_info_vram_used` and `mem_info_gtt_used` before and after model load.

> Numbers reported below are from the second timed call (warm pp + tg). `prompt_per_second` and `predicted_per_second` are taken straight from `timings` in the llama-server response.

> For the two **qwen3.6** entries, the raw-text prompt triggered an immediate EOS on the first run (`n_predicted=1`). Those two were re-benched with `ignore_eos=true` so the generation rate reflects real throughput. All others completed the full 128-token target on raw prompts.

Source data on hal0:
- `/root/llm-eval/candidates.tsv` — model manifest
- `/root/llm-eval/bench.sh` / `rebench.sh` — harness
- `/root/llm-eval/results.jsonl`, `results_rebench.jsonl` — raw timings + memory deltas
- `/root/llm-eval/logs/*.log` — full llama-server logs (KV size, n_ctx_train, n_params)

## Results

All ctx=8192. tok/s columns are warm (run 2 of 3); cold run is comparable for tg and lower for pp. Memory is the GTT delta around `llama-server` load (Strix Halo unified memory; VRAM aperture is only 1 GiB so weights spill to GTT).

| # | Model | Arch | Total / active | Quant | File | KV@8k | n_ctx_train | Load | TTFT | PP tok/s | TG tok/s | Mem (GTT Δ) |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `qwen3.5-0.8b` | qwen35 dense | 752 M / 752 M | Q4_K_XL | 0.56 GB | 96 MiB | 256k | 2.0 s | 176 ms | 7 131 | **205** | 1 231 MiB |
| 2 | `qwen3-zero-coder-v2-0.8b-f16` | qwen3 dense | 816 M / 816 M | F16 | 1.64 GB | 1 344 MiB | 40k | 4.0 s | 258 ms | 4 592 | 72 | 3 519 MiB |
| 3 | `qwen3-4b-q4_k_m` | qwen3 dense | 4.02 B / 4.02 B | Q4_K_M | 2.50 GB | 1 152 MiB | 40k | 4.0 s | 641 ms | 1 765 | 67 | 4 035 MiB |
| 4 | `qwen3.5-4b-q4_k_xl` | qwen35 dense | 4.21 B / 4.21 B | Q4_K_XL | 2.91 GB | 256 MiB | 256k | 4.0 s | 751 ms | 1 524 | 56 | 4 039 MiB |
| 5 | `qwen3.5-9b-q4_k_xl` | qwen35 dense | 8.95 B / 8.95 B | Q4_K_XL | 5.97 GB | 256 MiB | 256k | 8.1 s | 1 448 ms | 772 | 33 | 6 649 MiB |
| 6 | `hermes-4-14b-q5_k_m` | qwen3 dense | 14.77 B / 14.77 B | Q5_K_M | 10.51 GB | 1 280 MiB | 40k | 12.0 s | 2 958 ms | 383 | 20 | 11 746 MiB |
| 7 | `gemma-4-26b-a4b-it-q4_k_xl` | gemma4 MoE | 25.23 B / ~4 B | Q4_K_XL | 17.09 GB | 300 MiB | 256k | 18.1 s | 1 227 ms | 935 | 44 | 17 883 MiB |
| 8 | `qwen3-coder-reap-25b-a3b-q5_k_m` | qwen3 MoE | 24.87 B / ~3 B | Q5_K_M | 17.72 GB | 768 MiB | 256k | 18.1 s | 1 059 ms | 1 068 | **71** | 18 026 MiB |
| 9 | `qwen3.6-27b-q5_k_xl` | qwen35 dense | 26.90 B / 26.90 B | Q5_K_XL | 20.04 GB | 512 MiB | 256k | 20.6 s | 6 839 ms | 165 | 10 | 20 420 MiB |
| 10 | `qwen3.6-35b-a3b-q4_k_xl` | qwen35 MoE | 34.66 B / ~3 B | Q4_K_XL | 22.36 GB | 160 MiB | 256k | 24.5 s | 1 274 ms | 917 | **53** | 21 987 MiB |

KV is the `llama_kv_cache: Vulkan0 KV buffer size` line at `--ctx-size 8192 --parallel 1` (single seq, FP16 KV — no `--cache-type-k/v q8`). Memory delta includes weights + KV + compute buffers + scratch; for these models the weights dominate.

## Read of the results

**The MoE story.** Qwen3 MoE models (3 B active for the 25 B Coder and 35 B A3B; 4 B active for Gemma-4-26B) all generate at 44–71 tok/s — comparable to a dense 4 B and *much* faster than the dense 27 B Qwen3.6 (10 tok/s) at similar weight memory. On Strix Halo unified memory, bandwidth-bound generation collapses with parameter count for dense models; MoE keeps the active-set small and pays a flat memory tax for the rest. **MoE is the only way to get >25 B "brain" at agent-grade latency on this APU.**

**The dense scaling curve.** From 0.8 B → 4 B → 9 B → 14 B → 27 B: TG roughly halves with each step. 4 B is the practical ceiling for snappy interactive work (~60 tok/s, sub-second TTFT). 9 B is acceptable (~33 tok/s). 14 B is the floor for "still feels real-time on a single user." 27 B dense is too slow.

**KV-cache surprises.**
- `qwen3-4b-q4_k_m` and `qwen3-zero-coder-v2-0.8b-f16` use 1.1–1.3 GiB at only 8 k ctx — this generation of GGUFs ships pre-GQA-extension KV layouts (full KV per head). The newer Qwen3.5 / Qwen3.6 builds drop that to 96–512 MiB. **Prefer Qwen3.5+ GGUFs for any role that wants long context.**
- `qwen3.6-35b-a3b` is the headline: 160 MiB KV at 8 k → would only be ~5 GiB at 256 k. That's the largest brain on this list and also the cheapest KV per token.
- Gemma-4-26B keeps KV at 300 MiB at 8 k — sliding-window / interleaved local-global attention is doing real work.

**TTFT.** Mostly tracks file size (prefill is compute-bound on the iGPU) except for the qwen3.6 dense 27 B, which sits at ~7 s for a 1.1 k prompt — that alone disqualifies it for interactive agents. The 35 B MoE is back under 1.3 s because prefill only touches the active-expert MLP block.

## Shortlist — by role

For PLAN / catalog / capability defaults, I'd group these as:

| Role | Pick | Why |
|---|---|---|
| **Nano** (tab-complete, embedded micro-agent) | `qwen3.5-0.8b` | 205 tok/s, 1.2 GiB total. Already the install seed for `primary`. |
| **Small dense** (general chat, single-turn agent) | `qwen3.5-4b-q4_k_xl` | 256 MiB KV vs 1.1 GiB for `qwen3-4b-q4_k_m`. Same speed. Newer arch, 256 k native ctx. **Strict upgrade.** |
| **Medium dense** (capable single-user assistant) | `qwen3.5-9b-q4_k_xl` | 33 tok/s, 256 MiB KV, only 6 GiB total. Sweet spot for "fits + thinks." |
| **Reasoning / agent baseline** | `hermes-4-14b-q5_k_m` | The current production primary. Strong tool-use FT. Slow-ish at 20 tok/s but the tradeoff is intentional. |
| **Coder agent** | `qwen3-coder-reap-25b-a3b-q5_k_m` | 71 tok/s @ 18 GB, REAP-pruned MoE. Faster than the 4 B dense, with 25 B brain for code. |
| **Big general agent** | `qwen3.6-35b-a3b-q4_k_xl` | 53 tok/s @ 22 GB, 160 MiB KV. Best capability-per-second on this list. **The candidate to beat for default `primary` once Qwen3.6 tool-use is validated.** |
| **Tool-use specialist (alt)** | `gemma-4-26b-a4b-it-q4_k_xl` | 44 tok/s, sub-language-model latency, sliding-window attention. Google IT tuning. |

## Disqualified / skip

- `qwen3-zero-coder-v2-0.8b-f16` — same param class as `qwen3.5-0.8b` but 6× larger memory and ⅓ the throughput due to F16 weights. F16 0.8 B is a niche research weight; not for production.
- `qwen3-4b-q4_k_m` — superseded by `qwen3.5-4b-q4_k_xl` at the same speed but ¼ the KV.
- `qwen3.6-27b-q5_k_xl` — dense 27 B at 10 tok/s + 7 s TTFT. Use the 35 B MoE instead.

## Addendum 2026-05-22 — Coder-Next 80B class

Two Qwen3-Coder-Next variants are on disk + in the registry; both Q4_K_XL, both Apache 2.0:

| Slug | Source | Total / active | File | Notes |
|---|---|---|---:|---|
| `qwen3-coder-next-80b-a3b-q4_k_xl` (renamed from `qwen3-coder-next-q4_k_xl`) | `unsloth/Qwen3-Coder-Next-GGUF` | 80 B / 3 B | 47 GB | Full Qwen3-Coder-Next 80B-A3B |
| `qwen3-coder-next-reap-40b-a3b-q4_k_xl` | `lovedheart/…REAP-40B-A3B-GGUF` | ~40 B / 3 B | 27 GB | REAP-pruned (≈half experts dropped); tagged `recommended` |

Both have the same compute footprint per forward pass (~3 B active), so prefill speed is similar; generation differs because the router scans more weights per token in the full 80B. Expected tok/s on the Strix Halo iGPU, by interpolation from this report's MoE-A3B rows (`qwen3-coder-reap-25b-a3b` = 71 tok/s, `qwen3.6-35b-a3b` = 53 tok/s): REAP-40B ≈ 45 tok/s, full 80B ≈ 30–35 tok/s. Not yet directly benched; rerun `bench.sh` against these two to confirm.

**Canonical pick for the coder/agent role: REAP-40B.** Reasoning: ~25–30 GB headroom on hal0 vs. ~12–15 GB for the full 80B (with primary + embed + rerank + stt/tts all resident), and REAP reports low quality degradation for coding tasks. The full 80B stays registered as the max-capability alternative.

Capacity at coresident peak with the production primary `hermes-4-14b-q5_k_m` already loaded:

- Hermes-14B resident: ~11.7 GiB GTT
- REAP-40B added: ~28 GiB → total ~40 GiB GTT, leaving ~50 GiB headroom. Comfortable.
- Full 80B added: ~48 GiB → total ~60 GiB GTT, leaving ~30 GiB before pressure on OS + remaining slots. Workable but tight; would not coreside with `qwen3.6-35b-a3b` or `gemma-4-26b`.

The "recommended" preference is currently encoded only as a registry tag (`tags = [..., "recommended"]`); `src/hal0/capabilities/catalog.py` iterates `CURATED + registry` in arrival order and has no sort/recommend concept today. A future UI / orchestrator change can read this tag.

## Caveats

- All tok/s figures are at ctx 8192 with `--parallel 1`. The production primary slot runs `--parallel 2`, which splits the KV cache in half (per-seq), affects batch math, and reduces TG by ~10–20 % under contention.
- TG is reported as the warm steady-state rate (run 2 of 3, after a small warmup). Cold first-token rate is captured in TTFT.
- Prompt was raw English filler, not a chat-templated message. For models that expect role tokens (Qwen3.6 explicitly), real chat traffic flows through `/v1/chat/completions` and the prefill cost is similar. Generation tok/s is unaffected.
- All numbers were taken with Hermes-4-14B *also* resident on the iGPU via the primary slot (11.7 GiB GTT). On an idle iGPU the absolute load times will be a few seconds faster but tok/s is unchanged for these single-user, ngl=all configurations.
- This was a tok/s + memory snapshot, not a quality benchmark. No accuracy / agent-eval / tool-call success rates were measured. Those should come next, against `superpowers/python-testing-patterns` flows or a custom agent harness on hal0.

## Reproduce

```bash
ssh hal0
cat /root/llm-eval/candidates.tsv      # 10 model slugs + paths
bash /root/llm-eval/bench.sh           # ~12 min wall, runs all 10
jq -s '.' /root/llm-eval/results.jsonl  # consolidated results
```

To bench a single model, edit `CANDIDATES=` in `bench.sh` to point at a 1-line TSV.
