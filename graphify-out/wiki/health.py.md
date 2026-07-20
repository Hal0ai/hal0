# health.py

> 23 nodes · cohesion 0.12

## Key Concepts

- **health.py** (10 connections) — `src/hal0/api/routes/health.py`
- **get_status()** (9 connections) — `src/hal0/api/routes/health.py`
- **list_features()** (7 connections) — `src/hal0/api/routes/health.py`
- **health_system()** (5 connections) — `src/hal0/api/routes/health.py`
- **metrics_prometheus()** (5 connections) — `src/hal0/api/routes/health.py`
- **Request** (5 connections)
- **_loaded_models()** (5 connections) — `src/hal0/api/routes/slots.py`
- **_disk_free_mb()** (4 connections) — `src/hal0/api/routes/health.py`
- **_memory_degraded()** (4 connections) — `src/hal0/api/routes/health.py`
- **Any** (4 connections)
- **health()** (3 connections) — `src/hal0/api/routes/health.py`
- **metrics()** (1 connections) — `src/hal0/api/routes/health.py`
- **Path** (1 connections)
- **Response** (1 connections)
- **Health, status, metrics, features.  Routes mounted under /api:   GET  /api/statu** (1 connections) — `src/hal0/api/routes/health.py`
- **Lightweight liveness probe.      Returns 200 the moment the API event loop is se** (1 connections) — `src/hal0/api/routes/health.py`
- **Deep health: disk headroom, slot manager, event bus.      Always returns HTTP 20** (1 connections) — `src/hal0/api/routes/health.py`
- **Prometheus text-exposition surface over slot lifecycle state.      Rendered by :** (1 connections) — `src/hal0/api/routes/health.py`
- **Runtime feature gates the dashboard branches on.      Flat ``feature → bool | st** (1 connections) — `src/hal0/api/routes/health.py`
- **Free MiB on the filesystem hosting ``path`` (0 if unavailable).      Walks up to** (1 connections) — `src/hal0/api/routes/health.py`
- **Return the memory degraded state for /api/status.      True  → memory enabled, r** (1 connections) — `src/hal0/api/routes/health.py`
- **Overall liveness + dashboard summary.      The Vue dashboard polls this every fe** (1 connections) — `src/hal0/api/routes/health.py`
- **Model ids currently served by dispatchable slots.      The truth source for the** (1 connections) — `src/hal0/api/routes/slots.py`

## Relationships

- [slots.py](slots.py.md) (5 shared connections)
- [hal0.sh](hal0.sh.md) (1 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [HardwareInfo](HardwareInfo.md) (1 shared connections)
- [test_metrics_prometheus_route.py](test_metrics_prometheus_route.py.md) (1 shared connections)
- [Any](Any.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/health.py`
- `src/hal0/api/routes/slots.py`

## Audit Trail

- EXTRACTED: 63 (86%)
- INFERRED: 10 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*