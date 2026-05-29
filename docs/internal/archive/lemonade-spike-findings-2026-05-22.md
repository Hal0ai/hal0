# Lemonade Spike Findings — hal0 LXC 105 — 2026-05-22

## Verdict (qualitative)

Lemonade works on Strix Halo. Native gfx1151 ROCm binary present. But **perf and operational story is weaker than expected**, AND **modality coverage off-the-shelf is rougher than docs suggested**. Migration is feasible but value/cost ratio shifted. Worth re-grilling.

## Perf table (vs 2026-05-22 baseline)

| Model | Baseline (hal0 Vulkan) tok/s | Lemonade ROCm tok/s | Lemonade Vulkan tok/s | Delta vs baseline |
|---|---|---|---|---|
| qwen3.5-0.8b nano | 205 | 168 (warm) | 47-69 (variable) | ROCm -18% |
| qwen3.6-35b-a3b MoE | 53 | 46 (warm) | not tested | ROCm -13% |
| hermes-4-14b-q5_k_m (current primary) | 20 | 20.4 (warm) | not tested | **parity (+2%)** |
| qwen3-coder-next-reap-40b | not benched | not benched | not benched | — |

TTFT warm @ ROCm: 17-92ms across models. Load times 21s for qwen3.6-35b. GTT matches baseline (21.45 GiB for qwen3.6-35b, 11.09 GiB for hermes-14b).

**Vulkan-in-Lemonade is much slower than hal0 Vulkan baseline.** Cause unknown — different llama.cpp version (b9253 vs b1274), `--no-mmap` default, different compile flags. ROCm path is the only viable backend.

## Positive findings
- Lemonade v10.6.0 installs and runs on LXC 105
- gfx1151 native ROCm nightly build (b1274) installs cleanly (435 MB)
- NPU hardware detected by Lemonade auto-discovery
- Apache 2.0, OpenAI-compatible API, /v1/health includes backend_url per loaded model
- hermes-4-14b PARITY on ROCm — current primary user-experience preserved
- Strix Halo (gfx1151) first-class in nightly channel

## Negative findings / risks

### Operational
- **/metrics returns 501** on bundled llama-server (b9253). hal0 metric scrape strategy via backend_url is BROKEN. Must use /v1/stats fallback (Task #5 fallback path wins).
- **Nuclear evict-all CONFIRMED LIVE** — observed twice in this spike. Server log says verbatim: `"Load failed with non-file-not-found error, evicting all models and retrying..."`. Task #10 hazard is real.
- **Default backend pick is Vulkan**, which is 3x slower than ROCm in Lemonade. Migration config must force `--llamacpp rocm` per-model.
- **/v1/load API field shape undocumented** — direct curl returns `"type must be string, but is null"` with the documented body. CLI works. hal0 will need to reverse-engineer the CLI request shape, OR drive via shell CLI from Python.

### Install/deps
- **`unzip` is an undocumented system dep.** First backend install attempt failed silently. Required prereq for install.sh.
- **FLM install silently fails.** `lemonade backends install flm:npu` returns "Backend installation failed (no details from server)". Server log shows "Installing backend: flm:npu" then silence. libxrt-npu2 NOT installed on LXC; that is the likely prereq but Lemonade does not handle it. Memory `hal0_flm_models_mount_path.md` referenced `/opt/hal0/flm-ubuntu` which does NOT exist on this LXC — that memory was stale or describing the container-internal path.

### Model discovery + typing
- **`extra_models_dir` discovers GGUFs but matches them against server_models.json by HF repo coords.** hal0 GGUFs in /mnt/ai-models/local/ that are NOT in Lemonade's registry are discovered as type=LLM only with label "custom". Rerank/embed models discovered this way lack `--reranking`/`--embedding` flag in child llama-server → 501.
- **Reserved args** — `--reranking`, `--embedding`, `--ctx-size`, `--device`, `--gpu-layers`, `--mmproj*`, `-ngl`, `-m`, etc. are managed by Lemonade and CANNOT be user-overridden. Type classification at registration is mandatory.
- **HF cache resolution buggy** — for at least one model (pqnet/bge-reranker-v2-m3-Q8_0-GGUF) Lemonade pointed llama-server at the snapshot DIR not the .gguf file → llama_model_load failure → nuclear evict.

### Modality smokes
| Modality | Status |
|---|---|
| LLM chat (qwen3.5-0.8b, hermes-4-14b, qwen3.6-35b) | works on ROCm |
| Embedding (nomic-embed-text-v1-GGUF) | FAILED to load (HF resolution / model file issue) |
| Reranking (bge-reranker-v2-m3) | FAILED: registered server_models entry has broken HF cache; custom-discovered file loads as LLM without --reranking |
| ASR (whispercpp) | not tested |
| TTS (kokoro) | not tested |
| Image (sd-cpp) | not tested |
| NPU LLM (FLM) | install silently failed |

## Reserved-args list (verbatim from server log)

`--ctx-size, --device, --embedding, --embeddings, --gpu-layers, --jinja, --mmproj, --mmproj-auto, --mmproj-offload, --mmproj-url, --model, --n-gpu-layers, --no-jinja, --no-mmproj, --no-mmproj-auto, --no-mmproj-offload, --port, --rerank, --reranking, -c, -dev, -m, -mm, -mmu, -ngl`

## Backends matrix (Linux/Strix Halo) — from `lemonade backends`

| Recipe | Backend | State |
|---|---|---|
| llamacpp | rocm | installable + INSTALLED |
| llamacpp | vulkan | installable + auto-installed-as-sibling |
| llamacpp | cpu | installable |
| flm | npu | installable but install SILENTLY FAILS |
| ryzenai-llm | npu | **unsupported: Requires Windows** |
| whispercpp | cpu/vulkan | installable |
| whispercpp | npu | **unsupported: Requires Windows** |
| sd-cpp | cpu/rocm | installable |
| vllm | rocm | installable (out-of-scope per plan) |
| kokoro | cpu | installable (**no GPU variant** — confirms GPU-Kokoro loss) |

## What changed in understanding

Pre-spike: "Lemonade replaces six toolboxes with one binary, perf upside likely from newer llama.cpp + ROCm 7.13."

Post-spike: "Lemonade replaces SOME of the six toolboxes (LLM + img path), but each non-LLM modality (embed/rerank/ASR/TTS) requires explicit type-aware registration. NPU path is broken-to-install. Perf is parity-to-regression depending on model, not upside. The newer llama.cpp build (b9253) is actually SLOWER on Vulkan than hal0's b1274. The unified-binary value remains; the perf value evaporates."

## Recommendation

Re-grill the user on migration drivers given the perf data. Specifically question whether "performance" stays a driver. Topology decision: with weakened perf assumption, the spike data argues for a less ambitious v0.2 scope (LLM-only via Lemonade, keep toolboxes for embed/rerank/ASR/TTS/NPU). Total replacement is still doable but requires significantly more glue work than originally costed (per-modality type registration, FLM XRT prereq install, /v1/stats metrics shim, --llamacpp rocm override everywhere).
