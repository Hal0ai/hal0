# _probe_comfyui

> 14 nodes

## Key Concepts

- **_probe_comfyui()** (7 connections) — `src/hal0/api/routes/services_health.py`
- **services_health()** (7 connections) — `src/hal0/api/routes/services_health.py`
- **services_health.py** (6 connections) — `src/hal0/api/routes/services_health.py`
- **_queue_counts()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_probe_hermes()** (5 connections) — `src/hal0/api/routes/services_health.py`
- **_probe_openwebui()** (4 connections) — `src/hal0/api/routes/services_health.py`
- **_openwebui_url()** (3 connections) — `src/hal0/api/routes/services_health.py`
- **Any** (1 connections)
- **GET /api/services/health — dashboard services health aggregator.  Returns a stab** (1 connections) — `src/hal0/api/routes/services_health.py`
- **Configured public URL for OpenWebUI, or None when absent.** (1 connections) — `src/hal0/api/routes/services_health.py`
- **Probe ComfyUI via its /system_stats + /queue endpoints (in-process).      Return** (1 connections) — `src/hal0/api/routes/services_health.py`
- **Probe Hermes via systemd unit state (in-process, same as comfyui/status).      R** (1 connections) — `src/hal0/api/routes/services_health.py`
- **Real reachability probe — GET <loopback>/health on OpenWebUI.      SpikeB §5.4 c** (1 connections) — `src/hal0/api/routes/services_health.py`
- **Aggregate health of the four known hal0 companion services.      Response shape:** (1 connections) — `src/hal0/api/routes/services_health.py`

## Relationships

- [comfyui.py](comfyui.py.md) (3 shared connections)
- [services.py](services.py.md) (3 shared connections)
- [_fetch_json](_fetch_json.md) (2 shared connections)
- [Any](Any.md) (1 shared connections)
- [comfyui_switchover](comfyui_switchover.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`
- `src/hal0/api/routes/services_health.py`

## Audit Trail

- EXTRACTED: 36 (82%)
- INFERRED: 8 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*