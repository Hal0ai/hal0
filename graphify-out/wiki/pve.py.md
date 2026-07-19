# pve.py

> 36 nodes

## Key Concepts

- **pve.py** (19 connections) — `src/hal0/hardware/pve.py`
- **Any** (8 connections)
- **pve_config_path()** (6 connections) — `src/hal0/hardware/pve.py`
- **_load_pve_config()** (6 connections) — `src/hal0/hardware/pve.py`
- **save_pve_config()** (6 connections) — `src/hal0/hardware/pve.py`
- **pve_status()** (6 connections) — `src/hal0/hardware/pve.py`
- **_fetch_pve_resources()** (5 connections) — `src/hal0/hardware/pve.py`
- **_summarise()** (5 connections) — `src/hal0/hardware/pve.py`
- **detect_proxmox_host()** (5 connections) — `src/hal0/hardware/pve.py`
- **pve_test()** (5 connections) — `src/hal0/hardware/pve.py`
- **delete_pve_config()** (4 connections) — `src/hal0/hardware/pve.py`
- **PveDetectionState** (4 connections) — `src/hal0/hardware/pve.py`
- **_is_lxc_init()** (4 connections) — `src/hal0/hardware/pve.py`
- **project_slim()** (4 connections) — `src/hal0/hardware/pve.py`
- **invalidate_pve_cache()** (4 connections) — `src/hal0/hardware/pve.py`
- **_lxc_via_cgroup_v1()** (3 connections) — `src/hal0/hardware/pve.py`
- **_lxc_via_cgroup_v2_marker()** (3 connections) — `src/hal0/hardware/pve.py`
- **pop_transition()** (3 connections) — `src/hal0/hardware/pve.py`
- **Path** (2 connections)
- **_has_pve_kernel()** (2 connections) — `src/hal0/hardware/pve.py`
- **Proxmox host-pressure probe.  Optional integration: on Strix Halo (and any other** (1 connections) — `src/hal0/hardware/pve.py`
- **Return /etc/hal0/proxmox.json (or HAL0_HOME-rooted equivalent).** (1 connections) — `src/hal0/hardware/pve.py`
- **Read /etc/hal0/proxmox.json and return the parsed dict, or None.      Returns No** (1 connections) — `src/hal0/hardware/pve.py`
- **Atomically write /etc/hal0/proxmox.json from a flat payload.      Accepts the sa** (1 connections) — `src/hal0/hardware/pve.py`
- **Remove /etc/hal0/proxmox.json. Returns True iff the file existed.** (1 connections) — `src/hal0/hardware/pve.py`
- *... and 11 more nodes in this community*

## Relationships

- [Enum](Enum.md) (1 shared connections)
- [proxmox.py](proxmox.py.md) (1 shared connections)
- [die](die.md) (1 shared connections)
- [HardwareInfo](HardwareInfo.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/pve.py`

## Audit Trail

- EXTRACTED: 114 (95%)
- INFERRED: 6 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*