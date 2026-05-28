# hal0-cognee

`MemoryProvider` plugin for the Hermes agent runtime. Wraps the
hal0-memory REST surface (`/api/memory/*` on hal0-api) so that Hermes's
durable memory is backed by hal0's embedded Cognee store.

This plugin lives under hal0's repo (`src/hal0/agents/hermes/plugins/memory_cognee/`)
and is vendored into the Hermes plugin tree at provision time by
`hal0.agents.hermes_provision._phase_install`. It is NOT imported by
hal0 itself — the upstream `agent.memory_provider` ABC resolves inside
the hermes-agent venv at runtime.

## Contract summary

| Item | Value |
|---|---|
| Plugin name | `hal0-cognee` |
| Kind | `exclusive` (per `MemoryManager` single-provider invariant) |
| Base URL | `HAL0_MEMORY_BASE` env, defaults `http://127.0.0.1:8080` |
| Identity | `X-hal0-Agent: $HAL0_AGENT_ID` header (defaults `hermes-agent`) |
| Dataset field | **NEVER SENT** — server resolves from header (issue #317) |
| Timeouts | 3s connect / 10s read |
| Tool schemas | None — memory surfaces via system prompt + prefetch context |
| Operator CRUD | Via the `hal0-memory` MCP server (loaded separately) |

## Why no `dataset` field

The hal0-memory REST routes call `resolve_write_dataset(requested,
private, client_id)` (`src/hal0/api/routes/memory.py:291`). When the
client omits an explicit dataset, the server reads `X-hal0-Agent` and
routes the write to `private:<agent_id>`. Sending an explicit
`private:hermes-agent` re-trips the `_AGENT_ID_PATTERN` reject in
`src/hal0/mcp/memory.py:200` — the same bug the retired
`hal0-memory` plugin stub caused at
`installer/agents/hermes/plugins/hal0-memory/__init__.py:117`.

The regression test in
`tests/agents/hermes_plugins/test_memory_cognee_provider.py` asserts
that no outbound REST payload carries a `dataset` key, locking the
fix.

## ABC surface implemented

From `agent/memory_provider.py:42`:

* `name` (property) — returns `"hal0-cognee"`.
* `is_available()` — `True` unconditionally (config-only check; no
  network call per ABC docstring).
* `initialize(session_id, **kwargs)` — opens the async client, honours
  `agent_context` so cron/flush/subagent loops skip writes (the same
  guard honcho and supermemory ship).
* `system_prompt_block()` — short memory-availability preamble.
* `prefetch(query, *, session_id)` — best-effort `/api/memory/search`
  with a 5-item budget; transport failures fall back to empty string.
* `sync_turn(user, assistant, *, session_id)` — fire-and-forget
  `/api/memory/add`; honours `_SKIP_WRITE_CONTEXTS`.
* `get_tool_schemas()` — returns `[]`. Memory tools live on the
  MCP path so they don't double-register against the agent loop.
* `on_memory_write(action, target, content, metadata=None)` — mirrors
  the built-in memory tool's writes into hal0-cognee.
* `shutdown()` — closes the owned `httpx.AsyncClient`.

## Integration wiring (PR-3)

PR-3 (hermes_provision overhaul) will:

1. Copy this directory into `$HERMES_HOME/plugins/memory/hal0-cognee/`
   at `_phase_install` time.
2. Set `memory.provider = "hal0-cognee"` in `$HERMES_HOME/config.yaml`.
3. Drop the retired `installer/agents/hermes/plugins/hal0-memory/`
   stub.

End-to-end LXC smoke (provider loads, prefetch returns, sync_turn
writes hit hal0-memory) is deferred to that PR. This PR ships the
plugin sources + unit tests only.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `HAL0_MEMORY_BASE` | `http://127.0.0.1:8080` | hal0-api base URL |
| `HAL0_AGENT_ID` | `hermes-agent` | Identity for `X-hal0-Agent` |

Both are read from the environment at `initialize()` time so per-agent
unit overrides (PR-5 `hal0-agent@hermes.service`) take effect on
provider construction without restart.
