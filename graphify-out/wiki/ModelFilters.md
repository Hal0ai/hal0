# ModelFilters

> 31 nodes

## Key Concepts

- **ModelFilters** (18 connections) — `src/hal0/upstreams/filters.py`
- **.from_lists()** (16 connections) — `src/hal0/upstreams/filters.py`
- **apply_filters()** (13 connections) — `src/hal0/upstreams/filters.py`
- **TestFilterEngine** (13 connections) — `tests/config/test_upstream_filters.py`
- **is_advertised()** (9 connections) — `src/hal0/upstreams/filters.py`
- **TestFilterSchema** (7 connections) — `tests/config/test_upstream_filters.py`
- **filters.py** (4 connections) — `src/hal0/upstreams/filters.py`
- **.test_models_allowlist_exact()** (4 connections) — `tests/config/test_upstream_filters.py`
- **test_upstream_filters.py** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_none_or_empty_pass_all()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_include_glob_by_provider_prefix()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_models_and_include_are_ored()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_exclude_only_hides_from_pass_all()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_exclude_overrides_include()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_exclude_overrides_exact_allowlist()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_question_mark_glob()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_case_sensitive_matching()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.test_order_preserved()** (3 connections) — `tests/config/test_upstream_filters.py`
- **.is_empty()** (2 connections) — `src/hal0/upstreams/filters.py`
- **.test_from_lists_strips_and_drops_empty()** (2 connections) — `tests/config/test_upstream_filters.py`
- **Per-upstream model-advertising filters.  Implements docs/superpowers/specs/2026-** (1 connections) — `src/hal0/upstreams/filters.py`
- **Immutable runtime form of an [upstream.model_filters] table.** (1 connections) — `src/hal0/upstreams/filters.py`
- **Build from plain lists (e.g. a parsed UpstreamModelFilters dump).** (1 connections) — `src/hal0/upstreams/filters.py`
- **Return True when `model_id` should appear in /v1/models.** (1 connections) — `src/hal0/upstreams/filters.py`
- **Filter an iterable of model ids, preserving order.** (1 connections) — `src/hal0/upstreams/filters.py`
- *... and 6 more nodes in this community*

## Relationships

- [Upstream](Upstream.md) (7 shared connections)
- [test_v1_models_filters.py](test_v1_models_filters.py.md) (3 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (2 shared connections)
- [test_models_routes.py](test_models_routes.py.md) (1 shared connections)
- [v1.py](v1.py.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/upstreams/filters.py`
- `tests/config/test_upstream_filters.py`

## Audit Trail

- EXTRACTED: 91 (71%)
- INFERRED: 38 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*