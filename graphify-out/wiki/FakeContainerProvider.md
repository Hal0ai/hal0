# FakeContainerProvider

> 53 nodes

## Key Concepts

- **FakeContainerProvider** (17 connections) — `tests/golden_paths/conftest.py`
- **client_factory()** (12 connections) — `tests/golden_paths/conftest.py`
- **make_create_body()** (12 connections) — `tests/golden_paths/conftest.py`
- **conftest.py** (9 connections) — `tests/golden_paths/conftest.py`
- **test_gp09_slot_rename.py** (7 connections) — `tests/golden_paths/test_gp09_slot_rename.py`
- **test_gp10_slot_delete.py** (7 connections) — `tests/golden_paths/test_gp10_slot_delete.py`
- **test_gp14_api_restart.py** (6 connections) — `tests/golden_paths/test_gp14_api_restart.py`
- **Any** (5 connections)
- **test_rename_preserves_identity_port_and_config()** (5 connections) — `tests/golden_paths/test_gp09_slot_rename.py`
- **test_delete_cleans_up_unit_state_and_port()** (5 connections) — `tests/golden_paths/test_gp10_slot_delete.py`
- **test_gp15_no_hermes.py** (5 connections) — `tests/golden_paths/test_gp15_no_hermes.py`
- **fake_container()** (4 connections) — `tests/golden_paths/conftest.py`
- **test_rename_frees_old_name_for_reuse()** (4 connections) — `tests/golden_paths/test_gp09_slot_rename.py`
- **test_delete_is_idempotent()** (4 connections) — `tests/golden_paths/test_gp10_slot_delete.py`
- **test_restart_reconciles_running_slot_without_bouncing_it()** (4 connections) — `tests/golden_paths/test_gp14_api_restart.py`
- **test_restart_leaves_stopped_slot_stopped_without_starting_it()** (4 connections) — `tests/golden_paths/test_gp14_api_restart.py`
- **_hermetic_port_listeners()** (3 connections) — `tests/golden_paths/conftest.py`
- **_port_claim_for()** (3 connections) — `tests/golden_paths/test_gp09_slot_rename.py`
- **_hermes_removed()** (3 connections) — `tests/golden_paths/test_gp15_no_hermes.py`
- **MonkeyPatch** (2 connections)
- **.load_sync()** (2 connections) — `tests/golden_paths/conftest.py`
- **.unload_sync()** (2 connections) — `tests/golden_paths/conftest.py`
- **.health()** (2 connections) — `tests/golden_paths/conftest.py`
- **.expected_argv()** (2 connections) — `tests/golden_paths/conftest.py`
- **FakeContainerProvider** (2 connections)
- *... and 28 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `tests/golden_paths/conftest.py`
- `tests/golden_paths/test_gp09_slot_rename.py`
- `tests/golden_paths/test_gp10_slot_delete.py`
- `tests/golden_paths/test_gp14_api_restart.py`
- `tests/golden_paths/test_gp15_no_hermes.py`

## Audit Trail

- EXTRACTED: 160 (97%)
- INFERRED: 5 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*