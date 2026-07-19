# _phase_preflight

> 13 nodes

## Key Concepts

- **_phase_preflight()** (9 connections) — `src/hal0/agents/hermes_provision.py`
- **_provision_python_via_uv()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **path_is_writable()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_ensure_supported_python()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_python_range_error()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_resolve_supported_python()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_uv_available()** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **Whether we can actually create a file at (or under) ``target``.      Walks up to** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Hard-fail when the host can't host Hermes.      Documented blockers (plan §4):** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Actionable failure text for hosts with no hermes-compatible Python.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Fetch a uv-managed Python as the last resort (#1250).      ``uv python install``** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Resolve a venv interpreter: system Python first, uv-managed as fallback.      A** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Find an interpreter in hermes-agent's supported range, newest first.      Probes** (1 connections) — `src/hal0/agents/hermes_provision.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (7 shared connections)
- [Path](Path.md) (4 shared connections)
- [_StepCtx](_StepCtx.md) (2 shared connections)
- [Any](Any.md) (2 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 40 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*