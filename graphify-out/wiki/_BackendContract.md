# _BackendContract

> 8 nodes

## Key Concepts

- **_BackendContract** (13 connections) — `tests/harness/integration/_delegate_fakes.py`
- **test_all_fakes_implement_backend_contract()** (6 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **test_delegate_dispatch_per_backend_round_trips()** (5 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **.init_session()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.cleanup()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **The public shape every Hermes execution backend exposes.      Kept deliberately** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **Per-backend smoke: the runner picks the right backend and the     output round-t** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **Our three fakes must claim conformance with the ABC mirror.      A regression wh** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`

## Relationships

- [DelegateTaskSpec](DelegateTaskSpec.md) (3 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (3 shared connections)
- [FakeBackendResult](FakeBackendResult.md) (3 shared connections)
- [FakeDockerBackend](FakeDockerBackend.md) (2 shared connections)
- [FakeLocalBackend](FakeLocalBackend.md) (2 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (1 shared connections)
- [BackendInvocation](BackendInvocation.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`
- `tests/harness/integration/test_delegate_task_dispatch_matrix.py`

## Audit Trail

- EXTRACTED: 24 (83%)
- INFERRED: 5 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*