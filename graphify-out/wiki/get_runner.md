# get_runner

> 66 nodes

## Key Concepts

- **get_runner()** (26 connections) — `src/hal0/runners/__init__.py`
- **resolve_runner_image()** (24 connections) — `src/hal0/runners/__init__.py`
- **test_model_preferred_runner.py** (13 connections) — `tests/slots/test_model_preferred_runner.py`
- **test_registry.py** (11 connections) — `tests/runners/test_registry.py`
- **test_resolve_image.py** (10 connections) — `tests/runners/test_resolve_image.py`
- **_register()** (10 connections) — `tests/slots/test_model_preferred_runner.py`
- **_gpu_vulkan_cfg()** (10 connections) — `tests/slots/test_model_preferred_runner.py`
- **resolve_default_image()** (9 connections) — `src/hal0/config/schema.py`
- **__init__.py** (8 connections) — `src/hal0/runners/__init__.py`
- **Runner** (7 connections) — `src/hal0/runners/__init__.py`
- **runner_for_backend()** (7 connections) — `src/hal0/runners/__init__.py`
- **runner_matches()** (7 connections) — `src/hal0/runners/__init__.py`
- **.image_ref()** (6 connections) — `src/hal0/providers/flm.py`
- **.image_ref()** (6 connections) — `src/hal0/providers/kokoro.py`
- **.image_ref()** (6 connections) — `src/hal0/providers/qwen3tts.py`
- **test_create_adopts_compatible_preferred_runner()** (6 connections) — `tests/slots/test_model_preferred_runner.py`
- **test_apply_preferred_runner_swaps_when_compatible()** (6 connections) — `tests/slots/test_model_preferred_runner.py`
- **test_apply_preferred_runner_noop_when_already_adopted()** (6 connections) — `tests/slots/test_model_preferred_runner.py`
- **test_default_image_gate.py** (5 connections) — `tests/config/test_default_image_gate.py`
- **_write_manifest()** (5 connections) — `tests/runners/test_resolve_image.py`
- **test_no_manifest_key_skips_manifest_tier_even_if_manifest_has_a_match()** (5 connections) — `tests/runners/test_resolve_image.py`
- **test_manifest_digest_pin_beats_bundled_default()** (4 connections) — `tests/runners/test_resolve_image.py`
- **test_env_override_beats_manifest_digest_pin()** (4 connections) — `tests/runners/test_resolve_image.py`
- **test_env_override_beats_bundled_default_with_no_manifest_key()** (4 connections) — `tests/runners/test_resolve_image.py`
- **test_manifest_tier_applies_to_every_manifest_backed_runner()** (4 connections) — `tests/runners/test_resolve_image.py`
- *... and 41 more nodes in this community*

## Relationships

- [SlotManager](SlotManager.md) (9 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [write_slot_toml](write_slot_toml.md) (4 shared connections)
- [KokoroProvider](KokoroProvider.md) (3 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (3 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [flm.py](flm.py.md) (2 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (2 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [schema.py](schema.py.md) (1 shared connections)
- [slots_config_dir](slots_config_dir.md) (1 shared connections)
- [FLMProvider](FLMProvider.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/providers/flm.py`
- `src/hal0/providers/kokoro.py`
- `src/hal0/providers/qwen3tts.py`
- `src/hal0/runners/__init__.py`
- `tests/config/test_default_image_gate.py`
- `tests/runners/test_registry.py`
- `tests/runners/test_resolve_image.py`
- `tests/slots/test_model_preferred_runner.py`

## Audit Trail

- EXTRACTED: 181 (63%)
- INFERRED: 108 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*