# PortAuthority

> 36 nodes · cohesion 0.12

## Key Concepts

- **PortAuthority** (25 connections) — `src/hal0/ports/authority.py`
- **Connection** (16 connections)
- **._write()** (9 connections) — `src/hal0/ports/authority.py`
- **.acquire()** (8 connections) — `src/hal0/ports/authority.py`
- **._read()** (8 connections) — `src/hal0/ports/authority.py`
- **._listener_ports()** (7 connections) — `src/hal0/ports/authority.py`
- **.claims()** (6 connections) — `src/hal0/ports/authority.py`
- **.conflicts()** (6 connections) — `src/hal0/ports/authority.py`
- **._live_ports()** (6 connections) — `src/hal0/ports/authority.py`
- **.next_free()** (6 connections) — `src/hal0/ports/authority.py`
- **.reconcile_listeners()** (6 connections) — `src/hal0/ports/authority.py`
- **.reallocate()** (5 connections) — `src/hal0/ports/authority.py`
- **.reconcile()** (5 connections) — `src/hal0/ports/authority.py`
- **.release()** (5 connections) — `src/hal0/ports/authority.py`
- **.held_by()** (4 connections) — `src/hal0/ports/authority.py`
- **.is_free()** (4 connections) — `src/hal0/ports/authority.py`
- **.is_held_by_other()** (4 connections) — `src/hal0/ports/authority.py`
- **.release_port()** (4 connections) — `src/hal0/ports/authority.py`
- **.reserve()** (4 connections) — `src/hal0/ports/authority.py`
- **Any** (4 connections)
- **.__init__()** (3 connections) — `src/hal0/ports/authority.py`
- **.pool()** (1 connections) — `src/hal0/ports/authority.py`
- **Path** (1 connections)
- **Ports in LISTEN state inside the pool (best-effort psutil scan).** (1 connections) — `src/hal0/ports/authority.py`
- **Lowest free port in the pool, honouring ``preferred`` when free.** (1 connections) — `src/hal0/ports/authority.py`
- *... and 11 more nodes in this community*

## Relationships

- [authority.py](authority.py.md) (5 shared connections)
- [connect](connect.md) (3 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [_stores](_stores.md) (1 shared connections)
- [test_id_keying.py](test_id_keying.py.md) (1 shared connections)
- [SlotInterface](SlotInterface.md) (1 shared connections)
- [__init__.py](__init__.py.md) (1 shared connections)
- [tx](tx.md) (1 shared connections)

## Source Files

- `src/hal0/ports/authority.py`

## Audit Trail

- EXTRACTED: 151 (94%)
- INFERRED: 9 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*