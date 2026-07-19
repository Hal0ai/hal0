# test_logs_tail_redaction.py

> 16 nodes

## Key Concepts

- **test_logs_tail_redaction.py** (9 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **_logs_transport()** (3 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_logs_tail_dispatch_redacts_before_returning()** (3 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_redact_log_line_passes_through_safe_content()** (2 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_redact_logs_payload_masks_slot_logs_string_shape()** (2 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_redact_logs_payload_tolerates_missing_or_malformed_shape()** (2 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **queue()** (2 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_redact_log_line_masks_known_secret_shapes()** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **test_redact_logs_payload_walks_lines_array()** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **MonkeyPatch** (1 connections)
- **Unit tests for the logs_tail Bearer redactor in :mod:`hal0.mcp.admin`.  Security** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **No false positives on lines that don't carry secrets.** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **slot_logs returns one ``logs`` string (not a ``lines`` array) —     the redactor** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **If the upstream gives us an unexpected shape we return it     unchanged — never** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **Patch httpx.AsyncClient so GET /api/logs returns a leak-bearing     payload — th** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`
- **End-to-end via :func:`admin.dispatch` — the approval-gated     ``logs_tail`` too** (1 connections) — `tests/mcp/test_logs_tail_redaction.py`

## Relationships

- [ApprovalQueue](ApprovalQueue.md) (2 shared connections)

## Source Files

- `tests/mcp/test_logs_tail_redaction.py`

## Audit Trail

- EXTRACTED: 32 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*