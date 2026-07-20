# SlotManagerLike

> 19 nodes

## Key Concepts

- **SlotManagerLike** (13 connections) — `src/hal0/omni_router/filter.py`
- **chat_slot_has_tool_calling()** (9 connections) — `src/hal0/omni_router/filter.py`
- **.__init__()** (4 connections) — `src/hal0/omni_router/dispatch.py`
- **filter.py** (4 connections) — `src/hal0/omni_router/filter.py`
- **Any** (4 connections)
- **.iter_configs()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.loaded_slot()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.resolve_for_request()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.__init__()** (3 connections) — `src/hal0/omni_router/router.py`
- **test_chat_slot_has_tool_calling_true()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_chat_slot_has_tool_calling_false_no_labels()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_chat_slot_has_tool_calling_false_wrong_label()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_chat_slot_has_tool_calling_prefers_model_info()** (3 connections) — `tests/omni_router/test_filter_no_labels.py`
- **AsyncClient** (1 connections)
- **ChatCompletionFn** (1 connections)
- **Dynamic per-request tool filtering — plan §7.3.  Given the active chat slot and** (1 connections) — `src/hal0/omni_router/filter.py`
- **The narrow SlotManager surface filter.py + dispatch.py need.      Stated as a Pr** (1 connections) — `src/hal0/omni_router/filter.py`
- **Return True iff the chat slot's model is allowed to see tools.      Per plan §7.** (1 connections) — `src/hal0/omni_router/filter.py`
- **AsyncClient** (1 connections)

## Relationships

- [FakeSlotManager](FakeSlotManager.md) (10 shared connections)
- [make_slot](make_slot.md) (4 shared connections)
- [OmniRouter](OmniRouter.md) (3 shared connections)
- [DispatchContext](DispatchContext.md) (2 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (2 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/dispatch.py`
- `src/hal0/omni_router/filter.py`
- `src/hal0/omni_router/router.py`
- `tests/omni_router/test_filter.py`
- `tests/omni_router/test_filter_no_labels.py`

## Audit Trail

- EXTRACTED: 50 (78%)
- INFERRED: 14 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*