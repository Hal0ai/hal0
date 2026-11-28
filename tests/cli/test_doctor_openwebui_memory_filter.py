"""`hal0 doctor` reports the Open WebUI memory filter's state, read-only (#4066)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from hal0.cli import doctor_all as da
from hal0.openwebui import function_sync as fs


def _row(
    tmp_path: Path, *, token: str | None = "tok", unit: bool = True, state: dict | None = None
):
    unit_path = tmp_path / "openwebui.container"
    if unit:
        unit_path.write_text("[Container]\n")
    env = tmp_path / "api.env"
    env.write_text(f"HAL0_OPENWEBUI_ADMIN_TOKEN={token}\n" if token is not None else "OTHER=1\n")
    seen: list[dict[str, Any]] = []

    def inspect(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return state or {"status": "unavailable"}

    return da.check_openwebui_memory_filter(unit_path=unit_path, api_env=env, inspect=inspect), seen


def test_passes_when_open_webui_is_not_installed(tmp_path: Path) -> None:
    row, seen = _row(tmp_path, unit=False)
    assert (row.key, row.status) == ("openwebui_memory_filter", "pass")
    assert "not installed" in row.detail
    assert seen == []


@pytest.mark.parametrize("token", [None, "", "  "])
def test_no_token_is_information_and_asks_open_webui_nothing(tmp_path: Path, token) -> None:
    row, seen = _row(tmp_path, token=token)
    assert row.status == "pass"
    assert "HAL0_OPENWEBUI_ADMIN_TOKEN" in row.detail
    assert "imported by hand" in row.detail
    assert seen == []


def test_installed_current_active_and_global_passes(tmp_path: Path) -> None:
    row, seen = _row(tmp_path, state={"status": "converged", "active": True, "global": True})
    assert row.status == "pass"
    assert seen and seen[0]["token"] == "tok"


@pytest.mark.parametrize(
    ("state", "needle"),
    [
        ({"status": "converged", "active": False, "global": True}, "not active"),
        ({"status": "converged", "active": True, "global": False}, "not global"),
        ({"status": "outdated", "active": True, "global": True}, "older than this release"),
        ({"status": "absent"}, "restart hal0-api"),
        ({"status": "absent"}, "hal0 update component openwebui"),
        ({"status": "unavailable", "http_status": 500}, "answered HTTP 500"),
        ({"status": "unavailable", "http_status": 500}, "journalctl -u hal0-openwebui"),
        ({"status": "unavailable", "reason": "unexpected_body"}, "does not understand"),
        (
            {"status": "unavailable", "http_status": 200, "reason": "unexpected_body"},
            "does not understand",
        ),
        ({"status": "unavailable", "reason": "BadStatusLine"}, "did not answer"),
        ({"status": "auth_rejected"}, "rejected"),
        ({"status": "conflict"}, "does not manage"),
        ({"status": "unavailable"}, "did not answer"),
    ],
)
def test_every_other_state_warns_with_a_next_step(tmp_path: Path, state, needle: str) -> None:
    row, _ = _row(tmp_path, state=state)
    assert row.status == "warn"
    assert needle in row.detail


def test_the_token_never_appears_in_the_row(tmp_path: Path) -> None:
    for state in (
        {"status": "auth_rejected"},
        {"status": "converged", "active": True, "global": True},
    ):
        row, _ = _row(tmp_path, token="s3cret-admin-token", state=state)
        assert "s3cret-admin-token" not in row.detail


# ── the read-only inspection itself ───────────────────────────────────────────


def _requester(listed: Any, existing: Any = None, list_status: int = 200):
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path))
        if path.endswith("/functions/list"):
            return list_status, listed
        return (200, existing) if existing is not None else (404, None)

    return request, calls


def _managed(content: str, **extra: Any) -> dict[str, Any]:
    return {"id": fs.FUNCTION_ID, "content": content, "meta": {"hal0_managed": True}, **extra}


def test_inspect_only_ever_sends_gets() -> None:
    request, calls = _requester(
        [{"id": fs.FUNCTION_ID}], _managed("old source", is_active=True, is_global=True)
    )
    out = fs.inspect_hindsight_rag_function(requester=request)
    assert out["status"] == "outdated"
    assert calls and all(method == "GET" for method, _ in calls)


def test_inspect_reports_absent_without_creating() -> None:
    request, calls = _requester([{"id": "someone_elses"}])
    assert fs.inspect_hindsight_rag_function(requester=request)["status"] == "absent"
    assert calls == [("GET", "/api/v1/functions/list")]


def test_inspect_reports_converged_with_flags() -> None:
    request, _ = _requester(
        [{"id": fs.FUNCTION_ID}], _managed(fs.bundled_source(), is_active=True, is_global=False)
    )
    out = fs.inspect_hindsight_rag_function(requester=request)
    assert (out["status"], out["active"], out["global"]) == ("converged", True, False)
    assert hashlib.sha256(fs.bundled_source().encode()).hexdigest() == fs.bundled_source_hash()


def test_inspect_reports_conflict_for_an_unmanaged_function() -> None:
    request, _ = _requester([{"id": fs.FUNCTION_ID}], {"id": fs.FUNCTION_ID, "content": "x"})
    assert fs.inspect_hindsight_rag_function(requester=request)["status"] == "conflict"


@pytest.mark.parametrize(
    ("code", "want"), [(401, "auth_rejected"), (403, "auth_rejected"), (500, "unavailable")]
)
def test_inspect_maps_http_failures(code: int, want: str) -> None:
    request, _ = _requester(None, list_status=code)
    assert fs.inspect_hindsight_rag_function(requester=request)["status"] == want


def test_inspect_without_a_token_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_OPENWEBUI_ADMIN_TOKEN", raising=False)
    assert fs.inspect_hindsight_rag_function()["status"] == "auth_required"


def test_the_base_url_comes_from_api_env_or_the_default_never_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin token is sent to the address hal0-api would use. An exported
    variable in the shell that runs the doctor must not redirect it."""
    monkeypatch.setenv("HAL0_OPENWEBUI_INTERNAL_URL", "http://elsewhere.invalid:1")
    _, seen = _row(tmp_path, state={"status": "absent"})
    assert seen[0]["base_url"] == fs.DEFAULT_BASE_URL

    env = tmp_path / "api.env"
    unit = tmp_path / "openwebui.container"
    env.write_text(
        "HAL0_OPENWEBUI_ADMIN_TOKEN=tok\nHAL0_OPENWEBUI_INTERNAL_URL=http://127.0.0.1:3999\n"
    )
    calls: list[dict[str, Any]] = []
    da.check_openwebui_memory_filter(
        unit_path=unit, api_env=env, inspect=lambda **kw: calls.append(kw) or {"status": "absent"}
    )
    assert calls[0]["base_url"] == "http://127.0.0.1:3999"


