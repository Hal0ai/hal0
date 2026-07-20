# FLMInferError

> 8 nodes · cohesion 0.25

## Key Concepts

- **FLMInferError** (6 connections) — `src/hal0/providers/flm.py`
- **FLMHealthError** (4 connections) — `src/hal0/providers/flm.py`
- **.infer()** (4 connections) — `src/hal0/providers/flm.py`
- **test_infer_raises_typed_error_on_upstream_failure()** (3 connections) — `tests/providers/test_flm.py`
- **Hal0Error** (2 connections)
- **FLM health probe failed (typed for the error envelope).** (1 connections) — `src/hal0/providers/flm.py`
- **FLM inference call failed.** (1 connections) — `src/hal0/providers/flm.py`
- **Passthrough /v1/chat/completions to FLM.** (1 connections) — `src/hal0/providers/flm.py`

## Relationships

- [flm.py](flm.py.md) (3 shared connections)
- [Provider](Provider.md) (2 shared connections)
- [FLMProvider](FLMProvider.md) (2 shared connections)
- [test_flm.py](test_flm.py.md) (1 shared connections)

## Source Files

- `src/hal0/providers/flm.py`
- `tests/providers/test_flm.py`

## Audit Trail

- EXTRACTED: 18 (82%)
- INFERRED: 4 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*