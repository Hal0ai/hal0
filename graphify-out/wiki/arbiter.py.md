# arbiter.py

> 18 nodes

## Key Concepts

- **arbiter.py** (13 connections) — `src/hal0/slots/arbiter.py`
- **GpuMode** (7 connections) — `src/hal0/slots/arbiter.py`
- **GpuInferenceMode** (7 connections) — `src/hal0/slots/arbiter.py`
- **ArbiterPinned** (7 connections) — `src/hal0/slots/arbiter.py`
- **test_guard_dispatch_blocks_img_slot_in_llm_mode()** (6 connections) — `tests/slots/test_gpu_arbiter.py`
- **GpuImgNotReady** (5 connections) — `src/hal0/slots/arbiter.py`
- **test_restore_blocked_when_pinned_unless_force()** (5 connections) — `tests/slots/test_gpu_arbiter.py`
- **_comfyui_base_url()** (4 connections) — `src/hal0/slots/arbiter.py`
- **_comfyui_free()** (4 connections) — `src/hal0/slots/arbiter.py`
- **_comfyui_queue_counts()** (4 connections) — `src/hal0/slots/arbiter.py`
- **GpuArbiter — exclusive llm/img GPU group arbitration (spec §7, Phase D).  Strix** (1 connections) — `src/hal0/slots/arbiter.py`
- **Operational ComfyUI HTTP base (mirrors api/routes/comfyui.py).      Duplicated o** (1 connections) — `src/hal0/slots/arbiter.py`
- **Best-effort ``POST /free`` — drop ComfyUI's models from GTT.      True on a 200;** (1 connections) — `src/hal0/slots/arbiter.py`
- **ComfyUI ``GET /queue`` → (running, pending), or None when unreachable.      The** (1 connections) — `src/hal0/slots/arbiter.py`
- **Image dispatch refused — the GPU is serving the LLM set.      The resident Comfy** (1 connections) — `src/hal0/slots/arbiter.py`
- **Restore refused — image mode is manually pinned (force to override).** (1 connections) — `src/hal0/slots/arbiter.py`
- **Image slot did not become ready within the readiness timeout.      Raised when t** (1 connections) — `src/hal0/slots/arbiter.py`
- **Resident img container: the slot stays READY in llm mode, so the     dispatch gu** (1 connections) — `tests/slots/test_gpu_arbiter.py`

## Relationships

- [GpuArbiter](GpuArbiter.md) (10 shared connections)
- [FakeManager](FakeManager.md) (9 shared connections)
- [SlotState](SlotState.md) (4 shared connections)
- [Hal0Error](Hal0Error.md) (3 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Enum](Enum.md) (1 shared connections)
- [GpuImageMode](GpuImageMode.md) (1 shared connections)
- [die](die.md) (1 shared connections)

## Source Files

- `src/hal0/slots/arbiter.py`
- `tests/slots/test_gpu_arbiter.py`

## Audit Trail

- EXTRACTED: 57 (81%)
- INFERRED: 13 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*