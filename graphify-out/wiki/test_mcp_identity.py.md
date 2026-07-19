# test_mcp_identity.py

> 16 nodes

## Key Concepts

- **test_mcp_identity.py** (10 connections) — `tests/api/test_mcp_identity.py`
- **patch_request()** (10 connections) — `tests/api/test_mcp_identity.py`
- **test_private_write_lands_in_agent_namespace()** (4 connections) — `tests/api/test_mcp_identity.py`
- **_fake_request()** (3 connections) — `tests/api/test_mcp_identity.py`
- **test_client_id_from_agent_header()** (2 connections) — `tests/api/test_mcp_identity.py`
- **test_client_id_anonymous_when_header_absent()** (2 connections) — `tests/api/test_mcp_identity.py`
- **test_client_id_anonymous_outside_request()** (2 connections) — `tests/api/test_mcp_identity.py`
- **test_client_id_rejects_private_prefix()** (2 connections) — `tests/api/test_mcp_identity.py`
- **test_client_id_rejects_malformed()** (2 connections) — `tests/api/test_mcp_identity.py`
- **test_private_resolver_reads_header()** (2 connections) — `tests/api/test_mcp_identity.py`
- **Request** (1 connections)
- **MonkeyPatch** (1 connections)
- **Tests for #317 — MCP caller identity via the ``X-hal0-Agent`` header.  Before th** (1 connections) — `tests/api/test_mcp_identity.py`
- **Minimal Starlette Request exposing the given headers.** (1 connections) — `tests/api/test_mcp_identity.py`
- **Return a setter that points the MCP request context at fake headers.** (1 connections) — `tests/api/test_mcp_identity.py`
- **End-to-end #317: X-hal0-Agent + private → private:<agent>, not shared.** (1 connections) — `tests/api/test_mcp_identity.py`

## Relationships

- [MemoryNamespaceError](MemoryNamespaceError.md) (1 shared connections)

## Source Files

- `tests/api/test_mcp_identity.py`

## Audit Trail

- EXTRACTED: 44 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*