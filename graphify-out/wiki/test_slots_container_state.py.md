# test_slots_container_state.py

> 35 nodes · cohesion 0.09

## Key Concepts

- **test_slots_container_state.py** (18 connections) — `tests/api/test_slots_container_state.py`
- **TestClient** (13 connections)
- **app_with_container_slot()** (5 connections) — `tests/api/test_slots_container_state.py`
- **_fake_vulkan_catalog()** (5 connections) — `tests/api/test_slots_container_state.py`
- **test_container_actual_image_no_mismatch_when_running_matches_profile()** (4 connections) — `tests/api/test_slots_container_state.py`
- **test_container_image_mismatch_when_running_differs_from_profile()** (4 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_has_runtime_profile_image_fields()** (4 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_resolved_command_includes_flags()** (4 connections) — `tests/api/test_slots_container_state.py`
- **client_with_container_slot()** (3 connections) — `tests/api/test_slots_container_state.py`
- **FastAPI** (3 connections)
- **_seed_slot_toml()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_has_container_status_and_health_when_running()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_status_crashed_when_failed()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_status_starting_when_active_but_unhealthy()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_container_slot_status_stopped_when_inactive()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_every_slot_gets_container_state_fields()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_get_slot_container_state_fields()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_profileless_slot_actual_image_without_mismatch_key()** (3 connections) — `tests/api/test_slots_container_state.py`
- **test_profileless_slot_has_null_image_and_command()** (3 connections) — `tests/api/test_slots_container_state.py`
- **Path** (1 connections)
- **Tests for container-slot state fields on /api/slots (Issue #656, Phase E #687).** (1 connections) — `tests/api/test_slots_container_state.py`
- **Container slot with inactive unit returns container_status=stopped, container_he** (1 connections) — `tests/api/test_slots_container_state.py`
- **Container slot in 'failed' systemd state returns container_status=crashed.** (1 connections) — `tests/api/test_slots_container_state.py`
- **Active unit + unhealthy /health → container_status=starting (inference server no** (1 connections) — `tests/api/test_slots_container_state.py`
- **The container probe covers EVERY slot — the profile-less chat slot     carries c** (1 connections) — `tests/api/test_slots_container_state.py`
- *... and 10 more nodes in this community*

## Relationships

- [ProfileConfig](ProfileConfig.md) (3 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_container_state.py`

## Audit Trail

- EXTRACTED: 102 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*