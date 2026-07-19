# socket

> 18 nodes · cohesion 0.16

## Key Concepts

- **socket** (19 connections)
- **test_setup_plan.py** (13 connections) — `tests/install/test_setup_plan.py`
- **_write()** (6 connections) — `tests/install/test_setup_plan.py`
- **_bind_free_port()** (5 connections) — `tests/install/test_setup_plan.py`
- **_no_real_apply()** (3 connections) — `tests/install/test_setup_plan.py`
- **test_plan_detects_port_in_use()** (3 connections) — `tests/install/test_setup_plan.py`
- **test_plan_strict_answers_port_in_use_is_an_error()** (3 connections) — `tests/install/test_setup_plan.py`
- **_fail_if_called()** (2 connections) — `tests/install/test_setup_plan.py`
- **_hal0_home()** (2 connections) — `tests/install/test_setup_plan.py`
- **test_plan_answers_bad_file_exits_nonzero()** (2 connections) — `tests/install/test_setup_plan.py`
- **test_plan_answers_good_file_prints_resolved_slots()** (2 connections) — `tests/install/test_setup_plan.py`
- **Path** (1 connections)
- **Tests for ``hal0 setup --plan`` / ``--dry-run`` (issue #1116).  ``--plan`` must** (1 connections) — `tests/install/test_setup_plan.py`
- **Bind an OS-assigned free port and hold it open; caller reads     ``sock.getsockn** (1 connections) — `tests/install/test_setup_plan.py`
- **Hermetic HAL0_HOME so slot/sentinel writes (if any leaked) land in a     throwaw** (1 connections) — `tests/install/test_setup_plan.py`
- **--plan must never reach the apply path. Make that path explode.** (1 connections) — `tests/install/test_setup_plan.py`
- **test_plan_auto_prints_table_and_writes_nothing()** (1 connections) — `tests/install/test_setup_plan.py`
- **test_plan_dry_run_alias_behaves_identically()** (1 connections) — `tests/install/test_setup_plan.py`

## Relationships

- [network.py](network.py.md) (2 shared connections)
- [HermesDriver](HermesDriver.md) (1 shared connections)
- [agent_shim.py](agent_shim.py.md) (1 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (1 shared connections)
- [Check](Check.md) (1 shared connections)
- [build_auto_selections](build_auto_selections.md) (1 shared connections)
- [HardwareStats](HardwareStats.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [probes.py](probes.py.md) (1 shared connections)
- [mdns.py](mdns.py.md) (1 shared connections)
- [test_chat_proxy.py](test_chat_proxy.py.md) (1 shared connections)
- [._cfg](_cfg.md) (1 shared connections)

## Source Files

- `tests/install/test_setup_plan.py`

## Audit Trail

- EXTRACTED: 65 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*