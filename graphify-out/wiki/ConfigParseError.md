# ConfigParseError

> 93 nodes · cohesion 0.04

## Key Concepts

- **ConfigParseError** (53 connections) — `src/hal0/config/loader.py`
- **updater.py** (46 connections) — `src/hal0/updater/updater.py`
- **.commit()** (22 connections) — `src/hal0/updater/updater.py`
- **Path** (20 connections)
- **UpdateError** (19 connections) — `src/hal0/updater/updater.py`
- **.prepare()** (19 connections) — `src/hal0/updater/updater.py`
- **.rollback()** (14 connections) — `src/hal0/updater/updater.py`
- **_parse_manifest()** (12 connections) — `src/hal0/updater/updater.py`
- **UpdateManifestInvalid** (11 connections) — `src/hal0/updater/updater.py`
- **fetch_release_manifest()** (10 connections) — `src/hal0/updater/updater.py`
- **_is_editable_install()** (10 connections) — `src/hal0/updater/updater.py`
- **clear_stale_mtp_overrides()** (9 connections) — `src/hal0/updater/updater.py`
- **Any** (9 connections)
- **_raise_if_editable_install()** (9 connections) — `src/hal0/updater/updater.py`
- **_atomic_symlink_swap()** (8 connections) — `src/hal0/updater/updater.py`
- **ReleaseManifest** (8 connections) — `src/hal0/updater/updater.py`
- **_previous_record()** (7 connections) — `src/hal0/updater/updater.py`
- **_reinstall_into_venv()** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateCosignFailed** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateCosignMissing** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateDownloadError** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateExtractError** (7 connections) — `src/hal0/updater/updater.py`
- **.check()** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateRollbackUnavailable** (7 connections) — `src/hal0/updater/updater.py`
- **UpdateSwapError** (7 connections) — `src/hal0/updater/updater.py`
- *... and 68 more nodes in this community*

## Relationships

- [test_updater.py](test_updater.py.md) (36 shared connections)
- [load_hal0_config](load_hal0_config.md) (19 shared connections)
- [ContainerProvider](ContainerProvider.md) (11 shared connections)
- [load_profiles_config](load_profiles_config.md) (6 shared connections)
- [StacksCatalog](StacksCatalog.md) (4 shared connections)
- [run_migrations](run_migrations.md) (4 shared connections)
- [updater.py](updater.py.md) (4 shared connections)
- [HardwareInfo](HardwareInfo.md) (3 shared connections)
- [load_manifest](load_manifest.md) (3 shared connections)
- [load_slot_config](load_slot_config.md) (3 shared connections)
- [profiles_toml](profiles_toml.md) (3 shared connections)
- [releases_url](releases_url.md) (3 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/updater/updater.py`
- `tests/updater/test_updater.py`

## Audit Trail

- EXTRACTED: 380 (76%)
- INFERRED: 117 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*