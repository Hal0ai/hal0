# _FakeAgentManager

> 13 nodes · cohesion 0.15

## Key Concepts

- **_FakeAgentManager** (12 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enforce_single_pick_noop_when_only_hermes_installed()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_refuses_when_incumbent_present_without_switch()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_switch_uninstalls_incumbent_before_provisioning()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enforce_single_pick_dies_on_incumbent_without_switch()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enforce_single_pick_noop_when_nothing_installed()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enforce_single_pick_uninstalls_incumbent_with_switch()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **.__init__()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **.installed_names()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **Stand-in for :class:`hal0.agents.manager.AgentManager` — records     ``uninstall** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **A bare re-install of hermes itself (no other incumbent) is not a     single-pick** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **End-to-end: `hal0 agent install hermes` (no --switch) against an     existing pi** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **`--switch` clears the incumbent first, THEN provisioning proceeds —     same ato** (1 connections) — `tests/cli/test_agent_install_hermes.py`

## Relationships

- [test_agent_install_hermes.py](test_agent_install_hermes.py.md) (7 shared connections)
- [_fake_bundled_agent_manager](_fake_bundled_agent_manager.md) (1 shared connections)
- [test_uninstall.py](test_uninstall.py.md) (1 shared connections)

## Source Files

- `tests/cli/test_agent_install_hermes.py`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*