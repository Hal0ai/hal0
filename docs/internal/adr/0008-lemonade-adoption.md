# ADR 0008 — Lemonade adoption as the unified inference runtime (v0.2)

- **Status:** Accepted
- **Date proposed:** 2026-05-22
- **Date accepted:** 2026-05-22
- **Supersedes:** ADR-0006 (migrate-inference-to-lemonade), ADR-0007 (nuclear-evict-all-mitigation)
- **Implementing PRs:** PR-2 (`#137`, foundation — already shipped); PR-3 through PR-22 per `docs/internal/lemonade-adoption-plan-2026-05-22.md` §11
- **Backed by:** `docs/internal/lemonade-spike-2-findings-2026-05-22.md`; `docs/internal/lemonade-adoption-plan-2026-05-22.md` (post-grill source of truth)

## Context

ADR-0006 declared a total-replacement migration to AMD's Lemonade Server based on the first 2026-05-22 spike + initial grill session. Two follow-on activities invalidated significant chunks of that decision record:

1. **4-agent re-grill (architect / researcher / API / UI lanes)** surfaced six candidate paths instead of two. The "total replacement" frame in ADR-0006 had been treated as a binary; the re-grill demonstrated it was actually one of several viable topologies, with materially different risk profiles per modality.
2. **Spike #2 (Phase A / B / C)** ran fresh empirical tests against Lemonade v10.6.0 on hal0 LXC 105 and produced corrections to four foundational ADR-0006 assumptions:
   - **Embedding + reranking modalities are NOT broken.** The "5/7 modalities broken" framing in spike #1 was a registration bug, not a Lemonade limitation. Pulling with `labels: ["embeddings"]` (or `["reranking"]`) types the model correctly; embed serves 768-dim vectors at parity-with-toolbox speed.
   - **Nuclear evict-all does NOT fire on every load failure.** The router exempts errors whose message substring-matches "not found" / "does not exist" / "No such file" — i.e. the common failure modes (bad path, missing variant, mistyped name) return graceful errors and leave the loaded pool intact. Nuclear evict is the escape valve for the genuinely unexpected.
   - **Per-type LRU is real and is the default eviction policy.** `/v1/health.max_models` reports six independent type budgets (`llm`, `embedding`, `reranking`, `transcription`, `tts`, `image`) — co-residency is governed per-type, not by a single global pool.
   - **Concurrent multi-LLM on the iGPU IS viable.** Spike #1's concurrency-deadlock observation was caused by `llama-server` defaulting to all logical CPU cores when `--threads` is unset; two child processes contending for the same cores stalled the Vulkan dispatch threads and froze the GPU command queue. A bounded `--threads N` setting (where `N = (cores − 2) / 4`, min 2) resolves the deadlock fully.

Spike #2 also resolved the long-standing FLM/NPU install question by documenting the manual `.deb` install procedure (Lemonade's `flm:npu` auto-installer is Windows-only on upstream) and validated the **FLM trio** pattern: a single `flm serve` process can host chat + transcription + embedding concurrently via `--asr 1 --embed 1`, packing three NPU slots into the one AMDXDNA hardware context.

With ADR-0006 / ADR-0007 carrying inverted assumptions about half the runtime contract, a rewrite was needed. ADR-0008 is that rewrite — and it inherits a much more confident posture: every choice it locks has been pressure-tested in either spike #2 or the post-spike grill session that produced the adoption plan.

The full implementation contract lives in `docs/internal/lemonade-adoption-plan-2026-05-22.md`. This ADR records the architectural decisions; it does not duplicate the plan's PR sequence, config baselines, or service-topology diagrams.

## Decision

**Adopt Lemonade Server as the unified inference runtime for hal0 v0.2 (Path 4 of the six-path re-grill option matrix).** Specifically:

### 1. Single `lemond` per host, loopback-only

