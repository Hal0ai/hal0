# _container_runtime

> 19 nodes

## Key Concepts

- **_container_runtime()** (6 connections) — `src/hal0/providers/container.py`
- **.__init__()** (6 connections) — `src/hal0/slots/migrate_id_keying.py`
- **slot_container_name()** (6 connections) — `src/hal0/slots/naming.py`
- **naming.py** (5 connections) — `src/hal0/slots/naming.py`
- **.running_image()** (4 connections) — `src/hal0/providers/container.py`
- **.running_argv()** (4 connections) — `src/hal0/providers/container.py`
- **slot_unit_name()** (4 connections) — `src/hal0/slots/naming.py`
- **slot_quadlet_name()** (4 connections) — `src/hal0/slots/naming.py`
- **.image_present()** (3 connections) — `src/hal0/providers/container.py`
- **.pull_image_stream()** (3 connections) — `src/hal0/providers/container.py`
- **Resolve the podman binary path (docker is unsupported).      Priority: $HAL0_CON** (1 connections) — `src/hal0/providers/container.py`
- **Return True if ``image`` is in the local container image store.          Uses ``** (1 connections) — `src/hal0/providers/container.py`
- **Return the image ref of the running container for *slot_name* (#663).          D** (1 connections) — `src/hal0/providers/container.py`
- **Return the live container command argv for *slot_name*.          Uses ``<runtime** (1 connections) — `src/hal0/providers/container.py`
- **Async generator that runs ``<runtime> pull <image>`` and yields         layer-pr** (1 connections) — `src/hal0/providers/container.py`
- **Slot artefact naming — the ONE seam the M5 id-flip changes (§11.1 / P3-quadlet).** (1 connections) — `src/hal0/slots/naming.py`
- **The systemd **service** name for a slot (what ``systemctl`` verbs target).** (1 connections) — `src/hal0/slots/naming.py`
- **The Podman Quadlet ``.container`` source filename for a slot.** (1 connections) — `src/hal0/slots/naming.py`
- **The running podman container name (Quadlet ``ContainerName=`` default).      ``h** (1 connections) — `src/hal0/slots/naming.py`

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [ContainerProvider](ContainerProvider.md) (4 shared connections)
- [migrate_slot_id_keying](migrate_slot_id_keying.md) (2 shared connections)
- [SystemCtlSeam](SystemCtlSeam.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `src/hal0/slots/migrate_id_keying.py`
- `src/hal0/slots/naming.py`

## Audit Trail

- EXTRACTED: 40 (74%)
- INFERRED: 14 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*