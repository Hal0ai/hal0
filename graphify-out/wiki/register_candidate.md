# register_candidate

> 9 nodes · cohesion 0.28

## Key Concepts

- **register_candidate()** (11 connections) — `src/hal0/registry/discover.py`
- **_maybe_register_shard_files()** (9 connections) — `src/hal0/registry/discover.py`
- **CandidateModel** (7 connections) — `src/hal0/registry/discover.py`
- **test_register_candidate_curated_uses_curated_id()** (5 connections) — `tests/registry/test_discover.py`
- **test_register_candidate_non_curated_uses_suggested_id()** (5 connections) — `tests/registry/test_discover.py`
- **ModelRegistry** (4 connections)
- **One discovered file ready for registry registration.** (1 connections) — `src/hal0/registry/discover.py`
- **Build a :class:`Model` from ``candidate`` and add it to ``registry``.** (1 connections) — `src/hal0/registry/discover.py`
- **Best-effort ``model_file`` rows for a discovered shard group.      Only applies** (1 connections) — `src/hal0/registry/discover.py`

## Relationships

- [test_discover.py](test_discover.py.md) (9 shared connections)
- [scan_and_register](scan_and_register.md) (5 shared connections)
- [discover.py](discover.py.md) (3 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (3 shared connections)
- [CuratedModel](CuratedModel.md) (1 shared connections)
- [Model](Model.md) (1 shared connections)
- [connect](connect.md) (1 shared connections)
- [tx](tx.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`
- `tests/registry/test_discover.py`

## Audit Trail

- EXTRACTED: 31 (70%)
- INFERRED: 13 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*