One `lemond` process per host, bound to `127.0.0.1:13305`, supervised by a new `hal0-lemonade.service` systemd unit. Cache directory at `/var/lib/hal0/lemonade/`. The hal0-api service on 8081 remains the only user-facing inference surface; `lemond` is treated as an internal runtime, never exposed off-box. Multi-process topologies (briefly considered after Phase A) are rejected — per-type LRU within one process gives sufficient isolation without the WS-port-coordination cost.

### 2. All modalities served via Lemonade

iGPU and CPU work flows through `llamacpp` (chat + embed + rerank), `sd-cpp` (image), `whisper.cpp` (transcription), and `kokoro:cpu` (speech). NPU work flows through `flm:npu` via the manually-installed FastFlowLM `.deb`. The per-modality hal0 toolbox containers (vulkan, rocm, flm, moonshine, kokoro, comfyui) retire. The `hal0-slot@.service` systemd template retires. `LemonadeProvider` is the only `Provider` in v0.2.

### 3. Per-type LRU concurrency contract

Co-residency is governed by Lemonade's per-type LRU policy (default budget set to 4 globally in `config.json`). Nuclear evict-all is acknowledged as the escape valve but is NOT the default — common load failures return typed errors, the loaded pool survives. The pre-validation guardrails ADR-0007 specified (sha256 + GGUF magic-byte check on every load) are no longer required and DO NOT ship in v0.2; the operational hazard ADR-0007 was mitigating turned out to be an order-of-magnitude smaller than ADR-0006's framing suggested.

### 4. Mandatory `llamacpp.args = "--parallel 1 --threads N"`

The lemond config baseline (`/var/lib/hal0/lemonade/config.json`) MUST set `llamacpp.args` with both flags. `N = (cores − 2) / 4`, min 2; computed at install time. Without this, two concurrent child llama-servers oversubscribe the CPU and freeze the Vulkan dispatch, producing the multi-model deadlock spike #2 reproduced four times before isolating the cause. This is a hard install-time requirement, not a tunable; the installer writes it and the dashboard's Lemonade admin panel guards against overriding it to an unbounded value.

### 5. NPU multi-role via the FLM trio

Lemonade's `flm.args = "--asr 1 --embed 1"` packs three model roles (chat, transcription, embedding) into the single AMDXDNA hardware context. hal0 exposes this as three slots (`agent`, `stt-npu`, `embed-npu`) that all back the same FLM child process; hal0's capability dispatcher reads `/v1/health.loaded[].backend_url` for the FLM model and routes `stt-npu` / `embed-npu` requests directly to that child's port. Lemonade only knows about the chat role; hal0 owns the fan-out. NPU exclusivity (one `device = "npu", type = "llm"` slot enabled at a time) is enforced in `capabilities.toml` validation.

### 6. Slot abstraction preserved; runtime layer simplified

Slots remain the user-facing unit (named, configured serving targets with a `type`, `device`, `model`, `enabled`, and optional `default`). The seeded catalog expands to six slots (`primary`, `embed`, `rerank`, `stt`, `tts`, `img`) plus three NPU slots gated on FLM `.deb` presence. User-added named slots ship via `hal0 slot add NAME --type TYPE --model MODEL`. The lifecycle state machine in `src/hal0/slots/state.py` survives; per-slot systemd template + Provider ABC die. `SlotManager.start(slot)` becomes a Lemonade `/v1/load` call.

### 7. Model namespace: registered vs `user.*`, no `extra.*`

`registry.toml` is the source of truth; `hal0 registry sync` regenerates Lemonade's `server_models.json` and restarts `lemond`. User pulls (HF coords or local imports) go through `POST /v1/pull` with `user.*` prefix + explicit type label. Lemonade's `extra.*` auto-discovery namespace is unused — `extra_models_dir` points at the canonical models tree only for path compatibility, and it contains only registered-or-user entries by construction. Dashboard surfaces two badges (`blessed` / `pulled`); no third tier.

### 8. OmniRouter is client-side, owned by hal0

