# test_mcp_transport_security.py

> 28 nodes

## Key Concepts

- **test_mcp_transport_security.py** (9 connections) — `tests/api/test_mcp_transport_security.py`
- **mcp_mount.py** (8 connections) — `src/hal0/api/mcp_mount.py`
- **_mcp_transport_security()** (8 connections) — `src/hal0/api/mcp_mount.py`
- **_current_mcp_request()** (7 connections) — `src/hal0/api/mcp_mount.py`
- **mount_mcp_servers()** (7 connections) — `src/hal0/api/mcp_mount.py`
- **bearer_resolver()** (5 connections) — `src/hal0/api/mcp_mount.py`
- **client_id_resolver()** (5 connections) — `src/hal0/api/mcp_mount.py`
- **private_resolver()** (5 connections) — `src/hal0/api/mcp_mount.py`
- **_streamable_client()** (5 connections) — `tests/api/test_mcp_transport_security.py`
- **_resolve_bearer()** (4 connections) — `src/hal0/api/mcp_mount.py`
- **_initialize_body()** (3 connections) — `tests/api/test_mcp_transport_security.py`
- **test_allowed_host_is_not_rejected_over_http()** (3 connections) — `tests/api/test_mcp_transport_security.py`
- **test_unconfigured_host_still_rejected()** (3 connections) — `tests/api/test_mcp_transport_security.py`
- **Request** (2 connections)
- **test_default_is_localhost_only()** (2 connections) — `tests/api/test_mcp_transport_security.py`
- **test_extra_hosts_added_and_origins_derived()** (2 connections) — `tests/api/test_mcp_transport_security.py`
- **test_wildcard_disables_protection()** (2 connections) — `tests/api/test_mcp_transport_security.py`
- **test_explicit_origins_override_derivation()** (2 connections) — `tests/api/test_mcp_transport_security.py`
- **Glue layer that mounts the hal0 admin + memory MCP servers on the FastAPI app.** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Extract a bearer token from the Authorization header, if present.** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Build the MCP transport's DNS-rebinding allowlist from the env.      FastMCP aut** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Return the Starlette ``Request`` for the in-flight MCP tool call.      The MCP S** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Return ``(raw_bearer, client_id)`` for the current MCP request.      Wired into** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Return ``client_id`` for the current MCP request (issue #317).      Identity com** (1 connections) — `src/hal0/api/mcp_mount.py`
- **Return whether the calling client toggled ``--private`` mode.      Read from the** (1 connections) — `src/hal0/api/mcp_mount.py`
- *... and 3 more nodes in this community*

## Relationships

- [create_app](create_app.md) (3 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)

## Source Files

- `src/hal0/api/mcp_mount.py`
- `tests/api/test_mcp_transport_security.py`

## Audit Trail

- EXTRACTED: 72 (78%)
- INFERRED: 20 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*