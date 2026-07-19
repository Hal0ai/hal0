# huggingface.py

> 21 nodes

## Key Concepts

- **huggingface.py** (11 connections) — `src/hal0/upstreams/huggingface.py`
- **fetch_repo()** (10 connections) — `src/hal0/upstreams/huggingface.py`
- **search_models()** (5 connections) — `src/hal0/upstreams/huggingface.py`
- **HFUpstreamError** (4 connections) — `src/hal0/upstreams/huggingface.py`
- **_hf_headers()** (4 connections) — `src/hal0/upstreams/huggingface.py`
- **_normalise_search_row()** (4 connections) — `src/hal0/upstreams/huggingface.py`
- **Any** (4 connections)
- **_extract_readme_excerpt()** (4 connections) — `src/hal0/upstreams/huggingface.py`
- **normalise_repo_slug()** (3 connections) — `src/hal0/upstreams/huggingface.py`
- **_looks_like_flm_repo()** (3 connections) — `src/hal0/upstreams/huggingface.py`
- **_format_size()** (3 connections) — `src/hal0/upstreams/huggingface.py`
- **Unified HuggingFace Hub HTTP client.  Consolidates what used to be two independe** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **502 — fetching huggingface.co failed (network, 5xx, or unparseable).** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Shared request headers, forwarding ``HF_TOKEN``/``HUGGING_FACE_HUB_TOKEN`` when** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Reduce a HF repo input to ``org/name``.      Accepts the canonical ``org/name``** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Project an HF models-list row onto the dashboard's flat shape.      HF occasiona** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Hit HF's public models list and project it onto the row shape.      Caller is re** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **True when an HF repo tree has the FastFlowLM (NPU) model shape.      FLM models** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Pull a short README excerpt from the HF model API payload.      HF returns the m** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Format bytes as a short human label used in the variant dropdown.** (1 connections) — `src/hal0/upstreams/huggingface.py`
- **Fetch HF model metadata + tree listing for ``repo``.      Returns ``{"variants",** (1 connections) — `src/hal0/upstreams/huggingface.py`

## Relationships

- [errors.py](errors.py.md) (2 shared connections)
- [models_service.py](models_service.py.md) (2 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)

## Source Files

- `src/hal0/upstreams/huggingface.py`

## Audit Trail

- EXTRACTED: 62 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*