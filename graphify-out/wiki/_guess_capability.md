# _guess_capability

> 9 nodes

## Key Concepts

- **_guess_capability()** (6 connections) — `src/hal0/registry/discover.py`
- **capability_from_filename()** (5 connections) — `src/hal0/model_meta/__init__.py`
- **test_capability_guess_classifies_diffusion_media()** (3 connections) — `tests/registry/test_discover.py`
- **test_capability_guess_classifies_rerankers()** (3 connections) — `tests/registry/test_discover.py`
- **test_capability_from_filename()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **Best-effort capability token inferred from a model filename.      Returns one of** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Best-effort capability inference from the filename.      Delegates to the single** (1 connections) — `src/hal0/registry/discover.py`
- **Clearly-diffusion media files classify as image/video, not the chat     default** (1 connections) — `tests/registry/test_discover.py`
- **MR-3: a reranker filename must classify as 'rerank', not the old 'chat'     defa** (1 connections) — `tests/registry/test_discover.py`

## Relationships

- [test_discover.py](test_discover.py.md) (3 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (2 shared connections)
- [detect.py](detect.py.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)

## Source Files

- `src/hal0/model_meta/__init__.py`
- `src/hal0/registry/discover.py`
- `tests/model_meta/test_model_meta.py`
- `tests/registry/test_discover.py`

## Audit Trail

- EXTRACTED: 14 (61%)
- INFERRED: 9 (39%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*