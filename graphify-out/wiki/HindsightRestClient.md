# HindsightRestClient

> 23 nodes · cohesion 0.12

## Key Concepts

- **HindsightRestClient** (26 connections) — `src/hal0/memory/hindsight_client.py`
- **._headers()** (6 connections) — `src/hal0/memory/hindsight_client.py`
- **test_hindsight_client.py** (5 connections) — `tests/memory/test_hindsight_client.py`
- **.request_json()** (4 connections) — `src/hal0/memory/hindsight_client.py`
- **_HindsightStubProvider** (4 connections) — `tests/api/test_memory_admin_document_transfer.py`
- **test_delete_document_percent_encodes_id_in_path()** (3 connections) — `tests/memory/test_hindsight_client.py`
- **hindsight_client.py** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.delete_document()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.__init__()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.list_memories()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.recall()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.retain()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **.__init__()** (2 connections) — `tests/api/test_memory_admin_document_transfer.py`
- **test_list_memories_hits_list_endpoint_and_returns_json()** (2 connections) — `tests/memory/test_hindsight_client.py`
- **test_request_json_generic_forward_carries_auth_params_and_body()** (2 connections) — `tests/memory/test_hindsight_client.py`
- **test_retain_recall_delete_hit_v1_bank_paths()** (2 connections) — `tests/memory/test_hindsight_client.py`
- **.aclose()** (1 connections) — `src/hal0/memory/hindsight_client.py`
- **Any** (1 connections)
- **AsyncClient** (1 connections)
- **Async REST client for the shared hindsight-api (brain-redesign P1).  Talks to ``** (1 connections) — `src/hal0/memory/hindsight_client.py`
- **Generic authenticated forward to any Hindsight REST path.          The admin sur** (1 connections) — `src/hal0/memory/hindsight_client.py`
- **HindsightRestClient REST-path tests against a MockTransport (P1).** (1 connections) — `tests/memory/test_hindsight_client.py`
- **A deterministic ``<agent>:<session_id>`` document id (colon) must be     percent** (1 connections) — `tests/memory/test_hindsight_client.py`

## Relationships

- [test_memory_subgraph.py](test_memory_subgraph.py.md) (5 shared connections)
- [test_memory_admin_document_transfer.py](test_memory_admin_document_transfer.py.md) (4 shared connections)
- [test_memory_admin_routes.py](test_memory_admin_routes.py.md) (2 shared connections)
- [PgVectorProvider](PgVectorProvider.md) (1 shared connections)
- [_OtherEngineProvider](_OtherEngineProvider.md) (1 shared connections)

## Source Files

- `src/hal0/memory/hindsight_client.py`
- `tests/api/test_memory_admin_document_transfer.py`
- `tests/memory/test_hindsight_client.py`

## Audit Trail

- EXTRACTED: 61 (81%)
- INFERRED: 14 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*