# test_agent_restart_endpoint.py

> 27 nodes · cohesion 0.18

## Key Concepts

- **test_agent_restart_endpoint.py** (15 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **_FakeProc** (14 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **MonkeyPatch** (11 connections)
- **TestClient** (11 connections)
- **_patch_subprocess()** (10 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_activating_stderr_yields_restarting_status()** (6 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_actor_defaults_to_dashboard()** (6 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_emits_audit_log_event()** (6 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **patched_systemctl_ok()** (5 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_invokes_correct_argv()** (5 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_nonzero_exit_surfaces_stderr_in_error_code()** (5 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_timeout_surfaces_envelope()** (5 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_no_systemctl_on_host_returns_5xx()** (3 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_ok_returns_restarted_status()** (3 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_spawn_failure_surfaces_envelope()** (3 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_unknown_agent_returns_404()** (3 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **test_restart_with_async_mock_pattern()** (3 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **.communicate()** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **.__init__()** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **.kill()** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **HTTP tests for ``POST /api/agents/{agent_id}/restart`` (v0.3 PR-11).  Pins the r** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **Audit row goes to the ``hal0.agents.audit`` logger.      We patch the logger's `** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **No X-hal0-Agent header → actor recorded as ``hal0-dashboard``.** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **systemctl returns 0 + ``activating`` in stderr → status=restarting.** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- **Minimal stand-in for ``asyncio.subprocess.Process``.      Records the call so te** (1 connections) — `tests/agents/test_agent_restart_endpoint.py`
- *... and 2 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/agents/test_agent_restart_endpoint.py`

## Audit Trail

- EXTRACTED: 124 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*