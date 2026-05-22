# ADR 0006 — Migrate inference to Lemonade Server (v0.2)

- **Status:** Draft
- **Date:** 2026-05-22
- **Drivers:** /grill-me + /grill-with-docs sessions 2026-05-22; spike findings (`docs/internal/lemonade-spike-findings-2026-05-22.md`); plan doc (`docs/internal/lemonade-migration-plan.md`)
- **Supersedes:** Per-modality toolbox architecture from PLAN.md §2 (six Dockerfiles + Provider classes)

## Context

- hal0 v0.1.x ships six per-modality toolbox containers (vulkan, rocm, flm, moonshine, kokoro, comfyui), each wrapped by a `Provider` subclass. Maintenance burden, version-skew tracking, and per-modality publish pipelines consume disproportionate time.
- AMD ships **Lemonade Server** (Apache 2.0, github.com/lemonade-sdk/lemonade) — an officially-supported, embeddable inference server that bundles llama.cpp (ROCm + Vulkan + CPU), FLM (NPU), whisper.cpp, sd-cpp, kokoro under one process with OpenAI/Ollama/Anthropic-compatible APIs.
- Spike 2026-05-22 validated Lemonade on hal0 LXC 105 (Strix Halo gfx1151). Results: works; perf parity-to-regression depending on model (hermes-14b at parity, qwen3.5-0.8b -18%, qwen3.6-35b -13% on ROCm); Vulkan-in-Lemonade is 3x slower than hal0's Vulkan baseline.

## Options considered

| Option | Reason accepted / rejected |
|---|---|
| **Keep status quo** — maintain six toolboxes | Rejected — maintenance burden is a recurring tax; upstream AMD has a better-resourced solution |
| **Lemonade as one provider among many** | Rejected — leaves maintenance burden untouched; "Lemonade option" is not the goal |
| **Partial replacement** (Lemonade for LLM only; keep toolboxes for embed/rerank/ASR/TTS/img) | Rejected pre-spike; spike data argues this is now a fallback option if v0.2 PRs hit hard regressions |
| **Total replacement** with Lemonade for all modalities | ACCEPTED — captures full maintenance gain; supported by Lemonade's actual modality coverage on Linux |

## Decision

### 1. Total provider replacement
Lemonade is the sole inference backend in v0.2. The six per-modality Provider classes retire. A single `LemonadeProvider` implementing the existing `Provider` ABC drives load/unload/health via Lemonade's HTTP API.

### 2. Slot abstraction preserved at user-facing layer
Each hal0 slot (`primary`, `embed`, `embed-rerank`, `stt`, `tts`, `img`) remains a named, configured serving target with a chosen model + device. Runtime layer changes: slot = 1 Lemonade-loaded model rather than 1 systemd template instance + 1 container. `SlotManager.start(slot)` calls Lemonade load semantics; slot state derives from `/v1/health.loaded[]` by model_name. `hal0-slot@.service` template retires.

### 3. Drive method: HTTP-first
hal0 talks to Lemonade via `/v1/load`, `/v1/unload`, `/v1/health`, `/v1/pull`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/reranking`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/images/generations`. `LemonadeClient` (`src/hal0/lemonade/client.py`) wraps these. CLI subprocess (`lemonade load X`) is a bootstrap fallback in early PRs until `/v1/load` schema is reverse-engineered (research handoff pending).

### 4. Model registration via hal0-customized `server_models.json`
At install time hal0 generates `server_models.json` from `/var/lib/hal0/registry/registry.toml` and writes it into Lemonade's resources directory. Curated catalog with explicit type metadata per entry (llm/embedding/reranking/transcription/image/tts). Runtime user adds go via `POST /v1/pull` with `user.*` namespace + type. Spike confirmed Lemonade's bundled `server_models.json` does not include hal0's curated picks (e.g. `hermes-4-14b`, `qwen3-coder-next-reap-40b-a3b`).

### 5. Process supervision: containerized lemond in systemd
`/etc/systemd/system/lemond.service` runs `podman/docker run --device=/dev/dri --device=/dev/accel/accel0` with `Restart=on-failure RestartSec=5s`. Boot-enabled (always-running). hal0-api `Wants=lemond.service` (soft dep). This combines systemd's standard supervision (Restart, journal, watchdog) with container isolation hal0 already uses.

