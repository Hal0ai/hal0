# FakeLocalBackend

> 18 nodes · cohesion 0.17

## Key Concepts

- **FakeLocalBackend** (17 connections) — `tests/harness/integration/_delegate_fakes.py`
- **test_delegate_task_local.py** (12 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **_runner_for_backend()** (8 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **test_local_backend_error_envelope_does_not_crash_parent()** (6 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **test_local_backend_records_invocation_count_and_payload()** (6 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **test_local_backend_round_trips_simple_echo()** (6 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **test_local_backend_empty_goal_rejected_before_dispatch()** (5 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **.__init__()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.queue_result()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.cleanup()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.init_session()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **In-process stand-in for ``LocalEnvironment``.      Captures every ``execute()``** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **δ-harness — Hermes ``delegate_task`` over the LOCAL execution backend.  Referenc** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **Mirrors upstream tools/delegate_tool.py:2034 — empty goal is a hard reject.** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **Wire a runner that hands the scripted ``backend`` to every task.** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **Happy path: echo "hello" round-trips into the assistant response.** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **The backend captures the exact command + cwd the runner dispatched.** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`
- **A backend ``execute()`` raise propagates as a per-task ``error`` slot.** (1 connections) — `tests/harness/integration/test_delegate_task_local.py`

## Relationships

- [FakeBackendResult](FakeBackendResult.md) (8 shared connections)
- [DelegateTaskSpec](DelegateTaskSpec.md) (5 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (3 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (2 shared connections)
- [_BackendContract](_BackendContract.md) (2 shared connections)
- [BackendInvocation](BackendInvocation.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`
- `tests/harness/integration/test_delegate_task_local.py`

## Audit Trail

- EXTRACTED: 72 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*