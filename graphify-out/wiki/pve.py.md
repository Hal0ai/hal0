# pve.py

> 28 nodes · cohesion 0.11

## Key Concepts

- **pve.py** (19 connections) — `src/hal0/hardware/pve.py`
- **Any** (8 connections)
- **_load_pve_config()** (6 connections) — `src/hal0/hardware/pve.py`
- **pve_status()** (6 connections) — `src/hal0/hardware/pve.py`
- **detect_proxmox_host()** (5 connections) — `src/hal0/hardware/pve.py`
- **_fetch_pve_resources()** (5 connections) — `src/hal0/hardware/pve.py`
- **pve_test()** (5 connections) — `src/hal0/hardware/pve.py`
- **_summarise()** (5 connections) — `src/hal0/hardware/pve.py`
- **_is_lxc_init()** (4 connections) — `src/hal0/hardware/pve.py`
- **project_slim()** (4 connections) — `src/hal0/hardware/pve.py`
- **PveDetectionState** (4 connections) — `src/hal0/hardware/pve.py`
- **_lxc_via_cgroup_v1()** (3 connections) — `src/hal0/hardware/pve.py`
- **_lxc_via_cgroup_v2_marker()** (3 connections) — `src/hal0/hardware/pve.py`
- **pop_transition()** (3 connections) — `src/hal0/hardware/pve.py`
- **_has_pve_kernel()** (2 connections) — `src/hal0/hardware/pve.py`
- **StrEnum** (1 connections)
- **Proxmox host-pressure probe.  Optional integration: on Strix Halo (and any other** (1 connections) — `src/hal0/hardware/pve.py`
- **Blocking GET /api2/json/cluster/resources. Run via to_thread.** (1 connections) — `src/hal0/hardware/pve.py`
- **Reduce /cluster/resources entries to the dashboard's flat shape.** (1 connections) — `src/hal0/hardware/pve.py`
- **Confidence that the current host is a Proxmox-managed LXC.      Used only when /** (1 connections) — `src/hal0/hardware/pve.py`
- **Legacy cgroup-v1 pattern: lxc.payload.<vmid>/… or /lxc/<vmid>/…** (1 connections) — `src/hal0/hardware/pve.py`
- **cgroup-v2 unified LXCs have a /init.scope cgroup path that looks     identical t** (1 connections) — `src/hal0/hardware/pve.py`
- **Best-effort detection of whether hal0 is running inside a Proxmox LXC.      Sign** (1 connections) — `src/hal0/hardware/pve.py`
- **Strip per-tenant + unused-scalar fields from a full pve_status dict.      /api/s** (1 connections) — `src/hal0/hardware/pve.py`
- **Return ``"became_broken"`` / ``"recovered"`` / ``None``.      Pass the most rece** (1 connections) — `src/hal0/hardware/pve.py`
- *... and 3 more nodes in this community*

## Relationships

- [pve_config_path](pve_config_path.md) (6 shared connections)
- [Enum](Enum.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [sample](sample.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/pve.py`

## Audit Trail

- EXTRACTED: 89 (94%)
- INFERRED: 6 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*