# BackendInvocation

> 8 nodes · cohesion 0.32

## Key Concepts

- **BackendInvocation** (5 connections) — `tests/harness/integration/_delegate_fakes.py`
- **Any** (5 connections)
- **.execute()** (3 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.execute()** (3 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.execute()** (3 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.__init__()** (3 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.execute()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **One ``execute()`` call captured for assertions.** (1 connections) — `tests/harness/integration/_delegate_fakes.py`

## Relationships

- [DelegateTaskSpec](DelegateTaskSpec.md) (2 shared connections)
- [_BackendContract](_BackendContract.md) (1 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (1 shared connections)
- [FakeDockerBackend](FakeDockerBackend.md) (1 shared connections)
- [FakeLocalBackend](FakeLocalBackend.md) (1 shared connections)
- [FakeBackendResult](FakeBackendResult.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`

## Audit Trail

- EXTRACTED: 25 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*