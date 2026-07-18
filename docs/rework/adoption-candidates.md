# hal0 Adoption Candidates — Lemonade + ODS (combined)

What to pull into the **hal0 rework**, synthesized from two independent multi-agent analyses:

- **Lemonade** (lemonade-server.ai docs) — a local OpenAI-compatible inference server. Closest peer to hal0 itself. Full detail: `/home/mint/lemonade-analysis/HAL0-FEATURES-FROM-LEMONADE.md` + `worker-1..6.md`.
- **ODS** (github.com/Osmantic/ODS) — a full local-AI *deployment/orchestration* system, **already Strix-Halo/ROCm-aware**. Full detail: `/home/mint/ods-analysis/worker-1..8.md`.

**How to read the Source column:** `L` = Lemonade, `O` = ODS, **`L+O` = both independently recommended it → highest confidence, adopt first.** Verdicts: MUST (fills a real gap) / SHOULD (valuable) / COULD (nice-to-have) / HAVE (hal0 likely already does it).

hal0 baseline assumed: self-hosted OpenAI-compatible llama.cpp/llama-server on AMD Strix Halo (gfx1151 + ROCmFP4), agent/brain router, langfuse telemetry, honcho memory, `/v1` consumed by LAN desktop clients (currently **no auth**, on `10.0.1.142:8080`).

---

## The 12 convergent priorities (do these first)

1. **Close the `/v1` auth gap.** [L+O, MUST] Default-bind `127.0.0.1`; two-tier keys (`HAL0_API_KEY` + `HAL0_ADMIN_API_KEY`); bearer on `/v1/*`, admin-gate `/internal/*`; loud warning (or refuse) when bound to `0.0.0.0` without a key. Both projects flag hal0's exact situation as the #1 risk.
2. **Strix-Halo kernel/GPU tuning.** [O, MUST] `gttsize=120000`, `amd_iommu=off` (`iommu=pt` is *not* equivalent, +2–6%), `tuned-adm profile accelerator-performance` (+5–8%), `vm.swappiness=10`, BIOS UMA min. Concrete, measured wins unique to ODS.
3. **Runtime model-management API.** [L+O, MUST] `POST /v1/pull` (SSE + background jobs), `/v1/pull/variants`, `/v1/downloads[/control]`, `/v1/load|unload|delete`, per-model `recipe_options.json`. Biggest capability gap; enables a desktop model picker.
4. **Model catalog with integrity + per-model image pin.** [L+O, MUST] `model-library.json`-style entries: `{id, name, family, gguf_file/parts[], gguf_url, gguf_sha256, size_mb, vram_required_gb, context_length, quantization, specialty/labels, llama_server_image, license}`.
5. **Labels/capability taxonomy drives routing.** [L+O, MUST] `tool-calling, vision, reasoning, mtp, embeddings, reranking, transcription, realtime-transcription, coding, hot`. Router keys off labels, not model names.
6. **Local-first routing with named cloud fallback.** [L+O, MUST] ODS `hybrid.yaml` ladder `{local:[cloud]}` + `num_retries:2`; Lemonade `<provider>.<model>` unified namespace. Single `HAL0_MODE=local|cloud|hybrid` env.
7. **Multi-model memory manager.** [L, MUST] Per-type LRU `max_loaded_models`, `auto_evict` on VRAM pressure (poll `rocm-smi`/sysfs), two-stage idle degradation (clear KV → unload weights), pinning, PGID-kill on child timeout (VRAM-leak prevention).
8. **Introspection endpoints.** [L+O, MUST] `/v1/health` (full loaded-model detail), `/v1/stats` (TTFT/tok-s), `/v1/system-stats` (gpu%/vram), `/v1/system-info` (hw + backend install state), `/metrics` (Prometheus, root-only), `/live`.
9. **`hal0 doctor` + support bundle.** [O, MUST] Stable diagnosis IDs, evidence+next_steps, exit 0/1, `--json`/`--report`; redacted support bundle with command-status TSV. hal0 has neither today.
10. **Config system + contracts.** [L+O, MUST] Single `.env`/config.json, `hal0 config get/set` dot-notation, `/internal/set` (server-level vs deferred hot-reload split), and CI-enforced generated-config contracts.
11. **Backend versioning + gfx-arch guard.** [L+O, MUST] Pin llama.cpp build per model/`backend_versions.json`; **refuse to start if `system_info` HIP archs don't include gfx1151** (garbage-output failure mode); `rocm_channel` stable/nightly.
12. **Adopt the ODS AI-CI automation.** [O, SHOULD] nightly-code-review, claude-review (PR-time), ai-issue-triage, autonomous-code-scanner — with protected-paths + fork-skip + secret-scan + diff-cap + prompt-injection guards. High ROU for a Claude-Code-heavy owner.

