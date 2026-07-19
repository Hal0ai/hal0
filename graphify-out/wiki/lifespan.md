# lifespan

> 43 nodes · cohesion 0.09

## Key Concepts

- **lifespan()** (33 connections) — `src/hal0/api/__init__.py`
- **__init__.py** (24 connections) — `src/hal0/api/__init__.py`
- **hal0_slot_alias_models()** (13 connections) — `src/hal0/api/__init__.py`
- **hal0_chat_slot_alias_map()** (11 connections) — `src/hal0/api/__init__.py`
- **hal0_llm_slot_views()** (11 connections) — `src/hal0/api/__init__.py`
- **_fetch_hal0_composite_models()** (10 connections) — `src/hal0/api/__init__.py`
- **_prime_hal0_composite_cache()** (10 connections) — `src/hal0/api/__init__.py`
- **SlotManager** (9 connections)
- **_auto_resume_interrupted_pulls()** (8 connections) — `src/hal0/api/__init__.py`
- **_hal0_model_cache_clear()** (7 connections) — `src/hal0/api/__init__.py`
- **Any** (7 connections)
- **_slot_ctx_size()** (7 connections) — `src/hal0/api/__init__.py`
- **_slot_model_id()** (7 connections) — `src/hal0/api/__init__.py`
- **hal0_chat_slot_model_ids()** (6 connections) — `src/hal0/api/__init__.py`
- **_hydrate_upstreams()** (6 connections) — `src/hal0/api/__init__.py`
- **FastAPI** (6 connections)
- **_seed_multiplex_models()** (6 connections) — `src/hal0/api/__init__.py`
- **hal0_apply_registry_detail()** (5 connections) — `src/hal0/api/__init__.py`
- **_coerce_ctx()** (4 connections) — `src/hal0/api/__init__.py`
- **_model_recipe()** (4 connections) — `src/hal0/api/__init__.py`
- **_mount_dashboard()** (4 connections) — `src/hal0/api/__init__.py`
- **ModelRegistry** (4 connections)
- **_shutdown_pull_jobs()** (4 connections) — `src/hal0/api/__init__.py`
- **test_public_symbol_exports()** (4 connections) — `tests/api/test_upstream_dedup.py`
- **FastAPI application factory.  The module-level `app` exists so `uvicorn hal0.api** (1 connections) — `src/hal0/api/__init__.py`
- *... and 18 more nodes in this community*

## Relationships

- [test_upstream_dedup.py](test_upstream_dedup.py.md) (10 shared connections)
- [create_app](create_app.md) (5 shared connections)
- [_refresh_model_cache_on_ready](_refresh_model_cache_on_ready.md) (5 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (5 shared connections)
- [v1.py](v1.py.md) (4 shared connections)
- [test_v1_slot_alias_models.py](test_v1_slot_alias_models.py.md) (4 shared connections)
- [test_v1_chat_slot_alias.py](test_v1_chat_slot_alias.py.md) (3 shared connections)
- [test_slot_aliases.py](test_slot_aliases.py.md) (3 shared connections)
- [load_hal0_config](load_hal0_config.md) (3 shared connections)
- [Model](Model.md) (2 shared connections)
- [_FakeSlotManager](_FakeSlotManager.md) (2 shared connections)
- [_pull_root](_pull_root.md) (2 shared connections)

## Source Files

- `src/hal0/api/__init__.py`
- `tests/api/test_upstream_dedup.py`

## Audit Trail

- EXTRACTED: 168 (73%)
- INFERRED: 61 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*