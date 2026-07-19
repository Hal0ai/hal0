# test_hermes_live_resolve_render.py

> 15 nodes · cohesion 0.19

## Key Concepts

- **test_hermes_live_resolve_render.py** (8 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **_overlay()** (8 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **_build_config_overlay()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **test_disabled_omits_live_model_discovery()** (3 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **test_live_resolve_enables_live_model_discovery()** (3 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **test_live_resolve_forces_gateway_for_nonlocal_primary()** (3 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **test_live_resolve_tags_discovery_with_owned_by_filter()** (3 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **test_disabled_uses_physical_default()** (2 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **test_live_resolve_uses_virtual_default()** (2 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **Ordered ``(dotted_key, value)`` overlay applied via ``hermes config set``.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Live-resolve behaviour of the config-set overlay builder.  Post config-set redes** (1 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **Under live-resolve, providers.custom carries api_key + discover_models so     He** (1 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **#1148: under live-resolve, providers.custom.extra_headers carries the     X-hal0** (1 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **With live-resolve OFF, base_url is a single physical slot backend, so     live d** (1 connections) — `tests/agents/test_hermes_live_resolve_render.py`
- **A non-8080 primary backend_url must NOT leak into either base_url under     live** (1 connections) — `tests/agents/test_hermes_live_resolve_render.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (1 shared connections)
- [Path](Path.md) (1 shared connections)
- [Any](Any.md) (1 shared connections)
- [_phase_config_write](_phase_config_write.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`
- `tests/agents/test_hermes_live_resolve_render.py`

## Audit Trail

- EXTRACTED: 42 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*