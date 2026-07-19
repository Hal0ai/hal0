# logs.py

> 16 nodes

## Key Concepts

- **logs.py** (7 connections) — `src/hal0/api/routes/logs.py`
- **stream_logs()** (6 connections) — `src/hal0/api/routes/logs.py`
- **LogsError** (5 connections) — `src/hal0/api/routes/logs.py`
- **_validate_unit()** (5 connections) — `src/hal0/api/routes/logs.py`
- **_resolve_level()** (5 connections) — `src/hal0/api/routes/logs.py`
- **list_logs()** (5 connections) — `src/hal0/api/routes/logs.py`
- **journalctl_sse()** (4 connections) — `src/hal0/api/routes/logs.py`
- **Any** (2 connections)
- **StreamingResponse** (1 connections)
- **Log endpoints (mounted under /api/logs).  Tail and stream journald entries for h** (1 connections) — `src/hal0/api/routes/logs.py`
- **Logs endpoint validation/runtime errors.** (1 connections) — `src/hal0/api/routes/logs.py`
- **Validate a systemd unit name.      Rejects shell-special characters so the unit** (1 connections) — `src/hal0/api/routes/logs.py`
- **Map a level alias to a journalctl --priority value.** (1 connections) — `src/hal0/api/routes/logs.py`
- **Async generator that yields SSE frames tailing ``journalctl -f -u <unit>``.** (1 connections) — `src/hal0/api/routes/logs.py`
- **Return the last ``n`` journal entries for ``unit``.      Best-effort: on hosts w** (1 connections) — `src/hal0/api/routes/logs.py`
- **SSE tail of ``unit``'s journald output, line-by-line.      Closes its subprocess** (1 connections) — `src/hal0/api/routes/logs.py`

## Relationships

- [Hal0Error](Hal0Error.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/logs.py`

## Audit Trail

- EXTRACTED: 47 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*