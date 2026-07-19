# model_store_root

> 27 nodes · cohesion 0.15

## Key Concepts

- **model_store_root()** (16 connections) — `src/hal0/config/paths.py`
- **_build_spec()** (10 connections) — `tests/providers/test_container_chat_template.py`
- **TestContainerSpecChatTemplate** (9 connections) — `tests/providers/test_container_chat_template.py`
- **test_container_chat_template.py** (8 connections) — `tests/providers/test_container_chat_template.py`
- **_model_info()** (8 connections) — `tests/providers/test_container_chat_template.py`
- **_slot_cfg()** (8 connections) — `tests/providers/test_container_chat_template.py`
- **.test_model_defaults_chat_template_emitted_when_no_slot_override()** (6 connections) — `tests/providers/test_container_chat_template.py`
- **.test_slot_chat_template_emitted_in_command()** (6 connections) — `tests/providers/test_container_chat_template.py`
- **_moe_profile()** (5 connections) — `tests/providers/test_container_chat_template.py`
- **.test_auto_chat_template_treated_as_none()** (5 connections) — `tests/providers/test_container_chat_template.py`
- **.test_model_auto_chat_template_treated_as_none()** (5 connections) — `tests/providers/test_container_chat_template.py`
- **.test_no_chat_template_flag_when_neither_set()** (5 connections) — `tests/providers/test_container_chat_template.py`
- **.test_slot_override_wins_over_model_default()** (5 connections) — `tests/providers/test_container_chat_template.py`
- **TestLoadSyncChatTemplate** (5 connections) — `tests/providers/test_container_chat_template.py`
- **.test_unit_contains_chat_template_file_flag()** (4 connections) — `tests/providers/test_container_chat_template.py`
- **Any** (3 connections)
- **.test_unit_no_chat_template_flag_when_unset()** (3 connections) — `tests/providers/test_container_chat_template.py`
- **Resolve the model-store directory that slot containers bind-mount.      THIN SHI** (1 connections) — `src/hal0/config/paths.py`
- **Tests: container emits --chat-template-file from resolved chat_template.  Task 3** (1 connections) — `tests/providers/test_container_chat_template.py`
- **container_spec emits --chat-template-file from resolved slot/model template.** (1 connections) — `tests/providers/test_container_chat_template.py`
- **slot_cfg['chat_template'] = 'chatml' → --chat-template-file in command.** (1 connections) — `tests/providers/test_container_chat_template.py`
- **model_info['defaults']['chat_template'] = 'llama3' → flag emitted.** (1 connections) — `tests/providers/test_container_chat_template.py`
- **Slot-level chat_template takes priority over model defaults.** (1 connections) — `tests/providers/test_container_chat_template.py`
- **slot_cfg and model_info both without chat_template → flag absent.** (1 connections) — `tests/providers/test_container_chat_template.py`
- **chat_template='auto' is equivalent to no template — flag absent.** (1 connections) — `tests/providers/test_container_chat_template.py`
- *... and 2 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (5 shared connections)
- [paths.py](paths.py.md) (2 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (2 shared connections)
- [create_chat_template](create_chat_template.md) (1 shared connections)
- [fetch.py](fetch.py.md) (1 shared connections)
- [orchestrate_models](orchestrate_models.md) (1 shared connections)
- [store.py](store.py.md) (1 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (1 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (1 shared connections)
- [seed_chat_templates](seed_chat_templates.md) (1 shared connections)

## Source Files

- `src/hal0/config/paths.py`
- `tests/providers/test_container_chat_template.py`

## Audit Trail

- EXTRACTED: 100 (83%)
- INFERRED: 21 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*