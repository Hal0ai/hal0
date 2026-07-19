# _container_cgroup_mem_bytes

> 24 nodes

## Key Concepts

- **_container_cgroup_mem_bytes()** (11 connections) — `src/hal0/slots/capacity.py`
- **TestContainerCgroupMemBytes** (10 connections) — `tests/slots/test_container_cgroup_mem.py`
- **_make_proc()** (9 connections) — `tests/slots/test_container_cgroup_mem.py`
- **test_container_cgroup_mem.py** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_bytes_on_happy_path()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_container_not_found()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_pid_is_zero()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_on_inspect_timeout()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_on_cgroup_v1()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_memory_current_unreadable()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_docker_fallback_when_podman_absent()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_no_runtime()** (3 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Cgroup-wide ``memory.current`` for the podman/docker container backing *slot_nam** (1 connections) — `src/hal0/slots/capacity.py`
- **Tests for podman cgroup mem probe in hal0.slots.capacity.  Covers :func:`hal0.sl** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Return a fake asyncio.subprocess.Process mock.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Unit tests for the podman cgroup probe.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Full happy-path: podman inspect → PID → cgroup path → memory.current.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **No podman or docker binary → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Container inspect returns non-zero (container doesn't exist) → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Inspect returns PID 0 (stopped container) → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **asyncio.wait_for timeout during inspect → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **cgroupv1 line lacks '::' → probe cannot walk path → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **memory.current read fails (OSError) → 0.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Falls back to docker when podman is not found.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`

## Relationships

- [build_per_slot](build_per_slot.md) (2 shared connections)
- [capacity.py](capacity.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `tests/slots/test_container_cgroup_mem.py`

## Audit Trail

- EXTRACTED: 61 (79%)
- INFERRED: 16 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*