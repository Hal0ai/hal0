# .record_error

> 11 nodes

## Key Concepts

- **.record_nonstreaming()** (7 connections) — `src/hal0/metrics/seam.py`
- **.record_error()** (7 connections) — `src/hal0/metrics/seam.py`
- **_current_request_id()** (6 connections) — `src/hal0/metrics/seam.py`
- **_client_host()** (6 connections) — `src/hal0/metrics/seam.py`
- **.wrap_streaming()** (6 connections) — `src/hal0/metrics/seam.py`
- **Request** (5 connections)
- **seam.py** (4 connections) — `src/hal0/metrics/seam.py`
- **StreamingResponse** (1 connections)
- **BaseException** (1 connections)
- **RequestSeam -- the ONE T1 measurement point (plan §7.6 / S12).  Wraps ``api/rout** (1 connections) — `src/hal0/metrics/seam.py`
- **Best-effort read of the id ``request_id.install()`` bound this request to.** (1 connections) — `src/hal0/metrics/seam.py`

## Relationships

- [RequestSeam](RequestSeam.md) (4 shared connections)
- [build_request_metric_row](build_request_metric_row.md) (3 shared connections)
- [UpstreamCall](UpstreamCall.md) (3 shared connections)
- [parse_json_object](parse_json_object.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/seam.py`

## Audit Trail

- EXTRACTED: 41 (91%)
- INFERRED: 4 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*