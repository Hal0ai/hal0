# Hal0MemoryClient

> 18 nodes

## Key Concepts

- **Hal0MemoryClient** (16 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **._request()** (9 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **Any** (6 connections)
- **.add()** (4 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.search()** (4 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.recall()** (4 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.list_items()** (4 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.delete()** (4 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **._headers()** (2 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.base_url()** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.agent_id()** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **.close()** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **Synchronous REST client for hal0-memory.      Instantiated once per ``initialize** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **POST /api/memory/add. ``private=True`` → hermes-private, else shared.** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **POST /api/memory/search — semantic retrieval (union of both banks).** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **POST /api/memory/recall — token-budgeted consolidated recall (union).** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **GET /api/memory/list — page through stored items.** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`
- **POST /api/memory/delete — remove memory items by id.** (1 connections) — `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`

## Relationships

- [MemoryProvider](MemoryProvider.md) (5 shared connections)
- [Hal0MemoryProvider](Hal0MemoryProvider.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes/plugins/memory_hindsight/_client.py`

## Audit Trail

- EXTRACTED: 60 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*