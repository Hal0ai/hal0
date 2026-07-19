# detect

> 39 nodes · cohesion 0.09

## Key Concepts

- **detect()** (31 connections) — `src/hal0/registry/detect.py`
- **Path** (17 connections)
- **detect.py** (9 connections) — `src/hal0/registry/detect.py`
- **_heuristic_only()** (9 connections) — `src/hal0/registry/detect.py`
- **quant_from_filename()** (7 connections) — `src/hal0/registry/detect.py`
- **TestFilenameHeuristic** (7 connections) — `tests/registry/test_detect.py`
- **TestMr3ConsolidationPreservesDetectContract** (6 connections) — `tests/registry/test_detect.py`
- **.test_chat_gguf_still_chat()** (6 connections) — `tests/registry/test_detect.py`
- **.test_embed_gguf_still_embed()** (6 connections) — `tests/registry/test_detect.py`
- **_filename_capability()** (5 connections) — `src/hal0/registry/detect.py`
- **_hf_repo_name_from_path()** (5 connections) — `src/hal0/registry/detect.py`
- **DetectionResult** (4 connections) — `src/hal0/registry/detect.py`
- **quant_from_file_type()** (4 connections) — `src/hal0/registry/detect.py`
- **quant_from_rocmfpx_filename()** (4 connections) — `src/hal0/registry/detect.py`
- **.test_reranker_is_not_emitted_by_detect()** (4 connections) — `tests/registry/test_detect.py`
- **Path** (3 connections)
- **.test_bge_filename_embed()** (3 connections) — `tests/registry/test_detect.py`
- **.test_e5_filename_embed()** (3 connections) — `tests/registry/test_detect.py`
- **.test_kokoro_filename()** (3 connections) — `tests/registry/test_detect.py`
- **.test_moonshine_filename()** (3 connections) — `tests/registry/test_detect.py`
- **.test_unknown_extension_returns_empty_caps()** (3 connections) — `tests/registry/test_detect.py`
- **.test_whisper_filename()** (3 connections) — `tests/registry/test_detect.py`
- **.test_bad_gguf_degrades_to_filename_heuristic()** (3 connections) — `tests/registry/test_detect.py`
- **.test_missing_path_returns_filename_heuristic()** (3 connections) — `tests/registry/test_detect.py`
- **TestGgufUnreadable** (2 connections) — `tests/registry/test_detect.py`
- *... and 14 more nodes in this community*

## Relationships

- [_write_fixture](_write_fixture.md) (22 shared connections)
- [models_service.py](models_service.py.md) (4 shared connections)
- [plan_fileset](plan_fileset.md) (2 shared connections)
- [read_gguf_header](read_gguf_header.md) (1 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/detect.py`
- `tests/registry/test_detect.py`

## Audit Trail

- EXTRACTED: 132 (79%)
- INFERRED: 36 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*