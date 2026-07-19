# test_discover.py

> 30 nodes · cohesion 0.12

## Key Concepts

- **test_discover.py** (29 connections) — `tests/registry/test_discover.py`
- **find_candidates()** (25 connections) — `src/hal0/registry/discover.py`
- **Path** (21 connections)
- **test_register_candidate_writes_shard_model_file_rows()** (7 connections) — `tests/registry/test_discover.py`
- **test_register_candidate_comfyui_checkpoint_tagged_image()** (6 connections) — `tests/registry/test_discover.py`
- **test_curated_match_by_filename()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_associates_sidecar()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_drops_incomplete_shard_set()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_groups_complete_shard_set()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_no_sidecar_is_none()** (4 connections) — `tests/registry/test_discover.py`
- **test_known_paths_short_circuit()** (4 connections) — `tests/registry/test_discover.py`
- **model_root()** (3 connections) — `tests/registry/test_discover.py`
- **test_capability_guess()** (3 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_skips_noise()** (3 connections) — `tests/registry/test_discover.py`
- **test_suggested_id_normalisation()** (3 connections) — `tests/registry/test_discover.py`
- **vision_root()** (3 connections) — `tests/registry/test_discover.py`
- **test_model_mmproj_defaults_none()** (2 connections) — `tests/registry/test_discover.py`
- **Walk each root and return :class:`CandidateModel`s not already registered.** (1 connections) — `src/hal0/registry/discover.py`
- **Tests for hal0.registry.discover — filesystem scan + auto-register.** (1 connections) — `tests/registry/test_discover.py`
- **A discovered file whose name matches a curated entry's hf_file     must surface** (1 connections) — `tests/registry/test_discover.py`
- **Files already in known_paths must not appear in the candidate list.** (1 connections) — `tests/registry/test_discover.py`
- **A Model with no sidecar carries mmproj=None (the registry contract     the llama** (1 connections) — `tests/registry/test_discover.py`
- **A checkpoint under the ComfyUI models tree registers as image/comfyui,     not t** (1 connections) — `tests/registry/test_discover.py`
- **A model directory laid out like the real chat model + mmproj sidecar.** (1 connections) — `tests/registry/test_discover.py`
- **A *mmproj* file beside a main model attaches to that model's     candidate; the** (1 connections) — `tests/registry/test_discover.py`
- *... and 5 more nodes in this community*

## Relationships

- [scan_and_register](scan_and_register.md) (19 shared connections)
- [register_candidate](register_candidate.md) (9 shared connections)
- [discover.py](discover.py.md) (7 shared connections)
- [_guess_capability](_guess_capability.md) (3 shared connections)
- [_match_curated](_match_curated.md) (1 shared connections)
- [connect](connect.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`
- `tests/registry/test_discover.py`

## Audit Trail

- EXTRACTED: 115 (81%)
- INFERRED: 27 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*