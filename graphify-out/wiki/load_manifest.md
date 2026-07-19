# load_manifest

> 30 nodes

## Key Concepts

- **load_manifest()** (14 connections) — `src/hal0/config/loader.py`
- **TestManifestLoader** (14 connections) — `tests/config/test_loader.py`
- **Path** (10 connections)
- **manifest_image_ref()** (9 connections) — `src/hal0/config/loader.py`
- **TestWriteTomlAtomic** (8 connections) — `tests/config/test_loader.py`
- **._write_manifest()** (7 connections) — `tests/config/test_loader.py`
- **OSError** (7 connections)
- **.test_interrupted_write_leaves_original_intact()** (5 connections) — `tests/config/test_loader.py`
- **.test_interrupted_rename_cleans_up_tmpfile()** (5 connections) — `tests/config/test_loader.py`
- **._write_tree_manifest()** (5 connections) — `tests/config/test_loader.py`
- **.test_etc_manifest_overrides_release_tree()** (5 connections) — `tests/config/test_loader.py`
- **.test_manifest_parse_error_raises()** (4 connections) — `tests/config/test_loader.py`
- **.test_load_manifest_falls_back_to_release_tree()** (4 connections) — `tests/config/test_loader.py`
- **.test_writes_file_with_content()** (3 connections) — `tests/config/test_loader.py`
- **.test_creates_parent_directory()** (3 connections) — `tests/config/test_loader.py`
- **.test_overwrites_existing_file_atomically()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_manifest_round_trip()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_digest_pinned()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_falls_back_to_tag()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_missing_returns_none()** (3 connections) — `tests/config/test_loader.py`
- **test_manifest_comfyui_pinned_to_kyuz0()** (3 connections) — `tests/config/test_schema_seeds_d1.py`
- **.test_load_manifest_missing_returns_empty()** (2 connections) — `tests/config/test_loader.py`
- **Load the release manifest.      `scripts/update-toolbox-digests.sh` patches `too** (1 connections) — `src/hal0/config/loader.py`
- **Return the pinned image reference for a toolbox image, if any.      Resolution:** (1 connections) — `src/hal0/config/loader.py`
- **Tier 1: a mid-write crash leaves the prior file untouched.** (1 connections) — `tests/config/test_loader.py`
- *... and 5 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (20 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [updater.py](updater.py.md) (1 shared connections)
- [.json](json.md) (1 shared connections)
- [test_migrate_model_layout.py](test_migrate_model_layout.py.md) (1 shared connections)
- [test_probes.py](test_probes.py.md) (1 shared connections)
- [ModelRegistry](ModelRegistry.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema_seeds_d1.py`

## Audit Trail

- EXTRACTED: 91 (69%)
- INFERRED: 40 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*