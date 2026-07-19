# installed.py

> 28 nodes · cohesion 0.13

## Key Concepts

- **installed.py** (13 connections) — `src/hal0/mcp/installed.py`
- **install()** (9 connections) — `src/hal0/mcp/installed.py`
- **InstalledServer** (9 connections) — `src/hal0/mcp/installed.py`
- **patch_config()** (9 connections) — `src/hal0/mcp/installed.py`
- **get_installed()** (8 connections) — `src/hal0/mcp/installed.py`
- **_registry_path()** (8 connections) — `src/hal0/mcp/installed.py`
- **_validate_id()** (7 connections) — `src/hal0/mcp/installed.py`
- **_harden_registry_perms()** (5 connections) — `src/hal0/mcp/installed.py`
- **.to_toml_dict()** (5 connections) — `src/hal0/mcp/installed.py`
- **_registry_dir()** (5 connections) — `src/hal0/mcp/installed.py`
- **uninstall()** (5 connections) — `src/hal0/mcp/installed.py`
- **list_installed()** (4 connections) — `src/hal0/mcp/installed.py`
- **_registry_lock()** (4 connections) — `src/hal0/mcp/installed.py`
- **Path** (3 connections)
- **Any** (1 connections)
- **BaseModel** (1 connections)
- **Registry for hal0-hosted, user-installed MCP servers (issue #305).  Bundled MCP** (1 connections) — `src/hal0/mcp/installed.py`
- **Tighten perms on the registry dir + a single record file.      Called immediatel** (1 connections) — `src/hal0/mcp/installed.py`
- **Enforce a tight id charset.      The id becomes a filename + a URL path segment;** (1 connections) — `src/hal0/mcp/installed.py`
- **Return every installed-server record, sorted by id.      Missing dir → empty lis** (1 connections) — `src/hal0/mcp/installed.py`
- **Return one installed-server record. Raises :class:`NotFound`.** (1 connections) — `src/hal0/mcp/installed.py`
- **Write a new installed-server record. Raises :class:`Conflict` on dup.      The c** (1 connections) — `src/hal0/mcp/installed.py`
- **Remove the installed-server record. Raises :class:`NotFound`.      Bundled serve** (1 connections) — `src/hal0/mcp/installed.py`
- **Advisory exclusive lock serializing read-modify-write on one server's     regist** (1 connections) — `src/hal0/mcp/installed.py`
- **Merge env / enabled overrides into a registry record.      Replaces the record's** (1 connections) — `src/hal0/mcp/installed.py`
- *... and 3 more nodes in this community*

## Relationships

- [BoardStore](BoardStore.md) (4 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [test_installed.py](test_installed.py.md) (1 shared connections)

## Source Files

- `src/hal0/mcp/installed.py`

## Audit Trail

- EXTRACTED: 100 (93%)
- INFERRED: 8 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*