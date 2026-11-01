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


def test_kanban_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"boards": []})

    monkeypatch.setattr(hermes_doctor, "KANBAN_PROBE_TIMEOUT_S", 0.05)
    row = hermes_doctor.check_hermes_kanban_reachable(client=_client(handler))
    assert row == {"status": "warn", "detail": "board.unreachable: timed out after 0.05s"}


def test_token_not_installed_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.board import auth

    monkeypatch.setattr(auth, "hermes_installed", lambda: False)
    row = hermes_doctor.check_hermes_dashboard_token()
    assert row == {
        "status": "ok",
        "detail": "Hermes agent not installed; no dashboard token expected",
    }


def test_token_permission_denied_is_reraised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "tok"
    f.write_text("x\n")

    def deny(self: Path, *a: object, **k: object) -> str:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(PermissionError):
        hermes_doctor.check_hermes_dashboard_token(path=f)


def test_kanban_unexpected_probe_error_warns() -> None:
    class Boom:
        async def request_json(self, *a: object, **k: object) -> object:
            raise ValueError("bad url")

        async def aclose(self) -> None:
            pass

    row = hermes_doctor.check_hermes_kanban_reachable(client=Boom())
    assert row == {"status": "warn", "detail": "probe failed: ValueError: bad url"}


def test_kanban_probe_closes_client() -> None:
    closed: list[bool] = []

    class C:
        async def request_json(self, *a: object, **k: object) -> object:
            return {"boards": []}

        async def aclose(self) -> None:
            closed.append(True)

    hermes_doctor.check_hermes_kanban_reachable(client=C())
    assert closed == [True]


def test_client_aclose_closes_owned_transport_only() -> None:
    import asyncio

    async def run() -> tuple[bool, bool]:
        owned = HermesKanbanClient(token_resolver=lambda: "t")
        await owned.aclose()
        injected_http = httpx.AsyncClient(base_url="http://127.0.0.1:9119")
        injected = HermesKanbanClient(http_client=injected_http, token_resolver=lambda: "t")
        await injected.aclose()
        result = (owned._http.is_closed, injected_http.is_closed)
        await injected_http.aclose()
        return result

    assert asyncio.run(run()) == (True, False)
