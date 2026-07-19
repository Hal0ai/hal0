# _shared.py

> 21 nodes · cohesion 0.14

## Key Concepts

- **_shared.py** (16 connections) — `src/hal0/cli/_shared.py`
- **CliApiError** (12 connections) — `src/hal0/cli/_shared.py`
- **_api_request()** (11 connections) — `src/hal0/cli/_shared.py`
- **api_delete()** (9 connections) — `src/hal0/cli/_shared.py`
- **api_get_bytes()** (7 connections) — `src/hal0/cli/_shared.py`
- **Any** (7 connections)
- **uninstall_cmd()** (6 connections) — `src/hal0/cli/mcp_commands.py`
- **api_patch()** (6 connections) — `src/hal0/cli/_shared.py`
- **advertise_upstream()** (6 connections) — `src/hal0/cli/upstream_commands.py`
- **_auth_headers()** (5 connections) — `src/hal0/cli/_shared.py`
- **api_unreachable_exit()** (3 connections) — `src/hal0/cli/_shared.py`
- **api_unreachable_print()** (3 connections) — `src/hal0/cli/_shared.py`
- **Uninstall a user-installed MCP server. Bundled servers reject 409.** (1 connections) — `src/hal0/cli/mcp_commands.py`
- **RuntimeError** (1 connections)
- **Shared helpers for hal0 CLI modules.** (1 connections) — `src/hal0/cli/_shared.py`
- **GET ``path`` and return ``(raw_bytes, content_type)`` — for binary payloads** (1 connections) — `src/hal0/cli/_shared.py`
- **Issue a single HTTP request and decode JSON or raise CliApiError.** (1 connections) — `src/hal0/cli/_shared.py`
- **Raised by the api_* helpers when the API returns an error.** (1 connections) — `src/hal0/cli/_shared.py`
- **Bearer header for CLI→API calls on an auth-enabled box (halo150 O2).      Preced** (1 connections) — `src/hal0/cli/_shared.py`
- **Print an error and exit 1 when the API cannot be reached.** (1 connections) — `src/hal0/cli/_shared.py`
- **Flip an upstream's ``advertise_models`` flag live (no hal0-api restart).      Ad** (1 connections) — `src/hal0/cli/upstream_commands.py`

## Relationships

- [die](die.md) (28 shared connections)
- [test_auth_rotate.py](test_auth_rotate.py.md) (2 shared connections)
- [memory_bank_commands.py](memory_bank_commands.py.md) (2 shared connections)
- [upstream_commands.py](upstream_commands.py.md) (2 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)
- [memory_commands.py](memory_commands.py.md) (1 shared connections)
- [test_agent_uninstall_memory.py](test_agent_uninstall_memory.py.md) (1 shared connections)
- [config_commands.py](config_commands.py.md) (1 shared connections)
- [Check](Check.md) (1 shared connections)
- [update_commands.py](update_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/_shared.py`
- `src/hal0/cli/mcp_commands.py`
- `src/hal0/cli/upstream_commands.py`

## Audit Trail

- EXTRACTED: 74 (74%)
- INFERRED: 26 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*