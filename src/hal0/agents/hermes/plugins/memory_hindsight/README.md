# hal0-memory

`MemoryProvider` plugin for the Hermes agent runtime. Wraps the
hal0-memory REST surface (`/api/memory/*` on hal0-api) so that Hermes's
durable memory is backed by hal0's Hindsight store.

This plugin lives under hal0's repo (`src/hal0/agents/hermes/plugins/memory_hindsight/`);
the installer seed at `installer/agents/hermes/plugins/hal0-memory/` is a
byte-identical copy (enforced by
`tests/agents/hermes_plugins/test_seed_parity.py`) and is vendored into the
Hermes plugin tree at provision time by
`hal0.agents.hermes_provision._phase_install`. It is NOT imported by
hal0 itself — the upstream `agent.memory_provider` ABC resolves inside
the hermes-agent venv at runtime.

## Contract summary

| Item | Value |
|---|---|
| Plugin name | `hal0-memory` |
| Kind | `exclusive` (per `MemoryManager` single-provider invariant) |
| Base URL | arg → `HAL0_MEMORY_BASE` env → `memory.hal0.base_url` (config.yaml) → `http://127.0.0.1:8080` |
| Identity | `X-hal0-Agent` header: arg → `HAL0_AGENT_ID` env → `memory.hal0.agent_id` → `hermes` |
| Banks | `private:<agent-id>` (default writes) + `shared` (`X-hal0-Private: 0`); reads are a union |
| Dataset field | **NEVER SENT** — server resolves from headers (issue #317 / PR #366) |
| Transport | **synchronous** `httpx.Client` (memory hooks are sync; async wrapping broke on reuse) |
| Timeouts | 3s connect / 30s read / 1s probe |
| Tool schemas | `hal0_memory_search`, `hal0_memory_recall`, `hal0_memory_add(shared=…)` |
| Observability | first (and every 25th) transport failure per op logs WARNING; `failure_counts` property; degraded notice in system prompt |
| Operator CRUD | Via the `hal0-memory` MCP server (loaded separately) |

## Why no `dataset` field

The hal0-memory REST routes call `resolve_write_dataset(requested,
private, client_id)` (`src/hal0/api/routes/memory.py`). When the
client omits an explicit dataset, the server reads `X-hal0-Agent` (and
`X-hal0-Private`) and routes the write to `private:<agent_id>` or
`shared`. Sending an explicit `private:<id>` re-trips the
`_AGENT_ID_PATTERN` reject in `src/hal0/mcp/memory.py`.

The regression test in
`tests/agents/hermes_plugins/test_memory_hindsight_provider.py` asserts
that no outbound REST payload carries a `dataset` key, locking the fix.

## ABC surface implemented

From `agent/memory_provider.py`:

* `name` (property) — returns `"hal0-memory"`.
* `is_available()` — `True` unconditionally (config-only check; no network
  call per ABC docstring). Reachability is probed at `initialize()` and
  surfaced as a degraded notice in the system prompt instead.
* `initialize(session_id, **kwargs)` — builds the sync client, probes
  reachability (1s), honours `agent_context` so cron/flush/subagent loops
  skip writes.
* `system_prompt_block()` — two-bank preamble; three-tier variant for
  profile agent-ids (`hermes__<profile>`): profile-private bank, global
  roll-up bank, shared.
* `prefetch(query, *, session_id)` — best-effort `/api/memory/recall`
  with a 2048-token budget; transport failures fall back to empty string.
* `sync_turn(user, assistant, *, session_id)` — fire-and-forget
  `/api/memory/add`; honours `_SKIP_WRITE_CONTEXTS`.
* `get_tool_schemas()` / `handle_tool_call()` — explicit
  `hal0_memory_{search,recall,add}` tools (robust even when the MCP
  server's tools aren't surfaced to a session).
* `on_memory_write(action, target, content, metadata=None)` — mirrors
  the built-in memory tool's writes into hal0-memory.
* `shutdown()` — closes the owned `httpx.Client` (idempotent).

## Settings

| Setting | Resolution order | Default |
|---|---|---|
| base URL | ctor arg → `HAL0_MEMORY_BASE` → `memory.hal0.base_url` in `$HERMES_HOME/config.yaml` | `http://127.0.0.1:8080` |
| agent id | ctor arg → `HAL0_AGENT_ID` → `memory.hal0.agent_id` | `hermes` |

Env vars are read at `initialize()` time so per-agent unit overrides
(`hal0-agent@hermes.service` / gateway drop-ins) take effect on provider
construction without code change. The config.yaml fallback lets operators
retarget without touching unit files.
