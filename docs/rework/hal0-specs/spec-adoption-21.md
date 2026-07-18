## 21. Adoption integration (Lemonade + ODS)

This section synthesizes the six cluster analyses of the Lemonade + ODS adoption candidates into one addendum. Sources are marked `L` (Lemonade), `O` (ODS), `L+O` (both). Every candidate is dispositioned as **dup** (already built/speced — plan should absorb any extra detail noted), **new** (genuine gap — see §21.x subsections), **decision** (scope fork — see §21 Decisions), or **out** (not applicable to hal0's architecture).

Verification altitude: all findings below were spot-checked against `rework/descar` code. Where a candidate is stronger or weaker than hal0's existing implementation, that is called out so the plan text can be corrected rather than silently re-litigated.

---

### 21.A Mapping table — every candidate by disposition

#### 21.A.1 DUP — already built or fully speced (absorb the noted extra detail)

| Candidate | Src | Maps to (plan §/spec/code) | Absorb into plan |
|---|---|---|---|
| POST /v1/pull SSE {file,bytes,percent} + complete/error; raw-HF pull | L | `api/routes/models.py` POST `/{id}/pull` + `/pull/stream` + `/pull/status`; ML-2 (fileset) | Note hal0 has NO `user.X` namespace — flat id space already accepts arbitrary `hf_repo`/`hf_url` via `/inspect`+pull, so any repo is raw-pullable. |
| POST /v1/pull background job survives UI reload | L | `_schedule_pull_task`, GET `/pulls`, `_reconcile_persisted_pull_job` | hal0 is stronger: jobs persist + reconcile across full `hal0-api` restart, not just UI reload. |
| GET /v1/pull/variants (enumerate GGUF quants/mmproj/top-5) | L | POST `/inspect` (5-min TTL cache, returns variants[]+tags) | Same capability, different verb; no gap. |
| GET /v1/downloads + control {pause,cancel,remove} | L | GET `/pulls`, POST `/{id}/pull/cancel`, DELETE `/pulls/{id}` | Only `pause` missing (cancel + range-resume exists). One-line follow-up on the pull-cancel route if/when ML-2 lands; not a new lane. |
| Host-agent /v1/model/{list,status,download,activate,delete} | O | §7.2 (one hal0 user + narrow privileged helper) | Confirm not orphaned; the "don't run as root" goal is met without a separate daemon. See Decision D1. |
| POST /v1/unload (specific/all) + POST /v1/delete | L | POST `/{name}/unload`, DELETE `/{id}` | Exists. |
| Per-model recipe_options.json keyed by canonical id | L | §7.1a/§8.2 model row (profile, extra_args, n_gpu_layers, chat_template, mtp, jinja) | Note in §7.1a: relational SQLite model-row supersedes a flat per-model JSON file — candidate fully absorbed, no new format. |
| extra_models_dir drop-in dir + `extra.` namespace | L | `config/schema.py` `ModelsConfig.roots: list[str]` + `registry/discover.find_candidates` | hal0 already scans multiple roots; no `extra.` id-namespace needed (one flat registry). |
| Multi-shard `gguf_parts[]` | O | ML-2 `plan_fileset`, `SHARD_RE`, discover stops deleting shards | Speced + goes further: revision-pinned enumeration, deterministic mmproj tiebreak, whole-fileset update-detect. |
| hal0 model apply one-shot (.env+config+reseed+restart) | O | §7.5/§8 `SlotConfigStore` ChangeSet + `stacks/apply.StackApplyEngine` | Atomic multi-file ChangeSet is a more robust version of ODS's env-swap-and-restart. |
| Model bundles/collections ("coding rig"/"vision rig") | L | `stacks/apply.StackConfig`/`StackApplyEngine` | This IS hal0's existing Stacks concept. |
| Two-stage idle degradation (downsize KV → evict) | L | `slots/manager._sweep_idle_once` (Stage-1 soft relabel, Stage-2 unload) | Keep the architectural note: llama-server allocates KV statically at ctx_size, so Stage-1 is bookkeeping-only (dashboard label); Stage-2 full unload is the only real reclaim. Docstring already states this. |
| Provider EngineAdapter boundary (URL norm, bearer, capability probe) | L+O | `providers/base.Provider` ABC + `api/routes/providers.test_upstream` | Split already clean. See §21.6 for the one gap (formalize the 4-state error enum). |
| Unified `<provider>.<model>` namespace in /v1/models | L | `dispatcher/router` registry-first + passthrough-cache | hal0 resolves by exact id/cached advertisement, not name-prefix; only relevant if Decision D1(routing) adopts fallback. |
| drop_params / master_key-from-env / per-mode required-key | O | n/a — LiteLLM-specific | hal0 has its own router; `master_key` covered by `HAL0_API_KEY`/`HAL0_ADMIN_API_KEY` (§1 hardening). |
| Per-provider path norm (/v1 vs /api/v1), enable_thinking:false, 900s timeout | L+O | `UpstreamEntry.url` (free-form base URL) + `normalize/thinking.py` + `UpstreamEntry.timeout_seconds` | `enable_thinking` normalization already exists per-slot; bump `timeout_seconds` (default 300s) per-upstream if a slow cloud model needs 900s. |
| enable_dgpu_gtt combined pool in capability checks | L | `hardware/probe.py:407-416` `max(vram_total, gtt_total)` | hal0's `max()` is MORE correct than ODS's "combined pool"/naive-sum framing (VRAM+GTT overlap in unified memory — a sum double-counts). |
| -ngl 99 default + tight per-model ctx | L+O | §7.1a; `-ngl 999` already default in every seed profile | Plan not weaker, just not-yet-executed; §7.1a already covers consolidating `-ngl` (set in 4 places). |
| HSA_OVERRIDE_GFX_VERSION + ROCM_PATH; verify /dev/kfd, renderD*, GIDs | O | `install.sh`, `preflight.sh:~449-676`, `providers/_gpu.py` | Already exceeds candidate — `qwen3tts.py` deliberately withholds HSA_OVERRIDE to avoid slow MIOpen fallback (nuance ODS misses). No absorption. |
| /metrics (Prometheus) + /live | O | §13.1 + `/api/metrics/prometheus`, `/api/health` | `/api/health` = requested `/live` (no new endpoint). But existing `/metrics` is slot-lifecycle-only + explicitly UNAUTHENTICATED — conflicts with candidate's "root-only/bearer" and §1 auth lane. See Decision D2. |
| WS /logs/stream (snapshot+live, resume via seq) | O | `api/routes/logs.py`, `slots.py:1468` (SSE) | Implemented as SSE, functionally equivalent for a LAN dashboard. Verify resume-via-seq exists; if not, small fold-in, not a new subsystem. |
| Capability profile artifact (gfx_target/rocm_version/rocmfp4_supported) | O | `hardware/` probe → `hardware.json` (`HardwareInfo`) + `capabilities/` | Do NOT add a 2nd capability artifact (Phase-2 is deleting `capabilities.toml` double-bookkeeping). Extend `HardwareInfo` with the 3 fields + strict validation. |
| Telemetry: emit openinference.* AND gen_ai.* in one OTLP payload | O | §13.1 (Langfuse/OTLP off-by-default) | Zero telemetry code exists today (Langfuse is a separate CT105 podman stack, unwired). Fold "both attribute families in one span" as the format spec into §13.1; needs the §7.6 request seam first. |
| Redaction toggles + runtime on/off + /telemetry/flush | O | §13.1/§13.6 | No pipeline to toggle yet; fold as impl detail when the export is built (thinking blocks large+sensitive). |
| Reasoning normalization (canonicalize </think> variants) | O | §7.6 + `toolloop/engine._THINK_RE` | Largely done (one canonical regex); only extra closing-tag variants missing — one-line add when §7.6 lands. |
| Backend contract JSON per device | O | §7.1b `RUNNER_IMAGES` | WIDEN §7.1b with `public_api_port`/`public_health_url`/`provider_url` — one artifact, not a 2nd `config/backends/*.json`. See Decision D2. |
| Model-family args config (checkpoint_regex, 3-layer precedence) | O | §7.1a `FAMILY_DEFAULTS` + §7.1d `Model.architecture` | Core need covered (keyed off `architecture`, not filename regex); note candidate's `checkpoint_regex`/`enable_regex_match` as an alternative matching strategy to weigh in §7.1a. |
| backend_url in /v1/health (child /metrics /props) | O | §6 /v1/health item (§21.3) | Just a payload field of that endpoint; fold in. |
| /v1/reranking (→ llama.cpp /v1/rerank) | L | `v1.py:1098` `/rerankings` + `:1103` `/rerank` | Shipped + documented in `first-chat.mdx`. |
| /v1/embeddings (encoding_format) | L | `v1.py:1083` `/embeddings` | Shipped. Residual: doc the LangChain `check_embedding_ctx_length=False` gotcha in §21.12 client docs. |
| /v1/audio/transcriptions | L+O | `v1.py:1112` (multipart) | Shipped (the streaming gap is §21.9 /v1/realtime). |
| hal0 CLI thin dispatcher + verbs + completion | O | `cli/main.py` typer app (`add_completion=True`) | Cleaner than hand-rolled `lib/hal0-*.sh`; per-verb `hal0-x` aliases not worth it. |
| hal0 agent start/stop/status/logs (systemd) | O | `cli/agent_commands.py`, `agent_shim.py`, `hal0-agent@.service` | Deeper than candidate; no macOS/launchd/nohup fallback needed (LXC/systemd only). |
| hal0 bench (TTFT+TPS, JSON, --compare) | O | §20 Bench rework + §20.1 auto-tuner | Plan stronger. Add only the "needle" long-context-position scenario to §20's target list. |
| mDNS announcer (hal0.local, re-announce) | O | `services/mdns.py` (avahi) | Avahi inherits LAN/loopback gating + event-driven inotify — superior to a hand-rolled zeroconf poll. |
| Dashboard per-concern routers + Vite /api proxy | O | `api/routes/*.py` (~40 files) + `ui/` | Already the architecture. |
| Hardcoded "do-not-destroy" service set | O | `services/registry.SERVICES` (static Python + per-service action allow-list) | Protection holds by construction (no config→fail-open path). Note llama-server itself is owned by `slots/manager.py`, not this allow-list — flag to §7.5/§11 only if slot-delete gains a config-driven allow-list. |
| service_id/model_id validation regex; loopback host/port probe | O | `services/systemd._UNIT_RE`, `registry/pull._SANITISE_RE`/`_SHA256_HEX_RE` | Regexes enforced at exec boundary. A generic loopback host/port probe endpoint doesn't exist — minor/low-pri; if built, apply §1's SSRF/RFC1918 gating. |
| Stable model-name strings (suffix quant/device) | L | de-facto HF-publishing convention (`Qwen3-4B-ROCmFP4-Strix`) | Codify as one sentence in §7.1/`choose-models.mdx`; no new mechanism. |
| Process-group kill on child timeout | L | `providers/container.py` `podman stop -t 20` (cgroup-scoped) | **out** — hal0 runs backends in Podman cgroups under systemd; `podman stop` is a stronger guarantee than a PGID kill. Different, already-solved architecture. |
| Layered validation (unit→container→VM→real-hw) | O | CONTRIBUTING.md α/β/γ tiers + `hal0-test` LXC gate | Already a superset (γ runs on real Strix-Halo over SSH). Phase 4 should read "extend," not "build." |
| Capability-deferral state machine | O | release-test row statuses + `release-gate-report.json` | Coarser 4-state exists; refine existing rows + hook into pull-job states (priority #3), don't add a parallel machine. |
| Validation receipt template | O | `release-gate-report.json` + `release-check.sh` (7 gates) | Add hw/install-cmd identity + rollback/limits fields to the existing report — schema add, not new machinery. |
| Support tiers A/B/C + GPU tier map | O | `model_fit.evaluate_model_fit`, `hardware/recommend.py` | Logic is already a pure function; only the doc label missing. NAMING: call it "support class" — "tier" is double-booked (bench A/B/C, test α/β/γ). |
| Digest-pinned image refs + KNOWN-GOOD-VERSIONS.md | L+O | `manifest.json` + `update-toolbox-digests.sh` + gate #4 | Shipped + enforced. Each digest atomically encapsulates its llama.cpp/ROCm versions, so KNOWN-GOOD-VERSIONS.md is largely redundant — a human-readable companion table is COULD, not MUST. |
| Agent profile YAML (id/model/system_prompt/tools/…) | O | `agents/personas.Persona` + `/api/agents/{id}/personas` (§7.3/§7.4) | ~80% present. Absorb missing fields via §21.13 (fallback_model, routing_rules, tool_config, schema_version). Keep the name **persona** — "profile" already = model-runner profiles. |
| Local-first model + named fallback per profile | O | `persona.preferred_model` + dispatcher Rule 9 (ADR-0023) | Global fallback exists; per-persona chain missing → §21.13. Compose with Decision D1(routing), don't build a 2nd mechanism. |
| Capability/feature catalog (services_any, vram/disk gates) | O | `model_fit.evaluate_model_fit`, `capabilities/profile_fit.py` | HW-gating half exists. `services_any` OR-disjunction N/A (memory=Hindsight, single locked backend) unless Decision D5 reopens. |
| POST /run-agent → SSE tool events | L+O | `api/routes/board_chat.py` POST `/api/board/chat` | **DONE, near-verbatim** ({token,thinking,tool_call,tool_result,done,error}, shared toolloop). Mark DONE so nobody rebuilds. Follow-on in §21.13: generalize board-framed loop to slot-agnostic once §7.6 lands. |
| Voice pipeline (STT→LLM→TTS) as canonical workflow | O | §19 voice stack + §7.1d ASR/TTS modalities | Components adopted at model level; only "package as one named recipe" missing — small doc task once §19 lands. |
| toolDefinitions.json single source (UI↔server) | L | `omni_router/tool_definitions.json` + `check-tool-definitions.sh` | **DONE** incl. deferred drift-check script. Close out the drift-check in Phase 4. |
| Async job API (submit/poll/fetch) | O | `comfyui/fetch.py`, `provision.py` | **out** — ComfyUI already has job_id async submit/poll/fetch; don't generalize until a 2nd async backend ships (§2 "narrow every abstraction to its single concrete"). |

#### 21.A.2 NEW — genuine gaps (see §21.x subsections)

| Candidate | Src | Priority | Lands in |
|---|---|---|---|
| amdgpu `gttsize=120000` modprobe | O | MUST | §21.1 (host tuning) + preflight WARN |
| GRUB `amd_iommu=off` | O | MUST | §21.1 + preflight |
| `tuned-adm profile accelerator-performance` | O | MUST | §21.1 + preflight |
| `ppfeaturemask=0xffffffff`, `gpu_recovery=1` | O | MUST | §21.1 (same modprobe.d file) |
| ttm `pages_limit`/`page_pool_size` (derived from gttsize) | O | MUST/SHOULD | §21.1 |
| `vm.swappiness=10`, `vm.vfs_cache_pressure=50` | O | MUST/COULD | §21.1 + preflight |
| `update-initramfs -u` + reboot gate | O | MUST | §21.1 (loud, non-skippable) |
| Build/verify gfx1151 HIP arch + refuse-to-start guard | L+O | MUST (highest-value in cluster §2) | §21.2 + §7.1b |
| Persist ROCm kernel cache + generous cold-JIT `wait_for_ready` (~15-20 min) | L | SHOULD | §21.2 (agent_shim `_READY_TIMEOUT_S=90` far too short vs ~12-min ROCmFP4 JIT) |
| `--parallel` tier-scaled (Strix Halo → 8-12) | O | MUST | §21.2 (mechanism-complete, policy-missing; measure via `server_ab.py --mode batch` first) |
| ROCM_PATH resolution order + rocm_channel/rocm_bin pin | L | SHOULD | §21.2 (toolbox/build lane) |
| POST /v1/load blocked-arg denylist | L | SHOULD | §21.7 / §7.1a (`MANAGED_ARGS_DENYLIST`) |
| Managed-args reject in extra_args (--model/--ctx/--host/--port/-ngl) | O | MUST | §21.7 / §7.1a (`slots/argv.py`) — same code path |
| Bootstrap fast-start model → background swap to full | L+O | COULD | §21.8 (mechanism = existing `swap_slot`; only first-boot policy new) |
| `--source huggingface\|modelscope` + HF_ENDPOINT mirror | L | COULD | §21.8 (fold into ML-2 fileset as optional param) |
| `max_loaded_models` per model-type LRU | L | SHOULD/low | §21.10 (per-modality budget on P3-slots reaper) |
| `auto_evict` + threshold_pct (GTT-aware) | L | SHOULD (elevate) | §21.10 — **single most concrete gap**: pressure probe reads raw `/proc/meminfo` not GTT-aware `CapacitySnapshot` (the user's own "pve GTT hidden memory" blind spot) |
| Operator-settable pin + protect manual /unload of pinned | L | SHOULD | §21.10 (`SlotConfig.pinned` + 409/force on manual unload) |
| `eviction_score = idle/(load×weight)` | L | COULD | §21.10 (optional reaper refinement) |
| /v1/health per-model detail | O | MUST | §21.3 (as `GET /api/models/health`) |
| /v1/stats + /v1/system-stats | O | MUST | §21.3 (read API over §13.3 tables) |
| /v1/system-info (hw enum + backend install state) | O | MUST | §21.3 (fold `/api/hardware`+`/api/features`+ §7.1b lifecycle) |
| hal0 doctor `--json`/`--report` + stable diagnosis IDs | O | MUST | §21.4 (retrofit existing 1420-line doctor onto `_diagnosis` dataclass) |
| Support bundle (redaction, TSV, ROCm captures) | O | MUST | §21.4 (`hal0 doctor bundle`) |
| Backend lifecycle state (installed/update_available/…) | O | SHOULD | §21.3/§21.6 (§7.1b registry field) |
| backend_versions.json + rocm_arch_overrides + startup gfx-guard | O | MUST | §21.2/§21.6 (feeds `HAL0-GFX-TARGET-UNSUPPORTED`) |
| recipe:backend colon selector | O | COULD | §21.6 (§7.1b CLI sugar) |
| Auto-select installed-on-disk beats preference + `prefer_system` | O | MUST | §21.6 (§7.1b selection logic) |
| /v1/models extensions (recipe/checkpoint/labels/downloaded) + show_all filter | L | MUST | §21.5 (`v1.py`) |
| POST /v1/messages (Anthropic) + `hal0 launch claude` | O | MUST | §21.9 (highest strategic value — unlocks Claude Code) |
| /v1/tokenize (+/detokenize) | L | SHOULD | §21.5 (thin proxy to llama-server native) |
| WS /v1/realtime (OpenAI Realtime, PCM16, VAD) for OpenWhispr | O | MUST | §21.9 (own subsection; depends on §19 whisper.cpp slot) |
| Multiple path prefixes (/v0, /api/v1) | O | SHOULD | §21.5 (extra `include_router` mounts, near-zero cost) |
| Terminal chat REPL (`/think`, `--no-stream`, strip reasoning) | O | SHOULD | §21.14 (`hal0 chat`) |
| VRAM-scaled sub-agent concurrency + per-persona timeout | O | SHOULD | §21.13 (gate on Phase-3 capacity signal; replace fixed `_MAX_LOOP_ROUNDS=8`) |
| Regex `routing_rules` pre-classifier | O | SHOULD | §21.13 |
| safe/dangerous shell-exec command list | O | SHOULD | §21.13 (with §14.1 security fast-track) |
| Client-connection docs (LangChain/Continue/Cursor/n8n) + troubleshooting table | O | MUST | §21.12 (`docs/guides/connect-clients.mdx`) |
| `hal0 setup-cursor`/`setup-continue` config writers | O | SHOULD | §21.12 (with §17 installer/CLI overhaul) |
| Offline/air-gapped mode (`--offline`, bundle default GGUFs) | O | SHOULD/COULD | §21.12 (§17-adjacent note; no telemetry exists to drop today) |
| PR template + high-risk-change map | O | MUST | §21.15 (`.github/PULL_REQUEST_TEMPLATE.md`) |
| Stable-patch triage 4-question tree | O | SHOULD | §21.15 (CONTRIBUTING.md; channel-count-independent) |
| hw-support-class doc table (from `model_fit`) | O | SHOULD | §21.15 (name "support class") |
| Network-exposure-policy CI test / ports contract / golden-paths | L+O | MUST/SHOULD | §21.11 (config contracts) |
| EngineAdapter 4-state error enum (unreachable/auth/model-missing/unsupported) | L+O | low | §21.6 (formalize on remote path; useful if Decision D1 routing lands) |

#### 21.A.3 DECISION — scope forks (see §21 Decisions D1–D8)

| Candidate | Src | Decision |
|---|---|---|
| Local+cloud fallback ladder ({local:[cloud]}, num_retries, shuffle) | O | D1 |
| Privileged host-agent daemon (own bearer, per-service Lock, 16KB cap) | O | D1 |
| Two-secret split (dashboard key ≠ agent key) | O | D1 |
| Backend-contract JSON vs widen §7.1b registry | O | D2 |
| Auth on /metrics (root-only/bearer vs current unauthenticated) | O | D2 |
| Ollama-compat surface (:11434, /api/tags,/chat,/generate,/show,/ps,/embed) | O | D3 |
| Host-level Strix-Halo tuning blast radius (shared PVE host) | O | D4 |
| Document-RAG as first-class + services_any disjunction | O | D5 |
| Workflow catalog / generic DAG schema | O | D6 |
| AI-CI automation (nightly-review, claude-review, ai-triage, autonomous-scanner) + guardrails doc + prompt discipline | O | D7 |
| Five-channel release model vs locked §7.7 "remove nightly" | O | D8 |
| Upstream OSS PRs into Continue/Open WebUI | O | D3 |
| hal0-recipes standalone repo vs in-repo `bundles/` | L | D3 |

#### 21.A.4 OUT — not applicable to hal0's architecture

| Candidate | Src | Why out |
|---|---|---|
| `--enforce-eager` device-class default | L | vLLM/CUDA-graph-capture concept; hal0 is llama.cpp/HIP only — no equivalent flag, failure mode doesn't exist. |
| Process-group kill on child timeout | L | Podman cgroup `stop` is stronger; PGID trick only needed for bare subprocesses. |
| Async job API generalization | O | ComfyUI already covers the one async backend; don't generalize prematurely. |
| LiteLLM `drop_params`/`master_key`/required-key | O | hal0 has its own router, not a LiteLLM front. |

---

### 21.1 Host/hypervisor Strix-Halo kernel tuning (Proxmox layer — outside hal0's own install) — MUST, highest concrete ROI

Genuinely new lane, absent from plan and repo (grep: no `gttsize`/`modprobe.d`/`tuned-adm`/`sysctl` artifacts anywhere in `installer/` or `packaging/proxmox/`). These are kernel/GRUB/tuned-adm/sysctl settings that apply to the **PVE hypervisor hosting the new "halo" LXC**, NOT to hal0's own install path — an LXC shares the host kernel and cannot `modprobe`/GRUB/`tuned-adm`/`sysctl` the box. Scope is explicitly separate from §17's `hal0 provision --stage=system|services` (which only ever runs inside the halo guest).

**Ships as** a one-time, idempotent host-prep script, sibling to `packaging/proxmox/hal0-test-template/provision.sh` (e.g. `packaging/proxmox/host-tune-strix-halo.sh`) that:
- Writes `/etc/modprobe.d/amdgpu-hal0.conf`: `gttsize=120000`, `ppfeaturemask=0xffffffff`, `gpu_recovery=1`, plus ttm `pages_limit`/`page_pool_size` **derived from the chosen gttsize** (document the formula; don't hardcode two independent constants that can drift).
- Appends `amd_iommu=off` to `GRUB_CMDLINE_LINUX` (document that `iommu=pt` is NOT equivalent).
- Sets `tuned-adm profile accelerator-performance` (idempotent set + verify).
- Writes `/etc/sysctl.d/99-hal0-strix.conf`: `vm.swappiness=10`, `vm.vfs_cache_pressure=50`.
- Runs `update-initramfs -u`, then prints a **loud, non-skippable required-reboot banner and exits nonzero until an env var confirms the reboot happened** (matches hal0's "never auto-hide, always surface" posture).
- Documents the BIOS UMA Frame Buffer minimum as a precondition (not scriptable).
- Is **NOT auto-invoked by any hal0 installer path** (see Decision D4 — host-wide blast radius needs explicit opt-in).

**Verification (into `hal0 doctor`/preflight, MUST):** add WARN-only read-checks to `installer/lib/preflight.sh` alongside the existing `/dev/kfd`/`/dev/dri/renderD*` checks — read `/sys/module/amdgpu/parameters/*` (gttsize/ppfeaturemask), `/proc/cmdline` (amd_iommu), `sysctl vm.swappiness`, `tuned-adm active`. All are readable from inside the halo LXC because they reflect real host-kernel/global state, not namespaced values. Surface as a single "host not tuned for Strix Halo, expected +X% inference" WARN, never a hard failure (hal0 runs correctly untuned, just slower). Flag every numeric perf claim (e.g. amd_iommu=off +2–6%) as measure-first via `hal0-tune`, not blind-adopt.

**Coordination:** device_class-scoped runtime concerns that ARE container-level (arch guard, cold-start timeouts, `--parallel` tiers) stay in §21.2/§7.1b, not here. §17 explicitly does not extend into this scope.

### 21.2 gfx1151 arch-guard + ROCm cold-start/kernel-cache + --parallel tiers — MUST

Container/process-level companions to §21.1, slotting into existing plan machinery (§7.1a flags, §7.1b runner registry, §20 bench).

**(a) gfx1151 refuse-to-start guard (highest-value item in cluster §2).** Confirmed absent — no `system_info`/HIP-arch probe anywhere in `providers/container.py` or `mcp/probes.py`, despite `mcp/probes.py` already decoding `gfx_target_version` → `gfxNNNN`. The failure mode is **silent garbage (all-`?`) output, not a crash.** Add `required_hip_archs` to the §7.1b `RUNNER_IMAGES` entry and a startup probe (reuse the existing gfx-decode helper) that checks the launched llama-server's reported `system_info` HIP archs against the registry before marking the slot READY. On mismatch, transition WARMING→failed (never a lying READY). Surface pass/fail as doctor diagnosis ID `HAL0-GFX-TARGET-UNSUPPORTED` (§21.4). Pair with a `backend_versions.json` artifact (or a version/digest field folded into the widened §7.1b registry) recording the pinned llama.cpp build + `rocm_arch_overrides` suffix per runner.

**(b) ROCm cold-JIT persistence + timeouts (SHOULD).** The MIOpen `/cache`-mount pattern (`MIOPEN_USER_DB_PATH`/`MIOPEN_CUSTOM_CACHE_DIR`) exists ONLY for `providers/qwen3tts.py:181-192`, not the main ROCm llama-server containers in `providers/container.py`. Separately, `cli/agent_shim.py:383` hardcodes `_READY_TIMEOUT_S = 90.0` — far short of the ~12-min ROCmFP4 cold-JIT in the user's own `rocmfp4-quant-procedure` memory. Fix: (1) verify what the ROCmFPX fork actually JIT-caches to disk (rocBLAS/hipBLASLt tuning DB vs fork kernels), then extend qwen3tts's proven `/cache`-mount pattern to `container.py` if there's a real cacheable artifact; (2) audit `agent_shim._READY_TIMEOUT_S` and the `slots/manager.py` WARMING ceiling against the ~12–20 min reality and raise. Tie to §7.1b: make `cold_start_timeout_s` a device_class-scoped registry field, not a global constant.

**(c) `--parallel` tier-scaling (MUST — mechanism-complete, policy-missing).** Fully wired end-to-end (`providers/container.py`, `slots/argv.py`, `config/schema.py`, slot_view) but every seed profile hardcodes `--parallel 1`; `CHANGELOG.md:489` already flags defaults "stay --parallel 1 pending the on-box -np sweep (server_ab.py --mode batch)". Do NOT blind-adopt ODS's flat 8-12 — `hal0-tune` rules out applying a community claim without local measurement, and `server_ab.py --mode batch` is the exact sweep tool. Run the sweep now that the seam exists, then encode the winning tier-scaled defaults as a device_class-scoped field consumed by §7.1a flag resolution (and/or §7.1b registry), superseding `--parallel 1` in `installer/etc-hal0/profiles.toml` + `config/schema.py` seeds. Ties to §20 (np/parallel already a sweep axis).

**(d) ROCM_PATH build reuse (SHOULD, low-urgency, toolbox lane).** Document the resolution order (`ROCM_PATH` env → `rocm-sdk` path → `/opt/rocm`) + a `rocm_channel` (stable/nightly) + `rocm_bin` pin in `installer/agent-skills/hal0-quantize/` (already has `rocmfpx-env.sh`/`presets.md`) and `docs/design/container-image-overhaul.md`. Not urgent enough to block the model-layer epic or §17.

### 21.3 Introspection endpoints — MUST

hal0 has `/api/status`, `/api/health(/system)`, `/api/metrics` (stub), `/api/metrics/prometheus` (slot-lifecycle only), `/api/logs/stream` — but not the specific read surfaces ODS names. Keep hal0's `/api` naming, not literal `/v1`.

- **`GET /api/models/health`** (MUST) — per-model `{checkpoint,last_use,type,device,pinned,recipe,pid,recipe_options,backend_url}` shape; extend `health.py`'s `/api/status` merge logic, read `SlotManager` + the §13.3 `slot_sample`/`request_metric` tables. Sequence after ML-1 (needs those tables). `backend_url` exposes the child llama-server `/metrics`//`/props` (not proxied).
- **`GET /api/stats` + `GET /api/system-stats`** (MUST) — thin read API over §13.3's `request_metric`/`slot_sample` (TTFT/tok-s/vram/gpu%), which §13 defines but never exposes a read API for. This becomes the dashboard's data source instead of ad-hoc dashboard-only SQL. Add as an explicit bullet under §13.7 sequencing.
- **`GET /api/system-info`** (MUST) — one consolidated endpoint folding `/api/hardware` + `/api/features` + §7.1b's new backend lifecycle-state field (`installed/update_available/update_required/installable`), rather than three overlapping surfaces. Feeds a future setup-wizard "install this backend" action.
- **`/api/metrics/prometheus`** — expand body once §13 T1/T2 aggregation lands (currently only `hal0_slot_up/state/ready_total`). `/api/health` already = the requested `/live` (zero-work liveness) — no new endpoint. Auth on this route is Decision D2.

### 21.4 hal0 doctor rework + support bundle (new §13.8) — MUST

`hal0 doctor` already exists (1420 lines: perms/models/profiles/migrations/toolbox-pull/verify/logs + `preflight.sh` shell-out) — this is a retrofit, not greenfield (~1–2 weeks). Missing: a stable diagnosis-ID taxonomy (`HAL0-GFX-TARGET-UNSUPPORTED`, `HAL0-ROCM-LIB-MISSING`, `HAL0-MODEL-FILE-MISSING`…), a structured `_diagnosis(id,severity,confidence,evidence[],next_steps[])` return type, a `--json` flag, and structured autofix hints (beyond ad-hoc `repair_flm_store`/`repair_tree_group_share`).

Retrofit the existing checks onto one shared `_diagnosis` dataclass + `--json` renderer, adding IDs as each check is touched. Add **`hal0 doctor bundle`** (support bundle): redact KEY/TOKEN/Bearer from config dumps, emit a command-status TSV, layout system/config/diagnostics/logs/manifest, include `rocm-smi --showall` + `rocminfo` captures. Same PR/section. Sequence after §21.2's gfx-arch guard (needs a diagnosis ID) and §13's metrics tables (evidence source).

### 21.5 OpenAI-compat surface extensions — MUST/SHOULD

- **`/v1/models` extensions** (MUST) — current `GET /v1/models` (`v1.py:673`) emits only `{id,object,created,owned_by,name,context_length}`; the registry stores richer fields (`labels`, checkpoint/recipe) never surfaced on the read path. (1) Extend `hal0_slot_alias_models()` + the upstream-catalog loop to emit `labels`, `recipe`/`checkpoint`, `downloaded`; (2) alias `context_length` → `max_context_window` (or emit both); (3) add `show_all` query param (mirror the `owned_by` filter at `v1.py:807`) defaulting to hiding non-text-modality raw upstream catalog entries (e.g. image-gen models leaking in). Low-risk, additive; Claude Code probes this first, so it precedes §21.9 /v1/messages.
- **`/v1/tokenize` + `/detokenize`** (SHOULD) — absent; thin proxy to llama-server's native endpoints, routed by slot/model. Ties to §13's `ctx_used` metric + client-side prompt-fitting.
- **Extra path prefixes `/v0`, `/api/v1`** (SHOULD) — routers mount only at `/v1` (`api/__init__.py:1281-1282`); add duplicate `include_router` calls for clients that hardcode `/api/*`. Near-zero cost; rides along with the /v1/models work.

### 21.6 Backend/engine abstraction hardening (extend §7.1b) — MUST/SHOULD

- **Widen `RUNNER_IMAGES`** with `public_api_port`/`public_health_url`/`provider_url` so the registry is the single backend-contract source (currently scattered in `container.py` URL/port assembly). One artifact, not a 2nd `config/backends/*.json` — see Decision D2.
- **Managed-args denylist** (MUST) — see §21.7.
- **Backend lifecycle state** (SHOULD) — add `installed/update_available/update_required/installable`, computed by comparing pinned digest vs `podman image inspect`, surfaced via `/api/system-info` (§21.3). `updater.py`'s `update_available` today is whole-hal0-release only.
- **Auto-select** (MUST) — §7.1b covers registry shape but not selection when multiple backends exist. Prefer already-installed-on-disk over preference-order (avoid an unnecessary multi-GB pull), with a `prefer_system` escape hatch for operators managing their own ROCm.
- **`recipe:backend` colon selector** (COULD) — minor CLI sugar resolving into registry keys; follow-up once the registry exists.
- **EngineAdapter 4-state error enum** (low) — formalize `unreachable/auth/model-missing/unsupported` on the remote-upstream path (`test_upstream` is ad-hoc strings today). Only worth it if Decision D1(routing) adds retry logic.

### 21.7 Managed-args denylist (part of §7.1a flag resolution) — MUST

`[server].extra_args` is a free-form `shlex`-split string appended last-wins in `container.py`'s `resolve_argv`, with NO denylist anywhere in `slots/argv.py` (`merge_flags`/`normalize_argv`). A slot's `extra_args` (or a future request-level `llamacpp_args` on `/load`) can pass `--model`/`--ctx-size`/`--host`/`--port`/`-ngl` and silently clobber the OpenAI-shim contract or redirect the model file — a real correctness/security gap (a compromised board_chat/brain tool call could exploit it). Add a hardcoded `MANAGED_ARGS_DENYLIST` checked in `merge_flags`/`normalize_argv` before the `extra_args` segment is appended, erroring loudly (400/config-validation) instead of producing a broken running slot. Same merge algorithm as the §7.1a 5-tier precedence rewrite (`request→recipe_options→arch_defaults→env→default`) — land in the same PR.

### 21.8 Model-management niceties — COULD

- **Bootstrap fast-start → background swap** (COULD) — mechanism exists (`slots.swap_slot`: unload+load on a live slot with registry pre-validation); only first-boot policy is new (seed a tiny default model at install, auto-swap once a larger background pull completes). Thin installer/first-run enhancement, not a new primitive.
- **`--source huggingface\|modelscope` + HF_ENDPOINT mirror** (COULD) — no modelscope/mirror support exists; low value for a single-operator LAN box. Fold as an optional `hf_download_url` param into ML-2 fileset work.

### 21.9 Anthropic + Realtime + streaming STT — MUST (highest strategic value)

- **`POST /v1/messages` (Anthropic) + `hal0 launch claude`** (MUST) — fully absent. Single highest-strategic-value item: unlocks Claude Code and any Anthropic-SDK client with one command. (1) New route in `v1.py` implementing the Messages API shape — translate `{system, messages[content-blocks], tools, stream}` into the existing OpenAI chat-completions request the dispatcher routes, and translate the response/SSE back (Anthropic's `message_start`/`content_block_delta`/`message_delta`/`message_stop` differ from OpenAI chunks — a small but real translation shim, not passthrough); reuse `hal0_chat_slot_alias_map` so `model:"agent"` keeps working. (2) `hal0 launch claude` CLI verb sets `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` (dummy) + default model, then execs `claude`. Sequence after §21.5 /v1/models (Claude Code probes it first).
- **`WS /v1/realtime` for OpenWhispr** (MUST, own subsection) — the only WS route today (`board_ws.py`) is the unrelated kanban proxy; `/v1/audio/transcriptions` is multipart-only (the "per-chunk" pattern to replace). Concrete personal motivation: the user's OpenWhispr already points at hal0's `/v1` (`whisper-v3:turbo`, per memory). New `WS /v1/realtime` accepting 16 kHz PCM16 frames + VAD config + manual/auto commit (mirror OpenAI's Realtime protocol closely so OpenWhispr's client needs minimal changes), buffering server-side. First cut: windowed re-transcription of a rolling buffer with VAD-triggered commits against the existing batch endpoint, upgraded to true streaming once whisper.cpp's server gains it. **Depends on §19's whisper.cpp large-v3-turbo GGUF slot** — sequence after that lands.

### 21.10 Multi-model memory manager (fold into P3-slots reaper) — SHOULD (D-elevate one item)

hal0's LRU idle/pressure eviction, two-stage degrade, and pinning-exempt eviction already exist and are often stronger than the candidates. Three verified gaps fold into the P3-slots `reaper.py` extraction:

- **GTT-aware pressure probe (elevate — single most concrete gap).** `_pressure_evict_once` → `_probe_host_free_mb` → `capacity._read_meminfo` reads RAW `/proc/meminfo MemAvailable` with a fixed MiB floor, NOT the already-built GTT-aware `slots/capacity.CapacitySnapshot.free_vram_mb` — exactly the user's "pve GTT hidden memory" blind spot (amdgpu GTT isn't charged to normal RAM accounting). Switch the probe to `CapacitySnapshot.free_vram_mb/total_vram_mb` (or a direct rocm-smi/sysfs GTT read); optionally express the floor as `threshold_pct` of total, not only an absolute MiB.
- **Operator pin + manual-unload protection.** Automatic-eviction exemption exists (`_PINNED_BY_DEFAULT` frozenset: chat/agent/npu) but there's no `SlotConfig.pinned` field and `POST /{name}/unload` works unconditionally on a pinned anchor. Add `SlotConfig.pinned: bool` (overlay onto `_PINNED_BY_DEFAULT`) + require `force=true` (else 409 `slot.pinned`) on manual unload/delete.
- **Per-modality budget (SHOULD, low).** Global `[slots].max_slots` cap + LRU exist but no per-type quota ("never >1 vision model resident"). hal0's slots are fixed/named/role-typed with swap (not a dynamic same-type pool like Lemonade), so this is a smaller optional refinement, not a MUST — don't block the reaper extraction on it.
- **Weighted eviction score (COULD)** — replace plain idle-LRU with `idle/(load×weight)` to protect slow-to-reload models; optional, low-value.

### 21.11 Config contracts (network-exposure-policy CI + ports + golden paths) — MUST/SHOULD

Absorbs the "default-deny bind", id-validation, and loopback-probe hardening themes from §8 into a concrete CI/config contract lane (currently scattered):
- **Network-exposure-policy CI test (MUST)** — a test asserting no route/service binds beyond the intended LAN/loopback posture without going through §1's auth path; codifies the "unauthenticated by convention" surfaces (e.g. `/api/metrics/prometheus`, board routes) as an explicit allow-list the test guards, feeding Decision D2.
- **Ports contract (SHOULD)** — a single declared source of the ports hal0 and its companion services own (dashboard/api, slot ports, mDNS), drift-validated, so a slot's port can't silently collide (ties to §21.7 managed-args and the `_UNIT_RE`/`_SANITISE_RE` validators).
- **Golden-paths (SHOULD)** — pin the canonical on-disk layout (config/models/cache/logs) as a validated contract feeding `hal0 doctor` evidence and the support bundle (§21.4).

### 21.12 Client onboarding + docs — MUST/SHOULD

- **`docs/guides/connect-clients.mdx`** (MUST) — no per-client guide exists (grep confirms). Cover LangChain (`ChatOpenAI(base_url=…)`, note `check_embedding_ctx_length=False`), Continue (config.json provider block), Cursor (custom OpenAI-compatible endpoint), n8n (OpenAI node base_url), each with the host-vs-container base_url distinction (follow the `HAL0_OPENWEBUI_PUBLIC_URL` template in `first-chat.mdx`). Bundle a **troubleshooting table** per integration (base_url mistakes, no-built-in-auth header confusion, alias-not-found, SSE-parsing gotchas) into the same doc.
- **`hal0 setup-cursor`/`setup-continue`** (SHOULD) — two small CLI verbs writing the ~8-line client config pointing at `/v1` + a default slot alias, same shape as `cli/setup_command.py`'s first-run wizard. Lands with §17 (Lane E); pairs with `hal0 launch claude` as the third onboarding one-liner.
- **Offline/air-gapped mode** (SHOULD/COULD) — no such mode today (the only "offline" concept means install-time-before-api). There is also no telemetry to drop (`registry/update_check.py` only hits HF on-demand via CLI). So this is: (1) a `--offline` flag skipping HF update-check + first-run network probes, (2) optionally bundling a default small LLM+embedding GGUF for first-boot-without-pull, (3) local-RAG web-search fallback (blocked on Decision D5). Short §17-adjacent note, sequenced well after the higher-value §21.9 items.

### 21.13 Persona schema hardening (new §10.x) — SHOULD

Extend `agents/personas.Persona` + its TOML schema rather than introducing a new "agent profile" concept (avoids a 3rd meaning of "profile" alongside model-runner profiles and `profile_fit`):
- Add `fallback_model` (composes with Decision D1's local→cloud ladder; do NOT build a 2nd fallback mechanism vs dispatcher Rule 9).
- Add `routing_rules: list[{pattern, target}]` — a regex pre-classifier checked before the label-based dispatcher route.
- Add `tool_config` (structured per-tool config, not just the existing `tools_allowed` glob).
- Add `safe_commands`/`dangerous_commands` regex lists scoped to the `shell_exec` tool, rejected before the sandbox call — land with §14.1's security fast-track (same PR as auth fixes; §14.1 already flags `AUTONOMOUS_WRITE_TOOLS` running without approval).
- Add `timeout_s` per persona and gate `OmniRouter`/`board_chat` concurrent loops behind the Phase-3 capacity-manager's VRAM-headroom signal instead of the current fixed `_MAX_LOOP_ROUNDS=8` (`omni_router/router.py`, unbounded `asyncio.gather` today).
- Stamp `schema_version` on the Persona TOML.
- Once §7.6's shared `toolloop/engine.py` lands, generalize `board_chat`'s SSE loop (currently board/`caller_slot_name`-framed) into a slot-agnostic run-agent entry point any persona can drive — the SSE contract itself needs no change.

### 21.14 `hal0 chat` terminal REPL — SHOULD

New `cli/chat_commands.py` talking to local `/v1/chat/completions` over a slot alias, with `/think on|off|default` toggling the existing thinking-policy injection (`normalize.messages` already has this step) and stripping reasoning tokens from the REPL's in-memory history before the next turn (reuse dispatch's reasoning-separation logic, don't re-implement). `--no-stream` is a thin flag on the existing SSE client path. Useful for SSH/headless boxes; prevents reasoning-token context bloat. Self-contained, no cross-lane coordination.

### 21.15 Release-engineering hardening (new §11.x) — MUST/SHOULD

State explicitly in Phase 4 that layered validation (α/β/γ), validation receipts (`release-gate-report.json`), and digest-pinned images (`manifest.json`) are ALREADY SHIPPED, so Phase 4 reads "extend," not "build."
- **`.github/PULL_REQUEST_TEMPLATE.md`** (MUST) — risk-grade + touched-surface checkboxes + rollback note, cross-referencing §14.1's high-risk surfaces (unauthenticated board routes, `AUTONOMOUS_WRITE_TOOLS`). No template exists today (CONTRIBUTING.md has only a proto-version).
- **hw-support-class doc table** (SHOULD) — wire `model_fit.evaluate_model_fit` as the single source; name it "support class" (NOT "tier" — already double-booked by bench A/B/C and test α/β/γ).
- **Receipt schema fields** (SHOULD) — add hw/install-cmd identity + rollback/limits to `release-gate-report.json`.
- **Stable-patch triage 4-question tree** (SHOULD) — CONTRIBUTING.md addition; channel-count-independent, lands regardless of Decision D8.

---

### 21 Decisions for the user (scope forks)

Each below is a genuine fork requiring an explicit call — not silently absorbable.

**D1 — Cloud/hybrid routing + privileged host-agent (local-first ethos).** hal0 treats cloud providers as ordinary named upstreams (Anthropic/OpenAI/OpenRouter templates, reachability probe) but has ZERO automatic runtime failover from a local slot to a cloud upstream. Adding one is a privacy/data-locality change: a request the operator believed stayed on-LAN could silently leave to a cloud provider on local failure. Options: (a) do nothing — cloud stays manually-addressed (strictly local-first, current); (b) opt-in per-model/per-persona `fallback_model` that engages only on an explicit flag, never silently; (c) full hybrid.yaml ladder with retries/shuffle (closest to ODS, weakest fit with local-first). **Recommend (b) if adopted at all, gated off by default.** Bundled with this: the **privileged host-agent** fork — ODS proposes a separate always-on network daemon with its own bearer + two-secret split (dashboard key ≠ agent key). §7.2 already achieves "don't run as root" via a narrow in-process sudo/polkit helper, which fits the locked "one hal0 user" decision. **Recommend keeping §7.2's lighter direction, pulling only ODS's concrete hardening (per-service Lock, subprocess timeouts, 16KB body cap, default-deny bind, id-validation regex) into that helper.**

**D2 — Backend-contract artifact + metrics auth (§7.1b design + cross-lane).** (i) ODS proposes a new `config/backends/{rocm,cuda,cpu}.json`; **recommend instead widening §7.1b's `RUNNER_IMAGES` with the missing `public_api_port`/`public_health_url`/`provider_url` fields** — one-artifact-vs-two tradeoff for whoever implements §7.1b to confirm. (ii) `/api/metrics/prometheus` is explicitly documented as unauthenticated "by convention," which conflicts with ODS's "root-only/bearer" ask AND §1's LAN-auth-hardening lane. **Not resolvable within §6/§7 scope — needs coordination with the §1 owner** so the metrics-auth fix isn't split across two PRs.

**D3 — Second protocol surfaces + external repos.** (i) **Ollama-compat** (:11434, `/api/tags,/chat,/generate,/show,/ps,/embed`): hal0 only uses Ollama as an upstream it proxies TO, never a surface it exposes. Building a 2nd listening port that mimics Ollama's evolving API is real scope + ongoing drift for a benefit mostly duplicated by `/v1` (the user's own OpenWhispr/OpenWebUI already use `/v1` directly). **Recommend defer/skip unless a concrete fleet client cannot be redirected to `/v1`.** (ii) **Upstream OSS PRs into Continue/Open WebUI** conflict with the locked "work scope = hal0 only." **Recommend out-of-scope** unless the user carves an explicit exception. (iii) **`hal0-recipes` standalone repo** conflicts with the §15.5 monorepo-over-separate-repo precedent; hal0 already has `bundles/`+`tiers/`. **Recommend folding recipe JSON into in-repo `bundles/` or `installer/manifests/`** unless the user specifically wants a public community-recipe repo.

**D4 — Host-tuning blast radius (§21.1).** Applying `amd_iommu=off` + reserving up to 120 GB as amdgpu GTT + `tuned-adm accelerator-performance` at the PVE-host level is NOT scoped to halo alone: `amd_iommu=off` changes device-isolation posture for every guest on that host, and the 120 GB GTT reservation permanently removes RAM from co-located services (langfuse CT105, TrueNAS-backed PBS, etc. per memory). **Needs explicit sign-off before hal0 ships an automated host-tuning script vs a manual opt-in runbook. Recommend defaulting to a documented manual runbook (never auto-run by any installer path)** unless the user confirms this Proxmox host is dedicated enough to halo to take the system-wide hit.

**D5 — Document-RAG as a first-class capability.** hal0 has no document-ingestion/vector-store RAG today — memory is Hindsight conversational memory only (single backend, locked). Adopting the "two-half RAG (ingest async / query sync)" workflow or the `services_any: qdrant OR weaviate OR chromadb` disjunction means deciding hal0 wants a document-RAG feature at all, and on what backend(s) — a new product-scope decision, not a gap-fill. **Recommend defer unless the user actively wants document-RAG.**

**D6 — Workflow catalog / generic DAG schema.** hal0 has zero generic workflow-graph concept (only a narrow ComfyUI prompt-graph translator). ODS's node-type vocab (trigger/http/llm/rag/store/output/schedule/stt/tts/agent) turns hal0 from "local inference server + admin dashboard" into a workflow-authoring platform — real scope expansion against the appliance/single-user philosophy. **Recommend documenting concrete pipelines (voice STT→LLM→TTS, memory) as fixed named recipes instead**, unless the user actively wants a DAG authoring surface.

**D7 — AI-CI automation cluster.** None of nightly-code-review / claude-review / ai-issue-triage / autonomous-code-scanner exists in `.github/workflows/` today (`hermes-sdk-diff.yml` is an unrelated SDK-drift bot the plan wants deleted). Adopting it means the repo's FIRST scheduled/unattended Claude surface, distinct from the interactive Opus-orchestrates/Sonnet-implements model. Tradeoff: high leverage for a Claude-heavy owner vs real cost ($/run caps), blast-radius (protected-paths, secret-scan, diff-cap), and a new guardrail doc + "operate autonomously, never AskUserQuestion, Let It Crash" prompt discipline that doesn't exist today. **Needs explicit yes/no + rollout order; if yes, recommend ai-issue-triage first (labels-only, zero blast radius).** Protected-paths should reuse the existing sunset-shim/`check-sunset` CI guardrail, not a parallel allow-list.

**D8 — Five-channel release model vs locked §7.7.** hal0 ships `main` + a working `nightly` channel + tags (`nightly.yml`, `hal0.release.channel`, `manifest.json` channel field) — live code. §7.7 explicitly says "remove nightly channel" to collapse to one scheme; ODS's 5-channel model implicitly argues to keep a nightly-equivalent. **Needs one explicit re-confirmation: keep §7.7's single-channel decision (then this candidate dies beyond its patch-triage-tree sub-idea, which lands regardless per §21.15), or reopen §7.7 and keep a nightly-equivalent.** Don't let this drift silently either way.

---

### 21 Revised sequencing

Slotting the above into the existing Phases 0–4 + model-layer epic (the plan's own §230-238 sequencing). Two items are urgent/high-ROI-early and should jump the queue:

- **KB-1 auth gap (§1) — urgent.** `/v1` is open on the LAN today. This blocks D2 (metrics auth), gates §21.9 `/v1/messages` (Claude Code will want a token), and is a prerequisite the network-exposure-policy CI test (§21.11) codifies. Pull forward into Phase 0/1.
- **§21.1 Strix-Halo host tuning — urgent, highest concrete ROI.** Independent of the code epics (it's a host-provision script on the halo box), gated only by Decision D4 sign-off. Land as soon as D4 is answered — it multiplies the value of every subsequent inference change and is a one-time cost. Pair its preflight WARN checks with the §21.4 doctor rework.

Otherwise:

- **Phase 0/1 (foundations):** KB-1 auth; §21.11 config contracts (network-exposure-policy CI, ports, golden-paths — cheap guardrails that protect later work); §21.15 PR template + triage tree (process, no code risk); §21.1 host-tune script + preflight WARNs (pending D4).
- **Model-layer epic (§7.1a/b + ML-1/2/3):** §21.7 managed-args denylist (rides §7.1a flag resolution); §21.2 gfx1151 arch-guard + `backend_versions.json` + cold-JIT timeouts + `--parallel` sweep (§7.1b registry + §20 bench); §21.6 backend-contract widening + auto-select + lifecycle state; §21.5 /v1/models extensions + tokenize + extra prefixes; §21.3 introspection endpoints (after ML-1 tables).
- **Phase 3 (slots/capacity):** §21.10 multi-model memory manager folds into the P3-slots `reaper.py` extraction (GTT-aware probe = elevate); §21.13 persona `timeout_s`/concurrency gates on the extracted capacity signal.
- **Phase 4 (release/process):** §21.15 receipt-schema + support-class table + "extend not build" framing; §21.4 doctor rework + support bundle (after §21.2 gfx guard supplies a diagnosis ID and §13 tables supply evidence); close the toolDefinitions drift-check.
- **After §19 (voice):** §21.9 `WS /v1/realtime` for OpenWhispr (needs the whisper.cpp GGUF slot); voice pipeline named-recipe doc.
- **Strategic, sequence after §21.5 /v1/models:** §21.9 `POST /v1/messages` + `hal0 launch claude` (Claude Code onboarding); §21.12 connect-clients docs + `setup-cursor`/`setup-continue` (with §17 Lane E); §21.14 `hal0 chat` REPL (self-contained, any time).
- **Decisions D1/D3/D5/D6/D7/D8** are gates, not scheduled work — resolve before their dependent items enter a phase. D2 and D4 gate Phase-0/1 items above and should be answered first.