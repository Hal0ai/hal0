# _Headers

> 18 nodes · cohesion 0.11

## Key Concepts

- **_Headers** (7 connections) — `tests/api/test_chat_normalization.py`
- **_OmniRequest** (6 connections) — `tests/api/test_chat_normalization.py`
- **_Upstreams** (6 connections) — `tests/api/test_chat_normalization.py`
- **_FakeDispatcher** (5 connections) — `tests/api/test_chat_normalization.py`
- **_NonChatRequest** (5 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (4 connections) — `tests/api/test_chat_normalization.py`
- **test_dispatch_and_forward_does_not_normalize_non_chat()** (4 connections) — `tests/api/test_chat_normalization.py`
- **test_omni_path_receives_normalized_body()** (3 connections) — `tests/api/test_chat_normalization.py`
- **.forward()** (2 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (2 connections) — `tests/api/test_chat_normalization.py`
- **.get()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.body()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.body()** (1 connections) — `tests/api/test_chat_normalization.py`
- **Request that flows through chat_completions -> _read_json_body -> omni branch.** (1 connections) — `tests/api/test_chat_normalization.py`
- **The omni branch returns before _dispatch_and_forward, so chat_completions     mu** (1 connections) — `tests/api/test_chat_normalization.py`
- **_dispatch_and_forward must NOT invoke _normalize_chat_body — that would     rewr** (1 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.list()** (1 connections) — `tests/api/test_chat_normalization.py`

## Relationships

- [test_chat_normalization.py](test_chat_normalization.py.md) (8 shared connections)
- [SlotView](SlotView.md) (5 shared connections)
- [test_board_dispatch.py](test_board_dispatch.py.md) (1 shared connections)
- [test_hindsight_provider.py](test_hindsight_provider.py.md) (1 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [plan_fileset](plan_fileset.md) (1 shared connections)
- [_SlotManager](_SlotManager.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_normalization.py`

## Audit Trail

- EXTRACTED: 46 (88%)
- INFERRED: 6 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*