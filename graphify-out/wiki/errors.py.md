# errors.py

> 71 nodes · cohesion 0.04

## Key Concepts

- **errors.py** (75 connections) — `src/hal0/errors.py`
- **Hal0Error** (26 connections) — `src/hal0/errors.py`
- **agents.py** (9 connections) — `src/hal0/api/routes/agents.py`
- **proxmox.py** (9 connections) — `src/hal0/api/routes/proxmox.py`
- **put_proxmox_config()** (9 connections) — `src/hal0/api/routes/proxmox.py`
- **restart_agent()** (8 connections) — `src/hal0/api/agents/restart.py`
- **install_agent()** (7 connections) — `src/hal0/api/routes/agents.py`
- **test_proxmox_config()** (7 connections) — `src/hal0/api/routes/proxmox.py`
- **restart.py** (6 connections) — `src/hal0/api/agents/restart.py`
- **error_codes.py** (6 connections) — `src/hal0/api/middleware/error_codes.py`
- **_manager()** (6 connections) — `src/hal0/api/routes/agents.py`
- **_load_for_get()** (6 connections) — `src/hal0/api/routes/proxmox.py`
- **uninstall_agent()** (5 connections) — `src/hal0/api/routes/agents.py`
- **delete_proxmox_config()** (5 connections) — `src/hal0/api/routes/proxmox.py`
- **get_proxmox_config()** (5 connections) — `src/hal0/api/routes/proxmox.py`
- **Any** (5 connections)
- **_resolve_actor()** (4 connections) — `src/hal0/api/agents/restart.py`
- **_shape_validation_errors()** (4 connections) — `src/hal0/api/middleware/error_codes.py`
- **agent_activity()** (4 connections) — `src/hal0/api/routes/agents.py`
- **ProxmoxConfigBody** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **ProxmoxTestBody** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **_validation_details()** (4 connections) — `src/hal0/api/routes/proxmox.py`
- **MultiStatus** (4 connections) — `src/hal0/errors.py`
- **_systemctl_path()** (3 connections) — `src/hal0/api/agents/restart.py`
- **_unit_name()** (3 connections) — `src/hal0/api/agents/restart.py`
- *... and 46 more nodes in this community*

## Relationships

- [BoardStore](BoardStore.md) (11 shared connections)
- [BadRequest](BadRequest.md) (5 shared connections)
- [personas.py](personas.py.md) (3 shared connections)
- [auth.py](auth.py.md) (3 shared connections)
- [FakeManager](FakeManager.md) (3 shared connections)
- [comfyui.py](comfyui.py.md) (2 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (2 shared connections)
- [_profile](_profile.md) (2 shared connections)
- [get_runner](get_runner.md) (2 shared connections)
- [resolve_argv](resolve_argv.md) (2 shared connections)
- [StacksCatalog](StacksCatalog.md) (2 shared connections)
- [AgentManager](AgentManager.md) (1 shared connections)

## Source Files

- `src/hal0/api/agents/restart.py`
- `src/hal0/api/middleware/error_codes.py`
- `src/hal0/api/routes/agents.py`
- `src/hal0/api/routes/proxmox.py`
- `src/hal0/errors.py`

## Audit Trail

- EXTRACTED: 266 (90%)
- INFERRED: 28 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*