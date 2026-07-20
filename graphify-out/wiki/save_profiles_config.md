# save_profiles_config

> 18 nodes · cohesion 0.16

## Key Concepts

- **save_profiles_config()** (16 connections) — `src/hal0/config/loader.py`
- **ProfilesConfig** (11 connections) — `src/hal0/config/schema.py`
- **.test_embeds_referenced_profile()** (8 connections) — `tests/stacks/test_export.py`
- **TestSaveProfilesConfig** (6 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_omits_virtual_seeds()** (6 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_accepts_explicit_path()** (5 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_overwrites_previous_file()** (4 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_profiles_config_round_trips()** (4 connections) — `tests/config/test_loader_profiles_save.py`
- **TestProfilesConfig** (4 connections) — `tests/config/test_profiles.py`
- **.test_save_uses_profiles_toml_path_by_default()** (3 connections) — `tests/config/test_loader_profiles_save.py`
- **test_loader_profiles_save.py** (2 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_empty_profiles()** (2 connections) — `tests/config/test_profiles.py`
- **.test_parse_from_dict()** (2 connections) — `tests/config/test_profiles.py`
- **Atomically write the operator (non-seed) profile catalog to profiles.toml.** (1 connections) — `src/hal0/config/loader.py`
- **Parsed profiles.toml — top-level ``[profile]`` table.      Each key under ``[pro** (1 connections) — `src/hal0/config/schema.py`
- **Path** (1 connections)
- **Tests for save_profiles_config — round-trip + atomicity.  Targeted file run:** (1 connections) — `tests/config/test_loader_profiles_save.py`
- **Seeds are virtual: save persists only operator (non-seed) profiles.** (1 connections) — `tests/config/test_loader_profiles_save.py`

## Relationships

- [load_profiles_config](load_profiles_config.md) (6 shared connections)
- [ProfileConfig](ProfileConfig.md) (5 shared connections)
- [load_hal0_config](load_hal0_config.md) (3 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (3 shared connections)
- [embed_references](embed_references.md) (3 shared connections)
- [profiles_toml](profiles_toml.md) (2 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [StackConfig](StackConfig.md) (2 shared connections)
- [portable.py](portable.py.md) (1 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `tests/config/test_loader_profiles_save.py`
- `tests/config/test_profiles.py`
- `tests/stacks/test_export.py`

## Audit Trail

- EXTRACTED: 45 (58%)
- INFERRED: 33 (42%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*