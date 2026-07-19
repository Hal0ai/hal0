# _Headers

> 11 nodes

## Key Concepts

- **_Headers** (7 connections) — `tests/api/test_chat_normalization.py`
- **_Upstreams** (6 connections) — `tests/api/test_chat_normalization.py`
- **_OmniRequest** (6 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (4 connections) — `tests/api/test_chat_normalization.py`
- **test_omni_path_receives_normalized_body()** (3 connections) — `tests/api/test_chat_normalization.py`
- **.__init__()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.list()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.get()** (1 connections) — `tests/api/test_chat_normalization.py`
- **.body()** (1 connections) — `tests/api/test_chat_normalization.py`
- **Request that flows through chat_completions -> _read_json_body -> omni branch.** (1 connections) — `tests/api/test_chat_normalization.py`
- **The omni branch returns before _dispatch_and_forward, so chat_completions     mu** (1 connections) — `tests/api/test_chat_normalization.py`

## Relationships

- [test_chat_normalization.py](test_chat_normalization.py.md) (6 shared connections)
- [SlotView](SlotView.md) (3 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [plan_fileset](plan_fileset.md) (1 shared connections)
- [_SlotManager](_SlotManager.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_normalization.py`

## Audit Trail

- EXTRACTED: 29 (91%)
- INFERRED: 3 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*