---

## 1. Hardening & Auth  *(urgent — `/v1` is open on the LAN today)*

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Default bind `127.0.0.1`; LAN only via opt-in | L+O | MUST | Root cause of today's exposure. `${BIND_ADDRESS:-127.0.0.1}`. |
| Two-tier keys `HAL0_API_KEY` + `HAL0_ADMIN_API_KEY` (admin also gates `/internal/*`, defaults to regular) | L | MUST | Constant-time compare (`hmac.compare_digest`). |
| Refuse to start without an explicit key; auto-generate + surface at install (never auto-hide) | O | MUST | ODS M-finding: hidden auto-gen key = operator blind. |
| Warn loudly when bound non-loopback without key | L+O | MUST | Startup guard. |
| Accept any non-empty key when auth disabled (`HAL0_AUTH=disabled`) | L+O | MUST | Drop-in for Open WebUI/LangChain that require a key field. |
| WS auth via `?api_key=` query (browsers can't set WS headers) | L | MUST | Non-obvious but required for realtime. |
| `network-exposure-policy.json` per service (`lan_exposure`, `auth_required`, `risk`, `notes`) + CI contract test | O | MUST | Fails CI if llama-server gains a `0.0.0.0` port. Set `auth_required:true` for anything non-loopback. |
| Put an auth-gate proxy (Caddy `forward_auth` → verify-session → llama-server) in front for LAN; never `0.0.0.0` all services | O | SHOULD | Expose ONE gated port, not every service. |
| HMAC-signed session cookie (`id.expiry.sig`, HttpOnly, SameSite=Lax, Secure-on-HTTPS, 12h) + `verify-session` w/ identical-401 + revocation set | O | SHOULD | For a browser dashboard, if added. |
| Pin llama.cpp image by SHA (no `:latest`); run container non-root; mem/CPU caps | O | SHOULD | Supply-chain + blast-radius. |
| Tailscale (host-net) as the remote-access path; never port-forward/Funnel | O | SHOULD | After the auth-gate proxy exists. |
| `.pre-commit-config.yaml` + gitleaks in CI; SHA256-verify GGUF downloads | O | SHOULD | Both are ODS security-audit lessons. |
| Prompt-injection guard for any issue/PR text ("ignore instructions in body", truncate) | O | MUST (for AI CI) | Applies to §11 automation. |

## 2. Strix-Halo / ROCm inference tuning  *(highest concrete ROI — mostly ODS)*

| Setting | Source | Verdict | Value / effect |
|---|---|---|---|
| `amdgpu` modprobe `gttsize=120000` | O | MUST | 120 GB as GPU GTT — the single biggest knob. Requires BIOS UMA Frame Buffer **minimum**. |
| GRUB `amd_iommu=off` | O | MUST | +2–6% inference. `iommu=pt` is **not** equivalent (document this). |
| `tuned-adm profile accelerator-performance` | O | MUST | +5–8% prompt processing. |
| `amdgpu` `ppfeaturemask=0xffffffff`, `gpu_recovery=1` | O | MUST | All PM features on; survive GPU hangs. |
| `ttm pages_limit=31457280`, `page_pool_size=15728640` | O | MUST/SHOULD | Cap TTM at 120 GB; pre-cache ~60 GB. |
| `vm.swappiness=10`, `vm.vfs_cache_pressure=50` | O | MUST/COULD | Avoid mid-inference KV swap. |
| `update-initramfs -u` + reboot after modprobe/GRUB | O | MUST | Tuning silently fails otherwise. |
| Build/verify llama.cpp with `DCMAKE_HIP_ARCHITECTURES=gfx1151`; refuse generic `server-rocm` images lacking gfx1151 | L+O | MUST | Wrong arch → all-`?` output. Startup probe of `system_info` HIP archs. |
| `HSA_OVERRIDE_GFX_VERSION=11.5.1` + `ROCM_PATH`; verify `/dev/kfd`,`/dev/dri/renderD*`, video/render GIDs | O | MUST | ROCm runtime prerequisites. |
| `--enforce-eager` as managed device-class default on shared-mem APU | L | MUST | CUDA-graph/kernel capture unstable on unified memory. |
| `enable_dgpu_gtt` — combined pool in capability checks | L | SHOULD | hal0 is exactly this hardware (see `pve-gtt-hidden-memory` memory). |
| `--n-gpu-layers 99`; default ctx tight (8–16K), 32K on demand; per-model `n-ctx` in `models.ini` w/ `load-on-startup` | L+O | MUST/SHOULD | Tight ctx leaves room for parallel slots; warmup before first request. |
| Persist ROCm kernel cache; expect ~12-min FP4/ROCmFP4 cold JIT → generous `wait_for_ready` (~15–20 min) | L | SHOULD | "compiling kernels" log line. |
| `LLAMA_PARALLEL`/`--parallel` tier-scaled (Strix Halo → 8–12) | O | MUST | KV pre-alloc ≈ parallel × ctx. |
| `ROCM_PATH`→`rocm-sdk path`→`/opt/rocm` host-ROCm reuse order; `rocm_channel` stable/nightly + `rocm_bin` pin | L | SHOULD | Reproducible ROCm builds; reuse host runtime. |

## 3. Runtime model management

| Feature | Source | Verdict | Note |
|---|---|---|---|
| `POST /v1/pull` — SSE progress `{file,bytes,percent}` + `complete`/`error`; raw-HF pull via `user.X` namespace | L | MUST | Registry-bypass for arbitrary HF models. |
| `POST /v1/pull` background job (survives UI reload) | L | MUST | Desktop clients reload and lose SSE. |
| `GET /v1/pull/variants?checkpoint=` — enumerate GGUF quants, top-5 by popularity, mmproj | L | MUST | Auto-populates installer form. |
| `GET /v1/downloads` + `POST /v1/downloads/control {pause,cancel,remove}` | L | MUST | Download manager UI. |
| Host-agent `/v1/model/{list,status,download[/cancel],activate,delete}` w/ catalog-gated URLs, SHA256, progress file, atomic `.env` swap + backup | O | MUST | ODS's proven host-side implementation of the same surface. |
| `POST /v1/load` `{pinned, save_options, ctx_size, llamacpp_backend, llamacpp_args}`; blocked-arg list; precedence request→recipe_options→env→default | L | MUST | Explicit preload w/ "loading…" UX; ROCmFP4 backend select. |
| `POST /v1/unload` (specific or all); `POST /v1/delete` | L | MUST | Router-driven eviction. |
| Per-model `recipe_options.json` keyed by canonical id (`user./extra./builtin.`) | L | MUST | ctx/backend/args overrides without forking config. |
| `extra_models_dir` — drop-in dir scanned for GGUF, `extra.` namespace | L | MUST | Users drop ROCmFP4 quants in `/mnt/models`. |
| Multi-shard `gguf_parts[]` support | O | MUST | Any model >~50 GB. |
| Bootstrap fast-start model → background swap to full | L+O | SHOULD | Instant first-boot; hot-swap when download done. |
| `hal0 model apply` one-shot (.env + own config + reseed downstream + restart) | O | SHOULD | Model swap must never nuke user data. |
| Model bundles/collections (`collection.omni` analogue) as one picker entry | L | SHOULD | "coding rig" / "vision rig". |
| `--source huggingface\|modelscope` (+ `HF_ENDPOINT`) | L | SHOULD | Mirror/proxy support. |

## 4. Multi-model memory & routing

| Feature | Source | Verdict | Note |
|---|---|---|---|
| `max_loaded_models` per model-type LRU | L | MUST | Per-type slot budgets. |
| `auto_evict` + `auto_evict_threshold_pct` (poll rocm-smi/sysfs) | L | MUST | Share GPU with ComfyUI/Blender. |
| Two-stage idle degradation: `downsize_idle_timeout=60s` (clear KV) → `evict_idle_timeout=300s` (unload) | L | MUST | Restore transparently on next request. |
| Model pinning (`--pinned`/`/internal/pin`); `409 slots_pinned_error`; pinned skip eviction | L | MUST | Keep planner resident. |
| `eviction_score = idle/(load×weight)`; `evict_weight_factor` | L | SHOULD | Protect slow experts. |
| Process-group kill on child timeout | L | MUST | VRAM-leak prevention. |
| Provider `EngineAdapter` boundary: host/container URL normalization, bearer, capability probes, 4-state error (unreachable/auth/model-missing/unsupported) | L+O | MUST | One adapter, not scattered `requests.post`. `/healthz` distinguishes "down" vs "model warming". |
| Fallback ladder `{local:[cloud]}`, `num_retries:2`, `simple-shuffle`; pin `default`→local even in hybrid | O | MUST | hal0's killer feature. |
| Unified `<provider>.<model>` namespace across local+cloud in `/v1/models` | L | SHOULD | Routing invisible to client. |
| `drop_params:true`, `master_key` from env, per-mode required-key list (fail-fast) | O | MUST/SHOULD | LiteLLM-compat intermediary. |
| Per-provider path normalization (`/v1` vs `/api/v1`), `extra.<GGUF>` id twist, `enable_thinking:false` default, 900s timeout | L+O | MUST | Don't assume friendly-name/`/v1`; strip Qwen3 thinking for tool planners. |

## 5. Config system & contracts

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Single config file (`.env`/config.json), `HF_HUB_CACHE`-aware `models_dir` | L+O | MUST | One source of truth. |
| `hal0 config get/set key=value` dot-notation, applied live + persisted | L | SHOULD | `llamacpp.backend=rocm`. |
| `/internal/set` server-level (immediate) vs deferred (next load) split; `/internal/config[/defaults]` | L | MUST | Hot-reload safe keys without dropping the model; "reset to factory". |
| 5-tier setting precedence: request → recipe_options → arch_defaults → env/startup → hardcoded | L | MUST | Deterministic overrides. |
| Ports contract `{env_var, default, service, internal_port}` + core-service-id allowlist | O | MUST | One port truth across CLI/dashboard/health. |
| `generated-config-contracts.json` — every writer path + CI-asserted invariants | O | MUST | Config drift breaks CI, not prod. |
| Pure config renderers (`RenderInputs`→per-surface fn, dry-run-able) instead of scattered heredocs | O | MUST | Eliminates writer duplication. |
| `golden-paths.json` release-gate scenarios (host-strix-halo-rocm, container-rocmfp4, cpu, cloud) w/ 127.0.0.1 health URLs | O | MUST | Pristine release gate. |
| Semver-ordered migrations w/ pre-migration backup; schema-driven `validate-env` | O | MUST | Upgrade path. |
| `schema_version: hal0.services.v1` gate + `compatibility.min/max` on all manifests | O | MUST | Reject unknown schema loudly. |
| `.disabled` file-rename as the universal service enable/disable primitive | O | SHOULD | `hal0 enable honcho`. |

## 6. Observability (health / stats / telemetry / diagnostics)

| Feature | Source | Verdict | Note |
|---|---|---|---|
| `/v1/health` per-model `{checkpoint,last_use,type,device,pinned,recipe,pid,recipe_options,backend_url}` | L | MUST | Router introspection + "Loaded Models" panel. |
| `/v1/stats` (TTFT, tok/s), `/v1/system-stats` (cpu%, mem, gpu%, vram, null-when-N/A) | L | MUST | Status bar + langfuse spans. |
| `/v1/system-info` — hw enum + `recipes.<r>.backends.<b>.{devices,state,action}` | L | MUST | Setup wizard detects ROCm/driver, offers install action. |
| `/metrics` (Prometheus, root-only, bearer), `/live` (root-only) | L | MUST | Scraping + k8s/systemd probes. |
| `WS /logs/stream` (snapshot + live, resume via seq) | L | SHOULD | Real-time log viewer. |
| `hal0 doctor [--json] [--report]` — stable IDs (`HAL0-GFX-TARGET-UNSUPPORTED`, `HAL0-ROCM-LIB-MISSING`, `HAL0-MODEL-FILE-MISSING`…), `_diagnosis(id,severity,confidence,evidence[],next_steps[])`, exit 0/1, autofix hints | O | MUST | hal0 has none today. |
| Support bundle: redaction (KEY/TOKEN/Bearer), command-status TSV, `system/config/diagnostics/logs/manifest` layout, best-effort | O | MUST | With ROCm captures (`rocm-smi --showall`, `rocminfo`). |
| Capability profile artifact (detect→classify→merge→emit) written before any decision | O | MUST | Strict schema, reject unknown fields; add `gfx_target`, `rocm_version`, `rocmfp4_supported`. |
| Telemetry: emit **both** `openinference.*` and `gen_ai.*` in one OTLP payload | L | MUST | langfuse reads OpenInference natively. |
| Redaction toggles `hide_inputs/outputs/thinking` (keep metadata, blank text) | L | SHOULD | Think tokens are huge + sensitive. |
| Server-computed TTFT & tok/s at router layer; runtime `telemetry on/off`; `/internal/telemetry/flush` | L | SHOULD | Don't rely on backend metrics. |
| Reasoning normalization: canonicalize `</think>` variants before routing | L | MUST | Downstream parsing. |

## 7. Backend/engine abstraction & versioning

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Backend contract JSON per device (`{id, llm_engine, public_api_port, public_health_url, provider_url, container_image}`) | L+O | MUST | `config/backends/{rocm,cuda,cpu}.json`. |
| `recipe:backend` colon selector (`hal0 backend llamacpp:rocm`) | L | SHOULD | One CLI covers all. |
| `backend_versions.json` + `rocm_arch_overrides` (append `-{gfx_target}` at install); digest-pinned, not tag | L+O | MUST | Reproducible builds; startup gfx-arch guard. |
| Backend lifecycle state `installed/update_available/update_required/installable` in `/v1/system-info` | L | SHOULD | Setup wizard. |
| Managed-args list per recipe (reject `--model/--ctx-size/--host/--port/-ngl` from user args) | L | MUST | User args can't break the OpenAI shim. |
| Model-family args config (`{families, models, checkpoint_regex, enable_regex_match}`, 3-layer precedence) | L | SHOULD | Per-model chat-template/tool-parser/sampler defaults. |
| Auto-select: installed-on-disk beats preference-order; `prefer_system` escape hatch | L | MUST | Don't auto-download 2 GB when a backend is already installed. |
| `backend_url` in `/v1/health` to expose child llama-server `/metrics`/`/props` (not proxied) | L | SHOULD | Clean OpenAI surface + scrapable. |
| Async job API (submit/poll/fetch) if image/audio ever added | L | COULD | Off hal0's current axis. |

## 8. Control plane — CLI, host-agent, dashboard, discovery

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Privileged host-agent (ThreadingHTTPServer, bearer, per-service Lock, 16 KB body cap, subprocess timeouts, default-deny bind) | O | MUST | Docker-free daemon to manage llama-server/ROCm/models. Copy scaffold. |
| Two-secret split (dashboard key ≠ agent key) | O | SHOULD | Independent rotation. |
| `hal0` CLI thin dispatcher + `lib/hal0-{doctor,model,config,agent}.sh`; verbs `status[/-json], start, stop, restart, model, config, logs, doctor, agent, chat, benchmark, version` + bash completion | O | MUST | 3-level completion; alias `hal0-x`↔`x`. |
| `hal0 agent start\|stop\|status\|restart\|logs` (systemd/launchd/nohup detect) + PID file | O | MUST | Lifecycle. |
| `hal0 bench` — TTFT+TPS across backends/ctx, default scenarios chat/coding/long-ctx/embed, JSON + `--compare`, needle `context{filler,target,position}` | L | SHOULD | Benchmark harness. |
| Terminal chat REPL: `/think on/off/default`, `--no-stream`, reasoning rendered separately + stripped from history | L | SHOULD | SSH/headless use; prevents ctx bloat. |
| mDNS announcer (`hal0.local`, zeroconf, 30s poll, LAN-reachability gate = no SRV on loopback, config-signature re-announce) | L+O | MUST/SHOULD | Lemonade uses UDP broadcast; ODS uses zeroconf. Advertise only when LAN-bound. |
| Dashboard (if added): FastAPI per-concern routers + Vite `/api` proxy dev workflow; `/readiness`, `/api/gpu/detailed`, `/api/models*` | O | SHOULD | Bypass IPC for `/health`,`/system-stats`. |
| Hardcoded fallback "do-not-destroy" service set (llama-server, langfuse, honcho) | O | MUST | Prevents fail-open when config missing. |
| `service_id`/`model_id` validation regex; loopback-only `GET /host/port` probe | O | MUST | No path escape; no network-scanner abuse. |

## 9. API-surface parity

| Feature | Source | Verdict | Note |
|---|---|---|---|
| `/v1/models` extensions (`recipe, checkpoint, labels, max_context_window, downloaded`) + `?show_all=true`; **text models only** (hide image) | L+O | MUST | Router needs backend-per-model; prevents mis-routing to non-text. |
| `POST /v1/messages` (Anthropic) + `hal0 launch claude` | L+O | MUST | Unlocks Claude Code / Anthropic-SDK directly; one command sets base URL/key/model. |
| Ollama-compat surface (`/api/tags,/api/chat,/api/generate,/api/show,/api/ps,/api/embed`) on `:11434` | L | SHOULD | Open WebUI & many UIs hardcode these. |
| `POST /v1/reranking` (→ llama.cpp `/v1/rerank`) | L | MUST | RAG stacks need it; trivial proxy. |
| `POST /v1/tokenize` (+ `/detokenize`) | L | SHOULD | Router token-counting. |
| `WS /v1/realtime` (OpenAI Realtime, 16k PCM16, VAD config, manual commit) | L | MUST | **Streaming STT for OpenWhispr** instead of per-chunk. |
| `/v1/embeddings` (OpenAI-shaped, `encoding_format`) — document `check_embedding_ctx_length=False` for LangChain | L+O | HAVE/MUST | RAG + LangChain friction. |
| Multiple path prefixes (`/v0,/api/v0,/api/v1`); `/v1/audio/transcriptions` | L | SHOULD | Ollama-detect clients hardcode `/api/*`. |

## 10. Agent/profile & workflow model  *(ODS — architecturally adjacent, high-value for the brain router)*

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Agent profile YAML (`id, model, fallback_model, system_prompt, tools, tool_config, routing_rules, validation, notes`; kebab filename = id) | O | MUST | Add `langfuse_session`/`honcho_peer`. `schema_version: hal0.profiles.v1`. |
| Local-first `model` + named `fallback_model` per profile | O | MUST | llama-server first, Kimi/Claude fallback. |
| Tools allowlist + `safe_commands`/`dangerous_commands` for `exec` | O | SHOULD | Reject before forwarding to sandbox. |
| Regex `routing_rules` (pattern → local\|fallback) pre-classifier | O | SHOULD | Cheap before model-based routing. |
| Workflow catalog schema (`id,name,category,dependencies[],diagram{nodes,edges},featured`) + node-type vocab (`trigger,http,llm,rag,store,output,schedule,stt,tts,agent`) | O | MUST | Ship wired DAGs, not stub JSON. |
| Capability/feature catalog w/ `requirements.services_any` disjunction + `vram_gb`/`disk_gb` gates | O | MUST | RAG works if qdrant OR weaviate OR chromadb; hide profiles by tier. |
| `POST /run-agent` → token → SSE tool events → final (OmniRouter/OpenClaw-trigger shape) | L+O | MUST | Server-side tool loop; any OpenAI client gets it. |
| Voice pipeline family (STT→LLM→TTS) + two-half RAG (ingest async / query sync) as canonical workflows | O | SHOULD | Reuse OpenWhispr + honcho. |
| `toolDefinitions.json` single source shared UI↔server (OmniRouter) | L | SHOULD | No drift between offered and routed tools. |
| VRAM-scaled sub-agent concurrency + per-profile timeout | O | SHOULD | Strix Halo 128 GB → generous. |

## 11. Ops discipline & AI-CI automation  *(ODS — mature, worth copying wholesale)*

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Layered validation (unit → clean-install container → systemd VM → real Strix-Halo hardware gate) | O | MUST | Even single-stack needs >1 layer. |
| Capability-deferral state machine (bootstrap-active / downloading / downloaded-not-served / served-passed / served-failed) | O | MUST | Never mark "validated" with open deferrals; pass-or-degrade, never fail-blunt. |
| Validation receipt template (ref, commit, hw, install cmd, services, model, gates passed/skipped/deferred, limits) | O | MUST | Required output of every release. |
| Five-channel release model (`main`, `release/0.1.x`, tags, commit-pins, forks) + stable-patch triage 4-question tree | O | MUST | Into `CONTRIBUTING.md`. |
| Support tiers A/B/C + GPU tier map (hw→model→VRAM→backend) as pure code function | O | MUST | Doc reflects code truth; drift = validator. |
| High-risk change map + PR template (risk grade, changed surface, validation, rollback) | O | MUST | Auto-collected checkboxes. |
| Digest-pinned image refs + `KNOWN-GOOD-VERSIONS.md` (llama.cpp + model hash + ROCm + driver) | L+O | MUST | Never silently bump. |
| **nightly-code-review** workflow (Claude scans last N commits → draft PR; protected-file revert, secret scan, diff-cap, restricted tools) | O | SHOULD | Biggest single time-saver; `workflow_dispatch` first month. |
| **claude-review** (PR-time review always-on; opt-in `ai-fix` label; high-stakes detect; fork-skip; size gate) | O | SHOULD | Highest-value for Claude-Code owner. |
| **ai-issue-triage** (labels-only, advisory, sparse-checkout context) | O | SHOULD | ~$1.50/issue, zero blast radius. |
| **autonomous-code-scanner** (Ruff/Bandit/type-hints/docstrings, `$100/run` cost-tracker gate, draft PR per category) | O | SHOULD | Cost gate is the safety net. |
| AI-workflow guardrails doc: workflow classes, protected-paths allowlist (single source), "AI output ≠ release evidence" | O | MUST | Given Claude-Code-as-owner pattern. |
| Prompt: "operate autonomously, never AskUserQuestion, no defensive checks (Let It Crash), prefer no change over wrong change" | O | MUST | Prevents agent overreach. |

## 12. Client / integration friction

| Feature | Source | Verdict | Note |
|---|---|---|---|
| Document "just works" base-URL for LangChain/Continue/Cursor/Open WebUI/n8n; two URL conventions (host vs container) | L+O | MUST | OpenAI-compat is THE surface; no custom SDK. |
| First-class provider PRs into Continue / Open WebUI dropdowns | L | SHOULD | Highest strategic drop-in win. |
| Stable model-name strings usable in CLI-pull + `/v1/models` + client `model=` | L+O | MUST | Never break names; suffix quant/device (`Qwen3-4B-ROCmFP4-Strix`). |
| `hal0 setup-cursor`/`setup-continue` one-liner config writers | O | SHOULD | Writes the 8-line JSON. |
| Recipe/bundle registry repo (`hal0-recipes`, one JSON per preset) | L+O | COULD | One-line first-run. |
| Troubleshooting table per integration guide | L | SHOULD | Cheap, high-leverage. |
| Offline mode: bundle default LLM + embedding GGUF, drop telemetry/update pings, `--offline` flag, local-RAG web-search fallback | O | SHOULD | Air-gapped installs. |

---

## Explicitly out of scope (both analyses agree)
Image gen / TTS / music / 3D endpoints; vLLM-specific config (hal0 is GGUF/ROCmFP4); NPU-exclusivity (unified-memory APU); Docker-compose profiles (ODS deleted them — use manifest enable/disable); AP-mode captive portal / Wi-Fi mgmt (hal0 is a server, not an appliance); OAuth provider registry; multi-distro/Ventoy matrix (until v0.2); magic-link QR (unless multi-user). The one cloud-offload bit worth keeping if hybrid routing lands: env-over-runtime key precedence + `409 auth_conflict`.

## Suggested sequencing
1. **Harden** (§1) + **config/contracts foundation** (§5) + **Strix-Halo tuning** (§2) — parallel, unblock everything, close the LAN risk, bank the free perf.
2. **Model lifecycle** (§3) + **catalog** (§4-part) + **memory manager** (§4).
3. **Labels + router/adapter + fallback** (§4, §7).
4. **Observability** (§6) — health/stats/system-info/doctor/support-bundle/telemetry.
5. **Control plane** (§8) — host-agent, CLI, doctor, mDNS.
6. **Client reach** (§9, §12) — `/v1/messages`+`launch claude`, Ollama-compat, `/v1/realtime` for OpenWhispr, provider PRs.
7. **Agent/profile + workflow model** (§10) as the brain router matures.
8. **Ops discipline + AI-CI** (§11) — can start anytime; nightly-review + PR-review pay off immediately.

*Sources of record:* `/home/mint/lemonade-analysis/` and `/home/mint/ods-analysis/` (per-worker tables with exact file/line/flag citations).
