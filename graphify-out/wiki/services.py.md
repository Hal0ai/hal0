# services.py

> 27 nodes · cohesion 0.11

## Key Concepts

- **services.py** (11 connections) — `src/hal0/api/routes/services.py`
- **_probe()** (9 connections) — `src/hal0/api/routes/services.py`
- **service_action()** (8 connections) — `src/hal0/api/routes/services.py`
- **ServiceDef** (8 connections) — `src/hal0/services/registry.py`
- **list_services()** (7 connections) — `src/hal0/api/routes/services.py`
- **mdns_apply()** (6 connections) — `src/hal0/api/routes/services.py`
- **Any** (5 connections)
- **service_by_id()** (5 connections) — `src/hal0/services/registry.py`
- **_mdns_url()** (4 connections) — `src/hal0/api/routes/services.py`
- **_probe_http_env()** (4 connections) — `src/hal0/api/routes/services.py`
- **_public_url()** (4 connections) — `src/hal0/api/routes/services.py`
- **Request** (4 connections)
- **mdns_status()** (3 connections) — `src/hal0/api/routes/services.py`
- **registry.py** (3 connections) — `src/hal0/services/registry.py`
- **test_registry_comfyui_restart_only()** (2 connections) — `tests/api/test_services_page.py`
- **Companion-service management routes (mounted under /api/services).  The richer s** (1 connections) — `src/hal0/api/routes/services.py`
- **Dispatch to the service's probe strategy → (up, detail, stat).** (1 connections) — `src/hal0/api/routes/services.py`
- **Full management view of every registered companion service.      Never 500s — ev** (1 connections) — `src/hal0/api/routes/services.py`
- **Run one lifecycle verb against a registered service's unit.      Body: ``{"actio** (1 connections) — `src/hal0/api/routes/services.py`
- **avahi/mDNS discovery status + which addon services we advertise.** (1 connections) — `src/hal0/api/routes/services.py`
- **Advertise (or withdraw) the addon services over mDNS.      Body: ``{"advertise":** (1 connections) — `src/hal0/api/routes/services.py`
- **Operator-declared public URL (reverse-proxy deploys), or None.** (1 connections) — `src/hal0/api/routes/services.py`
- **``http://<host>.local:<port>`` when this service is being advertised.** (1 connections) — `src/hal0/api/routes/services.py`
- **Generic loopback HTTP probe with env override; honest when unwired.** (1 connections) — `src/hal0/api/routes/services.py`
- **Declarative catalog of hal0 companion services.  One ``ServiceDef`` per service,** (1 connections) — `src/hal0/services/registry.py`
- *... and 2 more nodes in this community*

## Relationships

- [config.py](config.py.md) (5 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (3 shared connections)
- [record_action](record_action.md) (2 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [test_services_page.py](test_services_page.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/services.py`
- `src/hal0/services/registry.py`
- `tests/api/test_services_page.py`

## Audit Trail

- EXTRACTED: 83 (87%)
- INFERRED: 12 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*