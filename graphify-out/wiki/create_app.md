# create_app

> 44 nodes · cohesion 0.06

## Key Concepts

- **create_app()** (104 connections) — `src/hal0/api/__init__.py`
- **conftest.py** (6 connections) — `tests/api/conftest.py`
- **test_events_fixes.py** (6 connections) — `tests/api/test_events_fixes.py`
- **isolated_app_client()** (5 connections) — `tests/api/conftest.py`
- **test_startup_persona_seed.py** (5 connections) — `tests/api/test_startup_persona_seed.py`
- **_personas_root()** (5 connections) — `tests/api/test_startup_persona_seed.py`
- **isolated_client()** (4 connections) — `tests/api/conftest.py`
- **_parse()** (4 connections) — `tests/api/test_events_fixes.py`
- **test_epoch_differs_across_process_instances()** (4 connections) — `tests/api/test_events_fixes.py`
- **test_stream_severity_filter_excludes_lower()** (4 connections) — `tests/api/test_events_fixes.py`
- **test_stream_type_glob_filter()** (4 connections) — `tests/api/test_events_fixes.py`
- **test_lifespan_seed_converges_old_box_without_touching_edits()** (4 connections) — `tests/api/test_startup_persona_seed.py`
- **test_lifespan_seeds_default_personas()** (4 connections) — `tests/api/test_startup_persona_seed.py`
- **test_lifespan_stamps_hal0_managed_marker_before_seed()** (4 connections) — `tests/api/test_startup_persona_seed.py`
- **_hermetic_port_listeners()** (3 connections) — `tests/api/conftest.py`
- **_reset_hal0_composite_model_cache()** (3 connections) — `tests/api/conftest.py`
- **test_list_events_includes_epoch()** (3 connections) — `tests/api/test_events_fixes.py`
- **test_memory_provider_rename.py** (3 connections) — `tests/api/test_memory_provider_rename.py`
- **FastAPI** (2 connections)
- **TestClient** (2 connections)
- **TestClient** (2 connections)
- **test_gpu_arbiter_idle_lifespan.py** (2 connections) — `tests/api/test_gpu_arbiter_idle_lifespan.py`
- **test_lifespan_starts_and_cancels_arbiter_idle_loop()** (2 connections) — `tests/api/test_gpu_arbiter_idle_lifespan.py`
- **test_app_state_has_memory_provider()** (2 connections) — `tests/api/test_memory_provider_rename.py`
- **test_health_memory_enabled_reads_provider()** (2 connections) — `tests/api/test_memory_provider_rename.py`
- *... and 19 more nodes in this community*

## Relationships

- [test_profiles_crud.py](test_profiles_crud.py.md) (7 shared connections)
- [test_v1_npu_trio_routing.py](test_v1_npu_trio_routing.py.md) (6 shared connections)
- [lifespan](lifespan.md) (5 shared connections)
- [test_stacks_routes.py](test_stacks_routes.py.md) (5 shared connections)
- [TestClient](TestClient.md) (4 shared connections)
- [test_mcp_transport_security.py](test_mcp_transport_security.py.md) (3 shared connections)
- [test_kb1_hardening_tail.py](test_kb1_hardening_tail.py.md) (3 shared connections)
- [EventBus](EventBus.md) (3 shared connections)
- [test_pull_shutdown.py](test_pull_shutdown.py.md) (3 shared connections)
- [conftest.py](conftest.py.md) (3 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [test_route_collisions.py](test_route_collisions.py.md) (2 shared connections)

## Source Files

- `src/hal0/api/__init__.py`
- `tests/api/conftest.py`
- `tests/api/test_events_fixes.py`
- `tests/api/test_gpu_arbiter_idle_lifespan.py`
- `tests/api/test_memory_provider_rename.py`
- `tests/api/test_startup_persona_seed.py`

## Audit Trail

- EXTRACTED: 95 (46%)
- INFERRED: 113 (54%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*