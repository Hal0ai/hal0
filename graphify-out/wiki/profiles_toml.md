# profiles_toml

> 15 nodes

## Key Concepts

- **profiles_toml()** (12 connections) — `src/hal0/config/paths.py`
- **ensure_seed_profiles()** (12 connections) — `src/hal0/updater/updater.py`
- **test_seed_profiles_migration.py** (8 connections) — `tests/updater/test_seed_profiles_migration.py`
- **_write_profiles()** (6 connections) — `tests/updater/test_seed_profiles_migration.py`
- **_seed_table()** (5 connections) — `tests/updater/test_seed_profiles_migration.py`
- **test_identical_materialised_seed_is_pruned()** (5 connections) — `tests/updater/test_seed_profiles_migration.py`
- **test_divergent_seed_named_entry_is_rescued_not_deleted()** (5 connections) — `tests/updater/test_seed_profiles_migration.py`
- **test_prune_writes_backup_once()** (5 connections) — `tests/updater/test_seed_profiles_migration.py`
- **test_operator_profiles_pass_through()** (5 connections) — `tests/updater/test_seed_profiles_migration.py`
- **test_absent_file_is_noop()** (3 connections) — `tests/updater/test_seed_profiles_migration.py`
- **Return the profile catalog path (/etc/hal0/profiles.toml).      The file is opti** (1 connections) — `src/hal0/config/paths.py`
- **Prune materialised seed profiles from /etc/hal0/profiles.toml.      Seeds are **** (1 connections) — `src/hal0/updater/updater.py`
- **ensure_seed_profiles — the virtual-seed prune/rescue migration.  Seeds are virtu** (1 connections) — `tests/updater/test_seed_profiles_migration.py`
- **Render a seed profile as TOML, byte-identical to SEED_PROFILES.** (1 connections) — `tests/updater/test_seed_profiles_migration.py`
- **An operator profile that collides with a (possibly newer) seed name is     renam** (1 connections) — `tests/updater/test_seed_profiles_migration.py`

## Relationships

- [paths.py](paths.py.md) (3 shared connections)
- [slots_config_dir](slots_config_dir.md) (2 shared connections)
- [load_profiles_config](load_profiles_config.md) (2 shared connections)
- [updater.py](updater.py.md) (2 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/paths.py`
- `src/hal0/updater/updater.py`
- `tests/updater/test_seed_profiles_migration.py`

## Audit Trail

- EXTRACTED: 44 (62%)
- INFERRED: 27 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*