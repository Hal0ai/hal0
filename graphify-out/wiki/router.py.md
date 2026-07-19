# router.py

> 22 nodes · cohesion 0.10

## Key Concepts

- **router.py** (28 connections) — `src/hal0/dispatcher/router.py`
- **_resolve_target_url()** (5 connections) — `src/hal0/dispatcher/router.py`
- **_join_url()** (4 connections) — `src/hal0/dispatcher/router.py`
- **_remap_model()** (4 connections) — `src/hal0/dispatcher/router.py`
- **_slot_name_of()** (4 connections) — `src/hal0/dispatcher/router.py`
- **Any** (3 connections)
- **_default_fetch_models()** (2 connections) — `src/hal0/dispatcher/router.py`
- **_default_is_online()** (2 connections) — `src/hal0/dispatcher/router.py`
- **_default_cached_models()** (1 connections) — `src/hal0/dispatcher/router.py`
- **Dispatcher — registry-aware request router.  The :class:`Dispatcher` reads the m** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: treated as "no binding" rather than fatal so the dispatcher** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: until ModelRegistry exposes (upstream_name, upstream_model)** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: auth header materialisation depends on Agent J's** (1 connections) — `src/hal0/dispatcher/router.py`
- **Set ``body["model"] = new_model`` and re-serialise to bytes.** (1 connections) — `src/hal0/dispatcher/router.py`
- **Return the local slot name for ``upstream`` (empty for remotes).      ``Upstream** (1 connections) — `src/hal0/dispatcher/router.py`
- **Map ``/v1/<path>`` onto ``<upstream_url>/<path>``.      Upstream URLs end in ``/** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: the capability/path routing constants (``_EMBED_PATHS``,** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: ``LegacyResolutionFailed`` (raised by the capability/path Step 4) now** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: until Agent J wires the cache, every cache lookup is empty so** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: pessimistic default — Agent J's health probe will replace this.** (1 connections) — `src/hal0/dispatcher/router.py`
- **# NOTE: structlog binds via contextvars in the request_id middleware, so** (1 connections) — `src/hal0/dispatcher/router.py`
- **Build the forward URL for ``upstream`` given the incoming request path.      Eve** (1 connections) — `src/hal0/dispatcher/router.py`

## Relationships

- [Dispatcher](Dispatcher.md) (10 shared connections)
- [Upstream](Upstream.md) (4 shared connections)
- [UpstreamCall](UpstreamCall.md) (3 shared connections)
- [SlotLoading](SlotLoading.md) (2 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`

## Audit Trail

- EXTRACTED: 66 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*