# test_transition_guard.py

> 8 nodes

## Key Concepts

- **test_transition_guard.py** (4 connections) — `tests/slots/test_transition_guard.py`
- **test_transition_blocks_modelless_ready_for_llama_server()** (3 connections) — `tests/slots/test_transition_guard.py`
- **test_transition_allows_modelless_ready_for_kokoro()** (3 connections) — `tests/slots/test_transition_guard.py`
- **test_transition_allows_ready_with_model_id()** (3 connections) — `tests/slots/test_transition_guard.py`
- **Belt-and-suspenders test for the modelless-READY guard in ``_transition``.  Even** (1 connections) — `tests/slots/test_transition_guard.py`
- **READY + empty model_id + llama-server → coerced to IDLE on disk.** (1 connections) — `tests/slots/test_transition_guard.py`
- **Self-managed providers may persist READY without a model_id.** (1 connections) — `tests/slots/test_transition_guard.py`
- **The guard must not interfere with a normal READY transition.** (1 connections) — `tests/slots/test_transition_guard.py`

## Relationships

- [SlotManager](SlotManager.md) (3 shared connections)

## Source Files

- `tests/slots/test_transition_guard.py`

## Audit Trail

- EXTRACTED: 14 (82%)
- INFERRED: 3 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*