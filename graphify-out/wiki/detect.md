# detect

> 59 nodes

## Key Concepts

- **detect()** (31 connections) — `src/hal0/registry/detect.py`
- **_write_fixture()** (21 connections) — `tests/registry/test_gguf_header.py`
- **_build_gguf()** (18 connections) — `tests/registry/test_gguf_header.py`
- **Path** (17 connections)
- **_enc_str()** (17 connections) — `tests/registry/test_gguf_header.py`
- **test_detect.py** (12 connections) — `tests/registry/test_detect.py`
- **Path** (12 connections)
- **test_gguf_header.py** (10 connections) — `tests/registry/test_gguf_header.py`
- **TestFilenameHeuristic** (7 connections) — `tests/registry/test_detect.py`
- **.test_truncated_mid_kv_returns_partial()** (7 connections) — `tests/registry/test_gguf_header.py`
- **.test_chat_model_high_confidence()** (6 connections) — `tests/registry/test_detect.py`
- **.test_chat_model_pooling_zero_is_chat()** (6 connections) — `tests/registry/test_detect.py`
- **.test_pooling_type_nonzero_means_embed()** (6 connections) — `tests/registry/test_detect.py`
- **.test_filename_fallback_for_embed_without_pooling()** (6 connections) — `tests/registry/test_detect.py`
- **TestMr3ConsolidationPreservesDetectContract** (6 connections) — `tests/registry/test_detect.py`
- **.test_chat_gguf_still_chat()** (6 connections) — `tests/registry/test_detect.py`
- **.test_embed_gguf_still_embed()** (6 connections) — `tests/registry/test_detect.py`
- **.test_detect_prefers_family_over_unmapped_file_type()** (6 connections) — `tests/registry/test_detect.py`
- **.test_detect_moe_rocmfpx_umbrella()** (6 connections) — `tests/registry/test_detect.py`
- **.test_llama_arch_with_context_length()** (6 connections) — `tests/registry/test_gguf_header.py`
- **.test_qwen_arch_promotes_alias()** (6 connections) — `tests/registry/test_gguf_header.py`
- **.test_pooling_type_promoted()** (6 connections) — `tests/registry/test_gguf_header.py`
- **.test_skips_unwanted_string_kv()** (6 connections) — `tests/registry/test_gguf_header.py`
- **.test_skips_scalar_kv()** (6 connections) — `tests/registry/test_gguf_header.py`
- **TestRocmfpxQuant** (5 connections) — `tests/registry/test_detect.py`
- *... and 34 more nodes in this community*

## Relationships

- [read_gguf_header](read_gguf_header.md) (12 shared connections)
- [detect.py](detect.py.md) (9 shared connections)
- [models_service.py](models_service.py.md) (3 shared connections)

## Source Files

- `src/hal0/registry/detect.py`
- `tests/registry/test_detect.py`
- `tests/registry/test_gguf_header.py`

## Audit Trail

- EXTRACTED: 281 (85%)
- INFERRED: 49 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*