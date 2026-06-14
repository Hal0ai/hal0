# hal0 codebase map

Where things live in the hal0 source tree at `/opt/hal0/`.

## Source tree (`/opt/hal0/src/hal0/`)

### API layer
- `api/routes/v1.py` — OpenAI-compatible `/v1/` endpoints. Chat handler at L550-584: `chat_completions()` reads body, rewrites slot aliases (`_rewrite_chat_slot_alias`, L233-281), optionally runs OmniRouter loop, then dispatches via `_dispatch_and_forward`. Also: `/v1/models` listing, embeddings, STT, TTS, image generation, rerankings.
- `api/routes/lemonade_proxy.py` — NoRouteFound fall-through proxy to lemond `:13305` (L138).
- `api/` — `hal0_chat_slot_alias_map()` builds alias→model_id dict from slot configs.

### Dispatcher
- `dispatcher/router.py` — `dispatch()` routes requests to upstreams. Composite `hal0` upstream (L99-119) redirects to lemond `:13305/v1/` (L138). Remote upstreams forward to their own URLs.
- `dispatcher/forward.py` — httpx-based forwarding with retry, error recovery.

### Agent provisioning (Hermes integration)
- `agents/hermes_templates/config.yaml.j2` — Jinja2 template for Hermes `config.yaml`. Sections: `model` (L45-57), `providers.custom` (L59-66), `model_aliases` (L70-76), `custom_providers` (L87-96, per-model context_length), `delegation` (L98-110), `memory` (L112-123), `mcp_servers` (L130-141), `skills` (L143-147), `terminal` (L149-151), `agent` (L153-164), `auxiliary` (L183-191), `stt/tts` (L193-210).
- `agents/hermes_provision.py` — Bootstrap state assembly, template rendering, config write. `_apply_overrides()` at L818-833 deep-merges `/etc/hal0/agents/hermes/overrides.yaml` on top of rendered YAML.
- `agents/hermes_provision.py:480` — legacy `Hal0Profile` model-provider plugin removal (dead `:8000` base_url).

### Bifrost (to be retired, per spec)
- `gateway/normalize/resolve.go` — live `/health` LLM-slot resolver + `isNPUorFLM` discriminator (ported to Python in spec).
- `gateway/normalize/normalize.go:35` — `chat_template_kwargs.enable_thinking=false` (wrong layer for current lemond).

## Runtime paths

| Path | Role |
|------|------|
| `/opt/hal0/` | Source repo root |
| `/var/lib/hal0/` | hal0 data directory (runtime state, models, venvs, secrets) |
| `/var/lib/hal0/.hermes/config.yaml` | Active Hermes config (rendered by provisioner) |
| `/var/lib/hal0/secrets/agents/hermes.env` | Platform tokens, allowlists (provisioner-owned) |
| `/var/lib/hal0/agents/hermes/logs/gateway.log` | Gateway application log (not journald) |
| `/root/.config/systemd/user/hermes-gateway.service` | Gateway systemd unit |
| `/etc/hal0/agents/hermes/overrides.yaml` | Config overrides (survives re-render) |
| `/etc/hal0/capabilities.toml` | Slot/capability config (edit via hal0_admin, not directly) |

## Design specs

At `/opt/hal0/docs/superpowers/specs/`. Dated filenames, e.g. `2026-06-04-hal0-api-lemond-normalization-design.md`.

## hal0-api request flow (chat)

```
POST /v1/chat/completions (:8080)
  → _read_json_body (v1.py:569)
  → _rewrite_chat_slot_alias (v1.py:233-281) — alias → model_id
  → [OmniRouter loop if omni:true]
  → _ensure_backend_for_model (v1.py:359) — SlotManager.load()
  → Dispatcher.dispatch → forward
      → composite "hal0" → http://127.0.0.1:13305/v1/chat/completions
      → NoRouteFound → lemonade_proxy._proxy → :13305
```

## Hermes → hal0 integration

- Provider: `custom` (built-in Hermes provider for OpenAI-compat LAN endpoints)
- Base URL: `http://127.0.0.1:8080/v1`
- Model discovery: Hermes queries `GET /v1/models` on startup (reads `data[].id` only — models.py:3168)
- Model picker (`/model`): populated EXCLUSIVELY from server-side `/v1/models` response. Client-side `model_aliases` in config.yaml feed `DIRECT_ALIASES` for request-time resolution only — they do NOT populate the picker UI.
- Context length: resolved via multi-step chain (model_metadata.py:1452-1738). Priority order: `model.context_length` global → `custom_providers` per-model → persistent cache → live `/v1/models` probe → provider-specific APIs → models.dev → hardcoded defaults → 256K fallback. `custom_providers` wins over `/v1/models` when both exist.
- Context-length fields read from `/v1/models`: `context_length` > `context_window` > `context_size` > `max_model_len` > ... (11 keys, first valid int wins).
- Thinking suppression: Hermes does NOT set `enable_thinking`/`chat_template_kwargs`/`no_think` on any outbound request. Safe for server-side defaults.
- JSON tolerance: Hermes ignores unknown fields in `/v1/models` rows. Adding a `_hal0` metadata block is safe.
- Subagents: `delegation.model` resolved once at delegate_task time, not re-read per turn.
- Auxiliary tasks: `auxiliary.<task>.{provider,model,base_url}` for compaction/title/search/vision
- Full internals: see `homelab-ops/hal0-hermes-integration` skill and its `references/hermes-provider-internals.md`.
