# _build_spec

> 14 nodes · cohesion 0.31

## Key Concepts

- **_build_spec()** (8 connections) — `tests/providers/test_container_vision_toggle.py`
- **test_container_vision_toggle.py** (7 connections) — `tests/providers/test_container_vision_toggle.py`
- **_model_info()** (6 connections) — `tests/providers/test_container_vision_toggle.py`
- **_slot_cfg()** (6 connections) — `tests/providers/test_container_vision_toggle.py`
- **TestVisionToggleGatesMmproj** (6 connections) — `tests/providers/test_container_vision_toggle.py`
- **.test_default_on_emits_mmproj()** (5 connections) — `tests/providers/test_container_vision_toggle.py`
- **.test_vision_false_suppresses_mmproj()** (5 connections) — `tests/providers/test_container_vision_toggle.py`
- **.test_no_sidecar_no_mmproj_regardless()** (4 connections) — `tests/providers/test_container_vision_toggle.py`
- **.test_vision_true_emits_mmproj()** (4 connections) — `tests/providers/test_container_vision_toggle.py`
- **_moe_profile()** (3 connections) — `tests/providers/test_container_vision_toggle.py`
- **Any** (3 connections)
- **Per-slot `vision` toggle gates the --mmproj emit (#901).  The container provider** (1 connections) — `tests/providers/test_container_vision_toggle.py`
- **No explicit vision flag + sidecar present → --mmproj emitted.** (1 connections) — `tests/providers/test_container_vision_toggle.py`
- **vision=false → text-only, no --mmproj even though a sidecar exists.** (1 connections) — `tests/providers/test_container_vision_toggle.py`

## Relationships

- [ContainerProvider](ContainerProvider.md) (2 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `tests/providers/test_container_vision_toggle.py`

## Audit Trail

- EXTRACTED: 58 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*