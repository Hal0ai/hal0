# _SlotManager

> 8 nodes · cohesion 0.29

## Key Concepts

- **_SlotManager** (8 connections) — `tests/api/test_chat_normalization.py`
- **TestRestart** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_restart_uses_slot_manager_img_restart()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_restart_background_failure_is_fail_soft()** (2 connections) — `tests/api/test_comfyui_phase4.py`
- **.__init__()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.iter_configs()** (1 connections) — `tests/api/test_chat_normalization.py`
- **restart must use the slot-owned img runtime, not /opt/comfyui scripts.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_restart_returns_503_when_slot_manager_unavailable()** (1 connections) — `tests/api/test_comfyui_phase4.py`

## Relationships

- [test_chat_normalization.py](test_chat_normalization.py.md) (2 shared connections)
- [SlotView](SlotView.md) (1 shared connections)
- [_Headers](_Headers.md) (1 shared connections)
- [test_comfyui_phase4.py](test_comfyui_phase4.py.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_normalization.py`
- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 16 (76%)
- INFERRED: 5 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*