# SlotState

> God node · 105 connections · `src/hal0/slots/state.py`

**Community:** [SlotState](SlotState.md)

## Connections by Relation

### calls
- .from_dict() `EXTRACTED`
- _make_slot() `INFERRED`

### contains
- state.py `EXTRACTED`

### inherits
- StrEnum `EXTRACTED`

### rationale_for
- Lifecycle states for a hal0 inference slot.      Each value is also its JSON/SSE `EXTRACTED`

### references
- _slot() `EXTRACTED`
- ._transition() `EXTRACTED`
- .state() `EXTRACTED`
- ._current_state() `EXTRACTED`
- ._await_ready() `EXTRACTED`
- test_forward_gates_slot_in_loading_state() `EXTRACTED`
- test_forward_passes_through_ready_states() `EXTRACTED`
- _await_state() `EXTRACTED`
- is_dispatchable_state() `EXTRACTED`
- is_transition_legal() `EXTRACTED`
- _wait_for_state() `EXTRACTED`
- ._transition() `EXTRACTED`
- .update() `EXTRACTED`
- ._transition() `EXTRACTED`
- _slot() `EXTRACTED`
- test_is_ready_for_dispatch_parametrized() `EXTRACTED`
- ._current_state() `EXTRACTED`
- .__init__() `EXTRACTED`
- _write_state() `EXTRACTED`
- .__init__() `EXTRACTED`

### uses
- [SlotManager](SlotManager.md) `INFERRED`
- [Dispatcher](Dispatcher.md) `INFERRED`
- [GpuArbiter](GpuArbiter.md) `INFERRED`
- [StackApplyEngine](StackApplyEngine.md) `INFERRED`
- [UpstreamCall](UpstreamCall.md) `INFERRED`
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) `INFERRED`
- Slot `INFERRED`
- FakeModelRegistry `INFERRED`
- _RecordingSlotManager `INFERRED`
- UpstreamUnavailable `INFERRED`
- [_ArbiterSlotManager](_ArbiterSlotManager.md) `INFERRED`
- NoRouteFound `INFERRED`
- [FakeSnap](FakeSnap.md) `INFERRED`
- [FakeContainerProvider](FakeContainerProvider.md) `INFERRED`
- [SlotLoading](SlotLoading.md) `INFERRED`
- SlotReaper `INFERRED`
- [FakeSlotManager](FakeSlotManager.md) `INFERRED`
- RecordingOrchestrator `INFERRED`
- [ReaperHost](ReaperHost.md) `INFERRED`
- [_FakeSlotManager](_FakeSlotManager.md) `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*