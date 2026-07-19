# test_exposure.py

> 28 nodes

## Key Concepts

- **test_exposure.py** (14 connections) — `tests/security/test_exposure.py`
- **_enumerate_routes()** (7 connections) — `tests/security/test_exposure.py`
- **_classify_method()** (5 connections) — `tests/security/test_exposure.py`
- **_iter_effective()** (4 connections) — `tests/security/test_exposure.py`
- **_resolve_path()** (4 connections) — `tests/security/test_exposure.py`
- **test_every_mounted_route_is_explicitly_classified()** (4 connections) — `tests/security/test_exposure.py`
- **test_enforcement_wired()** (4 connections) — `tests/security/test_exposure.py`
- **app_routes()** (3 connections) — `tests/security/test_exposure.py`
- **test_open_allowlist_is_exact()** (3 connections) — `tests/security/test_exposure.py`
- **test_bootstrap_class_covers_installer()** (3 connections) — `tests/security/test_exposure.py`
- **test_unclassified_new_route_denies_by_default()** (3 connections) — `tests/security/test_exposure.py`
- **BaseRoute** (2 connections)
- **FastAPI** (2 connections)
- **test_representative_admin_routes_are_admin()** (2 connections) — `tests/security/test_exposure.py`
- **test_representative_client_routes_are_client()** (2 connections) — `tests/security/test_exposure.py`
- **MonkeyPatch** (1 connections)
- **§21.11 exposure-CI ratchet (KB-1 / §1, seam S9).  Walks the *real* mounted route** (1 connections) — `tests/security/test_exposure.py`
- **Recursively resolve FastAPI's lazy included-router wrappers.      FastAPI >=0.13** (1 connections) — `tests/security/test_exposure.py`
- **Return the effective path template for ``route``.      FastAPI's ``_EffectiveRou** (1 connections) — `tests/security/test_exposure.py`
- **Return every concrete ``(method, path)`` pair the app actually serves.      ``pa** (1 connections) — `tests/security/test_exposure.py`
- **Websocket handshakes are HTTP GETs at the transport level.** (1 connections) — `tests/security/test_exposure.py`
- **No currently-mounted route may resolve via the ADMIN fallback.      This is the** (1 connections) — `tests/security/test_exposure.py`
- **The OPEN set is exactly ``OPEN_ALLOWLIST`` -- neither wider nor narrower.      W** (1 connections) — `tests/security/test_exposure.py`
- **Every ``/api/install/*`` route classifies as BOOTSTRAP, nothing else does.** (1 connections) — `tests/security/test_exposure.py`
- **Spot-check a handful of unambiguously RCE-class routes stay ADMIN.** (1 connections) — `tests/security/test_exposure.py`
- *... and 3 more nodes in this community*

## Relationships

- [create_app](create_app.md) (2 shared connections)
- [Enum](Enum.md) (2 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `tests/security/test_exposure.py`

## Audit Trail

- EXTRACTED: 70 (93%)
- INFERRED: 5 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*