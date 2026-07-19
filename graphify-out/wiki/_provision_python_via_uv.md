# _provision_python_via_uv

> 7 nodes · cohesion 0.29

## Key Concepts

- **_provision_python_via_uv()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_ensure_supported_python()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_resolve_supported_python()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_uv_available()** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **Fetch a uv-managed Python as the last resort (#1250).      ``uv python install``** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Resolve a venv interpreter: system Python first, uv-managed as fallback.      A** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Find an interpreter in hermes-agent's supported range, newest first.      Probes** (1 connections) — `src/hal0/agents/hermes_provision.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (4 shared connections)
- [Any](Any.md) (2 shared connections)
- [_phase_config_write](_phase_config_write.md) (2 shared connections)
- [Path](Path.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 21 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*