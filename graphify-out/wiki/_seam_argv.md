# ._seam_argv

> 20 nodes

## Key Concepts

- **._seam_argv()** (7 connections) — `src/hal0/system/seam.py`
- **_slot_id_from_unit()** (5 connections) — `src/hal0/system/seam.py`
- **.write_unit()** (5 connections) — `src/hal0/system/seam.py`
- **.remove_unit()** (5 connections) — `src/hal0/system/seam.py`
- **.write_quadlet()** (5 connections) — `src/hal0/system/seam.py`
- **.remove_quadlet()** (5 connections) — `src/hal0/system/seam.py`
- **.systemctl()** (5 connections) — `src/hal0/system/seam.py`
- **_slot_id_from_quadlet()** (4 connections) — `src/hal0/system/seam.py`
- **Path** (4 connections)
- **.restart_self()** (4 connections) — `src/hal0/system/seam.py`
- **CompletedProcess** (3 connections)
- **.__init__()** (2 connections) — `src/hal0/system/seam.py`
- **Extract ``<id>`` from ``hal0-slot@<id>.service``, or ``None`` if the     unit na** (1 connections) — `src/hal0/system/seam.py`
- **Extract ``<token>`` from ``hal0-slot@<token>.container``, or ``None``.** (1 connections) — `src/hal0/system/seam.py`
- **Write a ``hal0-slot@<id>.service`` unit file.** (1 connections) — `src/hal0/system/seam.py`
- **Delete a ``hal0-slot@<id>.service`` unit file (no-op if absent).** (1 connections) — `src/hal0/system/seam.py`
- **Write a ``hal0-slot@<token>.container`` Quadlet source file (P3-quadlet).** (1 connections) — `src/hal0/system/seam.py`
- **Delete a ``hal0-slot@<token>.container`` Quadlet file (no-op if absent).** (1 connections) — `src/hal0/system/seam.py`
- **Run ``systemctl <args...>``, routing daemon-reload + hal0-slot@         unit ver** (1 connections) — `src/hal0/system/seam.py`
- **``systemctl restart hal0-api.service`` — the self-update path.** (1 connections) — `src/hal0/system/seam.py`

## Relationships

- [SystemCtlSeam](SystemCtlSeam.md) (8 shared connections)
- [RuntimeError](RuntimeError.md) (2 shared connections)

## Source Files

- `src/hal0/system/seam.py`

## Audit Trail

- EXTRACTED: 62 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*