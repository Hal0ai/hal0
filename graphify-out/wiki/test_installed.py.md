# test_installed.py

> 27 nodes · cohesion 0.11

## Key Concepts

- **test_installed.py** (20 connections) — `tests/mcp/test_installed.py`
- **_record()** (15 connections) — `tests/mcp/test_installed.py`
- **NotFound** (14 connections) — `tests/memory/test_hindsight_provider.py`
- **test_validate_id_rejects_path_traversal()** (4 connections) — `tests/mcp/test_installed.py`
- **test_install_rejects_bad_id_charset()** (3 connections) — `tests/mcp/test_installed.py`
- **test_install_rejects_bundled_id()** (3 connections) — `tests/mcp/test_installed.py`
- **test_install_rejects_duplicate()** (3 connections) — `tests/mcp/test_installed.py`
- **test_install_writes_restrictive_permissions()** (3 connections) — `tests/mcp/test_installed.py`
- **test_patch_config_locked_rmw_applies()** (3 connections) — `tests/mcp/test_installed.py`
- **test_uninstall_bundled_id_rejected_at_registry_layer()** (3 connections) — `tests/mcp/test_installed.py`
- **test_get_installed_missing_raises_not_found()** (2 connections) — `tests/mcp/test_installed.py`
- **test_install_and_list_round_trip()** (2 connections) — `tests/mcp/test_installed.py`
- **test_list_installed_tolerates_malformed_file()** (2 connections) — `tests/mcp/test_installed.py`
- **test_patch_config_coerces_env_values()** (2 connections) — `tests/mcp/test_installed.py`
- **test_patch_config_noop_returns_record()** (2 connections) — `tests/mcp/test_installed.py`
- **test_patch_config_replaces_env()** (2 connections) — `tests/mcp/test_installed.py`
- **test_patch_config_toggles_enabled()** (2 connections) — `tests/mcp/test_installed.py`
- **test_uninstall_missing_raises_not_found()** (2 connections) — `tests/mcp/test_installed.py`
- **test_uninstall_round_trip()** (2 connections) — `tests/mcp/test_installed.py`
- **.__init__()** (2 connections) — `tests/memory/test_hindsight_provider.py`
- **Unit tests for :mod:`hal0.mcp.installed` — #305 registry layer.** (1 connections) — `tests/mcp/test_installed.py`
- **Registry TOMLs hold env blocks (API keys); they must be 0o600 + dir 0o700.** (1 connections) — `tests/mcp/test_installed.py`
- **Calling ``installed.uninstall("hal0-admin")`` rejects before disk lookup.      B** (1 connections) — `tests/mcp/test_installed.py`
- **``id="../evil"`` must reject at the registry validator, not after stat.      Eve** (1 connections) — `tests/mcp/test_installed.py`
- **#382: patch_config wraps its read-modify-write in an advisory lock.      Functio** (1 connections) — `tests/mcp/test_installed.py`
- *... and 2 more nodes in this community*

## Relationships

- [BoardStore](BoardStore.md) (6 shared connections)
- [test_hindsight_provider.py](test_hindsight_provider.py.md) (3 shared connections)
- [StacksCatalog](StacksCatalog.md) (3 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [installed.py](installed.py.md) (1 shared connections)
- [HindsightProvider](HindsightProvider.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `tests/mcp/test_installed.py`
- `tests/memory/test_hindsight_provider.py`

## Audit Trail

- EXTRACTED: 80 (82%)
- INFERRED: 18 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*