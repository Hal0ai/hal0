# FakeDockerBackend

> 16 nodes

## Key Concepts

- **FakeDockerBackend** (16 connections) — `tests/harness/integration/_delegate_fakes.py`
- **test_delegate_task_docker.py** (11 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **test_docker_backend_round_trips_with_image_kwargs()** (6 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **test_docker_backend_payload_includes_container_kwargs()** (6 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **test_docker_backend_nonzero_returncode_surfaces_as_error()** (6 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **test_docker_backend_unavailable_degrades_gracefully()** (5 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **.__init__()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.queue_result()** (2 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.init_session()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **.cleanup()** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **Stand-in for ``DockerEnvironment``.      Captures the image + container kwargs a** (1 connections) — `tests/harness/integration/_delegate_fakes.py`
- **δ-harness — Hermes ``delegate_task`` over the DOCKER execution backend.  Referen** (1 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **Happy path: ``image=alpine:3.20`` reaches the backend + output returns.** (1 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **``init_session()`` raise (no docker daemon) becomes a per-task error,     not a** (1 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **Capture the full sandbox-spec so tests can assert provisioning intent.      The** (1 connections) — `tests/harness/integration/test_delegate_task_docker.py`
- **Exit code 127 (command not found) becomes an inline error.** (1 connections) — `tests/harness/integration/test_delegate_task_docker.py`

## Relationships

- [FakeBackendResult](FakeBackendResult.md) (8 shared connections)
- [FakeDelegateRunner](FakeDelegateRunner.md) (6 shared connections)
- [DelegateTaskSpec](DelegateTaskSpec.md) (5 shared connections)
- [_delegate_fakes.py](_delegate_fakes.py.md) (2 shared connections)
- [_BackendContract](_BackendContract.md) (2 shared connections)
- [BackendInvocation](BackendInvocation.md) (1 shared connections)

## Source Files

- `tests/harness/integration/_delegate_fakes.py`
- `tests/harness/integration/test_delegate_task_docker.py`

## Audit Trail

- EXTRACTED: 61 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*