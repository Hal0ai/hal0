# load_profiles_config

> 41 nodes · cohesion 0.09

## Key Concepts

- **load_profiles_config()** (40 connections) — `src/hal0/config/loader.py`
- **Path** (19 connections)
- **TestLoadProfilesConfig** (17 connections) — `tests/config/test_profiles.py`
- **test_profiles.py** (12 connections) — `tests/config/test_profiles.py`
- **TestSeedFileParity** (6 connections) — `tests/config/test_profiles.py`
- **.test_missing_image_raises_config_parse_error()** (5 connections) — `tests/config/test_profiles.py`
- **.test_unknown_field_raises_config_parse_error()** (5 connections) — `tests/config/test_profiles.py`
- **test_cpu_default_profile_supports_llm()** (4 connections) — `tests/config/test_profiles.py`
- **.test_complete_seed_file_no_extras_added()** (4 connections) — `tests/config/test_profiles.py`
- **.test_invalid_toml_raises_config_parse_error()** (4 connections) — `tests/config/test_profiles.py`
- **.test_materialised_seed_on_disk_is_overwritten_by_code()** (4 connections) — `tests/config/test_profiles.py`
- **.test_missing_file_returns_seeds()** (4 connections) — `tests/config/test_profiles.py`
- **.test_partial_file_custom_profile_preserved()** (4 connections) — `tests/config/test_profiles.py`
- **.test_partial_file_gets_missing_seeds_merged_in()** (4 connections) — `tests/config/test_profiles.py`
- **.test_seed_vulkan_uses_rocmfpx_default()** (4 connections) — `tests/config/test_profiles.py`
- **.test_load_valid_file()** (3 connections) — `tests/config/test_profiles.py`
- **.test_seed_count()** (3 connections) — `tests/config/test_profiles.py`
- **.test_seed_gpu_profiles_have_backend()** (3 connections) — `tests/config/test_profiles.py`
- **.test_seed_profiles_have_correct_names()** (3 connections) — `tests/config/test_profiles.py`
- **.test_seed_rocm_mtp_false()** (3 connections) — `tests/config/test_profiles.py`
- **.test_seed_rocmfpx_grid_mtp_true()** (3 connections) — `tests/config/test_profiles.py`
- **test_profile_device_class_defaults_gpu()** (2 connections) — `tests/config/test_profiles.py`
- **.seed_file()** (2 connections) — `tests/config/test_profiles.py`
- **.test_seed_file_exists()** (2 connections) — `tests/config/test_profiles.py`
- **.test_seed_file_materialises_no_seeds()** (2 connections) — `tests/config/test_profiles.py`
- *... and 16 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (6 shared connections)
- [save_profiles_config](save_profiles_config.md) (6 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (6 shared connections)
- [ProfileConfig](ProfileConfig.md) (4 shared connections)
- [load_hal0_config](load_hal0_config.md) (3 shared connections)
- [KeyError](KeyError.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [container_enrichment](container_enrichment.md) (1 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)
- [image_pull.py](image_pull.py.md) (1 shared connections)
- [embed_references](embed_references.md) (1 shared connections)
- [portable.py](portable.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `tests/config/test_profiles.py`

## Audit Trail

- EXTRACTED: 124 (70%)
- INFERRED: 54 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*