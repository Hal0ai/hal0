# hal0 — Glossary

Project terminology. Update inline as new terms get resolved during design sessions or PR reviews. NOT a spec — this is just the canonical names + short disambiguators. For decision rationale, see `docs/internal/adr/`.

---

## agent

Two distinct senses in this repo. Disambiguate by context.

1. **internal dev sense** — a Claude teammate (the multi-agent fan-out pattern used in `docs/models-slots-impl-plan.md` and `CONTRIBUTING.md:93`). Never user-facing. About *how we build hal0*, not what hal0 is.
2. **product sense** — a Phase 8 bundled agent app (`pi-coder` or `Hermes-Agent`). User-facing. About *what users do with hal0*. See ADR-0004.

When in doubt, ask which sense applies before writing the word.

## Agents subsystem

**Stripped.** Previously a haloai-style first-party agent runtime (PLAN.md §1 Strip listed it as gone). The Phase 8 product-sense agents (above) are NOT a revival — they're third-party bundled apps with a fundamentally different architecture. Do not reintroduce the first-party runtime.

## bundled agent

Phase 8 product feature. A third-party agent application installed alongside hal0, prewired to use hal0 as its local AI provider and to consume hal0's MCP servers. v0.2 supports `pi-coder` (CLI shape) and `Hermes-Agent` (service shape). Single-pick at install. See ADR-0004.

## Cognee

The embedded memory engine adopted from v0.2 (Apache 2.0). Defaults: SQLite + LanceDB + Kuzu — all embedded, no external services. Powers `/mcp/memory`. See ADR-0005.

## dataset (Cognee)

Cognee's namespace primitive. hal0's namespace rule (v0.2): default `shared` for all clients; per-client `--private` toggle promotes that client's writes to `private:<client_id>`. Multi-user revisits the rule in Phase 9 (future ADR pending — ADR-0006 was reassigned to Lemonade migration). See ADR-0005 §3.

## MCP server (hal0-exposed)

hal0 exposes two MCP servers (Phase 8, v0.2):
- `/mcp/admin` — wraps existing `/api/*` routes (slot/model/hardware/log admin). Tool catalog rule: ships iff it maps to an existing route. See ADR-0004 §4.
- `/mcp/memory` — wraps Cognee's Python API. See ADR-0005 §2.

Both reachable by any MCP-speaking client: bundled agents, Claude Code, future RAG services. Auth via existing Bearer token (ADR-0001).

## memory

Two distinct memory surfaces coexist on a hal0 box. They serve different scopes — don't displace one with the other.

- **pi-memory-md** — project-scoped markdown files in the repo. Pi-coder's native extension, kept in place by the hal0 pi-coder shim. NOT touched by hal0's memory MCP.
- **hal0 memory MCP** — cross-session, cross-agent, cross-app. Backed by Cognee. Default namespace `shared`. See ADR-0005.

## pi-coder

Bundled agent option (CLI shape). Upstream: `badlogic/pi-mono`. Minimal-by-design (4 tools: read/write/edit/bash; no native MCP, no native memory). hal0's pi shim adds `pi-mcp-adapter` (MCP routing) + leaves `pi-memory-md` in place. Track-latest upstream (NOT pinned). See ADR-0004 §3, §6.

## Hermes-Agent

Bundled agent option (service shape). User-owned upstream — grows native hal0-awareness on the Hermes side rather than via a hal0-owned shim. Runs as `hal0-agent-hermes.service`. Sidebar link-out OWUI-style in dashboard. See ADR-0004 §3, §6.

## skills

Overloaded THREE ways. Default to sense (3) in hal0 product context.

1. **Claude Code skills** — the markdown + YAML-frontmatter format Claude Code itself uses (e.g. `~/.claude/skills/`). Internal tooling for dev sessions; not a hal0 product feature.
2. **stripped haloai skills subsystem** — historical, gone (PLAN.md §1 Strip section). Do not reintroduce.
3. **hal0 platform skills** = MCP tools exposed by the admin MCP server (Phase 8). An agent calling `/mcp/admin` sees `slot_list`, `model_swap`, etc. as its "skills." This is the sense used in hal0 product copy and ADR-0004.

