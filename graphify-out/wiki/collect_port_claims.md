# collect_port_claims

> 10 nodes · cohesion 0.29

## Key Concepts

- **collect_port_claims()** (6 connections) — `src/hal0/slots/port_alloc.py`
- **reject_port_conflict()** (6 connections) — `src/hal0/slots/port_alloc.py`
- **port_alloc.py** (5 connections) — `src/hal0/slots/port_alloc.py`
- **next_free_slot_port()** (5 connections) — `src/hal0/slots/port_alloc.py`
- **slot_port_range()** (5 connections) — `src/hal0/slots/port_alloc.py`
- **Slot port allocation + conflict rejection.  Extracted from ``routes/slots.py`` (** (1 connections) — `src/hal0/slots/port_alloc.py`
- **409-style 400 when an explicitly requested port is already owned.** (1 connections) — `src/hal0/slots/port_alloc.py`
- **Resolve the slot port pool: hal0.toml ``[slots]`` or the schema pool.      ``Slo** (1 connections) — `src/hal0/slots/port_alloc.py`
- **Every known claim in the pool via the central registry (hal0.ports).      Config** (1 connections) — `src/hal0/slots/port_alloc.py`
- **Return the next free port in the configured slot range (#275 bug 2).      Free =** (1 connections) — `src/hal0/slots/port_alloc.py`

## Relationships

- [__init__.py](__init__.py.md) (2 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [slots_config_dir](slots_config_dir.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `src/hal0/slots/port_alloc.py`

## Audit Trail

- EXTRACTED: 26 (81%)
- INFERRED: 6 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*