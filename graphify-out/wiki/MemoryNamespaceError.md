# MemoryNamespaceError

> 23 nodes · cohesion 0.15

## Key Concepts

- **MemoryNamespaceError** (16 connections) — `src/hal0/memory/namespace.py`
- **resolve_write_dataset()** (15 connections) — `src/hal0/memory/namespace.py`
- **test_namespace_policy.py** (11 connections) — `tests/memory/test_namespace_policy.py`
- **resolve_read_datasets()** (9 connections) — `src/hal0/memory/namespace.py`
- **is_known_namespace()** (6 connections) — `src/hal0/memory/namespace.py`
- **namespace.py** (5 connections) — `src/hal0/memory/namespace.py`
- **test_read_string_resolves_via_write_rules()** (3 connections) — `tests/memory/test_namespace_policy.py`
- **test_write_private_prefix_still_requires_toggle()** (3 connections) — `tests/memory/test_namespace_policy.py`
- **test_write_private_rejects_missing_or_anonymous_client_id()** (3 connections) — `tests/memory/test_namespace_policy.py`
- **test_write_rejects_free_form_names()** (3 connections) — `tests/memory/test_namespace_policy.py`
- **test_known_namespaces_table()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **test_read_default_expansion_unchanged()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **test_read_list_drops_unknown_and_foreign_private()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **test_unknown_namespaces_rejected()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **test_write_allows_spec_table_names()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **test_write_private_toggle_still_promotes()** (2 connections) — `tests/memory/test_namespace_policy.py`
- **ValueError** (1 connections)
- **Namespace resolution — shared by the MCP + REST surfaces.  The MCP server (:mod:** (1 connections) — `src/hal0/memory/namespace.py`
- **Translate a read request into the effective dataset filter.      Mirrors the rea** (1 connections) — `src/hal0/memory/namespace.py`
- **Raised when namespace resolution can't be satisfied (e.g. private     requested** (1 connections) — `src/hal0/memory/namespace.py`
- **Spec §3 table membership: ``shared`` | ``agents`` | ``project:<id>``     | the c** (1 connections) — `src/hal0/memory/namespace.py`
- **Translate a write request into the effective dataset name.      Mirrors :func:`h** (1 connections) — `src/hal0/memory/namespace.py`
- **Spec §3 closed-namespace policy — :mod:`hal0.memory.namespace`.  Free-form datas** (1 connections) — `tests/memory/test_namespace_policy.py`

## Relationships

- [memory.py](memory.py.md) (13 shared connections)
- [memory_admin.py](memory_admin.py.md) (1 shared connections)
- [test_mcp_identity.py](test_mcp_identity.py.md) (1 shared connections)

## Source Files

- `src/hal0/memory/namespace.py`
- `tests/memory/test_namespace_policy.py`

## Audit Trail

- EXTRACTED: 50 (54%)
- INFERRED: 43 (46%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*