# memory.py

> 30 nodes

## Key Concepts

- **memory.py** (15 connections) — `src/hal0/mcp/memory.py`
- **MemorySchemaError** (13 connections) — `src/hal0/mcp/memory.py`
- **Any** (10 connections)
- **_resolve_dataset()** (9 connections) — `src/hal0/mcp/memory.py`
- **_memory_add()** (9 connections) — `src/hal0/mcp/memory.py`
- **_memory_search()** (8 connections) — `src/hal0/mcp/memory.py`
- **_memory_recall()** (8 connections) — `src/hal0/mcp/memory.py`
- **_optional()** (7 connections) — `src/hal0/mcp/memory.py`
- **_normalise_tags()** (7 connections) — `src/hal0/mcp/memory.py`
- **make_dispatcher()** (7 connections) — `src/hal0/mcp/memory.py`
- **_require()** (6 connections) — `src/hal0/mcp/memory.py`
- **_memory_list()** (6 connections) — `src/hal0/mcp/memory.py`
- **_memory_delete()** (5 connections) — `src/hal0/mcp/memory.py`
- **build_server()** (5 connections) — `src/hal0/mcp/memory.py`
- **_scrub_detail()** (3 connections) — `src/hal0/mcp/memory.py`
- **_iso_now()** (2 connections) — `src/hal0/mcp/memory.py`
- **Exception** (1 connections)
- **FastMCP** (1 connections)
- **hal0 memory MCP server — Hindsight-backed long-term memory tools.  By design, me** (1 connections) — `src/hal0/mcp/memory.py`
- **Raised when a memory tool call's args don't match the tool schema.** (1 connections) — `src/hal0/mcp/memory.py`
- **Tags may arrive as None, list, or stringified CSV (some MCP clients     don't sp** (1 connections) — `src/hal0/mcp/memory.py`
- **Thin shim around :func:`hal0.memory.namespace.resolve_write_dataset`     that re** (1 connections) — `src/hal0/mcp/memory.py`
- **memory_add(text, dataset?, tags?, metadata?, document_id?)     → {id, timestamp,** (1 connections) — `src/hal0/mcp/memory.py`
- **memory_search(query, limit=10, dataset="shared"|list, tags=[],** (1 connections) — `src/hal0/mcp/memory.py`
- **memory_list(dataset="shared", cursor=null, limit=50) → {items, next_cursor}.** (1 connections) — `src/hal0/mcp/memory.py`
- *... and 5 more nodes in this community*

## Relationships

- [MemoryNamespaceError](MemoryNamespaceError.md) (3 shared connections)
- [MemoryDispatcher](MemoryDispatcher.md) (1 shared connections)
- [RealtimeSession](RealtimeSession.md) (1 shared connections)
- [FakeMemoryProvider](FakeMemoryProvider.md) (1 shared connections)

## Source Files

- `src/hal0/mcp/memory.py`

## Audit Trail

- EXTRACTED: 119 (89%)
- INFERRED: 15 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*