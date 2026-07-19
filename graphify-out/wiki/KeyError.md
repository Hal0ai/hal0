# KeyError

> 32 nodes · cohesion 0.11

## Key Concepts

- **KeyError** (10 connections)
- **.approve()** (9 connections) — `src/hal0/mcp/approval_queue.py`
- **.deny()** (9 connections) — `src/hal0/mcp/approval_queue.py`
- **.enqueue()** (9 connections) — `src/hal0/mcp/approval_queue.py`
- **.as_dict()** (8 connections) — `src/hal0/mcp/approval_queue.py`
- **_primary_target()** (8 connections) — `src/hal0/mcp/approval_queue.py`
- **Any** (8 connections)
- **_Event** (7 connections) — `src/hal0/mcp/approval_queue.py`
- **approval_queue.py** (6 connections) — `src/hal0/mcp/approval_queue.py`
- **._emit()** (6 connections) — `src/hal0/mcp/approval_queue.py`
- **.get()** (6 connections) — `src/hal0/mcp/approval_queue.py`
- **resolve_profile()** (5 connections) — `src/hal0/config/loader.py`
- **ApprovalEntry** (5 connections) — `src/hal0/mcp/approval_queue.py`
- **.list_all()** (4 connections) — `src/hal0/mcp/approval_queue.py`
- **.list_pending()** (4 connections) — `src/hal0/mcp/approval_queue.py`
- **.subscribe()** (4 connections) — `src/hal0/mcp/approval_queue.py`
- **_hash_args()** (4 connections) — `src/hal0/mcp/approval_queue.py`
- **Look up a named profile in the profiles.toml catalog.      Shared by every provi** (1 connections) — `src/hal0/config/loader.py`
- **Queue** (1 connections)
- **In-memory approval queue for gated MCP tool calls.  Phase 8 (Agents v0.2) MCP se** (1 connections) — `src/hal0/mcp/approval_queue.py`
- **A single gated tool invocation awaiting owner action.** (1 connections) — `src/hal0/mcp/approval_queue.py`
- **JSON-safe projection for REST / SSE consumers.** (1 connections) — `src/hal0/mcp/approval_queue.py`
- **A queue event emitted to SSE subscribers.** (1 connections) — `src/hal0/mcp/approval_queue.py`
- **Queue a gated tool call. Returns the approval id.          If an existing pendin** (1 connections) — `src/hal0/mcp/approval_queue.py`
- **Snapshot of every entry still in the ``pending`` state.** (1 connections) — `src/hal0/mcp/approval_queue.py`
- *... and 7 more nodes in this community*

## Relationships

- [ApprovalQueue](ApprovalQueue.md) (9 shared connections)
- [ModelVariant](ModelVariant.md) (1 shared connections)
- [admin.py](admin.py.md) (1 shared connections)
- [LlamaServerProvider](LlamaServerProvider.md) (1 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (1 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [test_v1_slot_alias_models.py](test_v1_slot_alias_models.py.md) (1 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/mcp/approval_queue.py`

## Audit Trail

- EXTRACTED: 115 (91%)
- INFERRED: 12 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*