The OpenAI tool-calling loop runs in hal0, not Lemonade — Lemonade provides the tool endpoints, hal0 provides the LLM loop that dispatches them. v0.2 ships 8 tools (5 upstream-mirrored, 3 hal0-custom: `embed_text`, `rerank_documents`, `route_to_chat`). Dynamic per-request filtering: a tool is included in the LLM prompt only if at least one enabled slot of its target type exists and (for label-gated tools) has the required model labels. LLMs without the `tool-calling` label receive no tools at all.

### 9. Bundle picker on first run; no default model stack

`capabilities.toml` ships empty. First-run dashboard renders a four-tier hardware-anchored picker (`hal0-Lite` / `Default` / `Pro` / `Max`) plus the vendor-blessed `LMX-Omni-52B-Halo` kit, with `/proc/meminfo`-driven greying-out and a "Skip — configure manually" path. hal0 is positioned as a platform, not a curated stack; opinionation is a per-tier concept.

### 10. Clean break, no migration script

`install.sh` detects v0.1.x state (presence of `/etc/hal0/slots/*.toml` AND absence of `/var/lib/hal0/lemonade/config.json`) and refuses to install, printing explicit backup / wipe instructions. v0.2 ships `hal0 registry import` as the only recovery path — slot selections must be re-done via the bundle picker. The v0.1.x audience is single-digit alpha users; migration-script ROI is poor.

## Consequences

### Positive

- **Unified runtime.** Six toolbox Dockerfiles + publish pipelines + per-modality Provider classes collapse into one Lemonade install + one `LemonadeProvider` + one systemd unit. Maintenance surface reduces by an order of magnitude.
- **Full modality coverage.** LLM + embed + rerank + transcription + tts + image + NPU LLM/STT/embed all served from the same process. The "5/7 broken" frame from spike #1 was wrong; the actual coverage is complete for v0.2.
- **NPU triple-role via FLM trio.** Three NPU slots backed by one FLM child process at ~2 GB total memory, validated at 40 tok/s chat + concurrent transcription + concurrent embedding. The NPU stops being a single-purpose accelerator and becomes a real multi-modal device under hal0.
- **Per-type LRU concurrency.** Multi-LLM co-residency, cross-type concurrency (chat + embed + rerank simultaneously), and NPU + GPU triple concurrency all validated in Phase B and Phase C of spike #2. Real production-quality parallelism with bounded resource use.
- **Standard systemd supervision** replaces fragile per-slot template + adoption logic.
- **Newer llama.cpp build** via AMD's release train; hal0 no longer carries a pinned llama.cpp branch.
- **AMD legitimacy** for v1.0 positioning — Lemonade is the AMD-endorsed Strix Halo inference stack.

### Costs / risks accepted

