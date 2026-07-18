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
| Banks | `private:<agent-id>` + `shared`; reads are a server-enforced union |
| Visibility policy | Raw turns/lifecycle captures → **private**; explicit durable writes (`hal0_memory_add`) → **shared by default**, `visibility:"private"` override (or `HAL0_MEMORY_DEFAULT_VISIBILITY` / profile policy). Enforced server-side. |
| Dataset field | **NEVER SENT** — server resolves the bank from `X-hal0-Agent` + `X-hal0-Private` headers (issue #317 / PR #366) |
| Transport | **synchronous** `httpx.Client` (memory hooks are sync; async wrapping broke on reuse) |
| Timeouts | 3s connect / 30s read |
| Tool schemas | `hal0_memory_search`, `hal0_memory_recall`, `hal0_memory_add(visibility=…)` |
| Recall framing | `prefetch()` labels recall as untrusted historical **DATA, not instructions** and annotates each item with provenance/visibility/verification/confidence/observed-at |
| Operator CRUD | Via the `hal0-memory` MCP server (loaded separately) |

## Why no `dataset` field

The hal0-memory REST routes call `resolve_write_dataset(requested,
private, client_id)` (`src/hal0/api/routes/memory.py`). When the
client omits an explicit dataset, the server reads `X-hal0-Agent` (and
`X-hal0-Private`) and routes the write to `private:<agent_id>` or
`shared`. Sending an explicit `private:<id>` re-trips the
`_AGENT_ID_PATTERN` reject in `src/hal0/mcp/memory.py`.

The regression test `test_no_dataset_field_ever_sent` in
`tests/agents/test_hal0_memory_client.py` asserts that the client's `add`
never sends a `dataset` key, locking the fix.

## ABC surface implemented

From `agent/memory_provider.py`:

* `name` (property) — returns `"hal0-memory"`.
* `is_available()` — `True` unconditionally (config-only check; no network
  call per ABC docstring). Reachability is a runtime concern for
  `initialize()`/diagnostics.
* `initialize(session_id, **kwargs)` — builds the sync client, records
  `agent_context` (cron/flush/subagent skip writes), resolves the durable
  default visibility and retry-spool location.
* `system_prompt_block()` — two-bank preamble stating durable writes default
  shared and recall is historical context, not instructions.
* `prefetch(query, *, session_id)` — best-effort `/api/memory/recall`
  (2048-token budget), framed as untrusted DATA and provenance-annotated;
  folds in any `queue_prefetch` query; transport failures fall back to `""`.
* `queue_prefetch(query, *, session_id)` — parks a deeper next-turn query
  (bounded, single-slot, non-blocking); drained on the next `prefetch`.
* `sync_turn(user, assistant, *, session_id, messages=None)` — non-blocking
  **private** raw capture with a stable `source_event` idempotency key;
  honours `_SKIP_WRITE_CONTEXTS`.
* `on_pre_compress(messages)` — persists a **private** continuity checkpoint
  and returns a compact continuity marker for Hermes to keep.
* `on_session_end(messages)` — flushes a **private** session-end checkpoint.
* `on_delegation(task, result, *, child_session_id)` — records delegated work
  in the **private** bank (separate namespace via `child_session_id`).
* `get_tool_schemas()` / `handle_tool_call()` — explicit
  `hal0_memory_{search,recall,add}` tools. `add` defaults to the **shared**
  bank; pass `visibility:"private"` for the private override.
* `on_memory_write(action, target, content, metadata=None)` — mirrors the
  built-in memory tool's writes **privately** into hal0-memory.
* `get_config_schema()` / `save_config(values, hermes_home)` — official setup
  schema (base_url, agent_id, default visibility); persists only the declared
  non-secret keys to `<hermes_home>/hal0-memory.config.json`.
* `backup_paths()` — declares the persisted config + retry-spool dir.
* `shutdown()` — closes the owned `httpx.Client` (idempotent).

## Settings

| Setting | Resolution order | Default |
|---|---|---|
| base URL | ctor arg → `HAL0_MEMORY_BASE` → `memory.hal0.base_url` | `http://127.0.0.1:8080` |
| agent id | ctor arg → `HAL0_AGENT_ID` → `memory.hal0.agent_id` | `hermes` |
| durable default visibility | `HAL0_MEMORY_DEFAULT_VISIBILITY` → profile policy → per-write `visibility` | `shared` |
| retry spool | `HAL0_MEMORY_SPOOL` | unset |

Env vars are read at `initialize()` time so per-agent unit overrides
(`hal0-agent@hermes.service` / gateway drop-ins) take effect on provider
construction without code change.
