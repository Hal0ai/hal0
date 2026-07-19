# DelegateTaskSpec

> 17 nodes

## Key Concepts

- **DelegateTaskSpec** (23 connections) — `tests/harness/integration/_delegate_runner.py`
- **FakeModalBackend** (16 connections) — `tests/harness/integration/_delegate_fakes.py`
- **test_delegate_task_modal.py** (11 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **test_modal_backend_round_trips_with_sandbox_kwargs()** (6 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **test_modal_backend_cold_start_latency_visible_in_duration()** (6 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **test_modal_backend_multiple_commands_share_one_sandbox()** (6 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **test_modal_backend_token_missing_degrades_gracefully()** (5 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **.queue_result()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.init_session()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.cleanup()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **Stand-in for ``ModalEnvironment``.      Modal is the closest analog to "remote s** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **One task entry the parent passes to delegate_task.      Mirrors the upstream sha** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **δ-harness — Hermes ``delegate_task`` over the MODAL execution backend.  Referenc** (1 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **Happy path: ``sandbox_kwargs`` reach the backend + output returns.** (1 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` missing → per-task error.      This** (1 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **Simulate a 200 ms cold-start and check the per-task duration reflects it.      M** (1 connections) — `tests/harness/integration/test_delegate_task_modal.py`
- **Two commands in one task → two execute() calls on the SAME backend instance.** (1 connections) — `tests/harness/integration/test_delegate_task_modal.py`

## Relationships

- [FakeBackendResult](FakeBackendResult.md) (10 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (9 shared connections)
- [FakeDockerBackend](FakeDockerBackend.md) (5 shared connections)
- [FakeLocalBackend](FakeLocalBackend.md) (5 shared connections)
- [_BackendContract](_BackendContract.md) (3 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (2 shared connections)
- [BackendInvocation](BackendInvocation.md) (2 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`
- `tests/harness/integration/_delegate_runner.py`
- `tests/harness/integration/test_delegate_task_modal.py`

## Audit Trail

- EXTRACTED: 83 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*