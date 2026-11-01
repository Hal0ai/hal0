"""Hermes dashboard-token and kanban-reachability doctor rows."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hal0.agents import hermes_doctor
from hal0.board import HermesKanbanClient


def _client(handler) -> HermesKanbanClient:  # type: ignore[no-untyped-def]
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9119"
    )
    return HermesKanbanClient(http_client=http, token_resolver=lambda: "tok")


def test_token_present(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("SECRET-VALUE\n")
    row = hermes_doctor.check_hermes_dashboard_token(path=f)
    assert row == {"status": "ok", "detail": "present"}
    assert "SECRET" not in str(row)


@pytest.mark.parametrize("content", [None, "  \n"])
def test_token_missing_or_blank(tmp_path: Path, content: str | None) -> None:
    f = tmp_path / "tok"
    if content is not None:
        f.write_text(content)
    row = hermes_doctor.check_hermes_dashboard_token(path=f)
    assert row["status"] == "warn"
    assert row["detail"] == f"missing at {f}; restart hal0-agent@hermes to mint it"


def test_kanban_reachable_counts_boards() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/boards")
        return httpx.Response(200, json={"boards": [{"slug": "a"}, {"slug": "b"}]})

    row = hermes_doctor.check_hermes_kanban_reachable(client=_client(handler))
    assert row == {"status": "ok", "detail": "2 board(s)"}


def test_kanban_connect_error_warns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    row = hermes_doctor.check_hermes_kanban_reachable(client=_client(handler))
    assert row["status"] == "warn"
    assert row["detail"].startswith("board.unreachable: ")


def test_kanban_no_token_warns() -> None:
    from hal0.board import BoardNoToken

    def resolver() -> str:
        raise BoardNoToken("Hermes dashboard token file unreadable", details={"path": "/x"})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url="http://127.0.0.1:9119",
    )
    client = HermesKanbanClient(http_client=http, token_resolver=resolver)
    row = hermes_doctor.check_hermes_kanban_reachable(client=client)
    assert row["status"] == "warn"
    assert row["detail"].startswith("board.no_token: ")


def test_kanban_hermes_not_installed_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.board import auth

    monkeypatch.setattr(auth, "hermes_installed", lambda: False)
    row = hermes_doctor.check_hermes_kanban_reachable()
    assert row == {
        "status": "ok",
        "detail": "Hermes agent not installed; board unavailable by design",
    }
