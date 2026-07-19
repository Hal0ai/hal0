# test_providers_routes.py

> 48 nodes · cohesion 0.12

## Key Concepts

- **test_providers_routes.py** (37 connections) — `tests/api/test_providers_routes.py`
- **TestClient** (36 connections)
- **_seed_upstreams()** (29 connections) — `tests/api/test_providers_routes.py`
- **_seed_openrouter_in_toml()** (18 connections) — `tests/api/test_providers_routes.py`
- **_upstreams_toml_path()** (11 connections) — `tests/api/test_providers_routes.py`
- **_toml_rows()** (10 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_does_not_touch_other_rows()** (8 connections) — `tests/api/test_providers_routes.py`
- **test_delete_upstream_retains_credentials()** (6 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_empty_body_is_noop()** (6 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_persists_atomically_no_partial_file()** (6 connections) — `tests/api/test_providers_routes.py`
- **test_delete_upstream_removes_row_and_registry()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_advertise_off_round_trip()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_advertise_on_restores_state()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_clear_filters_with_empty_object()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_clears_cached_models_when_off()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_drops_cache_on_reenable()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_enabled_and_filters()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_invalid_auth_style_400()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_patch_upstream_structural_fields_on_remote()** (5 connections) — `tests/api/test_providers_routes.py`
- **test_create_upstream_minimal()** (4 connections) — `tests/api/test_providers_routes.py`
- **test_create_upstream_with_filters_and_flags()** (4 connections) — `tests/api/test_providers_routes.py`
- **test_create_upstream_duplicate_409()** (3 connections) — `tests/api/test_providers_routes.py`
- **test_create_upstream_explicit_fields_beat_catalog()** (3 connections) — `tests/api/test_providers_routes.py`
- **test_create_upstream_from_catalog_prefills()** (3 connections) — `tests/api/test_providers_routes.py`
- **test_delete_upstream_404()** (3 connections) — `tests/api/test_providers_routes.py`
- *... and 23 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (4 shared connections)
- [UpstreamEntry](UpstreamEntry.md) (2 shared connections)
- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `tests/api/test_providers_routes.py`

## Audit Trail

- EXTRACTED: 268 (97%)
- INFERRED: 7 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*