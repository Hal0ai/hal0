# TestClient

> 41 nodes · cohesion 0.08

## Key Concepts

- **TestClient** (22 connections)
- **TestListProfiles** (20 connections) — `tests/api/test_profiles_route.py`
- **test_profiles_route.py** (6 connections) — `tests/api/test_profiles_route.py`
- **app()** (4 connections) — `tests/api/test_profiles_route.py`
- **.test_custom_profiles_file_used_when_present()** (4 connections) — `tests/api/test_profiles_route.py`
- **client()** (3 connections) — `tests/api/test_profiles_route.py`
- **FastAPI** (3 connections)
- **TestEnrichedFields** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_backend_values()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_dense_family_variant_seeds_present()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_device_class_values()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_flm_npu_seed_present()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_kokoro_cpu_seed_present()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_mtp_true_seed_resolved_flags_no_longer_bundle_expanded()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_no_jinja_flag_in_any_seed_profile()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_resolved_flags_equals_flags_for_every_seed()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_returns_seed_profiles()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_seed_flag_true_for_seeds()** (3 connections) — `tests/api/test_profiles_route.py`
- **.test_create_round_trips_intent_and_quant()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_seed_items_expose_intent_quant_bench()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_dense_mtp_rocmfp4_mtp_true()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_item_has_required_fields()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_moe_rocmfp4_mtp_false()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_mtp_false_resolved_flags_no_spec_type()** (2 connections) — `tests/api/test_profiles_route.py`
- **.test_returns_200()** (2 connections) — `tests/api/test_profiles_route.py`
- *... and 16 more nodes in this community*

## Relationships

- [create_app](create_app.md) (2 shared connections)

## Source Files

- `tests/api/test_profiles_route.py`

## Audit Trail

- EXTRACTED: 126 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*