- **KV% missing for GPU slots in v0.2.** Lemonade's bundled `llama-server` (b9253 Vulkan, b1274 ROCm) returns `null` for `n_past` / `n_prompt_tokens` / `prompt` in `/slots` responses. PR #124's KV%-from-`/slots` strategy does not survive the migration. Dashboard shows `—` for KV% on llamacpp slots; FLM/NPU slots get KV% native from `kv_token_occupancy_rate_percentage`. v0.2.x patch path: hal0 builds its own llama-server and swaps via `lemonade config set llamacpp.{rocm_bin,vulkan_bin}` if upstream doesn't populate the fields within ~6 weeks.
- **Clean-break upgrade burns the small alpha audience.** v0.1.x users have to back up + wipe + reinstall, then re-pick a bundle. The audience is small enough that this is acceptable; the migration-script work isn't.
- **Ongoing pin maintenance for two upstream artifacts.** The Lemonade embeddable tarball and the FastFlowLM `.deb` both ship breaking changes on roughly weekly / monthly cadences. Each hal0 release manually bumps both pins, sha256-verifies, and CI-smokes the install + a triple-concurrency probe before tagging.
- **Bundled tool definitions drift risk.** Upstream Lemonade's `toolDefinitions.json` evolves out-of-band; hal0 mirrors it with checksum-pinned copy + CI fail-on-drift. Drift is review work, not breakage, but it's recurring work.
- **kokoro:cpu only for v0.2 TTS.** No GPU-Kokoro on Linux upstream. UI surfaces a `[CPU]` chip + tooltip on the voice slot card. Accepted; the GPU TTS gap is real but the alternatives (ComfyUI-style stacks) carry far heavier maintenance burdens than they remove.
- **Performance: parity-to-regression vs hal0-Vulkan baseline** on tested models (-13% to -18% in spike #1; hermes-14b at parity). Accepted in exchange for the maintenance collapse.
- **ComfyUI workflows lost.** `sd-cpp` covers the 90% case; power-users are directed to external ComfyUI installations.
- **Moonshine ASR retired in favour of whisper.cpp.** More accurate but heavier on weak CPUs; the lite-tier audience may notice.

### Out of scope (deferred)

- Per-modality lemond processes (Phase A validated they work; ADR-0008 rejects the topology as unnecessary complexity for v0.2 — revisitable in v0.3 if a modality-isolation reason emerges).
- Upstream PR to make Lemonade's evict-on-failure policy configurable. Worth proposing eventually; not on v0.2 critical path.
- `route_to_chat`-style cross-NPU delegation. Blocked by the FLM-trio's single-chat-model constraint.
- OmniRouter `recall_memory` tool. Depends on Cognee MCP maturity in Phase 8 (ADR-0005).
- Lemonade Omni vs hal0 capability-orchestrator interop strategy. Coexist in v0.2; revisit pre-v0.3.

## References

- **Adoption plan (source of truth for the v0.2 contract):** `docs/internal/lemonade-adoption-plan-2026-05-22.md` — service topology, config baselines, slot model, NPU FLM trio, model management, OmniRouter, first-run UX, code reshuffle, PR sequence, operational caveats.
- **Spike #2 findings (empirical backing):** `docs/internal/lemonade-spike-2-findings-2026-05-22.md` — Phase A cross-process isolation passed, Phase B per-type LRU + `--threads` deadlock root-cause, Phase C NPU + GPU triple concurrency passed.
- **Re-grill artifacts:** `docs/internal/lemonade-research-2026-05-22/{researcher,architect,api,ui}.md` — 4-agent design pass that produced the six-path option matrix.
- **Upstream source-file map:** `docs/internal/lemonade-repo-deep-dive-2026-05-22.md` — referenced when modifying client code against `router.cpp`, `model_manager.cpp`, `backends/fastflowlm_server.cpp`, `Multi-Model-Spec.md`.
- **Superseded ADRs:**
  - `docs/internal/adr/0006-migrate-inference-to-lemonade.md` — original total-replacement decision; invalidated by inverted assumptions on embed/rerank failure, nuclear-evict trigger, multi-model concurrency, and the FLM Linux install path.
  - `docs/internal/adr/0007-nuclear-evict-all-mitigation.md` — pre-validation guardrails for the nuclear-evict path; the guardrails are no longer needed once the trigger conditions are correctly characterised.
- **Related ADRs (preserved):**
  - ADR-0001 — auth boundary (Bearer token survives unchanged; `lemond` runs loopback-only, no separate credential).
  - ADR-0002, ADR-0003 — capability overlay + slot-capabilities field; the capability layer is preserved, only the provider behind each capability changes.
  - ADR-0004, ADR-0005 — Phase 8 agents + Cognee memory engine; retargeted to v0.3 to ship after this migration, but unaffected at the architectural layer.
- **Glossary entries (canonical terminology):** `CONTEXT.md` — `slot`, `slot type`, `default slot`, `device`, `model namespace`, `FLM trio`, `bundle tiers`, `fresh install`, `OmniRouter`, `v0.1.x → v0.2 upgrade`.
- **Memories cross-linked into CONTEXT.md:** `hal0_lemonade_threads_deadlock`, `hal0_lemonade_flm_npu_install`, `hal0_lemonade_internals`, `hal0_lemonade_v1_load_schema`, `hal0_lemonade_ws_protocol`, `hal0_lemonade_omni_pattern`, `hal0_lemonade_gotchas`, `hal0_capability_slots_system`, `slot_architecture`.
