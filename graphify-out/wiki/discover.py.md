# discover.py

> 14 nodes · cohesion 0.18

## Key Concepts

- **discover.py** (14 connections) — `src/hal0/registry/discover.py`
- **Path** (8 connections)
- **_normalise_id()** (6 connections) — `src/hal0/registry/discover.py`
- **_is_skippable()** (5 connections) — `src/hal0/registry/discover.py`
- **_is_mmproj_sidecar()** (4 connections) — `src/hal0/registry/discover.py`
- **_shard_index()** (4 connections) — `src/hal0/registry/discover.py`
- **_shard_key()** (4 connections) — `src/hal0/registry/discover.py`
- **Model discovery — scan filesystem roots and auto-register found models.  The sca** (1 connections) — `src/hal0/registry/discover.py`
- **Turn a basename stem into a registry-friendly id.** (1 connections) — `src/hal0/registry/discover.py`
- **True for a multimodal-projector (mmproj) sidecar file.      Matched by filename** (1 connections) — `src/hal0/registry/discover.py`
- **Return the ``(dir, stem, total)`` grouping key for a shard file, else ``None``.** (1 connections) — `src/hal0/registry/discover.py`
- **Return the 1-based shard index encoded in ``p``'s filename, or ``None``.** (1 connections) — `src/hal0/registry/discover.py`
- **Skip dotfiles, .tmp partials, hash-only blob names, shards, accessory dirs.** (1 connections) — `src/hal0/registry/discover.py`
- **Public wrapper around the internal skip rules (shards, mmproj, hex blobs,     HF** (1 connections) — `src/hal0/registry/discover.py`

## Relationships

- [test_discover.py](test_discover.py.md) (7 shared connections)
- [scan_and_register](scan_and_register.md) (4 shared connections)
- [register_candidate](register_candidate.md) (3 shared connections)
- [_match_curated](_match_curated.md) (2 shared connections)
- [models_service.py](models_service.py.md) (2 shared connections)
- [_guess_capability](_guess_capability.md) (1 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`

## Audit Trail

- EXTRACTED: 49 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*