### 6. Container source: hal0-published wrapper image
`ghcr.io/hal0ai/hal0-lemond:vX.Y.Z`. Base + lemond tarball + `apt install unzip libxrt-npu2` (system deps Lemonade requires) + pre-pulled backends (`llamacpp:rocm`, `flm:npu`, `whispercpp:cpu`, `sdcpp:rocm`, `kokoro:cpu`). CI build + cosign sign. Replaces six per-modality toolbox images with one multi-backend image. (AMD does not currently publish an official docker image suitable for hal0's needs — research handoff confirms.)

### 7. `SlotConfig.backend` → `SlotConfig.device`
Schema refactor. Old `backend` field mixed providers and backends (`vulkan|rocm|flm|moonshine|kokoro|cpu`). New `device` field is hardware-preference only: `gpu-rocm | gpu-vulkan | cpu | npu`. Default `gpu-rocm`. `LemonadeProvider` maps `device` to Lemonade's `recipe:backend` pair internally. `capabilities.toml` schema_version bumps to 2; auto-migration preserves user choices.

### 8. UI rework deferred to v0.2.1
v0.2 ships minimal UI patch (retarget API client to new endpoints; preserve all Vue components/views). v0.2.1 ships UI rework informed by `docs/dev/web-ui.md` research (pending handoff at `/tmp/hal0-lemonade-research-handoff.md`). Vue/Pinia/Tailwind stack stays; brand wordmark and Playwright suite preserved.

### 9. Metrics shim via `/v1/stats` polling
Spike confirmed Lemonade's bundled llama-server returns 501 on `/metrics`. Backend_url scrape strategy (PR #124 path) does not survive the migration. hal0 builds a metrics aggregator (`src/hal0/lemonade/metrics.py`) that polls `/v1/stats` (last-request perf) and `/v1/health` (model state) per slot, exposes Prometheus surface for the dashboard.

### 10. Version pinning + bundling
`manifest.json` schema v2 adds `lemonade: { image, digest, version }`. Updates gated behind explicit hal0 release. install.sh pulls + cosign-verifies. Lemonade ships breaking changes weekly; pinning is non-negotiable.

### 11. Rollback
Downgrade-only via existing update mechanism (`hal0 update --version v0.1.x`). v0.2 deletes old Provider code cleanly — no long-term feature-flag plumbing. Schema_version=2 → 1 downgrade preserves a `.v1.bak` of capabilities.toml on upgrade.

### 12. PR sequence: flag-gated incremental + atomic cutover
Each PR adds capability behind `HAL0_BACKEND=lemonade` env var while v0.1.x code paths stay default. Sequence at `lemonade-migration-plan.md` §PR sequence. Atomic cutover PR flips default + deletes old Provider classes. v0.1.x patch releases stay shippable from main until cutover.

## Consequences

**Wins:**
- Six toolbox Dockerfiles + publish pipelines retire → one hal0-lemond image
- Multi-modal coverage in one process (LLM + embed + rerank + ASR + TTS + img + NPU)
- Newer llama.cpp build via AMD's release train (rather than hal0's pinned b9279)
- AMD legitimacy for v1.0 positioning
- Standard systemd supervision replaces fragile per-slot template + adoption logic

**Losses / regressions:**
- Performance: parity-to-regression vs hal0-Vulkan baseline (-13% to -18% on tested models; hermes-14b at parity)
- ComfyUI workflows lost — sdpp covers 90% case; power-users directed to external ComfyUI
- GPU-accelerated Kokoro TTS (Lemonade's kokoro is CPU-only on Linux); pending spike validation
- Moonshine ASR (lighter on weak CPUs) → whispercpp (more accurate, heavier)
- New install.sh deps: `unzip`, `libxrt-npu2`

**Operational risks (mitigated separately):**
- Nuclear evict-all on load failure → see ADR-0007
- Serialized load queue can deadlock under stuck load → hal0-side `/v1/load` timeout
- Weekly Lemonade breaking releases → pin discipline (decision §10)

## Open questions deferred to research handoff

- `/v1/load` actual request schema (CLI works; direct curl rejected)
- Lemonade WS protocol shape (`/logs/stream` + others) for v0.2.1 UI
- Omni recipe pattern — may inform `capabilities.toml` design
- Reserved-args extension hooks (server_models.json field set)

Research handoff: `/tmp/hal0-lemonade-research-handoff.md`. Findings land in `docs/internal/lemonade-repo-deep-dive-2026-05-22.md`.

## Related

- ADR-0007 — Nuclear-evict-all mitigation (tactical)
- ADR-0001 — Auth boundary (hal0 Bearer token survives unchanged; Lemonade has its own `LEMONADE_API_KEY` for internal hal0→lemond traffic only)
- ADR-0002, ADR-0003 — Capability overlay + slot-capabilities field (preserved; only the provider behind a capability changes)
