# Engine

> 21 nodes

## Key Concepts

- **Engine** (15 connections) — `src/hal0/bench/schema.py`
- **cell_key()** (14 connections) — `src/hal0/bench/schema.py`
- **_identity()** (12 connections) — `tests/bench/test_schema.py`
- **test_schema.py** (10 connections) — `tests/bench/test_schema.py`
- **test_identity_change_changes_cell_key()** (6 connections) — `tests/bench/test_schema.py`
- **canonical_json()** (5 connections) — `src/hal0/bench/schema.py`
- **test_host_does_not_change_cell_key()** (5 connections) — `tests/bench/test_schema.py`
- **test_record_post_init_fills_cell_key()** (5 connections) — `tests/bench/test_schema.py`
- **test_to_dict_flattens_outcome_enum()** (4 connections) — `tests/bench/test_schema.py`
- **.to_dict()** (3 connections) — `src/hal0/bench/schema.py`
- **Any** (3 connections)
- **test_cell_key_is_deterministic()** (3 connections) — `tests/bench/test_schema.py`
- **test_cell_key_canonicalizes_key_order()** (3 connections) — `tests/bench/test_schema.py`
- **test_cell_key_prefix()** (3 connections) — `tests/bench/test_schema.py`
- **.__post_init__()** (2 connections) — `src/hal0/bench/schema.py`
- **test_canonical_json_is_sorted_and_compact()** (2 connections) — `tests/bench/test_schema.py`
- **The engine + the local-only runner image that built it. ``image_digest``     and** (1 connections) — `src/hal0/bench/schema.py`
- **Plain-dict JSON shape for the store. ``outcome`` is flattened to its         str** (1 connections) — `src/hal0/bench/schema.py`
- **Deterministic JSON: sorted keys, no insignificant whitespace. This is the     ca** (1 connections) — `src/hal0/bench/schema.py`
- **The dedup/staleness key: ``sha256:`` + hex of the canonical-JSON identity     bl** (1 connections) — `src/hal0/bench/schema.py`
- **test_schema.py — cell_key stability, canonicalization, and the identity/host spl** (1 connections) — `tests/bench/test_schema.py`

## Relationships

- [planner.py](planner.py.md) (12 shared connections)
- [runner.py](runner.py.md) (12 shared connections)
- [parsers.py](parsers.py.md) (4 shared connections)
- [cli.py](cli.py.md) (2 shared connections)

## Source Files

- `src/hal0/bench/schema.py`
- `tests/bench/test_schema.py`

## Audit Trail

- EXTRACTED: 69 (69%)
- INFERRED: 31 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*