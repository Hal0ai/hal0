# NpuExclusivityViolation

> 21 nodes · cohesion 0.14

## Key Concepts

- **NpuExclusivityViolation** (10 connections) — `src/hal0/slots/state.py`
- **test_npu_exclusivity.py** (9 connections) — `tests/slots/test_npu_exclusivity.py`
- **_write_slot_toml()** (9 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_create_rejects_second_enabled_npu_llm()** (6 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_create_allows_disabled_second_npu_llm()** (5 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_update_config_rejects_enabling_second_npu_llm()** (5 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_create_allows_non_npu_slot_alongside_npu_llm()** (4 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_create_allows_npu_embedding_or_transcription_alongside_npu_llm()** (4 connections) — `tests/slots/test_npu_exclusivity.py`
- **test_update_config_self_idempotent_when_no_conflict()** (4 connections) — `tests/slots/test_npu_exclusivity.py`
- **Path** (3 connections)
- **test_create_allows_first_npu_llm_in_clean_home()** (3 connections) — `tests/slots/test_npu_exclusivity.py`
- **Two ``device=npu, type=llm, enabled=true`` slots cannot coexist.      The AMDXDN** (1 connections) — `src/hal0/slots/state.py`
- **NPU exclusivity validation in SlotManager (PR-11, plan §5.3, ADR-0008 §5).  The** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **A disabled second NPU LLM slot may coexist with an enabled one.** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **device=gpu-rocm slots are unaffected by NPU exclusivity.** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **Only ``type=llm`` slots claim the AMDXDNA chat context.      The FLM trio (stt-n** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **Updating the lone NPU LLM slot's own fields does NOT trip the guard.      The gu** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **Write a minimal slot TOML under HAL0_HOME without going through SlotManager.** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **The very first NPU LLM slot must succeed.** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **A second device=npu, type=llm, enabled=true slot must be rejected.** (1 connections) — `tests/slots/test_npu_exclusivity.py`
- **Flipping ``enabled=false → true`` on a sibling NPU LLM is blocked.** (1 connections) — `tests/slots/test_npu_exclusivity.py`

## Relationships

- [SlotManager](SlotManager.md) (7 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (4 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)

## Source Files

- `src/hal0/slots/state.py`
- `tests/slots/test_npu_exclusivity.py`

## Audit Trail

- EXTRACTED: 56 (78%)
- INFERRED: 16 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*