# conftest.py

> 13 nodes

## Key Concepts

- **conftest.py** (6 connections) — `tests/api/conftest.py`
- **isolated_app_client()** (5 connections) — `tests/api/conftest.py`
- **isolated_client()** (4 connections) — `tests/api/conftest.py`
- **_reset_hal0_composite_model_cache()** (3 connections) — `tests/api/conftest.py`
- **_hermetic_port_listeners()** (3 connections) — `tests/api/conftest.py`
- **TestClient** (2 connections)
- **FastAPI** (2 connections)
- **MonkeyPatch** (1 connections)
- **Shared pytest fixtures for ``tests/api/`` — module-level state isolation.  The c** (1 connections) — `tests/api/conftest.py`
- **Clear ``_HAL0_MODEL_CACHE`` before and after every api test.** (1 connections) — `tests/api/conftest.py`
- **TestClient whose lifespan resolves paths under tmp_hal0_home.** (1 connections) — `tests/api/conftest.py`
- **Like isolated_client, but also yields the app for state inspection.** (1 connections) — `tests/api/conftest.py`
- **Blind the port registry to the HOST's real sockets.      hal0.ports counts live** (1 connections) — `tests/api/conftest.py`

## Relationships

- [create_app](create_app.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `tests/api/conftest.py`

## Audit Trail

- EXTRACTED: 28 (90%)
- INFERRED: 3 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*