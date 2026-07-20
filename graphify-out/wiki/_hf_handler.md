# _hf_handler

> 28 nodes · cohesion 0.12

## Key Concepts

- **_hf_handler()** (14 connections) — `tests/api/test_models_routes.py`
- **_patch_httpx_transport()** (14 connections) — `tests/api/test_models_routes.py`
- **MonkeyPatch** (13 connections)
- **test_inspect_accepts_hf_url_alias()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_falls_back_to_top_level_size_when_no_lfs()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_ignores_non_mmproj_non_gguf_files()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_returns_404_when_repo_missing()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_returns_502_when_hf_unreachable()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_returns_gguf_variants_sorted_by_size()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_safetensors_repo_is_not_flm()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_surfaces_bare_mmproj_sidecar()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_surfaces_flm_npu_repo()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_caches_response_for_repeated_calls()** (5 connections) — `tests/api/test_models_routes.py`
- **test_inspect_response_is_application_json()** (5 connections) — `tests/api/test_models_routes.py`
- **Any** (1 connections)
- **Exception** (1 connections)
- **Build an httpx MockTransport handler for the inspect tests.** (1 connections) — `tests/api/test_models_routes.py`
- **Patch ``httpx.AsyncClient`` so the inspect route uses our mock transport.      T** (1 connections) — `tests/api/test_models_routes.py`
- **The route surfaces .gguf entries with LFS size + sorts ascending.** (1 connections) — `tests/api/test_models_routes.py`
- **A ``.mmproj`` sidecar (no ``.gguf`` suffix) must appear as a variant.      Regre** (1 connections) — `tests/api/test_models_routes.py`
- **An FLM/NPU repo (config.json + tokenizer + ``.q4nx`` weights, no GGUF)     surfa** (1 connections) — `tests/api/test_models_routes.py`
- **A plain safetensors transformers repo shares config.json + tokenizer but     mus** (1 connections) — `tests/api/test_models_routes.py`
- **A stray ``.mmproj``-less, ``.gguf``-less file stays out of variants.** (1 connections) — `tests/api/test_models_routes.py`
- **``hf_url`` is accepted as an alias for ``hf_repo``.** (1 connections) — `tests/api/test_models_routes.py`
- **The 5 minute in-process cache prevents a second HF hit on the     second click.** (1 connections) — `tests/api/test_models_routes.py`
- *... and 3 more nodes in this community*

## Relationships

- [test_models_routes.py](test_models_routes.py.md) (25 shared connections)

## Source Files

- `tests/api/test_models_routes.py`

## Audit Trail

- EXTRACTED: 119 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*