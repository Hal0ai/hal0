# TestClient

> 26 nodes

## Key Concepts

- **TestClient** (15 connections)
- **test_profiles_portable_routes.py** (10 connections) — `tests/api/test_profiles_portable_routes.py`
- **_seed_name()** (10 connections) — `tests/api/test_profiles_portable_routes.py`
- **TestImportCommit** (6 connections) — `tests/api/test_profiles_portable_routes.py`
- **TestImportDryRun** (5 connections) — `tests/api/test_profiles_portable_routes.py`
- **app()** (4 connections) — `tests/api/test_profiles_portable_routes.py`
- **_create_custom()** (4 connections) — `tests/api/test_profiles_portable_routes.py`
- **TestExportRoute** (4 connections) — `tests/api/test_profiles_portable_routes.py`
- **FastAPI** (3 connections)
- **client()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_export_seed_profile_200_valid_envelope()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_export_custom_profile_200_valid_envelope()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_dry_run_shape_and_checksum_ok()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_dry_run_collides_true_for_existing_name()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_dry_run_collides_false_for_fresh_name()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_dry_run_checksum_ok_false_when_tampered()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_commit_creates_profile()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_commit_without_name_400()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_commit_duplicate_name_409()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_commit_too_new_schema_400()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_export_then_import_under_new_name_appears_in_list()** (3 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_export_unknown_404()** (2 connections) — `tests/api/test_profiles_portable_routes.py`
- **.test_commit_bad_envelope_400()** (2 connections) — `tests/api/test_profiles_portable_routes.py`
- **TestRoundTripHttp** (2 connections) — `tests/api/test_profiles_portable_routes.py`
- **Tests for the portable profile routes — export/import over HTTP.  Mirrors tests/** (1 connections) — `tests/api/test_profiles_portable_routes.py`
- *... and 1 more nodes in this community*

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_profiles_portable_routes.py`

## Audit Trail

- EXTRACTED: 104 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*