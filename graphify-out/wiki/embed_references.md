# embed_references

> 28 nodes · cohesion 0.13

## Key Concepts

- **embed_references()** (19 connections) — `src/hal0/stacks/portable.py`
- **ModelRegistry** (10 connections)
- **_stack()** (10 connections) — `tests/stacks/test_export.py`
- **StackCapabilityRow** (9 connections) — `src/hal0/config/schema.py`
- **TestEmbedReferences** (9 connections) — `tests/stacks/test_export.py`
- **.test_coordless_registry_row_filled_from_curated()** (7 connections) — `tests/stacks/test_export.py`
- **.test_coordless_row_without_curated_stays_empty()** (7 connections) — `tests/stacks/test_export.py`
- **test_export.py** (5 connections) — `tests/stacks/test_export.py`
- **_referenced_profile_names()** (4 connections) — `src/hal0/stacks/portable.py`
- **.test_embeds_registry_model_metadata()** (4 connections) — `tests/stacks/test_export.py`
- **.test_missing_model_embedded_as_bare_id()** (4 connections) — `tests/stacks/test_export.py`
- **.test_mmproj_is_presence_marker_not_path()** (4 connections) — `tests/stacks/test_export.py`
- **.test_stamps_hal0_version()** (4 connections) — `tests/stacks/test_export.py`
- **TestExportEnvelope** (4 connections) — `tests/stacks/test_export.py`
- **TestStackCapabilityRow** (3 connections) — `tests/config/test_stacks_schema.py`
- **Path** (3 connections)
- **reg()** (3 connections) — `tests/stacks/test_export.py`
- **.test_checksum_is_deterministic_and_ignores_exported_at()** (3 connections) — `tests/stacks/test_export.py`
- **.test_envelope_shape()** (3 connections) — `tests/stacks/test_export.py`
- **.test_bad_device_raises()** (2 connections) — `tests/config/test_stacks_schema.py`
- **.test_valid_row()** (2 connections) — `tests/config/test_stacks_schema.py`
- **One (slot, child) capability selection carried by a stack slot entry.      Mirro** (1 connections) — `src/hal0/config/schema.py`
- **.device_valid()** (1 connections) — `src/hal0/config/schema.py`
- **Every profile name a stack's slots reference.** (1 connections) — `src/hal0/stacks/portable.py`
- **Return a copy of ``stack`` with ``models``/``profiles`` populated.      Model me** (1 connections) — `src/hal0/stacks/portable.py`
- *... and 3 more nodes in this community*

## Relationships

- [StackConfig](StackConfig.md) (9 shared connections)
- [portable.py](portable.py.md) (8 shared connections)
- [save_profiles_config](save_profiles_config.md) (3 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [StackModelMeta](StackModelMeta.md) (2 shared connections)
- [Model](Model.md) (2 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)
- [get_curated](get_curated.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/stacks/portable.py`
- `tests/config/test_stacks_schema.py`
- `tests/stacks/test_export.py`

## Audit Trail

- EXTRACTED: 97 (77%)
- INFERRED: 29 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*