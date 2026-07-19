# _mock_transport

> 13 nodes · cohesion 0.15

## Key Concepts

- **_mock_transport()** (9 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_preserves_extra_params()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_propagates_upstream_status_codes()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_stt_extra_headers_dont_clobber_content_type()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_stt_posts_multipart_to_static_port()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_posts_json_to_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **Any** (1 connections)
- **AsyncClient** (1 connections)
- **Verify the URL + content-type + body bytes the container sees.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **Caller-supplied params (encoding_format, dimensions, ...) round-trip     untouch** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **A container-side 4xx (e.g. validation) reaches the caller verbatim.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **If the caller passes an ``extra_headers`` dict that itself contains     a conten** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **httpx ``MockTransport`` convenience wrapper.** (1 connections) — `tests/dispatcher/test_npu_trio.py`

## Relationships

- [test_npu_trio.py](test_npu_trio.py.md) (11 shared connections)
- [NpuTrioRouter](NpuTrioRouter.md) (5 shared connections)

## Source Files

- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 35 (88%)
- INFERRED: 5 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*