# config.py

> 30 nodes · cohesion 0.11

## Key Concepts

- **config.py** (12 connections) — `src/hal0/api/routes/config.py`
- **update_models_config()** (12 connections) — `src/hal0/api/routes/config.py`
- **get_urls()** (9 connections) — `src/hal0/api/routes/config.py`
- **_browser_url()** (9 connections) — `src/hal0/api/routes/services.py`
- **_comfyui_link()** (7 connections) — `src/hal0/api/routes/config.py`
- **_behind_proxy()** (6 connections) — `src/hal0/api/routes/config.py`
- **_resolve_host()** (6 connections) — `src/hal0/api/routes/config.py`
- **get_models_config()** (5 connections) — `src/hal0/api/routes/config.py`
- **_host_without_port()** (5 connections) — `src/hal0/api/routes/config.py`
- **Request** (5 connections)
- **ConfigInvalidError** (4 connections) — `src/hal0/api/routes/config.py`
- **_validation_error_details()** (4 connections) — `src/hal0/api/routes/config.py`
- **_api_port()** (3 connections) — `src/hal0/api/routes/config.py`
- **_openwebui_is_active()** (3 connections) — `src/hal0/api/routes/config.py`
- **Any** (2 connections)
- **Hal0Error** (2 connections)
- **ValidationError** (1 connections)
- **Config + URL discovery endpoints (mounted under /api/config).  The dashboard rea** (1 connections) — `src/hal0/api/routes/config.py`
- **True when the request arrived through a reverse proxy.      Detected via the pro** (1 connections) — `src/hal0/api/routes/config.py`
- **Return a hostname suitable for adding the OpenWebUI fixed port.** (1 connections) — `src/hal0/api/routes/config.py`
- **Resolve the link the dashboard should use to open ComfyUI.      ``HAL0_COMFYUI_P** (1 connections) — `src/hal0/api/routes/config.py`
- **Return the canonical URLs the dashboard should advertise.      Response shape (s** (1 connections) — `src/hal0/api/routes/config.py`
- **Return the current [models] section (roots / auto-scan / extensions).      Route** (1 connections) — `src/hal0/api/routes/config.py`
- **Replace the [models] section, persist hal0.toml, then re-scan.      Body shape:** (1 connections) — `src/hal0/api/routes/config.py`
- **Schema validation failure for the [models] section.** (1 connections) — `src/hal0/api/routes/config.py`
- *... and 5 more nodes in this community*

## Relationships

- [services.py](services.py.md) (5 shared connections)
- [load_hal0_config](load_hal0_config.md) (3 shared connections)
- [test_redact.py](test_redact.py.md) (2 shared connections)
- [ModelsConfig](ModelsConfig.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/config.py`
- `src/hal0/api/routes/services.py`

## Audit Trail

- EXTRACTED: 96 (89%)
- INFERRED: 12 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*