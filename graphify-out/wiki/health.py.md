# health.py

> 19 nodes

## Key Concepts

- **health.py** (10 connections) — `src/hal0/api/routes/health.py`
- **list_features()** (7 connections) — `src/hal0/api/routes/health.py`
- **Request** (5 connections)
- **health_system()** (5 connections) — `src/hal0/api/routes/health.py`
- **metrics_prometheus()** (5 connections) — `src/hal0/api/routes/health.py`
- **_disk_free_mb()** (4 connections) — `src/hal0/api/routes/health.py`
- **_memory_degraded()** (4 connections) — `src/hal0/api/routes/health.py`
- **Any** (4 connections)
- **health()** (3 connections) — `src/hal0/api/routes/health.py`
- **Path** (1 connections)
- **metrics()** (1 connections) — `src/hal0/api/routes/health.py`
- **Response** (1 connections)
- **Health, status, metrics, features.  Routes mounted under /api:   GET  /api/statu** (1 connections) — `src/hal0/api/routes/health.py`
- **Free MiB on the filesystem hosting ``path`` (0 if unavailable).      Walks up to** (1 connections) — `src/hal0/api/routes/health.py`
- **Return the memory degraded state for /api/status.      True  → memory enabled, r** (1 connections) — `src/hal0/api/routes/health.py`
- **Lightweight liveness probe.      Returns 200 the moment the API event loop is se** (1 connections) — `src/hal0/api/routes/health.py`
- **Deep health: disk headroom, slot manager, event bus.      Always returns HTTP 20** (1 connections) — `src/hal0/api/routes/health.py`
- **Prometheus text-exposition surface over slot lifecycle state.      Rendered by :** (1 connections) — `src/hal0/api/routes/health.py`
- **Runtime feature gates the dashboard branches on.      Flat ``feature → bool | st** (1 connections) — `src/hal0/api/routes/health.py`

## Relationships

- [slots.py](slots.py.md) (4 shared connections)
- [hal0.sh](hal0.sh.md) (1 shared connections)
- [test_metrics_prometheus_route.py](test_metrics_prometheus_route.py.md) (1 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/health.py`

## Audit Trail

- EXTRACTED: 53 (93%)
- INFERRED: 4 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*