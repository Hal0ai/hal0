# pve_config_path

> 9 nodes · cohesion 0.28

## Key Concepts

- **pve_config_path()** (6 connections) — `src/hal0/hardware/pve.py`
- **save_pve_config()** (6 connections) — `src/hal0/hardware/pve.py`
- **delete_pve_config()** (4 connections) — `src/hal0/hardware/pve.py`
- **invalidate_pve_cache()** (4 connections) — `src/hal0/hardware/pve.py`
- **Path** (2 connections)
- **Remove /etc/hal0/proxmox.json. Returns True iff the file existed.** (1 connections) — `src/hal0/hardware/pve.py`
- **Drop the cached pve_status result so the next call re-fetches.** (1 connections) — `src/hal0/hardware/pve.py`
- **Return /etc/hal0/proxmox.json (or HAL0_HOME-rooted equivalent).** (1 connections) — `src/hal0/hardware/pve.py`
- **Atomically write /etc/hal0/proxmox.json from a flat payload.      Accepts the sa** (1 connections) — `src/hal0/hardware/pve.py`

## Relationships

- [pve.py](pve.py.md) (6 shared connections)

## Source Files

- `src/hal0/hardware/pve.py`

## Audit Trail

- EXTRACTED: 26 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*