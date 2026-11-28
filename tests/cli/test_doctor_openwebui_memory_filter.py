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
        ({"status": "absent"}, "hal0 update component openwebui"),
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
