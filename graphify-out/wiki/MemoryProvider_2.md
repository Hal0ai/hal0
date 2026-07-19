# MemoryProvider

> 23 nodes · cohesion 0.11

## Key Concepts

- **MemoryProvider** (14 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **provider.py** (13 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **Hal0MemoryClientError** (11 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **_FakeCtx** (7 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **_client.py** (6 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.__init__()** (5 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.__init__()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **_resolve_agent_id()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **_resolve_base_url()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.get_tool_schemas()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **.initialize()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **ABC** (2 connections)
- **test_register_registers_a_hal0_memory_provider()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **Client** (1 connections)
- **RuntimeError** (1 connections)
- **Thin synchronous REST client for the hal0-memory REST surface.  Design notes: (1** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **Raised when a hal0-memory REST call fails.      Wraps the upstream status code +** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.is_available()** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **.name()** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **Hermes ``MemoryProvider`` backed by hal0-memory REST — canonical, shipped source** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/provider.py`
- **.__init__()** (1 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **.register_memory_provider()** (1 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **Stub loader context — records ``register_memory_provider`` calls.** (1 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Relationships

- [Hal0MemoryClient](Hal0MemoryClient.md) (6 shared connections)
- [Hal0MemoryProvider](Hal0MemoryProvider.md) (6 shared connections)
- [PgVectorProvider](PgVectorProvider.md) (3 shared connections)
- [_FakeHttpClient](_FakeHttpClient.md) (2 shared connections)
- [HindsightProvider](HindsightProvider.md) (2 shared connections)
- [FakeMemoryProvider](FakeMemoryProvider.md) (2 shared connections)
- [MemoryProvider](MemoryProvider.md) (2 shared connections)
- [test_memory_hindsight_plugin.py](test_memory_hindsight_plugin.py.md) (2 shared connections)
- [test_hindsight_provider.py](test_hindsight_provider.py.md) (1 shared connections)

## Source Files

- `installer/agents/hermes/plugins/hal0-memory/_client.py`
- `installer/agents/hermes/plugins/hal0-memory/provider.py`
- `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Audit Trail

- EXTRACTED: 66 (82%)
- INFERRED: 14 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*