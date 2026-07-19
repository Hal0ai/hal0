# Hal0MemoryClient

> 18 nodes · cohesion 0.18

## Key Concepts

- **Hal0MemoryClient** (19 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **._request()** (9 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **Any** (6 connections)
- **.add()** (4 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.delete()** (4 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.list_items()** (4 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.recall()** (4 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.search()** (4 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **._headers()** (2 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.agent_id()** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.base_url()** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **.close()** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **POST /api/memory/add. ``private=True`` → hermes-private, else shared.** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **POST /api/memory/search — semantic retrieval (union of both banks).** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **POST /api/memory/recall — token-budgeted consolidated recall (union).** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **GET /api/memory/list — page through stored items.** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **POST /api/memory/delete — remove memory items by id.** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`
- **Synchronous REST client for hal0-memory.      Instantiated once per ``initialize** (1 connections) — `installer/agents/hermes/plugins/hal0-memory/_client.py`

## Relationships

- [MemoryProvider](MemoryProvider.md) (6 shared connections)
- [_FakeHttpClient](_FakeHttpClient.md) (2 shared connections)
- [Hal0MemoryProvider](Hal0MemoryProvider.md) (1 shared connections)

## Source Files

- `installer/agents/hermes/plugins/hal0-memory/_client.py`

## Audit Trail

- EXTRACTED: 60 (92%)
- INFERRED: 5 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*