# load_manifest

> 25 nodes · cohesion 0.13

## Key Concepts

- **load_manifest()** (14 connections) — `src/hal0/config/loader.py`
- **TestManifestLoader** (14 connections) — `tests/config/test_loader.py`
- **manifest_image_ref()** (9 connections) — `src/hal0/config/loader.py`
- **._write_manifest()** (7 connections) — `tests/config/test_loader.py`
- **Any** (6 connections)
- **_flatten_slot_toml()** (5 connections) — `src/hal0/config/loader.py`
- **_unflatten_slot_toml()** (5 connections) — `src/hal0/config/loader.py`
- **.test_etc_manifest_overrides_release_tree()** (5 connections) — `tests/config/test_loader.py`
- **._write_tree_manifest()** (5 connections) — `tests/config/test_loader.py`
- **.test_load_manifest_falls_back_to_release_tree()** (4 connections) — `tests/config/test_loader.py`
- **.test_manifest_parse_error_raises()** (4 connections) — `tests/config/test_loader.py`
- **.test_load_manifest_round_trip()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_digest_pinned()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_falls_back_to_tag()** (3 connections) — `tests/config/test_loader.py`
- **.test_manifest_image_ref_missing_returns_none()** (3 connections) — `tests/config/test_loader.py`
- **test_manifest_comfyui_pinned_to_kyuz0()** (3 connections) — `tests/config/test_schema_seeds_d1.py`
- **.test_load_manifest_missing_returns_empty()** (2 connections) — `tests/config/test_loader.py`
- **Normalise both slot-TOML shapes into the flat SlotConfig shape.      Two on-disk** (1 connections) — `src/hal0/config/loader.py`
- **Inverse of _flatten_slot_toml — produce the on-disk shape.      Writes only ``de** (1 connections) — `src/hal0/config/loader.py`
- **Load the release manifest.      `scripts/update-toolbox-digests.sh` patches `too** (1 connections) — `src/hal0/config/loader.py`
- **Return the pinned image reference for a toolbox image, if any.      Resolution:** (1 connections) — `src/hal0/config/loader.py`
- **Covers the toolbox-image manifest reader used at slot-spawn time.      The manif** (1 connections) — `tests/config/test_loader.py`
- **Write a manifest into the release-tree slot (usr_lib/current).** (1 connections) — `tests/config/test_loader.py`
- **With no /etc override, the manifest ships inside the current         release tre** (1 connections) — `tests/config/test_loader.py`
- **/etc/hal0/manifest.json is a deliberate operator override and         wins over** (1 connections) — `tests/config/test_loader.py`

## Relationships

- [load_hal0_config](load_hal0_config.md) (13 shared connections)
- [load_slot_config](load_slot_config.md) (3 shared connections)
- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [heal_missing_llm_type](heal_missing_llm_type.md) (1 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema_seeds_d1.py`

## Audit Trail

- EXTRACTED: 77 (75%)
- INFERRED: 26 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*