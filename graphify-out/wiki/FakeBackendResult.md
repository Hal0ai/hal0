# FakeBackendResult

> 13 nodes · cohesion 0.24

## Key Concepts

- **FakeBackendResult** (24 connections) — `tests/harness/integration/_delegate_fakes.py`
- **test_delegate_task_dispatch_matrix.py** (19 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **test_delegate_dispatch_fans_out_to_three_backends_in_one_call()** (7 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **_scripted_local()** (5 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **test_delegate_unknown_backend_raises_keyerror()** (5 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **_scripted_docker()** (4 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **_scripted_modal()** (4 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **test_upstream_base_environment_still_has_expected_methods()** (2 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **The ``execute()`` return value as scripted by the test.** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **δ-harness — ``delegate_task`` dispatch MATRIX across all three backends.  Refere** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **Asking for an unregistered backend name fails loudly — better than     a silent** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **If a contributor has the upstream checkout cloned, assert the     ``BaseEnvironm** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`
- **ONE delegate_task call fanning out to THREE backends — the real shape     of ups** (1 connections) — `tests/harness/integration/test_delegate_task_dispatch_matrix.py`

## Relationships

- [DelegateTaskSpec](DelegateTaskSpec.md) (10 shared connections)
- [FakeDockerBackend](FakeDockerBackend.md) (8 shared connections)
- [FakeLocalBackend](FakeLocalBackend.md) (8 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (4 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (3 shared connections)
- [_BackendContract](_BackendContract.md) (3 shared connections)
- [BackendInvocation](BackendInvocation.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`
- `tests/harness/integration/test_delegate_task_dispatch_matrix.py`

## Audit Trail

- EXTRACTED: 75 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*