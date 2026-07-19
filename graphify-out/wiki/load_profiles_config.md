# load_profiles_config

> 61 nodes

## Key Concepts

- **load_profiles_config()** (40 connections) — `src/hal0/config/loader.py`
- **Path** (19 connections)
- **TestLoadProfilesConfig** (17 connections) — `tests/config/test_profiles.py`
- **save_profiles_config()** (16 connections) — `src/hal0/config/loader.py`
- **test_profiles.py** (12 connections) — `tests/config/test_profiles.py`
- **ProfilesConfig** (11 connections) — `src/hal0/config/schema.py`
- **.test_embeds_referenced_profile()** (7 connections) — `tests/stacks/test_export.py`
- **TestSaveProfilesConfig** (6 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_omits_virtual_seeds()** (6 connections) — `tests/config/test_loader_profiles_save.py`
- **TestSeedFileParity** (6 connections) — `tests/config/test_profiles.py`
- **resolve_profile()** (5 connections) — `src/hal0/config/loader.py`
- **.test_save_accepts_explicit_path()** (5 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_missing_image_raises_config_parse_error()** (5 connections) — `tests/config/test_profiles.py`
- **.test_unknown_field_raises_config_parse_error()** (5 connections) — `tests/config/test_profiles.py`
- **.test_save_profiles_config_round_trips()** (4 connections) — `tests/config/test_loader_profiles_save.py`
- **.test_save_overwrites_previous_file()** (4 connections) — `tests/config/test_loader_profiles_save.py`
- **TestProfilesConfig** (4 connections) — `tests/config/test_profiles.py`
- **.test_missing_file_returns_seeds()** (4 connections) — `tests/config/test_profiles.py`
- **.test_seed_vulkan_uses_rocmfpx_default()** (4 connections) — `tests/config/test_profiles.py`
- **.test_invalid_toml_raises_config_parse_error()** (4 connections) — `tests/config/test_profiles.py`
- **.test_partial_file_gets_missing_seeds_merged_in()** (4 connections) — `tests/config/test_profiles.py`
- **.test_materialised_seed_on_disk_is_overwritten_by_code()** (4 connections) — `tests/config/test_profiles.py`
- **.test_partial_file_custom_profile_preserved()** (4 connections) — `tests/config/test_profiles.py`
- **.test_complete_seed_file_no_extras_added()** (4 connections) — `tests/config/test_profiles.py`
- **test_cpu_default_profile_supports_llm()** (4 connections) — `tests/config/test_profiles.py`
- *... and 36 more nodes in this community*

## Relationships

- [ProfileConfig](ProfileConfig.md) (18 shared connections)
- [ConfigParseError](ConfigParseError.md) (14 shared connections)
- [embed_references](embed_references.md) (6 shared connections)
- [profiles_toml](profiles_toml.md) (2 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [container_enrichment](container_enrichment.md) (1 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)
- [image_pull.py](image_pull.py.md) (1 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (1 shared connections)
- [ModelRegistry](ModelRegistry.md) (1 shared connections)
- [KeyError](KeyError.md) (1 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `tests/config/test_loader_profiles_save.py`
- `tests/config/test_profiles.py`
- `tests/stacks/test_export.py`

## Audit Trail

- EXTRACTED: 174 (67%)
- INFERRED: 87 (33%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*