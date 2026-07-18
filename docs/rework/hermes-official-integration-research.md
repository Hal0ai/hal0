# Hermes Agent official integration research for the hal0 rework

**Research date:** 2026-07-18

**Official upstream checked:** `NousResearch/hermes-agent` `main` at [`7fd419e`](https://github.com/NousResearch/hermes-agent/tree/7fd419e5e6a0ac53f934a69226262c41ba130a2c) (fetched 2026-07-18).

**Scope:** plugin architecture and lifecycle, providers, memory, MCP/tools/skills, gateway/platforms, voice, observability, sessions/context, security/config, and dashboard extensions. Primary sources are upstream documentation and source. Recommendations are explicitly labelled; an upstream capability is not evidence that hal0's pinned Hermes contains it.

## Supported compatibility freeze

hal0 supports the official Hermes tag [`v2026.7.7.2`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.7.2), version `0.18.2`, released 2026-07-07. The reviewed annotated tag resolves to immutable commit [`9de9c25f620ff7f1ce0fd5457d596052d5159596`](https://github.com/NousResearch/hermes-agent/tree/9de9c25f620ff7f1ce0fd5457d596052d5159596) (tag object `b7751df34688835a108e0d630f3495fc11f3df79`). The installer pins that commit rather than a moving branch or version range.

The compatibility review confirmed the three contracts needed by the first hal0 adapters:

- [`ProviderProfile`](https://github.com/NousResearch/hermes-agent/blob/9de9c25f620ff7f1ce0fd5457d596052d5159596/providers/base.py) defaults `api_mode` to `chat_completions`; the official provider guide says OpenAI-compatible providers should use that mode.
- [`MemoryProvider`](https://github.com/NousResearch/hermes-agent/blob/9de9c25f620ff7f1ce0fd5457d596052d5159596/agent/memory_provider.py) exposes `system_prompt_block(self)`, `prefetch(self, query, *, session_id="")`, and `sync_turn(self, user_content, assistant_content, *, session_id="", messages=None)`.
- [`PluginContext`](https://github.com/NousResearch/hermes-agent/blob/9de9c25f620ff7f1ce0fd5457d596052d5159596/hermes_cli/plugins.py) exposes `register_tts_provider(self, provider)` and `register_transcription_provider(self, provider)`. These Python hooks are selected for hal0 voice because the planned adapter needs runtime slot routing; command providers remain the official recommendation when a single static command is sufficient.

Minimal MIT-licensed signature fixtures are copied under `tests/fixtures/hermes/contracts/`. They are intentionally test-only: importing hal0 core remains independent of Hermes. Any upstream pin change must update these fixtures and pass the compatibility test before downstream adapter work continues.

## Executive findings

1. **Do not design one giant “hal0 plugin.”** Official Hermes has deliberately separate extension systems for general tools/hooks/commands/skills, LLM providers, memory providers, context engines, gateway platforms, image/video generation, TTS/STT, MCP, gateway hooks, shell hooks, dashboard plugins, dashboard authentication, and observability. The upstream map explicitly directs each use case to a different surface ([plugin guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#pluggable-interfaces--where-to-go-for-each)).
2. **hal0 is pinned to a materially older fork and contract.** `pyproject.toml` currently records `earendil-works/hermes-agent@0554ef1` dated 2026-05-28, while the official repository is `NousResearch/hermes-agent` and the researched upstream `main` was `7fd419e`. Therefore every item below needs an upstream reconciliation phase that selects a reviewed release/tag or commit. The researched `main` commit is evidence, not an automatic production pin.
3. **The existing `hal0-memory` idea remains architecturally correct, but its loader contract must be retested.** Official Hermes now has a formal `MemoryProvider` lifecycle, a one-active-external-provider rule, setup schema, profile/session identity inputs, per-turn prefetch/sync, compression and delegation hooks, tools, and plugin CLI discovery ([ABC source](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/agent/memory_provider.py), [authoring guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/memory-provider-plugin.md)). hal0 intentionally maintains an importable source copy and an installable hyphenated seed; that packaging choice is separate from the runtime discovery defect. The installed destination must use the official `plugins/memory/hal0-memory/` layout.
4. **A hal0 LLM provider is now useful, despite the older rework removing it.** The current provider profile is not merely a hard-coded base URL: it participates in auth resolution, model discovery, doctor/setup, transport mode, runtime fallback, auxiliary-model routing, model transformation, and provider-specific headers ([provider plugin guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/model-provider-plugin.md), [runtime resolution](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/provider-runtime.md)). The plugin should discover hal0 `/v1/models`, never embed a fixed port.
5. **Use Hermes's native dashboard/plugin APIs instead of maintaining a speculative SDK shim where possible.** Upstream supports manifest-declared tabs, shell and page slots, built-in page override/augmentation, a browser SDK, per-plugin backend routes, discovery/rescan endpoints, CSS, and themes ([dashboard extension guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/extending-the-dashboard.md)).
6. **Security boundary:** a Python plugin executes in the Hermes process and can read the same files and in-memory credentials as Hermes. Project-local plugins are disabled unless explicitly allowed; general/user plugins are opt-in; non-loopback dashboard serving is auth-gated and fails closed without a provider ([security policy](https://github.com/NousResearch/hermes-agent/security), [plugin opt-in rules](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#plugins-are-opt-in-with-a-few-exceptions), [dashboard security](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/web-dashboard.md#authentication-gated-mode)).

## 1. Verified upstream extension architecture

### General plugin contract

A general plugin is a directory containing `plugin.yaml` and Python code with `register(ctx)`. The manifest can declare name, version, description, author, tools, hooks, and required environment variables. `register()` is called once at startup; a registration crash disables that plugin rather than Hermes ([plugin authoring guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/plugins/index.md)).

The supported context includes:

- `register_tool`, `register_hook`, slash-command and CLI-command registration, `dispatch_tool`, `inject_message`, and namespaced bundled skills;
- platform, image/video, context-engine, TTS and transcription provider registration; and
- `ctx.llm.complete()` / `complete_structured()` for a plugin-owned one-shot call that borrows the active model and credentials ([capability table](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#what-plugins-can-do), [LLM access guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/plugin-llm-access.md)).

Discovery sources are bundled, `$HERMES_HOME/plugins`, project `.hermes/plugins` (trust-gated), Python `hermes_agent.plugins` entry points, and Nix declarations. Later sources override earlier name collisions. Specialized subdirectories route to their own loaders ([discovery table](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#plugin-discovery)).

General and user-installed backend plugins default to disabled and are allow-listed with `plugins.enabled`. Bundled specialized backends are discovered for config selection; exactly one memory provider and one context engine are active, whereas multiple model profiles register and a request/config selects one ([opt-in semantics](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#plugins-are-opt-in-with-a-few-exceptions)).

The official lifecycle CLI is `hermes plugins list|install|update|remove|enable|disable`; Git installs prompt before enabling, with explicit `--enable`/`--no-enable` for automation ([management commands](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#managing-plugins)). Standalone third-party-product integrations are official policy: install into `$HERMES_HOME/plugins` or distribute as a pip entry point rather than adding vendor coupling to core ([contribution policy](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/AGENTS.md#adding-new-tools)). This is the appropriate distribution model for hal0-owned Hermes integrations.

### Hooks and interception

Verified plugin hooks include pre/post tool and LLM calls; session start/end/finalize/reset; subagent start/stop; pre-gateway dispatch; approval request/response; and transforms for tool results, terminal output, and LLM output ([hook reference](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/hooks.md#plugin-hooks)). Separate gateway event hooks use `HOOK.yaml` plus `handler.py`; shell hooks are config-driven, use a JSON wire protocol, and require consent because they execute arbitrary commands ([gateway hooks](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/hooks.md#gateway-event-hooks), [shell-hook security](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/hooks.md#security)).

## 2. Proposed hal0 plugin suite

The following is the recommended full suite. “Verified seam” means upstream exposes it; “proposal” is hal0 product design.

### A. `hal0-provider` — model/inference provider plugin

**Verified seam.** A model provider lives under `$HERMES_HOME/plugins/model-providers/<name>/`, registers a `ProviderProfile`, can override a bundled profile by name, supports OpenAI-compatible, Anthropic Messages, Codex Responses, or Bedrock transports, and can supply model listing/normalization, auth, headers, base URL, and setup/doctor integration ([provider guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/model-provider-plugin.md)). Runtime resolution has explicit precedence and supports auxiliary-model routing and fallback chains ([runtime guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/provider-runtime.md)).

**Proposal.** Restore a provider plugin, but make it a discovery adapter:

- resolve base URL from `HAL0_API_URL`, config, or a loopback discovery endpoint; never hard-code `:8080`;
- fetch `/v1/models`, preserve stable slot aliases, and expose hal0 capability metadata to Hermes's model picker;
- identify an owning slot by its opaque stable slot ID; treat the name as a mutable label and source any surfaced port from PortAuthority rather than slot config;
- set OpenAI transport, normalize tool/reasoning fields, and identify `owned_by=hal0` models;
- support regular and auxiliary roles (`compression`, `vision`, approval, skill curation, MCP sampling) through named hal0 slots;
- promote the current provision-time `_resolve_auxiliary_tasks()` logic into a generation-stamped runtime role-slot endpoint; the provider must query that endpoint rather than freeze role assignments into profile config;
- provide a fallback chain from local hal0 slots to explicitly operator-configured cloud providers, never silently;
- make doctor check reachability, auth, model availability, and tool-call capability separately.

`hal0-hermes-core` uses `HAL0_CLIENT_KEY` for inference/read requests and `HAL0_ADMIN_KEY` only for mutations, with deny-by-default credential selection. Liveness and preflight use the OPEN `/api/health` endpoint; `/api/status` is admin-gated and must not be used as a reachability probe.

This reverses the old rework's removal of `Hal0Profile`, but fixes its root defect: discovery/config owns location; the provider adds integration semantics rather than duplicating router policy.

### B. `hal0-memory` — exclusive memory provider

**Verified seam.** `MemoryProvider` defines availability, initialize, prompt block, prefetch/queued prefetch, turn sync, tool schemas/dispatch, shutdown, setup schema/save/post-setup, turn/session/switch/compression/write/delegation hooks, and profile/session identity inputs such as `hermes_home`, `platform`, `agent_context`, `agent_identity`, `agent_workspace`, parent session and platform user IDs ([source](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/agent/memory_provider.py)). Hermes intentionally activates at most one external provider, and provider CLI commands are exposed only for the active provider ([authoring guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/memory-provider-plugin.md#single-provider-rule)).

**Proposal.** Evolve the existing plugin rather than add a second memory adapter:

- use Hindsight/hal0 memory's native HTTP API directly for recall/write; keep MCP only as an optional explicit tool surface;
- map `agent_identity` and `agent_workspace` to `private:<agent>` plus `shared`, and map stable platform user/session IDs without relying on a browser-set identity header;
- make `prefetch()` fast and bounded, sanitize/fence recalled text, and queue next-turn retrieval;
- use `sync_turn`, `on_pre_compress`, `on_session_end`, and `on_delegation` for durable capture with idempotency keys;
- ignore or separately namespace cron/subagent/flush contexts so synthetic prompts do not become user memories;
- expose search/recall/add/delete tools only when operator policy enables writes;
- implement setup schema, health diagnostics, post-setup bank creation, flush/shutdown, and an active-provider CLI;
- emit structured provenance: provider, agent, session, platform user, origin hook/tool, visibility, and source event ID.

### C. `hal0-context` — optional context-engine provider

**Verified seam.** A context engine implements the upstream `ContextEngine` ABC, maintains token/compression state, may expose tools, registers by directory or plugin context, and is selected explicitly through `context.engine`; engines are not auto-activated ([context-engine guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/context-engine-plugin.md)).

**Proposal.** Do not make memory responsible for whole-context retention. Add this only if hal0 wants Hindsight-backed lossless context or cross-device session continuation. Store immutable transcript/event references in hal0, return a bounded working set to Hermes, preserve tool-call/result pairs, and degrade to Hermes's built-in compressor when hal0 memory is unavailable. Ship disabled by default until parity tests cover branching, compression, resume, and tool-call continuity.

### D. `hal0-tools` — general control-plane plugin

**Verified seam.** General plugins can register tools, slash/CLI commands, skills, hooks, tool dispatch, and injected messages ([plugin capability table](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#what-plugins-can-do)).

**Proposal.** Expose narrow, typed hal0 operations rather than shelling out indiscriminately:

- read-only: service/slot/model status, health, logs tail, GPU/VRAM, download and benchmark status;
- state-changing: slot start/stop/switch, model pull/cancel, benchmark launch, service restart, with Hermes approval metadata and hal0-side authorization;
- slash commands `/hal0`, `/slots`, `/models`, `/memory-status`; an admin CLI namespace for diagnostics;
- a bundled `hal0-operator` skill describing capabilities, failure modes, and escalation rules.

Tools should call authenticated hal0 APIs, return JSON, include idempotency keys on mutations, and never grant the plugin arbitrary host-admin authority.

### E. `hal0-mcp` configuration bundle

**Verified seam.** Hermes supports stdio and HTTP MCP servers, OAuth HTTP, mTLS, `${ENV_VAR}` substitution, catalog installs, per-server enable/disable and allow/deny filtering, dynamic discovery/reload, toolsets, sampling, and can itself run as an MCP server ([MCP guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/mcp.md)). Stdio subprocess environments are filtered, and exposure can be constrained at configuration level ([MCP security](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/mcp.md#security-model)).

**Proposal.** Provision a profile-scoped MCP bundle rather than a Python plugin: hal0 memory (optional explicit tools), operator/status, registry/models, board/cron, and metrics query. Default to allowlists per server; separate read-only and mutating servers/toolsets; pass only declared secrets; expose reload through the supported RPC/API; publish a versioned catalog manifest with minimum Hermes compatibility.

Avoid duplicating a capability in `hal0-tools` and MCP unless there is a concrete consumer distinction. Recommended rule: native plugin tools for the bundled Hermes runtime and MCP for other agents/external clients, sharing one generated schema and backend implementation.

### F. `hal0-platform` / gateway integration

**Verified seam.** Gateway channels are platform plugins registered with `ctx.register_platform`; bundled examples cover Telegram, Discord, Slack, Teams, Matrix, IRC, Signal and others ([platform plugins source](https://github.com/NousResearch/hermes-agent/tree/7fd419e5e6a0ac53f934a69226262c41ba130a2c/plugins/platforms)). External hosts can drive the same agent via ACP, the TUI gateway JSON-RPC/WebSocket protocol, or an OpenAI-compatible HTTP/SSE API. The TUI gateway exposes session create/list/activate/close/history/compress/branch, prompts/steering, approvals, reload, delegation and streamed lifecycle events ([programmatic integration](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/programmatic-integration.md)).

**Proposal.** Prefer a hal0 dashboard client of the supported TUI gateway or API server instead of emulating a terminal. Add a platform adapter only if hal0 itself becomes a delivery channel. The adapter should delegate user authorization to hal0 sessions, retain Hermes platform/session identity, translate approvals into hal0 UI events, and support reconnect/resume. Keep Telegram/Discord/etc. upstream-native and manage their config/lifecycle from hal0.

### G. `hal0-voice` — TTS/STT providers and slot routing

**Verified seam.** Hermes recommends config-driven command providers for TTS/STT and offers Python registration for SDK/streaming cases. Voice mode covers local microphone flow, silence detection, streaming TTS, Telegram/Discord replies, Discord voice channels, access control, and provider selection ([voice guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/voice-mode.md), [extension map](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md#pluggable-interfaces--where-to-go-for-each)).

**Proposal.** Start with generated command-provider config pointing to stable hal0 STT/TTS wrappers. The wrappers resolve active slots at invocation time, accept input/output placeholders safely, enforce timeouts and size limits, and report structured errors. Use Python providers only for streaming audio, cancellation, progress, or voice metadata that cannot fit the command contract. Define fallbacks (local GPU/NPU → local CPU → operator-approved cloud), but never leak audio to cloud without explicit policy.

### H. `hal0-observability` — standalone observability plugin

**Verified seam.** Upstream already ships observability plugin examples for Langfuse and NeMo Relay, confirming a dedicated plugin category and precedent ([observability sources](https://github.com/NousResearch/hermes-agent/tree/7fd419e5e6a0ac53f934a69226262c41ba130a2c/plugins/observability), [Langfuse plugin](https://github.com/NousResearch/hermes-agent/tree/7fd419e5e6a0ac53f934a69226262c41ba130a2c/plugins/observability/langfuse)). Third-party observability integrations must be standalone, not merged into core ([policy](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/AGENTS.md#adding-new-tools)).

**Proposal.** Export Hermes turn/tool/provider/memory/context/gateway events to hal0's telemetry plane with correlation IDs spanning Hermes session, hal0 agent, model slot, tool call and memory event. Record latency/token/cost/error metadata; redact prompt, tool output, credentials and audio by default. Do not scrape dashboard internals. Prefer upstream hooks/observability APIs and tolerate exporter outage without blocking the agent.

### I. `hal0-dashboard` — native dashboard extension

**Verified seam.** A dashboard plugin has its own manifest and frontend bundle, may add/override tabs, insert shell or page-scoped slots, use a global browser SDK, mount backend API routes, apply plugin CSS, and reload through scan/rescan endpoints ([extension guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/extending-the-dashboard.md)). Hermes's dashboard also exposes management APIs for config/env, sessions, logs/analytics, cron, skills, MCP, memory, credentials, gateway lifecycle, operations and update checks ([dashboard REST API](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/web-dashboard.md#rest-api)).

**Proposal.** Build one native “hal0” dashboard plugin with:

- a hal0 overview tab for slots, services, GPU/VRAM and downloads;
- page slots augmenting Models (hal0 capability/slot badges), Memory (bank health), Sessions/Analytics (hal0 correlation links), and System (hal0 doctor/support bundle);
- backend routes that proxy only a narrow allowlist to loopback hal0 API, using server-side credentials;
- SDK feature detection and manifest compatibility bounds; no direct DOM reach-through;
- no duplicate secrets editor: link to the owning Hermes or hal0 settings surface.

If hal0 continues embedding upstream plugins inside its own dashboard, replace the hand-maintained shadow SDK with either the official bundle/manifest unchanged or a deliberately versioned adapter tested against the official example plugins ([example plugin repository](https://github.com/NousResearch/hermes-example-plugins)).

### J. `hal0-dashboard-auth` (optional)

**Verified seam.** Non-loopback dashboard binds engage an auth gate and fail closed without a configured provider. Built-ins include Nous OAuth, basic username/password for trusted network/VPN use, and self-hosted OIDC; cookies and WebSocket tickets protect HTTP and chat WS traffic ([dashboard authentication](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/web-dashboard.md#authentication-gated-mode)).

**Proposal.** Do not invent another session cookie unless hal0 truly needs single sign-on. Prefer self-hosted OIDC, reverse-proxy the dashboard without bypassing its gate, or implement a small `DashboardAuthProvider` that verifies a short-lived hal0 assertion. Keep the browser unable to mint `X-hal0-Agent`; derive agent identity server-side.

## 3. Configuration, session and security requirements

- Treat `$HERMES_HOME/config.yaml` as non-secret configuration and `.env`/auth stores as secret-bearing. Dashboard and CLI edit the same config; changes generally apply on the next session or gateway restart ([configuration docs](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/configuration.md), [dashboard config](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/web-dashboard.md#config)).
- Generate config fragments from one hal0 schema, deep-merge only owned keys, preserve operator edits, redact secrets in logs/UI, and provide dry-run/diff/rollback.
- Preserve upstream session IDs and stable session keys through every proxy. Hermes exposes stored session history and live-session controls as distinct concepts; do not conflate them ([programmatic integration](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/programmatic-integration.md#tui-gateway-json-rpc)).
- Keep project plugins disabled by default. Pin Git plugin installs to reviewed commits/digests; never auto-update production plugins. Upstream `update` pulls latest, so hal0 should wrap it with staged verification and rollback.
- Assume every loaded Python plugin has Hermes-process authority. Restrict what secrets Hermes receives and expose privileged hal0 mutations only through authenticated, audited APIs ([official security boundary](https://github.com/NousResearch/hermes-agent/security)). `$HERMES_HOME` is born `hal0:hal0`; root-owned residue is limited to the installed bundle/wrapper and seed configuration. Root writes use the audited `hal0-systemctl write-gateway-dropin` and `hal0-agentenv write-seed-toml` seams.
- Retain upstream dangerous-command approvals and hardline blocklist; do not use `--yolo`, `approvals.mode: off`, or dashboard `--insecure` as provisioning shortcuts ([security guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/security.md)).
- Apply gateway allowlists/pairing and default-deny authorization independently of hal0 LAN auth. A trusted hal0 host does not imply every messaging user is trusted ([gateway authorization](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/security.md#user-authorization-gateway)).

## 4. Delivery plan and gates

### Phase 0 — upstream reconciliation

1. Track a dedicated Hermes compatibility prerequisite. Change the source-of-record repository from the stale fork URL only after verifying lineage and selecting a reviewed official release/tag or commit; do not promote the researched `main` SHA automatically.
2. Diff the entire current tracked SDK set plus plugin manager/loaders, provider base/runtime, memory manager, context engine, dashboard extension/auth, gateway protocol, TTS/STT registries, hooks, MCP config/security and observability.
3. Expand `hermes-sdk-diff` from six files to these contractual surfaces; record manifest/config schema versions and CLI/API smoke fixtures.
4. Run the existing hal0 memory/plugin/dashboard tests against the candidate official pin. Do not attempt feature work until this compatibility PR lands.

### Phase 1 — foundations

1. Build a shared hal0 client/config package used by all plugins: endpoint discovery, auth, retries, timeouts, error schema, correlation/idempotency IDs.
2. Package plugins as a versioned, standalone hal0 distribution with pip entry points and/or staged directories under managed `$HERMES_HOME`; record source commit and file hashes.
3. Add `hal0 hermes plugins plan|apply|verify|rollback`, never a blind `plugins update`.
4. Add the hal0-side runtime role-slot API, seeded from `_resolve_auxiliary_tasks()`, before implementing restart-free provider aliases.

### Phase 2 — core integrations

1. Migrate and harden `hal0-memory`; test lifecycle, isolation, failed backend, compression, delegation, session switch and shutdown.
2. Add `hal0-provider`; consume `/api/events/stream?since=<cursor>` plus `/api/events?since=<cursor>` backfill and the runtime role-slot API; test discovery, gaps, tool calling, streaming, reasoning, vision/auxiliary roles, fallback and model hot-swap.
3. Add `hal0-tools` plus generated MCP servers; test read-only/mutating authorization and approval propagation.

### Phase 3 — user surfaces

1. Wire command-provider STT/TTS, then add streaming provider implementations only if needed.
2. Build the native dashboard extension and supported gateway client; remove obsolete SDK/PTY shims after parity.
3. Add observability with redaction and backpressure tests.

### Phase 4 — optional deep integration

Add `hal0-context` and hal0 dashboard auth only after core integrations are stable. These replace central lifecycle/security behavior and deserve separate threat models and rollback paths.

### Required verification matrix

- fresh install, upgrade, disable, remove, rollback and offline restart;
- CLI, gateway messaging, TUI-gateway WS/RPC and HTTP API sessions;
- local provider healthy/warming/down/unauthorized/model-missing plus explicit cloud fallback;
- memory backend healthy/slow/down, session switch, compression, subagent, cron and multi-profile isolation;
- dashboard loopback and authenticated non-loopback, HTTP and WS ticket paths;
- STT/TTS cancellation, timeout, oversized media, local fallback and cloud-consent behavior;
- plugin crash/load-order/name-collision/project-plugin trust and missing-env cases;
- secret redaction, shell/MCP environment filtering, approval enforcement and audit correlation.

## 5. Rework-document corrections and decisions

1. **Update the upstream record.** The current `earendil-works` pin is not the current official source. Preserve it as historical provenance, but select a reviewed target from `NousResearch/hermes-agent` before implementation.
2. **Revisit “Hal0Profile removed.”** Direct `base_url` configuration still works, but it leaves model discovery, provider doctor/setup, auxiliary routing and provider UX on the table. Restore a thin dynamic profile, not the stale hard-coded implementation.
3. **Retest `hal0-memory` discovery without deleting the packaging pair.** hal0 intentionally keeps an importable source copy and an installable hyphenated seed. The defect is the installed discovery destination: the local comment claims `$HERMES_HOME/plugins/<name>`, while official documentation requires `$HERMES_HOME/plugins/memory/<name>`. Preserve and parity-test both packaging copies, but install the seed under the official specialized layout ([official layout](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/memory-provider-plugin.md#directory-structure)).
4. **Replace the dashboard SDK assumption.** The rework tracks only `registry.ts` and `slots.ts`; current upstream has a documented manifest/SDK/backend-route contract and example repository. Track the full contract and consume it natively.
5. **Keep scheduler ownership separate.** Hermes cron and hal0 board/cron unification are control-plane concerns, not a memory-provider responsibility. Integrate through tools/MCP/gateway dispatch and stable session APIs.
6. **Clarify the two memory surfaces.** The collapsed `ARCHITECTURE.md` correctly records both MCP `/mcp/memory` and REST `/api/memory/*`, but it does not assign the Hermes-native lifecycle seam explicitly. Use REST for the native memory provider and retain MCP as the optional cross-agent facade.

## Primary-source index

- [Adding providers (live official docs)](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-providers)
- [Memory provider plugins (live official docs)](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [Context engine plugins (live official docs)](https://hermes-agent.nousresearch.com/docs/developer-guide/context-engine-plugin)
- [Official repository](https://github.com/NousResearch/hermes-agent)
- [General plugins](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/plugins.md)
- [Plugin authoring guide](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/plugins/index.md)
- [Model-provider plugin](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/model-provider-plugin.md)
- [Provider runtime](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/provider-runtime.md)
- [Memory-provider plugin](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/memory-provider-plugin.md)
- [Context-engine plugin](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/context-engine-plugin.md)
- [MCP](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/mcp.md)
- [Hooks](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/hooks.md)
- [Voice mode](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/voice-mode.md)
- [Programmatic integration](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/developer-guide/programmatic-integration.md)
- [Dashboard extensions](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/extending-the-dashboard.md)
- [Dashboard/API/auth](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/features/web-dashboard.md)
- [Security model](https://github.com/NousResearch/hermes-agent/blob/7fd419e5e6a0ac53f934a69226262c41ba130a2c/website/docs/user-guide/security.md)
- [Official example plugins](https://github.com/NousResearch/hermes-example-plugins)
