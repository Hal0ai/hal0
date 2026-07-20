# .emit

> 18 nodes

## Key Concepts

- **.emit()** (8 connections) — `src/hal0/events/__init__.py`
- **make_event()** (7 connections) — `src/hal0/events/__init__.py`
- **Any** (6 connections)
- **._enqueue()** (6 connections) — `src/hal0/events/__init__.py`
- **Severity** (6 connections) — `ui/src/api/hooks/useDiagnoses.ts`
- **__init__.py** (4 connections) — `src/hal0/events/__init__.py`
- **.subscribe()** (4 connections) — `src/hal0/events/__init__.py`
- **.backfill()** (4 connections) — `src/hal0/events/__init__.py`
- **_now_iso()** (3 connections) — `src/hal0/events/__init__.py`
- **.__init__()** (2 connections) — `src/hal0/events/__init__.py`
- **Queue** (2 connections)
- **In-process event bus + ring buffer for the dashboard footer.  The footer status** (1 connections) — `src/hal0/events/__init__.py`
- **Return an ISO-8601 UTC timestamp with microsecond precision.** (1 connections) — `src/hal0/events/__init__.py`
- **Build the canonical event dict. Exposed for tests + manual injection.** (1 connections) — `src/hal0/events/__init__.py`
- **Append an event to the ring and fan it out to every subscriber.          Never b** (1 connections) — `src/hal0/events/__init__.py`
- **Push to a subscriber queue, dropping oldest on overflow.          A slow consume** (1 connections) — `src/hal0/events/__init__.py`
- **Yield an asyncio.Queue receiving every event emitted after entry.          Use a** (1 connections) — `src/hal0/events/__init__.py`
- **Return matching ring entries with id > since, ordered ascending.          Filter** (1 connections) — `src/hal0/events/__init__.py`

## Relationships

- [EventBus](EventBus.md) (6 shared connections)
- [AuditStore](AuditStore.md) (2 shared connections)
- [realtime_ws](realtime_ws.md) (1 shared connections)
- [orchestrate_models](orchestrate_models.md) (1 shared connections)
- [useDiagnoses.ts](useDiagnoses.ts.md) (1 shared connections)

## Source Files

- `src/hal0/events/__init__.py`
- `ui/src/api/hooks/useDiagnoses.ts`

## Audit Trail

- EXTRACTED: 57 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*