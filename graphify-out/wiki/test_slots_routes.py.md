# test_slots_routes.py

> 15 nodes

## Key Concepts

- **test_slots_routes.py** (60 connections) — `tests/api/test_slots_routes.py`
- **_StubResponse** (9 connections) — `tests/api/test_slots_routes.py`
- **_patch_httpx()** (8 connections) — `tests/api/test_slots_routes.py`
- **MonkeyPatch** (5 connections)
- **test_scrape_llama_metrics_synthesises_kv_from_slots()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_scrape_llama_metrics_prefers_native_ratio_when_present()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_scrape_llama_metrics_omits_kv_when_slots_idle()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_scrape_llama_metrics_clamps_overrun()** (5 connections) — `tests/api/test_slots_routes.py`
- **Tests for the /api/slots route surface (container runtime).  Covers:   - list-me** (1 connections) — `tests/api/test_slots_routes.py`
- **Minimal httpx.Response stand-in for the scrape tests.      Implements just the s** (1 connections) — `tests/api/test_slots_routes.py`
- **Patch httpx.AsyncClient used inside slots._scrape_llama_metrics.      Routes the** (1 connections) — `tests/api/test_slots_routes.py`
- **Newer llama-server (b9279+) drops kv_cache_usage_ratio from     /metrics but sti** (1 connections) — `tests/api/test_slots_routes.py`
- **If a future llama.cpp reintroduces ``llamacpp:kv_cache_usage_ratio``     we use** (1 connections) — `tests/api/test_slots_routes.py`
- **Idle /slots payload (no n_prompt_tokens on any sub-slot) leaves     ``kv_cache_u** (1 connections) — `tests/api/test_slots_routes.py`
- **n_prompt_tokens can briefly exceed n_ctx during shift; clamp the     synthesised** (1 connections) — `tests/api/test_slots_routes.py`

## Relationships

- [TestClient](TestClient.md) (18 shared connections)
- [.json](json.md) (18 shared connections)
- [FastAPI](FastAPI.md) (11 shared connections)
- [Any](Any.md) (8 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 109 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*