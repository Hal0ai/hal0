# ensure_podman_apparmor_usable

> 18 nodes

## Key Concepts

- **ensure_podman_apparmor_usable()** (10 connections) — `src/hal0/agents/containers_apparmor.py`
- **containers_apparmor.py** (9 connections) — `src/hal0/agents/containers_apparmor.py`
- **_write_apparmor_unconfined()** (5 connections) — `src/hal0/agents/containers_apparmor.py`
- **_smoke()** (4 connections) — `src/hal0/agents/containers_apparmor.py`
- **_apparmor_already_unconfined()** (4 connections) — `src/hal0/agents/containers_apparmor.py`
- **Path** (4 connections)
- **ApparmorPreflightResult** (3 connections) — `src/hal0/agents/containers_apparmor.py`
- **_is_apparmor_failure()** (3 connections) — `src/hal0/agents/containers_apparmor.py`
- **_atomic_write()** (3 connections) — `src/hal0/agents/containers_apparmor.py`
- **_main()** (3 connections) — `src/hal0/agents/containers_apparmor.py`
- **Any** (2 connections)
- **CompletedProcess** (2 connections)
- **Convergent AppArmor preflight for podman on unconfined LXC (halo150 R4).  On a p** (1 connections) — `src/hal0/agents/containers_apparmor.py`
- **Outcome of :func:`ensure_podman_apparmor_usable`.      ``outcome`` is one of:** (1 connections) — `src/hal0/agents/containers_apparmor.py`
- **True iff ``containers.apparmor_profile`` is already ``"unconfined"``.** (1 connections) — `src/hal0/agents/containers_apparmor.py`
- **Idempotently set ``[containers] apparmor_profile = "unconfined"``.      Preserve** (1 connections) — `src/hal0/agents/containers_apparmor.py`
- **Detect the unconfined-LXC apparmor failure and converge the fix.      1. Smoke `** (1 connections) — `src/hal0/agents/containers_apparmor.py`
- **CLI entry (``python -m hal0.agents.containers_apparmor``) for install.sh.      R** (1 connections) — `src/hal0/agents/containers_apparmor.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/hal0/agents/containers_apparmor.py`

## Audit Trail

- EXTRACTED: 58 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*