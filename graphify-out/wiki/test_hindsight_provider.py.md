# test_hindsight_provider.py

> 39 nodes

## Key Concepts

- **test_hindsight_provider.py** (19 connections) — `tests/memory/test_hindsight_provider.py`
- **FakeHindsightClient** (18 connections) — `tests/memory/test_hindsight_provider.py`
- **Hal0Reranker** (13 connections) — `src/hal0/memory/hindsight_provider.py`
- **Fake404HindsightClient** (10 connections) — `tests/memory/test_hindsight_provider.py`
- **FakeReranker** (7 connections) — `tests/memory/test_hindsight_provider.py`
- **.retain()** (6 connections) — `tests/memory/test_hindsight_provider.py`
- **test_recall_fans_out_across_allowed_banks_and_merges()** (6 connections) — `tests/memory/test_hindsight_provider.py`
- **test_recall_default_types_include_observations()** (6 connections) — `tests/memory/test_hindsight_provider.py`
- **test_recall_merge_precedence_tier_overrides_bank_order_and_score()** (5 connections) — `tests/memory/test_hindsight_provider.py`
- **test_list_items_fans_out_real_endpoint()** (5 connections) — `tests/memory/test_hindsight_provider.py`
- **_Resp** (5 connections) — `tests/memory/test_hindsight_provider.py`
- **test_delete_sweep_survives_404_and_reaches_private_bank()** (5 connections) — `tests/memory/test_hindsight_provider.py`
- **test_delete_dataset_directs_sweep_to_project_bank()** (5 connections) — `tests/memory/test_hindsight_provider.py`
- **.recall()** (4 connections) — `tests/memory/test_hindsight_provider.py`
- **test_delete_non_404_engine_errors_still_raise()** (4 connections) — `tests/memory/test_hindsight_provider.py`
- **test_add_caller_document_id_upserts_same_document()** (4 connections) — `tests/memory/test_hindsight_provider.py`
- **test_add_routes_to_retain_under_mapped_bank()** (3 connections) — `tests/memory/test_hindsight_provider.py`
- **.rerank()** (3 connections) — `tests/memory/test_hindsight_provider.py`
- **test_hal0_reranker_posts_rerank_and_parses_results()** (3 connections) — `tests/memory/test_hindsight_provider.py`
- **test_hal0_reranker_failsoft_returns_empty_on_error()** (3 connections) — `tests/memory/test_hindsight_provider.py`
- **test_delete_missing_everywhere_counts_zero()** (3 connections) — `tests/memory/test_hindsight_provider.py`
- **.forward()** (2 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (2 connections) — `tests/memory/test_hindsight_provider.py`
- **.__init__()** (1 connections) — `src/hal0/memory/hindsight_provider.py`
- **Async reranker over hal0-api's OpenAI surface (Cohere-style ``/v1/rerankings``).** (1 connections) — `src/hal0/memory/hindsight_provider.py`
- *... and 14 more nodes in this community*

## Relationships

- [HindsightProvider](HindsightProvider.md) (17 shared connections)
- [StacksCatalog](StacksCatalog.md) (3 shared connections)
- [FakeMemoryProvider](FakeMemoryProvider.md) (2 shared connections)
- [Hal0MemoryProvider](Hal0MemoryProvider.md) (1 shared connections)
- [PgVectorProvider](PgVectorProvider.md) (1 shared connections)
- [test_chat_normalization.py](test_chat_normalization.py.md) (1 shared connections)

## Source Files

- `src/hal0/memory/hindsight_provider.py`
- `tests/api/test_chat_normalization.py`
- `tests/memory/test_hindsight_provider.py`

## Audit Trail

- EXTRACTED: 126 (80%)
- INFERRED: 31 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*