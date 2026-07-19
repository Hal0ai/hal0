# __init__.py

> 22 nodes

## Key Concepts

- **__init__.py** (11 connections) — `src/hal0/ports/__init__.py`
- **collect_claims()** (11 connections) — `src/hal0/ports/__init__.py`
- **PortClaim** (10 connections) — `src/hal0/ports/__init__.py`
- **port_report()** (9 connections) — `src/hal0/ports/__init__.py`
- **conflicts()** (7 connections) — `src/hal0/ports/__init__.py`
- **Any** (5 connections)
- **_listener_claims()** (5 connections) — `src/hal0/ports/__init__.py`
- **_owners()** (5 connections) — `src/hal0/ports/__init__.py`
- **claimed_by_other()** (5 connections) — `src/hal0/ports/__init__.py`
- **.as_dict()** (4 connections) — `src/hal0/ports/__init__.py`
- **_config_claims()** (4 connections) — `src/hal0/ports/__init__.py`
- **_runtime_claims()** (4 connections) — `src/hal0/ports/__init__.py`
- **next_free()** (4 connections) — `src/hal0/ports/__init__.py`
- **Path** (3 connections)
- **Central port-claim registry — one authority for who owns which port.  Motivation** (1 connections) — `src/hal0/ports/__init__.py`
- **Sockets in LISTEN state within the pool — the reality check.      Best-effort: p** (1 connections) — `src/hal0/ports/__init__.py`
- **Aggregate every known claim, deduplicated on (port, owner, source).** (1 connections) — `src/hal0/ports/__init__.py`
- **Distinct owners of ``port`` — bare listeners fold into a slot owner     on the s** (1 connections) — `src/hal0/ports/__init__.py`
- **Ports with more than one distinct owner.      Owners that all belong to one co-r** (1 connections) — `src/hal0/ports/__init__.py`
- **Lowest port in [start, end] with NO claim from any source.** (1 connections) — `src/hal0/ports/__init__.py`
- **Owners other than ``owner`` holding ``port`` (for create/edit checks).** (1 connections) — `src/hal0/ports/__init__.py`
- **The full picture: pool, per-port claims, conflicts, next free.      ``authority_** (1 connections) — `src/hal0/ports/__init__.py`

## Relationships

- [collect_port_claims](collect_port_claims.md) (2 shared connections)
- [PortAuthority](PortAuthority.md) (1 shared connections)
- [RoutingHost](RoutingHost.md) (1 shared connections)
- [slots_config_dir](slots_config_dir.md) (1 shared connections)

## Source Files

- `src/hal0/ports/__init__.py`

## Audit Trail

- EXTRACTED: 90 (95%)
- INFERRED: 5 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*