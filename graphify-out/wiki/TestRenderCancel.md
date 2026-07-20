# TestRenderCancel

> 5 nodes · cohesion 0.40

## Key Concepts

- **TestRenderCancel** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_cancel_is_fail_soft_when_comfyui_unreachable()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_cancel_posts_clear_and_interrupt_returns_202()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **cancel must POST {base}/queue?clear=true AND {base}/interrupt.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Network errors must still return 202 (fail-soft).** (1 connections) — `tests/api/test_comfyui_phase4.py`

## Relationships

- [TestClient](TestClient.md) (2 shared connections)
- [test_comfyui_phase4.py](test_comfyui_phase4.py.md) (1 shared connections)

## Source Files

- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 11 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*