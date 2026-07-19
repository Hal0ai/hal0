# test_slots_image_pull.py

> 41 nodes · cohesion 0.09

## Key Concepts

- **test_slots_image_pull.py** (21 connections) — `tests/api/test_slots_image_pull.py`
- **_fake_profile_catalog()** (12 connections) — `tests/api/test_slots_image_pull.py`
- **TestClient** (12 connections)
- **FastAPI** (6 connections)
- **container_app()** (5 connections) — `tests/api/test_slots_image_pull.py`
- **Path** (5 connections)
- **test_image_status_pulling_when_job_active()** (5 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_start_idempotent()** (5 connections) — `tests/api/test_slots_image_pull.py`
- **test_image_present_returns_false_on_nonzero_exit()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_image_present_returns_true_on_zero_exit()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_image_status_missing()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_image_status_present()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_image_stream_completed_on_success()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_image_stream_failed_on_nonzero_exit()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_start_returns_202()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_status_missing_when_no_job()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_status_present_when_no_job()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_status_returns_job_snapshot_when_active()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_stream_missing_when_no_job()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_stream_present_when_no_job()** (4 connections) — `tests/api/test_slots_image_pull.py`
- **container_client()** (3 connections) — `tests/api/test_slots_image_pull.py`
- **_seed_slot_toml()** (3 connections) — `tests/api/test_slots_image_pull.py`
- **test_pull_start_404_for_unknown_slot()** (3 connections) — `tests/api/test_slots_image_pull.py`
- **Tests for container image-pull progress (Issue #659).  Verifies:   - ``image_sta** (1 connections) — `tests/api/test_slots_image_pull.py`
- **image_status=missing when image_present() returns False.** (1 connections) — `tests/api/test_slots_image_pull.py`
- *... and 16 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (4 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_image_pull.py`

## Audit Trail

- EXTRACTED: 140 (96%)
- INFERRED: 6 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*