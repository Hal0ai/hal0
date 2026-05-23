"""Tests for the Lemonade log-proxy routes (PR-11, plan §11 + ADR-0008 §3).

Two surfaces:
  - ``/api/lemonade/logs/stream``: pass-through SSE of every parsed
    lemond log entry (consumed by PR-14's journal panel).
  - ``/api/lemonade/events/stream``: structured events derived from
    filtered log lines; today only ``nuclear_evict`` is emitted.

The unit tests below exercise the message-extraction + frame-flatten
helpers directly (where the bulk of the logic lives). The end-to-end
SSE behaviour is exercised by the gamma-suite Playwright spec.
"""

from __future__ import annotations

from hal0.api.routes.lemonade_logs import (
    NUCLEAR_EVICT_TRIGGER,
    _extract_message,
    _iter_entries,
)

# ── trigger constant ────────────────────────────────────────────────────────


def test_nuclear_evict_trigger_matches_documented_lemond_line() -> None:
    """The trigger substring must match lemond's actual error log.

    Source: ``hal0_lemonade_gotchas`` memory + ADR-0008 §3. If lemond
    upstream ever changes the wording, this constant updates and the
    dashboard banner keeps firing — but the test keeps the contract
    legible.
    """
    assert "Load failed" in NUCLEAR_EVICT_TRIGGER
    assert "evicting all models" in NUCLEAR_EVICT_TRIGGER
    assert "non-file-not-found error" in NUCLEAR_EVICT_TRIGGER


# ── _extract_message ────────────────────────────────────────────────────────


def test_extract_message_picks_canonical_message_key() -> None:
    assert _extract_message({"message": "hello"}) == "hello"


def test_extract_message_tolerates_msg_text_line_fallbacks() -> None:
    # lemond builds have varied — keep all three legacy keys working.
    assert _extract_message({"text": "from-text"}) == "from-text"
    assert _extract_message({"msg": "from-msg"}) == "from-msg"
    assert _extract_message({"line": "from-line"}) == "from-line"


def test_extract_message_returns_empty_string_when_absent() -> None:
    assert _extract_message({}) == ""
    assert _extract_message({"level": "info"}) == ""


def test_extract_message_skips_non_string_values() -> None:
    # A protocol bump that ships ``message: {...}`` must NOT crash the
    # filter — it should fall through to the next candidate key, then
    # to the empty-string sentinel.
    assert _extract_message({"message": None}) == ""
    assert _extract_message({"message": 42}) == ""
    assert _extract_message({"message": [], "text": "fallback"}) == "fallback"


# ── _iter_entries ───────────────────────────────────────────────────────────


def test_iter_entries_handles_logs_entry_frame() -> None:
    frame = {"op": "logs.entry", "entry": {"message": "hi", "level": "info"}}
    assert _iter_entries(frame) == [{"message": "hi", "level": "info"}]


def test_iter_entries_flattens_logs_snapshot_batch() -> None:
    frame = {
        "op": "logs.snapshot",
        "entries": [
            {"message": "first"},
            {"message": "second"},
            "not-a-dict",  # mixed in by buggy upstream — filter out
        ],
    }
    out = _iter_entries(frame)
    assert out == [{"message": "first"}, {"message": "second"}]


def test_iter_entries_handles_unknown_op_as_raw_passthrough() -> None:
    """Unknown frame ops (future protocol) pass through unfiltered.

    The dashboard's journal panel can render whatever shape it sees;
    the nuclear-evict filter routes through ``_extract_message`` which
    safely returns '' for shape mismatches.
    """
    frame = {"op": "logs.future_op", "some_field": 1}
    assert _iter_entries(frame) == [frame]


def test_iter_entries_drops_missing_payloads() -> None:
    # entry: None
    assert _iter_entries({"op": "logs.entry", "entry": None}) == []
    # entries: None
    assert _iter_entries({"op": "logs.snapshot", "entries": None}) == []
    # entry: wrong type
    assert _iter_entries({"op": "logs.entry", "entry": "string-not-dict"}) == []


# ── route integration ──────────────────────────────────────────────────────


def test_routes_mounted_under_api_lemonade_prefix() -> None:
    """The router is wired under /api/lemonade so the dashboard URLs match.

    Smoke test rather than functional — full SSE timing is covered by
    the gamma-suite Playwright spec which can drive the stream against
    mocked EventSource.
    """
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as client:
        # No auth → 401, but the path resolves (404 would mean unmounted).
        r = client.get("/api/lemonade/logs/stream")
        # Either 401 (auth required) or 200 (auth disabled in tests) —
        # NEVER 404, which would mean the route isn't registered.
        assert r.status_code != 404, "lemonade log routes not mounted"
