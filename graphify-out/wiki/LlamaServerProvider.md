# LlamaServerProvider

> 56 nodes · cohesion 0.06

## Key Concepts

- **LlamaServerProvider** (14 connections) — `src/hal0/providers/llama_server.py`
- **get_provider()** (11 connections) — `src/hal0/providers/__init__.py`
- **test_v1_images.py** (11 connections) — `tests/api/test_v1_images.py`
- **test_llama_server.py** (11 connections) — `tests/providers/test_llama_server.py`
- **TestClient** (10 connections)
- **_seed_img_upstream()** (9 connections) — `tests/api/test_v1_images.py`
- **llama_server.py** (5 connections) — `src/hal0/providers/llama_server.py`
- **ProviderInferError** (5 connections) — `src/hal0/providers/llama_server.py`
- **MonkeyPatch** (5 connections)
- **test_v1_images_b64_json_returns_inline_base64()** (5 connections) — `tests/api/test_v1_images.py`
- **test_v1_images_cold_slot_ensure_img_before_dispatch()** (5 connections) — `tests/api/test_v1_images.py`
- **test_v1_images_provider_error_surfaces()** (5 connections) — `tests/api/test_v1_images.py`
- **test_v1_images_url_response_format_writes_cache()** (5 connections) — `tests/api/test_v1_images.py`
- **test_llama_server_is_not_a_launcher()** (5 connections) — `tests/providers/test_llama_server.py`
- **.infer()** (4 connections) — `src/hal0/providers/llama_server.py`
- **test_v1_images_unknown_model_404()** (4 connections) — `tests/api/test_v1_images.py`
- **.health()** (3 connections) — `src/hal0/providers/llama_server.py`
- **.parse_metrics()** (3 connections) — `src/hal0/providers/llama_server.py`
- **ProviderHealthError** (3 connections) — `src/hal0/providers/llama_server.py`
- **Any** (3 connections)
- **test_images_cache_blocks_path_traversal()** (3 connections) — `tests/api/test_v1_images.py`
- **test_v1_images_empty_prompt_422()** (3 connections) — `tests/api/test_v1_images.py`
- **test_v1_images_no_upstream_returns_envelope()** (3 connections) — `tests/api/test_v1_images.py`
- **_mock_async_response()** (3 connections) — `tests/providers/test_llama_server.py`
- **test_health_empty_models_endpoint_is_not_ready()** (3 connections) — `tests/providers/test_llama_server.py`
- *... and 31 more nodes in this community*

## Relationships

- [Provider](Provider.md) (2 shared connections)
- [v1.py](v1.py.md) (1 shared connections)
- [KeyError](KeyError.md) (1 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `src/hal0/providers/__init__.py`
- `src/hal0/providers/llama_server.py`
- `tests/api/test_v1_images.py`
- `tests/providers/test_llama_server.py`

## Audit Trail

- EXTRACTED: 168 (91%)
- INFERRED: 17 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*