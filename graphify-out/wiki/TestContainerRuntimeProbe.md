# TestContainerRuntimeProbe

> 9 nodes · cohesion 0.22

## Key Concepts

- **TestContainerRuntimeProbe** (9 connections) — `tests/providers/test_container.py`
- **.test_docker_is_not_a_candidate()** (2 connections) — `tests/providers/test_container.py`
- **.test_falls_back_to_bare_podman_on_path()** (2 connections) — `tests/providers/test_container.py`
- **``_container_runtime`` resolves podman wherever PATH puts it (snap,     /usr/loc** (1 connections) — `tests/providers/test_container.py`
- **podman installed somewhere other than /usr/bin/ (snap, nix, ...)         must st** (1 connections) — `tests/providers/test_container.py`
- **Docker is unsupported: even when only docker is on PATH, resolution         must** (1 connections) — `tests/providers/test_container.py`
- **.test_env_override_wins_over_everything()** (1 connections) — `tests/providers/test_container.py`
- **.test_prefers_absolute_usr_bin_podman()** (1 connections) — `tests/providers/test_container.py`
- **.test_raises_when_no_runtime_found_anywhere()** (1 connections) — `tests/providers/test_container.py`

## Relationships

- [Mount](Mount.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `tests/providers/test_container.py`

## Audit Trail

- EXTRACTED: 17 (89%)
- INFERRED: 2 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*