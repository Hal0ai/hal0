# proxmox.py

> 20 nodes

## Key Concepts

- **proxmox.py** (9 connections) — `src/hal0/api/routes/proxmox.py`
- **put_proxmox_config()** (9 connections) — `src/hal0/api/routes/proxmox.py`
- **test_proxmox_config()** (7 connections) — `src/hal0/api/routes/proxmox.py`
- **_load_for_get()** (6 connections) — `src/hal0/api/routes/proxmox.py`
- **Any** (5 connections)
- **get_proxmox_config()** (5 connections) — `src/hal0/api/routes/proxmox.py`
- **delete_proxmox_config()** (5 connections) — `src/hal0/api/routes/proxmox.py`
- **ProxmoxConfigBody** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **ProxmoxTestBody** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **_validation_details()** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **Request** (3 connections)
- **ValidationError** (1 connections)
- **Proxmox-integration settings endpoints (mounted under /api/settings).  Lets oper** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Request shape for PUT /api/settings/proxmox.      ``token_value`` is optional on** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Request shape for POST /api/settings/proxmox/test.      Same as ProxmoxConfigBod** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Read the raw config (token included) to learn what's persisted.      Used by GET** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Return the current Proxmox integration state.      Shape:         {           co** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Write /etc/hal0/proxmox.json from the supplied body.      If ``token_value`` is** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Remove the Proxmox config file (returns to 'not configured').** (1 connections) — `src/hal0/api/routes/proxmox.py`
- **Validate a candidate config WITHOUT writing it.      Used by the Settings UI's '** (1 connections) — `src/hal0/api/routes/proxmox.py`

## Relationships

- [errors.py](errors.py.md) (3 shared connections)
- [BaseModel](BaseModel.md) (2 shared connections)
- [pve.py](pve.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/proxmox.py`

## Audit Trail

- EXTRACTED: 66 (94%)
- INFERRED: 4 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*