# test_comfyui_phase4.py

> 18 nodes

## Key Concepts

- **test_comfyui_phase4.py** (11 connections) — `tests/api/test_comfyui_phase4.py`
- **TestLogs** (8 connections) — `tests/api/test_comfyui_phase4.py`
- **_make_proc()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_empty_on_no_journal_entries()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **._make_log_proc()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_returns_lines_list()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_reads_img_slot_journal_unit()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_default_tail_60()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_custom_tail()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_logs_empty_when_no_journalctl()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **_reset_comfyui_state()** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Phase 4 TDD tests — control + monitoring routes for ComfyUI.  Covers:   POST /ap** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Return an AsyncMock that looks like asyncio.Process.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Logs must come from the hal0-slot@img journal, not `podman logs`.          Post-** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Default tail when not supplied must be 60.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **tail= query param must be forwarded to journalctl (-n).** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **If journalctl is not found, return empty lines not a 500.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **journalctl's '-- No entries --' placeholder normalises to [].** (1 connections) — `tests/api/test_comfyui_phase4.py`

## Relationships

- [TestClient](TestClient.md) (9 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [TestRenderCancel](TestRenderCancel.md) (1 shared connections)
- [_SlotManager](_SlotManager.md) (1 shared connections)
- [TestStatusTelemetry](TestStatusTelemetry.md) (1 shared connections)

## Source Files

- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 53 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*