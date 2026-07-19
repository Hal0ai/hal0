# PortAuthority

> 45 nodes

## Key Concepts

- **PortAuthority** (25 connections) — `src/hal0/ports/authority.py`
- **Connection** (16 connections)
- **._write()** (9 connections) — `src/hal0/ports/authority.py`
- **._read()** (8 connections) — `src/hal0/ports/authority.py`
- **.acquire()** (8 connections) — `src/hal0/ports/authority.py`
- **._listener_ports()** (7 connections) — `src/hal0/ports/authority.py`
- **authority.py** (6 connections) — `src/hal0/ports/authority.py`
- **.claims()** (6 connections) — `src/hal0/ports/authority.py`
- **._live_ports()** (6 connections) — `src/hal0/ports/authority.py`
- **.next_free()** (6 connections) — `src/hal0/ports/authority.py`
- **.conflicts()** (6 connections) — `src/hal0/ports/authority.py`
- **.reconcile_listeners()** (6 connections) — `src/hal0/ports/authority.py`
- **PortPoolExhausted** (5 connections) — `src/hal0/ports/authority.py`
- **AuthorityClaim** (5 connections) — `src/hal0/ports/authority.py`
- **.reallocate()** (5 connections) — `src/hal0/ports/authority.py`
- **.release()** (5 connections) — `src/hal0/ports/authority.py`
- **.reconcile()** (5 connections) — `src/hal0/ports/authority.py`
- **Any** (4 connections)
- **_row_to_claim()** (4 connections) — `src/hal0/ports/authority.py`
- **.is_free()** (4 connections) — `src/hal0/ports/authority.py`
- **.held_by()** (4 connections) — `src/hal0/ports/authority.py`
- **.is_held_by_other()** (4 connections) — `src/hal0/ports/authority.py`
- **.reserve()** (4 connections) — `src/hal0/ports/authority.py`
- **.release_port()** (4 connections) — `src/hal0/ports/authority.py`
- **.__init__()** (3 connections) — `src/hal0/ports/authority.py`
- *... and 20 more nodes in this community*

## Relationships

- [connect](connect.md) (4 shared connections)
- [AgentMCPClient](AgentMCPClient.md) (2 shared connections)
- [_stores](_stores.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [test_id_keying.py](test_id_keying.py.md) (1 shared connections)
- [_make_env](_make_env.md) (1 shared connections)
- [__init__.py](__init__.py.md) (1 shared connections)

## Source Files

- `src/hal0/ports/authority.py`

## Audit Trail

- EXTRACTED: 176 (95%)
- INFERRED: 10 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*