# hal0-memory

`MemoryProvider` plugin for the Hermes agent runtime. Wraps the
hal0-memory REST surface (`/api/memory/*` on hal0-api) so that Hermes's
durable memory is backed by hal0's Hindsight store.

This directory (`installer/agents/hermes/plugins/hal0-memory/`) is the
**canonical, shipped source** — vendored into the Hermes plugin tree at
provision time by `hal0.agents.hermes_provision._phase_install`. It is NOT
imported by hal0 itself — the upstream `agent.memory_provider` ABC resolves
inside the hermes-agent venv at runtime. (An earlier mirror lived at
`src/hal0/agents/hermes/plugins/memory_hindsight/`; it drifted out of sync
— async client, no tools, stale `hermes-agent` id — and was deleted.)

## Contract summary

| Item | Value |
|---|---|
| Plugin name | `hal0-memory` |
| Kind | `exclusive` (per `MemoryManager` single-provider invariant) |
| Base URL | `HAL0_MEMORY_BASE` env, defaults `http://127.0.0.1:8080` |
| Identity | `X-hal0-Agent: $HAL0_AGENT_ID` header (defaults `hermes`) |
| Banks | `private:hermes` (default) + `shared`, selected per write by `X-hal0-Private`; reads are a server-side union of both |
| Dataset field | **NEVER SENT** — server resolves the bank from the headers (issue #317) |
| Transport | synchronous `httpx.Client` (Hermes memory hooks are sync) |
| Timeouts | 3s connect / 30s read |
| Tool schemas | `hal0_memory_search`, `hal0_memory_recall`, `hal0_memory_add` — explicit read/write tools, on top of prompt-injection prefetch |

## Why no `dataset` field

The hal0-memory REST routes resolve the write target from the
`X-hal0-Agent` + `X-hal0-Private` headers (PR #366), never from an explicit
`dataset` in the request body. Sending an explicit `private:hermes` here
re-trips the `_AGENT_ID_PATTERN` reject that an older plugin stub caused.

The regression tests in `tests/agents/test_hal0_memory_client.py` assert
that no outbound REST payload from `add()` carries a `dataset` key, locking
the fix.

## ABC surface implemented

From `agent/memory_provider.py`:

* `name` (property) — returns `"hal0-memory"`.
* `is_available()` — `True` unconditionally (config-only check; no
  network call per ABC docstring).
* `initialize(session_id, **kwargs)` — opens the sync client, honours
  `agent_context` so cron/flush/subagent loops skip writes.
* `system_prompt_block()` — explains the two-bank model + the explicit
  memory tools.
* `prefetch(query, *, session_id)` — best-effort `/api/memory/recall` with
  a 2048-token budget, no explicit `types` (inherits the server's default
  recall mix — world + experience + observation); transport failures fall
  back to empty string.
* `sync_turn(user, assistant, *, session_id)` — fire-and-forget
  `/api/memory/add` to the private bank; honours `_SKIP_WRITE_CONTEXTS`.
* `get_tool_schemas()` — returns the three `hal0_memory_*` tool schemas.
* `handle_tool_call(name, args)` — dispatches `hal0_memory_search` /
  `hal0_memory_recall` / `hal0_memory_add`.
* `on_memory_write(action, target, content, metadata=None)` — mirrors the
  built-in memory tool's writes into hal0-memory (private bank).
* `shutdown()` — closes the owned `httpx.Client`.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `HAL0_MEMORY_BASE` | `http://127.0.0.1:8080` | hal0-api base URL |
| `HAL0_AGENT_ID` | `hermes` | Identity for `X-hal0-Agent`; also the private-bank suffix (`private:<id>`) |

Both are read from the environment at `initialize()` time so per-agent
unit overrides take effect on provider construction without restart.