@pytest.mark.parametrize(
    "exc",
    [
        __import__("http.client").client.BadStatusLine("junk"),
        __import__("http.client").client.InvalidURL("bad"),
        __import__("http.client").client.IncompleteRead(b""),
        ConnectionRefusedError("refused"),
        TimeoutError("slow"),
    ],
)
def test_inspect_reports_unavailable_for_every_transport_failure(exc: Exception) -> None:
    """A listener that is not HTTP, a bad URL or a cut-off body must not
    raise through the doctor: one row cannot take the whole report down."""

    def request(method: str, path: str, payload: dict | None = None):
        raise exc

    out = fs.inspect_hindsight_rag_function(requester=request)
    assert out["status"] == "unavailable"
    assert out["reason"] == type(exc).__name__


def test_inspect_marks_an_unexpected_body() -> None:
    request, _ = _requester({"not": "a list"})
    out = fs.inspect_hindsight_rag_function(requester=request)
    assert (out["status"], out["http_status"], out["reason"]) == (
        "unavailable",
        200,
        "unexpected_body",
    )


def test_inspect_reads_a_body_that_is_not_json_as_an_answer() -> None:
    """Open WebUI answered, with something that is not its Functions API."""
    import json as _json

    def request(method: str, path: str, payload: dict | None = None):
        raise _json.JSONDecodeError("Expecting value", "<html>", 0)

    out = fs.inspect_hindsight_rag_function(requester=request)
    assert (out["status"], out["reason"]) == ("unavailable", "unexpected_body")


def test_the_log_hint_names_the_unit_hal0_installs() -> None:
    """The row's log hint must be a command that shows something: the unit is
    the one the service registry names for Open WebUI."""
    from hal0.services.registry import service_by_id

    unit = service_by_id("openwebui").unit
    assert unit == "hal0-openwebui.service"


def test_an_unexpected_body_without_a_status_reads_cleanly(tmp_path: Path) -> None:
    row, _ = _row(tmp_path, state={"status": "unavailable", "reason": "unexpected_body"})
    assert "None" not in row.detail
    assert "Open WebUI answered with a body hal0 does not understand" in row.detail
