# FakeDelegateRunner

> 18 nodes

## Key Concepts

- **FakeDelegateRunner** (24 connections) — `tests/harness/integration/_delegate_runner.py`
- **_delegate_runner.py** (11 connections) — `tests/harness/integration/_delegate_runner.py`
- **.run_delegate_task()** (8 connections) — `tests/harness/integration/_delegate_runner.py`
- **TaskResult** (5 connections) — `tests/harness/integration/_delegate_runner.py`
- **._build_backend()** (5 connections) — `tests/harness/integration/_delegate_runner.py`
- **._assemble_envelope()** (4 connections) — `tests/harness/integration/_delegate_runner.py`
- **._assemble_final_response()** (4 connections) — `tests/harness/integration/_delegate_runner.py`
- **DelegateTrace** (3 connections) — `tests/harness/integration/_delegate_runner.py`
- **.register_backend()** (2 connections) — `tests/harness/integration/_delegate_runner.py`
- **.__init__()** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **BackendFactory** (1 connections)
- **In-process orchestration harness for δ-tier ``delegate_task`` tests.  Why a cust** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **One row in delegate_task's results array.** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **Everything the harness recorded for a single delegate_task call.      The runner** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **Simulated delegate_task dispatcher.      Usage:          runner = FakeDelegateRu** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **Execute one delegate_task call covering ``tasks``.          Returns the trace +** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **Serialise the per-task results in upstream's envelope shape.          Reference:** (1 connections) — `tests/harness/integration/_delegate_runner.py`
- **Compose the assistant message the parent would emit after the         delegate_t** (1 connections) — `tests/harness/integration/_delegate_runner.py`

## Relationships

- [DelegateTaskSpec](DelegateTaskSpec.md) (9 shared connections)
- [FakeDockerBackend](FakeDockerBackend.md) (6 shared connections)
- [FakeBackendResult](FakeBackendResult.md) (4 shared connections)
- [_BackendContract](_BackendContract.md) (3 shared connections)
- [FakeLocalBackend](FakeLocalBackend.md) (3 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (1 shared connections)
- [KeyError](KeyError.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_runner.py`

## Audit Trail

- EXTRACTED: 74 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*