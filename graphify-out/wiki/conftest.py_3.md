# conftest.py

> 18 nodes

## Key Concepts

- **conftest.py** (8 connections) — `tests/conftest.py`
- **MonkeyPatch** (4 connections)
- **app()** (4 connections) — `tests/conftest.py`
- **client()** (4 connections) — `tests/conftest.py`
- **tmp_hal0_home()** (4 connections) — `tests/conftest.py`
- **_no_static_slot_seed()** (3 connections) — `tests/conftest.py`
- **_store_not_nfs_by_default()** (3 connections) — `tests/conftest.py`
- **_auth_dev_open_by_default()** (3 connections) — `tests/conftest.py`
- **FastAPI** (3 connections)
- **TestClient** (1 connections)
- **TempPathFactory** (1 connections)
- **Shared pytest fixtures for hal0 tests.** (1 connections) — `tests/conftest.py`
- **Silence the lifespan's static slot-TOML seeding (flm/tts/rerank/     utility/img** (1 connections) — `tests/conftest.py`
- **Force ``hal0.config.store.is_nfs_path`` to False for the whole suite.      ML-3'** (1 connections) — `tests/conftest.py`
- **Force the KB-1/§1 auth middleware into dev-open for the whole suite.      ``requ** (1 connections) — `tests/conftest.py`
- **Return a fresh FastAPI app instance, filesystem-isolated under tmp_hal0_home.** (1 connections) — `tests/conftest.py`
- **TestClient with lifespan executed (so app.state singletons exist).** (1 connections) — `tests/conftest.py`
- **Set HAL0_HOME to a temporary directory for filesystem isolation.      Also opts** (1 connections) — `tests/conftest.py`

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/conftest.py`

## Audit Trail

- EXTRACTED: 44 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*