(Possible Phase 9+ stretch: agent-side skills in the `cognee-integrations/openclaw-skills` style — `SKILL.md` + YAML frontmatter + self-improving loop. If we ever ship that, it's a separate noun and gets its own gloss entry.)

## device

Per-slot hardware preference in v0.2. Field on `SlotConfig` replacing v0.1.x's overloaded `backend` field (which mixed providers and backends). Enum: `gpu-rocm` | `gpu-vulkan` | `cpu` | `npu`. Default for new installs: `gpu-rocm`. `LemonadeProvider` maps this to Lemonade's recipe:backend pair internally (e.g. `gpu-rocm` → `llamacpp:rocm`, `npu` → `flm:npu`).

Note: spike data showed `gpu-vulkan` is much slower than `gpu-rocm` for Strix Halo through Lemonade — likely user-facing UI should advise `gpu-rocm` as the recommended default and label `gpu-vulkan` as fallback.

## slot

A named, configured serving target — e.g. `primary`, `embed`, `stt`, `tts`, `img`, plus optional chat slots (`agent`, future others). NOT a memory or RAG primitive — slots serve inference, memory lives in `/mcp/memory`.

Two sub-senses depending on release:

1. **v0.1.x slot (current)** — `hal0-slot@.service` systemd template unit, parameterized by slot name. Owns a process + port + container under a Provider class (PLAN.md §2).
2. **v0.2 slot (with Lemonade)** — a logical mapping from slot name to ONE Lemonade-loaded model. The per-slot systemd template retires; `lemond` is the single shared process. Slot state (`ready`/`idle`/`serving`) is derived from `/v1/health.loaded[]` by model_name lookup. User-facing UX unchanged. See ADR-0006 (pending).

`SlotManager.start(slot)` in v0.2 calls Lemonade load semantics rather than starting a systemd unit. Slot identity persists in `capabilities.toml` and the user-facing surface; the runtime layer is the Lemonade pool.

### slot inventory (v0.2)

A slot has exactly ONE Lemonade `type` and ONE loaded model. **Slot identity is a bare name** (e.g., `primary`, `embed`, `rerank`, `agent`) — unique across the whole `capabilities.toml`. The `group` is a field on the slot's selection, used purely for dashboard rollup. `embed` and `rerank` are two separate slots filed under the `embed` group; same for `stt`/`tts` under `voice`.

Migration note: the v0.1.x `capabilities.toml` shape `selections.<group>.<slot>` carries the group implicitly in the TOML path. v0.2 keeps the same TOML path for back-compat but the canonical identity in code is the bare slot name.

| Slot | type (Lemonade) | UI group | Default at install |
|---|---|---|---|
| `primary` | `llm` | chat | seeded, empty model |
| `embed` | `embedding` | embed | seeded, empty |
| `rerank` | `reranking` | embed | seeded, empty |
| `stt` | `transcription` | voice | seeded, empty |
| `tts` | `tts` | voice | seeded, kokoro:cpu only in v0.2 |
| `img` | `image` | img | seeded, empty |
| `agent` | `llm` | chat | **only added when a bundled agent installs** — side-effect of Phase 8 |

The seeded slots are a **catalog**, not a stack — every selection is empty (`enabled = false`, `model = ""`) until the user picks. v0.2 does not prescribe a model stack at install.

### group

Pure UI rollup in `capabilities.toml` (`selections.<group>.<slot>`). Groups bundle related slots into one dashboard panel. v0.2 groups: `chat` (primary, agent, …), `embed` (embed, rerank), `voice` (stt, tts), `img` (img). Groups do NOT carry types or per-group state — slots do. Users adding custom slots pick which group to file under.

### user-defined slots

Beyond the seeded catalog, the user can add named slots via the dashboard (`hal0 slot add NAME --type TYPE --model MODEL`). The new slot:

- Must have a unique kebab-case `name` (not in the reserved seeded set)
- Must declare a `type` (see "slot type" below) — drives Lemonade per-type LRU and OmniRouter tool routing
- Picks a `model` from `registry.toml` OR pulls fresh via `/v1/pull` under the `user.*` namespace
- Lives in `capabilities.toml` under whichever group the user picks (or `selections.custom.<name>` if none chosen)

Removing a user-defined slot is a no-side-effect operation (`hal0 slot remove NAME`) — the underlying model stays in the registry.

## FLM trio (NPU coresident slots)

The Strix Halo NPU enforces ONE AMDXDNA hardware context per host — so only one `flm serve` process can run at a time. **But that one process can host three model roles simultaneously** via FLM's `--asr 1 --embed 1` flags: chat + transcription + embedding. Verified empirically 2026-05-22 (gemma3:1b + Whisper-V3-Turbo + Embedding-Gemma-300M loaded in one FLM process, ~2 GB NPU memory total, chat at 40 tok/s).

hal0 v0.2 leverages this by setting Lemonade's `flm.args = "--asr 1 --embed 1"` config and exposing three NPU slots in `capabilities.toml`:

| Slot | type | device | Backing |
|---|---|---|---|
| `agent` | `llm` | `npu` | the FLM trio's chat model |
| `stt-npu` | `transcription` | `npu` | the same FLM trio's `--asr` model |
| `embed-npu` | `embedding` | `npu` | the same FLM trio's `--embed` model |

**Routing fan-out.** Lemonade only knows about the chat slot; it sees `flm.args` but tracks the chat model as the WrappedServer. hal0's capability dispatcher reads `/v1/health.loaded[].backend_url` for the FLM model and routes `stt-npu`/`embed-npu` requests directly to that same port's `/v1/audio/transcriptions` and `/v1/embeddings` endpoints.

**Coresident constraint.** Loading any one of the three slots starts the FLM trio process. The other two are "available" instantly (no extra load time). Disabling a slot frees its model role at next FLM restart but otherwise the process keeps running.

**Default behavior on install.** When the FLM `.deb` is detected at install time AND a bundled agent is being installed, all three NPU slots (`agent`, `stt-npu`, `embed-npu`) default to `enabled = true` with default trio models (`gemma3:1b`, `whisper-v3-turbo`, `embed-gemma:300m`). Users without FLM installed get no NPU slots at all.

**Hard constraints (validated in capabilities.toml):**
- Only one `device = "npu", type = "llm"` slot can be `enabled = true` at a time. Selecting a different NPU chat means swapping the FLM trio's chat model (slow but supported).
- `route_to_chat` between two NPU `llm` slots is blocked — would require an FLM-process swap mid-conversation.

**Future-feature flag.** FLM's `--asr` and `--embed` are the documented v0.9.42 flags. Upstream may expose additional model roles (e.g., reranking on NPU) via similar flags later. The trio architecture extends naturally — add a fourth slot when FLM supports a fourth role.

## slot type

The discriminator that determines:
1. **Lemonade per-type LRU budget** — how many of this slot's models can be co-resident.
2. **OmniRouter tool routing** — which tools dispatch to slots of this type.
3. **Device constraints** — e.g. `npu`-device slots are exclusive at the runtime layer (Lemonade swaps on every NPU load; only one model occupies the NPU at a time).

v0.2 type vocabulary: `llm`, `embedding`, `reranking`, `transcription`, `tts`, `image`. Mirrors Lemonade's runtime types 1:1 to avoid a translation layer. UI labels are a separate concern (`Chat`/`Embed`/`Rerank`/`STT`/`TTS`/`Image`) rendered by the dashboard, not stored in config.

## OmniRouter

Client-side OpenAI tool-calling loop, owned by hal0 (not Lemonade). The LLM in a `chat`-type slot is given a JSON tool catalog; it emits `tool_calls`; hal0 dispatches each to the appropriate `/api/v1/*` endpoint and folds the result back into the conversation.

The tool set is per-bundle (a `collection.omni` manifest names which tools the LLM sees). v0.2 ships these 7 tools:

| Tool | Source | Endpoint | Target slot type | Required model labels |
|---|---|---|---|---|
| `generate_image` | upstream verbatim | `/v1/images/generations` | `image` | `image` |
| `edit_image` | upstream verbatim | `/v1/images/edits` | `image` | `edit` |
| `text_to_speech` | upstream verbatim | `/v1/audio/speech` | `tts` | `tts` |
| `transcribe_audio` | upstream verbatim | `/v1/audio/transcriptions` | `transcription` | `transcription` |
| `analyze_image` | upstream verbatim | `/v1/chat/completions` | `llm` | `vision` |
| `embed_text` | **hal0 custom** | `/v1/embeddings` | `embedding` | `embeddings` |
| `rerank_documents` | **hal0 custom** | `/v1/rerank` | `reranking` | `reranking` |

Deferred to v0.3: `route_to_chat` (LLM-driven persona swap; needs semantics ADR), `recall_memory` (depends on Cognee MCP maturity in Phase 8).

Upstream tools are kept in sync via a checksum-pinned copy of `src/app/src/renderer/utils/toolDefinitions.json` at `src/hal0/omni_router/tool_definitions.json`. hal0 custom tools live next to them in the same JSON.

**Dynamic tool filtering (per chat request).** hal0's OmniRouter client computes the active tool set at chat-start based on (a) which slots are `enabled = true` with a loadable model, AND (b) for label-gated tools like `analyze_image`, whether any enabled slot of the required type has a model with the required label. Only the active subset goes into the LLM prompt. LLMs without the `tool-calling` label receive no tools at all. Filtering re-runs at next dispatch when slot configuration changes mid-conversation.

Bundle-level tool whitelists/blacklists are NOT supported in v0.2 (YAGNI until requested). The set is always derived from slot enablement.

## model namespace (Lemonade)

Lemonade exposes three namespaces for models. hal0's policy (v0.2):

| Namespace | Lemonade source | hal0 usage |
|---|---|---|
| Registered (no prefix) | `resources/server_models.json` | hal0-curated models. Generated from `registry.toml` by `hal0 registry sync`. Requires `lemond` restart to pick up changes. |
| `user.*` | `user_models.json` | All on-demand pulls (HF coords or local file imports via the dashboard). Written via `POST /v1/pull`. No restart needed. |
| `extra.*` | `--extra-models-dir` auto-discovery | **UNUSED.** `extra_models_dir` config points at `/var/lib/hal0/models/` for compatibility but the dir contains only already-registered symlinks. Reasoning: `extra.*` cannot be deleted via API; auto-discovered models default to `["custom"]` label which broke embed/rerank in spike #1. |

Dashboard badges: `blessed` (registered) | `pulled` (user.*). No third tier.

Note: spike #2 found the spec's `extra.` prefix wasn't applied to auto-discovered files (bare names observed). Doc-only annotation — hal0 doesn't depend on the prefix because we don't use the namespace.

## default slot (per type)

Exactly one slot per type can carry `default = true` in `capabilities.toml`. This slot receives:
- All OmniRouter tool dispatches keyed on its type (e.g. `text_to_speech` → default `tts` slot)
- All unqualified `/api/v1/<endpoint>` calls that don't specify `model`
- The "Active" badge in the dashboard

Resolution rules:
1. **Type match first.** A request of type T resolves to the slot with `type = T` AND `default = true`.
2. **Label filter overlay.** OmniRouter tools may require model labels (e.g. `analyze_image` needs LLM + `vision` label). If the default LLM's model lacks the required label, fall through to any other enabled LLM slot whose model has it. Return "no compatible model" if none match.
3. **Fall-through if default disabled.** If the `default = true` slot is `enabled = false`, fall through to the first enabled slot of that type (in `capabilities.toml` declaration order). Dashboard surfaces a warning.
4. **Hard validation.** Two slots of the same type with `default = true` is a config error — refuse to save / refuse to load.

## chat persona (DO NOT use "chat-duo")

A user-facing label for "which chat slot is currently serving the dashboard chat surface". Implementation = each persona is just a chat slot (`primary`, `agent`, etc.). The UI offers a persona dropdown; the OmniRouter `route_to_chat` tool lets the LLM switch personas mid-conversation. "Chat-duo" was an early term that implied pairs — retired before it landed.

## v0.1.x → v0.2 upgrade

**Clean break, no migration script.** The v0.2 `install.sh` detects v0.1.x state (presence of `/etc/hal0/slots/*.toml` AND absence of `/var/lib/hal0/lemonade/config.json`) and refuses to install. It prints:

```
hal0 v0.1.x detected. v0.2 is a breaking change — slot architecture, model layout,
and runtime have all changed. The installer will not overwrite a v0.1.x state.

To preserve your configuration:
  sudo tar czf hal0-v0.1-backup-$(date +%F).tar.gz /etc/hal0 /var/lib/hal0/registry

To wipe v0.1.x and start fresh:
  sudo systemctl stop 'hal0-slot@*' hal0-api
  sudo systemctl disable 'hal0-slot@*' hal0-api
  sudo rm -rf /etc/hal0 /var/lib/hal0 /opt/hal0
  # then re-run this installer

Or read the v0.2 migration notes: https://hal0.dev/docs/v0.2-upgrade
```

Driver: v0.1.x audience is single-digit alpha users; migration script ROI is bad. The backup-and-wipe instruction takes 30 seconds; users who want their configs back later run `hal0 registry import hal0-v0.1-backup.tar.gz` (single command we ship in v0.2 to restore `registry.toml` only — slot selections must be redone via bundle picker).

## fresh install

v0.2 installs ship **no pre-selected model stack**. capabilities.toml lands with empty selections (`enabled = false` for every group). First-run dashboard shows a **bundle picker** — 4 hardware-anchored tiers plus the vendor-blessed LMX bundle — with a "Skip — configure manually" path to a blank dashboard.

The bundle PICKER is the user's first action; the installer never silently chooses. Driver: hal0 is a platform, not a curated bundle; opinionation is a per-tier concept, not a default-install concept.

## bundle tiers (v0.2 first-run picker)

| Tier | Min unified RAM | `chat.primary` | `chat.coder` | Aux | NPU trio |
|---|---|---|---|---|---|
| `hal0-Lite` | 16 GB | qwen3.5-0.8b | — | — | — (not shown) |
| `hal0-Default` | 32 GB | qwen3.5-9b | — | nomic-v1.5, whisper-tiny, kokoro:cpu | — (not shown) |
| `hal0-Pro` | 64 GB | Qwen3.6-27B-MTP | Qwen3-Coder-30B-A3B | + bge-reranker-v2-m3, whisper-base, sd-turbo | shown, **opt-in** |
| `hal0-Max` | 100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | Qwen3-Coder-Next-80B-A3B | + whisper-large-v3-turbo, flux-2-klein-9b | shown, **opt-in** |
| `LMX-Omni-52B-Halo` | 100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | — | Whisper-Large-v3-Turbo, kokoro-v1, Flux-2-Klein-9B | — |

Notes:
- `hal0-Max` was originally proposed as `hal0-Halo` but renamed to avoid collision with the vendor-blessed `LMX-Omni-52B-Halo`.
- The LMX bundle is shown under a "Pre-built kits" section below the tier picker, not as a tier card.
- `gpt-oss-120b` and other extreme models are intentionally excluded from bundle defaults — power users install them manually via `hal0 model pull`.
- Bundle definitions live in `/var/lib/hal0/models/collections/omni/<name>.json`. Each is a `collection.omni` Lemonade manifest plus hal0-specific slot-selection metadata.
- hal0 reads `/proc/meminfo` at install; tiers that don't fit are greyed out in the picker with a tooltip explaining why.

## two-tier scope

Access-control pattern for the admin MCP per ADR-0004. Routine ops (slot status, `model_swap`, `hardware_probe`, `memory_add`, etc.) = autonomous. Capital-D destructives (`model_pull`, `slot_delete`, `config_write`, `memory_delete` >1 record, etc.) = gated via the dashboard approval inbox. No per-agent trust toggle (destructives must always be approved).

---

# v0.3 vocabulary (added 2026-05-23)

## agent identity card

A memory item published by a bundled agent during its first-run bootstrap, recording who-it-is and how-to-reach-it. Lives in the dedicated `agents` Cognee dataset (NOT `shared`, NOT `private:*`), tagged `agent-identity`. Immutable — written once, cleaned on uninstall. Schema v1 pins required fields in `metadata`: `agent_id`, `display_name`, `namespace`, `hal0_state.{registered_at, bootstrap_version, hal0_version, hermes_version}`. The `text` field is a human-readable summary. See [ADR-0011](docs/internal/adr/0011-agent-identity-cards.md).

## agents dataset

Cognee dataset reserved for agent identity cards. Separate from `shared` (episodic memory) and `private:*` (per-agent working memory). Small forever (5-10 cards). Discoverable via `memory_search({dataset: "agents", tags: ["agent-identity"]})`. Foundation for multi-agent discovery in v0.3+.

## Hal0Profile

A hal0-owned Hermes plugin extending `providers.base.ProviderProfile`. Lives at `$HERMES_HOME/plugins/model-providers/hal0/`. Hardcodes the local Lemonade base URL, emits a hal0-distinct `User-Agent`, and is the extension point for Lemonade-specific request shaping (e.g., keep-alive injection). Packaged inside the hal0 wheel, copied into HERMES_HOME by bootstrap. v0.3.

## Hal0MemoryProvider

A hal0-owned Hermes plugin extending `agent.memory_provider.MemoryProvider`. Lives at `$HERMES_HOME/plugins/memory/hal0-memory/`. Native memory injection: implements `system_prompt_block`, `prefetch`, `sync_turn`, etc. — memory is part of the prompt, not a tool the agent has to remember to call. Talks to `hal0-memory` MCP at `/mcp/memory` over HTTP. v0.3.

## hermes_provision

The hal0 module at `src/hal0/agents/hermes_provision.py` that orchestrates the 12-phase Hermes bootstrap (preflight → install → env_probe → home_init → config_write → mcp_wire → context_link → namespace_register → model_automap → voice_wire → smoke_tests → self_report). Idempotent + checkpointed via `/var/lib/hal0/state/agents/hermes/provision.json`. CLI verb is `hal0 agent bootstrap hermes`. Renamed from `hermes_bootstrap.py` to avoid soft collision with upstream's Windows-UTF8 module of the same filename.

## HERMES_HOME (v0.3)

Pinned to `/var/lib/hal0/agents/hermes/` for hal0-bundled installs (not `~/.hermes`). Multi-agent-root-ready: pi-coder and future agents land at `/var/lib/hal0/agents/<name>/`. Wrapper `/usr/local/bin/hal0-hermes` sources `/var/lib/hal0/secrets/agents/hermes.env`, exports `HERMES_HOME`, and execs `/var/lib/hal0/venvs/hermes/bin/hermes`. Raw `/usr/local/bin/hermes` stays unwrapped so human SSH sessions get normal behavior.

## v0.3

The active milestone (2026-05-23 →). Five interlocking streams: (1) Hermes-Agent first-run bootstrap, (2) React dashboard v3 wired to live data, (3) Lemonade polish (preload+idle, GPU TTS, KV%, `/v1/*` proxy), (4) Admin/auth simplification (ADR-0001 close-out: FastAPI owns auth, Caddy collapsed to TLS-only or removed), (5) Advanced memory + MCP client side (Cognee graph + Memify + federation + per-agent external MCP allow-list). v0.3 ships when all five close. See PLAN.md §1 v0.3 + §15 Phase 10.

---

# v0.3 integration vocabulary (added 2026-05-28)

The terms below land with the v0.3 Hermes integration sweep (PRs #393–#404 + #PR-11). Pinned by [ADR-0019](docs/internal/adr/0019-v0_3-hermes-integration.md).

## composer

The browser-side input surface in the v3 dashboard's `<HermesChat>` tab. A React `<textarea>` + send button — NOT an xterm / PTY. Enter submits the current text as a `prompt.submit` JSON-RPC frame; Shift+Enter inserts a newline. Replaces the earlier "xterm-over-PTY" design (MASTER-PLAN §1 pivot #1) — the PTY route was cut after DA-sec-ops flagged it as a LAN-RCE class problem. Lives in `ui/src/dash/agents/chat/Composer.jsx`.

## transcript

The rolling chat history rendered above the composer. Built by zustand store `useTranscript` from `message.delta` + `message.complete` events streamed off `/api/agents/{id}/events`. Tool-call deltas appear as inline collapsed cards. The store dedupes by `(session_id, message_id)` so the WS reconnect path (250ms → 4s backoff, owned by `use-hermes-session.js`) doesn't duplicate frames.

## plugin host

The hal0-api surface (`/api/dashboard/plugins/*` + `/dashboard-plugins/{name}/*`) that proxies upstream Hermes plugin manifests + static assets so the v3 dashboard can mount plugin bundles (the kanban plugin in v0.3) inside an `<AgentView>` tab. Each plugin renders inside a shadow-DOM iframe with the `__HERMES_PLUGIN_SDK__` global shimmed from `src/hal0/api/plugins/sdk-shim.js`. v0.3 vendors the SDK shape; ADR-0018's drift detection alerts on upstream registry.ts changes that would break the shim. See PR-7.

## sidecar agent block

The compact agent panel rendered in the v3 dashboard sidebar — service-status chip, persona picker, memory chip, skills list, approvals bell, [Open chat] button. Lives at `ui/src/dash/agents/SidebarAgentBlock.jsx`. Parameterised by `agent_id` so v0.4 pi-coder lights up by adding a row. The service chip wires `POST /api/agents/{id}/restart` (PR-11); the memory chip reads `GET /api/agents/{id}/memory/stats` (PR-11); the skills list reads `GET /api/agents/skills` (PR-11).

## persona TOML

A file under `/var/lib/hal0/agents/hermes/personas/{id}.toml` declaring `[persona]` (`id`, `display_name`, `summary`, `system_prompt`, `tools_allowed`, `memory_namespace`, `preferred_upstream`, `preferred_model`) and `[persona.approval]` (`default_policy`, `auto_approve`, `require_approval`). The filename stem MUST match `[persona].id` — `load_persona` raises `PersonaError` on a mismatch to prevent silent renames. Seeded by `hermes_provision` Phase 8 with two personas (`hermes`, `coder`); operator-edited via `hal0 agent personas` or the dashboard persona editor. The active persona is the contents of `active.txt` next to the personas dir; switching swaps the system-prompt scope on the next turn without restart.

## hal0-cognee

The hal0-owned `MemoryProvider` plugin at `src/hal0/agents/hermes/plugins/memory_cognee/`, mounted into `$HERMES_HOME/plugins/memory/hal0-memory/` by the provisioner. Wraps hal0-memory's REST surface (`/api/memory/{add,search,list,delete}`) so memory injection happens inside Hermes's prompt pipeline (`system_prompt_block`) rather than as an explicit tool call. Per-persona namespace via persona TOML `memory_namespace`; omitted dataset on writes resolves to `private:<agent_id>` server-side per ADR-0005 §3 (#317 fix once it lands). v0.3 supersedes Hal0MemoryProvider above as the canonical name.

## hermes-sdk-diff

The weekly GitHub Action + local script (`scripts/hermes-sdk-diff.sh`) that diffs hal0's pinned Hermes upstream commit (`pyproject.toml [tool.hal0.upstream-hermes]`) against upstream HEAD for the tracked files (`web/src/plugins/registry.ts`, `web/src/plugins/slots.ts`, `hermes_cli/web_server.py`, `agent/memory_provider.py`, `tools/registry.py`, `agent/events.py`). When any tracked file changes, the workflow opens an issue with the upstream commit range so the bump is human-gated. Bump process documented in [ADR-0018](docs/internal/adr/0018-upstream-hermes-pin-and-upgrade.md) §4.

## HMAC session cookie

The browser-facing authn seam on hal0-api's chat-proxy surface (`/api/agents/{id}/{events,submit,session/*}`). Minted on first GET `/api/agents/{id}/session/handshake`, payload `{session_id, expires_at}`, signature `HMAC-SHA256(<secret>, base64url(payload))`. Secret lives at `/var/lib/hal0/agents/secret.bin` (chmod 0600, generated on first use). `HttpOnly` + `SameSite=Lax` + 8h TTL. Belongs to the chat-proxy specifically — ADR-0012 removed Bearer-token auth from the rest of the API. See `src/hal0/api/agents/_auth.py`.

## X-hal0-Agent

The HTTP header hal0-api sets on outbound hops to hermes (and that bundled agents set on inbound hops to hal0-api). Carries the agent's stable id (e.g. `hermes`, `pi-coder`). Per ADR-0012 it's the identity claim on hal0-api (NOT Bearer); per MASTER-PLAN §1 security-baseline the chat-proxy injects it, the browser never sees or sets it. The hal0-memory MCP resolver derives the per-agent private namespace from this header.

## composite hal0 upstream

A single `Upstream(name="hal0", kind="slot", url="http://127.0.0.1:8080/v1", slot_name=None)` registered automatically in the upstream registry (`src/hal0/api/__init__.py::_autoregister_slot_upstreams`). Replaces per-slot autoregistration: Lemonade serialises chat loading on a single port (R4 H2 from MASTER-PLAN), so registering one Upstream per chat slot produced duplicate entries pointing at the same URL. The composite aggregates every chat-capable slot's model id through one `/v1/models` response (5s TTL cache). Explicit `upstreams.toml` entries claiming the name `hal0` win — autoregistration is skipped when overridden.
