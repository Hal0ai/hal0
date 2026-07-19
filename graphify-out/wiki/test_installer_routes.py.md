# test_installer_routes.py

> 36 nodes · cohesion 0.09

## Key Concepts

- **test_installer_routes.py** (17 connections) — `tests/api/test_installer_routes.py`
- **TestClient** (14 connections)
- **Path** (8 connections)
- **test_install_complete_idempotent_when_sentinel_present()** (5 connections) — `tests/api/test_installer_routes.py`
- **_write_sentinel()** (5 connections) — `tests/api/test_installer_routes.py`
- **isolated_client()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_apply_idempotent_when_sentinel_present()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_apply_selections_idempotent_when_sentinel_present()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_complete_writes_sentinel_atomically()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_probe_writes_hardware_json()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_first_run_false_after_model_present()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_first_run_false_after_sentinel()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_has_default_slot_when_agent_toml_exists()** (4 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_has_default_slot_when_primary_toml_exists()** (4 connections) — `tests/api/test_installer_routes.py`
- **_no_chat_template_seed()** (3 connections) — `tests/api/test_installer_routes.py`
- **test_install_apply_open_without_sentinel_still_validates_body()** (3 connections) — `tests/api/test_installer_routes.py`
- **test_install_curated_models_returns_catalogue()** (3 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_first_run_true_on_empty_models_dir()** (3 connections) — `tests/api/test_installer_routes.py`
- **test_install_state_has_no_bundle_field()** (3 connections) — `tests/api/test_installer_routes.py`
- **MonkeyPatch** (1 connections)
- **Tests for /api/install — first-run state + probe + complete.  Tests use ``tmp_ha** (1 connections) — `tests/api/test_installer_routes.py`
- **An agent.toml on disk flips has_default_slot to True (canonical per ADR-0023).** (1 connections) — `tests/api/test_installer_routes.py`
- **The v1 ``bundle`` field is absent from /state (removed in Task 6.2).** (1 connections) — `tests/api/test_installer_routes.py`
- **POST /api/install/complete writes the marker file and flips first_run.** (1 connections) — `tests/api/test_installer_routes.py`
- **A sentinel written by another path (e.g. /apply) is harmless — /complete is idem** (1 connections) — `tests/api/test_installer_routes.py`
- *... and 11 more nodes in this community*

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_installer_routes.py`

## Audit Trail

- EXTRACTED: 116 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*